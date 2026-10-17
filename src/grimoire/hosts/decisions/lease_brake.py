"""``PreToolUse`` : le frein du bail (K3 de l'épopée #705, issue #768).

Un titulaire périmé — session reprise ailleurs, tâche annulée, bail rendu ou
récolté — ne doit plus écrire. Le ledger le refuse déjà pour ses propres
événements (K2) ; ce frein le refuse pour les *outils* : un ``Edit``, un
``git push``, un appel MCP lancé par une session dont le bail n'est plus le
sien. Trois refus, dans l'ordre où on les lit :

- **annulation** : la tâche de la session a été annulée ;
- **epoch périmé** : le fichier de bail porte un autre epoch que celui que la
  session tient, ou plus de titulaire ;
- **``--no-verify``** : tant que la session tient un bail, elle ne contourne
  pas le ``pre-push`` du worktree (:mod:`grimoire.missions.prepush_fence`).

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

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from grimoire.core.standard_state import LEDGER_RELPATH
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import ToolFacts, command_surface
from grimoire.missions.lease import ORIGIN_CANCEL, LeaseFile, lease_path, read_lease
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


def _lease_holder(hook: HookInput) -> tuple[str, int]:
    """``(tâche, epoch)`` que le journal de la session dit tenir ; ``("", 0)`` sinon."""
    if not hook.session_id:
        return "", 0
    from grimoire.policies.session_state import load_session_state

    state = load_session_state(hook.project_root, hook.session_id, now_iso="")
    return (state.task_id, state.lease_epoch) if state.task_id and state.lease_epoch > 0 else ("", 0)


def _stale_reason(root: Path, task_id: str, epoch: int) -> tuple[str, bool]:
    """``(motif, annulée)`` — ``("", False)`` quand la session tient encore son bail.

    Fichier d'abord ; absent ou illisible, le ledger relu une fois (``check_holder``).
    """
    cached = read_lease(lease_path(root, task_id))
    if cached is not None:
        if cached.holder and cached.epoch == epoch:
            return "", False
        if cached.origin == ORIGIN_CANCEL:
            return f"la tâche {task_id} a été annulée (bail rendu, epoch {cached.epoch})", True
        return f"epoch {epoch} périmé : le bail de {task_id} est à l'epoch {cached.epoch}", False
    if not (root / "events.jsonl").is_file():
        return "", False
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import TaskState

    ledger = MissionLedger(root)
    check = ledger.check_holder(task_id, epoch)
    if check.ok:
        return "", False
    task = ledger.get_task(task_id)
    return check.reason, bool(task is not None and task.status is TaskState.CANCELLED)


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
        reason, cancelled = _stale_reason(root, task_id, epoch)
        if reason:
            if facts.command and _GRIMOIRE_TASK.match(facts.command):
                return None
            return _deny(hook, task_id, epoch, reason, "cancelled" if cancelled else "stale_epoch")
        bypass = _no_verify(facts)
        if bypass:
            return _deny(hook, task_id, epoch, f"{bypass} alors que la session tient le bail de {task_id}", "no_verify")
        refused = _mark_busy(root, task_id, epoch, read_lease(lease_path(root, task_id)), _declared_timeout_ms(hook, facts))
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
