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
import subprocess
from datetime import UTC, datetime
from typing import Any

from grimoire.core.standard_state import LEDGER_RELPATH, ActiveTask
from grimoire.hosts.decisions._shared import HookInput

#: Profiles whose ``Stop`` hook refuses a closure — red gates, unevaluable
#: gates and work done outside any task alike. One place, three rules.
BLOCKING_PROFILES = frozenset({"governed", "production"})

#: Marqueur porté par ``Decision.detail["blocked_on"]`` quand le motif est
#: l'absence de tâche, pour qu'un test ou un journal le distingue d'un gate
#: de preuve rouge.
NO_ACTIVE_TASK = "no_active_task"

#: Marqueur de ``Decision.detail["blocked_on"]`` quand la session tient encore une
#: tâche ``claimed``/``running`` qu'elle n'a ni fermée ni garée (K3, §5.1).
TASK_UNPARKED = "task_unparked"

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


def ambiguity_text(candidates: tuple[str, ...]) -> str:
    """Issue #680 : plusieurs claims, aucun n'est rattaché à cette session — les nommer, avec la commande."""
    names = ", ".join(candidates)
    return (
        f"Plusieurs tâches sont réclamées en même temps ({names}) et aucune n'est rattachée à cette session.\n"
        "Rattache la tienne : `grimoire task attach <id>` (la session est lue dans l'environnement ; "
        "sinon `--session-id <id>`), ou task_context/GRIMOIRE_TASK_ID. "
        "Un `grimoire task claim` lancé depuis cette session la rattache déjà."
    )


def expired_text(expired: tuple[str, ...]) -> str:
    """Issue #710 : le bail du claim est dépassé — la tâche n'est plus celle de la session, et pourquoi."""
    names = ", ".join(expired)
    return (
        f"Claim expiré sur {names} : son bail est dépassé, il ne désigne plus la tâche de cette session. "
        f"Pour continuer dessus, pose GRIMOIRE_TASK_ID={expired[0]} ; sinon réclame une autre tâche."
    )


def _with_expired(text: str, expired: tuple[str, ...]) -> str:
    return f"{text}\n{expired_text(expired)}" if expired else text


def _ambiguity_tail(profile: str) -> str:
    if profile in BLOCKING_PROFILES:
        return f"En profil {profile}, une clôture après une écriture hors tâche est refusée au Stop."
    return f"Profil {profile} : une écriture hors tâche est signalée au Stop, pas refusée."


def no_task_context(profile: str, candidates: tuple[str, ...] = (), expired: tuple[str, ...] = ()) -> str:
    """Ce qu'une session sans tâche lit au ``SessionStart`` et au ``UserPromptSubmit``."""
    return _with_expired(_no_task_context(profile, candidates), expired)


def _no_task_context(profile: str, candidates: tuple[str, ...]) -> str:
    if candidates:
        head = f"[Grimoire] Tâche de la session indéterminée (profil {profile}). {ambiguity_text(candidates)}\n"
        return head + _ambiguity_tail(profile)
    head = (
        f"[Grimoire] Aucune tâche du Mission Ledger n'est en cours (repli `bootstrap`, profil {profile}). "
        "Ouvre-en une avant d'écrire :\n"
    )
    tail = (
        f"\nEn profil {profile}, une clôture après une écriture hors tâche est refusée au Stop."
        if profile in BLOCKING_PROFILES
        else f"\nProfil {profile} : une écriture hors tâche est signalée au Stop, pas refusée."
    )
    return head + remedy_text() + tail


def no_task_stop_reason(
    profile: str, mutations: int, candidates: tuple[str, ...] = (), expired: tuple[str, ...] = ()
) -> str:
    """Le motif que ``Stop`` rend quand une session a écrit sans tâche."""
    return _with_expired(_no_task_stop_reason(profile, mutations, candidates), expired)


