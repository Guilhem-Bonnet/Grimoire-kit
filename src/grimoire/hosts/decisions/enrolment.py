"""Enrôler le chantier d'une session dans le Mission Ledger (issue #638, lot A).

Constat du 2026-09-27 : treize sessions ouvertes sur un même projet gouverné,
aucune dans le ledger — toutes sous la tâche de repli ``bootstrap``, que le
gate ``Stop`` acceptait de clore. Ce module tient les trois règles qui
changent cela, partagées par ``SessionStart``, ``UserPromptSubmit`` et
``Stop`` pour qu'elles ne divergent jamais :

- :func:`no_task_context` — le texte, avec son remède copiable, injecté quand
  :func:`grimoire.core.standard_state.resolve_active_task` retombe sur
  ``bootstrap`` ;
- :func:`link_session` — le claim actif apprend la session qui le porte, et
  le journal de session apprend sa tâche ; un événement de plus au ledger,
  jamais une ligne réécrite ;
- :data:`BLOCKING_PROFILES` — les profils où travailler hors tâche est refusé
  au ``Stop``, pas seulement signalé.

Tout est best-effort à la frontière du hook : un ledger illisible ou un disque
en lecture seule dégradent en « rien rattaché », jamais en session cassée.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from grimoire.core.standard_state import ActiveTask
from grimoire.hosts.decisions._shared import HookInput

#: Profiles whose ``Stop`` hook refuses a closure — red gates, unevaluable
#: gates and work done outside any task alike. One place, three rules.
BLOCKING_PROFILES = frozenset({"governed", "production"})

#: Marqueur porté par ``Decision.detail["blocked_on"]`` quand le motif est
#: l'absence de tâche, pour qu'un test ou un journal le distingue d'un gate
#: de preuve rouge.
NO_ACTIVE_TASK = "no_active_task"

_REMEDY_LINES = (
    '  grimoire task add "<titre>" -a "<critère d\'acceptation>" --owner <nom> --ready',
    "  grimoire task context <id>   # le context bundle que le gate ready_to_in_progress exige",
    "  grimoire task claim <id> --actor <nom>",
    "ou, par MCP : task_add (ready=true) → task_context → task_claim ; "
    "ou GRIMOIRE_TASK_ID=<id> dans l'environnement de la session.",
)


def remedy_text() -> str:
    """Le remède copiable, identique dans chaque contexte qui le cite."""
    return "\n".join(_REMEDY_LINES)


def no_task_context(profile: str) -> str:
    """Ce qu'une session sans tâche lit au ``SessionStart`` et au ``UserPromptSubmit``."""
    head = (
        f"[Grimoire] Aucune tâche du Mission Ledger n'est en cours (repli `bootstrap`, profil {profile}). "
        "Ouvre-en une avant d'écrire :\n"
    )
    tail = (
        "\nEn profil governed, une clôture après une écriture hors tâche est refusée au Stop."
        if profile in BLOCKING_PROFILES
        else f"\nProfil {profile} : une écriture hors tâche est signalée au Stop, pas refusée."
    )
    return head + remedy_text() + tail


def no_task_stop_reason(profile: str, mutations: int) -> str:
    """Le motif que ``Stop`` rend quand une session a écrit sans tâche."""
    return (
        f"[Grimoire] Clôture hors tâche : {mutations} action(s) d'écriture observée(s) dans cette session "
        f"et aucune tâche du Mission Ledger n'est en cours (repli `bootstrap`, profil {profile}).\n"
        "Enrôle le chantier avant de conclure :\n"
        f"{remedy_text()}\n"
        "Si le travail doit rester hors ledger, dis-le explicitement à l'utilisateur au lieu de conclure."
    )


def link_session(hook: HookInput, active: ActiveTask) -> dict[str, Any]:
    """Rattache la session courante à la tâche active — claim et journal.

    Renvoie ``{"session_attached": bool, "journal_task_written": bool}`` :
    ``session_attached`` n'est vrai que si *cette* invocation a ajouté
    l'événement ``task.session_attached`` au ledger (un claim déjà rattaché,
    à cette session ou à une autre, ne l'est pas — une session ne vole
    jamais le claim d'une autre) ; ``journal_task_written`` si le journal
    ``session-<id>.json`` a reçu ``task_id`` cette fois. Sans ``session_id``
    dans le payload (hôte qui n'en envoie pas) ou sous ``bootstrap``, rien
    n'est écrit nulle part.
    """
    out: dict[str, Any] = {"session_attached": False, "journal_task_written": False}
    if not hook.session_id or active.task_id == "bootstrap":
        return out
    now_iso = datetime.now(UTC).isoformat()
    with contextlib.suppress(Exception):
        from grimoire.policies.session_state import note_session_task

        out["journal_task_written"] = note_session_task(
            hook.project_root, hook.session_id, active.task_id, now_iso=now_iso
        )
    if active.source != "ledger_claim":
        return out
    try:
        from grimoire.missions.service import TaskService

        service = TaskService(hook.project_root)
        task = service.ledger.get_task(active.task_id)
        if task is None or task.claim is None or task.claim.session_id:
            return out
        service.attach_session(task.id, hook.session_id, session_host=hook.host, actor=task.claim.actor_id)
        out["session_attached"] = True
    except Exception:
        return out
    return out


def session_mutation_count(hook: HookInput) -> int:
    """Les écritures que ``PostToolUse`` a comptées pour cette session — ``0`` sans journal."""
    if not hook.session_id:
        return 0
    try:
        from grimoire.policies.session_state import session_mutations

        return session_mutations(hook.project_root, hook.session_id)
    except Exception:
        return 0
