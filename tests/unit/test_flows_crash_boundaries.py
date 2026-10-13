"""W1-03b : un crash à chaque frontière d'écriture, puis ``resume`` — jamais un node sauté ni rejoué.

Un run vit dans trois fichiers sans transaction commune (``instances.jsonl``,
``checkpoints.jsonl``/``run_events.jsonl`` du kernel, méta de run du moteur).
Chaque écriture est atomique (W1-02) mais rien ne garantit la cohérence ENTRE
les écritures : un crash entre deux d'entre elles laisse un état que seul le
journal d'événements sait relire.

Le test rejoue un scénario complet (refus compris) et lève un crash APRÈS la
k-ième écriture, pour chaque k. Un hôte neuf (nouveau ``FlowEngine``, comme un
nouveau process) reprend en renvoyant l'appel interrompu puis la suite.

Défauts reproduits sur la base (W1-03) :

- crash après la transition RUNNING d'un node suivant, avant son
  ``STEP_STARTED`` : le node courant est relu depuis le dernier ``STEP_STARTED``
  — le node PRÉCÉDENT, déjà checkpointé — qui est rejoué ;
- crash après le checkpoint du dernier node, avant ``complete`` : plus aucun
  node courant, le run reste CHECKPOINTED à jamais ;
- crash entre ``fail_step`` et son événement : le node bloqué est relu comme
  le premier du run ;
- checkpoints et ``STEP_COMPLETED`` dupliqués quand le même node est refranchi.

Défauts de revue (W1-03b-S1, S2) :

- ``resume`` répare sans le dire quand le pilote est ``--executor dispatch`` (et
  les pilotes automatiques de ``genres``/``upgrade-flow``) : la note ne vivait
  que dans ``ResumeOutcome.repaired``, jamais rendue par ``FlowDispatchOutcome`` ;
- le docteur, dérivant le node ouvert des seuls événements, désignait le node
  déjà checkpointé comme courant et proposait de revenir en arrière dans les
  fenêtres d'un checkpoint à moitié écrit — alors que ``resume`` avance.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import grimoire.flows.engine as flows_engine
import grimoire.runtime.kernel as runtime_kernel
from grimoire.flows.engine import FlowEngine, rust_backend_available
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import RunEventType, WorkflowStatus

OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}
#: Chaque node porte une pin de sortie : une sortie renvoyée pour un node déjà
#: franchi est alors toujours invalide pour le suivant, donc reconnaissable comme
#: rejeu. Un node sans pin de sortie accepte n'importe quelle sortie bien formée
#: et rend le rejeu indécidable — limite écrite par W1-03, hors de cette matrice.
OUT_C = {"pins": {"out": {"contract": "c3"}}}
BAD_A = {"pins": {}}

#: Le scénario de l'hôte : démarrer, se faire refuser le node a, puis dérouler.
CALLS: list[tuple[str, dict[str, Any] | None]] = [
    ("run", None),
    ("resume", BAD_A),
    ("resume", OUT_A),
    ("resume", OUT_B),
    ("resume", OUT_C),
]

#: Les frontières d'écriture du scénario ci-dessus, dans l'ordre. Épinglées :
#: ``test_the_boundary_list_matches_the_scenario`` échoue si une écriture est
#: ajoutée ou retirée sans que cette liste suive (sinon une frontière nouvelle
#: échapperait à la matrice).
BOUNDARIES = [
    "instances",  # create_instance
    "instances",  # start -> RUNNING
    "event:workflow.started",
    "event:step.started",
    "meta",
    "instances",  # fail_step -> BLOCKED
    "event:step.failed",
    "instances",  # advance_step -> RUNNING
    "event:step.started",
    "checkpoint",  # a
    "instances",  # -> CHECKPOINTED
    "instances",  # checkpoint_refs
    "event:checkpoint.saved",
    "event:step.completed",
    "instances",  # advance_step b
    "event:step.started",
    "checkpoint",  # b
    "instances",
    "instances",
    "event:checkpoint.saved",
    "event:step.completed",
    "instances",  # advance_step c
    "event:step.started",
    "checkpoint",  # c
    "instances",
    "instances",
    "event:checkpoint.saved",
    "event:step.completed",
    "instances",  # complete -> RUNNING
    "instances",  # complete -> COMPLETED
    "event:workflow.completed",
]

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_flows_core not installed — build it locally (maturin develop) or run the Rust CI job",
)
BACKENDS = ["python", pytest.param("rust", marks=requires_rust_core)]


class _Crash(BaseException):
    """Le process meurt : ni ``except Exception`` ni ``finally`` de l'appelant ne doivent l'amortir."""


def _node(node_id: str, pins: list[dict[str, str]], ref: str = "ORC-01") -> dict[str, Any]:
    return {"id": node_id, "kind": "pattern", "ref": ref, "label": node_id.upper(), "pins": pins}


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes",
        "name": "Trois nodes",
        "nodes": [
            _node("a", [{"id": "out", "direction": "out", "contract": "c1"}]),
            {
                **_node(
                    "b",
                    [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}],
                    "demo/demo-node",
                ),
                "kind": "extension-node",
            },
            _node(
                "c",
                [{"id": "in", "direction": "in", "contract": "c2"}, {"id": "out", "direction": "out", "contract": "c3"}],
                "QUA-04",
            ),
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


@contextmanager
def _crash_after(limit: int | None, log: list[str]) -> Iterator[None]:
    """Compte les écritures d'état ; lève ``_Crash`` juste APRÈS la ``limit``-ième."""
    originals = (
        runtime_kernel.atomic_write,
        flows_engine.atomic_write,
        RuntimeKernel._append_event,
        RuntimeKernel._append_checkpoint,
    )

    def tick(label: str) -> None:
        log.append(label)
        if limit is not None and len(log) == limit:
            raise _Crash(label)

    def kernel_write(path: Path, data: str, *args: Any, **kwargs: Any) -> None:
        originals[0](path, data, *args, **kwargs)
        tick("instances")

    def meta_write(path: Path, data: str, *args: Any, **kwargs: Any) -> None:
        originals[1](path, data, *args, **kwargs)
        tick("meta")

    def append_event(self: RuntimeKernel, event: Any) -> None:
        originals[2](self, event)
        tick(f"event:{event.event_type.value}")

    def append_checkpoint(self: RuntimeKernel, chk: Any) -> None:
        originals[3](self, chk)
        tick("checkpoint")

    runtime_kernel.atomic_write = kernel_write  # type: ignore[assignment]
    flows_engine.atomic_write = meta_write  # type: ignore[assignment]
    RuntimeKernel._append_event = append_event  # type: ignore[method-assign]
    RuntimeKernel._append_checkpoint = append_checkpoint  # type: ignore[method-assign]
    try:
        yield
    finally:
        runtime_kernel.atomic_write = originals[0]  # type: ignore[assignment]
        flows_engine.atomic_write = originals[1]  # type: ignore[assignment]
        RuntimeKernel._append_event = originals[2]  # type: ignore[method-assign]
        RuntimeKernel._append_checkpoint = originals[3]  # type: ignore[method-assign]


