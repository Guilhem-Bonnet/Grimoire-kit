"""Tests for `grimoire policies` CLI commands (issue #463, defects 4 and 5).

Covers what `tests/unit/test_temporal_policies.py` does not: the CLI surface
around the temporal policy layer — `status`'s low-budget warning,
`reset-session`, and `doctor`'s design guard against a globally-blocking
`per_session` rule.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.policies.session_state import SessionState, save_session_state, session_state_path

runner = CliRunner()


def _write_policies_yaml(project_root: Path, yaml_text: str) -> None:
    path = project_root / "_grimoire" / "standard" / "policies.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")


# ── Defect 4: `status` warns below 10% of a budget's ceiling ─────────────────


def test_status_shows_atteint_once_the_budget_is_fully_used(tmp_path: Path) -> None:
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: writes-budget
    description: "budget"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    per_session: {max_writes: 10}
""",
    )
    state = SessionState.new("sess-low", "2026-01-01T00:00:00+00:00")
    rule_state = state.rule_state("writes-budget")
    rule_state.calls = 10
    rule_state.writes = 10  # exactly at 0 remaining
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:05:00+00:00")

    result = runner.invoke(app, ["policies", "status", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "atteint" in result.output


def test_status_warns_strictly_below_ten_percent_remaining(tmp_path: Path) -> None:
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: writes-budget
    description: "budget"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    per_session: {max_writes: 11}
""",
    )
    state = SessionState.new("sess-low", "2026-01-01T00:00:00+00:00")
    rule_state = state.rule_state("writes-budget")
    # 10/11 written: ~9.1% of the ceiling remains — strictly under 10%.
    rule_state.writes = 10
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:05:00+00:00")
    result = runner.invoke(app, ["policies", "status", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "ATTENTION" in result.output

    # 9/11 written: ~18.2% remains — at or above the threshold, no warning.
    rule_state.writes = 9
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:05:00+00:00")
    result = runner.invoke(app, ["policies", "status", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "ATTENTION" not in result.output


def test_status_does_not_warn_with_headroom(tmp_path: Path) -> None:
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: writes-budget
    description: "budget"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    per_session: {max_writes: 10}
""",
    )
    state = SessionState.new("sess-ok", "2026-01-01T00:00:00+00:00")
    rule_state = state.rule_state("writes-budget")
    rule_state.writes = 2
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:05:00+00:00")

    result = runner.invoke(app, ["policies", "status", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "ATTENTION" not in result.output


# ── Defect 4: `reset-session` ──────────────────────────────────────────────────


def test_reset_session_deletes_the_state_file_and_names_it(tmp_path: Path) -> None:
    state = SessionState.new("sess-to-reset", "2026-01-01T00:00:00+00:00")
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:00:00+00:00")
    path = session_state_path(tmp_path, "sess-to-reset")
    assert path.is_file()

    result = runner.invoke(
        app, ["policies", "reset-session", "--session-id", "sess-to-reset", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert str(path) in result.output
    assert not path.exists()


def test_reset_session_with_no_session_reports_nothing_to_do(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policies", "reset-session", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Aucune session" in result.output


def test_reset_session_json_output(tmp_path: Path) -> None:
    state = SessionState.new("sess-json", "2026-01-01T00:00:00+00:00")
    save_session_state(tmp_path, state, now_iso="2026-01-01T00:00:00+00:00")

    result = runner.invoke(
        app,
        [
            "policies",
            "reset-session",
            "--session-id",
            "sess-json",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_id"] == "sess-json"
    assert payload["deleted"] is True


# ── Defect 5: `doctor` warns about a globally-blocking `per_session` rule ────


def test_doctor_warns_on_a_tool_pattern_less_blocking_budget(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: global-block
    description: "budget global bloquant"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    per_session: {max_writes: 800, max_tool_calls: 3000}
""",
    )
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert "WARN" in result.output
    assert "global-block" in result.output
    assert "tool_pattern" in result.output


def test_doctor_does_not_warn_on_a_scoped_or_warn_only_budget(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: scoped-block
    description: "budget cible"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    tool_pattern: "Bash(rm:*)"
    per_session: {max_writes: 10}
  - id: global-warn
    description: "budget global non bloquant"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: "budget"
    per_session: {max_writes: 50}
""",
    )
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert "policy_budget_guard" not in result.output or "WARN" not in result.output.split(
        "policy_budget_guard"
    )[0]


def test_doctor_json_reports_policy_budget_guard(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: global-block
    description: "budget global bloquant"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "budget"
    per_session: {max_writes: 800}
""",
    )
    result = runner.invoke(app, ["doctor", "-o", "json", str(tmp_path)])
    payload = json.loads(result.output)
    guard = next(c for c in payload["checks"] if c["name"] == "policy_budget_guard")
    assert guard["level"] == "warn"
    assert guard["passed"] is True
    assert "global-block" in guard["detail"]


# ── Relapse (issue #481, 2026-09-14): `max_duration_min` measured from ───────
# ── `SessionStart`, not from "time actually working" ─────────────────────────


def test_doctor_warns_on_a_short_global_duration_budget(tmp_path: Path) -> None:
    """A `max_duration_min` under 2880 (48h) with no `tool_pattern`, in
    `block`, is a live risk for any host whose session can span days (a
    Claude Code session left open over a weekend) — the incident that
    motivated issue #481. `doctor` names it, distinctly from the general
    `policy_budget_guard` WARN above."""
    runner.invoke(app, ["init", str(tmp_path)])
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: session-duration-budget
    description: "budget de duree globale"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "fenetre depassee"
    per_session: {max_duration_min: 1440}
""",
    )
    result = runner.invoke(app, ["doctor", "-o", "json", str(tmp_path)])
    payload = json.loads(result.output)
    guard = next(c for c in payload["checks"] if c["name"] == "policy_budget_duration_guard")
    assert guard["level"] == "warn"
    assert guard["passed"] is True
    assert "session-duration-budget" in guard["detail"]
    assert "max_duration_min" in guard["detail"]


def test_doctor_does_not_warn_on_a_generous_or_scoped_duration_budget(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    _write_policies_yaml(
        tmp_path,
        """
rules:
  - id: generous-duration
    description: "fenetre large"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "fenetre depassee"
    per_session: {max_duration_min: 2880}
  - id: scoped-duration
    description: "fenetre ciblee"
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: block
    reason_template: "fenetre depassee"
    tool_pattern: "Bash(rm:*)"
    per_session: {max_duration_min: 60}
""",
    )
    result = runner.invoke(app, ["doctor", "-o", "json", str(tmp_path)])
    payload = json.loads(result.output)
    names = [c["name"] for c in payload["checks"]]
    assert "policy_budget_duration_guard" not in names
