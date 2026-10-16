"""W2-04 : empreinte du blueprint dans le run, chemin relatif, dérive, migration, export/import.

Défaut sur la base : ``FlowRunMeta`` stocke le chemin tel que reçu (absolu), sans
hash ; ``resume`` recharge le fichier d'aujourd'hui et juge la sortie contre lui,
sans comparer les nodes déjà franchis.
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
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor

OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}


def _node(i: str, pins: list[dict[str, str]], label: str | None = None) -> dict[str, Any]:
    return {"id": i, "kind": "pattern", "ref": "ORC-01", "label": label or i.upper(), "pins": pins}


def _bp(label_b: str = "B", extra_after: bool = False, rename_a: bool = False) -> dict[str, Any]:
    a = "a2" if rename_a else "a"
    nodes = [
        _node(a, [{"id": "out", "direction": "out", "contract": "c1"}]),
        _node("b", [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}], label_b),
        _node("c", [{"id": "in", "direction": "in", "contract": "c2"}, {"id": "out", "direction": "out", "contract": "c3"}]),
    ]
    edges = [{"from": f"{a}.out", "to": "b.in", "contract": "c1"}, {"from": "b.out", "to": "c.in", "contract": "c2"}]
    if extra_after:
        nodes.append(_node("d", [{"id": "in", "direction": "in", "contract": "c3"}]))
        edges.append({"from": "c.out", "to": "d.in", "contract": "c3"})
    return {"blueprintVersion": 1, "id": "trois", "name": "Trois", "nodes": nodes, "edges": edges}


def _write(root: Path, bp: dict[str, Any]) -> Path:
    path = root / "bp" / "trois.blueprint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bp), encoding="utf-8")
    return path


def _engine(root: Path) -> FlowEngine:
    out = root / "_grimoire-runtime-output"
    return FlowEngine(kernel_root=out / "runtime", flows_root=out / "flows", project_root=root)


def _start(root: Path) -> tuple[FlowEngine, str, Path]:
    path = _write(root, _bp())
    engine = _engine(root)
    wfi, _ = engine.run(path, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return engine, wfi.id, path


def _events(root: Path) -> str:
    return (root / "_grimoire-runtime-output" / "runtime" / "run_events.jsonl").read_text(encoding="utf-8")


def test_meta_holds_a_relative_path_and_a_fingerprint(tmp_path: Path) -> None:
    engine, run_id, _path = _start(tmp_path)
    meta = engine.run_meta(run_id)
    assert meta.blueprint_path == "bp/trois.blueprint.json"
    assert not Path(meta.blueprint_path).is_absolute()
    assert meta.blueprint_sha256 and len(meta.blueprint_sha256) == 64
    assert set(meta.node_digests) == {"a", "b", "c"}
    raw = (tmp_path / "_grimoire-runtime-output" / "flows" / f"{run_id}.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in raw


def test_legacy_absolute_path_run_still_resumes(tmp_path: Path) -> None:
    engine, run_id, path = _start(tmp_path)
    meta_file = tmp_path / "_grimoire-runtime-output" / "flows" / f"{run_id}.json"
    legacy = json.loads(meta_file.read_text(encoding="utf-8"))
    legacy.update(blueprint_path=str(path), blueprint_sha256=None, node_digests={})
    meta_file.write_text(json.dumps(legacy), encoding="utf-8")
    assert engine.resume(run_id, output=OUT_A).ok
    view = engine.status(run_id)
    assert view.blueprint_sha256 is None and view.blueprint_drift is None  # non vérifié, pas déclaré sain


def test_crossed_node_modified_gives_blueprint_drift_and_writes_nothing(tmp_path: Path) -> None:
    engine, run_id, _path = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok  # a franchi, b courant
    assert engine.resume(run_id, output=OUT_B).ok  # b franchi, c courant
    before = _events(tmp_path)
    bp = _bp()
    bp["nodes"][0]["label"] = "A MODIFIE"
    _write(tmp_path, bp)
    drift = engine.blueprint_drift(run_id)
    assert drift is not None and drift["kind"] == "blueprint_drift" and drift["changed_nodes"] == ["a"]
    with pytest.raises(GrimoireRuntimeError, match="blueprint_drift"):
        engine.resume(run_id, output={"pins": {"out": {"contract": "c3"}}})
    assert _events(tmp_path) == before
    view = engine.status(run_id)
    assert view.blueprint_drift is not None and view.directive is not None
    assert view.directive.kind == "blocked" and view.directive.reason == "blueprint_drift"


def test_crossed_node_contract_change_alone_is_drift(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok  # a franchi, b courant
    bp = _bp()
    bp["nodes"][0]["label"] = "A MODIFIE"
    _write(tmp_path, bp)
    drift = engine.blueprint_drift(run_id)
    assert drift is not None
    assert drift["changed_nodes"] == ["a"] and drift["reordered"] is False


def test_rename_of_a_crossed_node_is_drift_with_order_diff(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok
    _write(tmp_path, _bp(rename_a=True))
    drift = engine.blueprint_drift(run_id)
    assert drift is not None and drift["removed_nodes"] == ["a"]
    assert any(line.startswith("-a") for line in drift["order_diff"])


def test_node_added_after_the_current_one_is_accepted(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok
    _write(tmp_path, _bp(extra_after=True))
    assert engine.blueprint_drift(run_id) is None
    assert engine.resume(run_id, output=OUT_B).ok


def test_current_and_later_node_edits_are_not_drift(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok  # b courant
    _write(tmp_path, _bp(label_b="B RETOUCHE"))
    assert engine.blueprint_drift(run_id) is None


def test_migrate_adopts_a_new_tail_when_crossed_nodes_are_a_prefix(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok
    _write(tmp_path, _bp(extra_after=True))
    old, new = engine.migrate(run_id)
    assert list(old.order) == ["a", "b", "c"] and list(new.order) == ["a", "b", "c", "d"]
    assert new.blueprint_sha256 != old.blueprint_sha256
    assert engine.status(run_id).pending_nodes == ("c", "d")


def test_migrate_refuses_without_writing_when_crossed_nodes_are_not_a_prefix(tmp_path: Path) -> None:
    engine, run_id, _ = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A).ok
    _write(tmp_path, _bp(rename_a=True))
    meta_file = tmp_path / "_grimoire-runtime-output" / "flows" / f"{run_id}.json"
    before = meta_file.read_bytes()
    with pytest.raises(GrimoireRuntimeError, match="migration refusée"):
        engine.migrate(run_id)
    assert meta_file.read_bytes() == before


def test_export_import_round_trip_reproduces_status_json(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    engine, run_id, _ = _start(src)
    assert engine.resume(run_id, output=OUT_A).ok
    runner = CliRunner()
    bundle = tmp_path / "run.json"
    r = runner.invoke(app, ["flow", "export", run_id, "--out", str(bundle), "--project-root", str(src)])
    assert r.exit_code == 0, r.output
    assert str(src) not in bundle.read_text(encoding="utf-8")
    r = runner.invoke(app, ["flow", "import", str(bundle), "--project-root", str(dst)])
    assert r.exit_code == 0, r.output

    def status(root: Path) -> dict[str, Any]:
        res = CliRunner().invoke(app, ["--output", "json", "flow", "status", run_id, "--project-root", str(root)])
        assert res.exit_code == 0, res.output
        return json.loads(res.output)  # type: ignore[no-any-return]

    assert status(dst) == status(src)
    again = runner.invoke(app, ["flow", "import", str(bundle), "--project-root", str(dst)])
    assert again.exit_code != 0  # jamais d'écrasement


def test_import_refuses_an_unsafe_run_id_and_an_escaping_path(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    engine, run_id, _ = _start(src)
    from grimoire.flows.transfer import export_run, import_run

    bundle = export_run(engine, run_id, src)
    evil = json.loads(json.dumps(bundle))
    evil["meta"]["run_id"] = "../evil"
    with pytest.raises(GrimoireRuntimeError):
        import_run(_engine(dst), evil, dst)
    escaping = json.loads(json.dumps(bundle))
    escaping["meta"]["blueprint_path"] = "../../outside.json"
    meta = import_run(_engine(dst), escaping, dst)
    assert (dst / meta.blueprint_path).is_file() and not (tmp_path / "outside.json").exists()