def _call(engine: FlowEngine, bp: Path, run_id: str | None, call: tuple[str, dict[str, Any] | None]) -> Any:
    executor = InteractiveNodeExecutor(stream=io.StringIO())
    kind, output = call
    if kind == "run":
        wfi, _ = engine.run(bp, executor=executor)
        return wfi.id
    assert run_id is not None and output is not None
    return engine.resume(run_id, output=output, executor=executor)


def _play(tmp_path: Path, bp: Path, limit: int | None) -> tuple[list[str], int | None, str | None]:
    """Joue le scénario. Rend (écritures, index de l'appel interrompu, run_id connu de l'hôte)."""
    log: list[str] = []
    engine = _engine(tmp_path)
    run_id: str | None = None
    with _crash_after(limit, log):
        for index, call in enumerate(CALLS):
            try:
                result = _call(engine, bp, run_id, call)
            except _Crash:
                return log, index, run_id
            if call[0] == "run":
                run_id = result
    return log, None, run_id


def _recover(tmp_path: Path, bp: Path, crashed: int, known_run_id: str | None) -> tuple[FlowEngine, str]:
    """Un hôte neuf renvoie l'appel interrompu puis la suite du scénario."""
    engine = _engine(tmp_path)
    run_id = known_run_id
    start = crashed
    if run_id is None:
        ids = engine.list_run_ids()
        if ids:  # run() est mort après la méta : le run est visible et repris
            run_id = ids[0]
            start = max(crashed, 1)
        else:  # run() est mort avant la méta : l'hôte n'a pas d'id, il relance
            run_id = _call(engine, bp, None, CALLS[0])
            start = 1
    for index in range(start, len(CALLS)):
        outcome = _call(engine, bp, run_id, CALLS[index])
        refused = CALLS[index][1] is BAD_A
        assert outcome.ok is not refused, (index, outcome)
    return engine, run_id


