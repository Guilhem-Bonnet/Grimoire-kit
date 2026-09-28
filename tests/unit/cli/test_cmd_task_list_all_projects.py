"""`grimoire task list --all-projects` — le portefeuille en ligne de commande (#638, lot C)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.tools import project_registry as reg

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")


def _project_with_task(root: Path, title: str) -> str:
    root.mkdir(parents=True)
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission(title="Travaux courants", origin="test", created_by="test")
    task = ledger.create_task(mission.id, title, acceptance=("un critère",), owner="winston")
    ledger.transition_task(task.id, TaskState.READY, actor_id="test")
    return task.id


@pytest.fixture
def registre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    task_a = _project_with_task(tmp_path / "a", "Tâche Alpha")
    task_b = _project_with_task(tmp_path / "b", "Tâche Beta")
    reg.save_registry(
        [
            {"slug": "alpha", "name": "Alpha", "path": str(tmp_path / "a")},
            {"slug": "beta", "name": "Beta", "path": str(tmp_path / "b")},
            {"slug": "gamma", "name": "Gamma", "path": str(tmp_path / "absent")},
        ]
    )
    return {"a": task_a, "b": task_b}


def test_all_projects_en_json_rend_le_portefeuille(registre: dict[str, str], tmp_path: Path) -> None:
    res = runner.invoke(
        task_app, ["list", "--all-projects", "--project-root", str(tmp_path / "a")], obj={"output": "json"}
    )

    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert {t["id"] for t in payload["tasks"]} == set(registre.values())
    assert {t["project"]["slug"] for t in payload["tasks"]} == {"alpha", "beta"}
    gamma = next(p for p in payload["projects"] if p["slug"] == "gamma")
    assert gamma["state"] == "unreadable"


def test_all_projects_en_texte_rend_un_tableau_et_nomme_le_projet_illisible(
    registre: dict[str, str], tmp_path: Path
) -> None:
    res = runner.invoke(
        task_app, ["list", "--all-projects", "--project-root", str(tmp_path / "a")], obj={"output": "text"}
    )

    assert res.exit_code == 0, res.output
    assert "Alpha" in res.output and "Beta" in res.output
    assert registre["a"] in res.output and registre["b"] in res.output
    assert "gamma" in res.output and "absent" in res.output


def test_all_projects_respecte_les_filtres_etat_et_projet(registre: dict[str, str], tmp_path: Path) -> None:
    res = runner.invoke(
        task_app,
        ["list", "--all-projects", "--status", "ready", "--project", "beta", "--project-root", str(tmp_path / "a")],
        obj={"output": "json"},
    )

    assert res.exit_code == 0, res.output
    assert [t["id"] for t in json.loads(res.stdout)["tasks"]] == [registre["b"]]


def test_sans_all_projects_le_comportement_historique_est_intact(registre: dict[str, str], tmp_path: Path) -> None:
    res = runner.invoke(task_app, ["list", "--project-root", str(tmp_path / "a")], obj={"output": "json"})

    assert res.exit_code == 0, res.output
    assert [t["id"] for t in json.loads(res.stdout)] == [registre["a"]]


@pytest.mark.parametrize("argv", [["--project", "alpha"], ["--live"], ["--live-minutes", "5"]])
def test_filtres_portefeuille_sans_all_projects_echouent(
    registre: dict[str, str], tmp_path: Path, argv: list[str]
) -> None:
    """`--project`/`--live`/`--live-minutes` sans `--all-projects` : le filtre serait
    silencieusement ignoré (revue Copilot #641) — on échoue au lieu de laisser croire
    qu'il a été appliqué."""
    res = runner.invoke(task_app, ["list", "--project-root", str(tmp_path / "a"), *argv])

    assert res.exit_code != 0
    assert "--all-projects" in res.output
