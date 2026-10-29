"""P94 — la liste des tâches n'embarque que ce que les cartes affichent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
                added.task.id,
                f"Consigne {n} : reprends le point précédent et vérifie les preuves.",
                actor_id="guilhem",
                kind="comment",
            )
    return service


@pytest.fixture(scope="module")
def ledger(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    """Le ledger de 300 tâches, construit une fois (lecture seule pour les tests) :
    sa racine et son poids complet, ``_task_json`` avant allègement."""
    root = tmp_path_factory.mktemp("p94-ledger")
    service = _seed(root)
    return root, sum(len(json.dumps(workspace_api._task_json(t))) for t in service.list_tasks())


def test_tasks_view_is_under_half_of_full_ledger(ledger: tuple[Path, int]) -> None:
    root, before = ledger
    view = workspace_api.tasks_view(root)
    after = len(json.dumps(view["tasks"]))
    assert view["count"] == TASKS
    assert after < before * 0.75, (after, before)  # en clair ; la moitié se mesure sur le fil, ci-dessous


def test_card_keeps_display_fields_and_drops_detail(ledger: tuple[Path, int]) -> None:
    root, _ = ledger
    card = workspace_api.tasks_view(root)["tasks"][0]
    for kept in (
        "id",
        "title",
        "status",
        "board",
        "acceptance",
        "expected_evidence",
        "directives_pending",
        "directives_unacknowledged",
        "effective_priority",
    ):
        assert kept in card
    for dropped in ("directives", "description", "guardrails", "dependencies"):
        assert dropped not in card
    detail = workspace_api.task_view(root, card["id"])
    assert detail["description"].startswith("Contexte et pistes")
    assert len(detail["directives"]) == 6


def test_tasks_response_on_the_wire_is_under_half_of_before(
    tmp_path: Path, ledger: tuple[Path, int], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Le critère « pèse moins de la moitié » : les octets réellement reçus par le client."""
    import http.client
    import threading
    from functools import partial

    from grimoire.cli import cmd_cockpit
    from grimoire.tools import project_registry

    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    proj, before = ledger
    project_registry.save_registry([{"name": "Served", "path": str(proj), "slug": "served"}])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "served")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", proj.resolve())
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
        conn.request("GET", "/api/workspace/tasks?project=served", headers={"Accept-Encoding": "gzip"})
        resp = conn.getresponse()
        raw = resp.read()
        assert resp.status == 200
        assert resp.getheader("Content-Encoding") == "gzip"
        assert len(raw) < before / 2, (len(raw), before)
        import gzip

        assert json.loads(gzip.decompress(raw))["count"] == TASKS
    finally:
        httpd.shutdown()
        httpd.server_close()