def _labelled(boundaries: list[str]) -> list[Any]:
    return [pytest.param(i + 1, id=f"{i + 1:02d}-{label}") for i, label in enumerate(boundaries)]


def test_the_boundary_list_matches_the_scenario(tmp_path: Path) -> None:
    log, crashed, _ = _play(tmp_path, _blueprint(tmp_path), None)

    assert crashed is None
    assert log == BOUNDARIES


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("limit", _labelled(BOUNDARIES))
def test_crash_then_resume_neither_skips_nor_replays_a_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, limit: int
) -> None:
    monkeypatch.setenv("GRIMOIRE_FLOWS_BACKEND", backend)
    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, limit)
    assert crashed is not None, "le crash doit interrompre un appel du scénario"

    engine, run_id = _recover(tmp_path, bp, crashed, known)

    kernel = RuntimeKernel(tmp_path / "runtime")
    assert engine.status(run_id).status == WorkflowStatus.COMPLETED.value
    assert [c.step_id for c in kernel.list_checkpoints(run_id)] == ["a", "b", "c"]
    events = kernel.get_run_events(run_id)
    completed = [e.payload["step_id"] for e in events if e.event_type is RunEventType.STEP_COMPLETED]
    assert completed == ["a", "b", "c"]
    assert sum(1 for e in events if e.event_type is RunEventType.WORKFLOW_COMPLETED) == 1
    assert sum(1 for e in events if e.event_type is RunEventType.CHECKPOINT_SAVED) == 3
    refs = kernel.get_instance(run_id).checkpoint_refs  # type: ignore[union-attr]
    assert refs == tuple(c.id for c in kernel.list_checkpoints(run_id))


@pytest.mark.parametrize("limit", _labelled(BOUNDARIES))
def test_crash_then_resume_leaves_a_run_the_doctor_calls_healthy(tmp_path: Path, limit: int) -> None:
    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, limit)
    assert crashed is not None

    engine, run_id = _recover(tmp_path, bp, crashed, known)

    report = engine.doctor(run_id)
    assert report.ok, report.to_dict()


def test_status_after_a_crash_names_the_node_the_host_must_answer(tmp_path: Path) -> None:
    """``status`` (lecture seule) ne doit pas désigner un node déjà franchi."""
    bp = _blueprint(tmp_path)
    # 15 = après la transition RUNNING du node b, avant son STEP_STARTED.
    _log, crashed, known = _play(tmp_path, bp, 15)
    assert crashed == 3 and known is not None
    engine = _engine(tmp_path)

    view = engine.status(known)

    assert view.current_node == "b"
    assert view.completed_nodes == ("a",)
    assert view.pending_nodes == ("c",)


def test_a_fully_checkpointed_run_is_finished_by_resending_the_last_output(tmp_path: Path) -> None:
    bp = _blueprint(tmp_path)
    # 28 = après le dernier STEP_COMPLETED, avant complete().
    _log, crashed, known = _play(tmp_path, bp, 28)
    assert crashed == 4 and known is not None
    engine = _engine(tmp_path)
    assert engine.status(known).status == WorkflowStatus.CHECKPOINTED.value

    outcome = engine.resume(known, output=OUT_C)

    assert outcome.ok and outcome.finished and outcome.replay
    assert outcome.replay_of == "c"
    assert outcome.repaired, "la reprise doit dire ce qu'elle a complété"
    assert engine.status(known).status == WorkflowStatus.COMPLETED.value


