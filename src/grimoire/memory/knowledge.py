"""Fiche de fait et fraîcheur calculée à la lecture (portail de connaissance, E1).

Un souvenir rendu par la recherche dit d'où il vient, de quand il date et s'il
est encore vrai. Ce module est l'unique endroit qui le décide :

- **à l'écriture**, :func:`stamp_write` pose la provenance dans les
  métadonnées (date d'indexation, empreinte sha256 du fichier source, date de
  la source) ;
- **à la lecture**, :func:`annotate` recalcule la fraîcheur selon le tableau
  du cadrage (§5.2) et attache la fiche ``grimoire.knowledge.fact/v1`` à
  l'entrée. La fraîcheur n'est jamais une valeur stockée : un champ stocké
  mentirait dès que la source change.

Règles qui ne se négocient pas :

- un fait sans source à relire n'est jamais ``a_jour`` : au mieux
  ``non_verifie`` (confirmation de 270 jours au plus), sinon ``hypothese`` ;
- une source qu'on ne peut pas relire (illisible, hors projet, trop grosse)
  n'est jamais ``a_jour`` non plus ; seule une absence franche vaut
  ``source_disparue`` ;
- relire une source est borné et local : aucun réseau, un fichier du projet,
  :data:`MAX_SOURCE_BYTES` au plus, haché par blocs.

Pas de durée de vie (D11) : les paliers de 90 et 270 jours, repris de la
mémoire transverse, déclassent sans jamais supprimer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.shared import STALE_DAYS
from grimoire.memory.supersession import SUPERSEDED_KEY

__all__ = [
    "FACT_SCHEMA",
    "MAX_SOURCE_BYTES",
    "FreshnessState",
    "annotate",
    "file_digest",
    "label_of",
    "projection_is_current",
    "stamp_projection_entries",
    "stamp_source",
    "stamp_write",
]

#: Version de la fiche de fait (D13). L'export JSON historique de ``MemoryEntry``
#: reste intact : la fiche vit dans ``provenance`` et s'y ajoute.
FACT_SCHEMA = "grimoire.knowledge.fact/v1"

#: Plafond de relecture d'une source. Au-delà, on ne la hache pas : l'état est
#: ``non_verifie`` avec la raison, jamais ``a_jour`` sur une lecture tronquée.
MAX_SOURCE_BYTES = 4 * 1024 * 1024

_CHUNK = 64 * 1024

# Clés de métadonnées posées à l'écriture.
KEY_INDEXED_AT = "indexed_at"
KEY_CONFIRMED_AT = "confirmed_at"
KEY_SOURCE_URI = "source_uri"
KEY_SOURCE_DIGEST = "source_digest"
KEY_SOURCE_DATE = "source_date"
KEY_FILE_PATH = "file_path"
KEY_LINE = "line_start"


class FreshnessState:
    """Les cinq états de fraîcheur (identifiants stables, sans accent)."""

    A_JOUR = "a_jour"
    PERIME = "perime"
    SOURCE_DISPARUE = "source_disparue"
    NON_VERIFIE = "non_verifie"
    HYPOTHESE = "hypothese"


_LABELS = {
    FreshnessState.A_JOUR: "à jour",
    FreshnessState.PERIME: "périmé",
    FreshnessState.SOURCE_DISPARUE: "source disparue",
    FreshnessState.NON_VERIFIE: "non vérifié",
    FreshnessState.HYPOTHESE: "hypothèse",
}

#: Ordre de service : les états qui se lisent sans réserve d'abord, l'hypothèse
#: en dernier (§5.2). Le tri est stable : l'ordre de pertinence est conservé
#: à l'intérieur d'un rang.
_SERVE_RANK = {
    FreshnessState.A_JOUR: 0,
    FreshnessState.PERIME: 0,
    FreshnessState.NON_VERIFIE: 0,
    FreshnessState.SOURCE_DISPARUE: 1,
    FreshnessState.HYPOTHESE: 2,
}

_URI_SCHEMES = {"task": "tâche", "session": "session", "commit": "commit"}


def label_of(state: str) -> str:
    """Libellé français d'un état de fraîcheur."""
    return _LABELS.get(state, state)


def serve_rank(entry: MemoryEntry) -> int:
    return _SERVE_RANK.get(entry.freshness, 0)


# ── Écriture ────────────────────────────────────────────────────────────────


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _confined(root: Path, rel: str) -> Path | None:
    """Le chemin ``rel`` sous ``root``, ou ``None`` s'il en sort."""
    if not rel:
        return None
    try:
        base = root.resolve()
        target = (base / rel).resolve()
        target.relative_to(base)
    except (OSError, ValueError, RuntimeError):
        return None
    return target


