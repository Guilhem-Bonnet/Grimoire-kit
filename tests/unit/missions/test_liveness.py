"""Vivacité calculée, progression, attention et vue de soi — K5, issue #770.

La vivacité a quatre valeurs et ne conclut jamais « morte » sur une absence :
pas de journal, pas de sonde, pas de bail = ``inconnu``. « Vivant » n'est pas
« avance » : la progression est une dimension à part, qui monte en attention.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions import liveness as lv
from grimoire.missions.lease import LeaseFile
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH, TaskService
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_portfolio as wp

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _lease(*, renewed_min_ago: int = 5, busy_in_min: int | None = None, ttl: int = 1200) -> LeaseFile:
    busy = (NOW + timedelta(minutes=busy_in_min)).isoformat() if busy_in_min is not None else ""
    return LeaseFile("T-1", 1, (NOW - timedelta(minutes=renewed_min_ago)).isoformat(), ttl, busy)


class TestLiveness:
    def test_no_journal_is_unknown_not_dead(self) -> None:
        state = lv.liveness(now=NOW)
        assert state.state == lv.INCONNU
        assert state.state not in (lv.TERMINE, lv.INACTIF)

    def test_old_journal_without_other_sign_is_unknown_not_dead(self) -> None:
        state = lv.liveness(now=NOW, journal_at=NOW - timedelta(hours=6), lease=_lease(renewed_min_ago=400))
        assert state.state == lv.INCONNU
        assert state.since == (NOW - timedelta(hours=6)).isoformat(), "since = dernier indice connu"

    def test_recent_journal_is_alive(self) -> None:
        assert lv.liveness(now=NOW, journal_at=NOW - timedelta(minutes=2)).state == lv.VIVANT

    def test_long_tool_under_a_valid_lease_is_inactive_not_unknown(self) -> None:
        state = lv.liveness(now=NOW, journal_at=NOW - timedelta(minutes=40), lease=_lease(busy_in_min=30))
        assert state.state == lv.INACTIF
        assert "outil long" in state.reason

    def test_long_tool_declaration_without_a_valid_lease_proves_nothing(self) -> None:
        lost = LeaseFile("T-1", 1, (NOW - timedelta(hours=3)).isoformat(), 1200, "", holder=False)
        assert lv.liveness(now=NOW, lease=lost).state == lv.INCONNU

    def test_host_probe_busy_is_alive_idle_is_inactive(self) -> None:
        assert lv.liveness(now=NOW, probe_status="busy").state == lv.VIVANT
        assert lv.liveness(now=NOW, probe_status="idle").state == lv.INACTIF

    def test_ended_session_is_terminated(self) -> None:
        assert lv.liveness(now=NOW, ended=True, journal_at=NOW).state == lv.TERMINE


class TestProgressAndAttention:
    def test_progress_at_is_the_latest_of_the_three_sources(self) -> None:
        a, b, c = NOW - timedelta(hours=3), NOW - timedelta(hours=1), NOW - timedelta(hours=2)
        assert lv.progress_at(a, b, c) == b
        assert lv.progress_at(None, None) is None

    def test_no_progress_raises_attention(self) -> None:
        alive = lv.Liveness(lv.VIVANT)
        warn = lv.attention(alive, NOW - timedelta(minutes=70), now=NOW)
        assert warn is not None and warn.level == 2
        assert "sans progression depuis 70 min" in warn.reason

    def test_recent_progress_raises_nothing(self) -> None:
        assert lv.attention(lv.Liveness(lv.VIVANT), NOW - timedelta(minutes=10), now=NOW) is None

    def test_alive_without_any_progress_counts_from_the_claim(self) -> None:
        alive = lv.Liveness(lv.VIVANT)
        assert lv.attention(alive, None, now=NOW, since_work=NOW - timedelta(minutes=50)) is not None
        assert lv.attention(alive, None, now=NOW, since_work=NOW - timedelta(minutes=5)) is None

    def test_prolonged_unknown_raises_attention(self) -> None:
        since = (NOW - timedelta(minutes=41)).isoformat()
        warn = lv.attention(lv.Liveness(lv.INCONNU, "", since), None, now=NOW, ttl_seconds=1200)
        assert warn is not None and warn.level == 2 and "inconnue depuis" in warn.reason

    def test_short_unknown_is_not_yet_an_alert(self) -> None:
        since = (NOW - timedelta(minutes=39)).isoformat()
        assert lv.attention(lv.Liveness(lv.INCONNU, "", since), None, now=NOW, ttl_seconds=1200) is None

    def test_inactive_never_raises_attention(self) -> None:
        assert lv.attention(lv.Liveness(lv.INACTIF, "outil"), NOW - timedelta(hours=5), now=NOW) is None


class TestProbe:
    @staticmethod
    def _run(stdout: str, code: int = 0) -> Any:
        def run(*_a: Any, **_k: Any) -> Any:
            return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")

        return run

    def test_parses_interactive_sessions(self) -> None:
        payload = json.dumps([{"sessionId": "s1", "status": "busy"}, {"sessionId": "s2", "status": "idle"}, {"x": 1}])
        assert lv.probe_claude_agents(run=self._run(payload)) == {"s1": "busy", "s2": "idle"}

    @pytest.mark.parametrize("stdout,code", [("not json", 0), ("{}", 0), ("[]", 1)])
    def test_a_mute_probe_is_none_not_an_empty_fleet(self, stdout: str, code: int) -> None:
        got = lv.probe_claude_agents(run=self._run(stdout, code))
        assert got is None

    def test_missing_binary_is_none(self) -> None:
        def run(*_a: Any, **_k: Any) -> Any:
            raise FileNotFoundError("claude")

        assert lv.probe_claude_agents(run=run) is None


SESSION = "efd17015-0000-4000-8000-000000000001"


def _fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, journal_age: timedelta | None) -> tuple[Path, str]:
    """Un projet gouverné, une tâche réclamée par ``SESSION`` ; journal de session d'âge donné, ou absent."""
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    ledger = TaskService(root, DEFAULT_LEDGER_RELPATH).ledger
    mission = ledger.create_mission(title="M", origin="t", created_by="t")
    task = ledger.create_task(mission.id, "Tâche vivacité", acceptance=("critère",), owner="winston")
    ledger.transition_task(task.id, TaskState.READY, actor_id="t")
    ledger.claim_task(task.id, "claude", "host-claude-code-cli")
    ledger.attach_session(task.id, SESSION, session_host="host-claude-code-cli")
    if journal_age is not None:
        runs = root / "_grimoire-output" / ".runs"
        runs.mkdir(parents=True)
        at = (datetime.now(UTC) - journal_age).isoformat()
        (runs / f"session-{SESSION}.json").write_text(
            json.dumps({"session_id": SESSION, "updated_at": at, "started_at": at, "task_id": task.id}), encoding="utf-8"
        )
    reg.save_registry([{"slug": "proj", "name": "Proj", "path": str(root)}])
    return root, task.id


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


