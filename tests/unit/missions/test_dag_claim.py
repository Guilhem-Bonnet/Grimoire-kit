"""W5-01 : un plan de tâches est un DAG — un claim se refuse tant qu'une dépendance n'est pas close.

Avant : ``task link`` écrivait un événement que le rejeu ignorait, donc
``MissionTask.dependencies`` restait vide et rien n'était jamais refusé ; aucun
cycle n'était contrôlé ; ``task waves`` n'existait pas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.board import build_board
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import DependencyKind, TaskState
from grimoire.missions.service import TaskService

ACC = "le plan se déroule dans l'ordre"


def _pret(ledger: MissionLedger, mission: str, titre: str, **kw):
    task = ledger.create_task(mission, titre, acceptance=(ACC,), owner="amelia", **kw)
    ledger.transition_task(task.id, TaskState.READY)
    return task


def _clore(ledger: MissionLedger, task_id: str) -> None:
    ledger.claim_task(task_id, "amelia", "local")
    ledger.transition_task(task_id, TaskState.RUNNING)
    ledger.transition_task(task_id, TaskState.NEEDS_VERIFICATION)
    ledger.transition_task(task_id, TaskState.CLOSED)


@pytest.fixture
def ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / "ledger")


@pytest.fixture
def mission(ledger: MissionLedger) -> str:
    return ledger.create_mission(title="Plan", origin="test").id


def test_un_lien_est_rejoue_dans_les_dependances(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id)
    rouvert = MissionLedger(ledger._root)
    assert [(d.kind, d.target) for d in rouvert.get_task(b.id).dependencies] == [(DependencyKind.BLOCKS, a.id)]


def test_claim_refuse_tant_que_la_dependance_nest_pas_close_et_la_nomme(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id)
    with pytest.raises(GrimoireMissionError) as err:
        ledger.claim_task(b.id, "marcus", "local")
    assert a.id in str(err.value)
    assert "ready" in str(err.value)
    assert ledger.get_task(b.id).status is TaskState.READY
    _clore(ledger, a.id)
    assert ledger.claim_task(b.id, "marcus", "local").status is TaskState.CLAIMED


def test_une_dependance_annulee_ou_inconnue_ne_libere_pas(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id)
    ledger.transition_task(a.id, TaskState.CANCELLED)
    with pytest.raises(GrimoireMissionError, match=a.id):
        ledger.claim_task(b.id, "marcus", "local")


def test_une_tache_sans_dependance_nest_pas_affectee(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id, DependencyKind.RELATES)  # `relates` ne bloque pas
    assert ledger.claim_task(b.id, "marcus", "local").status is TaskState.CLAIMED
    assert ledger.claim_task(a.id, "amelia", "local").status is TaskState.CLAIMED


def test_le_service_refuse_le_claim_sans_ecrire_de_transition(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    m = service.ledger.create_mission(title="Plan", origin="test").id
    a, b = _pret(service.ledger, m, "A"), _pret(service.ledger, m, "B")
    service.link(b.id, a.id)
    avant = len(service.ledger.list_events())
    with pytest.raises(GrimoireMissionError, match=a.id):
        service.claim(b.id, "marcus")
    assert len(service.ledger.list_events()) == avant


def test_un_cycle_est_refuse_a_lecriture(ledger: MissionLedger, mission: str) -> None:
    a, b, c = (_pret(ledger, mission, t) for t in "ABC")
    ledger.link_tasks(b.id, a.id)
    ledger.link_tasks(c.id, b.id)
    avant = len(ledger.list_events())
    with pytest.raises(GrimoireMissionError, match="cycle"):
        ledger.link_tasks(a.id, c.id)
    with pytest.raises(GrimoireMissionError, match="elle-même"):
        ledger.link_tasks(a.id, a.id)
    assert len(ledger.list_events()) == avant, "un lien refusé n'écrit rien"
    ledger.link_tasks(c.id, b.id)  # idempotent
    assert len(ledger.list_events()) == avant


def test_la_carte_du_board_ne_garde_pas_un_blocage_leve(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id)

    def blockers() -> list[str]:
        card = next(t for t in build_board(ledger, project="test")["tasks"] if t["task_id"] == b.id)
        return [x["target"] for x in card["blockers"]]

    assert blockers() == [a.id]
    _clore(ledger, a.id)
    assert blockers() == []


def test_waves_expose_les_vagues_et_les_chevauchements_de_fichiers(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    m = service.ledger.create_mission(title="Plan", origin="test").id
    a = service.ledger.create_task(m, "A", acceptance=(ACC,), extra={"files": ["src/x.py"]})
    b = service.ledger.create_task(m, "B", acceptance=(ACC,), extra={"files": ["src/x.py", "src/y.py"]})
    c = service.ledger.create_task(m, "C", acceptance=(ACC,), extra={"files": ["src/x.py"]})
    service.link(c.id, a.id)
    plan = service.waves()
    assert plan["waves"] == [{"wave": 1, "tasks": sorted([a.id, b.id])}, {"wave": 2, "tasks": [c.id]}]
    assert plan["file_overlaps"] == [{"wave": 1, "file": "src/x.py", "tasks": sorted([a.id, b.id])}]
    assert plan["cyclic"] == [] and plan["unresolved"] == {}


def test_la_cli_link_refuse_un_cycle_et_waves_sort_en_json(tmp_path: Path) -> None:
    runner = CliRunner()

    def run(*args: str):
        return runner.invoke(task_app, [*args, "--project-root", str(tmp_path)])

    for titre in ("Alpha", "Beta"):
        res = run("add", titre, "-a", ACC, "--file", "src/z.py")
        assert res.exit_code == 0, res.output
    service = TaskService(tmp_path)
    ids = [t.id for t in service.ledger.list_tasks()]
    assert run("link", ids[1], "--depends-on", ids[0]).exit_code == 0
    res = run("link", ids[0], "--depends-on", ids[1])
    assert res.exit_code == 1 and "cycle" in res.output
    res = runner.invoke(task_app, ["waves", "--project-root", str(tmp_path)], obj={"output": "json"})
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["waves"][0]["tasks"] == [ids[0]]


def test_un_evenement_de_lien_deja_ecrit_bloque_le_claim(ledger: MissionLedger, mission: str) -> None:
    """Le défaut d'origine, sans l'API neuve : l'événement que `task link` écrit déjà doit être rejoué."""
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.append_event("task.linked", b.id, "task", "cli", {"task_id": b.id, "depends_on": a.id, "kind": "blocks"})
    with pytest.raises(GrimoireMissionError, match=a.id):
        ledger.claim_task(b.id, "marcus", "local")
