"""``grimoire expertise list/add/remove`` (issue #616) — surface CLI du
catalogue d'expertises. Construit un projet minimal à la main (pas de
`grimoire init` complet) : ces commandes ne lisent que
``_grimoire/{kit,overrides}/agents`` et ``registry/expertises.yaml``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

pytestmark = pytest.mark.covers(
    "cli:expertise add",
    "cli:expertise list",
    "cli:expertise remove",
)

runner = CliRunner()


def _seed_stack_engineer(root: Path) -> None:
    agents_dir = root / "_grimoire" / "kit" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "stack-engineer.md").write_text(
        '---\nname: "stack-engineer"\ndescription: "test"\ntools: "read, edit, execute"\nskills: ["stack-python"]\n---\nCorps.\n',
        encoding="utf-8",
    )
    skills_dir = root / "_grimoire" / "kit" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "stack-python.md").write_text(
        '---\ndescription: "python"\ntools: ["read"]\n---\nCorps.\n', encoding="utf-8"
    )


class TestExpertiseList:
    def test_lists_the_full_catalogue_by_default(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "expertise", "list"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        ids = {entry["id"] for entry in payload}
        assert "rust" in ids
        assert "aws" in ids

    def test_detected_shows_only_recommendations_with_a_reason(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")

        result = runner.invoke(app, ["-o", "json", "expertise", "list", "--detected", "--project-root", str(tmp_path)])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        by_id = {entry["id"]: entry for entry in payload}
        assert "rust" in by_id
        assert by_id["rust"]["reason"]


class TestExpertiseAddRemove:
    def test_add_attaches_and_remove_detaches(self, tmp_path: Path) -> None:
        _seed_stack_engineer(tmp_path)

        add_result = runner.invoke(app, ["-o", "json", "expertise", "add", "rust", "--project-root", str(tmp_path)])
        assert add_result.exit_code == 0, add_result.output
        payload = json.loads(add_result.output)
        assert payload[0]["porteur"] == "stack-engineer"
        assert (tmp_path / "_grimoire" / "overrides" / "skills" / "expertise-rust.md").is_file()

        remove_result = runner.invoke(
            app, ["-o", "json", "expertise", "remove", "rust", "--project-root", str(tmp_path)]
        )
        assert remove_result.exit_code == 0, remove_result.output
        assert not (tmp_path / "_grimoire" / "overrides" / "skills" / "expertise-rust.md").exists()

    def test_add_unknown_id_is_a_named_refusal(self, tmp_path: Path) -> None:
        _seed_stack_engineer(tmp_path)
        result = runner.invoke(app, ["expertise", "add", "not-a-real-id", "--project-root", str(tmp_path)])
        assert result.exit_code == 1
        assert "not-a-real-id" in result.output

    def test_add_without_any_porteur_installed_refuses_named(self, tmp_path: Path) -> None:
        (tmp_path / "_grimoire" / "kit" / "agents").mkdir(parents=True)
        result = runner.invoke(app, ["expertise", "add", "rust", "--project-root", str(tmp_path)])
        assert result.exit_code == 1
        assert "rust" in result.output


class TestExpertiseTextOutput:
    """Rendu texte (hors `-o json`) des trois commandes — lot onboarding."""

    def test_list_groups_entries_by_family(self) -> None:
        result = runner.invoke(app, ["expertise", "list"])
        assert result.exit_code == 0, result.output
        assert "languages" in result.output
        assert "cloud" in result.output
        assert "rust" in result.output

    def test_list_detected_says_so_when_nothing_is_detected(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["expertise", "list", "--detected", "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Aucune expertise détectée" in result.output

    def test_list_detected_prints_reason_and_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
        result = runner.invoke(app, ["expertise", "list", "--detected", "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "rust" in result.output
        assert "confiance" in result.output

    def test_add_then_remove_report_in_text(self, tmp_path: Path) -> None:
        _seed_stack_engineer(tmp_path)
        add_result = runner.invoke(app, ["expertise", "add", "rust", "--project-root", str(tmp_path)])
        assert add_result.exit_code == 0, add_result.output
        assert "attaché" in add_result.output
        assert "host sync" in add_result.output

        again = runner.invoke(app, ["expertise", "add", "rust", "--project-root", str(tmp_path)])
        assert again.exit_code == 0, again.output
        assert "déjà attaché" in again.output

        remove_result = runner.invoke(app, ["expertise", "remove", "rust", "--project-root", str(tmp_path)])
        assert remove_result.exit_code == 0, remove_result.output
        assert "détaché" in remove_result.output

        not_attached = runner.invoke(app, ["expertise", "remove", "rust", "--project-root", str(tmp_path)])
        assert not_attached.exit_code == 0, not_attached.output
        assert "n'était pas attaché" in not_attached.output

    def test_remove_unknown_id_is_a_named_refusal(self, tmp_path: Path) -> None:
        _seed_stack_engineer(tmp_path)
        result = runner.invoke(app, ["expertise", "remove", "nope", "--project-root", str(tmp_path)])
        assert result.exit_code == 1
        assert "inconnu" in result.output
        assert "nope" in result.output

    def test_remove_in_json_lists_results(self, tmp_path: Path) -> None:
        _seed_stack_engineer(tmp_path)
        runner.invoke(app, ["expertise", "add", "rust", "--project-root", str(tmp_path)])
        result = runner.invoke(app, ["-o", "json", "expertise", "remove", "rust", "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload[0]["expertise_id"] == "rust"
        assert payload[0]["was_attached"] is True