def _no_task_stop_reason(profile: str, mutations: int, candidates: tuple[str, ...]) -> str:
    if candidates:
        return (
            f"[Grimoire] Clôture hors tâche : {mutations} action(s) d'écriture observée(s) dans cette session "
            f"et aucune tâche n'est rattachée à elle (profil {profile}).\n"
            f"{ambiguity_text(candidates)}\n"
            "Si le travail doit rester hors ledger, dis-le explicitement à l'utilisateur au lieu de conclure."
        )
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

    K3 (issue #768) : le journal reçoit aussi l'epoch du bail que la session
    tient (``lease_epoch``), et le ledger la version de l'hôte — c'est ce que le
    frein ``PreToolUse`` et la récolte comparent. Une session dont le journal
    porte déjà un autre epoch pour cette tâche a été reprise : elle ne se
    rattache pas au claim du nouveau titulaire.
    """
    out: dict[str, Any] = {"session_attached": False, "journal_task_written": False}
    if not hook.session_id or active.task_id == "bootstrap":
        return out
    now_iso = datetime.now(UTC).isoformat()
    known_epoch = _journal_epoch(hook, active.task_id)
    with contextlib.suppress(Exception):
        from grimoire.policies.session_state import note_session_task

        out["journal_task_written"] = note_session_task(
            hook.project_root, hook.session_id, active.task_id, now_iso=now_iso
        )
    if active.source not in ("ledger_claim", "session_claim"):
        return out
    if active.source == "session_claim" and known_epoch:
        return out  # déjà rattachée, epoch et version déjà notés : une lecture du journal, pas du ledger
    try:
        from grimoire.hosts.capabilities import resolve_host
        from grimoire.hosts.host_version import detect_host_version
        from grimoire.missions.service import TaskService

        service = TaskService(hook.project_root)
        task = service.ledger.get_task(active.task_id)
        if task is None or task.claim is None:
            return out
        claim = task.claim
        if claim.session_id and claim.session_id != hook.session_id:
            return out
        if known_epoch and claim.lease_epoch != known_epoch:
            return out  # reprise ailleurs : le claim d'aujourd'hui n'est pas celui que cette session tenait
        version = detect_host_version(resolve_host(hook.host) if hook.host else None, hook.raw)
        if not claim.session_id or (version and version != claim.session_host_version):
            service.attach_session(
                task.id, hook.session_id, session_host=hook.host, actor=claim.actor_id, host_version=version
            )
            out["session_attached"] = not claim.session_id
        with contextlib.suppress(Exception):
            from grimoire.policies.session_state import note_session_task

            note_session_task(
                hook.project_root, hook.session_id, task.id, now_iso=now_iso, lease_epoch=claim.lease_epoch
            )
    except Exception:
        return out
    return out


def _journal_epoch(hook: HookInput, task_id: str) -> int:
    """L'epoch que le journal de la session note pour *task_id* — ``0`` s'il n'en note aucun."""
    try:
        from grimoire.policies.session_state import load_session_state

        state = load_session_state(hook.project_root, hook.session_id, now_iso="")
    except Exception:
        return 0
    return state.lease_epoch if state.task_id == task_id else 0


#: États où la session a lâché sa tâche avec une raison : attente (K6), rendue pour vérification,
#: bloquée, échouée, annulée, fermée (D-12). Seuls ``claimed``/``running`` la tiennent encore.
PARKED_STATES = frozenset({"waiting", "needs_verification", "blocked", "failed", "cancelled", "closed"})


def parked_task_of_session(hook: HookInput) -> Any:
    """La tâche que cette session tenait et a garée ou rendue (:data:`PARKED_STATES`), ou ``None``.

    Une tâche qui n'est plus ``claimed``/``running`` n'est plus désignée par la résolution de tâche
    active, et le ``Stop`` prendrait le travail de la session pour une écriture hors tâche. Le journal
    de la session dit quelle tâche elle a tenue ; le ledger, que ce claim est le sien et ce qu'il est
    devenu. Une autre session n'est jamais couverte par elle.
    """
    if not hook.session_id:
        return None
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.policies.session_state import load_session_state

        task_id = load_session_state(hook.project_root, hook.session_id, now_iso="").task_id
        events = hook.project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
        if not task_id or not events.is_file():
            return None
        task = MissionLedger(events.parent).get_task(task_id)
    except Exception:
        return None
    if task is None or task.status.value not in PARKED_STATES or task.claim is None:
        return None
    return task if task.claim.session_id == hook.session_id else None


def session_mutation_count(hook: HookInput) -> int:
    """Les écritures que ``PostToolUse`` a comptées pour cette session — ``0`` sans journal."""
    if not hook.session_id:
        return 0
    try:
        from grimoire.policies.session_state import session_mutations

        return session_mutations(hook.project_root, hook.session_id)
    except Exception:
        return 0


#: Au plus ce nombre de chemins dans ``session.unattached_work`` : un signal, pas un inventaire.
_MAX_UNATTACHED_FILES = 50


def stop_scope(hook: HookInput, active: ActiveTask) -> ActiveTask:
    """La tâche que ``Stop`` juge : celle rattachée à *cette* session, jamais une autre (K3, issue #768).

    ``resolve_active_task`` hérite, pour un hook qui doit désigner une tâche au
    prompt, du claim sans session (``ledger_claim``) et de l'unique carte
    ``in_progress`` du board (``board``) — des règles faites pour que le premier
    prompt rattache la session, pas pour décider qu'une clôture est refusée. Au
    ``Stop`` elles faisaient bloquer une session sans tâche sur les gates rouges
    d'une tâche qui n'était pas la sienne. Ici, une tâche du ledger dont le claim
    n'est pas rattaché à cette session retombe sur ``bootstrap`` ; une carte du
    board que le ledger ne connaît pas (board écrit à la main) reste jugée.
    Sans ``session_id`` (hôte qui n'en envoie pas), la résolution est inchangée.
    """
    if not hook.session_id or active.source not in ("ledger_claim", "board"):
        return active
    try:
        from grimoire.missions.ledger import MissionLedger

        events = hook.project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
        if not events.is_file():
            return active
        task = MissionLedger(events.parent).get_task(active.task_id)
    except Exception:
        return active
    if task is None:
        return active
    if task.claim is not None and task.claim.session_id == hook.session_id:
        return active
    return ActiveTask("bootstrap", "bootstrap", expired=active.expired)


def dirty_files(hook: HookInput) -> tuple[str, ...]:
    """Les chemins que ``git status`` dit modifiés ou non suivis, au plus :data:`_MAX_UNATTACHED_FILES`.

    Information, jamais décision : un arbre sale n'est pas le travail de cette session
    (c'est le compteur d'écritures du journal qui décide). Hors dépôt git, git absent ou
    trop lent : ``()``.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(hook.project_root), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if done.returncode != 0:
        return ()
    lines = [line[3:].strip() for line in done.stdout.splitlines() if len(line) > 3]
    return tuple(lines[:_MAX_UNATTACHED_FILES])


def record_unattached_work(hook: HookInput, mutations: int) -> dict[str, Any]:
    """Journalise ``session.unattached_work{files, mutations}`` (§5.1) et le rend pour le détail de la décision.

    Best-effort : un journal inscriptible ou non, la décision de ``Stop`` ne change pas.
    """
    files = dirty_files(hook)
    with contextlib.suppress(Exception):
        from grimoire.policies.session_state import note_unattached_work

        note_unattached_work(
            hook.project_root, hook.session_id, mutations=mutations, files=files, now_iso=datetime.now(UTC).isoformat()
        )
    return {"files": list(files), "mutations": mutations}
