"""CLI wiring of the Claude Code activation hook (grimoire standard)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


class TestActivationContext:
    def test_prints_builtin_directive(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "activation-context", str(tmp_path)])
        assert result.exit_code == 0
        assert "[Grimoire Standard — activation]" in result.output
        assert "gate check --task-id bootstrap --strict" in result.output

    def test_prefers_project_file(self, tmp_path: Path) -> None:
        context = tmp_path / ".claude" / "activation-context.md"
        context.parent.mkdir(parents=True)
        context.write_text("directive maison\n", encoding="utf-8")
        result = runner.invoke(app, ["standard", "activation-context", str(tmp_path)])
        assert result.exit_code == 0
        assert result.output == "directive maison\n"


class TestStandardInitClaudeHook:
    def test_init_installs_hook_by_default(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path)])
        assert result.exit_code == 0
        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        commands = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            for hook in entry["hooks"]
        ]
        assert "grimoire standard activation-context" in commands
        assert (tmp_path / ".claude" / "activation-context.md").is_file()

    def test_no_claude_hook_opt_out(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path), "--no-claude-hook"])
        assert result.exit_code == 0
        assert not (tmp_path / ".claude").exists()

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert not (tmp_path / ".claude").exists()

    def test_json_output_reports_activation(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["--output", "json", "standard", "init", str(tmp_path)]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["claude_activation"]["status"] == "installed"
        assert ".claude/settings.json" in payload["claude_activation"]["written"]
