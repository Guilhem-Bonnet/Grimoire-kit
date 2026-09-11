"""Tests for the `agent_freshness` check in `grimoire doctor` (issue #396).

The known agent set is monkeypatched to a fixed ``["concierge",
"security-auditor"]`` so these tests exercise the freshness rule itself
rather than whatever agents the "minimal" archetype happens to ship —
`grimoire.core.agent_freshness` already has its own composition tests via
`grimoire.traces.ledger.compute_agent_freshness` (see `tests/unit/test_traces.py`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.traces.ledger import AGENT_DISPATCH_TAG, TraceLedger, TraceOutcome


def _record_dispatch(project_root: Path, agent_id: str, *, days_ago: int) -> None:
    ledger = TraceLedger(project_root / TRACES_DIR)
    started_at = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    ledger.record(
        run_id=f"RUN-{agent_id}-{days_ago}",
        workflow_instance_id="",
        mission_id="",
        task_id="",
        recipe_id="grimoire.entry-persona",
        outcome=TraceOutcome.SUCCESS,
        started_at=started_at,
        agent_id=agent_id,
        tags=[AGENT_DISPATCH_TAG],
    )


@pytest.fixture(autouse=True)
def _known_agents():
    with patch(
        "grimoire.core.agent_freshness.known_agent_names",
        return_value=["concierge", "security-auditor"],
    ):
        yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestDoctorAgentFreshness:
    """Mirroir du critère d'arrêt de l'issue #396, à la lettre."""

    def test_threshold_90_flags_both_agents(self, runner: CliRunner, init_project: Path) -> None:
        _record_dispatch(init_project, "concierge", days_ago=100)
        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "agent_freshness")
        assert check["level"] == "warn"
        assert check["passed"] is True  # signal seulement, jamais FAIL

        stale = {a["name"]: a for a in check["stale_agents"]}
        assert set(stale) == {"concierge", "security-auditor"}
        assert stale["concierge"]["days_since"] == 100
        assert stale["security-auditor"]["last_seen"] is None

    def test_threshold_200_flags_nothing_insufficient_history(self, runner: CliRunner, init_project: Path) -> None:
        (init_project / "project-context.yaml").write_text(
            'project:\n  name: "test-project"\n'
            'memory:\n  backend: "local"\n'
            'agents:\n  archetype: "minimal"\n  freshness_threshold_days: 200\n',
            encoding="utf-8",
        )
        _record_dispatch(init_project, "concierge", days_ago=100)
        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "agent_freshness")
        assert check["level"] == "info"
        assert "stale_agents" not in check

    def test_empty_journal_is_info_without_list(self, runner: CliRunner, init_project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "agent_freshness")
        assert check["level"] == "info"
        assert "stale_agents" not in check

    def test_check_never_fails_doctor(self, runner: CliRunner, init_project: Path) -> None:
        """Signal seulement : `agent_freshness` ne fait jamais échouer `doctor`."""
        _record_dispatch(init_project, "concierge", days_ago=100)
        result = runner.invoke(app, ["doctor", str(init_project)])
        assert result.exit_code == 0
        assert "WARN" in result.output
