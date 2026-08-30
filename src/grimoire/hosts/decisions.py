"""Host-neutral hook decisions: the rules, decided once.

A hook is two things glued together: a *decision* ("may this command run?",
"is this task finished?") and a *wire format* (the JSON a given host reads and
writes). Only the second is host-specific. Keeping the first here means the
governed standard behaves identically whether it runs under Claude Code,
Copilot, or a host added next year — and it means the rules are testable
without simulating any host at all.

Every decision is defensive by construction:

- it never raises into the host — a crashed hook must not brick a session, so
  failures degrade to :data:`Outcome.ALLOW` with the error carried as context;
- it fails **open** when the project is not enrolled in the standard: gates
  that do not exist cannot be red, and blocking on their absence would make
  the hook a trap rather than a guardrail;
- it fails **closed** only where the standard says so: a governed task whose
  evidence gates are red is not a finished task.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.core.claude_activation import activation_context_text
from grimoire.core.standard_state import active_profile_id, active_task_id, is_standard_enrolled
from grimoire.hosts.secrets import secret_patterns
from grimoire.hosts.surface import HookEvent
from grimoire.policies.engine import PolicyEngine
from grimoire.policies.schemas import (
    ActionKind,
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyRequest,
    PolicyRule,
    VerdictKind,
)


class Outcome(StrEnum):
    """What the host should do with the pending action or turn."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    BLOCK = "block"
    """Refuse to end the turn: the agent must keep working."""


@dataclass(frozen=True, slots=True)
class HookInput:
    """A host hook payload, normalised.

    Hosts disagree on field names (``tool_name`` vs ``toolName``) and on tool
    vocabulary (``Bash`` vs ``run_in_terminal``); :mod:`grimoire.hosts.runtime`
    flattens both before a decision ever sees them.
    """

    event: HookEvent
    project_root: Path
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_response: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    agent_name: str = ""
    session_id: str = ""
    stop_active: bool = False
    """True when the host is already re-running the agent because of a previous
    block. Blocking again here is how a session gets stuck in a loop."""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """A host-neutral verdict plus whatever context the agent should receive."""

    outcome: Outcome = Outcome.ALLOW
    reason: str = ""
    context: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_refusal(self) -> bool:
        return self.outcome in {Outcome.DENY, Outcome.BLOCK}

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "context": self.context,
            "detail": dict(self.detail),
        }


# ── Tool classification ──────────────────────────────────────────────────────
#
# Tool names are host vocabulary: Claude Code says ``Bash``/``Edit``/``Write``,
# VS Code says ``run_in_terminal``/``create_file``/``replace_string_in_file``,
# an MCP tool says whatever its server called it. Matching on substrings of the
# lowercased name covers all three without a per-host table to keep in sync.

_EXECUTE_MARKERS = ("bash", "shell", "terminal", "execute", "run_command", "runcommand", "process")
_WRITE_MARKERS = (
    "write",
    "edit",
    "create_file",
    "createfile",
    "replace_string",
    "apply_patch",
    "applypatch",
    "notebook",
)
_READ_MARKERS = ("read", "cat_file", "view", "open_file", "openfile")
_WEB_MARKERS = ("fetch", "websearch", "web_search", "browser", "navigate", "http")

#: Commands whose blast radius survives the session. Matched case-insensitively
#: on the command string; each is a shape that destroys work rather than a
#: specific tool.
_DESTRUCTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "recursive/forced delete"),
    (r"\bgit\s+push\b.*(--force|-f)\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset"),
    (r"\bgit\s+clean\s+-[a-z]*f", "forced clean"),
    (r"\bgit\s+checkout\s+--\s+\.", "discard all working-tree changes"),
    (r"\bdd\s+if=", "raw device write"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bchmod\s+-R\s+777\b", "world-writable recursive chmod"),
    (r"\bdrop\s+(table|database)\b", "schema drop"),
    (r"\btruncate\s+table\b", "table truncation"),
    (r"\bterraform\s+destroy\b", "infrastructure destruction"),
    (r"\bkubectl\s+delete\b", "cluster resource deletion"),
    (r"\bdocker\s+system\s+prune\b.*-a", "full docker prune"),
)

