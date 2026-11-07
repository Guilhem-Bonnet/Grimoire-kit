"""Flux de sortie des commandes de lecture de `grimoire task` (issue #879).

`grimoire task show X | grep -q closed` ne voyait rien : le rendu nominal
partait sur stderr. Les commandes de lecture écrivent désormais sur stdout ;
stderr ne garde que les erreurs et diagnostics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")
EVIDENCE = Path("_grimoire-runtime-output/evidence")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    """Un projet avec une tâche prête, rendue par les trois commandes de lecture."""
    ledger = MissionLedger(tmp_path / LEDGER)
    mission = ledger.create_mission("Flux de sortie", origin="test", created_by="test")
    task = ledger.create_task(mission.id, "Rendre la fiche sur stdout", acceptance=("un critère",), owner="winston")
    ledger.transition_task(task.id, TaskState.READY, actor_id="test")
    return tmp_path


def task_id(projet: Path) -> str:
    """L'identifiant de la tâche ensemencée, relu par la commande `list` en JSON."""
    res = runner.invoke(task_app, ["list", "--project-root", str(projet)], obj={"output": "json"})
    assert res.exit_code == 0, res.output
    return str(json.loads(res.stdout)[0]["id"])


def test_show_rend_la_fiche_sur_stdout(projet: Path) -> None:
    tid = task_id(projet)
    res = runner.invoke(task_app, ["show", tid, "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    assert tid in res.stdout
    assert "Rendre la fiche sur stdout" in res.stdout
    assert res.stderr == ""


def test_list_rend_les_taches_sur_stdout(projet: Path) -> None:
    tid = task_id(projet)
    res = runner.invoke(task_app, ["list", "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    assert tid in res.stdout
    assert "ready" in res.stdout
    assert res.stderr == ""


def test_pack_rend_le_pack_sur_stdout(projet: Path) -> None:
    tid = task_id(projet)
    pack = EvidenceService(projet / EVIDENCE).create_pack(
        task_id=tid,
        profile=EvidenceProfile.STANDARD,
        items=[EvidenceItem(id="e1", kind=EvidenceKind.TEST, uri="pytest://", digest="d1", summary="un critère")],
        acceptance=("un critère",),
    )
    res = runner.invoke(task_app, ["pack", tid, pack.id, "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    assert pack.id in res.stdout
    assert "Critères couverts" in res.stdout
    assert res.stderr == ""


def test_show_rend_aussi_le_dernier_dispatch_et_la_finition_sur_stdout(projet: Path) -> None:
    tid = task_id(projet)
    MissionLedger(projet / LEDGER).append_event(
        "task.dispatched",
        tid,
        "task",
        "test",
        {
            "verdict": "green",
            "review": "review_required",
            "review_files": ["src/a.py"],
            "review_note": "note de relecture",
            "uncertainties": [{"where": "src/a.py", "what": "point douteux", "why": "non vérifié"}],
            "uncertainty_warnings": ["bloc incomplet"],
        },
    )

    res = runner.invoke(task_app, ["show", tid, "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    for attendu in ("src/a.py", "note de relecture", "point douteux", "bloc incomplet"):
        assert attendu in res.stdout
    assert res.stderr == ""
