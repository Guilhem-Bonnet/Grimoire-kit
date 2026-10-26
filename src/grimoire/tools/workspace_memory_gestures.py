"""Correction directe d'une entrée mémoire depuis le cockpit (P34) : aperçu, puis application.

Servi par ``POST /api/memory`` avec ``action: "gesture"`` — la route de
gouvernance de ``grimoire cockpit serve``, donc les mêmes gardes que la
suppression : ``Origin``, ``Sec-Fetch-Site`` et corps JSON, puis projet servi
seulement (P02, 403 sur tout autre projet). Ce module ne voit la requête
qu'après ces gardes ; ``cmd_cockpit`` ne fait que l'aiguiller.

Gestes (``gesture``) :

``tags``       remplace les étiquettes (``run_gesture("update_tags")``) ;
``supersede``  marque l'entrée remplacée par une autre (``MemoryManager.supersede``) ;
``retract``    la retire du service sans l'effacer (``run_gesture("retract")``) ;
``unretract``  la rend au service (``run_gesture("unretract")``) ;
``merge``      fusionne d'autres entrées dans celle-ci : ``update`` du survivant
               (texte, union des étiquettes) puis ``supersede`` des autres ;
``source``     rattache une source : tâche, session, fichier et ligne, commit
               (``MemoryManager.update`` de ``source_uri``) ;
``undo``       défait une ligne de l'historique de l'entrée (journal P14).

Chaque geste passe par :class:`~grimoire.memory.manager.MemoryManager` — jamais
par le backend directement — donc écrit le journal des écritures
(``_grimoire-output/memory-changes.jsonl``). Les gestes déjà portés par la CLI
et le MCP (P23) appellent la même entrée, :func:`grimoire.memory.gestures.run_gesture`.

Deux temps, toujours :

1. ``mode: "preview"`` rend l'avant/après de chaque champ touché et la
   ``version`` des entrées en jeu (empreinte de leur texte, étiquettes et
   métadonnées). Rien n'est écrit.
2. ``mode: "apply"`` exige ``confirm: true`` et la ``version`` de l'aperçu. Si
   une entrée a changé entre-temps, rien n'est écrit : 409 avec l'aperçu
   recalculé (concurrence optimiste). Une raison est exigée pour remplacer,
   retirer et fusionner.

Annuler une écriture qui a changé le texte ou les métadonnées demande l'état
d'avant, que le journal ne porte jamais (il n'a que des empreintes). Ces gestes
déposent donc l'état d'avant dans ``_grimoire-output/memory-undo.jsonl``, à côté
du journal, et la ligne du journal y renvoie par ``undo_ref``. Ce fichier reste
dans le projet, comme le store qui contient déjà ce texte.

Une **projection** (copie régénérable du code, des docs ou du ledger) n'accepte
aucun geste : elle se corrige à sa source, puis se régénère.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireError
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.journal import JOURNAL_RELPATH, fingerprint
from grimoire.memory.supersession import SUPERSEDED_KEY, is_retracted, retraction_of

__all__ = [
    "GESTURES",
    "GESTURE_ACTION",
    "REASON_REQUIRED",
    "UNDO_RELPATH",
    "GestureRefusedError",
    "can_undo",
    "entry_version",
    "handle",
]

GESTURE_ACTION = "gesture"
GESTURES: tuple[str, ...] = ("tags", "supersede", "retract", "unretract", "merge", "source", "undo")
REASON_REQUIRED = frozenset({"supersede", "retract", "merge"})
UNDO_RELPATH = Path("_grimoire-output") / "memory-undo.jsonl"

_MAX_REASON = 500
_MAX_TAGS = 32
_MAX_TAG_CHARS = 64
_MAX_MERGE = 20
_MAX_TEXT = 20_000
_UNDO_MAX_BYTES = 8 * 1024 * 1024
_EXCERPT = 160
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE_KEYS = ("source_uri", "source_digest", "source_date")

# Une application à la fois : la vérification de version et l'écriture ne
# doivent pas être entrelacées avec une autre application du même processus.
_APPLY_LOCK = threading.Lock()

_LABELS = {
    "tags": "Corriger les étiquettes",
    "supersede": "Marquer remplacée",
    "retract": "Retirer du service",
    "unretract": "Rendre au service",
    "merge": "Fusionner dans cette entrée",
    "source": "Rattacher une source",
    "undo": "Annuler une écriture",
}


class GestureRefusedError(GrimoireError):
    """Un geste refusé avant toute écriture, avec le statut HTTP qui le dit."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class _Change:
    entry_id: str
    entry: str
    field: str
    before: str
    after: str


