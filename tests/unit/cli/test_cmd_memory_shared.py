"""Tests de la surface CLI ``grimoire memory shared``.

Pas de mock du store : chaque test travaille sur un vrai projet temporaire et
un vrai store transverse, pour que la commande soit exercée par le chemin
qu'elle empruntera en production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()

PATTERN = "les migrations Alembic cassent quand deux heads coexistent dans le dépôt"
PROJECT_FACT = "Mon Super Projet tourne sur Postgres 16 en production depuis mars"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Projet temporaire avec mémoire transverse activée."""
    monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project-context.yaml").write_text(
        'project:\n  name: "Mon Super Projet"\n'
        'memory:\n  backend: "local"\n  collection_prefix: "mon_projet"\n'
        '  shared_collection: "GrimoireShared"\n'
        'agents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def project_without_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project-context.yaml").write_text(
        'project:\n  name: "Sans Transverse"\n'
        'memory:\n  backend: "local"\n'
        'agents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return root


# ── Désactivé par défaut ──────────────────────────────────────────────────────


class TestDisabled:
    def test_promote_explains_how_to_enable(self, project_without_shared: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "promote", PATTERN, "-d", "alembic"])
        assert result.exit_code == 1
        assert "shared_collection" in result.output

    def test_confirm_explains_how_to_enable(self, project_without_shared: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "confirm", "abc"])
        assert result.exit_code == 1
        assert "shared_collection" in result.output

    def test_recall_still_works_on_the_project_alone(self, project_without_shared: Path) -> None:
        """Sans transverse, la recherche projet doit rester utilisable."""
        result = runner.invoke(app, ["memory", "shared", "recall", "alembic"])
        assert result.exit_code == 0
        assert "aucun motif transverse" in result.output


# ── promote ───────────────────────────────────────────────────────────────────


class TestPromote:
    def test_pattern_is_accepted(self, project: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "promote", PATTERN, "-d", "alembic"])
        assert result.exit_code == 0, result.output
        assert "domain-alembic" in result.output
        assert "mon-super-projet" in result.output

    def test_project_fact_is_refused(self, project: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "promote", PROJECT_FACT, "-d", "db"])
        assert result.exit_code == 1
        assert "nomme le projet" in result.output
        assert "--force" in result.output

    def test_refusal_writes_nothing(self, project: Path) -> None:
        runner.invoke(app, ["memory", "shared", "promote", PROJECT_FACT, "-d", "db"])
        listed = runner.invoke(app, ["-o", "json", "memory", "shared", "recall", "Postgres"])
        assert json.loads(listed.output)["shared"] == []

    def test_force_reports_the_bypass(self, project: Path) -> None:
        result = runner.invoke(
            app, ["memory", "shared", "promote", PROJECT_FACT, "-d", "db", "--force"]
        )
        assert result.exit_code == 0, result.output
        assert "forcé" in result.output
        assert "nomme le projet" in result.output

    def test_json_success_carries_the_entry(self, project: Path) -> None:
        result = runner.invoke(
            app, ["-o", "json", "memory", "shared", "promote", PATTERN, "-d", "alembic"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["promoted"] is True
        assert payload["entry"]["metadata"]["domain"] == "alembic"

    def test_json_refusal_carries_the_reasons(self, project: Path) -> None:
        result = runner.invoke(
            app, ["-o", "json", "memory", "shared", "promote", PROJECT_FACT, "-d", "db"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["promoted"] is False
        assert any("nomme le projet" in r for r in payload["reasons"])


# ── confirm ───────────────────────────────────────────────────────────────────


class TestConfirm:
    def test_unknown_id_fails_cleanly(self, project: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "confirm", "identifiant-inexistant"])
        assert result.exit_code == 1
        assert "introuvable" in result.output

    def test_confirmation_records_this_project(self, project: Path) -> None:
        promoted = runner.invoke(
            app, ["-o", "json", "memory", "shared", "promote", PATTERN, "-d", "alembic"]
        )
        entry_id = json.loads(promoted.output)["entry"]["id"]

        result = runner.invoke(app, ["memory", "shared", "confirm", entry_id])
        assert result.exit_code == 0, result.output
        assert "mon-super-projet" in result.output

    def test_confirmation_json(self, project: Path) -> None:
        promoted = runner.invoke(
            app, ["-o", "json", "memory", "shared", "promote", PATTERN, "-d", "alembic"]
        )
        entry_id = json.loads(promoted.output)["entry"]["id"]
        result = runner.invoke(app, ["-o", "json", "memory", "shared", "confirm", entry_id])
        assert result.exit_code == 0
        assert "mon-super-projet" in json.loads(result.output)["metadata"]["confirmed_in"]


# ── recall ────────────────────────────────────────────────────────────────────


class TestRecall:
    def test_empty_both_scopes(self, project: Path) -> None:
        result = runner.invoke(app, ["memory", "shared", "recall", "rien"])
        assert result.exit_code == 0
        assert "aucun résultat local" in result.output
        assert "aucun motif transverse" in result.output

    def test_shared_hit_shows_origin(self, project: Path) -> None:
        runner.invoke(app, ["memory", "shared", "promote", PATTERN, "-d", "alembic"])
        result = runner.invoke(app, ["memory", "shared", "recall", "alembic"])
        assert result.exit_code == 0, result.output
        assert "appris dans mon-super-projet" in result.output

    def test_json_separates_the_two_scopes(self, project: Path) -> None:
        runner.invoke(app, ["memory", "shared", "promote", PATTERN, "-d", "alembic"])
        result = runner.invoke(app, ["-o", "json", "memory", "shared", "recall", "alembic"])
        payload = json.loads(result.output)
        assert payload["project"] == []
        assert payload["shared"]
        assert payload["shared"][0]["scope"] == "shared"
        assert payload["shared"][0]["learnedIn"] == ["mon-super-projet"]

    def test_both_scopes_populated_stay_labelled(self, project: Path) -> None:
        """Le cas qui compte : deux portées peuplées, restituées séparément."""
        runner.invoke(app, ["memory", "remember", "-a", "dev", "-t", "decisions",
                            "ici alembic tourne avec une seule head"])
        runner.invoke(app, ["memory", "shared", "promote", PATTERN, "-d", "alembic"])

        result = runner.invoke(app, ["memory", "shared", "recall", "alembic"])
        assert result.exit_code == 0, result.output
        assert "une seule head" in result.output          # passe projet
        assert "appris dans mon-super-projet" in result.output  # passe transverse
        # Le projet est annonce avant le transverse (ORC-06).
        assert result.output.index("Projet") < result.output.index("Transverse")

    def test_limit_is_honoured(self, project: Path) -> None:
        for i in range(4):
            runner.invoke(
                app,
                ["memory", "shared", "promote", f"{PATTERN} — variante numéro {i}", "-d", "alembic"],
            )
        result = runner.invoke(
            app, ["-o", "json", "memory", "shared", "recall", "alembic", "--limit", "2"]
        )
        assert len(json.loads(result.output)["shared"]) <= 2
