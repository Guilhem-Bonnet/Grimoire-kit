"""Tests for the Policy Engine module."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.policies.engine import PolicyEngine
from grimoire.policies.schemas import (
    ActionKind,
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyMode,
    PolicyRequest,
    PolicyRule,
    VerdictKind,
)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _make_request(
    tool: str = "shell",
    kind: ActionKind = ActionKind.TOOL_USE,
    mutation: MutationClass = MutationClass.READ_ONLY,
    risk: str = "standard",
) -> PolicyRequest:
    return PolicyRequest(
        id="req-001",
        run_id="RUN-test",
        task_id="GAO-task-001",
        actor=PolicyActor(actor_id="agent", host_id="host-test"),
        action=PolicyAction(kind=kind, tool=tool, mutation_class=mutation),
        risk_profile=risk,
        created_at=_now_iso(),
    )


# ── Allow ─────────────────────────────────────────────────────────────────────

def test_allow_by_default():
    engine = PolicyEngine(mode=PolicyMode.ENFORCED, include_builtins=False)
    verdict = engine.evaluate(_make_request())
    assert verdict.verdict == VerdictKind.ALLOW


def test_allow_read_only_tool():
    engine = PolicyEngine()
    verdict = engine.evaluate(_make_request(tool="filesystem.read", mutation=MutationClass.READ_ONLY))
    assert verdict.verdict == VerdictKind.ALLOW


# ── Block ─────────────────────────────────────────────────────────────────────

def test_block_destructive_on_standard():
    engine = PolicyEngine()
    req = _make_request(mutation=MutationClass.DESTRUCTIVE, risk="standard")
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.BLOCK


def test_block_secret_access():
    engine = PolicyEngine()
    req = _make_request(kind=ActionKind.SECRET_ACCESS)
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.BLOCK


def test_block_pack_activation_builtin():
    engine = PolicyEngine()
    req = _make_request(kind=ActionKind.PACK_ACTIVATION)
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.BLOCK


# ── Shadow mode ───────────────────────────────────────────────────────────────

def test_shadow_mode_downgrades_block_to_warn():
    engine = PolicyEngine(mode=PolicyMode.SHADOW)
    req = _make_request(mutation=MutationClass.DESTRUCTIVE)
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.WARN
    assert verdict.reason.startswith("[shadow]")


# ── evaluate_or_raise ─────────────────────────────────────────────────────────

def test_evaluate_or_raise_blocks_and_raises():
    engine = PolicyEngine(mode=PolicyMode.ENFORCED)
    req = _make_request(mutation=MutationClass.DESTRUCTIVE)
    with pytest.raises(GrimoirePolicyError, match="Policy blocked"):
        engine.evaluate_or_raise(req)


def test_evaluate_or_raise_allow_does_not_raise():
    engine = PolicyEngine(mode=PolicyMode.ENFORCED, include_builtins=False)
    req = _make_request()
    verdict = engine.evaluate_or_raise(req)
    assert verdict.verdict == VerdictKind.ALLOW


# ── Custom rules ──────────────────────────────────────────────────────────────

def test_register_custom_rule():
    engine = PolicyEngine(include_builtins=False)
    rule = PolicyRule(
        id="no-network",
        description="Block all network calls",
        action_kinds=(ActionKind.NETWORK,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="Network access disabled",
    )
    engine.register_rule(rule)
    req = _make_request(kind=ActionKind.NETWORK)
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.BLOCK


def test_register_duplicate_rule_raises():
    engine = PolicyEngine(include_builtins=False)
    rule = PolicyRule(
        id="dup",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.WARN,
    )
    engine.register_rule(rule)
    with pytest.raises(GrimoirePolicyError, match="already registered"):
        engine.register_rule(rule)


def test_remove_rule():
    engine = PolicyEngine(include_builtins=False)
    rule = PolicyRule(
        id="removable",
        description="",
        action_kinds=(ActionKind.NETWORK,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
    )
    engine.register_rule(rule)
    removed = engine.remove_rule("removable")
    assert removed is True
    req = _make_request(kind=ActionKind.NETWORK)
    verdict = engine.evaluate(req)
    assert verdict.verdict == VerdictKind.ALLOW


def test_most_restrictive_verdict_wins():
    engine = PolicyEngine(include_builtins=False)
    engine.register_rule(PolicyRule(
        id="warn-rule",
        description="",
        action_kinds=(ActionKind.TOOL_USE,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.WARN,
    ))
    engine.register_rule(PolicyRule(
        id="block-rule",
        description="",
        action_kinds=(ActionKind.TOOL_USE,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
    ))
    verdict = engine.evaluate(_make_request())
    assert verdict.verdict == VerdictKind.BLOCK
