"""Tests for ``grimoire debugger`` CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


@dataclass
class _FakePlan:
    title: str = "Plan"
    tasks: list[dict] = field(default_factory=lambda: [{"title": "T1"}])


@dataclass
class _FakeSnapshot:
    claims: list[object] = field(default_factory=lambda: [type("Claim", (), {"__dict__": {"claim": "c", "verdict": "supported"}})()])
    plan: _FakePlan = field(default_factory=_FakePlan)

    def to_dict(self):
        return {
            "claims": [{"claim": "c", "verdict": "supported"}],
            "capabilities": [],
            "score": {"total": 80},
            "plan": {"title": "Plan", "tasks": [{"title": "T1"}]},
        }


class _FakeModule:
    def build_snapshot(self, root: Path):
        return _FakeSnapshot()

    def print_status(self, snapshot: _FakeSnapshot) -> None:
        print("status-ok")

    def print_claims(self, snapshot: _FakeSnapshot) -> None:
        print("claims-ok")

    def print_plan(self, snapshot: _FakeSnapshot) -> None:
        print("plan-ok")

    def write_dashboard(self, root: Path, output: Path | None = None) -> Path:
        target = output or (root / "dashboard.html")
        target.write_text("<html></html>", encoding="utf-8")
        return target

    def serve_dashboard(self, root: Path, output: Path | None, port: int, open_browser: bool) -> int:
        return 0


def _patch_debugger():
    return patch("grimoire.cli.cmd_debugger._load_debugger_module", return_value=_FakeModule())


def _patch_project_root(tmp_path: Path):
    return patch("grimoire.cli.cmd_debugger._project_root", return_value=tmp_path)


class TestDebuggerCli:
    def test_status_text(self, tmp_path: Path) -> None:
        with _patch_debugger(), _patch_project_root(tmp_path):
            result = runner.invoke(app, ["debugger", "status"])
        assert result.exit_code == 0
        assert "status-ok" in result.output

    def test_claims_json(self, tmp_path: Path) -> None:
        with _patch_debugger(), _patch_project_root(tmp_path):
            result = runner.invoke(app, ["-o", "json", "debugger", "claims"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["claim"] == "c"

    def test_plan_text(self, tmp_path: Path) -> None:
        with _patch_debugger(), _patch_project_root(tmp_path):
            result = runner.invoke(app, ["debugger", "plan"])
        assert result.exit_code == 0
        assert "plan-ok" in result.output

    def test_generate_dashboard(self, tmp_path: Path) -> None:
        with _patch_debugger(), _patch_project_root(tmp_path):
            result = runner.invoke(app, ["debugger", "generate"])
        assert result.exit_code == 0
        target = Path(result.output.strip())
        assert target.exists()

    def test_alias_dbg(self, tmp_path: Path) -> None:
        with _patch_debugger(), _patch_project_root(tmp_path):
            result = runner.invoke(app, ["dbg", "status"])
        assert result.exit_code == 0
        assert "status-ok" in result.output