"""Issue #906 : un lien vers une tâche annulée se retire, il ne se contourne pas.

Avant : ``cancelled`` est terminal et aucune commande ne retirait un lien ; un
dépendant d'une tâche annulée (remplacée par une autre, close) n'était plus
réclamable à vie. Une dépendance annulée reste NON satisfaite
(``test_dag_claim.py::test_une_dependance_annulee_ou_inconnue_ne_libere_pas``) :
le remède est ``task unlink``, motif journalisé, puis ``task link`` au remplaçant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.ledger_links import unlink_tasks
from grimoire.missions.schemas import DependencyKind, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.trace import build_task_timeline

ACC = "le plan se déroule dans l'ordre"


def _pret(ledger: MissionLedger, mission: str, titre: str):
    task = ledger.create_task(mission, titre, acceptance=(ACC,), owner="amelia")
    ledger.transition_task(task.id, TaskState.READY)
    return task


@pytest.fixture
def ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / "ledger")


@pytest.fixture
def mission(ledger: MissionLedger) -> str:
    return ledger.create_mission(title="Plan", origin="test").id


def test_le_refus_de_claim_sur_une_dependance_annulee_nomme_le_remede(ledger: MissionLedger, mission: str) -> None:
    ancien, dependant = _pret(ledger, mission, "Ancien"), _pret(ledger, mission, "Dépendant")
    ledger.link_tasks(dependant.id, ancien.id)
    ledger.transition_task(ancien.id, TaskState.CANCELLED)

    with pytest.raises(GrimoireMissionError) as err:
        ledger.claim_task(dependant.id, "marcus", "local")

    assert "ne sera jamais close" in str(err.value)
    assert f"grimoire task unlink {dependant.id} --depends-on {ancien.id}" in str(err.value)


def test_retirer_le_lien_rend_la_tache_reclamable_et_survit_au_rejeu(ledger: MissionLedger, mission: str) -> None:
    ancien, dependant = _pret(ledger, mission, "Ancien"), _pret(ledger, mission, "Dépendant")
    ledger.link_tasks(dependant.id, ancien.id)
    ledger.transition_task(ancien.id, TaskState.CANCELLED)

    after = unlink_tasks(ledger, dependant.id, ancien.id, reason="remplacée par -002, close", actor_id="guilhem")

    assert after.dependencies == ()
    rouvert = MissionLedger(ledger._root)
    assert rouvert.get_task(dependant.id).dependencies == ()
    assert rouvert.claim_task(dependant.id, "marcus", "local").status is TaskState.CLAIMED
    event = next(e for e in rouvert.list_events() if e.event_type == "task.unlinked")
    assert event.actor_id == "guilhem"
    assert event.payload["reason"] == "remplacée par -002, close"


def test_relier_au_remplacant_garde_le_blocage_tant_qu_il_n_est_pas_clos(ledger: MissionLedger, mission: str) -> None:
    ancien, nouveau, dependant = (_pret(ledger, mission, t) for t in ("Ancien", "Nouveau", "Dépendant"))
    ledger.link_tasks(dependant.id, ancien.id)
    ledger.transition_task(ancien.id, TaskState.CANCELLED)
    unlink_tasks(ledger, dependant.id, ancien.id, reason="remplacée")
    ledger.link_tasks(dependant.id, nouveau.id)

    with pytest.raises(GrimoireMissionError, match=nouveau.id):
        ledger.claim_task(dependant.id, "marcus", "local")


def test_un_retrait_sans_motif_est_refuse_sans_ecrire(ledger: MissionLedger, mission: str) -> None:
    a, b = _pret(ledger, mission, "A"), _pret(ledger, mission, "B")
    ledger.link_tasks(b.id, a.id)
    avant = len(ledger.list_events())

    with pytest.raises(GrimoireMissionError, match="--reason"):
        unlink_tasks(ledger, b.id, a.id, reason="   ")

    assert len(ledger.list_events()) == avant
    assert ledger.get_task(b.id).dependencies


def test_un_lien_absent_est_un_refus_qui_liste_les_vraies_dependances(ledger: MissionLedger, mission: str) -> None:
    """Une faute de frappe ne doit pas laisser croire que le blocage est levé."""
    a, b, c = (_pret(ledger, mission, t) for t in ("A", "B", "C"))
    ledger.link_tasks(b.id, a.id)

    with pytest.raises(GrimoireMissionError) as err:
        unlink_tasks(ledger, b.id, c.id, reason="faute de frappe")
    assert f"{a.id} (blocks)" in str(err.value)
    with pytest.raises(GrimoireMissionError, match="relates"):
        unlink_tasks(ledger, b.id, a.id, DependencyKind.RELATES, reason="mauvaise nature")
    with pytest.raises(GrimoireMissionError, match="Task not found"):
        unlink_tasks(ledger, "GAO-fantome-001", a.id, reason="absente")


def test_le_retrait_ne_touche_que_le_lien_vise(ledger: MissionLedger, mission: str) -> None:
    a, b, c = (_pret(ledger, mission, t) for t in ("A", "B", "C"))
    ledger.link_tasks(c.id, a.id)
    ledger.link_tasks(c.id, b.id)
    ledger.link_tasks(c.id, a.id, DependencyKind.RELATES)

    unlink_tasks(ledger, c.id, a.id, reason="A abandonnée")

    restant = {(d.kind, d.target) for d in MissionLedger(ledger._root).get_task(c.id).dependencies}
    assert restant == {(DependencyKind.BLOCKS, b.id), (DependencyKind.RELATES, a.id)}


def test_la_frise_montre_le_retrait_et_son_motif(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    m = service.ledger.create_mission(title="Plan", origin="test").id
    a, b = _pret(service.ledger, m, "A"), _pret(service.ledger, m, "B")
    service.link(b.id, a.id)
    service.unlink(b.id, a.id, reason="doublon de A", actor="cli")

    summaries = [entry.summary for entry in build_task_timeline(tmp_path, b.id).entries]
    assert any(s == f"ne dépend plus de {a.id} (blocks) — doublon de A" for s in summaries)


def test_la_cli_retire_le_lien_et_refuse_sans_motif(tmp_path: Path) -> None:
    runner = CliRunner()

    def run(*args: str, output: str | None = None):
        return runner.invoke(task_app, [*args, "--project-root", str(tmp_path)], obj={"output": output} if output else None)

    for titre in ("Alpha", "Beta"):
        assert run("add", titre, "-a", ACC).exit_code == 0
    ids = [t.id for t in TaskService(tmp_path).ledger.list_tasks()]
    assert run("link", ids[1], "--depends-on", ids[0]).exit_code == 0

    res = run("unlink", ids[1], "--depends-on", ids[0])
    assert res.exit_code != 0, "le motif est obligatoire"
    res = run("unlink", ids[1], "--depends-on", ids[0], "--kind", "bidon", "--reason", "x")
    assert res.exit_code == 1 and "inconnue" in res.output
    res = run("unlink", ids[1], "--depends-on", ids[0], "--reason", "plan revu", output="json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == {"task": ids[1], "removed": ids[0], "kind": "blocks", "reason": "plan revu"}
    res = run("unlink", ids[1], "--depends-on", ids[0], "--reason", "encore")
    assert res.exit_code == 1 and "ne dépend pas" in res.output
    assert TaskService(tmp_path).ledger.get_task(ids[1]).dependencies == ()