def test_a_fully_checkpointed_run_refuses_an_output_that_was_never_accepted(tmp_path: Path) -> None:
    from grimoire.core.exceptions import GrimoireRuntimeError

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, 28)
    assert crashed == 4 and known is not None
    engine = _engine(tmp_path)

    with pytest.raises(GrimoireRuntimeError, match="tous les nodes"):
        engine.resume(known, output={"pins": {"autre": {"contract": "zz"}}})

    assert engine.status(known).status == WorkflowStatus.CHECKPOINTED.value


def test_a_blocked_node_survives_a_crash_between_the_status_and_its_event(tmp_path: Path) -> None:
    """Crash après ``fail_step`` sur le node b : le node bloqué reste b, pas le premier du run."""
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)
    executor = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(bp, executor=executor)
    engine.resume(wfi.id, output=OUT_A, executor=executor)
    # resume(BAD) sur b : advance_step (instances, step.started) puis fail_step
    # (instances BLOCKED, step.failed) -> on coupe après le BLOCKED, avant l'événement.
    with _crash_after(3, []), pytest.raises(_Crash):
        engine.resume(wfi.id, output={"pins": {}}, executor=executor)

    reborn = _engine(tmp_path)
    view = reborn.status(wfi.id)

    assert view.status == WorkflowStatus.BLOCKED.value
    assert view.current_node == "b"
    outcome = reborn.resume(wfi.id, output=OUT_B, executor=executor)
    assert outcome.ok and outcome.node_id == "c"
    assert [c.step_id for c in RuntimeKernel(tmp_path / "runtime").list_checkpoints(wfi.id)] == ["a", "b"]


# ── W1-03b-S1 : une réparation n'est jamais silencieuse, pilote dispatch compris ──

#: La sortie que l'exécuteur doublé « produit » pour chaque node.
_DISPATCH_OUTPUTS = {"a": OUT_A, "b": OUT_B, "c": OUT_C}


