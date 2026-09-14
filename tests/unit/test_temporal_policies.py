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

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.hosts.decisions._shared import HookInput, Outcome
from grimoire.hosts.decisions.activation import decide_activation
from grimoire.hosts.decisions.tool_facts import is_read_only_command
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
from grimoire.policies.temporal import (
    REPAIR_EXEMPTION_REASON,
    evaluate_temporal,
    glob_match,
    record_post_tool_use_approval,
    tool_pattern_matches,
)

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
    # A matching tool name whose command detail does not start with the
    # pattern's prefix ("rm") records nothing either.
    assert record_post_tool_use_approval(rules, state, tool_name="Bash", tool_detail="git status") is False
    assert record_post_tool_use_approval(rules, state, tool_name="Bash", tool_detail="rm -rf x") is True
    # Already approved: no further state change reported.
    assert record_post_tool_use_approval(rules, state, tool_name="Bash", tool_detail="rm -rf x") is False


# ── tool_pattern_matches: the "tool key" fix ─────────────────────────────────
#
# Defect found in real use on Grimoire-Forge: `tool_pattern` used to be
# compared to `tool_name` alone, so a documented pattern like
# `Bash(git push:*)` was matched against the bare string `"Bash"` and could
# never fire — `require_approval: true` answered `allow` on the very first
# occurrence. These pin the fixed "tool key" convention from `docs/hosts.md`.


def test_tool_pattern_matches_bash_command_prefix() -> None:
    assert tool_pattern_matches("Bash(rm:*)", "Bash", "rm -rf x")
    # A word-prefix, not a raw string prefix: "rmdir" is not "rm ".
    assert not tool_pattern_matches("Bash(rm:*)", "Bash", "rmdir foo")
    assert tool_pattern_matches("Bash(git push:*)", "Bash", "git push origin main")
    assert not tool_pattern_matches("Bash(git push:*)", "Bash", "git pull")
    # The exact prefix alone (no trailing arguments) still matches.
    assert tool_pattern_matches("Bash(rm:*)", "Bash", "rm")


def test_tool_pattern_matches_file_target_glob() -> None:
    assert tool_pattern_matches(
        "Write(_grimoire/standard/*)", "Write", "_grimoire/standard/policies.yaml"
    )
    assert not tool_pattern_matches("Write(_grimoire/standard/*)", "Write", "other/path.yaml")


def test_tool_pattern_matches_star_is_unchanged() -> None:
    assert tool_pattern_matches("*", "Bash", "")
    assert tool_pattern_matches("*", "Bash", "rm -rf x")


def test_tool_pattern_matches_bare_name_is_unchanged() -> None:
    assert tool_pattern_matches("Bash", "Bash", "")
    assert not tool_pattern_matches("Bash", "Write", "")


def test_tool_pattern_matches_detail_less_mcp_call() -> None:
    """An MCP tool with no established argument convention (`detail == ""`,
    see `policy_tool_detail`) can only match the bare-name form or a
    parenthesised pattern whose body is exactly ``"*"``."""
    assert tool_pattern_matches("mcp__grimoire__write_file(*)", "mcp__grimoire__write_file", "")
    assert not tool_pattern_matches(
        "mcp__grimoire__write_file(secret)", "mcp__grimoire__write_file", ""
    )
    assert tool_pattern_matches("mcp__grimoire__write_file", "mcp__grimoire__write_file", "")


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


def test_git_push_command_prefix_end_to_end_asks_then_allows_after_post_tool_use(
    governed_project: Path,
) -> None:
    """Reproduction of the defect found in real use on Grimoire-Forge: with
    the bug, `tool_pattern: "Bash(git push:*)"` was compared to the bare
    string `"Bash"` and never matched, so `require_approval: true` answered
    `allow` on the very first `git push`. Both `docs/hosts.md` examples are
    covered: `rm` and `git push`."""
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: git-push-approval
    description: "git push demande une confirmation, une fois par session"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: "Push demandant une approbation explicite"
    tool_pattern: "Bash(git push:*)"
    require_approval: true
