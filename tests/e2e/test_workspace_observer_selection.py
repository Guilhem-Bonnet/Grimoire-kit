"""Observer — sélection d'un span et passage à la timeline d'une tâche (#536).

Les routes ``/api/otel``, ``/api/workspace/flows/runs`` et ``/api/events/log``
sont interceptées (``page.route``) : la forme du span est fixée, ce qui est
sous test est ce que la page en fait — l'inspecteur qui se remplit au clic et
le drill-down vers l'espace Exécuter.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page, Route


def _fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _span(name: str, agent: str, *, seconds: int, task_id: str | None = None, error: bool = False) -> dict:
    attributes = {
        "gen_ai.agent.name": agent,
        "gen_ai.system": "anthropic",
        "gen_ai.request.model": "claude-test",
        "grimoire.source": "ledger",
    }
    if task_id:
        attributes["grimoire.task_id"] = task_id
    return {
        "name": name,
        "traceId": f"trace-{name}",
        "startTime": "2026-10-23T10:00:00Z",
        "endTime": f"2026-10-23T10:00:{seconds:02d}Z",
        "attributes": attributes,
        "status": {"code": "ERROR", "message": "boom"} if error else {"code": "OK"},
    }


_SPANS = [
    _span("dispatch.lent", "winston", seconds=30, task_id="GAO-demo-001"),
    _span("dispatch.rapide", "amelia", seconds=2),
    _span("dispatch.casse", "amelia", seconds=5, error=True),
]


@pytest.fixture
def observer(workspace: Page) -> Page:
    workspace.route("**/api/otel*", lambda route: _fulfill(route, {"spans": _SPANS}))
    workspace.route("**/api/workspace/flows/runs*", lambda route: _fulfill(route, {"runs": [], "total": 0}))
    workspace.route("**/api/events/log*", lambda route: _fulfill(route, {}))
    workspace.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "observer")
    workspace.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg="observer")
    workspace.wait_for_selector("table.tbl-pick tbody tr")
    return workspace


def test_le_clic_sur_un_span_lent_le_montre_dans_l_inspecteur(observer: Page) -> None:
    inspector = observer.locator("#panel-inspector")
    assert "Sélectionnez un span" in inspector.inner_text()

    observer.locator("table.tbl-pick tbody tr", has_text="dispatch.rapide").click()

    text = inspector.inner_text()
    assert "dispatch.rapide" in text
    assert "amelia" in text
    assert "claude-test" in text
    assert "Voir la timeline" not in text, "pas de bouton sans grimoire.task_id"


def test_un_span_en_erreur_affiche_son_message(observer: Page) -> None:
    observer.locator("table.tbl-pick tbody tr", has_text="dispatch.casse").click()

    assert "Erreur : boom" in observer.locator("#panel-inspector").inner_text()


def test_la_ligne_d_un_agent_selectionne_son_premier_span(observer: Page) -> None:
    observer.locator(".ob-bar-row", has_text="winston").first.click()

    assert "dispatch.lent" in observer.locator("#panel-inspector").inner_text()


def test_voir_la_timeline_d_une_tache_ouvre_executer_sur_la_timeline(observer: Page) -> None:
    observer.locator("table.tbl-pick tbody tr", has_text="dispatch.lent").click()

    button = observer.locator("#panel-inspector .btn", has_text="Voir la timeline de GAO-demo-001")
    button.click()

    observer.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    observer.wait_for_selector("#view-seg button[aria-pressed='true']")
    # La vue se lit sur sa valeur, pas sur son libellé (« Timeline » est devenu « Chronologie »).
    pressed = observer.locator("#view-seg button[aria-pressed='true']")
    assert pressed.get_attribute("data-value") == "timeline", pressed.inner_text()
