"""Issue #1151 : la capsule ne relit que les flows actifs les plus récents.

Chaque run de flow encore en vol ajoutait une ligne à la capsule, relue à
chaque tour. Sur la Forge, 22 runs empilés faisaient 22 lignes. Attendu :
au plus 3 lignes détaillées (les plus récentes), puis une ligne de comptage
qui renvoie vers ``grimoire flow list``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.live_runs import active_flow_lines


def _blueprint(project: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois",
        "name": "Trois",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "A",
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    path = project / "trois.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _engine(project: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=project / "_grimoire-runtime-output/runtime",
        flows_root=project / "_grimoire-runtime-output/flows",
        project_root=project,
    )


def _start_runs(project: Path, count: int) -> list[str]:
    """Lance ``count`` runs dans l'ordre : le dernier de la liste est le plus récent."""
    engine = _engine(project)
    blueprint = _blueprint(project)
    run_ids: list[str] = []
    for _ in range(count):
        wfi, _ = engine.run(blueprint, executor=InteractiveNodeExecutor(stream=io.StringIO()))
        run_ids.append(wfi.id)
    return run_ids


def _rewrite_created_at(project: Path, run_id: str, created_at: str) -> None:
    """Force la date de lancement lue par le plafond (la méta n'est pas relue par le moteur)."""
    path = project / "_grimoire-runtime-output" / "flows" / f"{run_id}.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["created_at"] = created_at
    path.write_text(json.dumps(meta), encoding="utf-8")


def test_a_single_run_keeps_the_original_line(tmp_path: Path) -> None:
    """Un seul run : la ligne est identique au caractère près, sans ligne de comptage."""
    run_id = _start_runs(tmp_path, 1)[0]
    assert active_flow_lines(tmp_path) == [
        f"[Grimoire — flow actif] run {run_id} (blueprint trois), nœud courant a, statut running"
    ]


def test_twenty_two_runs_are_capped_to_three_lines_and_a_count(tmp_path: Path) -> None:
    run_ids = _start_runs(tmp_path, 22)
    lines = active_flow_lines(tmp_path)

    assert len(lines) == 4
    assert lines[-1] == "+ 19 autres, grimoire flow list"
    # Les trois plus récents, du plus récent au plus ancien.
    for line, run_id in zip(lines[:3], reversed(run_ids[-3:]), strict=True):
        assert run_id in line
    rendered = "\n".join(lines)
    for run_id in run_ids[:-3]:
        assert run_id not in rendered


def test_three_runs_are_not_capped(tmp_path: Path) -> None:
    run_ids = _start_runs(tmp_path, 3)
    lines = active_flow_lines(tmp_path)

    assert len(lines) == 3
    rendered = "\n".join(lines)
    for run_id in run_ids:
        assert run_id in rendered
    assert all("autres" not in line for line in lines)


def test_settled_runs_neither_show_nor_count(tmp_path: Path) -> None:
    """Un run soldé n'occupe aucune ligne et ne gonfle pas le compte des runs tus."""
    run_ids = _start_runs(tmp_path, 5)
    engine = _engine(tmp_path)
    for run_id in run_ids[:2]:
        engine.abort(run_id, reason="fin de test")

    lines = active_flow_lines(tmp_path)

    assert len(lines) == 3
    rendered = "\n".join(lines)
    for run_id in run_ids[:2]:
        assert run_id not in rendered
    assert all("autres" not in line for line in lines)


def test_the_shown_lines_are_the_most_recent_by_created_at(tmp_path: Path) -> None:
    """La récence vient de ``created_at``, pas de l'ordre alphabétique de ``list_run_ids``."""
    run_ids = _start_runs(tmp_path, 5)
    _rewrite_created_at(tmp_path, run_ids[0], "2999-01-01T00:00:00+00:00")

    lines = active_flow_lines(tmp_path)

    assert len(lines) == 4
    assert run_ids[0] in lines[0]
    assert lines[-1] == "+ 2 autres, grimoire flow list"
