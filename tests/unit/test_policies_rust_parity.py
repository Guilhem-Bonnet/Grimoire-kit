"""Cross-backend parity for the Policy Engine (issue #354).

`grimoire.policies.engine` optionally delegates its matching loop to a
PyO3-compiled Rust core (`rust/grimoire-policies-core/`) when it is
importable, and falls back to the pure-Python implementation otherwise —
see the module docstring in `engine.py` for the `GRIMOIRE_POLICIES_BACKEND`
override this file relies on.

`tests/unit/test_policies.py` proves each backend individually behaves like
`PolicyEngine` always has (it is the golden contract and stays unmodified in
both configurations). This file proves something narrower and easy to miss:
that the two backends do not merely both "work" in isolation, they produce
*the same verdict* on the same inputs. Two implementations can each pass
their own tests and still disagree with each other — that is exactly what a
port is supposed to rule out.

When the compiled core is not installed (the default contributor
environment, and the normal CI job), the parity tests are skipped rather
than failed: there is nothing to compare against, and that is expected, not
a regression. The dedicated Rust CI job installs the core first and is
where this file actually exercises both sides.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.policies.engine import PolicyEngine, rust_backend_available
from grimoire.policies.schemas import (
    ActionKind,
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyMode,
    PolicyRequest,
    PolicyRule,
    PolicyVerdict,
    VerdictKind,
)

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_policies_core not installed — build it locally (maturin develop) or run the Rust CI job",
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


def _evaluate_with_backend(
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: PolicyMode = PolicyMode.ENFORCED,
    include_builtins: bool = True,
    extra_rules: tuple[PolicyRule, ...] = (),
    request: PolicyRequest | None = None,
) -> PolicyVerdict:
    monkeypatch.setenv("GRIMOIRE_POLICIES_BACKEND", backend)
    engine = PolicyEngine(mode=mode, include_builtins=include_builtins)
    for rule in extra_rules:
        engine.register_rule(rule)
    return engine.evaluate(request if request is not None else _make_request())


def _assert_same_verdict(python_verdict: PolicyVerdict, rust_verdict: PolicyVerdict) -> None:
    """Compare everything except `id`/`created_at` — both are randomised or
    wall-clock, generated identically by `engine.py` regardless of backend
    (see its module docstring), and therefore never expected to match byte
    for byte between two separate `evaluate()` calls."""
    assert python_verdict.verdict == rust_verdict.verdict
    assert python_verdict.mode == rust_verdict.mode
    assert python_verdict.reason == rust_verdict.reason
    assert python_verdict.allow_retry_after == rust_verdict.allow_retry_after
    assert len(python_verdict.matched_rules) == len(rust_verdict.matched_rules)
    for p_rule, r_rule in zip(python_verdict.matched_rules, rust_verdict.matched_rules, strict=True):
        assert p_rule.rule_id == r_rule.rule_id
        assert p_rule.verdict == r_rule.verdict
        assert p_rule.reason == r_rule.reason


# ── Same verdict, not just "doesn't crash" ──────────────────────────────────

_SCENARIOS: tuple[tuple[ActionKind, MutationClass, str], ...] = (
    (ActionKind.TOOL_USE, MutationClass.READ_ONLY, "standard"),
    (ActionKind.TOOL_USE, MutationClass.DESTRUCTIVE, "standard"),
    (ActionKind.TOOL_USE, MutationClass.DESTRUCTIVE, "light"),
    (ActionKind.TOOL_USE, MutationClass.DESTRUCTIVE, "strict"),
    (ActionKind.SECRET_ACCESS, MutationClass.READ_ONLY, "standard"),
    (ActionKind.PACK_ACTIVATION, MutationClass.PACK_ACTIVATION, "standard"),
    (ActionKind.TASK_CLOSE, MutationClass.READ_ONLY, "standard"),
    (ActionKind.MISSION_CLOSE, MutationClass.MUTATION_CONTROLLED, "standard"),
    (ActionKind.NETWORK, MutationClass.READ_ONLY, "standard"),
    (ActionKind.FILE_WRITE, MutationClass.MUTATION_CONTROLLED, "light"),
)


@requires_rust_core
@pytest.mark.parametrize(("kind", "mutation", "risk"), _SCENARIOS)
def test_builtin_rules_agree_across_backends(
    monkeypatch: pytest.MonkeyPatch, kind: ActionKind, mutation: MutationClass, risk: str
) -> None:
    request = _make_request(kind=kind, mutation=mutation, risk=risk)
    python_verdict = _evaluate_with_backend("python", monkeypatch, request=request)
    rust_verdict = _evaluate_with_backend("rust", monkeypatch, request=request)
    _assert_same_verdict(python_verdict, rust_verdict)


@requires_rust_core
@pytest.mark.parametrize("mode", [PolicyMode.ENFORCED, PolicyMode.SHADOW, PolicyMode.CANARY])
def test_shadow_downgrade_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, mode: PolicyMode) -> None:
    request = _make_request(mutation=MutationClass.DESTRUCTIVE, risk="standard")
    python_verdict = _evaluate_with_backend("python", monkeypatch, mode=mode, request=request)
    rust_verdict = _evaluate_with_backend("rust", monkeypatch, mode=mode, request=request)
    _assert_same_verdict(python_verdict, rust_verdict)


@requires_rust_core
def test_custom_rules_and_most_restrictive_wins_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_rules = (
        PolicyRule(
            id="warn-on-tool-use",
            description="",
            action_kinds=(ActionKind.TOOL_USE,),
            mutation_classes=(),
            risk_profiles=(),
            verdict_on_match=VerdictKind.WARN,
            reason_template="warn reason",
        ),
        PolicyRule(
            id="block-on-tool-use",
            description="",
            action_kinds=(ActionKind.TOOL_USE,),
            mutation_classes=(),
            risk_profiles=(),
            verdict_on_match=VerdictKind.BLOCK,
            reason_template="block reason",
        ),
    )
    request = _make_request()
    python_verdict = _evaluate_with_backend(
        "python", monkeypatch, include_builtins=False, extra_rules=custom_rules, request=request
    )
    rust_verdict = _evaluate_with_backend(
        "rust", monkeypatch, include_builtins=False, extra_rules=custom_rules, request=request
    )
    _assert_same_verdict(python_verdict, rust_verdict)
    assert python_verdict.verdict == VerdictKind.BLOCK


@requires_rust_core
def test_no_rules_matched_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request()
    python_verdict = _evaluate_with_backend("python", monkeypatch, include_builtins=False, request=request)
    rust_verdict = _evaluate_with_backend("rust", monkeypatch, include_builtins=False, request=request)
    _assert_same_verdict(python_verdict, rust_verdict)
    assert python_verdict.verdict == VerdictKind.ALLOW


# ── Backend selection itself ────────────────────────────────────────────────


def test_backend_python_forced_ignores_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when the compiled core is installed, GRIMOIRE_POLICIES_BACKEND=python
    must stay on the pure-Python path — this is the "testable in both
    directions" toggle the port is required to provide."""
    monkeypatch.setenv("GRIMOIRE_POLICIES_BACKEND", "python")
    verdict = PolicyEngine().evaluate(_make_request(mutation=MutationClass.DESTRUCTIVE))
    assert verdict.verdict == VerdictKind.BLOCK