""",
    )
    session_id = "sess-git-push"

    def pre(command: str) -> Outcome:
        return decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": command},
                session_id=session_id,
            )
        ).outcome

    def post(command: str) -> None:
        from grimoire.hosts.decisions import run_decision

        run_decision(
            "grimoire.evidence-trace",
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": command},
                session_id=session_id,
            ),
        )

    # A command the pattern is not about is never asked for.
    assert pre("git pull") is Outcome.ALLOW
    # The documented pattern must actually fire: this is the regression this
    # fix closes — it used to be `Outcome.ALLOW` here.
    assert pre("git push origin main") is Outcome.ASK
    # Retried without a PostToolUse in between: still asks (fail-open guard).
    assert pre("git push origin main") is Outcome.ASK
    post("git push origin main")
    assert pre("git push origin main") is Outcome.ALLOW


def test_rm_command_prefix_end_to_end_asks(tmp_path: Path) -> None:
    """``Bash(rm:*)`` on ``rm -rf x`` from `docs/hosts.md`. Uses the
    ``production`` (strict) profile so the base engine's own
    destructive-mutation builtin also *warns* rather than *blocks* — a
    ``rm -rf`` is flagged destructive independently of this fix (see
    `tool_facts._DESTRUCTIVE_PATTERNS`), and blocking would mask whether the
    temporal ``require_approval`` layer fired at all. Both stay `warn`, so
    the observable outcome (`ask`) is unambiguous either way."""
    setup_standard_profile(tmp_path, profile_id="production", task_id="bootstrap")
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: rm-approval
    description: "rm demande une confirmation"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: "Suppression demandant une approbation explicite"
    tool_pattern: "Bash(rm:*)"
    require_approval: true
""",
    )
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=tmp_path,
            tool_name="Bash",
            tool_input={"command": "rm -rf x"},
            session_id="sess-rm",
        )
    )
    assert decision.outcome is Outcome.ASK


def test_write_target_glob_pattern_end_to_end_asks(governed_project: Path) -> None:
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: standard-write-approval
    description: "Toute ecriture sous _grimoire/standard demande une confirmation"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: "Ecriture du standard demandant une approbation explicite"
    tool_pattern: "Write(_grimoire/standard/*)"
    require_approval: true
""",
    )
    # A workspace-relative path, matching how `docs/hosts.md`'s
    # `Write(_grimoire/standard/*)` example reads — an absolute path would
    # never start with the literal prefix the pattern names.
    matching = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": "_grimoire/standard/policies.yaml"},
            session_id="sess-write",
        )
    )
    assert matching.outcome is Outcome.ASK
    not_matching = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": "other.txt"},
            session_id="sess-write-2",
        )
    )
    assert not_matching.outcome is Outcome.ALLOW


def test_cooldown_with_command_prefix_pattern_end_to_end(governed_project: Path) -> None:
    """Cooldown keyed on a ``Bash(rm:*)``-shaped pattern. Uses ``rm old-log.txt``
    (no ``-r``/``-f`` flag) rather than the doc's ``rm -rf x`` so the base
    engine's unrelated destructive-mutation builtin never fires here — this
    test is only about the cooldown pattern matching the command prefix, not
    about the destructive-command guard (covered separately)."""
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: rm-cooldown
    description: "rm limite a 2 par 10 minutes"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "Refroidissement rm"
    cooldown_after: {pattern: "Bash(rm:*)", count: 2, minutes: 10}
""",
    )
    session_id = "sess-rm-cooldown"

    def rm_call() -> Outcome:
        return decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": "rm old-log.txt"},
                session_id=session_id,
            )
        ).outcome

    assert rm_call() is Outcome.ALLOW
    assert rm_call() is Outcome.ALLOW
    assert rm_call() is Outcome.DENY
    # A command the pattern does not cover is never rate-limited by it.
    assert (
        decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": "ls -la"},
                session_id=session_id,
            )
        ).outcome
        is Outcome.ALLOW
    )


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


# ── Defect 1 (issue #463): reads must not count as writes ────────────────────
#
# Real incident on Grimoire-Forge: a `per_session` rule with no `tool_pattern`
# ended up refusing every tool in the session, including read-only `Bash`
# calls (`cat`, `grep`, `find`, `git status`...) that `classify_tool` used to
# treat as a mutation the instant they carried a command string at all.


@pytest.mark.parametrize(
    "command",
    [
        "cat file.txt",
        "grep -rn foo src/",
        "find . -name '*.py'",
        "git status",
        "gh pr view 42",
        "ls -la",
        "git status && echo done",
    ],
)
def test_is_read_only_command_recognises_common_reads(command: str) -> None:
    assert is_read_only_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "git commit -m 'x'",
        "git push origin main",
        "cat file.txt >> out.txt",
        "cat file.txt | tee out.txt",
        "sed -i s/a/b/ file.txt",
        "git branch -d old",
        "",
    ],
)
def test_is_read_only_command_stays_conservative_on_writes(command: str) -> None:
    assert not is_read_only_command(command)