def file_digest(path: Path) -> str | None:
    """sha256 hexadécimal du contenu, ou ``None`` si illisible ou trop gros.

    Octets bruts, par blocs : pas de normalisation de fins de ligne, donc la
    même empreinte à l'indexation et à la relecture sur toute plateforme.
    """
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        digest = hashlib.sha256()
        read = 0
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                read += len(chunk)
                if read > MAX_SOURCE_BYTES:
                    return None
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _source_rel(metadata: dict[str, Any]) -> str:
    """Chemin relatif du fichier source déclaré, ``""`` si la source n'est pas un fichier."""
    explicit = str(metadata.get(KEY_SOURCE_URI) or "").strip()
    if explicit:
        scheme, sep, _ = explicit.partition(":")
        if sep and scheme in _URI_SCHEMES:
            return ""
        return explicit.rsplit(":", 1)[0] if _has_line_suffix(explicit) else explicit
    return str(metadata.get(KEY_FILE_PATH) or "").strip()


def _has_line_suffix(uri: str) -> bool:
    head, sep, tail = uri.rpartition(":")
    return bool(sep) and tail.isdigit() and bool(head) and len(head) > 1


def stamp_source(
    metadata: dict[str, Any], project_root: Path | None, cache: dict[str, tuple[str, str] | None] | None = None,
) -> dict[str, Any]:
    """Empreinte et date de la source fichier, si elle se relit — rien d'autre.

    Déterministe pour un contenu donné (aucune horloge de l'appel) : les
    constructeurs de projections, qui promettent des entrées reproductibles,
    peuvent l'appeler. Une source qu'on ne peut pas hacher (absente, trop
    grosse, hors projet) ne reçoit pas d'empreinte, et sera lue ``non_verifie`` :
    l'absence d'empreinte ne devient jamais ``a_jour``.
    """
    stamped = dict(metadata)
    rel = _source_rel(stamped)
    if not rel or project_root is None or KEY_SOURCE_DIGEST in stamped:
        return stamped
    if cache is not None and rel in cache:
        found = cache[rel]
    else:
        target = _confined(project_root, rel)
        digest = file_digest(target) if target is not None and target.is_file() else None
        found = (digest, _mtime_iso(target)) if digest is not None and target is not None else None
        if cache is not None:
            cache[rel] = found
    if found is not None:
        stamped[KEY_SOURCE_DIGEST] = found[0]
        stamped.setdefault(KEY_SOURCE_DATE, found[1])
    return stamped


