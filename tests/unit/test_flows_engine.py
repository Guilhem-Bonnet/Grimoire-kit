"""Tests du moteur de flows (#204) : le kernel avance, un blueprint minimal le prouve.

Le blueprint à trois nodes (``a -> b -> c``) est écrit par le test lui-même,
pas chargé depuis ``registry/`` : il n'a besoin que d'un graphe linéaire avec
des contrats de pin distincts pour exercer avancement, suspension et reprise.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.schemas import FlowRunMeta
from grimoire.missions.trace import build_task_timeline
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext, RunEventType, WorkflowStatus


def _write_blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes",
        "name": "Trois nodes",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "A",
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b",
                "kind": "extension-node",
                "ref": "demo/demo-node",
                "label": "B",
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c1"},
                    {"id": "out", "direction": "out", "contract": "c2"},
                ],
            },
            {
                "id": "c",
                "kind": "pattern",
                "ref": "QUA-04",
                "label": "C",
                "pins": [{"id": "in", "direction": "in", "contract": "c2"}],
            },
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


@pytest.fixture
def bp(tmp_path: Path) -> Path:
    return _write_blueprint(tmp_path)


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


def _capture_executor() -> tuple[InteractiveNodeExecutor, io.StringIO]:
    buf = io.StringIO()
    return InteractiveNodeExecutor(stream=buf), buf


def test_run_prints_the_first_node_contract(tmp_path: Path, bp: Path) -> None:
    engine = _engine(tmp_path)
    executor, buf = _capture_executor()
    wfi, contract = engine.run(bp, executor=executor)
    assert contract.node_id == "a"
    assert wfi.status is WorkflowStatus.RUNNING
    text = buf.getvalue()
    assert "Node « a »" in text
    assert "c1" in text


def test_resume_with_conforming_output_advances_to_completion(tmp_path: Path, bp: Path) -> None:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))

    out1 = engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert out1.ok
    assert not out1.finished
    assert out1.node_id == "b"

    out2 = engine.resume(wfi.id, output={"pins": {"out": {"contract": "c2"}}})
    assert out2.ok
    assert not out2.finished
    assert out2.node_id == "c"

    # 'c' ne porte aucune pin de sortie : une sortie vide la satisfait.
    out3 = engine.resume(wfi.id, output={"pins": {}})
    assert out3.ok
    assert out3.finished

    final = engine.status(wfi.id)
    assert final.status == WorkflowStatus.COMPLETED.value
    assert final.completed_nodes == ("a", "b", "c")
    assert final.current_node is None


def test_non_conforming_output_suspends_naming_node_and_pin(tmp_path: Path, bp: Path) -> None:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))

    refused = engine.resume(wfi.id, output={"pins": {"out": {"contract": "mauvais-contrat"}}})
    assert not refused.ok
    assert not refused.finished
    assert refused.node_id == "a"
    assert any("node=a" in f and "pin=out" in f for f in refused.faults)

    status = engine.status(wfi.id)
    assert status.status == WorkflowStatus.BLOCKED.value
    assert status.current_node == "a"
    assert status.last_refusal is not None
    assert status.last_refusal["node_id"] == "a"

    # Retentative après correction : le même node, cette fois conforme.
    retried = engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert retried.ok
    assert retried.node_id == "b"


def test_crash_between_two_nodes_resumes_at_the_exact_current_node(tmp_path: Path, bp: Path) -> None:
    kernel_root = tmp_path / "runtime"
    flows_root = tmp_path / "flows"
    engine1 = FlowEngine(kernel_root=kernel_root, flows_root=flows_root)
    wfi, _ = engine1.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    # Node 'a' conforme : checkpointé, le contrat de 'b' est présenté mais son
    # advance_step n'est délibérément pas encore appelé (voir engine.resume) —
    # c'est exactement l'état laissé par un crash entre deux nodes.
    step1 = engine1.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert step1.node_id == "b"

    kernel_check = RuntimeKernel(kernel_root)
    wfi_after_crash = kernel_check.get_instance(wfi.id)
    assert wfi_after_crash is not None
    assert wfi_after_crash.status is WorkflowStatus.CHECKPOINTED

    # « Nouveau process » : un second FlowEngine sur la même racine, sans rien
    # de l'état en mémoire du premier.
    engine2 = FlowEngine(kernel_root=kernel_root, flows_root=flows_root)
    status = engine2.status(wfi.id)
    assert status.current_node == "b"

    resumed = engine2.resume(wfi.id, output={"pins": {"out": {"contract": "c2"}}})
    assert resumed.ok
    assert resumed.node_id == "c"

    # 'b' n'a été démarré (STEP_STARTED) qu'une seule fois, par ce second appel.
    events = kernel_check.get_run_events(wfi.id)
    step_started_b = [
        e for e in events if e.event_type is RunEventType.STEP_STARTED and e.payload.get("step_id") == "b"
    ]
    assert len(step_started_b) == 1


def test_abort_is_terminal(tmp_path: Path, bp: Path) -> None:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    aborted = engine.abort(wfi.id, reason="test")
    assert aborted.status is WorkflowStatus.ABORTED
    assert aborted.abort_reason == "test"

    status = engine.status(wfi.id)
    assert status.status == WorkflowStatus.ABORTED.value
    assert status.current_node is None

    with pytest.raises(GrimoireRuntimeError):
        engine.resume(wfi.id, output={"pins": {}})


def test_step_events_reach_the_task_timeline(tmp_path: Path, bp: Path) -> None:
    """missions/trace.py lit déjà STEP_*: ce test prouve que le kernel les émet enfin."""
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c2"}}})
    engine.resume(wfi.id, output={"pins": {}})

    kernel_root = tmp_path / "runtime"
    events = RuntimeKernel(kernel_root).get_run_events(wfi.id)
    kinds = {e.event_type for e in events}
    assert RunEventType.STEP_STARTED in kinds
    assert RunEventType.STEP_COMPLETED in kinds

    timeline = build_task_timeline(tmp_path, wfi.task_id, kernel_root=kernel_root)
    summaries = [e.summary for e in timeline.entries if e.source == "runtime"]
    assert any("étape a — started" in s for s in summaries)
    assert any("étape a — completed" in s for s in summaries)
    assert any("étape c — completed" in s for s in summaries)


# --- #446 : run_id/wfi_id ne tronque plus recipe_id/blueprint_id -----------


def test_run_id_keeps_full_blueprint_id(tmp_path: Path, bp: Path) -> None:
    """``bp`` a l'id ``trois-nodes`` (11 caractères) : rien à tronquer, mais

    ce test verrouille le comportement attendu bout-en-bout, pas seulement
    au niveau du kernel — le ``run_id`` exposé par ``flow run`` est bien le
    ``wfi_id`` construit sur l'id complet."""
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    assert wfi.id == "WFI-trois-nodes-001"
    status = engine.status(wfi.id, include_contract=False)
    assert status.run_id == wfi.id
    assert status.blueprint_id == "trois-nodes"