#: Where a quoted string stops being data and becomes a command again: whatever
#: is handed to these is executed, so it stays under inspection.
_EVAL_INTRODUCER = re.compile(
    r"(?:\b(?:bash|sh|zsh|dash|ksh|ash)\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*c"
    r"|\beval|\bxargs(?:\s+-\S+)*|\bsu\s+-c|\bssh\s+\S+|\btimeout\s+\S+)\s*$"
)

#: Single- or double-quoted runs, the shape shell arguments take when they carry
#: prose: a commit message, a ``--description``, a log line.
_QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\"")

#: ``<<TAG`` / ``<<'TAG'`` / ``<<-TAG``, opening a body the command *writes*.
_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredoc_bodies(command: str) -> str:
    """Drop every heredoc body, keeping the redirection that opened it.

    A heredoc body is data the command writes to a file. The shell never runs
    it, so nothing inside it can be a destructive action.
    """
    out = command
    for match in _HEREDOC_OPEN_RE.finditer(command):
        tag = re.escape(match.group(2))
        body = re.compile(
            rf"({re.escape(match.group(0))}).*?^[ \t]*{tag}[ \t]*$",
            re.DOTALL | re.MULTILINE,
        )
        out = body.sub(r"\1", out, count=1)
    return out


def command_surface(command: str) -> str:
    """The part of *command* the shell will execute, with carried data removed.

    Matching the destructive patterns against the whole command line meant the
    policy refused a heredoc that *documented* a dangerous command, and a commit
    message that merely named one — while the same words written through an
    editing tool passed, because those carry no command string at all. Reading
    data as if it were an action was the defect; the asymmetry was the symptom.

    Quoted text is dropped, *except* where a shell is about to run it: what
    follows ``bash -c``, ``eval`` or ``xargs`` is executed and stays inspected.
    """
    surface = _strip_heredoc_bodies(command)
    out: list[str] = []
    cursor = 0
    for quoted in _QUOTED_RE.finditer(surface):
        preceding = surface[cursor:quoted.start()]
        out.append(preceding)
        # Keep the quotes as a word boundary so ``rm -rf`` cannot be spliced
        # together out of two neighbouring fragments.
        out.append(quoted.group(0) if _EVAL_INTRODUCER.search(preceding.rstrip()) else " ")
        cursor = quoted.end()
    out.append(surface[cursor:])
    return "".join(out)


