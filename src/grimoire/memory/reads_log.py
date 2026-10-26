"""Reçu de rappel : le journal des lectures mémoire servies aux agents (P13).

``MemoryManager`` écrit une ligne dans ``_grimoire-output/memory-reads.jsonl``
à chaque lecture qui sert des souvenirs (recherche, recherche hybride,
taxonomie, rappel typé, rappel de tâche). Le journal répond à « qu'est-ce que
la mémoire a réellement servi, à qui, et quand ? » sans jamais porter ce qu'elle
a servi.

**Ce qu'une ligne contient** (les champs inconnus sont omis, jamais remplacés
par une valeur vide) :

* ``ts`` : horodatage UTC ; ``mode`` : ``search``, ``hybrid``, ``taxonomy``,
  ``typed`` ou ``task_recall`` ; ``surface`` : ``cli``, ``mcp``, ``cockpit``,
  ``api`` ou ``bench`` ;
* ``actor``, ``session_id``, ``task_ref`` : la provenance de la lecture
  (même résolution que le journal des écritures) ;
* ``query_chars`` : la longueur de la requête. **Jamais d'empreinte** :
  l'empreinte d'une requête courte se retrouve par dictionnaire (une empreinte à
  clé locale demanderait de créer un second fichier, ce que les outils annotés
  en lecture seule n'ont pas le droit de faire) ;
* ``query`` : le texte de la requête, **seulement pour une lecture rattachée à
  une tâche** (``task_ref``), secrets caviardés (``redact_secrets``) et borné à
  :data:`MAX_QUERY_CHARS`. C'est ce que l'inspecteur de la tâche montre et
  rejoue (P27). Hors tâche, le texte n'est pas gardé : une requête libre peut
  porter une donnée personnelle que personne n'a demandé de conserver ;
* ``filters`` : les filtres appliqués (limite, utilisateur, type, agent, ailes,
  ``include_*``) ;
* ``served`` : pour chaque souvenir servi, ``id``, ``rank`` (1 = premier), ``score``
  et, en lecture hybride, ``lanes`` (rang et score dans chaque classement
  avant fusion) ;
* ``hidden_stale`` : le nombre d'extraits dont la source a disparu, écartés.

**Ce qu'elle ne contient pas** : le texte, les étiquettes, les métadonnées ni
le titre d'un souvenir, ni le texte d'une requête hors tâche.

**Bornes** : au-delà de ``MAX_BYTES`` le fichier est ramené à ses lignes les plus
récentes (la moitié du plafond), réécrit atomiquement ; une lecture concurrente
qui s'ajoute pendant cette réécriture peut perdre sa ligne, le journal est un
reçu, pas un registre d'audit. Au plus ``MAX_SERVED`` souvenirs par ligne. **Jamais bloquant** : une écriture qui échoue est un
avertissement dans les logs, la lecture a déjà eu lieu. Aucun appel réseau.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core import fsio

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry

__all__ = [
    "MAX_BYTES",
    "MAX_QUERY_CHARS",
    "MAX_SERVED",
    "READS_RELPATH",
    "ReadCounts",
    "read_counts",
    "record_read",
]

_log = logging.getLogger(__name__)

READS_RELPATH = Path("_grimoire-output") / "memory-reads.jsonl"
#: Taille au-delà de laquelle le fichier tourne (1 Mio, soit quelques milliers de lectures).
MAX_BYTES = 1_048_576
#: Souvenirs détaillés par ligne ; au-delà, ``served_truncated`` donne le total.
MAX_SERVED = 50
#: Longueur gardée d'une requête rattachée à une tâche (même borne que le rappel de tâche).
MAX_QUERY_CHARS = 300
#: Surfaces dont les lectures ne comptent pas comme un service rendu aux agents.
_UNCOUNTED_SURFACES = frozenset({"bench"})


def _served_rows(
    entries: Sequence[MemoryEntry], lanes: Mapping[str, Sequence[tuple[str, float]]] | None,
) -> list[dict[str, Any]]:
    lane_rank = {
        name: {entry_id: (rank, score) for rank, (entry_id, score) in enumerate(ranking, start=1)}
        for name, ranking in (lanes or {}).items()
    }
    rows: list[dict[str, Any]] = []
    for rank, entry in enumerate(entries[:MAX_SERVED], start=1):
        row: dict[str, Any] = {"id": entry.id, "rank": rank, "score": round(float(entry.score), 6)}
        per_lane = {
            name: {"rank": found[0], "score": round(float(found[1]), 6)}
            for name, ranks in lane_rank.items()
            if (found := ranks.get(entry.id)) is not None
        }
        if per_lane:
            row["lanes"] = per_lane
        rows.append(row)
    return rows


def _kept_query(query: str) -> str:
    """Le texte d'une requête rattachée à une tâche : secrets caviardés, espaces réduits, borné."""
    from grimoire.memory.validation import redact_secrets

    redacted, _ = redact_secrets(" ".join(query.split()))
    return redacted[:MAX_QUERY_CHARS]