@dataclass
class _Plan:
    """Ce qu'un geste changerait, sur quelles entrées, et comment l'écrire."""

    gesture: str
    entry_id: str
    involved: list[MemoryEntry]
    apply: Callable[[str], str]
    changes: list[_Change] = field(default_factory=list)
    note: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "gesture": self.gesture,
            "id": self.entry_id,
            "title": _LABELS[self.gesture],
            "version": _version(self.involved),
            "requiresReason": self.gesture in REASON_REQUIRED,
            "changes": [
                {"entryId": c.entry_id, "entry": c.entry, "field": c.field, "before": c.before, "after": c.after}
                for c in self.changes
            ],
            "note": self.note,
        }


# ── Versions : l'empreinte de ce que l'aperçu a lu ──────────────────────────


def entry_version(entry: MemoryEntry) -> str:
    """Empreinte du texte, des étiquettes et des métadonnées d'une entrée."""
    body = {"id": entry.id, "text": entry.text, "tags": list(entry.tags), "metadata": entry.metadata or {}}
    return fingerprint(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str))


def _version(entries: Iterable[MemoryEntry]) -> str:
    return fingerprint("|".join(entry_version(e) for e in sorted(entries, key=lambda e: e.id)))


# ── Libellés de l'aperçu ────────────────────────────────────────────────────


def _title(entry: MemoryEntry | None) -> str:
    """Première phrase, bornée : le titre que la liste montre déjà."""
    if entry is None:
        return "entrée introuvable"
    first = " ".join(entry.text.split())
    cut = first.find(". ")
    if 0 < cut < 96:
        first = first[: cut + 1]
    return first if len(first) <= 80 else first[:79].rstrip() + "…"