@requires_rust_core
def test_backend_rust_forced_uses_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_POLICIES_BACKEND", "rust")
    verdict = PolicyEngine().evaluate(_make_request(mutation=MutationClass.DESTRUCTIVE))
    assert verdict.verdict == VerdictKind.BLOCK


def test_backend_rust_forced_without_compiled_core_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    if rust_backend_available():
        pytest.skip("grimoire_policies_core is installed in this environment — nothing to reject")
    monkeypatch.setenv("GRIMOIRE_POLICIES_BACKEND", "rust")
    with pytest.raises(GrimoirePolicyError, match="introuvable"):
        PolicyEngine().evaluate(_make_request())


def test_backend_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_POLICIES_BACKEND", "not-a-real-backend")
    with pytest.raises(GrimoirePolicyError, match="GRIMOIRE_POLICIES_BACKEND invalide"):
        PolicyEngine().evaluate(_make_request())


def test_backend_auto_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset (or "auto") must behave exactly like today: whichever backend is
    available, silently — no environment variable required for the common
    case of "just use PolicyEngine"."""
    monkeypatch.delenv("GRIMOIRE_POLICIES_BACKEND", raising=False)
    verdict = PolicyEngine().evaluate(_make_request(mutation=MutationClass.DESTRUCTIVE))
    assert verdict.verdict == VerdictKind.BLOCK
