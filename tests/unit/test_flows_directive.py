"""W2-03 : ``directive_id`` stable ; ``resume`` lié à la directive émise ; quatre kinds.

Défaut sur la base : ``resume(run_id, output)`` déduit le node courant et
valide la sortie contre lui, quelle que soit la directive à laquelle l'hôte
répondait. Une réponse destinée au node N-1 est donc jugée contre N (et, si elle
le viole, écrit ``STEP_FAILED`` et bloque un run sain). Aucune directive
n'avait d'identité.
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
from grimoire.flows.directive import DIRECTIVE_KINDS
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor

OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}


def _blueprint(tmp_path: Path) -> Path:
    def node(i: str, pins: list[dict[str, str]], ref: str) -> dict[str, Any]:
        return {"id": i, "kind": "pattern", "ref": ref, "label": i.upper(), "pins": pins}

    bp = {
        "blueprintVersion": 1,
        "id": "trois",
        "name": "Trois",
        "nodes": [
            node("a", [{"id": "out", "direction": "out", "contract": "c1"}], "ORC-01"),
            node(
                "b",
                [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}],
                "QUA-04",
            ),
            node("c", [{"id": "in", "direction": "in", "contract": "c2"}], "QUA-05"),
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois.blueprint.json"
    path.write_text(json.dumps(bp), encoding="utf-8")
    return path


def _start(tmp_path: Path) -> tuple[FlowEngine, str]:
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    wfi, _ = engine.run(_blueprint(tmp_path), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return engine, wfi.id


def _bytes(tmp_path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in sorted((tmp_path / "runtime").rglob("*"))
        if p.is_file() and p.suffix != ".lock"
    }


def test_directive_id_is_stable_between_two_reads(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    first, second = engine.directive(run_id), engine.directive(run_id)
    assert first.directive_id and first.directive_id == second.directive_id
    assert first.kind == "execute" and first.node_id == "a"
    assert engine.status(run_id).directive == first


def test_directive_id_changes_with_node_and_with_attempt(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    at_a = engine.directive(run_id).directive_id
    refused = engine.resume(run_id, output={"pins": {}})  # contrat violé : nouvelle tentative
    assert not refused.ok
    retry = engine.directive(run_id)
    assert retry.kind == "blocked" and retry.reason == "contract_violation" and retry.attempt == 2
    assert retry.directive_id != at_a
    assert refused.directive == retry
    ok = engine.resume(run_id, output=OUT_A, directive_id=retry.directive_id)
    assert ok.ok and ok.directive is not None and ok.directive.node_id == "b"
    assert ok.directive.directive_id not in (at_a, retry.directive_id)


def test_resume_aimed_at_previous_node_is_refused_without_writing(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    old = engine.directive(run_id).directive_id
    assert engine.resume(run_id, output=OUT_A, directive_id=old).ok
    before = _bytes(tmp_path)

    # Réponse destinée au node a, sortie différente de celle acceptée : jamais jugée contre b.
    with pytest.raises(GrimoireRuntimeError, match="directive_id"):
        engine.resume(run_id, output={"pins": {"out": {"contract": "autre"}}}, directive_id=old)

    assert _bytes(tmp_path) == before
    assert engine.status(run_id).current_node == "b"


def test_unknown_directive_id_is_refused_without_writing(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(GrimoireRuntimeError, match="directive_id"):
        engine.resume(run_id, output=OUT_A, directive_id="0" * 64)
    assert _bytes(tmp_path) == before


def test_replay_of_the_accepted_request_still_succeeds_with_its_directive(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    old = engine.directive(run_id).directive_id
    assert engine.resume(run_id, output=OUT_A, directive_id=old).ok
    before = _bytes(tmp_path)
    again = engine.resume(run_id, output=OUT_A, directive_id=old)  # réponse perdue, même requête
    assert again.ok and again.replay and again.replay_of == "a"
    assert _bytes(tmp_path) == before


def test_resume_without_directive_id_keeps_the_previous_behaviour(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok


def test_final_directive_is_done(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    engine.resume(run_id, output=OUT_A)
    engine.resume(run_id, output=OUT_B)
    last = engine.resume(run_id, output={"pins": {}})
    assert last.finished and last.directive is not None and last.directive.kind == "done"
    assert last.directive.directive_id is None


def test_schema_has_exactly_four_kinds() -> None:
    assert DIRECTIVE_KINDS == ("execute", "checkpoint", "blocked", "done")
    assert len(set(DIRECTIVE_KINDS)) == 4


def test_every_emitted_kind_belongs_to_the_four(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    seen = {engine.directive(run_id).kind}
    engine.resume(run_id, output={"pins": {}})
    seen.add(engine.directive(run_id).kind)
    engine.abort(run_id, reason="test")
    seen.add(engine.directive(run_id).kind)
    assert seen <= set(DIRECTIVE_KINDS) and {"execute", "blocked"} <= seen


runner = CliRunner()


def _enroll(root: Path, profile: str) -> None:
    std = root / "_grimoire" / "standard"
    std.mkdir(parents=True)
    (std / "standard-profile.yaml").write_text(f"profile: {profile}\n", encoding="utf-8")


def test_flow_run_in_governed_profile_without_claim_names_task_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    _enroll(tmp_path, "governed")
    bp = _blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "task claim" in json.loads(result.output)["error"]
    assert not (tmp_path / "_grimoire-runtime-output" / "flows").exists() or not list(
        (tmp_path / "_grimoire-runtime-output" / "flows").glob("*")
    )


def test_flow_run_in_lighter_profile_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    _enroll(tmp_path, "starter")
    bp = _blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["directive"]["kind"] == "execute"


def test_cli_resume_refuses_a_stale_directive_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    bp = _blueprint(tmp_path)
    started = json.loads(
        runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)]).output
    )
    out = tmp_path / "o.json"
    out.write_text(json.dumps(OUT_A), encoding="utf-8")
    args = ["--output", "json", "flow", "resume", started["run_id"], "--result", str(out), "--project-root", str(tmp_path)]
    ok = runner.invoke(app, [*args, "--directive-id", started["directive"]["directive_id"]])
    assert ok.exit_code == 0, ok.output
    out.write_text(json.dumps({"pins": {}}), encoding="utf-8")
    stale = runner.invoke(app, [*args, "--directive-id", started["directive"]["directive_id"]])
    assert stale.exit_code == 1 and "directive_id" in json.loads(stale.output)["error"]


# ── Revue W2-03 (S1 à S3) ───────────────────────────────────────────────────


def _finish(engine: FlowEngine, run_id: str) -> str:
    """Termine le run a→b→c avec les directive_id émis ; rend celui du dernier node."""
    engine.resume(run_id, output=OUT_A, directive_id=engine.directive(run_id).directive_id)
    engine.resume(run_id, output=OUT_B, directive_id=engine.directive(run_id).directive_id)
    last = engine.directive(run_id).directive_id
    assert last is not None
    assert engine.resume(run_id, output={"pins": {}}, directive_id=last).finished
    return last


def test_replay_of_the_last_node_with_its_directive_id_succeeds_on_a_finished_run(tmp_path: Path) -> None:
    """S1 : la directive est ``done`` ; le rejeu W1-03 du dernier node avec son directive_id reste admis."""
    engine, run_id = _start(tmp_path)
    last = _finish(engine, run_id)
    assert engine.directive(run_id).kind == "done"
    before = _bytes(tmp_path)

    again = engine.resume(run_id, output={"pins": {}}, directive_id=last)

    assert again.ok and again.finished and again.replay and again.replay_of == "c"
    assert _bytes(tmp_path) == before


def test_stale_directive_id_with_an_unknown_output_is_still_refused_on_a_finished_run(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _finish(engine, run_id)
    before = _bytes(tmp_path)
    with pytest.raises(GrimoireRuntimeError, match="directive_id"):
        engine.resume(run_id, output=OUT_A, directive_id="0" * 64)
    with pytest.raises(GrimoireRuntimeError, match="directive_id"):
        engine.resume(run_id, output={"pins": {"out": {"contract": "autre"}}}, directive_id="1" * 64)
    assert _bytes(tmp_path) == before


def _crash_on_complete(engine: FlowEngine, run_id: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """a→b acceptés, puis c checkpointé mais ``complete`` plante : le statut reste CHECKPOINTED."""
    engine.resume(run_id, output=OUT_A, directive_id=engine.directive(run_id).directive_id)
    engine.resume(run_id, output=OUT_B, directive_id=engine.directive(run_id).directive_id)
    did_c = engine.directive(run_id).directive_id
    assert did_c is not None

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("crash entre le dernier checkpoint et complete")

    monkeypatch.setattr(engine._kernel, "complete", boom)
    with pytest.raises(RuntimeError, match="crash"):
        engine.resume(run_id, output={"pins": {}}, directive_id=did_c)
    monkeypatch.undo()
    return did_c


def test_directive_is_not_done_while_the_run_is_not_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 : tous les nodes sont checkpointés mais le run n'est pas terminé : jamais ``done``."""
    engine, run_id = _start(tmp_path)
    did_c = _crash_on_complete(engine, run_id, monkeypatch)
    assert engine._kernel.get_instance(run_id).status.value == "checkpointed"  # type: ignore[union-attr]

    directive = engine.directive(run_id)

    assert directive.kind != "done"
    assert directive.kind in DIRECTIVE_KINDS and directive.node_id == "c"
    assert directive.directive_id == did_c


