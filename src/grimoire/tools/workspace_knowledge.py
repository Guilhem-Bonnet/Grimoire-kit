"""Espace Savoir du cockpit : entrées paginées et fiche d'une entrée (P24).

Deux lectures, servies en ``GET`` par ``workspace_routes`` :

- ``memory/entries`` — une page d'entrées d'UN projet, lue par tranches
  (``backend.get_all(offset=, limit=)``) jusqu'à remplir la page ou épuiser le
  budget de parcours (:data:`MAX_SCAN`). Jamais une liste complète du store :
  la page suivante repart du curseur rendu (``nextOffset``) ;
- ``memory/entry`` — la fiche d'une entrée : provenance calculée à la lecture
  (``memory/knowledge.py``), chaîne de supersession dans les deux sens (le
  remplaçant suivi par ``superseded_by``, ce qu'elle remplace lu dans le
  journal des écritures P14) et nœuds liés dérivés de l'entrée elle-même.

Les deux distinguent les **souvenirs** (écrits par un agent ou une personne)
des **projections** (copies régénérables du code, des docs et du ledger :
``projection_group`` ou ``source_kind == "projection"``).

Refus tenus ici, pour toutes les surfaces qui les appellent :

- lecture seule : rien n'est écrit chez le projet parcouru, pas même un reçu
  de lecture ;
- les chemins viennent du registre de la machine (ou du projet que l'hôte
  sert déjà), jamais de la requête ;
- dans un emplacement partagé (même collection, même fichier), les entrées
  d'un AUTRE projet n'apparaissent qu'en comptes, jamais en contenu. Une
  entrée sans projet n'est montrée que si l'emplacement n'est partagé avec
  aucun autre projet du registre ;
- une lecture ratée porte son motif (``reason``), jamais une liste vide muette.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.memory.journal import JOURNAL_RELPATH
from grimoire.memory.supersession import SUPERSEDED, SUPERSEDED_KEY, is_retracted, supersession_state
from grimoire.tools.project_registry import load_registry, slug_for_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from grimoire.memory.backends.base import MemoryEntry

__all__ = [
    "ENTRIES_SCHEMA_VERSION",
    "ENTRY_SCHEMA_VERSION",
    "Ownership",
    "classify",
    "describe",
    "journal_index",
    "memory_entries",
    "memory_entry",
    "open_readonly",
    "ownership",
]

ENTRIES_SCHEMA_VERSION = "grimoire-memory-entries/v1"
ENTRY_SCHEMA_VERSION = "grimoire-memory-entry/v1"

DEFAULT_PAGE = 30
MAX_PAGE = 200
#: Taille d'une tranche demandée au backend.
SCAN_BATCH = 200
#: Entrées parcourues au plus par appel : au-delà, la page rend ce qu'elle a
#: et le curseur ``nextOffset`` dit où reprendre.
MAX_SCAN = 2000
#: Journal des écritures lu au plus sur ses derniers octets.
_JOURNAL_MAX_BYTES = 4 * 1024 * 1024
#: Maillons suivis au plus dans une chaîne de remplacement.
_CHAIN_MAX = 20
_TITLE_MAX = 96
_JOURNAL_KEYS = (
    "ts", "op", "actor", "session_id", "task_ref", "reason", "before_id", "after_id", "gesture",
    "tags_added", "tags_removed", "fingerprint", "undo_ref",
)
_EXCERPT_MAX = 240

KINDS = ("souvenir", "projection")

_MEMORY_TYPES = {
    "decisions": "decision", "decision": "decision",
    "failures": "incident", "failure": "incident", "incident": "incident",
    "shared-context": "convention", "shared_context": "convention", "convention": "convention",
    "agent-learnings": "learning", "agent_learnings": "learning", "learning": "learning",
    "stories": "story", "story": "story",
}
_TAG_TYPES = {
    "decision": "decision", "decisions": "decision",
    "failure": "incident", "failures": "incident", "incident": "incident", "piege": "incident",
    "convention": "convention", "conventions": "convention",
}
_PROJECTION_TYPES = {"code": "code", "docs": "docs", "task_memory": "task"}


# ── Classement : souvenir ou projection, et son type ───────────────────────


def classify(entry: MemoryEntry) -> tuple[str, str]:
    """``(nature, type)`` d'une entrée.

    Nature : ``souvenir`` ou ``projection``. Type d'un souvenir : ``decision``,
    ``incident``, ``convention``, ``learning``, ``story`` ou ``untyped`` ; type
    d'une projection : ``code``, ``docs``, ``task`` ou ``projection``.
    """
    meta = entry.metadata or {}
    group = str(meta.get("projection_group") or "").strip()
    if group or meta.get("source_kind") == "projection":
        return "projection", _PROJECTION_TYPES.get(group, "projection")
    raw = str(meta.get("memory_type") or meta.get("type") or "").strip().lower()
    if raw in _MEMORY_TYPES:
        return "souvenir", _MEMORY_TYPES[raw]
    for tag in entry.tags:
        found = _TAG_TYPES.get(str(tag).strip().lower())
        if found:
            return "souvenir", found
    return "souvenir", "untyped"


# ── Propriété : à qui est une entrée d'un emplacement peut-être partagé ─────


@dataclass(frozen=True, slots=True)
class Ownership:
    """Le projet lecteur et les autres projets du registre qui lisent le même store."""

    mine: str
    shared_with: tuple[str, ...] = field(default_factory=tuple)

    def visibility(self, entry: MemoryEntry) -> str:
        """``own``, ``other`` ou ``unattributed``.

        Même clé que le garde des gestes (``project_name``) : une entrée d'un
        autre projet n'est jamais montrée. Sans projet, elle n'est montrée que
        si personne d'autre ne lit cet emplacement.
        """
        owner = owner_of(entry)
        if owner:
            return "own" if not self.mine or owner == self.mine else "other"
        return "unattributed" if self.shared_with else "own"


def owner_of(entry: MemoryEntry) -> str:
    return str((entry.metadata or {}).get("project_name") or "").strip()


def ownership(cfg: Any, project_root: Path) -> Ownership:
    """Qui d'autre, dans le registre, lit le même store que ce projet.

    Comparaison par :func:`grimoire.memory.manager.store_identity` (emplacement
    + collection), sans rien ouvrir. Un projet du registre illisible est
    ignoré : il ne peut pas être celui qui partage.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import store_identity

    mine = str(getattr(getattr(cfg, "project", None), "name", "") or "")
    try:
        key = store_identity(cfg, project_root)
    except Exception:
        return Ownership(mine)
    here = project_root.resolve()
    others: list[str] = []
    for row in load_registry():
        raw = str(row.get("path", "") or "")
        if not raw:
            continue
        other_root = Path(raw)
        config = other_root / "project-context.yaml"
        try:
            if other_root.resolve() == here or not config.is_file():
                continue
            if store_identity(GrimoireConfig.from_yaml(config), other_root) == key:
                others.append(str(row.get("name", "") or row.get("slug", "") or other_root.name))
        except Exception:  # noqa: S112 — un voisin illisible ne partage rien de lisible
            continue
    return Ownership(mine, tuple(others))


