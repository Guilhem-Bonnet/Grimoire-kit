"""``grimoire memory gate`` — la migration est vérifiée par présence du bundle (#1042).

Un poste qui n'a jamais lancé ``memory migrate export-bundle`` n'a pas de
migration en cours : le gate doit le dire, pas échouer. ``--require-migration``
garde le FAIL et nomme la séquence export → import → verify.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner, Result

from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import MemoryEntry

pytestmark = pytest.mark.covers("cli:memory gate")

runner = CliRunner()


def _cfg() -> GrimoireConfig:
    return GrimoireConfig.from_dict({
        "project": {"name": "test", "type": "generic", "stack": []},
        "memory": {"backend": "local", "weaviate_url": "http://localhost:8080"},
        "agents": {"archetype": "minimal"},
    })


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    """Projet minimal dont les projections sont déjà à jour (graphe et vecteurs OK)."""
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    graph = MagicMock()
    graph.stats.return_value = {}
    mgr = MagicMock()
    mgr.get_all.return_value = [
        MemoryEntry(id="code:src/lib.py", text="x", user_id="g", metadata={"projection_group": "code"}),
    ]
    with (
        patch("grimoire.cli.cmd_memory._load_config_context", return_value=(_cfg(), tmp_path)),
        patch("grimoire.cli.cmd_memory._load_manager_context", return_value=(mgr, _cfg(), tmp_path)),
        patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
    ):
        yield tmp_path


def _gate(*args: str) -> tuple[Result, MagicMock]:
    with patch("grimoire.memory.migration.verify_migration_bundle", return_value={"ok": True, "issues": []}) as verify:
        result = runner.invoke(app, ["-o", "json", "memory", "gate", "--paths", "src", "--no-sync", "--skip-graph", "--skip-vectors", *args])
    return result, verify


def test_bundle_absent_sans_option_est_un_constat_nomme(projet: Path) -> None:
    result, verify = _gate()

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["migration"]["skipped"] is True
    assert data["migration"]["notice"] == "aucune migration en cours sur ce poste"
    assert data["issues"] == []
    verify.assert_not_called()


def test_bundle_absent_avec_require_migration_echoue_et_dit_le_remede(projet: Path) -> None:
    result, verify = _gate("--require-migration")

    assert result.exit_code == 1, result.output
    verify.assert_not_called()
    data = json.loads(result.output)
    assert data["ok"] is False
    (issue,) = data["issues"]
    assert "Migration bundle not found" in issue
    assert "export-bundle" in issue
    assert "import" in issue
    assert "verify" in issue


def test_bundle_present_garde_la_verification_stricte(projet: Path) -> None:
    (projet / "_grimoire" / "_memory" / "migration" / "weaviate-neo4j").mkdir(parents=True)
    failing = {"ok": False, "issues": ["Bundle manifest missing"]}
    with patch("grimoire.memory.migration.verify_migration_bundle", return_value=failing) as verify:
        result = runner.invoke(app, ["-o", "json", "memory", "gate", "--paths", "src", "--no-sync", "--skip-graph", "--skip-vectors"])

    assert result.exit_code == 1, result.output
    verify.assert_called_once()
    data = json.loads(result.output)
    assert data["issues"] == ["Bundle manifest missing"]
    assert "notice" not in data["migration"]


def test_le_rendu_texte_nomme_le_constat(projet: Path) -> None:
    result = runner.invoke(app, ["memory", "gate", "--paths", "src", "--no-sync", "--skip-graph", "--skip-vectors"])

    assert result.exit_code == 0, result.output
    assert "aucune migration en cours sur ce poste" in result.output
