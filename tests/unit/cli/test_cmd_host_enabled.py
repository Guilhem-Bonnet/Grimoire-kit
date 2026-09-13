"""`grimoire host sync`/`status` respect `hosts.enabled` (issue #177, petite version).

A fresh `grimoire init` declares `hosts.enabled: ["claude"]` when nothing else
is detected (see `tests/unit/test_hosts_detection.py` and
`tests/test_scaffold_copilot.py` for the detection/scaffold side). These
tests cover the CLI-facing consequences: `sync --host all` only writes
enabled hosts, an explicit disabled `--host` is a named refusal unless
`--force-host`, and `--prune-disabled`/`status` handle the resulting
orphans.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


class TestFreshProjectOnlyGetsClaude:
    def test_stop_criterion_no_gemini_no_cursor(self, cli_project: Path) -> None:
        """Critère d'arrêt de l'issue #177 : un projet Claude-only ne reçoit
        pas le catalogue Gemini/Cursor/Codex au premier `init`."""
        assert (cli_project / ".claude").is_dir()
        assert not (cli_project / "GEMINI.md").is_file()
        assert not (cli_project / ".cursor").exists()
        agents_dir = cli_project / ".github" / "agents"
        assert not agents_dir.is_dir() or not list(agents_dir.glob("*.agent.md"))

    def test_hosts_enabled_written_by_init(self, cli_project: Path) -> None:
        content = (cli_project / "project-context.yaml").read_text(encoding="utf-8")
        assert "hosts:" in content
        assert "claude" in content


class TestSyncRefusesDisabledHost:
    def test_explicit_disabled_host_is_refused(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["host", "sync", "--host", "gemini", "--project-root", str(cli_project)])
        assert result.exit_code == 1
        assert "non activé" in result.output
        assert not (cli_project / "GEMINI.md").is_file()

    def test_force_host_bypasses_the_refusal(self, cli_project: Path) -> None:
        result = runner.invoke(
            app, ["host", "sync", "--host", "gemini", "--force-host", "--project-root", str(cli_project)]
        )
        assert result.exit_code == 0
        assert (cli_project / "GEMINI.md").is_file()

    def test_enabled_host_is_not_refused(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["host", "sync", "--host", "claude", "--project-root", str(cli_project)])
        assert result.exit_code == 0

    def test_sync_all_never_touches_a_disabled_host(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["host", "sync", "--host", "all", "--project-root", str(cli_project)])
        assert result.exit_code == 0
        assert not (cli_project / "GEMINI.md").is_file()
        assert not (cli_project / ".cursor").exists()


class TestPruneDisabled:
    def test_prune_disabled_removes_a_kit_owned_orphan(self, cli_project: Path) -> None:
        # Force-write the (disabled) Gemini surface, then disable-and-prune it.
        forced = runner.invoke(
            app, ["host", "sync", "--host", "gemini", "--force-host", "--project-root", str(cli_project)]
        )
        assert forced.exit_code == 0
        assert (cli_project / "GEMINI.md").is_file()

        result = runner.invoke(
            app, ["host", "sync", "--host", "all", "--prune-disabled", "--project-root", str(cli_project)]
        )
        assert result.exit_code == 0
        assert not (cli_project / "GEMINI.md").is_file()

    def test_prune_disabled_never_touches_a_hand_written_file(self, cli_project: Path) -> None:
        (cli_project / "GEMINI.md").write_text("# mine, not the kit's\n", encoding="utf-8")

        result = runner.invoke(
            app, ["host", "sync", "--host", "all", "--prune-disabled", "--project-root", str(cli_project)]
        )
        assert result.exit_code == 0
        assert (cli_project / "GEMINI.md").read_text(encoding="utf-8") == "# mine, not the kit's\n"

    def test_dry_run_prune_reports_without_deleting(self, cli_project: Path) -> None:
        runner.invoke(app, ["host", "sync", "--host", "gemini", "--force-host", "--project-root", str(cli_project)])
        result = runner.invoke(
            app,
            ["host", "sync", "--host", "all", "--prune-disabled", "--dry-run", "--project-root", str(cli_project)],
        )
        assert result.exit_code == 0
        assert (cli_project / "GEMINI.md").is_file()


class TestStatusListsOrphans:
    def test_status_all_lists_orphan_files_in_json(self, cli_project: Path) -> None:
        runner.invoke(app, ["host", "sync", "--host", "gemini", "--force-host", "--project-root", str(cli_project)])

        result = runner.invoke(
            app, ["-o", "json", "host", "status", "--host", "all", "--project-root", str(cli_project)]
        )
        payload = json.loads(result.stdout)
        assert "orphans" in payload
        gemini_orphans = next((o for o in payload["orphans"] if o["host"] == "host-gemini-cli"), None)
        assert gemini_orphans is not None
        assert "GEMINI.md" in gemini_orphans["files"]

    def test_status_all_reports_no_orphans_when_nothing_disabled_was_ever_written(self, cli_project: Path) -> None:
        result = runner.invoke(
            app, ["-o", "json", "host", "status", "--host", "all", "--project-root", str(cli_project)]
        )
        payload = json.loads(result.stdout)
        assert payload["orphans"] == []

    def test_status_single_host_json_shape_is_unchanged(self, cli_project: Path) -> None:
        """Un `--host claude` explicite garde la forme JSON historique (liste
        nue) — seul `--host all` gagne la clé `orphans` (issue #177)."""
        result = runner.invoke(
            app, ["-o", "json", "host", "status", "--host", "claude", "--project-root", str(cli_project)]
        )
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
