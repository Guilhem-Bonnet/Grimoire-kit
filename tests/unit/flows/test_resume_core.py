"""Caractérisation des branches de ``FlowEngine._resume_core`` qu'aucun test n'exerçait.

Écrits et passés sur ``_resume_core`` d'un seul tenant, avant son découpage en
étapes ``_resume_*``, puis rejoués tels quels après. Mesure de départ
(``pytest --cov=grimoire.flows.engine --cov-branch`` sur les tests du moteur) :
trois branches sans test hors backend Rust — le run que le kernel ne connaît
pas, et le node courant introuvable, avec et sans checkpoint en attente. Les
deux refus n'écrivent rien : ni événement, ni statut, ni checkpoint.

Le reste de la fonction est tenu par ``tests/unit/test_flows_engine.py``,
``test_flows_resume_replay.py`` et ``test_flows_crash_boundaries.py`` (qui
interrompt le moteur à chaque écriture, donc fige l'ordre des effets).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.runtime.schemas import WorkflowStatus

OUT_A = {"pins": {"out": {"contract": "c1"}}}


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "deux-nodes",
        "name": "Deux nodes",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "label": "A", "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            {"id": "b", "kind": "pattern", "ref": "QUA-04", "label": "B", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]},
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "deux-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _engine(tmp_path: Path, kernel: str = "runtime") -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / kernel, flows_root=tmp_path / "flows")


def _start(tmp_path: Path) -> tuple[FlowEngine, str]:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(_blueprint(tmp_path), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return engine, wfi.id


def _snapshot(tmp_path: Path) -> dict[str, bytes]:
    """Tout ce que le moteur a écrit (kernel, métas, journaux), hors verrous."""
    return {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and p.suffix != ".lock"
    }


def _no_current_node(*_args: Any) -> None:
    return None


def test_un_run_inconnu_du_kernel_est_refuse_sans_rien_ecrire(tmp_path: Path) -> None:
    """La méta du run existe, mais ce kernel n'a aucune instance à ce nom."""
    _engine_qui_connait, run_id = _start(tmp_path)
    ailleurs = _engine(tmp_path, kernel="autre-runtime")
    before = _snapshot(tmp_path)

    with pytest.raises(GrimoireRuntimeError) as core:
        ailleurs._resume_core(run_id, output=OUT_A)
    with pytest.raises(GrimoireRuntimeError) as public:
        ailleurs.resume(run_id, output=OUT_A)

    assert str(core.value) == f"run introuvable côté kernel : {run_id}"
    assert str(public.value) == f"run introuvable côté kernel : {run_id}"
    assert _snapshot(tmp_path) == before


def test_sans_node_courant_un_run_en_cours_est_refuse_sans_rien_ecrire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, run_id = _start(tmp_path)
    assert engine.status(run_id).status == WorkflowStatus.RUNNING.value
    monkeypatch.setattr(engine, "_current_node", _no_current_node)
    before = _snapshot(tmp_path)

    with pytest.raises(GrimoireRuntimeError) as raised:
        engine._resume_core(run_id, output=OUT_A)

    assert str(raised.value) == f"run {run_id} : aucun node courant résoluble (status=running)"
    assert _snapshot(tmp_path) == before


def test_sans_node_courant_un_checkpoint_qui_attend_encore_des_nodes_ne_clot_pas_le_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHECKPOINTED, node courant introuvable, mais ``b`` reste à faire : ni clôture, ni rejeu — un refus.

    Même en renvoyant la sortie déjà acceptée de ``a`` : la clôture sur la
    dernière sortie acceptée ne vaut que si plus aucun node n'attend.
    """
    engine, run_id = _start(tmp_path)
    assert engine.resume(run_id, output=OUT_A, executor=InteractiveNodeExecutor(stream=io.StringIO())).ok
    status = engine.status(run_id)
    assert (status.status, status.current_node) == (WorkflowStatus.CHECKPOINTED.value, "b")
    monkeypatch.setattr(engine, "_current_node", _no_current_node)
    before = _snapshot(tmp_path)

    with pytest.raises(GrimoireRuntimeError) as raised:
        engine._resume_core(run_id, output=OUT_A)

    assert str(raised.value) == f"run {run_id} : aucun node courant résoluble (status=checkpointed)"
    assert _snapshot(tmp_path) == before
