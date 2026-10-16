"""W2-05 : le run de flow en vol survit à la compaction (FLOW-3).

Défaut sur la base : ``decide_context_capsule`` ne lit jamais les flows, et
``decide_activation`` ignore ``source=compact``. Après une compaction, le
``run_id`` et le nœud courant ne sont plus dans la fenêtre ; le kernel seul les
garde, et rien ne les remonte.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import decide_activation, decide_context_capsule
from grimoire.hosts.decisions._shared import HookEvent, HookInput


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


def _project_engine(project: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=project / "_grimoire-runtime-output/runtime",
        flows_root=project / "_grimoire-runtime-output/flows",
        project_root=project,
    )


def _start_run(project: Path) -> str:
    engine = _project_engine(project)
    wfi, _ = engine.run(_blueprint(project), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return wfi.id


def _governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


def test_the_capsule_names_the_active_run_and_its_node(tmp_path: Path) -> None:
    project = _governed(tmp_path)
    run_id = _start_run(project)
    decision = decide_context_capsule(HookInput(event=HookEvent.PRE_COMPACT, project_root=project))
    assert run_id in decision.context
    assert "nœud courant a" in decision.context


def test_the_capsule_without_a_run_keeps_its_exact_text(tmp_path: Path) -> None:
    project = _governed(tmp_path)
    decision = decide_context_capsule(HookInput(event=HookEvent.PRE_COMPACT, project_root=project))
    assert "flow actif" not in decision.context
    assert decision.context.endswith("à compléter avant toute clôture.")


def test_a_settled_run_is_not_reinjected(tmp_path: Path) -> None:
    project = _governed(tmp_path)
    run_id = _start_run(project)
    _project_engine(project).abort(run_id, reason="fin de test")
    decision = decide_context_capsule(HookInput(event=HookEvent.PRE_COMPACT, project_root=project))
    assert run_id not in decision.context


def test_session_start_after_compaction_prints_the_flow_line(tmp_path: Path) -> None:
    project = _governed(tmp_path)
    run_id = _start_run(project)
    compact = HookInput(event=HookEvent.SESSION_START, project_root=project, raw={"source": "compact"})
    decision = decide_activation(compact)
    assert run_id in decision.context
    assert decision.detail["flow_lines_injected"] is True


def test_a_fresh_session_start_does_not_reinject_the_run(tmp_path: Path) -> None:
    project = _governed(tmp_path)
    run_id = _start_run(project)
    fresh = HookInput(event=HookEvent.SESSION_START, project_root=project, raw={"source": "startup"})
    decision = decide_activation(fresh)
    assert run_id not in decision.context
    assert decision.detail["flow_lines_injected"] is False