def test_write_budget_of_one_survives_ten_bash_reads_then_blocks_a_write(
    governed_project: Path,
) -> None:
    """The exact acceptance shape of defect 1: ten reads must never touch a
    ``max_writes: 1`` budget; the first real write still hits it."""
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
    per_session: {max_writes: 1}
""",
    )
    session_id = "sess-reads-dont-count"

    def bash_read(command: str) -> Outcome:
        return decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": command},
                session_id=session_id,
            )
        ).outcome

    reads = ["cat a.txt", "grep -rn x .", "find . -name '*.py'", "git status", "ls"] * 2
    assert len(reads) == 10
    for command in reads:
        assert bash_read(command) is Outcome.ALLOW

    first_write = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id=session_id,
        )
    )
    assert first_write.outcome is Outcome.ALLOW

    second_write = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "g.txt")},
            session_id=session_id,
        )
    )
    assert second_write.outcome is Outcome.DENY
    assert "écritures" in second_write.reason


# ── Defect 2 (issue #463): repair exemption ───────────────────────────────────


def test_read_only_call_is_exempt_once_a_call_budget_is_exhausted() -> None:
    rules = (_budget_rule(max_tool_calls=1),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    first = evaluate_temporal(rules, state, tool_name="Read", is_write=False)
    assert first.verdict is VerdictKind.ALLOW
    second = evaluate_temporal(rules, first.state, tool_name="Read", is_write=False)
    assert second.verdict is VerdictKind.ALLOW
    assert any(m.reason == REPAIR_EXEMPTION_REASON for m in second.matched_rules)


def test_edit_of_policies_yaml_is_exempt_once_the_write_budget_is_reached(
    governed_project: Path,
) -> None:
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
    per_session: {max_writes: 1}
""",
    )
    session_id = "sess-repair"
    policies_path = governed_project / "_grimoire" / "standard" / "policies.yaml"

    first_write = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id=session_id,
        )
    )
    assert first_write.outcome is Outcome.ALLOW

    blocked = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "other.txt")},
            session_id=session_id,
        )
    )
    assert blocked.outcome is Outcome.DENY
    assert "grimoire policies reset-session" in blocked.reason

    repair = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Edit",
            tool_input={"file_path": str(policies_path)},
            session_id=session_id,
        )
    )
    assert repair.outcome is Outcome.ALLOW


# ── Defect 3 (issue #463): sub-agents share the parent session's budget ──────


def test_session_budget_subagents_defaults_to_shared() -> None:
    assert SessionBudget.from_dict({"max_writes": 5}).subagents == "shared"
    assert SessionBudget.from_dict({"max_writes": 5, "subagents": "shared"}).subagents == "shared"


def test_session_budget_subagents_separate_is_refused_by_name() -> None:
    with pytest.raises(GrimoirePolicyError, match="GR-POL-003"):
        SessionBudget.from_dict({"max_writes": 5, "subagents": "separate"})


def test_session_budget_subagents_unknown_value_is_refused() -> None:
    with pytest.raises(GrimoirePolicyError, match="GR-POL-002"):
        SessionBudget.from_dict({"max_writes": 5, "subagents": "isolated"})


# ── Relapse (issue #481, 2026-09-14): duration budget locked out reads and ───
# ── the remedy command it names ───────────────────────────────────────────────
#
# Real incident on Grimoire-Forge: a `per_session.max_duration_min: 1440`
# rule in `block`, once the session outlived its window, refused *every*
# matching tool — including a read-only `Bash` call (`git status`) already
# covered by `_is_repair_exempt`'s `not is_write` branch, and, unlike that
# one, `grimoire policies reset-session` itself: an unrecognised leading
# verb (`grimoire`) makes `is_read_only_command` classify it as a mutation,
# and its command line never matches the file-glob repair patterns — so the
# very remedy `_BUDGET_REMEDY_SUFFIX` names was itself refused.


