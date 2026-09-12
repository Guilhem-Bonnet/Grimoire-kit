"""Temporal policies: budgets, prior approval, cooldowns (issue #429, point 3).

Point 3 of the 2026-09-12 positioning audit
(``docs/audits/positionnement-2026-09-12.md``). Covers the four layers this
change adds:

- :mod:`grimoire.policies.schemas` — the declarative fields and their
  load-time validation (``SessionBudget``, ``CooldownRule``,
  ``PolicyRule.from_dict``/``to_dict``);
- :mod:`grimoire.policies.session_state` — the per-session counters file;
- :mod:`grimoire.policies.temporal` — the pure decision;
- :mod:`grimoire.hosts.decisions.tool_policy` — the ``PreToolUse`` wiring,
  end to end, through ``_grimoire/standard/policies.yaml``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.hosts.decisions._shared import HookInput, Outcome
from grimoire.hosts.decisions.activation import decide_activation
from grimoire.hosts.decisions.tool_policy import decide_tool_policy
from grimoire.hosts.events import HookEvent
from grimoire.policies.schemas import CooldownRule, PolicyRule, SessionBudget, VerdictKind
from grimoire.policies.session_state import (
    SessionState,
    load_session_state,
    reset_session_state,
    save_session_state,
    session_state_path,
)
from grimoire.policies.temporal import evaluate_temporal, glob_match, record_post_tool_use_approval

# ── schemas: validation ──────────────────────────────────────────────────────


def test_session_budget_round_trips_through_dict() -> None:
    budget = SessionBudget(max_tool_calls=10, max_writes=5, max_cost_usd=1.5, max_duration_min=30.0)
    assert SessionBudget.from_dict(budget.to_dict()) == budget


def test_session_budget_rejects_unknown_key() -> None:
    with pytest.raises(GrimoirePolicyError, match="clé"):
        SessionBudget.from_dict({"max_tool_calls": 1, "max_wrytes": 1})


def test_cooldown_rule_round_trips_through_dict() -> None:
    cooldown = CooldownRule(pattern="Bash(rm:*)", count=3, minutes=10.0)
    assert CooldownRule.from_dict(cooldown.to_dict()) == cooldown


def test_cooldown_rule_rejects_unknown_key() -> None:
    with pytest.raises(GrimoirePolicyError, match="clé"):
        CooldownRule.from_dict({"pattern": "*", "count": 1, "minutes": 1, "extra": True})


def _minimal_rule_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "r1",
        "description": "",
        "action_kinds": [],
        "mutation_classes": [],
        "risk_profiles": [],
        "verdict_on_match": "block",
        "reason_template": "blocked",
    }
    base.update(overrides)
    return base


def test_policy_rule_from_dict_backward_compatible_without_temporal_keys() -> None:
    """A rule dict predating issue #429 (no temporal keys at all) still parses,
    and the resulting rule carries none of the temporal semantics."""
    rule = PolicyRule.from_dict(_minimal_rule_dict())
    assert rule.tool_pattern == "*"
    assert rule.require_approval is False
    assert rule.per_session is None
    assert rule.cooldown_after is None
    assert rule.is_temporal is False


def test_policy_rule_from_dict_rejects_unknown_key() -> None:
    with pytest.raises(GrimoirePolicyError, match="clé"):
        PolicyRule.from_dict(_minimal_rule_dict(requires_approval=True))


def test_policy_rule_from_dict_parses_temporal_fields() -> None:
    rule = PolicyRule.from_dict(
        _minimal_rule_dict(
            tool_pattern="Bash(rm:*)",
            require_approval=True,
            per_session={"max_writes": 5},
            cooldown_after={"pattern": "Bash(rm:*)", "count": 3, "minutes": 10},
            estimated_cost_usd=0.02,
        )
    )
    assert rule.is_temporal
    assert rule.per_session == SessionBudget(max_writes=5)
    assert rule.cooldown_after == CooldownRule(pattern="Bash(rm:*)", count=3, minutes=10.0)
    assert rule.to_dict()["require_approval"] is True


# ── session_state ─────────────────────────────────────────────────────────────


def test_session_state_missing_file_is_a_fresh_session(tmp_path: Path) -> None:
    state = load_session_state(tmp_path, "sess-1", now_iso="2026-01-01T00:00:00+00:00")
    assert state.rules == {}
    assert state.started_at == "2026-01-01T00:00:00+00:00"


def test_session_state_round_trips(tmp_path: Path) -> None:
    state = SessionState.new("sess-1", "2026-01-01T00:00:00+00:00")
    state.rule_state("rule-a").calls = 3
    state.rule_state("rule-a").hits = ["2026-01-01T00:00:01+00:00"]
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:00:02+00:00")

    reloaded = load_session_state(tmp_path, "sess-1", now_iso="ignored-since-file-exists")
    assert reloaded.rule_state("rule-a").calls == 3
    assert reloaded.rule_state("rule-a").hits == ["2026-01-01T00:00:01+00:00"]
    assert reloaded.started_at == "2026-01-01T00:00:00+00:00"


def test_session_state_corrupted_file_is_a_fresh_session(tmp_path: Path) -> None:
    path = session_state_path(tmp_path, "sess-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    state = load_session_state(tmp_path, "sess-1", now_iso="2026-01-01T00:00:00+00:00")
    assert state.rules == {}


def test_session_state_wrong_schema_version_is_a_fresh_session(tmp_path: Path) -> None:
    path = session_state_path(tmp_path, "sess-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema_version": 999, "rules": {"x": {"calls": 5}}}', encoding="utf-8")
    state = load_session_state(tmp_path, "sess-1", now_iso="2026-01-01T00:00:00+00:00")
    assert state.rules == {}


def test_reset_session_state_drops_the_file(tmp_path: Path) -> None:
    state = SessionState.new("sess-1", "2026-01-01T00:00:00+00:00")
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:00:00+00:00")
    assert session_state_path(tmp_path, "sess-1").exists()
    reset_session_state(tmp_path, "sess-1")
    assert not session_state_path(tmp_path, "sess-1").exists()


def test_reset_session_state_on_missing_file_does_not_raise(tmp_path: Path) -> None:
    reset_session_state(tmp_path, "never-existed")  # must not raise


def test_session_state_path_sanitizes_unsafe_session_id(tmp_path: Path) -> None:
    unsafe = "../../etc/passwd"
    path = session_state_path(tmp_path, unsafe)
    assert tmp_path in path.parents
    assert ".." not in path.parts


def test_decide_activation_resets_temporal_session(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    path = session_state_path(tmp_path, "sess-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema_version": 1, "rules": {"x": {"calls": 5}}}', encoding="utf-8")
    decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=tmp_path, session_id="sess-1"))
    assert not path.exists()


# ── temporal: pure decision ───────────────────────────────────────────────────


def _budget_rule(max_writes: int | None = None, max_tool_calls: int | None = None) -> PolicyRule:
    return PolicyRule(
        id="budget",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="budget",
        per_session=SessionBudget(max_writes=max_writes, max_tool_calls=max_tool_calls),
    )


def _approval_rule(pattern: str = "*") -> PolicyRule:
    return PolicyRule(
        id="approval",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.WARN,
        reason_template="approval",
        tool_pattern=pattern,
        require_approval=True,
    )


def _cooldown_rule(count: int, minutes: float, pattern: str = "*") -> PolicyRule:
    return PolicyRule(
        id="cooldown",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="cooldown",
        cooldown_after=CooldownRule(pattern=pattern, count=count, minutes=minutes),
    )


def test_glob_match_star_only() -> None:
    assert glob_match("*", "anything")
    assert glob_match("Bash", "Bash")
    assert not glob_match("Bash", "bash")
    assert glob_match("mcp__*__write*", "mcp__grimoire__write_file")
    assert not glob_match("mcp__*__write*", "mcp__grimoire__read_file")


def test_budget_allows_up_to_the_limit_then_blocks_named() -> None:
    rules = (_budget_rule(max_writes=2),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    for _ in range(2):
        decision = evaluate_temporal(rules, state, tool_name="Write", is_write=True)
        assert decision.verdict is VerdictKind.ALLOW
        state = decision.state
    third = evaluate_temporal(rules, state, tool_name="Write", is_write=True)
    assert third.verdict is VerdictKind.BLOCK
    assert "2 écritures" in third.reason


def test_require_approval_keeps_asking_without_a_recorded_post_tool_use() -> None:
    """Regression for the fail-open bug fixed after review (2026-09-12):
    ``evaluate_temporal`` (the ``PreToolUse`` path) must never mark a rule
    approved by itself — only :func:`record_post_tool_use_approval` may,
    and only from ``PostToolUse``, the event a host emits solely when the
    tool actually ran. Without that call in between, a second ``PreToolUse``
    for the same pattern asks again, exactly like the first."""
    rules = (_approval_rule(),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    first = evaluate_temporal(rules, state, tool_name="Bash", is_write=False)
    assert first.verdict is VerdictKind.WARN
    assert first.state.rule_state("approval").approved is False
    second = evaluate_temporal(rules, first.state, tool_name="Bash", is_write=False)
    assert second.verdict is VerdictKind.WARN


def test_require_approval_allows_once_post_tool_use_recorded_it() -> None:
    rules = (_approval_rule(),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    asked = evaluate_temporal(rules, state, tool_name="Bash", is_write=False)
    assert asked.verdict is VerdictKind.WARN

    changed = record_post_tool_use_approval(rules, asked.state, tool_name="Bash")
    assert changed is True
    assert asked.state.rule_state("approval").approved is True

    allowed = evaluate_temporal(rules, asked.state, tool_name="Bash", is_write=False)
    assert allowed.verdict is VerdictKind.ALLOW


def test_record_post_tool_use_approval_is_idempotent_and_pattern_scoped() -> None:
    rules = (_approval_rule(pattern="Bash(rm:*)"),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    # A non-matching tool name records nothing.
    assert record_post_tool_use_approval(rules, state, tool_name="Write") is False
    assert record_post_tool_use_approval(rules, state, tool_name="Bash(rm:*)") is True
    # Already approved: no further state change reported.
    assert record_post_tool_use_approval(rules, state, tool_name="Bash(rm:*)") is False


def test_require_approval_asks_again_in_a_fresh_session() -> None:
    rules = (_approval_rule(),)
    state_a = SessionState.new("session-a", datetime.now(UTC).isoformat())
    evaluate_temporal(rules, state_a, tool_name="Bash", is_write=False)
    state_b = SessionState.new("session-b", datetime.now(UTC).isoformat())
    fresh = evaluate_temporal(rules, state_b, tool_name="Bash", is_write=False)
    assert fresh.verdict is VerdictKind.WARN


def test_cooldown_blocks_after_count_hits_then_clears() -> None:
    rules = (_cooldown_rule(count=2, minutes=10),)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    state = SessionState.new("s", now.isoformat())
    for _ in range(2):
        decision = evaluate_temporal(rules, state, tool_name="Bash", is_write=False, now=now)
        assert decision.verdict is VerdictKind.ALLOW
        state = decision.state
    blocked = evaluate_temporal(rules, state, tool_name="Bash", is_write=False, now=now)
    assert blocked.verdict is VerdictKind.BLOCK
    assert "Refroidissement" in blocked.reason

    from datetime import timedelta

    later = now + timedelta(minutes=15)
    cleared = evaluate_temporal(rules, state, tool_name="Bash", is_write=False, now=later)
    assert cleared.verdict is VerdictKind.ALLOW


def test_non_temporal_rule_is_invisible_to_evaluate_temporal() -> None:
    plain = PolicyRule(
        id="plain",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="should never fire here",
    )
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    decision = evaluate_temporal((plain,), state, tool_name="Write", is_write=True)
    assert decision.verdict is VerdictKind.ALLOW


# ── tool_policy: end-to-end through policies.yaml ────────────────────────────


def _write_policies_yaml(project_root: Path, yaml_text: str) -> None:
    path = project_root / "_grimoire" / "standard" / "policies.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")


@pytest.fixture
def governed_project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def test_write_budget_denies_named_at_the_nplus1th_call(governed_project: Path) -> None:
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: writes-budget
    description: "budget"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget depasse"
    per_session: {max_writes: 2}
""",
    )
    session_id = "sess-budget"

    def call() -> Outcome:
        hook = HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id=session_id,
        )
        return decide_tool_policy(hook).outcome

    assert call() is Outcome.ALLOW
    assert call() is Outcome.ALLOW
    third = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id=session_id,
        )
    )
    assert third.outcome is Outcome.DENY
    assert "2 écritures" in third.reason