@dataclass(frozen=True, slots=True)
class ToolFacts:
    """What a decision needs to know about a pending tool call."""

    kind: ActionKind
    mutation: MutationClass
    command: str = ""
    targets: tuple[str, ...] = ()
    destructive_reason: str = ""
    secret_target: str = ""

    @property
    def family(self) -> str:
        """Neutral tool family, matching :attr:`HookSpec.matcher` values."""
        if self.kind is ActionKind.FILE_WRITE:
            return "write"
        if self.kind is ActionKind.NETWORK:
            return "network"
        if self.kind is ActionKind.SECRET_ACCESS:
            return "secret"
        if self.command:
            return "execute"
        return "read"


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _target_paths(tool_input: dict[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for key in ("file_path", "filePath", "path", "notebook_path", "target_file", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                value = edit.get("file_path") or edit.get("filePath")
                if isinstance(value, str):
                    paths.append(value)
    return tuple(dict.fromkeys(paths))


def classify_tool(tool_name: str, tool_input: dict[str, Any] | None = None) -> ToolFacts:
    """Describe a pending tool call in host-neutral policy terms."""
    payload = tool_input or {}
    name = tool_name.lower()
    command = _first_str(payload, "command", "cmd", "commandLine", "script")
    targets = _target_paths(payload)
    # Each candidate is matched on its own: joining them first turns the start
    # of a path into the middle of a blob, and ``.env`` stops looking like a
    # path anchor.
    candidates = [c.lower() for c in (command, *targets) if c]

    secret_target = ""
    for candidate in candidates:
        for pattern in secret_patterns():
            match = re.search(pattern, candidate)
            if match:
                # Report the fragment that matched, not the whole command line:
                # the refusal must name the credential, not echo the shell.
                secret_target = match.group(0).strip().strip("'\"=")
                break
        if secret_target:
            break

    destructive_reason = ""
    if command:
        surface = command_surface(command)
        for pattern, label in _DESTRUCTIVE_PATTERNS:
            if re.search(pattern, surface, flags=re.IGNORECASE):
                destructive_reason = label
                break

    is_execute = bool(command) or any(marker in name for marker in _EXECUTE_MARKERS)
    is_write = any(marker in name for marker in _WRITE_MARKERS)
    is_web = any(marker in name for marker in _WEB_MARKERS)
    is_read = any(marker in name for marker in _READ_MARKERS)

    if secret_target and (is_read or is_execute or not name):
        kind = ActionKind.SECRET_ACCESS
    elif is_write:
        kind = ActionKind.FILE_WRITE
    elif is_web:
        kind = ActionKind.NETWORK
    elif is_execute:
        kind = ActionKind.TOOL_USE
    else:
        kind = ActionKind.TOOL_USE

    if destructive_reason:
        mutation = MutationClass.DESTRUCTIVE
    elif is_write or (is_execute and command):
        mutation = MutationClass.MUTATION_CONTROLLED
    else:
        mutation = MutationClass.READ_ONLY
    if kind is ActionKind.TOOL_USE and not is_execute and not is_write:
        mutation = MutationClass.READ_ONLY

    return ToolFacts(
        kind=kind,
        mutation=mutation,
        command=command,
        targets=targets,
        destructive_reason=destructive_reason,
        secret_target=secret_target,
    )


#: Standard profile -> policy risk profile. The standard grades *how much
#: evidence* a project owes; the policy engine grades *how much freedom* it
#: gets. A production project owes the most and gets the least.
_RISK_BY_PROFILE = {"starter": "light", "governed": "standard", "production": "strict"}


def _risk_profile(project_root: Path) -> str:
    return _RISK_BY_PROFILE.get(active_profile_id(project_root), "light")


#: The engine's built-in ``no-destructive-without-strict`` rule blocks
#: destructive mutations below the strict profile — and therefore *allows* them
#: silently at strict, which is where the most damage is possible. A project
#: that owes the most evidence should not be the one where ``rm -rf`` passes
#: unremarked, so strict escalates to a confirmation instead of a green light.
_DESTRUCTIVE_AT_STRICT = PolicyRule(
    id="destructive-requires-confirmation",
    description="Destructive mutations always require explicit confirmation, strict profile included",
    action_kinds=(),
    mutation_classes=(MutationClass.DESTRUCTIVE,),
    risk_profiles=("strict",),
    verdict_on_match=VerdictKind.WARN,
    reason_template="Destructive mutation requires explicit confirmation",
)


def _engine() -> PolicyEngine:
    engine = PolicyEngine()
    engine.register_rule(_DESTRUCTIVE_AT_STRICT)
    return engine


def _policy_request(hook: HookInput, facts: ToolFacts, task_id: str, risk: str) -> PolicyRequest:
    return PolicyRequest(
        id=f"req-{uuid.uuid4().hex[:12]}",
        run_id=hook.session_id or "session-unknown",
        task_id=task_id,
        actor=PolicyActor(
            actor_id=hook.agent_name or "agent", host_id=os.environ.get("GRIMOIRE_HOST_ID", "host-unknown")
        ),
        action=PolicyAction(
            kind=facts.kind,
            tool=hook.tool_name or "unknown",
            mutation_class=facts.mutation,
            command=facts.command,
            target_files=facts.targets,
        ),
        risk_profile=risk,
        created_at=datetime.now(UTC).isoformat(),
        context={"event": hook.event.value},
    )


# ── Decisions ────────────────────────────────────────────────────────────────


def decide_activation(hook: HookInput) -> Decision:
    """Session start: hand the agent the project's standing directive.

    Validated 40/40 by the 2026-07-09 campaign against 0/40 without it — an
    unread standard is an inert standard.
    """
    task_id = active_task_id(hook.project_root)
    context = activation_context_text(hook.project_root, task_id=task_id)
    return Decision(outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id})


def decide_task_context(hook: HookInput) -> Decision:
    """Prompt submit: state which task the work will be charged to.

    Cheap, non-blocking, and it removes the single most common failure of the
    evidence protocol — writing proof under the wrong task id.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root)
    profile = active_profile_id(hook.project_root)
    context = (
        f"[Grimoire] Tâche courante : {task_id} (profil {profile}). "
        f"Toute preuve va dans _grimoire-output/evidence/{task_id}/."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id, "profile": profile})


def decide_tool_policy(hook: HookInput) -> Decision:
    """Pre tool use: run the pending call through the policy engine.

    This is the call site the engine never had: before it, ``PolicyEngine``
    was a library that only its own tests invoked.
    """
    facts = classify_tool(hook.tool_name, hook.tool_input)
    if facts.mutation is MutationClass.READ_ONLY and not facts.secret_target:
        return Decision()

    task_id = active_task_id(hook.project_root)
    risk = _risk_profile(hook.project_root)
    verdict = _engine().evaluate(_policy_request(hook, facts, task_id, risk))
    detail = {
        "tool": hook.tool_name,
        "family": facts.family,
        "mutation": facts.mutation.value,
        "risk_profile": risk,
        "rules": [rule.rule_id for rule in verdict.matched_rules],
    }

    if verdict.verdict is VerdictKind.BLOCK:
        what = facts.destructive_reason or facts.secret_target or facts.kind.value
        reason = (
            f"[Grimoire policy] {hook.tool_name or 'action'} refusé — {what}. "
            f"Motif : {verdict.reason or 'règle de sécurité du standard'} (profil de risque {risk}). "
            "Demande une autorisation explicite ou passe par une commande réversible."
        )
        return Decision(outcome=Outcome.DENY, reason=reason, detail=detail)
    if verdict.verdict is VerdictKind.WARN:
        return Decision(
            outcome=Outcome.ASK,
            reason=f"[Grimoire policy] {verdict.reason or 'action sensible'} (profil {risk}).",
            detail=detail,
        )
    return Decision(detail=detail)


def decide_evidence_trace(hook: HookInput) -> Decision:
    """Post tool use: remind the agent that a write owes a line of proof."""
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    facts = classify_tool(hook.tool_name, hook.tool_input)
    if facts.kind is not ActionKind.FILE_WRITE:
        return Decision()
    task_id = active_task_id(hook.project_root)
    touched = ", ".join(facts.targets[:3]) or "le fichier modifié"
    context = (
        f"[Grimoire] Écriture enregistrée ({touched}). Ajoute la preuve correspondante à "
        f"_grimoire-output/evidence/{task_id}/evidence-pack.md — commande exécutée, test vert ou diff clé."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id, "targets": list(facts.targets)})


#: Board states that owe no evidence artifact yet. A task parked here passes
#: every gate by construction — see ``check_evidence_gates``.
_STATES_WITHOUT_EVIDENCE = {"proposed", "", None}


def _gate_summary(project_root: Path, task_id: str) -> tuple[bool, str, dict[str, Any]]:
    # Imported here, not at module scope: evaluating gates needs the standard
    # engine, but deciding a tool call does not. The tool-policy decision runs
    # on every action of every session, and paying 48 ms to import a module it
    # never calls is the difference between a guardrail and a tax.
    from grimoire.core.agentic_standard import check_evidence_gates

    result = check_evidence_gates(project_root, task_id=task_id)
    missing = list(result.missing)
    lines = [f"  - {item}" for item in missing[:6]]
    if len(missing) > 6:
        lines.append(f"  - … {len(missing) - 6} autre(s)")
    summary = "\n".join(lines)
    detail = {
        "task_id": result.task_id,
        "profile": result.profile,
        "state": result.state,
        "ok": result.ok,
        "missing": missing,
    }
    return result.ok, summary, detail


def decide_evidence_gate(hook: HookInput) -> Decision:
    """Stop: refuse to end a governed task whose evidence gates are red.

    The kit's own directive says a closure without green gates is an unfinished
    task. Said in a prompt, that is a suggestion; said here, it is the rule —
    this is the only decision in the module that can make a host refuse.
    """
    if hook.stop_active:
        # The host is already re-running us after a previous block. Blocking a
        # second time is how a session becomes unexitable.
        return Decision(detail={"skipped": "stop_hook_already_active"})
    if not is_standard_enrolled(hook.project_root):
        return Decision(detail={"skipped": "project_not_enrolled"})

    task_id = active_task_id(hook.project_root)
    profile = active_profile_id(hook.project_root)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return Decision(context=f"[Grimoire] Gates non évaluables : {exc}", detail={"error": str(exc)})

    if ok:
        if detail.get("state") in _STATES_WITHOUT_EVIDENCE:
            # Green because nothing is owed yet, not because the work is proven.
            # Saying so is the difference between a guardrail and a placebo.
            return Decision(
                context=(
                    f"[Grimoire] Tâche {task_id} encore en état « {detail.get('state') or 'non défini'} » : "
                    "aucun artefact de preuve n'est exigé à ce stade, donc le gate ne protège rien. "
                    "Passe la tâche à `in_progress` dans _grimoire/standard/task-board.yaml pour "
                    "que la preuve devienne opposable."
                ),
                detail=detail,
            )
        return Decision(detail=detail)
    if profile not in {"governed", "production"}:
        return Decision(
            context=(f"[Grimoire] Gates de preuve rouges pour {task_id} (profil {profile}, non bloquant) :\n{summary}"),
            detail=detail,
        )
    reason = (
        f"[Grimoire] Tâche {task_id} non terminée : les gates de preuve sont rouges (profil {profile}).\n"
        f"{summary}\n"
        f"Complète _grimoire-output/evidence/{task_id}/ puis relance "
        f"`grimoire standard gate check --task-id {task_id} --strict`. "
        "Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
    )
    return Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail)


def decide_subagent_gate(hook: HookInput) -> Decision:
    """Subagent stop: report gate state upward without blocking the sub-agent.

    A sub-agent owns a slice of the work, not the closure of the task; blocking
    it would strand the orchestrator with no way to finish the remaining slices.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return Decision(detail={"error": str(exc)})
    if ok:
        return Decision(detail=detail)
    return Decision(
        context=f"[Grimoire] Sous-agent terminé, gates encore rouges pour {task_id} :\n{summary}",
        detail=detail,
    )


def decide_context_capsule(hook: HookInput) -> Decision:
    """Pre compact: keep the governance state across a context reset.

    Compaction summarises the conversation; it does not know that the task id
    and the open gates are the two facts the next window cannot rebuild.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root)
    profile = active_profile_id(hook.project_root)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return Decision(detail={"error": str(exc)})
    state = "verts" if ok else f"rouges :\n{summary}"
    context = (
        f"[Grimoire — capsule] Tâche {task_id}, profil {profile}. Gates de preuve {state}\n"
        f"Preuves : _grimoire-output/evidence/{task_id}/ — à compléter avant toute clôture."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail=detail)


#: Decision id -> implementation. ``HookSpec.decision`` carries the id, so a
#: host emitter never embeds a rule, only a reference to one.
DECISIONS: dict[str, Callable[[HookInput], Decision]] = {
    "grimoire.activation": decide_activation,
    "grimoire.task-context": decide_task_context,
    "grimoire.tool-policy": decide_tool_policy,
    "grimoire.evidence-trace": decide_evidence_trace,
    "grimoire.evidence-gate": decide_evidence_gate,
    "grimoire.subagent-gate": decide_subagent_gate,
    "grimoire.context-capsule": decide_context_capsule,
}

#: Which decision answers which event when the caller only names an event.
DEFAULT_DECISION_BY_EVENT: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "grimoire.activation",
    HookEvent.USER_PROMPT_SUBMIT: "grimoire.task-context",
    HookEvent.PRE_TOOL_USE: "grimoire.tool-policy",
    HookEvent.POST_TOOL_USE: "grimoire.evidence-trace",
    HookEvent.SUBAGENT_STOP: "grimoire.subagent-gate",
    HookEvent.PRE_COMPACT: "grimoire.context-capsule",
    HookEvent.STOP: "grimoire.evidence-gate",
}


def run_decision(decision_id: str, hook: HookInput) -> Decision:
    """Run *decision_id* against *hook*, never raising into the host."""
    func = DECISIONS.get(decision_id)
    if func is None:
        return Decision(context=f"[Grimoire] décision inconnue : {decision_id}", detail={"error": "unknown-decision"})
    try:
        return func(hook)
    except Exception as exc:
        return Decision(
            outcome=Outcome.ALLOW,
            context=f"[Grimoire] hook {decision_id} en erreur, session non bloquée : {exc}",
            detail={"error": str(exc), "decision": decision_id},
        )
