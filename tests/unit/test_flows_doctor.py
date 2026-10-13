"""W1-03b : ``flow doctor`` rejoue le journal d'un run et nomme l'invariant rompu.

Le journal d'événements fait autorité ; le docteur est en lecture seule et ne
fait que PROPOSER une réparation. Les journaux altérés sont fabriqués en
réécrivant les fichiers du kernel, comme le ferait un crash ou une main
malheureuse — jamais en appelant une API qui les corromprait.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows import engine as flows_engine
from grimoire.flows.doctor import diagnose
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import RunEventType, WorkflowStatus

runner = CliRunner()

OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}
OUT_C = {"pins": {"out": {"contract": "c3"}}}


def _node(node_id: str, pins: list[dict[str, str]]) -> dict[str, Any]:
    return {"id": node_id, "kind": "pattern", "ref": "ORC-01", "label": node_id.upper(), "pins": pins}


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes",
        "name": "Trois nodes",
        "nodes": [
            _node("a", [{"id": "out", "direction": "out", "contract": "c1"}]),
            _node("b", [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}]),
            _node("c", [{"id": "in", "direction": "in", "contract": "c2"}, {"id": "out", "direction": "out", "contract": "c3"}]),
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
    return FlowEngine(
        kernel_root=tmp_path / "_grimoire-runtime-output" / "runtime",
        flows_root=tmp_path / "_grimoire-runtime-output" / "flows",
        project_root=tmp_path,
    )


def _run(tmp_path: Path, outputs: list[dict[str, Any]]) -> tuple[FlowEngine, str]:
    engine = _engine(tmp_path)
    executor = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(_blueprint(tmp_path), executor=executor)
    for output in outputs:
        engine.resume(wfi.id, output=output, executor=executor)
    return engine, wfi.id


def _runtime(tmp_path: Path) -> Path:
    return tmp_path / "_grimoire-runtime-output" / "runtime"


def _codes(report: Any) -> set[str]:
    return {v.code for v in report.violations}


def _rewrite_lines(path: Path, edit: Any) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── Un run sain ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outputs", [[], [OUT_A], [OUT_A, OUT_B], [OUT_A, OUT_B, OUT_C]])
def test_a_healthy_run_is_ok_at_every_step(tmp_path: Path, outputs: list[dict[str, Any]]) -> None:
    engine, run_id = _run(tmp_path, outputs)

    report = engine.doctor(run_id)

    assert report.ok, report.to_dict()
    assert report.violations == ()
    assert report.authority == "events"
    assert report.checked is not None and report.checked["nodes"] == 3


def test_a_refused_then_corrected_run_is_ok(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [{"pins": {}}, OUT_A, OUT_B, OUT_C])

    assert engine.doctor(run_id).ok


def test_an_aborted_run_is_ok(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    engine.abort(run_id, reason="test")

    assert engine.doctor(run_id).ok


def test_the_report_states_what_it_did_not_evaluate(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])

    report = engine.doctor(run_id)

    assert any("blueprint" in note for note in report.unevaluated)
    assert report.to_dict()["unevaluated"]


def test_the_doctor_never_writes(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    _rewrite_lines(_runtime(tmp_path) / "run_events.jsonl", lambda lines: lines[:-1])
    before = _tree(tmp_path / "_grimoire-runtime-output")

    report = engine.doctor(run_id)

    assert not report.ok
    assert _tree(tmp_path / "_grimoire-runtime-output") == before


def test_an_unknown_run_raises(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    with pytest.raises(GrimoireRuntimeError, match="introuvable"):
        engine.doctor("WFI-nope-001")


# ── Un journal altéré ───────────────────────────────────────────────────────


def test_any_single_deleted_event_of_a_finished_run_is_detected(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B, OUT_C])
    events_path = _runtime(tmp_path) / "run_events.jsonl"
    original = events_path.read_text(encoding="utf-8").splitlines()
    assert len(original) == 11

    undetected: list[str] = []
    for index in range(len(original)):
        events_path.write_text("\n".join(original[:index] + original[index + 1 :]) + "\n", encoding="utf-8")
        report = engine.doctor(run_id)
        if report.ok:
            undetected.append(json.loads(original[index])["event_type"] + f"#{index}")
    events_path.write_text("\n".join(original) + "\n", encoding="utf-8")

    assert undetected == []
    assert engine.doctor(run_id).ok


def test_a_deleted_step_completed_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B])

    def drop_first_completed(lines: list[str]) -> list[str]:
        out, dropped = [], False
        for line in lines:
            if not dropped and json.loads(line)["event_type"] == RunEventType.STEP_COMPLETED.value:
                dropped = True
                continue
            out.append(line)
        return out

    _rewrite_lines(_runtime(tmp_path) / "run_events.jsonl", drop_first_completed)

    report = engine.doctor(run_id)

    assert not report.ok
    assert "step_completed_missing" in _codes(report)
    assert "multiple_open_nodes" in _codes(report)
    missing = next(v for v in report.violations if v.code == "step_completed_missing")
    assert missing.node_id == "a"
    assert missing.repair


def test_an_instance_ahead_of_the_journal_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    kernel = RuntimeKernel(_runtime(tmp_path))
    wfi = kernel.get_instance(run_id)
    assert wfi is not None
    from dataclasses import replace

    kernel._save_instance(replace(wfi, status=WorkflowStatus.COMPLETED))

    report = engine.doctor(run_id)

    assert not report.ok
    assert "instance_ahead_of_journal" in _codes(report)
    ahead = next(v for v in report.violations if v.code == "instance_ahead_of_journal")
    assert "completed" in ahead.detail and "checkpointed" in ahead.detail


def test_an_instance_behind_the_journal_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B, OUT_C])
    kernel = RuntimeKernel(_runtime(tmp_path))
    wfi = kernel.get_instance(run_id)
    assert wfi is not None
    from dataclasses import replace

    kernel._save_instance(replace(wfi, status=WorkflowStatus.RUNNING))

    assert "instance_behind_journal" in _codes(engine.doctor(run_id))


def test_a_meta_order_that_contradicts_the_journal_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B])
    meta_path = tmp_path / "_grimoire-runtime-output" / "flows" / f"{run_id}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["order"] = ["b", "a", "c"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    codes = _codes(engine.doctor(run_id))

    assert "step_skipped" in codes
    assert "checkpoint_order_mismatch" in codes


def test_a_duplicated_checkpoint_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    path = _runtime(tmp_path) / "checkpoints.jsonl"
    _rewrite_lines(path, lambda lines: [*lines, lines[-1]])

    assert "checkpoint_duplicate" in _codes(engine.doctor(run_id))


def test_checkpoint_refs_that_do_not_match_the_file_are_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    kernel = RuntimeKernel(_runtime(tmp_path))
    wfi = kernel.get_instance(run_id)
    assert wfi is not None
    from dataclasses import replace

    kernel._save_instance(replace(wfi, checkpoint_refs=()))

    assert "checkpoint_refs_mismatch" in _codes(engine.doctor(run_id))


def test_a_run_whose_meta_was_never_written_is_an_orphan(tmp_path: Path) -> None:
    """Crash pendant ``run`` avant la méta : l'instance existe, l'hôte n'a jamais reçu d'id."""
    engine, run_id = _run(tmp_path, [])
    (tmp_path / "_grimoire-runtime-output" / "flows" / f"{run_id}.json").unlink()

    report = engine.doctor(run_id)

    assert not report.ok
    assert _codes(report) == {"meta_missing"}
    assert any("journal" in note for note in report.unevaluated)


def test_a_run_without_instance_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A])
    (_runtime(tmp_path) / "instances.jsonl").write_text("", encoding="utf-8")

    assert _codes(engine.doctor(run_id)) == {"instance_missing"}


def test_a_fully_checkpointed_run_that_never_completed_is_named(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B, OUT_C])
    # Retire la clôture du journal ET ramène l'instance à CHECKPOINTED : l'état
    # qu'un crash entre le dernier checkpoint et ``complete`` laisse.
    _rewrite_lines(_runtime(tmp_path) / "run_events.jsonl", lambda lines: lines[:-1])
    kernel = RuntimeKernel(_runtime(tmp_path))
    wfi = kernel.get_instance(run_id)
    assert wfi is not None
    from dataclasses import replace

    kernel._save_instance(replace(wfi, status=WorkflowStatus.CHECKPOINTED))

    report = engine.doctor(run_id)

    assert _codes(report) == {"completion_pending"}
    assert report.violations[0].repair


def test_every_violation_proposes_a_repair_and_none_is_applied(tmp_path: Path) -> None:
    engine, run_id = _run(tmp_path, [OUT_A, OUT_B])
    _rewrite_lines(_runtime(tmp_path) / "run_events.jsonl", lambda lines: lines[1:])
    before = _tree(tmp_path / "_grimoire-runtime-output")

    report = engine.doctor(run_id)

    assert not report.ok and report.violations
    assert all(v.repair for v in report.violations), report.to_dict()
    assert report.repairs
    assert _tree(tmp_path / "_grimoire-runtime-output") == before


def test_diagnose_is_pure_and_names_a_journal_that_skips_a_node() -> None:
    from grimoire.flows.schemas import FlowRunMeta
    from grimoire.runtime.schemas import RunEvent

    def event(kind: RunEventType, step: str | None = None) -> RunEvent:
        return RunEvent(
            id=f"evt-{kind.value}-{step}",
            run_id="RUN-x",
            mission_id="MIS-x",
            task_id="TASK-x",
            workflow_instance_id="WFI-x-001",
            event_type=kind,
            actor_id="cli",
            host_id="local",
            created_at="2026-10-10T00:00:00+00:00",
            payload={"step_id": step} if step else {},
            span_id="span-x",
        )

    meta = FlowRunMeta(run_id="WFI-x-001", blueprint_id="x", blueprint_path="x", order=("a", "b"), created_at="t")
    events = [
        event(RunEventType.WORKFLOW_STARTED),
        event(RunEventType.STEP_STARTED, "b"),
    ]

    report = diagnose(run_id="WFI-x-001", meta=meta, instance=None, events=events, checkpoints=[], current_node=None)

    assert _codes(report) == {"instance_missing"}
    assert not report.ok


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli_run(tmp_path: Path) -> str:
    bp = _blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return str(json.loads(result.output)["run_id"])


def test_cli_flow_doctor_json_on_a_healthy_run(tmp_path: Path) -> None:
    run_id = _cli_run(tmp_path)

    result = runner.invoke(app, ["flow", "doctor", run_id, "--json", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["run_id"] == run_id
    assert body["violations"] == []
    assert body["authority"] == "events"


def test_cli_flow_doctor_names_the_violation_and_exits_non_zero(tmp_path: Path) -> None:
    run_id = _cli_run(tmp_path)
    out = tmp_path / "a.json"
    out.write_text(json.dumps(OUT_A), encoding="utf-8")
    resumed = runner.invoke(
        app, ["flow", "resume", run_id, "--result", str(out), "--project-root", str(tmp_path)]
    )
    assert resumed.exit_code == 0, resumed.output
    _rewrite_lines(_runtime(tmp_path) / "run_events.jsonl", lambda lines: [ln for ln in lines if "step.completed" not in ln])

    result = runner.invoke(app, ["flow", "doctor", run_id, "--json", "--project-root", str(tmp_path)])

    assert result.exit_code == 1, result.output
    body = json.loads(result.output)
    assert body["ok"] is False
    assert "step_completed_missing" in {v["code"] for v in body["violations"]}
    assert body["repairs"]


def test_cli_flow_doctor_unknown_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "doctor", "WFI-nope-001", "--json", "--project-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "introuvable" in json.loads(result.output)["error"]


def test_cli_flow_doctor_text_output(tmp_path: Path) -> None:
    run_id = _cli_run(tmp_path)

    result = runner.invoke(app, ["flow", "doctor", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.output


# ── Frontière PyO3 ──────────────────────────────────────────────────────────


class _StrictCore:
    """Double du cœur Rust : encode STRICTEMENT en UTF-8 tout ``str`` reçu, comme PyO3."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    @staticmethod
    def _strict(value: Any) -> None:
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, (list, tuple)):
            for item in value:
                _StrictCore._strict(item)

    def is_flow_terminal_status(self, status: str) -> bool:
        self._strict(status)
        return status in ("completed", "verified", "aborted", "refused")

    def current_node(
        self,
        status: str,
        order: list[str],
        last_step_failed: str | None,
        has_checkpoint: bool,
        checkpoint_pending_head: str | None,
        last_step_started: str | None,
    ) -> str | None:
        args = (status, order, last_step_failed, has_checkpoint, checkpoint_pending_head, last_step_started)
        self._strict(args)
        self.calls.append(args)
        if status == "blocked":
            return last_step_failed if last_step_failed is not None else (order[0] if order else None)
        if status == "checkpointed":
            return checkpoint_pending_head if has_checkpoint else (order[0] if order else None)
        return last_step_started if last_step_started is not None else (order[0] if order else None)


@pytest.mark.parametrize("crash_after", [15, 6])
def test_the_arguments_handed_to_the_rust_core_survive_strict_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: int
) -> None:
    """Les états de reprise (RUNNING périmé, BLOCKED sans événement) passent des ``str`` au cœur Rust."""
    from . import test_flows_crash_boundaries as matrix

    bp = matrix._blueprint(tmp_path)
    _log, crashed, known = matrix._play(tmp_path, bp, crash_after)
    assert crashed is not None and known is not None
    python_engine = matrix._engine(tmp_path)
    wfi = python_engine._kernel.get_instance(known)
    assert wfi is not None
    order = ["a", "b", "c"]
    expected = python_engine._current_node(wfi, order)

    core = _StrictCore()
    monkeypatch.setenv("GRIMOIRE_FLOWS_BACKEND", "rust")
    monkeypatch.setattr(flows_engine, "_rust_core", core)

    assert python_engine._current_node(wfi, order) == expected
    assert core.calls, "le cœur Rust doit avoir été consulté"
