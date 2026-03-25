"""Tests for cli/cmd_init.py — enhanced init command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from grimoire.cli.cmd_init import _git_user_name, detect_memory_backend


class TestDetectMemoryBackend:
    def test_returns_local_when_nothing_available(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=OSError("no server")):
            result = detect_memory_backend()
        assert result == "local"

    def test_returns_qdrant_local_when_qdrant_up(self) -> None:
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b""

        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", return_value=FakeResp()):
            result = detect_memory_backend()
        assert result == "qdrant-local"

    def test_returns_local_on_timeout(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = detect_memory_backend()
        assert result == "local"


class TestGitUserName:
    def test_returns_name_on_success(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Test User\n"
            result = _git_user_name()
        assert result == "Test User"

    def test_returns_empty_on_failure(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            result = _git_user_name()
        assert result == ""

    def test_returns_empty_on_exception(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run", side_effect=FileNotFoundError("no git")):
            result = _git_user_name()
        assert result == ""


class TestInitCLI:
    """CLI integration tests using typer CliRunner."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    def test_init_dry_run(self, runner, app, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-y", "init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower() or "mkdir" in result.output.lower()

    def test_init_express_creates_project(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "my-project"
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 0
        assert (target / "project-context.yaml").is_file()
        assert (target / "_grimoire" / "_config" / "custom" / "agents").is_dir()

    def test_init_express_sets_project_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "cool-app"
        runner.invoke(app, ["-y", "init", str(target)])
        content = (target / "project-context.yaml").read_text()
        assert "cool-app" in content

    def test_init_with_explicit_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "dir"
        runner.invoke(app, ["-y", "init", str(target), "--name", "MyApp"])
        content = (target / "project-context.yaml").read_text()
        assert "MyApp" in content

    def test_init_with_explicit_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "infra"
        runner.invoke(app, ["-y", "init", str(target), "--archetype", "infra-ops"])
        content = (target / "project-context.yaml").read_text()
        assert "infra-ops" in content
        # Should have infra-ops agents
        agents_dir = target / "_grimoire" / "_config" / "custom" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        assert "ops-engineer" in agent_names

    def test_init_refuses_existing_without_force(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        (target / "project-context.yaml").write_text("existing")
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 1

    def test_init_force_overwrites(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "overwrite"
        target.mkdir()
        (target / "project-context.yaml").write_text("old")
        result = runner.invoke(app, ["-y", "init", str(target), "--force"])
        assert result.exit_code == 0
        content = (target / "project-context.yaml").read_text()
        assert "old" not in content

    def test_init_json_output(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "json-proj"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["project"] == "json-proj"
        assert "agents" in data

    def test_init_invalid_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-arch"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "nonexistent"])
        assert result.exit_code == 1

    def test_init_invalid_backend(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-backend"
        result = runner.invoke(app, ["-y", "init", str(target), "--backend", "redis"])
        assert result.exit_code == 1

    def test_init_deploys_meta_agents(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "meta-test"
        runner.invoke(app, ["-y", "init", str(target)])
        agents_dir = target / "_grimoire" / "_config" / "custom" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        assert "project-navigator" in agent_names
        assert "memory-keeper" in agent_names
        assert "agent-optimizer" in agent_names

    def test_init_creates_session_branch(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "session-test"
        runner.invoke(app, ["-y", "init", str(target)])
        branch_file = target / "_grimoire-output" / ".runs" / "main" / "branch.json"
        assert branch_file.is_file()

    # ── Batch 3 — Enriched dry-run and JSON output ──────────────

    def test_dry_run_shows_agent_categories(self, runner, app, tmp_path: Path) -> None:
        """Dry-run should display agents grouped by category."""
        target = tmp_path / "dry-cats"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run"])
        assert result.exit_code == 0
        assert "meta" in result.output.lower()

    def test_dry_run_shows_gitignore_patterns(self, runner, app, tmp_path: Path) -> None:
        """Dry-run should preview .gitignore patterns."""
        target = tmp_path / "dry-gi"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run"])
        assert result.exit_code == 0
        assert "_grimoire-output/.runs/" in result.output

    def test_dry_run_infra_shows_dna(self, runner, app, tmp_path: Path) -> None:
        """Dry-run for infra-ops should show DNA traits."""
        target = tmp_path / "dry-dna"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run", "--archetype", "infra-ops"])
        assert result.exit_code == 0
        assert "Archetype DNA" in result.output

    def test_json_output_agents_categorized(self, runner, app, tmp_path: Path) -> None:
        """JSON output should have agents.by_category breakdown."""
        import json
        target = tmp_path / "json-cats"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "by_category" in data["agents"]
        assert "meta" in data["agents"]["by_category"]

    def test_init_deploys_archetype_dna(self, runner, app, tmp_path: Path) -> None:
        """Init with infra-ops should deploy archetype.dna.yaml."""
        target = tmp_path / "dna-test"
        runner.invoke(app, ["-y", "init", str(target), "--archetype", "infra-ops"])
        dna = target / "_grimoire" / "_config" / "archetype.dna.yaml"
        assert dna.is_file()

    def test_init_creates_gitignore(self, runner, app, tmp_path: Path) -> None:
        """Init should generate .gitignore with grimoire patterns."""
        target = tmp_path / "gi-test"
        runner.invoke(app, ["-y", "init", str(target)])
        gi = target / ".gitignore"
        assert gi.is_file()
        content = gi.read_text()
        assert "Grimoire Kit" in content

    # ── Batch 4 — Multi-archetype ──────────────────────────────

    def test_init_multi_archetype_comma_separated(self, runner, app, tmp_path: Path) -> None:
        """CLI --archetype supports comma-separated values."""
        target = tmp_path / "multi-arch"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "web-app,infra-ops"])
        assert result.exit_code == 0
        # Should have DNA for primary archetype at least
        dna = target / "_grimoire" / "_config" / "archetype.dna.yaml"
        assert dna.is_file()

    def test_init_multi_archetype_json_output(self, runner, app, tmp_path: Path) -> None:
        """JSON output should contain archetypes list."""
        import json
        target = tmp_path / "multi-json"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target), "--archetype", "web-app,fix-loop"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archetypes" in data
        assert "web-app" in data["archetypes"]
        assert "fix-loop" in data["archetypes"]

    def test_init_multi_archetype_dry_run(self, runner, app, tmp_path: Path) -> None:
        """Dry-run with multiple archetypes shows composite info."""
        target = tmp_path / "multi-dry"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run", "--archetype", "infra-ops,fix-loop"])
        assert result.exit_code == 0


class TestParseArchetypeSelection:
    """Tests for _parse_archetype_selection helper."""

    def test_all_returns_all_keys(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("all")
        assert result == list(_ARCHETYPE_KEYS)

    def test_none_returns_minimal(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        assert _parse_archetype_selection("none") == ["minimal"]

    def test_empty_returns_minimal(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        assert _parse_archetype_selection("") == ["minimal"]

    def test_single_number(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_multiple_numbers(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,3,5")
        assert result == [_ARCHETYPE_KEYS[0], _ARCHETYPE_KEYS[2], _ARCHETYPE_KEYS[4]]

    def test_numbers_with_spaces(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1 3 5")
        assert result == [_ARCHETYPE_KEYS[0], _ARCHETYPE_KEYS[2], _ARCHETYPE_KEYS[4]]

    def test_deduplication(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,1,1")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_archetype_names_directly(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        result = _parse_archetype_selection("web-app")
        assert result == ["web-app"]

    def test_out_of_range_ignored(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,99")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_zero_triggers_guided(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        # "0" calls _guided_discovery which needs user input — for unit test, we mock
        from unittest.mock import patch
        with patch("grimoire.cli.cmd_init._guided_discovery", return_value=["web-app"]):
            result = _parse_archetype_selection("0")
        assert result == ["web-app"]
