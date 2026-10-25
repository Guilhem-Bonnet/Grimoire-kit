"""Provenance et journal des écritures mémoire (P14).

Deux choses, posées par ``MemoryManager`` et jamais par une route HTTP :

* une **provenance d'écriture** (``actor``, ``session_id``, ``task_ref``) dans
  les métadonnées de l'entrée ;
* une **ligne de journal** par mutation dans
  ``_grimoire-output/memory-changes.jsonl`` : qui, dans quelle session, pour
  quelle tâche, quels identifiants avant/après, quelle empreinte, pourquoi.

Règle commune : une valeur inconnue est **omise**, jamais remplacée par une
chaîne vide ou un placeholder. Une provenance inventée est pire qu'une absence.

Ordre de résolution, champ par champ : argument explicite, puis environnement
de la session (``GRIMOIRE_ACTOR``, ``GRIMOIRE_SESSION_ID`` /
``CLAUDE_CODE_SESSION_ID``), puis claim actif du ledger pour la tâche.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

JOURNAL_RELPATH = Path("_grimoire-output") / "memory-changes.jsonl"

#: Clés posées dans ``MemoryEntry.metadata``.
KEY_ACTOR = "actor"
KEY_SESSION = "session_id"
KEY_TASK_REF = "task_ref"

_FINGERPRINT_LENGTH = 16


@dataclass(frozen=True, slots=True)
class WriteContext:
    """Qui écrit, depuis quelle session, pour quelle tâche — champs vides = inconnus."""

    actor: str = ""
    session_id: str = ""
    task_ref: str = ""

    def as_metadata(self) -> dict[str, str]:
        """Les seuls champs connus, sous leurs clés de métadonnées."""
        pairs = ((KEY_ACTOR, self.actor), (KEY_SESSION, self.session_id), (KEY_TASK_REF, self.task_ref))
        return {key: value for key, value in pairs if value}


def resolve_write_context(
    project_root: Path | None,
    *,
    actor: str = "",
    session_id: str = "",
    task_ref: str = "",
    env: Mapping[str, str] | None = None,
) -> WriteContext:
    """Résoudre la provenance d'une écriture : explicite, sinon session et claim actifs."""
    from grimoire.core.standard_state import ACTOR_ENV, current_session_id, resolve_active_task

    environ = os.environ if env is None else env
    actor = actor.strip() or str(environ.get(ACTOR_ENV, "")).strip()
    session_id = session_id.strip() or current_session_id(environ)
    task_ref = task_ref.strip()
    if not task_ref and project_root is not None:
        try:
            active = resolve_active_task(
                project_root, env=environ, write_cache=False, session_id=session_id
            )
        except Exception:  # la provenance ne doit jamais empêcher d'écrire
            _log.debug("résolution de la tâche active impossible", exc_info=True)
        else:
            if not active.is_fallback:
                task_ref = active.task_id
    return WriteContext(actor=actor, session_id=session_id, task_ref=task_ref)


def fingerprint(text: str) -> str:
    """Empreinte courte et stable d'un contenu — le journal ne porte jamais le texte."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]


def record_change(
    project_root: Path | None,
    *,
    op: str,
    context: WriteContext,
    before_id: str = "",
    after_id: str = "",
    content: str = "",
    reason: str = "",
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Ajouter une ligne au journal ; sans racine de projet, il n'y a pas de journal.

    Un échec d'écriture du journal est consigné (``warning``) sans défaire une
    mutation déjà faite : la lever ici ferait croire que la mémoire n'a pas bougé.
    """
    if project_root is None:
        return
    row: dict[str, Any] = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "op": op}
    row.update(context.as_metadata())
    if before_id:
        row["before_id"] = before_id
    if after_id:
        row["after_id"] = after_id
    if content:
        row["fingerprint"] = fingerprint(content)
    if reason.strip():
        row["reason"] = reason.strip()
    if extra:
        row.update(extra)
    path = project_root / JOURNAL_RELPATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        _log.warning("journal mémoire inaccessible : %s", path, exc_info=True)
