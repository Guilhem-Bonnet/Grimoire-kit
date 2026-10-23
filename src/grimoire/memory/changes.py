"""Provenance et journal des écritures mémoire.

Chaque écriture du :class:`~grimoire.memory.manager.MemoryManager` (``store``,
``update``, ``supersede``, ``delete``, ``invalidate_fact``) laisse une ligne
JSONL dans ``_grimoire-output/memory-changes.jsonl`` : qui (acteur, session,
tâche), quoi (ids avant/après, empreinte), pourquoi (raison). Le journal part
du manager, jamais d'une route HTTP : une écriture qui contourne le manager ne
laisse pas de trace, et c'est le manager qu'il faut donc emprunter.

Acteur, session et tâche viennent des arguments explicites s'ils sont passés,
sinon de la session et du claim actifs ; absents des deux, le champ est omis.
Jamais de valeur par défaut inventée.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: Journal des écritures, relatif à la racine du projet.
CHANGES_RELPATH = Path("_grimoire-output") / "memory-changes.jsonl"

#: Clés de métadonnées qui portent la provenance d'une écriture.
ACTOR_KEY = "actor"
SESSION_KEY = "session_id"
TASK_KEY = "task_ref"


def resolve_provenance(
    project_root: Path | None,
    *,
    actor: str = "",
    session_id: str = "",
    task_ref: str = "",
) -> dict[str, str]:
    """``{actor, session_id, task_ref}`` restreint aux champs connus.

    Argument explicite d'abord, puis la session d'hôte (``GRIMOIRE_SESSION_ID``
    ou variable de l'hôte), l'acteur (``GRIMOIRE_ACTOR``) et le claim actif du
    ledger. Un champ qu'aucune source ne donne est absent du résultat.
    """
    from grimoire.core.standard_state import ACTOR_ENV, current_session_id

    resolved = {
        ACTOR_KEY: actor.strip() or os.environ.get(ACTOR_ENV, "").strip(),
        SESSION_KEY: session_id.strip() or current_session_id(),
        TASK_KEY: task_ref.strip(),
    }
    if not resolved[TASK_KEY] and project_root is not None:
        try:
            from grimoire.core.standard_state import resolve_active_task

            active = resolve_active_task(
                project_root, session_id=resolved[SESSION_KEY], write_cache=False
            )
            if not active.is_fallback:
                resolved[TASK_KEY] = active.task_id
        except Exception as exc:  # une provenance introuvable ne bloque jamais l'écriture
            _log.debug("claim actif illisible, tâche omise : %s", exc)
    return {key: value for key, value in resolved.items() if value}


def fingerprint(*parts: str) -> str:
    """Empreinte SHA-256 stable de *parts*."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def append_change(
    project_root: Path | None,
    *,
    op: str,
    provenance: dict[str, str],
    ids_before: list[str],
    ids_after: list[str],
    digest: str,
    reason: str = "",
) -> dict[str, Any] | None:
    """Ajouter une ligne au journal ; ``None`` sans racine de projet.

    Un échec d'écriture du journal est signalé dans les logs sans défaire
    l'écriture mémoire qu'il documente.
    """
    if project_root is None:
        return None
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "op": op,
        **provenance,
        "ids_before": ids_before,
        "ids_after": ids_after,
        "hash": digest,
    }
    if reason.strip():
        record["reason"] = reason.strip()
    path = Path(project_root) / CHANGES_RELPATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        _log.warning("journal des écritures mémoire illisible (%s) : %s", path, exc)
        return None
    return record
