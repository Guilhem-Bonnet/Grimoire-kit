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
from typing import TYPE_CHECKING

from grimoire.core.standard_state import active_profile_id, active_task_id
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import ToolFacts, classify_tool
from grimoire.policies.engine import _SEVERITY, PolicyEngine
from grimoire.policies.rules_config import load_custom_rules
from grimoire.policies.schemas import (
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyRequest,
    PolicyRule,
    VerdictKind,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def _engine(custom_rules: Sequence[PolicyRule] = ()) -> PolicyEngine:
    engine = PolicyEngine()
    engine.register_rule(_DESTRUCTIVE_AT_STRICT)
    for rule in custom_rules:
        engine.register_rule(rule)
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


def _evaluate_temporal_layer(
    hook: HookInput,
    custom_rules: tuple[PolicyRule, ...],
    facts: ToolFacts,
) -> tuple[VerdictKind, str, list[str]]:
    """Point 3 (issue #429): budgets, prior approval, cooldowns — session-scoped.

    Returns ``(verdict, reason, matched_rule_ids)`` — ``verdict`` is
    ``VerdictKind.ALLOW`` with an empty reason when no temporal rule is
    declared, no rule matches this call, or the host sent no
    ``session_id`` (there is nothing to key a session's state on; see
    :mod:`grimoire.policies.session_state`). Never raises: a corrupted or
    unwritable session-state file degrades to "new session", exactly like
    :func:`grimoire.policies.session_state.load_session_state` documents.
    """
    temporal_rules = tuple(rule for rule in custom_rules if rule.is_temporal)
    if not temporal_rules or not hook.session_id:
        return VerdictKind.ALLOW, "", []

    from grimoire.policies.session_state import load_session_state, save_session_state
    from grimoire.policies.temporal import evaluate_temporal

    now = datetime.now(UTC)
    now_iso = now.isoformat()
    state = load_session_state(hook.project_root, hook.session_id, now_iso=now_iso)
    decision = evaluate_temporal(
        temporal_rules,
        state,
        tool_name=hook.tool_name or "unknown",
        is_write=facts.mutation is not MutationClass.READ_ONLY,
        now=now,
    )
    save_session_state(hook.project_root, decision.state, now_iso=now_iso)
    return decision.verdict, decision.reason, [rule.rule_id for rule in decision.matched_rules]


def decide_tool_policy(hook: HookInput) -> Decision:
    """Pre tool use: run the pending call through the policy engine.

    This is the call site the engine never had: before it, ``PolicyEngine``
    was a library that only its own tests invoked.

    Two layers, evaluated in the same call and escalated together
    (block > ask > allow, see :data:`grimoire.policies.engine._SEVERITY`):
    the request-only base engine (unchanged since before issue #429), and
    the session-scoped temporal layer added by it
    (:func:`_evaluate_temporal_layer`) — budgets, prior approval, cooldowns,
    declared in ``_grimoire/standard/policies.yaml`` (see
    :mod:`grimoire.policies.rules_config`). A project that declares no
    temporal rule pays one ``Path.is_file()`` check more than before and
    behaves exactly as it did before this change.
    """
    facts = classify_tool(hook.tool_name, hook.tool_input)
    custom_rules = load_custom_rules(hook.project_root)
    # A temporal rule (require_approval/per_session/cooldown_after) commonly
    # leaves action_kinds/mutation_classes/risk_profiles empty — it has no
    # opinion on those dimensions, only on the session. Registered as-is into
    # the base engine, ``PolicyRule.matches`` would read those three empty
    # tuples the way it always has ("no constraint" on every dimension) and
    # match — and therefore enforce ``verdict_on_match`` — on *every* request,
    # independently of any session state. The base engine only ever sees the
    # non-temporal rules; temporal ones are the temporal layer's alone.
    base_rules = tuple(rule for rule in custom_rules if not rule.is_temporal)
    has_temporal_rules = any(rule.is_temporal for rule in custom_rules)
    read_only = facts.mutation is MutationClass.READ_ONLY and not facts.secret_target
    if read_only and not has_temporal_rules:
        return Decision()

    task_id = active_task_id(hook.project_root)
    risk = _risk_profile(hook.project_root)
    base_verdict = (
        _engine(base_rules).evaluate(_policy_request(hook, facts, task_id, risk))
        if not read_only
        else None
    )
    temporal_verdict, temporal_reason, temporal_rule_ids = _evaluate_temporal_layer(hook, custom_rules, facts)

    verdict_kind = base_verdict.verdict if base_verdict is not None else VerdictKind.ALLOW
    reason = base_verdict.reason if base_verdict is not None else ""
    matched_rule_ids = [rule.rule_id for rule in base_verdict.matched_rules] if base_verdict is not None else []
    if _SEVERITY[temporal_verdict] > _SEVERITY[verdict_kind]:
        verdict_kind = temporal_verdict
        reason = temporal_reason
    matched_rule_ids.extend(temporal_rule_ids)

    detail = {
        "tool": hook.tool_name,
        "family": facts.family,
        "mutation": facts.mutation.value,
        "risk_profile": risk,
        "rules": matched_rule_ids,
    }

    if verdict_kind is VerdictKind.BLOCK:
        what = facts.destructive_reason or facts.secret_target or facts.kind.value
        motif = reason or "règle de sécurité du standard"
        reason_text = (
            f"[Grimoire policy] {hook.tool_name or 'action'} refusé — {what}. "
            f"Motif : {motif} (profil de risque {risk}). "
            "Demande une autorisation explicite ou passe par une commande réversible."
        )
        return Decision(outcome=Outcome.DENY, reason=reason_text, detail=detail)
    if verdict_kind is VerdictKind.WARN:
        return Decision(
            outcome=Outcome.ASK,
            reason=f"[Grimoire policy] {reason or 'action sensible'} (profil {risk}).",
            detail=detail,
        )
    return Decision(detail=detail)
