"""Messages trompeurs relevés au rejeu de la première heure d'un nouvel utilisateur (3.122.0).

Chaque test échoue sans le correctif correspondant : ``up`` ne se contredit plus, une pile est
reconnue à ses sources, le score dit son seuil, ``doctor`` compte ses avertissements, la suggestion
MCP ignore le serveur du kit, l'index lexical est ignoré de git, et l'avis de modèle se comprend.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.env_checks import EnvCheck
from grimoire.core.onboarding_panel import build_next_steps
from grimoire.core.scanner import StackScanner
from grimoire.core.standard_generation import (
    GITIGNORE_MARKER,
    GITIGNORE_SECTION,
    LEXICAL_INDEX_IGNORE,
    ensure_grimoire_gitignore,
)
from grimoire.missions.dispatch_advice import model_hint

runner = CliRunner()


def _flat(output: str) -> str:
    """La sortie Rich coupe les lignes à la largeur du terminal : on compare à blanc normalisé."""
    return re.sub(r"[\s│╭╮╰╯─]+", " ", output)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


@pytest.fixture
def bare_project(tmp_path: Path) -> Path:
    """Le projet du rejeu : un script et un README, aucun fichier de build."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "app.py").write_text('print("hi")\n', encoding="utf-8")
    (target / "README.md").write_text("# x\n", encoding="utf-8")
    return target


# ── 1, 2, 5 : `grimoire up` ─────────────────────────────────────────────────────


class TestUpSaysOneThing:
    def test_up_does_not_announce_governance_as_not_started(self, bare_project: Path) -> None:
        result = runner.invoke(app, ["up", str(bare_project), "--backend", "local"])
        assert result.exit_code == 0, result.output
        out = _flat(result.output)
        assert "profile 'starter'" in out  # le tableau dit que le standard est installé…
        assert "governance not started" not in out  # … le panneau ne dit pas le contraire
        assert "Not yet used: governance/standard" not in out

    def test_up_without_standard_still_says_it_is_not_started(self, bare_project: Path) -> None:
        result = runner.invoke(app, ["up", str(bare_project), "--backend", "local", "--no-standard"])
        assert result.exit_code == 0, result.output
        assert "governance not started" in _flat(result.output)

    def test_a_folder_of_python_files_is_not_reported_as_having_no_stack(self, bare_project: Path) -> None:
        result = runner.invoke(app, ["up", str(bare_project), "--backend", "local"])
        out = _flat(result.output)
        assert "No stack marker detected" not in out
        assert "Python project detected" in out

    def test_up_does_not_suggest_tool_mediation_for_its_own_mcp_config(self, bare_project: Path) -> None:
        result = runner.invoke(app, ["up", str(bare_project), "--backend", "local"])
        assert (bare_project / ".mcp.json").is_file()
        assert "tool-mediation-security" not in result.output

    def test_up_ignores_the_lexical_index_in_git(self, bare_project: Path) -> None:
        runner.invoke(app, ["up", str(bare_project), "--backend", "local"])
        assert LEXICAL_INDEX_IGNORE in (bare_project / ".gitignore").read_text(encoding="utf-8")

    def test_up_adds_the_lexical_rule_to_a_project_installed_before(self, bare_project: Path) -> None:
        old = f"{GITIGNORE_MARKER}\n_grimoire-output/.runs/\n_grimoire-output/memory-reads.jsonl\n"
        (bare_project / ".gitignore").write_text(old, encoding="utf-8")
        runner.invoke(app, ["up", str(bare_project), "--backend", "local"])
        text = (bare_project / ".gitignore").read_text(encoding="utf-8")
        assert text.count(LEXICAL_INDEX_IGNORE) == 1
        assert text.count(GITIGNORE_MARKER) == 1


class TestPanelStandardFollows:
    def test_the_standard_hints_are_left_out_when_the_standard_follows(self, bare_project: Path) -> None:
        from grimoire.core.archetype_resolver import ArchetypeResolver

        resolved = ArchetypeResolver().resolve(StackScanner(bare_project).scan())
        plain = build_next_steps(bare_project, resolved=resolved, backend="local", no_cockpit=True)
        following = build_next_steps(
            bare_project, resolved=resolved, backend="local", no_cockpit=True, standard_follows=True
        )
        assert any("grimoire standard needs" in a for a in plain.actions)
        assert not any("grimoire standard" in a and "governed task" not in a for a in following.actions)
        assert "governance/standard" in (plain.unexploited_line or "")
        assert "governance/standard" not in (following.unexploited_line or "")


# ── 2 : détection de pile par les sources ───────────────────────────────────────


