"""``PreToolUse``: run the pending call through the policy engine.

The heaviest decision in the package on purpose — it is the only one that
needs :mod:`grimoire.policies.engine`, :mod:`grimoire.policies.schemas` and
the policy request machinery below. Splitting :mod:`grimoire.hosts.decisions`
into a package (issue #419) means the other six decisions never import any
of it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.standard_state import active_profile_id, active_task_id
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import ToolFacts, classify_tool
from grimoire.policies.engine import PolicyEngine
from grimoire.policies.schemas import (
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyRequest,
    PolicyRule,
    VerdictKind,
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