def test_status_reads_legacy_truncated_run_id(tmp_path: Path, bp: Path) -> None:
    """Rétro-compatibilité : un run déjà persisté sous l'ancien format tronqué

    (#446, ``WFI-asklib-hardening-001`` pour le blueprint ``tasklib-hardening``)
    reste lisible par ``status``/``list_runs`` — la lecture ne dépend que de
    la cohérence entre le fichier de métadonnées et le kernel, jamais de la
    forme ou de la longueur du ``run_id``."""
    engine = _engine(tmp_path)
    ctx = ExecutionContext(
        run_id="RUN-legacy",
        mission_id="MIS-flow-tasklib-hardening",
        task_id="FLOW-tasklib-hardening",
        workflow_instance_id="",
        actor_id="cli",
        host_id="local",
        risk_profile="standard",
    )
    legacy_run_id = "WFI-asklib-hardening-001"
    wfi = engine._kernel.create_instance(ctx, recipe_id="tasklib-hardening", wfi_id=legacy_run_id)
    engine._kernel.start(wfi.id, ctx)
    engine._save_meta(
        FlowRunMeta(
            run_id=legacy_run_id,
            blueprint_id="tasklib-hardening",
            blueprint_path=str(bp),
            order=("a", "b", "c"),
            created_at="2026-09-11T00:00:00+00:00",
        )
    )

    assert legacy_run_id in engine.list_run_ids()
    status = engine.status(legacy_run_id, include_contract=False)
    assert status.run_id == legacy_run_id
    assert status.blueprint_id == "tasklib-hardening"
    runs = engine.list_runs()
    assert any(r.run_id == legacy_run_id for r in runs)