@pytest.fixture
def scripted_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remplace la cascade de ``DispatchExecutor`` par une sortie conforme par node."""
    from grimoire.flows.dispatch_executor import DispatchExecutor
    from grimoire.flows.schemas import NodeExecutionResult

    def execute_plain(self: DispatchExecutor, contract: Any, context_pack: dict[str, Any]) -> NodeExecutionResult:
        result = NodeExecutionResult(pending=False, output=_DISPATCH_OUTPUTS[contract.node_id])
        self.last_result = result
        return result

    monkeypatch.setattr(DispatchExecutor, "_execute_plain", execute_plain)


#: 10 = checkpoint du node a écrit, statut jamais changé : ``resume`` complète l'écriture.
_REPAIRING_BOUNDARY = 10


def test_resume_with_dispatch_reports_the_repair_it_performed(tmp_path: Path, scripted_dispatch: None) -> None:
    from grimoire.flows.dispatch_executor import resume_with_dispatch

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, _REPAIRING_BOUNDARY)
    assert crashed is not None and known is not None

    outcome = resume_with_dispatch(_engine(tmp_path), known, project_root=tmp_path)

    assert outcome.status == "finished"
    body = outcome.to_dict()
    assert body["repaired"], "la réparation écrite par resume doit figurer dans le rapport du pilote"
    assert "checkpoint a" in " ".join(body["repaired"])


def test_resume_with_dispatch_on_a_healthy_run_reports_no_repair(tmp_path: Path, scripted_dispatch: None) -> None:
    from grimoire.flows.dispatch_executor import resume_with_dispatch

    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))

    outcome = resume_with_dispatch(engine, wfi.id, project_root=tmp_path)

    assert outcome.status == "finished"
    assert outcome.to_dict()["repaired"] == []


def _crashed_project(root: Path) -> str:
    """Un projet dont le run a planté à la frontière 10, rangé là où la CLI le cherche."""
    import shutil

    bp = _blueprint(root)
    _log, crashed, known = _play(root, bp, _REPAIRING_BOUNDARY)
    assert crashed is not None and known is not None
    output = root / "_grimoire-runtime-output"
    output.mkdir()
    shutil.move(str(root / "runtime"), str(output / "runtime"))
    shutil.move(str(root / "flows"), str(output / "flows"))
    return known


@pytest.mark.parametrize("fmt", ["human", "json"])
def test_cli_resume_dispatch_prints_the_repair(tmp_path: Path, scripted_dispatch: None, fmt: str) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    run_id = _crashed_project(tmp_path)
    args = ["flow", "resume", run_id, "--executor", "dispatch", "--project-root", str(tmp_path)]

    result = CliRunner().invoke(app, ["--output", "json", *args] if fmt == "json" else args)

    assert result.exit_code == 0, result.output
    if fmt == "json":
        assert json.loads(result.output)["repaired"]
    else:
        assert "réparé" in result.output
        assert "checkpoint a" in result.output


def test_the_composite_child_driver_logs_the_repair_it_triggers(
    tmp_path: Path, scripted_dispatch: None, caplog: pytest.LogCaptureFixture
) -> None:
    """``genres`` appelle ``resume(allow_replay=False)`` sans rendre le rapport : le journal de logs le dit."""
    import logging

    from grimoire.flows.dispatch_executor import DispatchExecutor
    from grimoire.flows.genres import _drive_composite_child
    from grimoire.flows.schemas import NodeExecutionResult

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, _REPAIRING_BOUNDARY)
    assert crashed is not None and known is not None
    engine = _engine(tmp_path)
    executor = DispatchExecutor(project_root=tmp_path, blueprint_path=bp, engine=engine)

    with caplog.at_level(logging.WARNING, logger="grimoire.flows.engine"):
        status, *_ = _drive_composite_child(
            engine, known, executor, NodeExecutionResult(pending=False, output=OUT_B), max_cost_usd=None
        )

    assert status == "finished"
    assert any(known in r.getMessage() and "checkpoint a" in r.getMessage() for r in caplog.records)


def test_log_repairs_is_silent_without_a_repair(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from grimoire.flows.engine import log_repairs

    with caplog.at_level(logging.WARNING, logger="grimoire.flows.engine"):
        log_repairs("WFI-x-001", ())
    assert not caplog.records


# ── W1-03b-S2 : le docteur ne propose que de rouler en avant dans une écriture interrompue ──

#: Les fenêtres où un checkpoint est écrit mais pas encore complet, pour les trois nodes.
_INTERRUPTED_CHECKPOINT_BOUNDARIES = [10, 11, 12, 13, 17, 18, 19, 20, 24, 25, 26, 27]


@pytest.mark.parametrize("limit", _INTERRUPTED_CHECKPOINT_BOUNDARIES)
def test_the_doctor_only_proposes_to_roll_forward_in_an_interrupted_checkpoint(tmp_path: Path, limit: int) -> None:
    from grimoire.flows.doctor import _RESUME

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, limit)
    assert crashed is not None and known is not None
    engine = _engine(tmp_path)

    report = engine.doctor(known)

    assert not report.ok, "un checkpoint à moitié écrit est une violation, jamais un « ok »"
    assert _RESUME in report.repairs
    for repair in report.repairs:
        assert "flow resume" in repair, f"réparation qui ne roule pas en avant : {repair}"
    codes = {v.code for v in report.violations}
    assert not codes & {"current_node_mismatch", "instance_ahead_of_journal", "status_conflict"}, report.to_dict()


@pytest.mark.parametrize("limit", _INTERRUPTED_CHECKPOINT_BOUNDARIES)
def test_the_doctor_repair_is_the_one_resume_performs(tmp_path: Path, limit: int) -> None:
    """Le docteur annonce `flow resume` : appliqué, il ramène bien un run sain (la prédiction se vérifie)."""
    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, limit)
    assert crashed is not None and known is not None

    _engine_after, run_id = _recover(tmp_path, bp, crashed, known)

    assert _engine(tmp_path).doctor(run_id).ok


# ── W1-03b-SK1-01 : `flow resume` ne répare que le DERNIER checkpoint d'un run en cours ──


def _play_to(tmp_path: Path, bp: Path, *, finished: bool) -> tuple[FlowEngine, str, list[dict[str, Any]]]:
    """Un run a→b→c, terminé ou arrêté après b ; rend aussi les sorties déjà envoyées."""
    engine = _engine(tmp_path)
    executor = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(bp, executor=executor)
    sent = [OUT_A, OUT_B, *([OUT_C] if finished else [])]
    for output in sent:
        assert engine.resume(wfi.id, output=output, executor=executor).ok
    return engine, wfi.id, sent


def _drop_event(tmp_path: Path, event_type: str, step_id: str) -> None:
    path = tmp_path / "runtime" / "run_events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [
        line
        for line in lines
        if not (json.loads(line)["event_type"] == event_type and json.loads(line)["payload"].get("step_id") == step_id)
    ]
    assert len(kept) == len(lines) - 1, f"{event_type} de {step_id} introuvable"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _drop_ref(tmp_path: Path, run_id: str, step_id: str) -> None:
    path = tmp_path / "runtime" / "instances.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    touched = 0
    for row in rows:
        if row.get("id") == run_id:
            row["checkpoint_refs"] = [r for r in row["checkpoint_refs"] if not r.endswith(f"-{step_id}")]
            touched += 1
    assert touched
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


_EARLIER_CHECKPOINT_DAMAGES = [
    pytest.param(lambda p, rid: _drop_event(p, "checkpoint.saved", "a"), "checkpoint_event_missing", id="checkpoint.saved"),
    pytest.param(lambda p, rid: _drop_event(p, "step.completed", "a"), "step_completed_missing", id="step.completed"),
    pytest.param(lambda p, rid: _drop_ref(p, rid, "a"), "checkpoint_refs_mismatch", id="checkpoint_refs"),
]


@pytest.mark.parametrize("finished", [True, False], ids=["run-terminé", "run-arrêté-après-b"])
@pytest.mark.parametrize(("damage", "code"), _EARLIER_CHECKPOINT_DAMAGES)
def test_the_doctor_does_not_promise_a_resume_that_cannot_heal_an_earlier_checkpoint(
    tmp_path: Path, finished: bool, damage: Any, code: str
) -> None:
    from grimoire.flows.doctor import _RESUME

    bp = _blueprint(tmp_path)
    engine, run_id, sent = _play_to(tmp_path, bp, finished=finished)
    damage(tmp_path, run_id)
    engine = _engine(tmp_path)

    before = engine.doctor(run_id)

    assert not before.ok
    flagged = [v for v in before.violations if v.code == code]
    assert flagged, before.to_dict()
    assert all(v.repair != _RESUME for v in flagged), "resume ne complète que le dernier checkpoint d'un run en cours"
    assert all("sauvegarde" in v.repair for v in flagged), [v.repair for v in flagged]
    # Si une réparation `resume` reste proposée, l'appliquer doit rendre le run sain.
    if _RESUME in before.repairs:
        engine.resume(run_id, output=sent[-1], executor=InteractiveNodeExecutor(stream=io.StringIO()))
        assert _engine(tmp_path).doctor(run_id).ok


@pytest.mark.parametrize("finished", [True, False], ids=["run-terminé", "run-arrêté-après-b"])
def test_following_the_proposed_repair_never_leaves_the_doctor_red_behind_a_green_resume(
    tmp_path: Path, finished: bool
) -> None:
    """Invariant : une réparation `resume` annoncée est tenue — jamais un run avancé sur un journal toujours altéré."""
    from grimoire.flows.doctor import _RESUME

    bp = _blueprint(tmp_path)
    _engine_unused, run_id, sent = _play_to(tmp_path, bp, finished=finished)
    _drop_event(tmp_path, "checkpoint.saved", "a")
    engine = _engine(tmp_path)

    if _RESUME in engine.doctor(run_id).repairs:
        engine.resume(run_id, output=sent[-1], executor=InteractiveNodeExecutor(stream=io.StringIO()))
        assert _engine(tmp_path).doctor(run_id).ok
    else:
        assert not engine.doctor(run_id).ok


def test_the_doctor_still_proposes_resume_for_the_last_checkpoint_of_a_running_run(tmp_path: Path) -> None:
    """Le correctif ne retire pas la bonne réparation : dernier checkpoint, run en cours."""
    from grimoire.flows.doctor import _RESUME

    bp = _blueprint(tmp_path)
    _engine_unused, run_id, sent = _play_to(tmp_path, bp, finished=False)
    _drop_event(tmp_path, "checkpoint.saved", "b")
    engine = _engine(tmp_path)

    report = engine.doctor(run_id)

    assert [v.repair for v in report.violations if v.code == "checkpoint_event_missing"] == [_RESUME]
    engine.resume(run_id, output=sent[-1], executor=InteractiveNodeExecutor(stream=io.StringIO()))
    assert _engine(tmp_path).doctor(run_id).ok


# ── W1-03b-SK2-01 : ce que le docteur annonce de `resume` est ce que `resume` fait ──

#: Un run avec ces frontières a un identifiant côté hôte ou lisible dans le dossier de flows
#: (les frontières 1 à 4 meurent avant la méta : rien à diagnostiquer, voir « Limites »).
_BOUNDARIES_WITH_A_RUN = [(i + 1, label) for i, label in enumerate(BOUNDARIES) if i + 1 >= 5]


def _denials(doctor: Any) -> set[str]:
    return {doctor._RESTORE_STATUS, doctor._RESTORE_EVENTS, doctor._RESTORE_REFS}


def _repair_claims(report: Any) -> tuple[bool, bool]:
    """(le docteur promet que `resume` soigne, le docteur affirme que `resume` ne soigne pas)."""
    from grimoire.flows import doctor

    repairs = set(report.repairs)
    promised = any("flow resume" in repair and repair not in _denials(doctor) for repair in repairs)
    denied = bool(repairs & _denials(doctor))
    return promised, denied


@pytest.mark.parametrize(
    "limit", [pytest.param(i, id=f"{i:02d}-{label}") for i, label in _BOUNDARIES_WITH_A_RUN]
)
def test_the_doctor_prediction_about_resume_holds_at_every_boundary(tmp_path: Path, limit: int) -> None:
    """Réparation promise (`flow resume`) => `resume` remet le run sain ; refusée (« ne le ferait pas ») => il ne le fait pas."""
    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, limit)
    assert crashed is not None
    run_id = known or _engine(tmp_path).list_run_ids()[0]
    promised, denied = _repair_claims(_engine(tmp_path).doctor(run_id))

    _recover(tmp_path, bp, crashed, known)
    healed = _engine(tmp_path).doctor(run_id).ok

    assert not (promised and not healed), "le docteur promet `flow resume`, le run reste altéré après la reprise"
    assert not (denied and healed), "le docteur affirme que `flow resume` ne répare pas, et il remet le run sain"


def test_the_doctor_proposes_resume_when_the_status_was_reopened_before_its_event(tmp_path: Path) -> None:
    """Frontière 8 : `advance_step` a écrit RUNNING, `step.started` manque, le journal s'arrête sur `step.failed`."""
    from grimoire.flows.doctor import _RESTORE_STATUS

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, 8)
    assert crashed is not None and known is not None

    report = _engine(tmp_path).doctor(known)

    behind = [v for v in report.violations if v.code == "instance_behind_journal"]
    assert len(behind) == 1, report.to_dict()
    assert _RESTORE_STATUS not in report.repairs, "resume reprend ce run : le docteur ne dit pas le contraire"
    assert "flow resume" in behind[0].repair and "ne le ferait pas" not in behind[0].repair, behind[0].repair
    # La promesse se vérifie : la sortie suivante de l'hôte remet le run sain.
    assert _engine(tmp_path).resume(known, output=OUT_A).ok
    assert _engine(tmp_path).doctor(known).ok