def _excerpt(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= _EXCERPT else clean[: _EXCERPT - 1].rstrip() + "…"


def _tags_label(tags: Iterable[str]) -> str:
    return ", ".join(tags) or "aucune"


def _state_label(
    entry: MemoryEntry, recall: Callable[[str], MemoryEntry | None], *,
    retracted: bool | None = None, metadata: Mapping[str, Any] | None = None,
) -> str:
    """État lu sur l'entrée, ou sur les métadonnées qu'elle aurait (``metadata``)."""
    meta = (entry.metadata or {}) if metadata is None else metadata
    if (bool(meta.get("retracted")) if retracted is None else retracted):
        return "retirée (plus servie aux agents)"
    target = str(meta.get(SUPERSEDED_KEY) or "").strip()
    if target:
        return f"remplacée par « {_title(recall(target))} »"
    return "en vigueur"


def _source_label(meta: Mapping[str, Any]) -> str:
    uri = str(meta.get("source_uri") or "").strip()
    if not uri:
        path = str(meta.get("file_path") or "").strip()
        return f"fichier {path}" if path else "aucune"
    scheme, sep, rest = uri.partition(":")
    names = {"task": "tâche", "session": "session", "commit": "commit"}
    if sep and scheme in names:
        return f"{names[scheme]} {rest}"
    return f"fichier {uri}"


# ── Ouverture et lectures gardées ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Ctx:
    root: Path
    mgr: Any
    project: str

    def recall(self, entry_id: str) -> MemoryEntry | None:
        entry: MemoryEntry | None = self.mgr.recall(entry_id)
        return entry

    def load(self, entry_id: str, *, role: str = "Entrée") -> MemoryEntry:
        """L'entrée, du projet servi, qui n'est pas une projection — ou un refus."""
        wanted = str(entry_id or "").strip()
        if not wanted:
            raise GestureRefusedError(f"{role} : identifiant requis.")
        entry = self.recall(wanted)
        if entry is None:
            raise GestureRefusedError(f"{role} introuvable : {wanted}.", 404)
        owner = str((entry.metadata or {}).get("project_name") or "").strip()
        if owner and self.project and owner != self.project:
            raise GestureRefusedError(f"{role} d'un autre projet ({owner}) : aucun geste ne la touche.", 403)
        from grimoire.tools.workspace_knowledge import classify

        if classify(entry)[0] == "projection":
            raise GestureRefusedError(
                f"{role} est une projection régénérable : corrige la source, elle sera régénérée.", 409
            )
        return entry


def _open(project_root: Path) -> _Ctx:
    config = project_root / "project-context.yaml"
    if not config.is_file():
        raise GestureRefusedError("Projet non initialisé (pas de project-context.yaml).")
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    cfg = GrimoireConfig.from_yaml(config)
    mgr = MemoryManager.from_config(cfg, project_root=project_root)
    mgr.surface, mgr.journal_reads = "cockpit", False
    return _Ctx(project_root, mgr, str(cfg.project.name or ""))


def _clean_tags(raw: object) -> tuple[str, ...]:
    items = raw.split(",") if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else []
    out: list[str] = []
    for item in items:
        tag = str(item).strip()
        if not tag:
            continue
        if len(tag) > _MAX_TAG_CHARS or _CONTROL.search(tag):
            raise GestureRefusedError(f"Étiquette refusée : {_MAX_TAG_CHARS} caractères au plus, sans caractère de contrôle.")
        if tag not in out:
            out.append(tag)
    if len(out) > _MAX_TAGS:
        raise GestureRefusedError(f"Trop d'étiquettes : {len(out)} (maximum {_MAX_TAGS}).")
    return tuple(out)


# ── Annulation : l'état d'avant, déposé à côté du journal ───────────────────


def _snapshot(root: Path, entry: MemoryEntry) -> str:
    """Dépose l'état d'avant d'une écriture qui touche texte ou métadonnées ; rend sa clé."""
    ref = uuid.uuid4().hex[:16]
    row = {
        "ref": ref, "ts": datetime.now(UTC).isoformat(timespec="seconds"), "id": entry.id,
        "text": entry.text, "tags": list(entry.tags), "metadata": entry.metadata or {},
    }
    path = root / UNDO_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return ref


def _snapshot_of(root: Path, ref: str) -> dict[str, Any] | None:
    path = root / UNDO_RELPATH
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _UNDO_MAX_BYTES:
                handle.seek(size - _UNDO_MAX_BYTES)
                handle.readline()
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(raw.splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("ref") == ref:
            return row
    return None


def can_undo(row: Mapping[str, Any]) -> bool:
    """Vrai si cette ligne du journal se défait depuis l'historique de l'entrée."""
    op = row.get("op")
    if op == "update_tags":
        return bool(row.get("tags_added") or row.get("tags_removed"))
    if op in {"retract", "unretract"}:
        return bool(row.get("before_id"))
    if op == "supersede":
        return bool(row.get("before_id") and row.get("after_id"))
    return op == "update" and bool(row.get("undo_ref"))


def _journal_row(root: Path, entry_id: str, wanted: Mapping[str, Any]) -> dict[str, Any]:
    """La ligne d'historique désignée par son horodatage, son opération, ses identifiants et son empreinte."""
    keys = ("ts", "op", "before_id", "after_id", "fingerprint", "undo_ref")
    target = {k: str(wanted.get(k) or "") for k in keys}
    path = root / JOURNAL_RELPATH
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or entry_id not in {row.get("before_id"), row.get("after_id")}:
            continue
        if all(str(row.get(k) or "") == target[k] for k in keys):
            return row
    raise GestureRefusedError("Ligne d'historique introuvable : relis la fiche de l'entrée.", 404)


# ── Les plans, un par geste ─────────────────────────────────────────────────


def _restore_metadata(current: Mapping[str, Any], wanted: Mapping[str, Any]) -> dict[str, Any]:
    """``wanted``, plus ``None`` sur les clés apparues depuis : le manager fusionne les métadonnées."""
    return {**{k: None for k in current if k not in wanted}, **wanted}


def _plan_tags(ctx: _Ctx, entry: MemoryEntry, params: Mapping[str, Any]) -> _Plan:
    from grimoire.memory.gestures import run_gesture

    after = _clean_tags(params.get("tags"))
    if after == tuple(entry.tags):
        raise GestureRefusedError("Rien ne change : ce sont déjà ses étiquettes.")

    def apply(reason: str) -> str:
        run_gesture(ctx.mgr, "update_tags", entry.id, replace=after, reason=reason)
        return "Étiquettes corrigées."

    change = _Change(entry.id, _title(entry), "Étiquettes", _tags_label(entry.tags), _tags_label(after))
    return _Plan("tags", entry.id, [entry], apply, [change])


def _plan_retract(ctx: _Ctx, entry: MemoryEntry, *, undo: bool) -> _Plan:
    from grimoire.memory.gestures import run_gesture

    if is_retracted(entry):
        raise GestureRefusedError("Entrée déjà retirée : rends-la au service d'abord.")
    before = _state_label(entry, ctx.recall)

    def apply(reason: str) -> str:
        run_gesture(ctx.mgr, "retract", entry.id, reason=reason)
        return "Entrée retirée du service : elle reste lisible ici, et s'annule depuis son historique."

    change = _Change(entry.id, _title(entry), "État", before, "retirée (plus servie aux agents)")
    return _Plan("undo" if undo else "retract", entry.id, [entry], apply, [change])


def _plan_unretract(ctx: _Ctx, entry: MemoryEntry, *, undo: bool) -> _Plan:
    from grimoire.memory.gestures import run_gesture

    if not is_retracted(entry):
        raise GestureRefusedError("Entrée non retirée : rien à rendre au service.")
    was = retraction_of(entry)

    def apply(reason: str) -> str:
        run_gesture(ctx.mgr, "unretract", entry.id, reason=reason)
        return "Entrée rendue au service."

    after = _state_label(entry, ctx.recall, retracted=False)
    note = f"Retirée pour : {was.get('reason')}" if was.get("reason") else ""
    change = _Change(entry.id, _title(entry), "État", "retirée (plus servie aux agents)", after)
    return _Plan("undo" if undo else "unretract", entry.id, [entry], apply, [change], note)


def _plan_supersede(ctx: _Ctx, entry: MemoryEntry, params: Mapping[str, Any]) -> _Plan:
    by = str(params.get("by") or "").strip()
    if by == entry.id:
        raise GestureRefusedError("Une entrée ne peut pas se remplacer elle-même.")
    replacement = ctx.load(by, role="Remplaçante")
    if is_retracted(replacement):
        raise GestureRefusedError("La remplaçante est retirée : rends-la au service ou choisis-en une autre.")
    if str((replacement.metadata or {}).get(SUPERSEDED_KEY) or "").strip():
        raise GestureRefusedError("La remplaçante est elle-même remplacée : vise la dernière de la chaîne.")

    def apply(reason: str) -> str:
        ctx.mgr.supersede(entry.id, replacement.id, reason=reason)
        return f"Entrée marquée remplacée par « {_title(replacement)} »."

    change = _Change(
        entry.id, _title(entry), "État", _state_label(entry, ctx.recall), f"remplacée par « {_title(replacement)} »"
    )
    return _Plan("supersede", entry.id, [entry, replacement], apply, [change])


def _plan_merge(ctx: _Ctx, entry: MemoryEntry, params: Mapping[str, Any]) -> _Plan:
    raw = params.get("others")
    wanted = [str(x).strip() for x in raw] if isinstance(raw, list) else []
    ids = list(dict.fromkeys(i for i in wanted if i and i != entry.id))
    if not ids:
        raise GestureRefusedError("Choisis au moins une autre entrée à fusionner dans celle-ci.")
    if len(ids) > _MAX_MERGE:
        raise GestureRefusedError(f"Trop d'entrées à fusionner : {len(ids)} (maximum {_MAX_MERGE}).")
    others = [ctx.load(i, role="Entrée à fusionner") for i in ids]
    text = params.get("text")
    new_text = " ".join(str(text).split()) if isinstance(text, str) and str(text).strip() else ""
    if len(new_text) > _MAX_TEXT:
        raise GestureRefusedError(f"Texte trop long : {len(new_text)} caractères (maximum {_MAX_TEXT}).")
    text_changes = bool(new_text) and new_text != " ".join(entry.text.split())
    tags = _clean_tags([*entry.tags, *(t for o in others for t in o.tags)])
    tags_change = tags != tuple(entry.tags)
    changes: list[_Change] = []
    if text_changes:
        changes.append(_Change(entry.id, _title(entry), "Texte", _excerpt(entry.text), _excerpt(new_text)))
    if tags_change:
        changes.append(_Change(entry.id, _title(entry), "Étiquettes", _tags_label(entry.tags), _tags_label(tags)))
    survivor = _excerpt(new_text) if text_changes else _title(entry)
    changes.extend(
        _Change(o.id, _title(o), "État", _state_label(o, ctx.recall), f"remplacée par « {survivor} »") for o in others
    )

    def apply(reason: str) -> str:
        if text_changes or tags_change:
            ref = _snapshot(ctx.root, entry)
            ctx.mgr.update(
                entry.id, text=new_text if text_changes else None, tags=tags if tags_change else None,
                reason=reason, journal_extra={"gesture": "merge", "undo_ref": ref, "merged": ids},
            )
        done: list[str] = []
        for other in others:
            try:
                ctx.mgr.supersede(other.id, entry.id, reason=reason, journal_extra={"gesture": "merge"})
            except GrimoireError as exc:
                raise GestureRefusedError(
                    f"Fusion partielle : {len(done)} entrée(s) remplacée(s), arrêt sur « {_title(other)} » : {exc}"
                ) from exc
            done.append(other.id)
        return f"Fusion faite : {len(done)} entrée(s) remplacée(s) par celle-ci."

    return _Plan("merge", entry.id, [entry, *others], apply, changes)


def _source_uri(ctx: _Ctx, params: Mapping[str, Any]) -> str:
    kind = str(params.get("kind") or "")
    value = str(params.get("value") or "").strip()
    if kind in {"task", "session"}:
        if not _ID.match(value):
            raise GestureRefusedError("Identifiant refusé : lettres, chiffres, « . », « _ », « - » ou « : », 128 au plus.")
        return f"{kind}:{value}"
    if kind == "commit":
        if not _COMMIT.match(value):
            raise GestureRefusedError("Commit refusé : 7 à 40 caractères hexadécimaux.")
        return f"commit:{value.lower()}"
    if kind != "file":
        raise GestureRefusedError("Source inconnue : tâche, session, fichier ou commit.")
    rel = value.replace("\\", "/")
    if not rel or rel.startswith("/") or _CONTROL.search(rel) or len(rel) > 512:
        raise GestureRefusedError("Chemin refusé : un chemin relatif à la racine du projet.")
    # Confinement écrit ici, dans la fonction qui touche au disque : l'analyse
    # statique (CodeQL py/path-injection) ne suit pas une garde déportée dans un
    # autre module (même motif que `missions/ledger.py::_confined_ledger_file`).
    try:
        base = ctx.root.resolve()
        target = (base / rel).resolve()
    except (OSError, RuntimeError):
        raise GestureRefusedError("Chemin refusé : un chemin relatif à la racine du projet.") from None
    if not target.is_relative_to(base):
        raise GestureRefusedError(f"Fichier introuvable dans le projet : {rel} (le chemin sort de sa racine).")
    if not target.is_file():
        raise GestureRefusedError(f"Fichier introuvable dans le projet : {rel}.")
    line = params.get("line")
    if line in (None, ""):
        return rel
    try:
        number = int(str(line))
    except ValueError:
        number = 0
    if number < 1:
        raise GestureRefusedError("Ligne refusée : un entier à partir de 1.")
    return f"{rel}:{number}"


def _plan_source(ctx: _Ctx, entry: MemoryEntry, params: Mapping[str, Any]) -> _Plan:
    from grimoire.memory.knowledge import stamp_source

    uri = _source_uri(ctx, params)
    meta = {k: v for k, v in (entry.metadata or {}).items() if k not in _SOURCE_KEYS}
    stamped = stamp_source({**meta, "source_uri": uri}, ctx.root)
    for key in _SOURCE_KEYS:
        stamped.setdefault(key, None)  # efface l'empreinte de l'ancienne source
    before, after = _source_label(entry.metadata or {}), _source_label(stamped)
    if before == after:
        raise GestureRefusedError("Rien ne change : c'est déjà sa source.")

    def apply(reason: str) -> str:
        ref = _snapshot(ctx.root, entry)
        ctx.mgr.update(entry.id, metadata=stamped, reason=reason, journal_extra={"gesture": "source", "undo_ref": ref})
        return f"Source rattachée : {after}."

    return _Plan("source", entry.id, [entry], apply, [_Change(entry.id, _title(entry), "Source", before, after)])


def _plan_undo(ctx: _Ctx, entry: MemoryEntry, params: Mapping[str, Any]) -> _Plan:
    wanted = params.get("row")
    row = _journal_row(ctx.root, entry.id, wanted if isinstance(wanted, Mapping) else {})
    if not can_undo(row):
        raise GestureRefusedError("Cette écriture ne s'annule pas depuis le cockpit.")
    op = row["op"]
    target = ctx.load(str(row.get("before_id")))
    if op == "retract":
        return _plan_unretract(ctx, target, undo=True)
    if op == "unretract":
        plan = _plan_retract(ctx, target, undo=True)
        why = str((row.get("was_retracted") or {}).get("reason") or "") if isinstance(row.get("was_retracted"), dict) else ""
        inner = plan.apply
        plan.apply = lambda reason: inner(reason or why or "Annulation d'un rétablissement")
        return plan
    if op == "update_tags":
        added = [str(t) for t in row.get("tags_added") or []]
        removed = [str(t) for t in row.get("tags_removed") or []]
        after = [t for t in target.tags if t not in added] + [t for t in removed if t not in target.tags]
        plan = _plan_tags(ctx, target, {"tags": after})
        plan.gesture = "undo"
        return plan
    if op == "supersede":
        return _plan_unsupersede(ctx, target, str(row.get("after_id")))
    snapshot = _snapshot_of(ctx.root, str(row.get("undo_ref")))
    if snapshot is None:
        raise GestureRefusedError("L'état d'avant de cette écriture n'est plus disponible : annulation impossible.", 410)
    return _plan_restore(ctx, target, snapshot)


def _plan_unsupersede(ctx: _Ctx, target: MemoryEntry, replaced_by: str) -> _Plan:
    if str((target.metadata or {}).get(SUPERSEDED_KEY) or "") != replaced_by:
        raise GestureRefusedError("Cette entrée n'est plus remplacée par celle du journal : rien à annuler.")
    meta = {**(target.metadata or {}), SUPERSEDED_KEY: None}

    def apply(reason: str) -> str:
        ref = _snapshot(ctx.root, target)
        ctx.mgr.update(
            target.id, metadata=meta, reason=reason,
            journal_extra={"gesture": "undo", "undoes": "supersede", "undo_ref": ref},
        )
        return "Remplacement annulé : l'entrée est de nouveau en vigueur."

    change = _Change(target.id, _title(target), "État", _state_label(target, ctx.recall), "en vigueur")
    return _Plan("undo", target.id, [target], apply, [change])


def _plan_restore(ctx: _Ctx, target: MemoryEntry, snapshot: Mapping[str, Any]) -> _Plan:
    text = str(snapshot.get("text") or "")
    tags = tuple(str(t) for t in snapshot.get("tags") or [])
    meta = _restore_metadata(target.metadata or {}, snapshot.get("metadata") or {})
    changes: list[_Change] = []
    if text and text != target.text:
        changes.append(_Change(target.id, _title(target), "Texte", _excerpt(target.text), _excerpt(text)))
    if tags != tuple(target.tags):
        changes.append(_Change(target.id, _title(target), "Étiquettes", _tags_label(target.tags), _tags_label(tags)))
    source_before, source_after = _source_label(target.metadata or {}), _source_label(meta)
    if source_before != source_after:
        changes.append(_Change(target.id, _title(target), "Source", source_before, source_after))
    state_before, state_after = _state_label(target, ctx.recall), _state_label(target, ctx.recall, metadata=meta)
    if state_before != state_after:
        changes.append(_Change(target.id, _title(target), "État", state_before, state_after))
    if not changes:
        raise GestureRefusedError("Rien à annuler : l'entrée est déjà dans cet état.")

    def apply(reason: str) -> str:
        ref = _snapshot(ctx.root, target)
        ctx.mgr.update(
            target.id, text=text if text and text != target.text else None, tags=tags, metadata=meta,
            reason=reason, journal_extra={"gesture": "undo", "undo_ref": ref},
        )
        return "Écriture annulée : l'entrée a retrouvé son état d'avant."

    return _Plan("undo", target.id, [target], apply, changes)


def _plan(ctx: _Ctx, gesture: str, entry_id: str, params: Mapping[str, Any]) -> _Plan:
    entry = ctx.load(entry_id)
    if gesture == "tags":
        return _plan_tags(ctx, entry, params)
    if gesture == "retract":
        return _plan_retract(ctx, entry, undo=False)
    if gesture == "unretract":
        return _plan_unretract(ctx, entry, undo=False)
    if gesture == "supersede":
        return _plan_supersede(ctx, entry, params)
    if gesture == "merge":
        return _plan_merge(ctx, entry, params)
    if gesture == "source":
        return _plan_source(ctx, entry, params)
    return _plan_undo(ctx, entry, params)


# ── Point d'entrée de la route ──────────────────────────────────────────────


def handle(project_root: Path, data: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    """``(statut HTTP, charge utile)`` d'un aperçu ou d'une application de geste.

    Appelé par ``POST /api/memory`` après les gardes de mutation et la garde du
    projet servi. Un refus porte toujours un message lisible (``error``).
    """
    gesture = str(data.get("gesture") or "")
    entry_id = str(data.get("id") or "").strip()
    mode = str(data.get("mode") or "preview")
    base: dict[str, Any] = {"gesture": gesture, "id": entry_id, "mode": mode}
    if gesture not in GESTURES:
        return 400, {**base, "ok": False, "error": f"Geste inconnu : {gesture or '(vide)'}."}
    if mode not in {"preview", "apply"}:
        return 400, {**base, "ok": False, "error": "Mode inconnu : preview ou apply."}
    if mode == "apply" and data.get("confirm") is not True:
        return 403, {**base, "ok": False, "error": "confirmation explicite requise"}
    raw_params = data.get("params")
    params: Mapping[str, Any] = raw_params if isinstance(raw_params, Mapping) else {}
    reason = " ".join(str(data.get("reason") or "").split())[:_MAX_REASON]
    with _APPLY_LOCK:
        try:
            plan = _plan(_open(project_root), gesture, entry_id, params)
            preview = plan.payload()
            if mode == "preview":
                return 200, {**base, "ok": True, **preview}
            if str(data.get("version") or "") != preview["version"]:
                return 409, {
                    **base, "ok": False, "stale": True, "preview": preview,
                    "error": "L'entrée a changé depuis l'aperçu : rien n'a été appliqué, l'aperçu est recalculé.",
                }
            if preview["requiresReason"] and not reason:
                return 400, {**base, "ok": False, "error": "Une raison est obligatoire pour ce geste."}
            message = plan.apply(reason)
        except GestureRefusedError as exc:
            return exc.status, {**base, "ok": False, "error": str(exc)}
        except GrimoireError as exc:
            return 400, {**base, "ok": False, "error": str(exc)}
    return 200, {**base, "ok": True, "message": message, "target": plan.entry_id}