# ── Journal des écritures (P14) : qui a écrit, ce qu'une entrée remplace ────


def _journal_rows(project_root: Path) -> list[dict[str, Any]]:
    """Les lignes du journal ``memory-changes.jsonl``, bornées à ses derniers octets."""
    path = project_root / JOURNAL_RELPATH
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _JOURNAL_MAX_BYTES:
                handle.seek(size - _JOURNAL_MAX_BYTES)
                handle.readline()  # ligne coupée par la borne
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


@dataclass(frozen=True, slots=True)
class JournalIndex:
    """Ce que le journal dit d'une entrée : ce qu'elle remplace, ses écritures."""

    replaces: dict[str, list[str]] = field(default_factory=dict)
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def journal_index(project_root: Path) -> JournalIndex:
    replaces: dict[str, list[str]] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in _journal_rows(project_root):
        before = str(row.get("before_id") or "")
        after = str(row.get("after_id") or "")
        if row.get("op") == "supersede" and before and after:
            replaces.setdefault(after, []).append(before)
        for key in {before, after} - {""}:
            rows.setdefault(key, []).append(row)
    return JournalIndex(replaces, rows)


# ── Une entrée telle que l'interface la lit ────────────────────────────────


def _split_text(text: str) -> tuple[str, str]:
    """Titre (première phrase ou ligne, borné) et extrait (la suite, bornée)."""
    clean = " ".join(text.split())
    if not clean:
        return "", ""
    # La première ligne non vide, espaces réduits, est un préfixe de `clean`.
    first = " ".join(next(line for line in text.splitlines() if line.strip()).split())
    cut = first.find(". ")
    head = first[: cut + 1] if 0 < cut < _TITLE_MAX else first
    title = head
    if len(head) > _TITLE_MAX:
        space = head.rfind(" ", 0, _TITLE_MAX)
        head = head[: space if space > _TITLE_MAX // 2 else _TITLE_MAX - 1]
        title = head.rstrip() + "…"
    rest = clean[len(head):].strip()
    if len(rest) > _EXCERPT_MAX:
        rest = rest[: _EXCERPT_MAX - 1].rstrip() + "…"
    return title, rest


def _date_of(entry: MemoryEntry) -> str:
    return str(entry.updated_at or entry.created_at or "")


def _title_of(entry: MemoryEntry | None) -> str | None:
    return _split_text(entry.text)[0] if entry is not None else None


def _state_of(entry: MemoryEntry, recall: Callable[[str], MemoryEntry | None]) -> str:
    return "retracted" if is_retracted(entry) else supersession_state(entry, recall)


def describe(
    entry: MemoryEntry,
    *,
    slug: str,
    name: str,
    recall: Callable[[str], MemoryEntry | None],
    journal: JournalIndex,
) -> dict[str, Any]:
    """La forme commune d'une entrée pour ``memory/entries``, ``memory/entry`` et la recherche.

    ``entry`` doit déjà porter sa provenance (``knowledge.annotate``).
    """
    meta = entry.metadata or {}
    kind, type_ = classify(entry)
    title, excerpt = _split_text(entry.text)
    replaced_id = str(meta.get(SUPERSEDED_KEY) or "").strip()
    replacement = recall(replaced_id) if replaced_id else None
    replaces = [
        {"id": old_id, "title": _title_of(recall(old_id))}
        for old_id in journal.replaces.get(entry.id, [])
    ]
    return {
        "id": entry.id,
        "text": entry.text,
        "title": title,
        "excerpt": excerpt,
        "kind": kind,
        "type": type_,
        "group": str(meta.get("projection_group") or ""),
        "projectSlug": slug,
        "projectName": name,
        "owner": owner_of(entry),
        "date": _date_of(entry),
        "createdAt": entry.created_at,
        "updatedAt": entry.updated_at,
        "source": entry.source,
        "provenance": dict(entry.provenance),
        "freshness": entry.freshness,
        "tags": list(entry.tags),
        "userId": entry.user_id,
        "agent": str(meta.get("agent") or meta.get("agent_name") or ""),
        "actor": str(meta.get("actor") or ""),
        "sessionId": str(meta.get("session_id") or ""),
        "taskRef": entry.task_ref or str(meta.get("task_ref") or meta.get("task_id") or ""),
        "supersession": {
            "state": _state_of(entry, recall),
            "replacedBy": (
                {"id": replaced_id, "title": _title_of(replacement), "found": replacement is not None}
                if replaced_id else None
            ),
            "replaces": replaces,
        },
    }


# ── Ouverture d'un projet en lecture seule ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Target:
    slug: str
    name: str
    root: Path | None
    reason: str | None = None


def _target(project_root: Path, slug: str | None) -> _Target:
    """Le projet visé : un slug du registre, sinon le projet que l'hôte sert déjà."""
    if slug:
        row = next((r for r in load_registry() if str(r.get("slug", "")) == slug), None)
        if row is None:
            return _Target(slug, slug, None, "projet absent du registre de la machine")
        raw = str(row.get("path", "") or "")
        name = str(row.get("name", "") or slug)
        if not raw or not Path(raw).is_dir():
            return _Target(slug, name, None, f"dossier introuvable : {raw or 'chemin absent du registre'}")
        return _Target(slug, name, Path(raw))
    own = slug_for_path(project_root) or ""
    row = next((r for r in load_registry() if own and str(r.get("slug", "")) == own), None)
    return _Target(own, str((row or {}).get("name", "") or ""), project_root)


def open_readonly(project_root: Path) -> tuple[Any, Any, str | None]:
    """``(config, manager, raison)`` — un manager qui n'écrit aucun reçu de lecture."""
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return None, None, "projet non initialisé (pas de project-context.yaml)"
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.memory.manager import MemoryManager

        cfg = GrimoireConfig.from_yaml(config_path)
        mgr = MemoryManager.from_config(cfg, project_root=project_root)
    except Exception as exc:
        return None, None, f"mémoire illisible : {exc}"
    mgr.surface, mgr.journal_reads = "cockpit", False
    return cfg, mgr, None


def _memoized(recall: Callable[[str], MemoryEntry | None]) -> Callable[[str], MemoryEntry | None]:
    cache: dict[str, MemoryEntry | None] = {}

    def cached(entry_id: str) -> MemoryEntry | None:
        if entry_id not in cache:
            try:
                cache[entry_id] = recall(entry_id)
            except Exception:
                cache[entry_id] = None
        return cache[entry_id]

    return cached


def _as_int(raw: object, default: int, *, low: int, high: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _types(raw: str | None) -> frozenset[str]:
    return frozenset(t.strip() for t in str(raw or "").split(",") if t.strip())


# ── memory/entries ──────────────────────────────────────────────────────────


@dataclass
class _Scan:
    """Ce qu'un parcours a vu, écarté et retenu."""

    picked: list[MemoryEntry] = field(default_factory=list)
    others: Counter[str] = field(default_factory=Counter)
    kinds: Counter[str] = field(default_factory=Counter)
    types: Counter[str] = field(default_factory=Counter)
    unattributed: int = 0
    superseded: int = 0
    retracted: int = 0
    filtered: int = 0
    scanned: int = 0


@dataclass(frozen=True, slots=True)
class _Filters:
    kind: str
    types: frozenset[str]
    since: str
    include_superseded: bool

    def keeps(self, kind: str, type_: str, date: str) -> bool:
        if self.kind and kind != self.kind:
            return False
        if self.types and type_ not in self.types:
            return False
        return not (self.since and date[:10] < self.since)


def _consider(entry: MemoryEntry, scan: _Scan, owners: Ownership, filters: _Filters, recall: Any) -> None:
    visibility = owners.visibility(entry)
    if visibility == "other":
        scan.others[owner_of(entry)] += 1
        return
    if visibility == "unattributed":
        scan.unattributed += 1
        return
    if is_retracted(entry):
        scan.retracted += 1
        return
    if not filters.include_superseded and supersession_state(entry, recall) == SUPERSEDED:
        scan.superseded += 1
        return
    kind, type_ = classify(entry)
    scan.kinds[kind] += 1
    scan.types[type_] += 1
    if filters.keeps(kind, type_, _date_of(entry)):
        scan.picked.append(entry)
    else:
        scan.filtered += 1


def _empty_entries(target: _Target, offset: int, limit: int, reason: str | None) -> dict[str, Any]:
    return {
        "schemaVersion": ENTRIES_SCHEMA_VERSION,
        "project": {"slug": target.slug, "name": target.name},
        "reason": reason,
        "entries": [],
        "offset": offset,
        "limit": limit,
        "nextOffset": None,
        "exhausted": reason is None,
        "scanned": 0,
        "sharedWith": [],
        "hidden": {"otherProjects": [], "unattributed": 0, "superseded": 0, "retracted": 0, "filtered": 0},
        "counts": {"kind": {}, "type": {}},
    }


def memory_entries(
    project_root: Path,
    *,
    slug: str | None = None,
    kind: str | None = None,
    types: str | None = None,
    since: str | None = None,
    offset: object = 0,
    limit: object = DEFAULT_PAGE,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/entries`` — une page, jamais tout le store.

    ``offset`` est le curseur brut dans le store (``nextOffset`` de la page
    précédente) ; ``kind`` vaut ``souvenir`` ou ``projection`` ; ``types`` est
    une liste ``decision,incident,…`` ; ``since`` une date ISO (``AAAA-MM-JJ``).
    Les comptes par nature et par type portent sur les entrées parcourues
    avant les filtres de nature, de type et de date.
    """
    start = _as_int(offset, 0, low=0, high=10**9)
    size = _as_int(limit, DEFAULT_PAGE, low=1, high=MAX_PAGE)
    target = _target(project_root, slug)
    if target.root is None:
        return _empty_entries(target, start, size, target.reason)
    cfg, mgr, reason = open_readonly(target.root)
    if mgr is None:
        return _empty_entries(target, start, size, reason)
    name = target.name or str(cfg.project.name or target.root.name)
    owners = ownership(cfg, target.root)
    filters = _Filters(
        kind=kind if kind in KINDS else "", types=_types(types),
        since=str(since or "")[:10], include_superseded=include_superseded,
    )
    recall = _memoized(mgr.backend.recall)
    scan = _Scan()
    cursor = start
    exhausted = False
    try:
        while len(scan.picked) < size and scan.scanned < MAX_SCAN:
            batch = mgr.backend.get_all(offset=cursor, limit=SCAN_BATCH)
            consumed = 0
            for entry in batch:
                consumed += 1
                cursor += 1
                scan.scanned += 1
                _consider(entry, scan, owners, filters, recall)
                if len(scan.picked) >= size:
                    break
            if len(batch) < SCAN_BATCH and consumed == len(batch):
                # Tranche courte lue jusqu'au bout : le store est épuisé.
                exhausted = True
                break
    except Exception as exc:
        return _empty_entries(target, start, size, f"lecture du store impossible : {exc}")
    from grimoire.memory.knowledge import annotate

    journal = journal_index(target.root)
    served = annotate(scan.picked, target.root, project=name)
    payload = _empty_entries(target, start, size, None)
    payload.update({
        "project": {"slug": target.slug, "name": name, "owner": owners.mine},
        "entries": [describe(e, slug=target.slug, name=name, recall=recall, journal=journal) for e in served],
        "nextOffset": None if exhausted else cursor,
        "exhausted": exhausted,
        "scanned": scan.scanned,
        "sharedWith": list(owners.shared_with),
        "hidden": {
            "otherProjects": [{"name": n, "count": c} for n, c in sorted(scan.others.items())],
            "unattributed": scan.unattributed,
            "superseded": scan.superseded,
            "retracted": scan.retracted,
            "filtered": scan.filtered,
        },
        "counts": {"kind": dict(scan.kinds), "type": dict(scan.types)},
    })
    return payload


# ── memory/entry ────────────────────────────────────────────────────────────


def _chain(entry: MemoryEntry, recall: Callable[[str], MemoryEntry | None]) -> list[dict[str, Any]]:
    """Les remplaçants successifs, du premier au dernier maillon lisible."""
    chain: list[dict[str, Any]] = []
    seen = {entry.id}
    current = entry
    while len(chain) < _CHAIN_MAX:
        target_id = str((current.metadata or {}).get(SUPERSEDED_KEY) or "").strip()
        if not target_id or target_id in seen:
            break
        nxt = recall(target_id)
        chain.append({"id": target_id, "title": _title_of(nxt), "found": nxt is not None})
        if nxt is None:
            break
        seen.add(target_id)
        current = nxt
    return chain


def _links(described: dict[str, Any]) -> list[dict[str, str]]:
    """Nœuds liés, dérivés de l'entrée : ceux que la projection graphe en tire.

    Tâche (``MEMORY_FOR``), source (``SOURCED_FROM``), tags (``TAGGED_WITH``),
    remplaçant et remplacées. Aucune requête graphe : ce que l'entrée porte.
    """
    links: list[dict[str, str]] = []
    if described["taskRef"]:
        links.append({"kind": "tâche", "id": described["taskRef"], "label": described["taskRef"]})
    source = (described["provenance"].get("source") or {}) if described["provenance"] else {}
    if source.get("uri"):
        links.append({"kind": str(source.get("kind") or "source"), "id": str(source["uri"]), "label": str(source["uri"])})
    replaced = described["supersession"]["replacedBy"]
    if replaced:
        links.append({"kind": "remplaçant", "id": replaced["id"], "label": replaced["title"] or replaced["id"]})
    for old in described["supersession"]["replaces"]:
        links.append({"kind": "remplacée", "id": old["id"], "label": old["title"] or old["id"]})
    links.extend({"kind": "tag", "id": tag, "label": tag} for tag in described["tags"])
    return links


def memory_entry(project_root: Path, entry_id: str | None, *, slug: str | None = None) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/entry`` — la fiche d'une entrée.

    Une entrée d'un autre projet (emplacement partagé) n'est jamais rendue :
    ``found`` faux, ``hidden`` vrai, et aucun champ de contenu.
    """
    target = _target(project_root, slug)
    base: dict[str, Any] = {
        "schemaVersion": ENTRY_SCHEMA_VERSION,
        "project": {"slug": target.slug, "name": target.name},
        "found": False,
        "hidden": False,
        "reason": None,
        "entry": None,
    }
    wanted = str(entry_id or "").strip()
    if not wanted:
        return {**base, "reason": "identifiant d'entrée requis"}
    if target.root is None:
        return {**base, "reason": target.reason}
    cfg, mgr, reason = open_readonly(target.root)
    if mgr is None:
        return {**base, "reason": reason}
    name = target.name or str(cfg.project.name or target.root.name)
    base["project"] = {"slug": target.slug, "name": name}
    recall = _memoized(mgr.backend.recall)
    entry = recall(wanted)
    if entry is None:
        return {**base, "reason": "entrée introuvable dans ce store"}
    if ownership(cfg, target.root).visibility(entry) != "own":
        return {
            **base, "hidden": True,
            "reason": "entrée d'un autre projet du même emplacement : comptée, jamais montrée",
        }
    from grimoire.memory.knowledge import annotate

    journal = journal_index(target.root)
    served = annotate([entry], target.root, project=name)[0]
    described = describe(served, slug=target.slug, name=name, recall=recall, journal=journal)
    described["chain"] = _chain(served, recall)
    described["links"] = _links(described)
    from grimoire.tools.workspace_memory_gestures import can_undo

    described["journal"] = [
        {
            **{k: row[k] for k in _JOURNAL_KEYS if k in row},
            "undoable": can_undo(row),  # P34 : ligne annulable depuis l'historique de l'entrée
        }
        for row in journal.rows.get(entry.id, [])
    ]
    described["metadata"] = {k: v for k, v in (entry.metadata or {}).items() if not isinstance(v, (dict, list))}
    return {**base, "found": True, "entry": described}