def stamp_projection_entries(project_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pose l'empreinte de source sur des entrées de projection (une lecture par fichier)."""
    cache: dict[str, tuple[str, str] | None] = {}
    for entry in entries:
        entry["metadata"] = stamp_source(entry["metadata"], project_root, cache)
    return entries


def stamp_write(
    metadata: dict[str, Any], project_root: Path | None, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Provenance posée à l'écriture, sans jamais écraser ce que l'appelant a fixé.

    ``indexed_at`` : quand on l'a lu. Pour un fichier du projet : l'empreinte du
    contenu source à cet instant et sa date de modification (:func:`stamp_source`).
    """
    stamped = dict(metadata)
    stamped.setdefault(KEY_INDEXED_AT, _now_iso(now))
    return stamp_source(stamped, project_root)


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
    except OSError:
        return ""


def projection_is_current(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Une projection déjà écrite est-elle encore la bonne ?

    Le texte projeté peut rester identique alors que sa source a changé (un
    fichier long tronqué, un corps de fonction hors résumé) : comparer le seul
    ``content_hash`` laissait alors l'empreinte d'indexation vieillir, et
    l'entrée sortait ``périmé`` pour toujours. L'empreinte de la source compte
    donc aussi (``expected`` en porte une fraîche, voir
    :func:`stamp_projection_entries`).
    """
    return (
        existing.get("content_hash") == expected.get("content_hash")
        and existing.get(KEY_SOURCE_DIGEST) == expected.get(KEY_SOURCE_DIGEST)
    )


# ── Lecture ─────────────────────────────────────────────────────────────────


def _parse_when(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Source:
    kind: str
    uri: str
    rel: str  # chemin relisible, "" si la source n'est pas un fichier


def _source_of(entry: MemoryEntry) -> _Source:
    meta = entry.metadata or {}
    explicit = str(meta.get(KEY_SOURCE_URI) or "").strip()
    line = meta.get(KEY_LINE)
    suffix = f":{line}" if isinstance(line, int) and line > 0 else ""
    if explicit:
        scheme, sep, _ = explicit.partition(":")
        if sep and scheme in _URI_SCHEMES:
            return _Source(_URI_SCHEMES[scheme], explicit, "")
        return _Source("fichier", explicit, _source_rel(meta))
    path = str(meta.get(KEY_FILE_PATH) or "").strip()
    if path:
        group = str(meta.get("projection_group") or "")
        kind = {"code": "code", "docs": "doc"}.get(group, "fichier")
        return _Source(kind, path + suffix, path)
    task_id = str(meta.get("task_id") or "")
    if task_id and meta.get("projection_group") == "task_memory":
        return _Source("tâche", f"task:{task_id}", "")
    declared = str(meta.get("source_kind") or "").strip()
    if declared == "memory":
        return _Source("saisie libre", "", "")
    if declared:
        origin = str(meta.get("projection_source_id") or "")
        return _Source(declared, origin, "")
    return _Source("inconnue", "", "")


def _age_days(since: datetime | None, now: datetime) -> int | None:
    if since is None:
        return None
    return max((now - since).days, 0)


def _unlinked_state(confirmed: datetime | None, now: datetime) -> tuple[str, str]:
    """Fait sans source à relire : au mieux ``non_verifie``, jamais ``a_jour``."""
    age = _age_days(confirmed, now)
    if age is None:
        return FreshnessState.NON_VERIFIE, "aucune source à relire et date de confirmation inconnue"
    if age > STALE_DAYS:
        return FreshnessState.HYPOTHESE, f"aucune source à relire, dernière confirmation il y a {age} jours"
    return FreshnessState.NON_VERIFIE, f"aucune source à relire, dernière confirmation il y a {age} jours"


def _file_state(
    meta: dict[str, Any], source: _Source, root: Path | None, cache: dict[str, tuple[str | None, str]],
) -> tuple[str, str, str]:
    """(état, raison, date du changement) d'une source fichier, relue localement."""
    if root is None:
        return FreshnessState.NON_VERIFIE, "racine du projet inconnue : source non relue", ""
    target = _confined(root, source.rel)
    if target is None:
        return FreshnessState.NON_VERIFIE, "chemin hors du projet : source non relue", ""
    if source.rel not in cache:
        digest = None
        reason = ""
        if not target.exists():
            reason = "absent"
        elif not target.is_file():
            reason = "pas un fichier"
        else:
            digest = file_digest(target)
            if digest is None:
                reason = "illisible ou trop volumineux"
        cache[source.rel] = (digest, reason)
    digest, reason = cache[source.rel]
    if reason == "absent":
        return FreshnessState.SOURCE_DISPARUE, "le fichier source n'existe plus", ""
    if digest is None:
        return FreshnessState.NON_VERIFIE, f"source non relue ({reason})", ""
    stored = str(meta.get(KEY_SOURCE_DIGEST) or "")
    if not stored:
        return FreshnessState.NON_VERIFIE, "empreinte d'indexation absente : indexé avant le suivi de provenance", ""
    if stored == digest:
        return FreshnessState.A_JOUR, "l'empreinte de la source égale celle de l'indexation", ""
    return FreshnessState.PERIME, "la source a changé depuis l'indexation", _mtime_iso(target)


def _fact(
    entry: MemoryEntry, source: _Source, state: str, reason: str, changed: str, project: str,
) -> dict[str, Any]:
    meta = entry.metadata or {}
    superseded_by = str(meta.get(SUPERSEDED_KEY) or "")
    indexed = str(meta.get(KEY_INDEXED_AT) or entry.created_at or entry.updated_at or "")
    producer = (
        "projection" if meta.get("source_kind") == "projection"
        else "agent" if meta.get("agent") else "inconnu"
    )
    return {
        "schema": FACT_SCHEMA,
        "id": entry.id,
        "projet": project,
        "type": str(meta.get("memory_type") or meta.get("type") or ""),
        "source": {
            "kind": source.kind,
            "uri": source.uri,
            "empreinte": str(meta.get(KEY_SOURCE_DIGEST) or ""),
        },
        "date_source": str(meta.get(KEY_SOURCE_DATE) or ""),
        "date_indexation": indexed,
        "date_confirmation": str(meta.get(KEY_CONFIRMED_AT) or ""),
        "date_changement": changed,
        "fraicheur": state,
        "fraicheur_libelle": label_of(state),
        "fraicheur_raison": reason,
        "cycle": "remplacé" if superseded_by else "actif",
        "remplace_par": superseded_by,
        "producteur": producer,
    }


def annotate(
    entries: list[MemoryEntry],
    project_root: Path | None,
    *,
    project: str = "",
    now: datetime | None = None,
) -> list[MemoryEntry]:
    """Attache la fiche et la fraîcheur recalculée à chaque entrée.

    Idempotent et sans effet de bord : rien n'est écrit, les sources fichier
    sont relues une seule fois par appel (cache local).
    """
    moment = now or datetime.now(UTC)
    cache: dict[str, tuple[str | None, str]] = {}
    annotated: list[MemoryEntry] = []
    for entry in entries:
        meta = entry.metadata or {}
        source = _source_of(entry)
        changed = ""
        if source.rel:
            state, reason, changed = _file_state(meta, source, project_root, cache)
        else:
            confirmed = _parse_when(
                meta.get(KEY_CONFIRMED_AT) or meta.get(KEY_INDEXED_AT) or entry.updated_at or entry.created_at
            )
            state, reason = _unlinked_state(confirmed, moment)
        fact = _fact(entry, source, state, reason, changed, project or str(meta.get("project_name") or ""))
        shown = source.uri or ("saisie libre" if source.kind == "saisie libre" else "inconnue")
        annotated.append(replace(
            entry,
            provenance=fact,
            freshness=state,
            source=shown,
            task_ref=str(meta.get("task_ref") or ""),
        ))
    return annotated


def order_for_serving(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Hypothèses en dernier, ordre de pertinence conservé dans chaque rang."""
    return sorted(entries, key=serve_rank)
