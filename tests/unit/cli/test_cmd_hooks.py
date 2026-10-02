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


class TestHooksStaleness:
    """Un hook installé par une version antérieure continue de tourner en
    silence après une mise à jour. Le signaler est le seul moyen de ne pas
    présenter comme sain un cycle agent qui ne correspond plus à ce qui est
    livré."""

    def test_outdated_hook_is_reported_stale(self, kit_repo: Path) -> None:
        runner.invoke(app, ["hooks", "install", str(kit_repo)])
        # La source évolue (nouvelle version du kit), l'installé reste l'ancien.
        source = kit_repo / "framework" / "hooks" / "pre-push.sh"
        source.write_text(
            "#!/usr/bin/env bash\n# Grimoire hook pre-push.sh\n# nouvelle garde\nexit 0\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["-o", "json", "hooks", "list", str(kit_repo)])
        states = {h["name"]: h["state"] for h in json.loads(result.stdout)["hooks"]}
        assert states["pre-push"] == "stale"
        assert states["commit-msg"] == "installed"

    def test_status_fails_when_a_hook_is_stale(self, kit_repo: Path) -> None:
        runner.invoke(app, ["hooks", "install", str(kit_repo)])
        assert runner.invoke(app, ["hooks", "status", str(kit_repo)]).exit_code == 0

        (kit_repo / "framework" / "hooks" / "commit-msg.sh").write_text(
            "#!/usr/bin/env bash\n# Grimoire hook commit-msg.sh\n# v2\nexit 0\n", encoding="utf-8",
        )

        result = runner.invoke(app, ["-o", "json", "hooks", "status", str(kit_repo)])
        assert result.exit_code == 1, "un hook obsolète ne doit pas passer pour installé"
        payload = json.loads(result.stdout)
        assert payload["stale"] == 1
        assert payload["installed"] == payload["total"] - 1

    def test_install_refreshes_a_stale_hook(self, kit_repo: Path) -> None:
        runner.invoke(app, ["hooks", "install", str(kit_repo)])
        (kit_repo / "framework" / "hooks" / "pre-push.sh").write_text(
            "#!/usr/bin/env bash\n# Grimoire hook pre-push.sh\n# v2\nexit 0\n", encoding="utf-8",
        )
        runner.invoke(app, ["hooks", "install", str(kit_repo)])

        result = runner.invoke(app, ["-o", "json", "hooks", "status", str(kit_repo)])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["stale"] == 0

    def test_mnemo_suffix_is_not_mistaken_for_drift(self, kit_repo: Path) -> None:
        """`install` ajoute le bloc mnemo à pre-commit — c'est attendu."""
        runner.invoke(app, ["hooks", "install", str(kit_repo)])
        content = (kit_repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        assert "mnemo-consolidate.sh" in content

        result = runner.invoke(app, ["-o", "json", "hooks", "list", str(kit_repo)])
        states = {h["name"]: h["state"] for h in json.loads(result.stdout)["hooks"]}
        assert states["pre-commit"] == "installed"


class TestHooksCalibrate:
    """``grimoire hooks calibrate`` (Refs #644, party-mode idée B)."""

    def test_no_journal_reports_an_empty_calibration(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "hooks", "calibrate", "--project-root", str(tmp_path)])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"groups": []}

    def test_calibrate_reports_holds_by_hook_and_reason(self, tmp_path: Path) -> None:
        from grimoire.hosts.decisions.calibration import record_hold_followup, record_policy_hold

        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:deny", fingerprint="fp1", target="bash:rm",
        )
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp1", target="bash:rm")

        result = runner.invoke(app, ["-o", "json", "hooks", "calibrate", "--project-root", str(tmp_path)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == {
            "groups": [
                {
                    "hook": "grimoire.tool-policy",
                    "reason": "tool_policy:deny",
                    "total": 1,
                    "labels": {"respected": 0, "retried_same": 1, "retried_variant": 0, "abandoned": 0},
                }
            ]
        }

    def test_calibrate_renders_a_table_in_text_mode(self, tmp_path: Path) -> None:
        from grimoire.hosts.decisions.calibration import record_policy_hold

        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.evidence-gate",
            reason="done_gate:stale", fingerprint="fp1", target="done_gate:t1",
        )
        result = runner.invoke(app, ["hooks", "calibrate", "--project-root", str(tmp_path)])
        assert result.exit_code == 0
        # ``console = Console(stderr=True)`` (comme ``cmd_dispatch``) : la table
        # texte va sur stderr, jamais sur le stdout que le mode ``--json`` réserve.
        assert "grimoire.evidence-gate" in result.stderr
        assert "done_gate:stale" in result.stderr

    def test_calibrate_since_rejects_a_malformed_value(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["hooks", "calibrate", "--project-root", str(tmp_path), "--since", "3weeks"])
        assert result.exit_code == 2

    def test_calibrate_since_excludes_an_older_hold(self, tmp_path: Path) -> None:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.hosts.decisions.calibration import record_policy_hold

        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        # Backdate the just-written hold well outside any `--since` window.
        traces_file = tmp_path / TRACES_DIR / "traces.jsonl"
        import json as _json

        lines = traces_file.read_text(encoding="utf-8").splitlines()
        record = _json.loads(lines[0])
        record["started_at"] = "2020-01-01T00:00:00+00:00"
        traces_file.write_text(_json.dumps(record) + "\n", encoding="utf-8")

        result = runner.invoke(
            app, ["-o", "json", "hooks", "calibrate", "--project-root", str(tmp_path), "--since", "7d"]
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"groups": []}
