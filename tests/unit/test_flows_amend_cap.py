"""W2-10 — plafond d'amendements et ``accept_as_is`` avec risque accepté tracé.

Avant : ``amend`` avançait identiquement à ``approve`` (aucune boucle de
révision, donc aucun compteur ni plafond). Ici ``amend`` rouvre le node et
est compté ; ``accept_as_is`` n'est possible qu'après N amendements (3 par
défaut), écrit ``accepted_risk`` et remonte la tâche liée au moins en V1.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent

runner = CliRunner()
_GATE_OUT = {"pins": {"out": {"contract": "c1"}}}


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


def _blueprint(root: Path, *, destructive: bool = False, cap: Any = None) -> Path:
    config: dict[str, Any] = {}
    if destructive:
        config["destructive"] = True
    if cap is not None:
        config["max_amendments"] = cap
    gate: dict[str, Any] = {
        "id": "gate",
        "kind": "checkpoint",
        "ref": "",
        "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
    }
    if config:
        gate["config"] = {"checkpoint": config}
    after = {"id": "after", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]}
    path = root / "bp.blueprint.json"
    path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "amend-cap",
                "nodes": [gate, after],
                "edges": [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _quiet() -> InteractiveNodeExecutor:
    return InteractiveNodeExecutor(stream=io.StringIO())


def _start(root: Path, *, task_id: str = "", mission_id: str = "", **kw: Any) -> tuple[FlowEngine, str]:
    engine = _engine(root)
    wfi, _contract = engine.run(_blueprint(root, **kw), executor=_quiet(), task_id=task_id, mission_id=mission_id)
    return engine, wfi.id


def _decide(engine: FlowEngine, run_id: str, decision: str, reason: str = "") -> Any:
    out: dict[str, Any] = {**_GATE_OUT, "checkpoint_decision": decision}
    if reason:
        out["checkpoint_reason"] = reason
    return engine.resume(run_id, output=out, executor=_quiet())


def _human_turn(root: Path) -> None:
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt="ok",
            session_id="S1",
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def _amend(engine: FlowEngine, run_id: str, times: int) -> None:
    for i in range(times):
        outcome = _decide(engine, run_id, "amend", f"révision {i + 1}")
        assert outcome.ok is False and outcome.node_id == "gate"


def test_amend_rouvre_le_node_et_compte_vers_le_plafond(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    outcome = _decide(engine, run_id, "amend", "reprendre la section 2")
    assert outcome.ok is False and outcome.node_id == "gate"
    assert any("amend" in f and "1/3" in f for f in outcome.faults)
    status = engine.status(run_id)
    assert status.current_node == "gate"
    assert [d["decision"] for d in status.checkpoint_decisions] == ["amend"]
    assert status.checkpoint_decisions[0]["reason"] == "reprendre la section 2"


def test_accept_as_is_refuse_avant_le_plafond(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _amend(engine, run_id, 2)

    outcome = _decide(engine, run_id, "accept_as_is", "livrable assez bon")

    assert outcome.ok is False and outcome.node_id == "gate"
    fault = " ".join(outcome.faults)
    assert "accept_as_is" in fault and "2/3" in fault
    assert all("accepted_risk" not in d for d in engine.status(run_id).checkpoint_decisions)


def test_accept_as_is_sans_aucun_amendement_est_refuse(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    outcome = _decide(engine, run_id, "accept_as_is", "pressé")
    assert outcome.ok is False and "0/3" in " ".join(outcome.faults)


def test_accept_as_is_accepte_apres_le_plafond_et_trace_le_risque(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _amend(engine, run_id, 3)

    outcome = _decide(engine, run_id, "accept_as_is", "la section 2 reste approximative")

    assert outcome.ok is True and outcome.node_id == "after"
    accepted = engine.status(run_id).checkpoint_decisions[-1]
    assert accepted["decision"] == "accept_as_is"
    assert accepted["accepted_risk"]["amendments"] == 3
    assert accepted["accepted_risk"]["cap"] == 3
    assert accepted["accepted_risk"]["reason"] == "la section 2 reste approximative"


def test_accept_as_is_exige_le_risque_nomme(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _amend(engine, run_id, 3)
    outcome = _decide(engine, run_id, "accept_as_is")
    assert outcome.ok is False
    assert "checkpoint_reason" in " ".join(outcome.faults)


def test_plafond_configurable_par_le_blueprint(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path, cap=1)
    assert _decide(engine, run_id, "accept_as_is", "x").ok is False
    _amend(engine, run_id, 1)
    assert _decide(engine, run_id, "accept_as_is", "risque connu").ok is True


@pytest.mark.parametrize("bad", [0, -1, "3", True, 2.5])
def test_plafond_invalide_refuse_au_chargement(tmp_path: Path, bad: Any) -> None:
    path = _blueprint(tmp_path, cap=bad)
    with pytest.raises(GrimoireRuntimeError, match="max_amendments"):
        load_blueprint(path, tmp_path)


def test_checkpoint_destructif_exige_l_attestation_pour_accept_as_is(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path, destructive=True)
    _amend(engine, run_id, 3)

    refused = _decide(engine, run_id, "accept_as_is", "risque connu")
    assert refused.ok is False and "awaiting_human" in " ".join(refused.faults)

    _human_turn(tmp_path)
    accepted = _decide(engine, run_id, "accept_as_is", "risque connu")
    assert accepted.ok is True and accepted.attestation["status"] == "attested"


def test_risque_accepte_visible_dans_task_show_et_tache_au_moins_v1(tmp_path: Path) -> None:
    from grimoire.missions.ledger import MissionLedger

    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    mission = ledger.create_mission("m", origin="user")
    from grimoire.missions.schemas import TaskCheck

    task = ledger.create_task(
        mission.id, "t", acceptance=("pytest tests/unit -q passe",), checks=(TaskCheck(cmd="true"),)
    )

    def _show() -> dict[str, Any]:
        result = runner.invoke(app, ["--output", "json", "task", "show", task.id, "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    assert _show()["verifiability"]["class"] == "V0"

    engine, run_id = _start(tmp_path, task_id=task.id, mission_id=mission.id)
    _amend(engine, run_id, 3)
    assert _show()["verifiability"]["class"] == "V0"  # amender n'est pas accepter un risque
    assert _decide(engine, run_id, "accept_as_is", "section 2 approximative").ok is True

    shown = _show()
    assert shown["verifiability"]["class"] == "V1"
    assert shown["accepted_risks"][0]["reason"] == "section 2 approximative"
    assert shown["accepted_risks"][0]["amendments"] == 3
    assert shown["accepted_risks"][0]["run_id"] == run_id


def test_risque_accepte_ne_baisse_pas_une_tache_v2(tmp_path: Path) -> None:
    from grimoire.missions.ledger import MissionLedger

    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    mission = ledger.create_mission("m", origin="user")
    task = ledger.create_task(mission.id, "t", acceptance=("c'est bien",))
    engine, run_id = _start(tmp_path, task_id=task.id, mission_id=mission.id)
    _amend(engine, run_id, 3)
    assert _decide(engine, run_id, "accept_as_is", "ok").ok is True
    result = runner.invoke(app, ["--output", "json", "task", "show", task.id, "--project-root", str(tmp_path)])
    assert json.loads(result.stdout)["verifiability"]["class"] == "V2"