def test_resume_with_the_last_directive_id_finishes_a_run_left_checkpointed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 : l'hôte qui suit la directive et renvoie la dernière sortie termine le run (plus d'impasse)."""
    engine, run_id = _start(tmp_path)
    _crash_on_complete(engine, run_id, monkeypatch)
    directive = engine.directive(run_id)
    assert directive.directive_id is not None

    done = engine.resume(run_id, output={"pins": {}}, directive_id=directive.directive_id)

    assert done.ok and done.finished
    assert engine._kernel.get_instance(run_id).status.value == "completed"  # type: ignore[union-attr]
    assert engine.directive(run_id).kind == "done"


def _claim(root: Path, task_id: str) -> None:
    from grimoire.cli.cmd_task import task_app

    res = runner.invoke(task_app, ["claim", task_id, "--project-root", str(root)], obj={"output": "text"})
    assert res.exit_code == 0, res.output


def _ready_task(root: Path) -> str:
    from grimoire.missions.service import TaskService

    added = TaskService(root).add(
        "rattacher un flow", ("le run porte la tache claimee",), owner="guilhem", actor="claude", ready=True
    )
    TaskService(root).context(added.task.id)
    return added.task.id


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from grimoire.core.agentic_standard import setup_standard_profile

    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _flow_run(root: Path, *extra: str, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    bp = _blueprint(root)
    return runner.invoke(
        app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(root), *extra], env=env
    )


def _no_run_written(root: Path) -> bool:
    flows = root / "_grimoire-runtime-output" / "flows"
    return not flows.exists() or not list(flows.glob("*"))


def test_flow_run_refuses_an_unknown_task_id_in_governed_profile(governed: Path) -> None:
    """S3 : ``--task-id`` fantôme, aucun claim : refus, aucun run créé."""
    result = _flow_run(governed, "--task-id", "FANTOME-1")
    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert "FANTOME-1" in error and "task claim" in error
    assert _no_run_written(governed)


def test_flow_run_refuses_a_task_id_from_the_environment_without_claim(governed: Path) -> None:
    result = _flow_run(governed, env={"GRIMOIRE_TASK_ID": "FANTOME-1"})
    assert result.exit_code == 1, result.output
    assert "task claim" in json.loads(result.output)["error"]
    assert _no_run_written(governed)


def test_flow_run_refuses_a_task_id_that_is_not_the_claimed_one(governed: Path) -> None:
    claimed = _ready_task(governed)
    _claim(governed, claimed)
    result = _flow_run(governed, "--task-id", "FANTOME-1")
    assert result.exit_code == 1, result.output
    assert "task claim" in json.loads(result.output)["error"]
    assert _no_run_written(governed)


def test_flow_run_accepts_the_claimed_task_id_in_governed_profile(governed: Path) -> None:
    claimed = _ready_task(governed)
    _claim(governed, claimed)
    result = _flow_run(governed, "--task-id", claimed)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["directive"]["kind"] == "execute"


def test_flow_run_in_lighter_profile_keeps_an_arbitrary_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    _enroll(tmp_path, "starter")
    result = _flow_run(tmp_path, "--task-id", "LIBRE-1")
    assert result.exit_code == 0, result.output


def test_refusal_message_names_the_real_option(governed: Path) -> None:
    """S3 : l'option s'appelle ``--task-id``, pas ``--task``."""
    error = json.loads(_flow_run(governed).output)["error"]
    assert "--task-id" in error
    assert "--task " not in error and "--task>" not in error