def test_require_approval_end_to_end_requires_a_post_tool_use_to_stop_asking(governed_project: Path) -> None:
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: rm-approval
    description: "approval"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: "approbation"
    tool_pattern: "Bash"
    require_approval: true
""",
    )

    def pre(session_id: str) -> Outcome:
        return decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={},
                session_id=session_id,
            )
        ).outcome

    def post(session_id: str) -> None:
        from grimoire.hosts.decisions import run_decision

        run_decision(
            "grimoire.evidence-trace",
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={},
                session_id=session_id,
            ),
        )

    # First attempt: no PostToolUse has ever fired for this pattern in this
    # session, so it asks.
    assert pre("sess-a") is Outcome.ASK
    # Without a recorded PostToolUse (e.g. the human declined, or the agent
    # is only now retrying), a second PreToolUse must ask again — this is
    # the fail-open bug fixed after review: the old version marked the rule
    # approved the moment it merely asked.
    assert pre("sess-a") is Outcome.ASK
    # PostToolUse only fires because the tool actually ran — proof a prior
    # ask was granted — and that is what may mark the pattern approved.
    post("sess-a")
    assert pre("sess-a") is Outcome.ALLOW
    # A fresh session carries no approval at all: it asks again too.
    assert pre("sess-b") is Outcome.ASK


def test_a_purely_temporal_rule_never_blocks_the_first_call(governed_project: Path) -> None:
    """Regression test: a temporal-only rule (empty action_kinds/mutation_classes/
    risk_profiles) must never be registered into the base, request-only engine —
    it would then match, and enforce ``verdict_on_match``, on *every* call
    regardless of session state (see ``tool_policy.decide_tool_policy``)."""
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: writes-budget
    description: "budget"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget depasse"
    per_session: {max_writes: 100}
""",
    )
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id="sess-first-call",
        )
    )
    assert decision.outcome is Outcome.ALLOW


