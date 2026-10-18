"""Vagues d'un blueprint et ``flow next --wave`` (W5-04) : l'ordre des nodes est prouvé, pas supposé."""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import topo_order, topo_waves
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.pilot import PilotPolicy, load_pilot_policy

runner = CliRunner()


def _node(node_id: str, pins: list[dict[str, str]]) -> dict[str, Any]:
    return {"id": node_id, "kind": "pattern", "ref": "ORC-01", "label": node_id.upper(), "pins": pins}


def _dag(n: int, edges: list[tuple[int, int]]) -> dict[str, Any]:
    """Un blueprint à n nodes ; une arête (i, j) relie la pin ``o{j}`` de i à la pin ``i{i}`` de j."""
    nodes = []
    for k in range(n):
        pins = [{"id": f"i{i}", "direction": "in", "contract": "c"} for i, j in edges if j == k]
        pins += [{"id": f"o{j}", "direction": "out", "contract": "c"} for i, j in edges if i == k]
        nodes.append(_node(f"n{k:02d}", pins))
    return {
        "blueprintVersion": 1,
        "id": "dag",
        "nodes": nodes,
        "edges": [{"from": f"n{i:02d}.o{j}", "to": f"n{j:02d}.i{i}", "contract": "c"} for i, j in edges],
    }


def test_waves_property_sur_200_dag_aleatoires() -> None:
    rng = random.Random(20261017)
    for _ in range(200):
        n = rng.randint(1, 12)
        edges = [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < 0.3]
        perm = list(range(n))
        rng.shuffle(perm)  # les ids ne suivent pas l'ordre topologique
        edges = [(perm[i], perm[j]) for i, j in edges]
        bp = _dag(n, edges)

        waves = topo_waves(bp)
        level = {node: w for w, wave in enumerate(waves) for node in wave}

        assert sorted(level) == sorted(f"n{k:02d}" for k in range(n))
        for i, j in edges:  # toute arête va d'une vague plus tôt à une vague plus tard
            assert level[f"n{i:02d}"] < level[f"n{j:02d}"]
        for node, w in level.items():  # et une vague est la plus tôt possible
            preds = [level[f"n{i:02d}"] for i, j in edges if f"n{j:02d}" == node]
            assert w == (max(preds) + 1 if preds else 0)
        assert all(wave == sorted(wave) for wave in waves)
        assert topo_waves(bp) == waves  # reproductible
        assert topo_order(bp) == [node for wave in waves for node in wave]


def test_un_cycle_est_refuse_en_nommant_les_nodes() -> None:
    bp = _dag(3, [(0, 1), (1, 2), (2, 1)])
    with pytest.raises(GrimoireRuntimeError) as exc:
        topo_waves(bp)
    assert "n01" in str(exc.value)
    assert "n02" in str(exc.value)


def _diamond(tmp_path: Path) -> Path:
    bp = {
        "blueprintVersion": 1,
        "id": "diamant",
        "nodes": [
            _node("a", [{"id": "out", "direction": "out", "contract": "c1"}]),
            _node("b", [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}]),
            _node("c", [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c3"}]),
            _node("d", [{"id": "x", "direction": "in", "contract": "c2"}, {"id": "y", "direction": "in", "contract": "c3"}]),
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "a.out", "to": "c.in", "contract": "c1"},
            {"from": "b.out", "to": "d.x", "contract": "c2"},
            {"from": "c.out", "to": "d.y", "contract": "c3"},
        ],
    }
    path = tmp_path / "diamant.blueprint.json"
    path.write_text(json.dumps(bp), encoding="utf-8")
    return path


def test_next_wave_sur_un_diamant_rend_b_et_c_ensemble(tmp_path: Path) -> None:
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    wfi, _ = engine.run(_diamond(tmp_path), executor=InteractiveNodeExecutor(stream=io.StringIO()))

    assert engine.next_wave(wfi.id) == ["a"]
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert engine.next_wave(wfi.id) == ["b", "c"]
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c2"}}})
    assert engine.next_wave(wfi.id) == ["c"]  # b franchi : il ne reste que c dans la vague
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c3"}}})
    assert engine.next_wave(wfi.id) == ["d"]
    engine.resume(wfi.id, output={"pins": {}})
    assert engine.next_wave(wfi.id) == []  # run terminé


def test_next_wave_d_un_run_abandonne_est_vide(tmp_path: Path) -> None:
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    wfi, _ = engine.run(_diamond(tmp_path), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    engine.abort(wfi.id, reason="test")
    assert engine.next_wave(wfi.id) == []


def test_cli_flow_next_wave(tmp_path: Path) -> None:
    bp = _diamond(tmp_path)
    started = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    assert started.exit_code == 0, started.output
    run_id = json.loads(started.output)["run_id"]
    result = runner.invoke(app, ["--output", "json", "flow", "next", run_id, "--wave", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"run_id": run_id, "wave": True, "nodes": ["a"]}

    single = runner.invoke(app, ["--output", "json", "flow", "next", run_id, "--project-root", str(tmp_path)])
    assert json.loads(single.output)["nodes"] == ["a"]


def test_pilot_max_parallel(tmp_path: Path) -> None:
    assert PilotPolicy().max_parallel == 1
    std = tmp_path / "_grimoire" / "standard"
    std.mkdir(parents=True)
    (std / "pilot.yaml").write_text("max_parallel: 3\n", encoding="utf-8")
    assert load_pilot_policy(tmp_path).max_parallel == 3
    for bad in ("0", "-1", "true", "'deux'", "1.5"):
        (std / "pilot.yaml").write_text(f"max_parallel: {bad}\n", encoding="utf-8")
        with pytest.raises(GrimoireRuntimeError, match="max_parallel"):
            load_pilot_policy(tmp_path)


def test_next_wave_rend_les_nodes_prets_du_dag_pas_le_reste_de_la_premiere_vague(tmp_path: Path) -> None:
    """a -> b avec c indépendant : après a, b (vague 2) est prêt autant que c (vague 1) (W5-04-S3)."""
    bp = {
        "blueprintVersion": 1,
        "id": "inegal",
        "nodes": [
            _node("a", [{"id": "out", "direction": "out", "contract": "c1"}]),
            _node("b", [{"id": "in", "direction": "in", "contract": "c1"}]),
            _node("c", []),
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "inegal.blueprint.json"
    path.write_text(json.dumps(bp), encoding="utf-8")
    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    wfi, _ = engine.run(path, executor=InteractiveNodeExecutor(stream=io.StringIO()))

    assert engine.next_wave(wfi.id) == ["a", "c"]
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert engine.next_wave(wfi.id) == ["c", "b"]  # ordre des vagues puis des ids : c (vague 1), b (vague 2)
    engine.resume(wfi.id, output={"pins": {}})
    assert engine.next_wave(wfi.id) == ["b"]


def test_ready_nodes_propriete_sur_200_dag_aleatoires() -> None:
    from grimoire.flows.blueprint_loader import ready_nodes

    rng = random.Random(20261018)
    for _ in range(200):
        n = rng.randint(1, 12)
        edges = [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < 0.3]
        bp = _dag(n, edges)
        order = topo_order(bp)
        for cut in range(len(order) + 1):
            done = set(order[:cut])
            expected = {
                f"n{k:02d}" for k in range(n)
                if f"n{k:02d}" not in done and all(f"n{i:02d}" in done for i, j in edges if j == k)
            }
            got = ready_nodes(bp, done)
            assert set(got) == expected and len(got) == len(expected)
