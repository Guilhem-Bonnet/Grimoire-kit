"""La surface `grimoire workflows` — ce qu'elle expose, et ce qu'elle exposait.

Elle listait sept commandes d'hygiène et taisait six workflows d'orchestration
installés dans le projet. Le groupe n'avait aucun test : c'est ce qui a permis
au catalogue de ne montrer que la moitié de ce que le kit livre.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core import layout

runner = CliRunner()


def _installed(project: Path, name: str, body: str) -> Path:
    path = project / layout.KIT_DIR / layout.WORKFLOWS_SUBDIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return tmp_path


class TestList:
    def test_the_orchestrations_are_listed(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "list", str(project)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        slugs = {w["slug"] for w in data["workflows"] if w["kind"] == "orchestration"}
        assert {"party-mode", "boomerang-orchestration", "subagent-orchestration"} <= slugs

    def test_the_count_separates_the_two_kinds(self, project: Path) -> None:
        data = json.loads(runner.invoke(app, ["-o", "json", "workflows", "list", str(project)]).output)

        assert data["counts_by_kind"]["orchestration"] >= 6
        assert data["counts_by_kind"]["command"] >= 7

    def test_kind_filters(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "list", str(project), "--kind", "orchestration"])

        data = json.loads(result.output)
        assert data["workflows"]
        assert all(w["kind"] == "orchestration" for w in data["workflows"])

    def test_an_unknown_kind_yields_nothing_rather_than_everything(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "list", str(project), "-k", "sorcellerie"])

        assert json.loads(result.output)["count"] == 0

    def test_the_text_table_names_the_agents(self, project: Path) -> None:
        result = runner.invoke(app, ["workflows", "list", str(project), "-k", "orchestration"])

        assert result.exit_code == 0
        assert "orchestration" in result.output


class TestShow:
    def test_show_renders_the_declared_team(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "show", "boomerang-orchestration", str(project)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["team"] == "team-build"
        assert data["team_manifest"]["handoff_to"] == "team-ops"
        assert "dev" in data["team_manifest"]["required_agents"]

    def test_show_reports_a_team_that_is_not_installed(self, project: Path) -> None:
        _installed(
            project, "orpheline.md",
            "---\nkind: orchestration\ndescription: 'x'\nteam: team-fantome\n---\n\nCorps.\n",
        )
        result = runner.invoke(app, ["workflows", "show", "orpheline", str(project)])

        assert result.exit_code == 0
        assert "team-fantome" in result.output

    def test_show_finds_an_orchestration_not_just_a_prompt(self, project: Path) -> None:
        """Elle résolvait en `<slug>.prompt.md` : une orchestration ne pouvait pas être trouvée."""
        result = runner.invoke(app, ["workflows", "show", "party-mode", str(project)])

        assert result.exit_code == 0

    def test_an_unknown_workflow_exits_non_zero(self, project: Path) -> None:
        result = runner.invoke(app, ["workflows", "show", "workflow-imaginaire", str(project)])

        assert result.exit_code == 1


class TestTeams:
    def test_teams_lists_the_chain(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "teams", str(project)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        chain = {t["name"]: t["handoff_to"] for t in data["teams"]}
        assert chain["team-vision"] == "team-build"
        assert chain["team-build"] == "team-ops"


class TestInstall:
    def test_an_orchestration_lands_in_the_kit_workflows_dir(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "install", "party-mode", str(project)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["kind"] == "orchestration"
        assert (project / layout.KIT_DIR / layout.WORKFLOWS_SUBDIR / "party-mode.md").is_file()

    def test_a_command_still_lands_in_the_prompts_dir(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "install", "grimoire-status", str(project)])

        assert result.exit_code == 0
        assert (project / ".github" / "prompts" / "grimoire-status.prompt.md").is_file()

    def test_installing_an_unknown_workflow_exits_non_zero(self, project: Path) -> None:
        result = runner.invoke(app, ["workflows", "install", "workflow-imaginaire", str(project)])

        assert result.exit_code == 1

    def test_dry_run_writes_nothing(self, project: Path) -> None:
        result = runner.invoke(
            app, ["-o", "json", "workflows", "install", "party-mode", str(project), "--dry-run"]
        )

        assert result.exit_code == 0
        assert not (project / layout.KIT_DIR / layout.WORKFLOWS_SUBDIR / "party-mode.md").exists()


class TestSearch:
    def test_search_reaches_the_orchestrations(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "workflows", "search", "orchestrateur", str(project)])

        assert result.exit_code == 0
        slugs = {row["slug"] for row in json.loads(result.output)["results"]}
        assert "boomerang-orchestration" in slugs