class TestStackFromSources:
    def test_python_files_without_a_manifest_are_a_python_project(self, bare_project: Path) -> None:
        names = {d.name: d for d in StackScanner(bare_project).scan().stacks}
        assert names["python"].evidence == ("*.py",)

    def test_sources_one_directory_down_count_too(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("package main\n", encoding="utf-8")
        assert [d.name for d in StackScanner(tmp_path).scan().stacks] == ["go"]

    def test_a_manifest_wins_over_the_sources(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
        (tmp_path / "helper.py").write_text("", encoding="utf-8")
        assert [d.name for d in StackScanner(tmp_path).scan().stacks] == ["rust"]

    def test_a_readme_only_repo_has_no_stack(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
        assert StackScanner(tmp_path).scan().stacks == ()

    def test_hidden_and_excluded_directories_are_not_read(self, tmp_path: Path) -> None:
        for name in (".venv", "node_modules", "_grimoire"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "x.py").write_text("", encoding="utf-8")
        assert StackScanner(tmp_path).scan().stacks == ()

    def test_a_stray_script_does_not_make_a_subdirectory_a_subproject(self, tmp_path: Path) -> None:
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "x.py").write_text("", encoding="utf-8")
        assert StackScanner(tmp_path).scan_tree().subprojects == ()

    def test_unrecognised_sources_are_not_reported_as_an_empty_repo(self, tmp_path: Path) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        (tmp_path / "main.swift").write_text("", encoding="utf-8")
        reason = recommend_naked_stack(StackScanner(tmp_path).scan()).reason
        assert "No stack marker detected" not in reason
        assert "language the kit does not recognise" in reason


# ── 3 : `grimoire standard score` ───────────────────────────────────────────────


def test_score_says_its_threshold_its_lowest_dimensions_and_where_the_detail_is(bare_project: Path) -> None:
    assert runner.invoke(app, ["up", str(bare_project), "--backend", "local"]).exit_code == 0
    result = runner.invoke(app, ["standard", "score", str(bare_project)])
    out = _flat(result.output)
    assert re.search(r"compliance score \d+/100, threshold 70 for profile 'starter'", out), out
    assert "lowest dimensions:" in out
    assert "grimoire standard audit" in out


# ── 4 : `grimoire doctor` ───────────────────────────────────────────────────────


class TestDoctorSummary:
    def _doctor(self, monkeypatch: pytest.MonkeyPatch, project: Path, checks: list[EnvCheck]) -> str:
        assert runner.invoke(app, ["up", str(project), "--backend", "local"]).exit_code == 0
        monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: checks)
        return _flat(runner.invoke(app, ["doctor", str(project)]).output)

    def test_warnings_are_counted_in_the_conclusion(self, monkeypatch: pytest.MonkeyPatch, bare_project: Path) -> None:
        warn = EnvCheck("w", passed=True, level="warn", detail="un avertissement")
        out = self._doctor(monkeypatch, bare_project, [warn])
        assert re.search(r"\d+ OK, \d+ avertissements? ", out + " "), out
        assert "checks passed" not in out

    def test_a_clean_run_keeps_the_plain_conclusion(self, monkeypatch: pytest.MonkeyPatch, bare_project: Path) -> None:
        out = self._doctor(monkeypatch, bare_project, [])
        if "avertissement" not in out:  # selon la machine, d'autres contrôles peuvent avertir
            assert re.search(r"(\d+)/\1 checks passed", out), out

    def test_the_json_report_counts_warnings(self, monkeypatch: pytest.MonkeyPatch, bare_project: Path) -> None:
        import json

        assert runner.invoke(app, ["up", str(bare_project), "--backend", "local"]).exit_code == 0
        warn = EnvCheck("w", passed=True, level="warn", detail="un avertissement")
        monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [warn])
        data = json.loads(runner.invoke(app, ["-o", "json", "doctor", str(bare_project)]).output)
        assert data["warnings"] >= 1


# ── 7 : avis de modèle ──────────────────────────────────────────────────────────


def test_the_missing_model_advice_says_it_concerns_a_sub_agent() -> None:
    hint = model_hint({"recommended_model": None})
    assert "sous-agent" in hint
    assert "omets le paramètre model" in hint
    assert model_hint({"recommended_model": "haiku"}) == "haiku"


# ── 6 : règle d'exclusion ───────────────────────────────────────────────────────


def test_the_kit_gitignore_section_lists_the_lexical_index(tmp_path: Path) -> None:
    assert LEXICAL_INDEX_IGNORE in GITIGNORE_SECTION
    ensure_grimoire_gitignore(tmp_path)
    assert LEXICAL_INDEX_IGNORE in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ensure_grimoire_gitignore(tmp_path) is None  # idempotent