@pytest.mark.parametrize("finished", [False, True], ids=["run-arrêté-après-b", "run-terminé-côté-journal"])
def test_the_doctor_keeps_restoring_the_status_when_resume_cannot_realign_it(tmp_path: Path, finished: bool) -> None:
    """Une instance relue CREATED alors que le journal est avancé : `resume` n'y peut rien, la restauration reste dite."""
    from grimoire.core.exceptions import GrimoireRuntimeError
    from grimoire.flows.doctor import _RESTORE_STATUS

    bp = _blueprint(tmp_path)
    _engine_unused, run_id, sent = _play_to(tmp_path, bp, finished=finished)
    path = tmp_path / "runtime" / "instances.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        if row.get("id") == run_id:
            row["status"] = WorkflowStatus.CREATED.value
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    engine = _engine(tmp_path)

    report = engine.doctor(run_id)

    assert _RESTORE_STATUS in report.repairs, report.to_dict()
    with pytest.raises(GrimoireRuntimeError):
        engine.resume(run_id, output=sent[-1], executor=InteractiveNodeExecutor(stream=io.StringIO()))
    assert not _engine(tmp_path).doctor(run_id).ok


# ── W1-03b-SK2-02 : un refus qui suit une réparation la dit ──

#: 24 = checkpoint du dernier node écrit, statut RUNNING : `resume` complète l'écriture avant de juger la sortie.
_REPAIRING_THEN_REFUSING_BOUNDARY = 24
_NEVER_ACCEPTED = {"pins": {"autre": {"contract": "zz"}}}


