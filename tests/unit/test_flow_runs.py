"""``grimoire.tools.flow_runs`` — lire les runs de flow sans le moteur (issue #506).

Observer affichait « Aucune trace : le TraceLedger de ce projet est vide »
même juste après un `grimoire upgrade-flow run` réel : le TraceLedger
(dispatch/agent-miss) et les métadonnées de run du moteur de flow
(``_grimoire-runtime-output/flows/<run_id>.json``, écrites par
``grimoire.flows.engine.FlowEngine``) sont deux mécanismes distincts. Ce
module lit les secondes directement — jamais via ``FlowEngine``, qui ouvrirait
aussi un ``RuntimeKernel`` pour une simple lecture d'affichage.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.tools.flow_runs import list_flow_runs


def _write_run(root: Path, run_id: str, blueprint_id: str, created_at: str) -> None:
    flows_dir = root / "_grimoire-runtime-output" / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)
    (flows_dir / f"{run_id}.json").write_text(
        json.dumps({
            "schema_version": "grimoire.flow_run_meta.v1",
            "run_id": run_id,
            "blueprint_id": blueprint_id,
            "blueprint_path": "whatever.yaml",
            "order": ["backup", "preview"],
            "created_at": created_at,
        }),
        encoding="utf-8",
    )


def test_no_flows_dir_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    assert list_flow_runs(tmp_path) == []


def test_lists_runs_newest_first(tmp_path: Path) -> None:
    _write_run(tmp_path, "run-a", "project-upgrade", "2026-09-14T10:00:00+00:00")
    _write_run(tmp_path, "run-b", "project-upgrade", "2026-09-14T12:00:00+00:00")
    runs = list_flow_runs(tmp_path)
    assert [r["runId"] for r in runs] == ["run-b", "run-a"]


def test_filters_by_blueprint_id(tmp_path: Path) -> None:
    _write_run(tmp_path, "run-a", "project-upgrade", "2026-09-14T10:00:00+00:00")
    _write_run(tmp_path, "run-b", "some-other-flow", "2026-09-14T11:00:00+00:00")
    runs = list_flow_runs(tmp_path, blueprint_id="project-upgrade")
    assert [r["runId"] for r in runs] == ["run-a"]


def test_a_corrupt_metadata_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    flows_dir = tmp_path / "_grimoire-runtime-output" / "flows"
    flows_dir.mkdir(parents=True)
    (flows_dir / "broken.json").write_text("{not json", encoding="utf-8")
    _write_run(tmp_path, "run-a", "project-upgrade", "2026-09-14T10:00:00+00:00")
    runs = list_flow_runs(tmp_path)
    assert [r["runId"] for r in runs] == ["run-a"]


def test_limit_caps_the_result(tmp_path: Path) -> None:
    for i in range(5):
        _write_run(tmp_path, f"run-{i}", "project-upgrade", f"2026-09-14T{10 + i:02d}:00:00+00:00")
    runs = list_flow_runs(tmp_path, limit=2)
    assert len(runs) == 2
    assert runs[0]["runId"] == "run-4"


# ── Restes #510/#513 : statut live pour le badge « checkpoint en attente » ──


def _write_two_node_blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "deux-nodes",
        "name": "Deux nodes",
        "nodes": [
            {
                "id": "a", "kind": "pattern", "ref": "ORC-01", "label": "A",
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b", "kind": "pattern", "ref": "QUA-04", "label": "B",
                "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
            },
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "deux-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def test_a_returned_run_carries_its_live_status_and_current_node(tmp_path: Path) -> None:
    """Le badge Piloter « checkpoint destructif en attente » ne doit pas être

    un état purement client : `list_flow_runs` pose désormais `status`/
    `currentNode` depuis le kernel réel, pas seulement `runId`/`blueprintId`/
    `createdAt` (la métadonnée immuable écrite une fois par `run()`)."""
    bp = _write_two_node_blueprint(tmp_path)
    engine = FlowEngine(
        kernel_root=tmp_path / "_grimoire-runtime-output" / "runtime",
        flows_root=tmp_path / "_grimoire-runtime-output" / "flows",
        project_root=tmp_path,
    )
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})

    runs = list_flow_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["runId"] == wfi.id
    assert runs[0]["status"] == "checkpointed"
    assert runs[0]["currentNode"] == "b"


def test_a_run_missing_from_the_kernel_has_a_null_status(tmp_path: Path) -> None:
    """Une métadonnée sans kernel derrière (nettoyage externe, ou une fixture

    de test qui n'écrit que le fichier de métadonnées) ne casse jamais la
    liste — `status`/`currentNode` restent `None`, jamais une exception."""
    _write_run(tmp_path, "run-a", "project-upgrade", "2026-09-14T10:00:00+00:00")
    runs = list_flow_runs(tmp_path)
    assert runs[0]["status"] is None
    assert runs[0]["currentNode"] is None