class TestPortfolioJson:
    def test_no_live_boolean_anywhere_in_the_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fleet(tmp_path, monkeypatch, journal_age=timedelta(minutes=1))
        view = wp.portfolio_tasks(tmp_path, live=True)
        assert "live" not in _all_keys(view)
        assert view["tasks"][0]["session"]["liveness"]["state"] == "vivant"
        assert view["filters"]["liveness"] == "vivant"

    def test_a_claim_without_journal_is_unknown_in_the_portfolio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fleet(tmp_path, monkeypatch, journal_age=None)
        session = wp.portfolio_tasks(tmp_path)["tasks"][0]["session"]
        assert session["liveness"]["state"] == "inconnu"

    def test_the_host_probe_feeds_the_card_and_is_called_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fleet(tmp_path, monkeypatch, journal_age=timedelta(hours=5))
        calls: list[int] = []

        def probe() -> dict[str, str]:
            calls.append(1)
            return {SESSION: "busy"}

        view = wp.portfolio_tasks(tmp_path, probe=probe)
        assert view["tasks"][0]["session"]["liveness"]["state"] == "vivant"
        assert len(calls) == 1

    def test_a_living_session_without_progress_raises_attention_on_the_card(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fleet(tmp_path, monkeypatch, journal_age=timedelta(minutes=1))
        later = datetime.now(UTC) + timedelta(minutes=70)
        view = wp.portfolio_tasks(tmp_path, now=later, live_minutes=600)
        session = view["tasks"][0]["session"]
        assert session["liveness"]["state"] == "vivant"
        assert session["attention"]["level"] == 2
        assert "sans progression" in session["attention"]["reason"]
        assert view["summary"]["attention"] == 1


class TestSelfViewLine:
    def test_the_line_is_present_at_user_prompt_submit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, task_id = _fleet(tmp_path, monkeypatch, journal_age=None)
        payload = {"session_id": SESSION, "hook_event_name": "UserPromptSubmit", "cwd": str(root), "prompt": "salut"}
        rendered, _, _ = run_hook(
            payload, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.USER_PROMPT_SUBMIT, project_root=root
        )
        context = rendered["hookSpecificOutput"]["additionalContext"]
        assert f"Vue de soi — {task_id}" in context
        assert "bail epoch" in context and "vivacité vivant" in context

    def test_a_taken_over_lease_is_announced_to_the_old_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from grimoire.hosts.decisions.self_view import self_view_context
        from grimoire.missions.lease import lease_path, write_lease

        root, task_id = _fleet(tmp_path, monkeypatch, journal_age=None)
        ledger = TaskService(root, DEFAULT_LEDGER_RELPATH).ledger
        epoch = ledger.get_task(task_id).claim.lease_epoch  # type: ignore[union-attr]
        write_lease(
            lease_path(root / "_grimoire-runtime-output/ledger", task_id),
            LeaseFile(task_id, epoch + 1, datetime.now(UTC).isoformat(), holder=False),
        )
        line = self_view_context(root, task_id, SESSION)
        assert "reprise" in line and "PERDU" in line
