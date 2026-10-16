"""W2-10-S1-01 : la remontée V0 -> V1 d'un risque accepté vaut pour toute surface qui décide.

``grimoire task show`` était la seule surface à lire ``task.risk_accepted`` ;
l'outil MCP ``task_show``, ``dispatch_advice``, ``classify`` et la transition
``needs_verification`` de ``grimoire task dispatch`` voyaient encore V0. La
remontée vit désormais dans la tâche rejouée par le ledger
(``MissionTask.accepted_risks``) et dans ``classify`` : une seule classification.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.verifiability import Verifiability, as_dict, classify


def _load_sibling(name: str, *parts: str) -> Any:
    """Charge un module de test voisin par son chemin.

    `tests/` n'est pas un paquet et `tests/unit/missions/` non plus : un
    `from tests.unit...` ne résout que si le cwd est sur sys.path, ce qui est
    faux sous `pytest` nu en CI.
    """
    path = Path(__file__).parent.joinpath(*parts)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_td = _load_sibling("_w210_test_dispatch", "missions", "test_dispatch.py")
_tf = _load_sibling("_w210_test_flows_amend_cap", "test_flows_amend_cap.py")
_WRITE_MARKER = _td._WRITE_MARKER
_invocation = _td._invocation
_provider_yaml = _td._provider_yaml
_run_task = _td._run_task
_script = _td._script
_service = _td._service
_task = _td._task
_write_registry = _td._write_registry
_amend = _tf._amend
_decide = _tf._decide
_session_env = _tf._session_env
_start = _tf._start

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")


def _accepted_task(root: Path, acceptance: tuple[str, ...]) -> tuple[MissionLedger, MissionTask]:
    """Une tâche liée à un run dont le checkpoint a été clos par ``accept_as_is``."""
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission("m", origin="user")
    task = ledger.create_task(mission.id, "t", acceptance=acceptance)
    engine, run_id = _start(root, task_id=task.id, mission_id=mission.id)
    _amend(engine, run_id, 3)
    assert _decide(engine, run_id, "accept_as_is", "section 2 approximative").ok is True
    reloaded = MissionLedger(root / LEDGER).get_task(task.id)
    assert reloaded is not None
    return ledger, reloaded


def test_classify_et_dispatch_advice_voient_v1(tmp_path: Path) -> None:
    _, task = _accepted_task(tmp_path, ("pytest tests/unit -q passe",))
    assert classify(task) is Verifiability.V1
    advice = dispatch_advice(task)
    assert advice["verifiability"]["class"] == "V1"
    assert advice["verifiability"]["raised_by"] == "accepted_risk"
    assert advice["recommended_model"] == "sonnet"
    assert advice["model_tier"] == "mid"


def test_mcp_task_show_voit_v1(tmp_path: Path) -> None:
    from grimoire.mcp.server import _task_json

    _, task = _accepted_task(tmp_path, ("pytest tests/unit -q passe",))
    data = _task_json(task)
    assert data["verifiability"]["class"] == "V1"
    assert (data["recommended_model"], data["model_tier"]) == ("sonnet", "mid")
    assert data["accepted_risks"][0]["reason"] == "section 2 approximative"


def test_cli_task_show_et_mcp_disent_la_meme_chose(tmp_path: Path) -> None:
    from grimoire.mcp.server import _task_json

    _, task = _accepted_task(tmp_path, ("pytest tests/unit -q passe",))
    shown = json.loads(
        runner.invoke(app, ["--output", "json", "task", "show", task.id, "--project-root", str(tmp_path)]).stdout
    )
    mcp = _task_json(task)
    for key in ("verifiability", "recommended_model", "model_tier"):
        assert shown[key] == mcp[key]
    assert shown["accepted_risks"][0]["run_id"] == mcp["accepted_risks"][0]["run_id"]


def test_task_claim_voit_v1(tmp_path: Path) -> None:
    from grimoire.missions.service import TaskService

    _accepted_task(tmp_path, ("pytest tests/unit -q passe",))
    service = TaskService(tmp_path, LEDGER)
    task = next(iter(service.ledger.list_tasks()))
    service.transition(task.id, TaskState.READY, "amelia")
    data = service.claim(task.id, "amelia").to_dict()
    assert data["verifiability"]["class"] == "V1"
    assert data["model_tier"] == "mid"


def test_v2_reste_v2_et_v0_sans_risque_reste_v0(tmp_path: Path) -> None:
    _, v2 = _accepted_task(tmp_path, ("c'est bien",))
    assert classify(v2) is Verifiability.V2
    plain = MissionLedger(tmp_path / "autre").create_task(
        MissionLedger(tmp_path / "autre").create_mission("m", origin="user").id,
        "t",
        acceptance=("pytest tests/unit -q passe",),
    )
    assert classify(plain) is Verifiability.V0
    assert "raised_by" not in as_dict(plain)


def test_le_risque_survit_aux_transitions_de_la_tache(tmp_path: Path) -> None:
    ledger, task = _accepted_task(tmp_path, ("pytest tests/unit -q passe",))
    ledger.transition_task(task.id, TaskState.READY, actor_id="amelia")
    again = MissionLedger(tmp_path / LEDGER).get_task(task.id)
    assert again is not None and classify(again) is Verifiability.V1
    assert MissionTask.from_dict(again.to_dict()).accepted_risks == again.accepted_risks


def test_dispatch_passe_en_needs_verification_apres_accept_as_is(tmp_path: Path) -> None:
    """La transition ``needs_verification`` de ``task dispatch`` suit la classe remontée."""
    green = _script(tmp_path, "green.py", _WRITE_MARKER)
    _write_registry(tmp_path, _provider_yaml("mid-only", "mid", _invocation(green)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=("pytest tests/unit -q passe",))
    _run_task(service, tid)
    service.ledger.append_event(
        "task.risk_accepted",
        tid,
        "task",
        "flow-engine",
        {"run_id": "r1", "node_id": "n", "amendments": 3, "cap": 3, "reason": "approximatif"},
    )

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.verifiability == "V1"
    assert report.succeeded
    assert report.transitioned_to == TaskState.NEEDS_VERIFICATION.value
    assert report.attempts[0].tier == "mid"


@pytest.mark.parametrize("payload", [{}, {"reason": 3}])
def test_evenement_incomplet_ne_casse_pas_le_rejeu(tmp_path: Path, payload: dict[str, Any]) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("m", origin="user")
    task = ledger.create_task(mission.id, "t", acceptance=("pytest tests/unit -q passe",))
    ledger.append_event("task.risk_accepted", task.id, "task", "x", payload)
    ledger.append_event("task.risk_accepted", "inconnue", "task", "x", payload)
    replayed = MissionLedger(tmp_path / "ledger").get_task(task.id)
    assert replayed is not None and classify(replayed) is Verifiability.V1
