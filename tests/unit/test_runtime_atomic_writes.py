"""W1-02 : les écritures d'état du kernel, des métas de run et du TraceLedger sont atomiques et verrouillées.

Constat sur la base (W1-00) : ``RuntimeKernel._save_instance`` relisait tout puis
``write_text`` sans verrou ni ``os.replace`` (deux créations concurrentes se perdaient
l'une l'autre, un crash laissait un fichier tronqué) ; ``FlowEngine._save_meta`` faisait
de même ; ``TraceLedger._next_id`` comptait les lignes hors verrou (deux processus
obtenaient le même identifiant).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from grimoire.flows.engine import FlowEngine, FlowRunMeta
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext

SRC = Path(__file__).resolve().parents[2] / "src" / "grimoire"


def _ctx(run_id: str = "") -> ExecutionContext:
    return ExecutionContext(
        run_id=run_id,
        mission_id="MIS-w102",
        task_id="GAO-w102",
        workflow_instance_id="",
        actor_id="cli",
        host_id="local",
        risk_profile="standard",
    )


def _meta(run_id: str, blueprint: str = "bp") -> FlowRunMeta:
    return FlowRunMeta(
        run_id=run_id,
        blueprint_id=blueprint,
        blueprint_path="bp.yaml",
        order=("a", "b"),
        created_at="2026-10-10T00:00:00+00:00",
    )


# ── Kernel : instances.jsonl ────────────────────────────────────────────────


def test_eight_threads_create_eight_instances_and_all_are_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = RuntimeKernel(tmp_path / "runtime")
    original = RuntimeKernel._load_instances

    def slow_load(self: RuntimeKernel):  # type: ignore[no-untyped-def]
        loaded = original(self)
        time.sleep(0.02)  # élargit la fenêtre lecture -> écriture
        return loaded

    monkeypatch.setattr(RuntimeKernel, "_load_instances", slow_load)
    barrier = threading.Barrier(8)
    created: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            barrier.wait()
            created.append(kernel.create_instance(_ctx(), recipe_id="recipe.w102").id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    monkeypatch.undo()
    assert len(set(created)) == 8, f"identifiants dupliqués : {sorted(created)}"
    assert set(kernel._load_instances()) == set(created)


def test_instances_file_survives_a_crash_before_the_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = RuntimeKernel(tmp_path / "runtime")
    first = kernel.create_instance(_ctx(), recipe_id="recipe.w102")
    path = tmp_path / "runtime" / "instances.jsonl"
    before = path.read_bytes()

    def boom(fd: int) -> None:
        raise OSError("disque plein simulé")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disque plein"):
        kernel.create_instance(_ctx(), recipe_id="recipe.w102-bis")
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert list(kernel._load_instances()) == [first.id]
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


# ── Méta de run ─────────────────────────────────────────────────────────────


def test_run_meta_survives_a_crash_before_the_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    engine._save_meta(_meta("WFI-x-001"))
    path = tmp_path / "flows" / "WFI-x-001.json"
    before = path.read_bytes()

    def boom(fd: int) -> None:
        raise OSError("coupure simulée")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="coupure"):
        engine._save_meta(_meta("WFI-x-001", blueprint="autre"))
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["blueprint_id"] == "bp"


def test_eight_threads_write_eight_run_metas(tmp_path: Path) -> None:
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    barrier = threading.Barrier(8)

    def work(i: int) -> None:
        barrier.wait()
        engine._save_meta(_meta(f"WFI-m-{i:03d}"))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert engine.list_run_ids() == [f"WFI-m-{i:03d}" for i in range(8)]
    assert all(engine.run_meta(r).blueprint_id == "bp" for r in engine.list_run_ids())


# ── TraceLedger ─────────────────────────────────────────────────────────────

_TRACE_WORKER = """
import sys, time
from pathlib import Path
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TraceOutcome

root, go, count = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
ledger = TraceLedger(root)
orig = TraceLedger._next_id

def slow_next_id(self, run_id):
    value = orig(self, run_id)
    time.sleep(0.0005)  # élargit la fenêtre calcul de l'identifiant -> ajout
    return value

TraceLedger._next_id = slow_next_id
while not go.exists():
    time.sleep(0.001)
for _ in range(count):
    ledger.record(run_id="RUN-w102", workflow_instance_id="WFI-1", mission_id="MIS-1", task_id="GAO-1",
                  recipe_id="r", outcome=TraceOutcome.SUCCESS, started_at="2026-10-10T00:00:00+00:00")
"""


def test_two_processes_record_400_distinct_trace_ids(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    root.mkdir()
    go = tmp_path / "go"
    script = tmp_path / "worker.py"
    script.write_text(_TRACE_WORKER, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC.parent), os.environ.get("PYTHONPATH", "")])}
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(root), str(go), "200"],
            env=env,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    go.write_text("go", encoding="utf-8")
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
    ids = [json.loads(line)["id"] for line in (root / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ids) == 400
    assert len(set(ids)) == 400, f"{400 - len(set(ids))} identifiants dupliqués"


# ── Architecture ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", ["runtime/kernel.py", "flows/engine.py"])
def test_no_direct_write_text_in_state_writers(rel: str) -> None:
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    direct = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "write_text"
    ]
    assert not direct, f"{rel} : write_text direct aux lignes {direct} — utiliser fsio.atomic_write"
