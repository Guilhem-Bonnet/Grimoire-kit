"""Tests for grimoire.cli.cmd_hooks — git hooks install/list/status."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


@pytest.fixture()
def kit_repo(tmp_path: Path) -> Path:
    """A git repo mimicking the kit layout (framework/hooks present)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hooks_src = tmp_path / "framework" / "hooks"
    hooks_src.mkdir(parents=True)
    for name in (
        "pre-commit-cc.sh", "post-checkout.sh", "prepare-commit-msg.sh",
        "commit-msg.sh", "post-commit.sh", "pre-push.sh", "mnemo-consolidate.sh",
    ):
        (hooks_src / name).write_text(f"#!/usr/bin/env bash\n# Grimoire hook {name}\nexit 0\n", encoding="utf-8")
    (hooks_src / ".pre-commit-config.tpl.yaml").write_text("repos: []\n", encoding="utf-8")
    return tmp_path


class TestHooksInstall:
    def test_installs_all_hooks(self, kit_repo: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "hooks", "install", str(kit_repo)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert sorted(payload["installed"]) == sorted([
            "pre-commit", "post-checkout", "prepare-commit-msg",
            "commit-msg", "post-commit", "pre-push",
        ])
        hook = kit_repo / ".git" / "hooks" / "pre-commit"
        assert hook.is_file()
        assert hook.stat().st_mode & 0o111
        assert payload["precommit_config_written"] is True
        assert (kit_repo / ".pre-commit-config.yaml").is_file()

    def test_injects_mnemo_into_grimoire_precommit(self, kit_repo: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "hooks", "install", str(kit_repo)])
        payload = json.loads(result.stdout)
        assert payload["mnemo_injected"] is True
        content = (kit_repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        assert "mnemo-consolidate.sh" in content

    def test_preserves_third_party_precommit(self, kit_repo: Path) -> None:
        third_party = kit_repo / ".git" / "hooks" / "pre-commit"
        third_party.parent.mkdir(parents=True, exist_ok=True)
        third_party.write_text("#!/bin/sh\n# husky\nexit 0\n", encoding="utf-8")
        result = runner.invoke(app, ["-o", "json", "hooks", "install", str(kit_repo)])
        payload = json.loads(result.stdout)
        assert "pre-commit" in payload["chained"]
        assert "husky" in third_party.read_text(encoding="utf-8")
        assert (kit_repo / ".git" / ".git-hooks-precommit" / "grimoire-pre-commit.sh").is_file()

    def test_force_overwrites_third_party(self, kit_repo: Path) -> None:
        third_party = kit_repo / ".git" / "hooks" / "pre-commit"
        third_party.parent.mkdir(parents=True, exist_ok=True)
        third_party.write_text("#!/bin/sh\n# husky\nexit 0\n", encoding="utf-8")
        result = runner.invoke(app, ["-o", "json", "hooks", "install", str(kit_repo), "--force"])
        payload = json.loads(result.stdout)
        assert "pre-commit" in payload["installed"]
        assert "Grimoire" in third_party.read_text(encoding="utf-8")

    def test_single_hook(self, kit_repo: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "hooks", "install", str(kit_repo), "--hook", "pre-push"])
        payload = json.loads(result.stdout)
        assert payload["installed"] == ["pre-push"]
        assert not (kit_repo / ".git" / "hooks" / "commit-msg").exists()

    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        result = runner.invoke(app, ["hooks", "install", str(plain)])
        assert result.exit_code == 1


class TestHooksStatusList:
    def test_status_incomplete_then_complete(self, kit_repo: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "hooks", "status", str(kit_repo)])
        assert result.exit_code == 1
        runner.invoke(app, ["hooks", "install", str(kit_repo)])
        result = runner.invoke(app, ["-o", "json", "hooks", "status", str(kit_repo)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["installed"] == payload["total"]

    def test_list_reports_states(self, kit_repo: Path) -> None:
        runner.invoke(app, ["hooks", "install", str(kit_repo), "--hook", "pre-push"])
        result = runner.invoke(app, ["-o", "json", "hooks", "list", str(kit_repo)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        states = {h["name"]: h["state"] for h in payload["hooks"]}
        assert states["pre-push"] == "installed"
        assert states["commit-msg"] == "missing"