def test_malformed_policies_yaml_raises_a_named_error_at_decide_tool_policy(governed_project: Path) -> None:
    """``decide_tool_policy`` itself does not catch a malformed rule file — its
    caller, ``run_decision``, is what degrades the raise to ``ask`` at the
    hook boundary (see the next test). This pins the narrower contract: the
    error is named (``GR-POL-002``), never silently swallowed or misfiled."""
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: bad-rule
    verdict_on_match: block
    per_sesion: {max_writes: 1}
""",
    )
    with pytest.raises(GrimoirePolicyError, match="GR-POL-002"):
        decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Write",
                tool_input={"file_path": str(governed_project / "f.txt")},
                session_id="sess-x",
            )
        )


def test_malformed_policies_yaml_through_run_decision_asks_with_the_cause(governed_project: Path) -> None:
    from grimoire.hosts.decisions import run_decision

    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: bad-rule
    verdict_on_match: block
    per_sesion: {max_writes: 1}
""",
    )
    decision = run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id="sess-x",
        ),
    )
    assert decision.outcome is Outcome.ASK
    assert "GR-POL-002" in decision.reason


def test_no_policies_yaml_is_unaffected_and_stays_fast(governed_project: Path) -> None:
    """No ``policies.yaml`` at all: the read-only fast path (issue #419) is untouched."""
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Read",
            tool_input={"file_path": "README.md"},
            session_id="sess-x",
        )
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail == {}