def test_a_refusal_after_a_repair_names_the_repair(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from grimoire.core.exceptions import GrimoireRuntimeError

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, _REPAIRING_THEN_REFUSING_BOUNDARY)
    assert crashed is not None and known is not None
    engine = _engine(tmp_path)

    with caplog.at_level(logging.WARNING, logger="grimoire.flows.engine"), pytest.raises(GrimoireRuntimeError) as raised:
        engine.resume(known, output=_NEVER_ACCEPTED)

    message = str(raised.value)
    assert "tous les nodes sont déjà validés" in message
    assert "réparé avant ce refus" in message and "checkpoint c" in message, message
    assert any(known in r.getMessage() and "checkpoint c" in r.getMessage() for r in caplog.records)
    # La réparation a bien eu lieu : le refus ne l'annule pas, et l'erreur n'a pas menti.
    assert engine.status(known).status == WorkflowStatus.CHECKPOINTED.value


def test_a_refusal_without_a_repair_keeps_its_message(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from grimoire.core.exceptions import GrimoireRuntimeError

    bp = _blueprint(tmp_path)
    _log, crashed, known = _play(tmp_path, bp, 28)
    assert crashed is not None and known is not None

    with caplog.at_level(logging.WARNING, logger="grimoire.flows.engine"), pytest.raises(GrimoireRuntimeError) as raised:
        _engine(tmp_path).resume(known, output=_NEVER_ACCEPTED)

    assert "réparé" not in str(raised.value)
    assert not caplog.records


def test_every_refusal_raised_after_the_reconcile_goes_through_the_repair_note() -> None:
    """Les trois `raise` de `resume` postérieurs à `_reconcile` passent par `_refusal` : aucun ne perd `repaired_notes`."""
    import inspect
    import re

    source = inspect.getsource(FlowEngine.resume)
    after = source.split("self._reconcile(wfi, ctx)", 1)[1]
    assert not re.search(r"raise GrimoireRuntimeError\(", after), "un refus de resume ignore les réparations déjà écrites"
    assert after.count("raise _refusal(") == 3


def test_refusal_formats_the_repairs_and_logs_them(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    err = flows_engine._refusal("run X : refus", "WFI-x-001", ("checkpoint a : complété", "autre"))

    assert str(err) == "run X : refus (réparé avant ce refus : checkpoint a : complété ; autre)"
    assert flows_engine._refusal("run X : refus", "WFI-x-001", ()).args == ("run X : refus",)
    with caplog.at_level(logging.WARNING, logger="grimoire.flows.engine"):
        flows_engine._refusal("m", "WFI-x-002", ("n",))
    assert any("WFI-x-002" in r.getMessage() for r in caplog.records)
