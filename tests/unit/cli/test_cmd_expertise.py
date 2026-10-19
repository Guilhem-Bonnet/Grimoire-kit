"""``grimoire expertise list/add/remove`` (issue #616) — surface CLI du
catalogue d'expertises. Construit un projet minimal à la main (pas de
`grimoire init` complet) : ces commandes ne lisent que
``_grimoire/{kit,overrides}/agents`` et ``registry/expertises.yaml``.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

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

        remove_result = runner.invoke(app, ["-o", "json", "expertise", "remove", "rust", "--project-root", str(tmp_path)])
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
