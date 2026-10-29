"""P94 — la liste des tâches n'embarque que ce que les cartes affichent."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.missions.service import TaskService
from grimoire.tools import workspace_api

TASKS = 300


def _seed(root: Path) -> TaskService:
    (root / "_grimoire").mkdir(parents=True, exist_ok=True)
    service = TaskService(root)
    for i in range(TASKS):
        added = service.add(
            f"Tâche {i:03d}",
            (f"critère {i} a", f"critère {i} b"),
            description="Contexte et pistes de la tâche. " * 10,
        )
        # Le poids réel d'un ledger : des consignes, tour après tour.
        for n in range(6):
            service.ledger.add_directive(
                added.task.id, f"Consigne {n} : reprends le point précédent et vérifie les preuves.",
                actor_id="guilhem", kind="comment",
            )
    return service


def test_tasks_view_is_under_half_of_full_ledger(tmp_path: Path) -> None:
    service = _seed(tmp_path)
    before = sum(len(json.dumps(workspace_api._task_json(t))) for t in service.list_tasks())
    view = workspace_api.tasks_view(tmp_path)
    after = len(json.dumps(view["tasks"]))
    assert view["count"] == TASKS
    assert after < before / 2, (after, before)


def test_card_keeps_display_fields_and_drops_detail(tmp_path: Path) -> None:
    _seed(tmp_path)
    card = workspace_api.tasks_view(tmp_path)["tasks"][0]
    for kept in ("id", "title", "status", "board", "acceptance", "expected_evidence",
                 "directives_pending", "directives_unacknowledged", "effective_priority"):
        assert kept in card
    for dropped in ("directives", "description", "guardrails", "dependencies"):
        assert dropped not in card
    detail = workspace_api.task_view(tmp_path, card["id"])
    assert detail["description"].startswith("Contexte et pistes")
    assert len(detail["directives"]) == 6
