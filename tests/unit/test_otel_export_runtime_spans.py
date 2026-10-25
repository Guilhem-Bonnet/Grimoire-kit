"""R26 : l'export OTel relit le journal du RuntimeKernel, avec `parentSpanId` (une seule source par donnée)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.runtime.schemas import WorkflowStatus
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.runtime_spans import runtime_otel_spans


def _blueprint(tmp_path: Path) -> Path:
    pin = lambda d, c: {"id": d, "direction": d, "contract": c}  # noqa: E731
    blueprint = {
        "blueprintVersion": 1, "id": "deux-nodes", "name": "Deux nodes",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "label": "A", "pins": [pin("out", "c1")]},
            {"id": "b", "kind": "pattern", "ref": "QUA-04", "label": "B", "pins": [pin("in", "c1")]},
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "deux.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _run_two_nodes(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    engine = FlowEngine(kernel_root=runtime, flows_root=tmp_path / "flows")
    wfi, _ = engine.run(_blueprint(tmp_path), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    assert engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}}).ok
    assert engine.resume(wfi.id, output={"pins": {}}).finished
    assert engine.status(wfi.id).status == WorkflowStatus.COMPLETED.value
    return runtime


def _export(tmp_path: Path, runtime: Path | None) -> list[dict]:
    dest = tmp_path / "otel.jsonl"
    TraceLedger(tmp_path / "traces").export_otel_jsonl(dest, runtime_root=runtime)
    return [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()]


def test_run_de_flow_a_deux_nodes_exporte_des_spans_avec_parent(tmp_path: Path) -> None:
    runtime = _run_two_nodes(tmp_path)
    spans = _export(tmp_path, runtime)
    with_parent = [s for s in spans if s.get("parentSpanId")]
    assert with_parent, "aucun span avec parentSpanId"
    ids = {s["spanId"] for s in spans}
    # Chaque parent déclaré est un span du même export : l'arbre se referme.
    assert {s["parentSpanId"] for s in with_parent} <= ids
    roots = [s for s in spans if not s.get("parentSpanId")]
    assert [r["attributes"]["grimoire.run_event.type"] for r in roots] == ["workflow.started"]
    assert len({s["traceId"] for s in spans}) == 1
    assert {s["attributes"]["grimoire.step_id"] for s in spans if "grimoire.step_id" in s["attributes"]} == {"a", "b"}


def test_l_export_est_stable_d_un_appel_a_l_autre(tmp_path: Path) -> None:
    runtime = _run_two_nodes(tmp_path)
    assert _export(tmp_path, runtime) == _export(tmp_path, runtime)


def test_sans_run_l_export_ne_change_pas_et_ne_cree_rien(tmp_path: Path) -> None:
    absent = tmp_path / "runtime"
    assert _export(tmp_path, absent) == _export(tmp_path, None) == []
    assert runtime_otel_spans(absent) == []
    assert not absent.exists()


def test_le_journal_du_kernel_n_est_pas_modifie_par_l_export(tmp_path: Path) -> None:
    runtime = _run_two_nodes(tmp_path)
    before = {p.name: p.read_bytes() for p in runtime.iterdir()}
    _export(tmp_path, runtime)
    assert {p.name: p.read_bytes() for p in runtime.iterdir()} == before


def test_filtre_par_mission(tmp_path: Path) -> None:
    runtime = _run_two_nodes(tmp_path)
    assert runtime_otel_spans(runtime, mission_id="mission-inconnue") == []
    assert runtime_otel_spans(runtime)
