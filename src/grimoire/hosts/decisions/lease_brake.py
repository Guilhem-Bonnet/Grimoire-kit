"""``PreToolUse`` : le frein du bail (K3 de l'épopée #705, issue #768).

Un titulaire périmé — session reprise ailleurs, tâche annulée, bail rendu ou
récolté — ne doit plus écrire. Le ledger le refuse déjà pour ses propres
événements (K2) ; ce frein le refuse pour les *outils* : un ``Edit``, un
``git push``, un appel MCP lancé par une session dont le bail n'est plus le
sien. Les refus, dans l'ordre où on les lit :

- **annulation** : la tâche de la session a été annulée ;
- **epoch périmé** : le fichier de bail porte un autre epoch que celui que la
  session tient, ou plus de titulaire ;
- **``--no-verify``** : tant que la session tient un bail, elle ne contourne
  pas le ``pre-push`` du worktree (:mod:`grimoire.missions.prepush_fence`) ;
- **tâche garée** (``waiting``, WAIT-SCOPE) : *pas* un refus. Le bail est gardé pour la reprise mais ne
  se renouvelle pas ; la session est jugée comme une session sans tâche (voir ``lease_brake``), pour
  qu'une attente ne gèle ni la session ni ses sous-agents.

Le contrôle d'epoch lit le fichier de bail, jamais le journal : un ``stat`` et
une lecture, et aucune dépendance à l'horloge. Le fichier absent renvoie au
ledger, relu une fois. Le journal de la session (``session-<id>.json``) dit
quelle tâche et quel epoch la session tient : sans lui, la session ne tient
aucun bail et le frein se tait.

Le frein pose aussi ``busy_until`` : un outil long déclaré (``timeout`` d'un
``Bash``) repousse l'instant où le bail est jugé perdu, pour que la récolte ne
relance pas une tâche dont l'outil tourne encore.

Limites écrites : un hôte dont le refus n'est pas vérifié (K0) ne tient pas
ce frein, il est conseillé ; un effet distant déjà parti (``gh``) n'est pas
repris ; une session qui lance du code arbitraire contourne n'importe quelle
garde de chemin.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from grimoire.core.standard_generation import normalize_task_id
from grimoire.core.standard_state import LEDGER_RELPATH, TASK_ENV
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import ToolFacts, command_surface
from grimoire.missions.lease import ORIGIN_CANCEL, ORIGIN_HAND_BACK, LeaseFile, lease_path, read_lease
from grimoire.policies.schemas import MutationClass

if TYPE_CHECKING:
    from pathlib import Path

#: Timeout d'un ``Bash`` quand l'appel n'en déclare pas (Claude Code : 2 min), en millisecondes.
DEFAULT_TOOL_TIMEOUT_MS = 120_000
#: Plafond que l'hôte accepte pour un ``Bash`` (10 min) : une valeur plus grande n'est pas crue.
MAX_TOOL_TIMEOUT_MS = 600_000

_NO_VERIFY = re.compile(
    r"\bgit\b[^;&|\n]*?\b(?:commit|push|merge|rebase|cherry-pick|am)\b[^;&|\n]*?(?:^|\s)--no-verify\b"
)
_COMMIT_SHORT_N = re.compile(r"\bgit\b[^;&|\n]*?\bcommit\b[^;&|\n]*?\s-[A-Za-z]*n[A-Za-z]*(?=\s|$)")
_HOOKS_PATH_OVERRIDE = re.compile(r"\bgit\b[^;&|\n]*?\s-c\s*core\.hooksPath\b")
#: Les commandes qui passent par le ledger lui-même (claim, attach, renew…) : le ledger
#: refuse déjà un effet d'epoch périmé, et c'est par elles qu'une session reprend un bail.
_GRIMOIRE_TASK = re.compile(r"^\s*(?:\S*/)?grimoire\s+task\b")
#: Ce qui enchaîne, redirige ou substitue : une commande qui en porte n'est pas « un `grimoire task` ».
_SHELL_COMPOSITION = re.compile(r"[;&|<>`\r\n]|\$\(")
#: ``2>&1`` : une redirection de descripteur, pas une écriture de fichier.
_FD_DUP = re.compile(r"\d*>&\d")


def _simple_grimoire_task(command: str) -> bool:
    """``True`` pour une commande ``grimoire task …`` seule, sans rien enchaîner derrière (K3-S1-04).

    L'exemption existe pour que la session périmée puisse reprendre son bail ; elle ne
    vaut que pour cette commande. ``grimoire task show x && touch f`` exécuterait ``touch``
    sous couvert de l'exemption. Le texte entre guillemets n'est pas du shell
    (:func:`command_surface`) : ``grimoire task add 'a; b'`` reste simple. Fail-closed :
    la moindre composition refuse l'exemption, jamais le contraire.
    """
    if "$(" in command or "`" in command:  # entre guillemets doubles, le shell substitue quand même
        return False
    surface = _FD_DUP.sub("", command_surface(command)).strip()
    return bool(_GRIMOIRE_TASK.match(surface)) and not _SHELL_COMPOSITION.search(surface)


def _lease_holder(hook: HookInput) -> tuple[str, int]:
    """``(tâche, epoch)`` que le journal de la session dit tenir ; ``("", 0)`` sinon."""
    if not hook.session_id:
        return "", 0
    from grimoire.policies.session_state import load_session_state

    state = load_session_state(hook.project_root, hook.session_id, now_iso="")
    return (state.task_id, state.lease_epoch) if state.task_id and state.lease_epoch > 0 else ("", 0)


def held_task_id(hook: HookInput) -> str:
    """La tâche que cette session tient d'après son journal et le fichier de bail, ``""`` sinon (K13).

    La même lecture que le frein — un ``stat`` et un fichier, jamais le journal du ledger — pour
    que le reste du PreToolUse sache *quelle* tâche sans relancer ``resolve_active_task``, qui
    rejoue tout le ledger (≈ 0,4 s à 10 000 événements, contre 50 ms de budget). Vide, la
    résolution classique reste la règle : ``GRIMOIRE_TASK_ID`` d'abord, un fichier absent, une
    barrière, un autre epoch, une session sans bail noté, une tâche garée (``waiting``, marque
    du fichier de bail) ou un bail perdu (``renewed_at`` + TTL et ``busy_until`` dépassés, comme
    ``LeaseFile.is_lost``) renvoient au ledger : ``resolve_active_task`` ne nomme ni une tâche
    garée ni un claim expiré, et le chemin rapide ne doit pas dire autre chose que lui (K13-S1-01).
    """
    if os.environ.get(TASK_ENV, "").strip():
        return ""
    try:
        task_id, epoch = _lease_holder(hook)
        if not task_id:
            return ""
        cached = read_lease(lease_path(hook.project_root.resolve() / LEDGER_RELPATH, task_id))
        if cached is None or not cached.holder or cached.epoch != epoch or cached.waiting:
            return ""
        if cached.is_lost(datetime.now(UTC)):
            return ""  # bail perdu (TTL, ``busy_until`` dépassés) : la résolution écarte aussi un claim expiré
        return normalize_task_id(task_id)
    except Exception:
        return ""


_HELD = "held"
_RELEASED = "released"
_STALE = "stale_epoch"
_CANCELLED = "cancelled"


def _lease_state(root: Path, task_id: str, epoch: int) -> tuple[str, str]:
    """``(état, motif)`` du bail que la session croit tenir : ``held``, ``released``, ``stale_epoch`` ou ``cancelled``.

    ``released`` : la session a quitté son bail par la porte — release, blocage, échec ou
    clôture (barrière ``hand_back`` à l'epoch suivant le sien). Elle ne tient plus rien,
    mais personne ne lui a pris : le frein se tait, comme pour une session sans bail
    (K3-S1-01). Une barrière posée par la récolte, un autre titulaire, un autre epoch ou
    une annulation restent des refus.

    Fichier d'abord ; absent ou illisible, le ledger relu une fois (``check_holder``).
    """
    cached = read_lease(lease_path(root, task_id))
    if cached is not None:
        if cached.holder and cached.epoch == epoch:
            return _HELD, ""
        if cached.origin == ORIGIN_CANCEL:
            return _CANCELLED, f"la tâche {task_id} a été annulée (bail rendu, epoch {cached.epoch})"
        if not cached.holder and cached.origin == ORIGIN_HAND_BACK and cached.epoch == epoch + 1:
            return _RELEASED, ""
        return _STALE, f"epoch {epoch} périmé : le bail de {task_id} est à l'epoch {cached.epoch}"
    if not (root / "events.jsonl").is_file():
        return _HELD, ""
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import TaskState

    ledger = MissionLedger(root)
    check = ledger.check_holder(task_id, epoch)
    if check.ok:
        return _HELD, ""
    task = ledger.get_task(task_id)
    if task is not None and task.status is TaskState.CANCELLED:
        return _CANCELLED, check.reason
    if (
        task is not None
        and task.claim is not None
        and task.claim.lease_epoch == epoch
        and task.status in (TaskState.CLOSED, TaskState.BLOCKED, TaskState.FAILED)
    ):
        return _RELEASED, ""  # le claim de la session, rendu par une sortie légitime
    return _STALE, check.reason


def _no_verify(facts: ToolFacts) -> str:
    if not facts.command:
        return ""
    surface = command_surface(facts.command)
    if _NO_VERIFY.search(surface) or _COMMIT_SHORT_N.search(surface):
        return "contourne les hooks git (--no-verify)"
    if _HOOKS_PATH_OVERRIDE.search(surface):
        return "remplace core.hooksPath pour une commande git"
    return ""


def _declared_timeout_ms(hook: HookInput, facts: ToolFacts) -> int:
    """Le temps que l'outil peut tourner, d'après ce qu'il déclare ; ``0`` s'il n'attend pas."""
    if not facts.command or hook.tool_input.get("run_in_background") is True:
        return 0
    declared = hook.tool_input.get("timeout")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
        return min(declared, MAX_TOOL_TIMEOUT_MS)
    return DEFAULT_TOOL_TIMEOUT_MS


def _mark_busy(root: Path, task_id: str, epoch: int, cached: LeaseFile | None, timeout_ms: int) -> str:
    """Pose ``busy_until`` si l'outil dépasse le bail ; renvoie le motif de refus si le ledger le refuse.

    Le plus souvent rien n'est écrit : un appel court tient dans le TTL restant. L'écriture
    passe par ``renew_lease`` (verrou du journal, epoch vérifié) et jamais par une écriture
    directe du fichier, qui pourrait ressusciter un bail que la récolte vient de retirer.
    """
    if timeout_ms <= 0 or cached is None:
        return ""
    until = datetime.now(UTC) + timedelta(milliseconds=timeout_ms)
    lost_at = cached.lost_at()
    if lost_at is not None and until <= lost_at:
        return ""
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.ledger import MissionLedger

    try:
        MissionLedger(root).renew_lease(task_id, epoch, busy_until=until, ttl_seconds=cached.ttl_seconds or 1200)
    except GrimoireMissionError as exc:
        return str(exc)
    except Exception:
        return ""
    return ""


def _deny(hook: HookInput, task_id: str, epoch: int, what: str, detail_kind: str) -> Decision:
    return Decision(
        outcome=Outcome.DENY,
        reason=(
            f"[Grimoire lease] {hook.tool_name or 'action'} refusé — {what}. "
            f"Cette session ne tient plus la tâche {task_id} : elle n'écrit plus pour elle. "
            "Arrête, dis-le à l'utilisateur, et ne relance pas l'action par un autre chemin "
            "(`grimoire task show` dit où en est la tâche). "
            "Limite : un effet distant déjà parti (gh, push) n'est pas repris par ce frein."
        ),
        detail={"tool": hook.tool_name, "lease_brake": detail_kind, "task_id": task_id, "lease_epoch": epoch},
    )


def lease_brake(hook: HookInput, facts: ToolFacts) -> Decision | None:
    """Le refus du bail pour cet appel d'outil, ou ``None`` : la session tient son bail (ou n'en tient aucun).

    Les appels en lecture seule passent (ils ne produisent aucun effet) : ils ne coûtent
    pas même la lecture du journal de session. Une exception ne devient jamais un ``allow``
    silencieux : l'appel est rendu à l'utilisateur (``ask``) avec la cause.
    """
    if facts.mutation is MutationClass.READ_ONLY:
        return None
    try:
        task_id, epoch = _lease_holder(hook)
        if not task_id:
            return None
        root = hook.project_root.resolve() / LEDGER_RELPATH
        state, reason = _lease_state(root, task_id, epoch)
        if state == _RELEASED:
            return None
        if state != _HELD:
            if facts.command and _simple_grimoire_task(facts.command):
                return None
            return _deny(hook, task_id, epoch, reason, state)
        bypass = _no_verify(facts)
        if bypass:
            return _deny(hook, task_id, epoch, f"{bypass} alors que la session tient le bail de {task_id}", "no_verify")
        cached = read_lease(lease_path(root, task_id))
        if cached is not None and cached.waiting:
            # WAIT-SCOPE : tâche garée (ci, quota, paused, question humaine) — le frein se tait. Une
            # attente porte sur *sa* tâche, jamais sur la session. Avant, passé le TTL du bail (qui ne se
            # renouvelle pas en ``waiting``), toute commande Bash était refusée sauf la reprise de cette
            # tâche ; or une question humaine ne se lève que par l'humain, la reprise est refusée à juste
            # titre, et la session ne pouvait plus rattacher une autre tâche, lancer le cockpit pour que
            # l'humain réponde, ni laisser travailler ses sous-agents (même journal de session).
            # Le bail gardé n'ouvre plus aucun droit d'écriture : ``held_task_id`` et ``resolve_active_task``
            # ne nomment pas une tâche garée, le moteur de règles juge la session comme une session
            # *sans tâche* (consignes du repli normal : prendre ou rattacher une tâche). Reste protégé :
            # aucune écriture au nom de la tâche garée, et la question humaine n'est levée que par
            # l'humain (``ledger_rules``, inchangé).
            return None
        refused = _mark_busy(root, task_id, epoch, cached, _declared_timeout_ms(hook, facts))
        if refused:
            return _deny(hook, task_id, epoch, refused, "stale_epoch")
    except Exception as exc:
        return Decision(
            outcome=Outcome.ASK,
            reason=(
                f"[Grimoire lease] frein du bail en erreur — {type(exc).__name__}: {exc}. "
                f"Le bail n'a pas pu être vérifié pour {hook.tool_name or 'cet appel'} : à toi de décider."
            ),
            detail={"tool": hook.tool_name, "lease_brake": "error", "error": f"{type(exc).__name__}: {exc}"},
        )
    return None