def _rotate(path: Path) -> None:
    """Un journal trop gros est ramené à ses lignes les plus récentes (moitié du plafond)."""
    try:
        if path.stat().st_size < MAX_BYTES:
            return
        kept: list[bytes] = []
        size = 0
        for line in reversed(path.read_bytes().splitlines(keepends=True)):
            size += len(line)
            if size > MAX_BYTES // 2:
                break
            kept.append(line)
        fsio.atomic_write(path, b"".join(reversed(kept)))
    except FileNotFoundError:
        return


def record_read(
    project_root: Path | None,
    *,
    mode: str,
    surface: str,
    served: Sequence[MemoryEntry],
    query: str = "",
    filters: Mapping[str, Any] | None = None,
    lanes: Mapping[str, Sequence[tuple[str, float]]] | None = None,
    actor: str = "",
    session_id: str = "",
    task_ref: str = "",
    hidden_stale: int = 0,
) -> None:
    """Ajouter une ligne au journal des lectures. Ne lève jamais.

    Sans racine de projet il n'y a pas de journal (manager sans projet). Un
    échec — disque plein, droits, répertoire absent — devient un avertissement :
    la lecture qui l'a déclenché a déjà réussi et ne doit pas échouer pour un
    reçu.
    """
    if project_root is None:
        return
    try:
        row: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "mode": mode,
            "surface": surface,
        }
        row.update({k: v for k, v in (("actor", actor), ("session_id", session_id), ("task_ref", task_ref)) if v})
        if query:
            row["query_chars"] = len(query)
            if task_ref:
                row["query"] = _kept_query(query)
        if filters:
            row["filters"] = {k: v for k, v in filters.items() if v not in ("", None, False)}
        row["served"] = _served_rows(served, lanes)
        if len(served) > MAX_SERVED:
            row["served_truncated"] = len(served)
        if hidden_stale:
            row["hidden_stale"] = hidden_stale
        path = project_root / READS_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    except Exception:  # un reçu n'a jamais le droit de faire échouer la lecture
        _log.warning("journal des lectures mémoire inaccessible", exc_info=True)


@dataclass(frozen=True, slots=True)
class ReadCounts:
    """Ce que le journal dit des ``days`` derniers jours."""

    days: int
    reads: int
    #: Lectures qui ont servi au moins un souvenir.
    reads_with_results: int
    #: Identifiant -> nombre de lectures qui l'ont servi (``recall_count_7d`` pour 7 jours).
    per_memory: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "reads": self.reads,
            "reads_with_results": self.reads_with_results,
            f"recall_count_{self.days}d": dict(self.per_memory),
        }


def _lines(path: Path) -> Iterable[str]:
    try:
        with path.open(encoding="utf-8") as handle:
            yield from handle
    except OSError:
        return


def read_counts(project_root: Path, *, days: int = 7, now: datetime | None = None) -> ReadCounts:
    """Combien de fois chaque souvenir a été servi sur ``days`` jours.

    Les lignes illisibles
    ou sans horodatage sont ignorées, jamais comptées. Les lectures du banc ne
    comptent pas.
    """
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    counts: dict[str, int] = {}
    reads = with_results = 0
    base = project_root / READS_RELPATH
    for line in _lines(base):
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(str(row["ts"]))
        except (ValueError, KeyError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when < since or not isinstance(row, dict) or row.get("surface") in _UNCOUNTED_SURFACES:
            continue
        reads += 1
        ids = {str(item["id"]) for item in row.get("served", ()) if isinstance(item, dict) and "id" in item}
        if ids:
            with_results += 1
        for entry_id in ids:
            counts[entry_id] = counts.get(entry_id, 0) + 1
    return ReadCounts(days=days, reads=reads, reads_with_results=with_results, per_memory=counts)