@pytest.mark.parametrize(
    "command",
    [
        "grimoire policies reset-session",
        "grimoire policies status",
        "/home/u/.venv/bin/grimoire policies reset-session",
        "python -m grimoire policies reset-session",
        "python3 -m grimoire policies status",
    ],
)
def test_grimoire_policies_invocation_is_exempt_in_every_documented_shape(command: str) -> None:
    rules = (_budget_rule(max_writes=0),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    decision = evaluate_temporal(rules, state, tool_name="Bash", tool_detail=command, is_write=True)
    assert decision.verdict is VerdictKind.ALLOW
    assert any(m.reason == REPAIR_EXEMPTION_REASON for m in decision.matched_rules)


@pytest.mark.parametrize(
    "command",
    [
        "echo grimoire policies reset-session",
        "grimoire standard verify",
        "rm -rf grimoire policies",
    ],
)
def test_grimoire_policies_lookalikes_stay_refused(command: str) -> None:
    """Naming the command, or invoking an unrelated `grimoire` subcommand,
    must not borrow the exemption — only the actual invocation shape does."""
    rules = (_budget_rule(max_writes=0),)
    state = SessionState.new("s", datetime.now(UTC).isoformat())
    decision = evaluate_temporal(rules, state, tool_name="Bash", tool_detail=command, is_write=True)
    assert decision.verdict is VerdictKind.BLOCK


@pytest.mark.parametrize(
    "budget_kwargs",
    [
        {"max_tool_calls": 1},
        {"max_writes": 1},
        {"max_cost_usd": 0.5},
        {"max_duration_min": 1},
    ],
)
def test_grimoire_policies_command_is_exempt_across_every_budget_dimension(
    budget_kwargs: dict[str, float | int],
) -> None:
    """The repair exemption is one gate every `per_session` dimension calls
    before refusing (see `_is_repair_exempt`) — proven here dimension by
    dimension, not just for `max_writes` (already covered by defect 2's own
    tests above)."""
    rule = PolicyRule(
        id="budget",
        description="",
        action_kinds=(),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="budget",
        per_session=SessionBudget(**budget_kwargs),
        estimated_cost_usd=1.0,
    )
    now = datetime.now(UTC)
    is_duration = "max_duration_min" in budget_kwargs
    started = now - timedelta(hours=25) if is_duration else now
    state = SessionState.new("s", started.isoformat())
    if not is_duration:
        # Exhaust the counter/cost dimension with one ordinary write first —
        # `max_duration_min` needs no such step: it is already past its
        # window from the moment the session started.
        exhausted = evaluate_temporal((rule,), state, tool_name="Bash", tool_detail="echo x", is_write=True, now=now)
        state = exhausted.state
    decision = evaluate_temporal(
        (rule,), state, tool_name="Bash", tool_detail="grimoire policies reset-session", is_write=True, now=now
    )
    assert decision.verdict is VerdictKind.ALLOW
    assert any(m.reason == REPAIR_EXEMPTION_REASON for m in decision.matched_rules)


def _write_session_state(project_root: Path, session_id: str, *, started_at: str) -> None:
    save_session_state(project_root, SessionState.new(session_id, started_at), now_iso=started_at)


def test_duration_budget_end_to_end_exempts_reads_and_the_reset_command_it_recommends(
    governed_project: Path,
) -> None:
    """The exact reproduction from issue #481: a session 25h old against a
    `max_duration_min: 1440` `block` budget must still allow a read-only
    `Bash` call and `grimoire policies reset-session` — and an unrelated
    write it does still refuse must name a command that is, in the same
    breath, actually allowed."""
    _write_policies_yaml(
        governed_project,
        """
rules:
  - id: session-duration-budget
    description: "budget de duree"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "fenetre depassee"
    per_session: {max_duration_min: 1440}
""",
    )
    session_id = "sess-25h-old"
    started_at = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    _write_session_state(governed_project, session_id, started_at=started_at)

    def bash(command: str) -> Outcome:
        return decide_tool_policy(
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=governed_project,
                tool_name="Bash",
                tool_input={"command": command},
                session_id=session_id,
            )
        ).outcome

    assert bash("git status") is Outcome.ALLOW
    for reset_command in (
        "grimoire policies reset-session",
        "grimoire policies status",
        "python -m grimoire policies reset-session",
    ):
        assert bash(reset_command) is Outcome.ALLOW, reset_command

    other_write = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed_project,
            tool_name="Write",
            tool_input={"file_path": str(governed_project / "f.txt")},
            session_id=session_id,
        )
    )
    assert other_write.outcome is Outcome.DENY
    # The refusal's own recommendation, replayed verbatim, must not itself
    # be refused — a refusal never points to a dead end.
    assert "grimoire policies reset-session" in other_write.reason
    assert bash("grimoire policies reset-session") is Outcome.ALLOW
