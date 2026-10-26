"""Reçu de rappel lisible et rejouable dans le cockpit (P27).

Le reçu est écrit par la mémoire elle-même (P13, ``memory/reads_log.py``) : une
ligne de ``_grimoire-output/memory-reads.jsonl`` par lecture servie aux agents,
avec les identifiants servis, leur rang et leur score. Ce module le **lit** et
le **rejoue**, rien d'autre. Deux lectures, servies en ``GET`` :

- :func:`recall_receipts` — les reçus d'une tâche (``tasks/<id>/receipts``) ou
  les derniers du projet (``memory/recall``), chaque entrée servie décrite telle
  qu'elle est **aujourd'hui** : titre, type, portée, état (``live``,
  ``superseded``, ``retracted``, ``orphan``, ``missing``) et fraîcheur ;
- :func:`recall_replay` — relance la même requête, même mode et mêmes filtres,
  sur la mémoire d'aujourd'hui (``memory/recall/replay``) et rend le
  différentiel (:func:`recall_diff`). Une requête libre se rejoue de la même
  façon : le premier passage rend la liste, le suivant la compare à celle-là.

Refus tenus ici :

- lecture seule : le manager est ouvert par
  :func:`~grimoire.tools.workspace_knowledge.open_readonly` (``journal_reads``
  faux), donc rejouer depuis le cockpit n'écrit **aucun** reçu ;
- une lecture ratée n'est pas un zéro : journal absent, mémoire illisible ou
  requête non conservée sont dits par leur motif, jamais par une liste vide ;
- dans un emplacement partagé, l'entrée d'un autre projet n'est décrite que par
  sa portée, jamais par son contenu (même règle que Savoir) ;
- une lecture antérieure à P27, ou faite hors tâche, n'a pas gardé sa requête :
  elle reste lisible, elle n'est pas rejouable, et c'est dit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.memory.reads_log import MAX_BYTES, READS_RELPATH

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry
    from grimoire.tools.workspace_knowledge import Ownership

__all__ = [
    "DIFF_KINDS",
    "RECEIPTS_SCHEMA_VERSION",
    "REPLAY_SCHEMA_VERSION",
    "recall_diff",
    "recall_receipts",
    "recall_replay",
    "receipt_key",
]

RECEIPTS_SCHEMA_VERSION = "grimoire-recall-receipts/v1"
REPLAY_SCHEMA_VERSION = "grimoire-recall-replay/v1"

#: Reçus rendus au plus par lecture (les plus récents d'abord).
MAX_RECEIPTS = 20
#: Souvenirs demandés par une requête libre, par défaut et au plus.
FREE_LIMIT = 5
FREE_LIMIT_MAX = 20
#: Identifiants de base acceptés pour comparer une requête libre.
MAX_BASE = 50

#: Les sorties possibles d'un rejeu, dans l'ordre où l'interface les liste.
DIFF_KINDS: tuple[str, ...] = ("appeared", "replaced", "demoted", "vanished", "promoted", "same")

_REPLAYABLE_MODES = frozenset({"search", "hybrid", "taxonomy", "typed", "task_recall"})
#: Surfaces dont les lectures ne sont pas un service rendu aux agents (comme ``read_counts``).
_UNSHOWN_SURFACES = frozenset({"bench"})


# ── Le journal ──────────────────────────────────────────────────────────────


def receipt_key(line: str) -> str:
    """Identifiant stable d'un reçu : l'empreinte de sa ligne (16 hex)."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def _journal(project_root: Path) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
    """``([(clé, ligne)…], présent)`` dans l'ordre du fichier ; lignes illisibles écartées.

    Le fichier tourne à :data:`MAX_BYTES` : il est lu en entier, au plus deux
    fois ce plafond (une écriture concurrente pendant la rotation).
    """
    path = project_root / READS_RELPATH
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > 2 * MAX_BYTES:
                handle.seek(size - 2 * MAX_BYTES)
                handle.readline()
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return [], False
    rows: list[tuple[str, dict[str, Any]]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("surface") not in _UNSHOWN_SURFACES:
            rows.append((receipt_key(line), row))
    return rows, True


# ── Décrire une entrée servie telle qu'elle est aujourd'hui ─────────────────


class _Lens:
    """Ce qu'il faut pour décrire un identifiant servi : manager, propriété, fiche."""

    def __init__(self, root: Path, slug: str, name: str, cfg: Any, mgr: Any) -> None:
        from grimoire.tools.workspace_knowledge import _memoized, journal_index, ownership

        self.root, self.slug, self.name, self.mgr = root, slug, name, mgr
        self.owners: Ownership = ownership(cfg, root)
        self.recall: Callable[[str], MemoryEntry | None] = _memoized(mgr.backend.recall)
        self.journal = journal_index(root)

    def state_of(self, entry_id: str) -> str:
        from grimoire.memory.gestures import entry_state

        entry = self.recall(entry_id)
        return "missing" if entry is None else entry_state(entry, self.recall)

    def describe(self, entry_id: str, entry: MemoryEntry | None = None) -> dict[str, Any]:
        """``id``, ``title``, ``type``, ``scope``, ``state``, ``freshness`` — ou le motif de leur absence."""
        from grimoire.memory.gestures import entry_state
        from grimoire.memory.knowledge import annotate, label_of
        from grimoire.tools.workspace_knowledge import describe

        found = entry if entry is not None else self.recall(entry_id)
        if found is None:
            return {"id": entry_id, "found": False, "state": "missing", "scope": None,
                    "title": None, "type": None, "freshness": None, "freshnessLabel": None}
        visibility = self.owners.visibility(found)
        if visibility == "other":
            return {"id": entry_id, "found": True, "hidden": True, "state": entry_state(found, self.recall),
                    "scope": "other", "title": None, "type": None, "freshness": None, "freshnessLabel": None}
        served = annotate([found], self.root, project=self.name)[0]
        card = describe(served, slug=self.slug, name=self.name, recall=self.recall, journal=self.journal)
        return {
            "id": entry_id, "found": True, "hidden": False,
            "title": card["title"] or entry_id, "kind": card["kind"], "type": card["type"],
            "scope": "project" if visibility == "own" else "unattributed",
            "state": entry_state(found, self.recall),
            "freshness": card["freshness"], "freshnessLabel": label_of(card["freshness"]) if card["freshness"] else None,
            "replacedBy": card["supersession"]["replacedBy"],
        }


def _open(project_root: Path, slug: str | None) -> tuple[Any, _Lens | None, str | None]:
    """``(cible, lentille, motif)`` — la lentille est ``None`` quand la mémoire ne s'ouvre pas."""
    from grimoire.tools.workspace_knowledge import _target, open_readonly

    target = _target(project_root, slug)
    if target.root is None:
        return target, None, target.reason
    cfg, mgr, reason = open_readonly(target.root)
    if mgr is None:
        return target, None, reason
    name = target.name or str(cfg.project.name or target.root.name)
    return target, _Lens(target.root, target.slug, name, cfg, mgr), None


# ── Les reçus ───────────────────────────────────────────────────────────────


def _task_titles(root: Path, ids: set[str]) -> dict[str, str]:
    """Titre de chaque tâche nommée par un reçu, best-effort (ledger absent : rien)."""
    if not ids:
        return {}
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.recall import DEFAULT_LEDGER_RELPATH

        ledger_path = root / DEFAULT_LEDGER_RELPATH
        if not (ledger_path / "events.jsonl").is_file():
            return {}
        ledger = MissionLedger(ledger_path)
        titles: dict[str, str] = {}
        for task_id in ids:
            task = ledger.get_task(task_id)
            if task is not None:
                titles[task_id] = task.title
        return titles
    except Exception:
        return {}


def _not_replayable(row: Mapping[str, Any]) -> str | None:
    if row.get("mode") not in _REPLAYABLE_MODES:
        return f"mode de lecture inconnu : {row.get('mode')!r}"
    if not str(row.get("query") or "").strip():
        return ("requête non conservée : lecture faite hors tâche ou avant que le reçu garde sa requête"
                if row.get("query_chars") else "lecture sans requête")
    return None


def _served_of(row: Mapping[str, Any], lens: _Lens | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in row.get("served") or ():
        if not isinstance(item, dict) or "id" not in item:
            continue
        entry_id = str(item["id"])
        described: dict[str, Any] = lens.describe(entry_id) if lens is not None else {"id": entry_id}
        out.append({
            **described,
            "rank": item.get("rank"),
            "score": item.get("score"),
            "lanes": item.get("lanes") or {},
        })
    return out


def _receipt(key: str, row: Mapping[str, Any], lens: _Lens | None, titles: Mapping[str, str]) -> dict[str, Any]:
    task_ref = str(row.get("task_ref") or "")
    why_not = _not_replayable(row)
    return {
        "key": key,
        "ts": row.get("ts"),
        "mode": row.get("mode"),
        "surface": row.get("surface"),
        "actor": row.get("actor") or "",
        "sessionId": row.get("session_id") or "",
        "taskRef": task_ref,
        "taskTitle": titles.get(task_ref),
        "query": row.get("query") or None,
        "queryChars": row.get("query_chars"),
        "filters": dict(row.get("filters") or {}),
        "hiddenStale": int(row.get("hidden_stale") or 0),
        "servedTruncated": row.get("served_truncated"),
        "served": _served_of(row, lens),
        "replayable": why_not is None and lens is not None,
        "notReplayable": why_not,
    }


def recall_receipts(
    project_root: Path, *, task_id: str | None = None, slug: str | None = None, limit: object = MAX_RECEIPTS,
) -> dict[str, Any]:
    """Charge utile de ``GET tasks/<id>/receipts`` (``task_id``) et ``GET memory/recall`` (sans).

    ``logPresent`` faux : aucun journal des lectures chez ce projet. ``receipts``
    vide avec un journal : aucune lecture rattachée à cette tâche. ``memoryReason``
    dit pourquoi les entrées ne sont pas décrites (mémoire illisible).
    """
    from grimoire.tools.workspace_knowledge import _as_int, _target

    size = _as_int(limit, MAX_RECEIPTS, low=1, high=MAX_RECEIPTS)
    target = _target(project_root, slug)
    payload: dict[str, Any] = {
        "schemaVersion": RECEIPTS_SCHEMA_VERSION,
        "project": {"slug": target.slug, "name": target.name},
        "taskId": task_id or None,
        "reason": target.reason,
        "logPresent": False,
        "memoryReason": None,
        "receipts": [],
        "total": 0,
    }
    if target.root is None:
        return payload
    rows, present = _journal(target.root)
    wanted = [(k, r) for k, r in rows if not task_id or str(r.get("task_ref") or "") == task_id]
    payload.update({"logPresent": present, "total": len(wanted)})
    if not wanted:
        return payload
    newest = list(reversed(wanted))[:size]
    _, lens, reason = _open(target.root, slug)
    if lens is not None:
        payload["project"] = {"slug": lens.slug, "name": lens.name}
    payload["memoryReason"] = reason
    titles = _task_titles(target.root, {str(r.get("task_ref")) for _k, r in newest if r.get("task_ref")})
    payload["receipts"] = [_receipt(k, r, lens, titles) for k, r in newest]
    return payload


# ── Le rejeu ────────────────────────────────────────────────────────────────


def _flag(filters: Mapping[str, Any], key: str) -> bool:
    return bool(filters.get(key))


def _int(filters: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return max(1, min(50, int(filters.get(key) or default)))
    except (TypeError, ValueError):
        return default


def _rerun(mgr: Any, mode: str, query: str, filters: Mapping[str, Any]) -> list[MemoryEntry]:
    """La même lecture que le reçu : même mode, mêmes filtres, sur la mémoire d'aujourd'hui."""
    limit = _int(filters, "limit", FREE_LIMIT)
    user_id = str(filters.get("user_id") or "")
    flags = {"include_superseded": _flag(filters, "include_superseded"),
             "include_retracted": _flag(filters, "include_retracted")}
    if mode == "search":
        return list(mgr.search(query, user_id=user_id, limit=limit, include_stale=_flag(filters, "include_stale"), **flags))
    if mode == "hybrid":
        return list(mgr.hybrid_search(
            query, user_id=user_id, limit=limit, include_stale=_flag(filters, "include_stale"), **flags))
    if mode == "taxonomy":
        return list(mgr.search_taxonomy(
            query, user_id=user_id, limit=limit, wing=str(filters.get("wing") or ""),
            hall=str(filters.get("hall") or ""), room=str(filters.get("room") or ""),
            include_stale=_flag(filters, "include_stale"), **flags))
    if mode == "typed":
        return list(mgr.recall_typed(
            query, type_=str(filters.get("type") or ""), agent=str(filters.get("agent") or ""),
            limit=limit, log_read=False, **flags))
    if mode == "task_recall":
        from grimoire.missions.recall import task_recall_entries

        return [entry for _type, entry in task_recall_entries(mgr, query, limit=limit)][:limit]
    from grimoire.memory.taxonomy import run_memory_search

    return list(run_memory_search(mgr, query, hybrid=mgr.prefers_hybrid, limit=limit))


def recall_diff(
    before: Sequence[tuple[str, int]], after: Sequence[tuple[str, int]], state_of: Callable[[str], str],
) -> dict[str, Any]:
    """Différentiel de deux listes classées ``(id, rang)`` — calcul pur.

    - ``appeared`` : servie aujourd'hui, absente du reçu ;
    - ``replaced`` : servie alors, absente aujourd'hui parce que remplacée ou retirée
      depuis (``state`` le dit) ;
    - ``vanished`` : servie alors, absente aujourd'hui sans avoir été remplacée
      (supprimée, ou simplement sortie du lot) ;
    - ``demoted`` / ``promoted`` : servie les deux fois, rang moins bon / meilleur ;
    - ``same`` : même rang.
    """
    then = dict(before)
    now = dict(after)
    rows: list[dict[str, Any]] = []
    for entry_id, rank in after:
        if entry_id not in then:
            rows.append({"id": entry_id, "kind": "appeared", "before": None, "after": rank})
            continue
        old = then[entry_id]
        kind = "same" if old == rank else ("demoted" if rank > old else "promoted")
        rows.append({"id": entry_id, "kind": kind, "before": old, "after": rank})
    for entry_id, rank in before:
        if entry_id in now:
            continue
        state = state_of(entry_id)
        kind = "replaced" if state in {"superseded", "retracted"} else "vanished"
        rows.append({"id": entry_id, "kind": kind, "before": rank, "after": None, "state": state})
    order = {kind: i for i, kind in enumerate(DIFF_KINDS)}
    rows.sort(key=lambda r: (order[r["kind"]], r["after"] or r["before"] or 0))
    counts = dict.fromkeys(DIFF_KINDS, 0)
    for row in rows:
        counts[row["kind"]] += 1
    changed = sum(n for kind, n in counts.items() if kind != "same")
    return {"rows": rows, "counts": counts, "changed": changed}


def _base_ids(raw: str | None) -> list[str] | None:
    """La liste classée d'une requête libre déjà passée, ``None`` au premier passage."""
    if raw is None:
        return None
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    return list(dict.fromkeys(ids))[:MAX_BASE]


def recall_replay(
    project_root: Path,
    *,
    receipt: str | None = None,
    task_id: str | None = None,
    query: str | None = None,
    base: str | None = None,
    limit: object = FREE_LIMIT,
    slug: str | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET memory/recall/replay`` — lecture seule, aucun reçu écrit.

    ``receipt`` (clé d'un reçu) rejoue ce reçu ; sinon ``q`` est une requête
    libre, et ``base`` (identifiants séparés par des virgules, dans l'ordre servi)
    la liste du passage précédent : sans ``base``, pas de différentiel.
    """
    from grimoire.tools.workspace_knowledge import _as_int

    payload: dict[str, Any] = {
        "schemaVersion": REPLAY_SCHEMA_VERSION, "project": {"slug": slug or "", "name": ""},
        "reason": None, "receipt": None, "query": None, "mode": None,
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": [], "diff": None,
    }
    target, lens, reason = _open(project_root, slug)
    payload["project"] = {"slug": target.slug, "name": lens.name if lens is not None else target.name}
    if lens is None:
        return {**payload, "reason": reason}
    if receipt:
        rows, _present = _journal(lens.root)
        found = next(((k, r) for k, r in rows if k == receipt), None)
        if found is None:
            return {**payload, "reason": "reçu introuvable : le journal des lectures a tourné ou la clé est fausse"}
        key, row = found
        if task_id and str(row.get("task_ref") or "") != task_id:
            return {**payload, "reason": "ce reçu n'appartient pas à cette tâche"}
        why_not = _not_replayable(row)
        described = _receipt(key, row, lens, _task_titles(lens.root, {str(row.get("task_ref") or "")} - {""}))
        payload.update({"receipt": described, "mode": row.get("mode"), "query": row.get("query") or None})
        if why_not is not None:
            return {**payload, "reason": why_not}
        mode, text, filters = str(row["mode"]), str(row["query"]), dict(row.get("filters") or {})
        before: list[tuple[str, int]] | None = [
            (str(s["id"]), int(s.get("rank") or i)) for i, s in enumerate(row.get("served") or (), start=1)
            if isinstance(s, dict) and "id" in s
        ]
    else:
        text = " ".join(str(query or "").split())
        if not text:
            return {**payload, "reason": "requête vide : rien à rejouer"}
        mode = "free"
        filters = {"limit": _as_int(limit, FREE_LIMIT, low=1, high=FREE_LIMIT_MAX)}
        ids = _base_ids(base)
        before = None if ids is None else [(entry_id, rank) for rank, entry_id in enumerate(ids, start=1)]
        payload.update({"mode": mode, "query": text})
    try:
        served = _rerun(lens.mgr, mode, text, filters)
    except Exception as exc:  # une mémoire qui casse au rejeu est un motif, jamais un lot vide
        return {**payload, "reason": f"rejeu impossible : {exc}"}
    payload["results"] = [
        {**lens.describe(entry.id, entry),
         "rank": rank, "score": round(float(entry.score), 6)}
        for rank, entry in enumerate(served, start=1)
    ]
    if before is not None:
        diff = recall_diff(before, [(r["id"], r["rank"]) for r in payload["results"]], lens.state_of)
        titles = {r["id"]: r for r in payload["results"]}
        for row in diff["rows"]:
            row["entry"] = titles.get(row["id"]) or lens.describe(row["id"])
        payload["diff"] = diff
    return payload
