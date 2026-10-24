"""Espace Mémoire, onglet Flotte — interactions et états restés sans test (#536).

``test_workspace_memoire_overview.py`` prouve l'agrégation sur deux vrais
projets. Ce module ne rejoue pas le serveur : il intercepte les routes
``/api/workspace/memory/*`` (``page.route``) pour fixer la forme de réponse,
et vérifie ce que la page en fait — le rendu et l'interaction, pas le
moteur d'agrégation (déjà couvert par ``tests/unit/tools/test_workspace_memory.py``).

Couvre : recherche croisée au clavier (Entrée), requête vide (aucun appel
réseau), clic et clavier sur un résultat (l'inspecteur le montre), et les
états d'un projet sans mémoire liée (``unavailable``, ``uninitialized``,
``unreadable``) nommés dans l'aperçu.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page, Route


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _project(slug: str, state: str = "ok", reason: str | None = None, entries: int | None = 3) -> dict:
    return {
        "slug": slug,
        "name": slug,
        "path": f"/dev/null/{slug}",
        "state": state,
        "configuredBackend": "local",
        "resolvedBackend": "local",
        "entries": entries,
        "lastWrite": None,
        "lexicalIndex": "inconnu",
        "reason": reason,
    }


_RESULTS = {
    "query": "cache",
    "count": 2,
    "projects": [_project("alpha")],
    "results": [
        {
            "id": "mem-001",
            "projectSlug": "alpha",
            "projectName": "Alpha",
            "score": 0.91,
            "text": "le cache de session bloque le ticket",
            "tags": ["decisions"],
        },
        {
            "id": "mem-002",
            "projectSlug": "alpha",
            "projectName": "Alpha",
            "score": 0.5,
            "text": "second résultat",
            "tags": [],
        },
    ],
}


def _open_fleet(page: Page) -> None:
    _goto(page, "memoire")
    page.wait_for_selector("#view-seg button")
    page.locator("#view-seg button", has_text="Flotte").click()
    page.wait_for_selector(".me-search input")


def _stub_search(page: Page, payload: dict) -> list[str]:
    """Intercepte la recherche croisée et rend la liste des URL réellement appelées."""
    calls: list[str] = []

    def _route(route: Route) -> None:
        calls.append(route.request.url)
        _fulfill(route, payload)

    page.route("**/api/workspace/memory/search*", _route)
    return calls


def test_la_touche_entree_lance_la_recherche_croisee(workspace: Page) -> None:
    calls = _stub_search(workspace, _RESULTS)
    _open_fleet(workspace)

    field = workspace.locator(".me-search input")
    field.fill("cache")
    field.press("Enter")

    workspace.wait_for_selector(".me-result")
    assert workspace.locator(".me-result").count() == 2
    assert len(calls) == 1
    assert "q=cache" in calls[0]


def test_une_requete_vide_n_appelle_aucune_route_et_invite_a_saisir(workspace: Page) -> None:
    calls = _stub_search(workspace, _RESULTS)
    _open_fleet(workspace)

    field = workspace.locator(".me-search input")
    field.fill("   ")
    field.press("Enter")
    workspace.locator(".me-search button", has_text="Rechercher").click()

    results = workspace.locator(".me-results")
    assert "Saisissez un terme" in results.inner_text()
    assert workspace.locator(".me-result").count() == 0
    assert calls == [], "une requête vide ne doit déclencher aucun appel réseau"


def test_le_clic_sur_un_resultat_l_ouvre_dans_l_inspecteur(workspace: Page) -> None:
    _stub_search(workspace, _RESULTS)
    _open_fleet(workspace)
    workspace.locator(".me-search input").fill("cache")
    workspace.locator(".me-search input").press("Enter")
    workspace.wait_for_selector(".me-result")

    workspace.locator(".me-result").first.click()

    inspector = workspace.locator("#panel-inspector")
    assert "mem-001" in inspector.inner_text()
    assert "le cache de session bloque le ticket" in inspector.inner_text()
    assert "score 0.910" in inspector.inner_text()


@pytest.mark.parametrize("key", ["Enter", " "])
def test_le_clavier_ouvre_un_resultat_dans_l_inspecteur(workspace: Page, key: str) -> None:
    _stub_search(workspace, _RESULTS)
    _open_fleet(workspace)
    workspace.locator(".me-search input").fill("cache")
    workspace.locator(".me-search input").press("Enter")
    workspace.wait_for_selector(".me-result")

    second = workspace.locator(".me-result").nth(1)
    second.focus()
    second.press(key)

    assert "mem-002" in workspace.locator("#panel-inspector").inner_text()
    assert workspace.locator(".me-result").first.get_attribute("role") == "button"
    assert workspace.locator(".me-result").first.get_attribute("tabindex") == "0"


def test_un_projet_sans_memoire_liee_est_nomme_dans_l_apercu(workspace: Page) -> None:
    overview = {
        "projects": [
            _project("sain"),
            _project("sans-backend", state="unavailable", reason="backend qdrant injoignable", entries=None),
            _project(
                "neuf",
                state="uninitialized",
                reason="projet non initialisé (pas de project-context.yaml)",
                entries=None,
            ),
            _project("perdu", state="unreadable", reason="chemin introuvable : /dev/null/perdu", entries=None),
            _project("muet", state="unavailable", entries=None),
        ],
        "summary": {"count": 5, "readable": 4, "totalEntries": 3},
    }
    workspace.route("**/api/workspace/memory/overview*", lambda route: _fulfill(route, overview))
    _open_fleet(workspace)
    workspace.wait_for_selector(".me-wrap table.tbl tbody tr")

    rows = {
        r.locator("td").first.inner_text(): r.inner_text()
        for r in workspace.locator(".me-wrap table.tbl tbody tr").all()
    }
    assert "backend qdrant injoignable" in rows["sans-backend"]
    assert "projet non initialisé" in rows["neuf"]
    assert "chemin introuvable" in rows["perdu"]
    assert "indisponible" in rows["muet"], "sans raison, l'état est nommé par son mot"
    assert "opérationnel" in rows["sain"]
    assert "4/5 projet(s) lisible(s)" in workspace.locator(".me-summary").inner_text()


def test_un_projet_sans_memoire_propre_n_offre_que_la_flotte(workspace: Page) -> None:
    status = {
        "state": "uninitialized",
        "available": False,
        "entries": 0,
        "configuredBackend": None,
        "resolvedBackend": None,
        "layers": [],
        "layerProfile": None,
    }
    workspace.route("**/api/memory/status*", lambda route: _fulfill(route, status))
    workspace.route(
        "**/api/workspace/memory/overview*",
        lambda route: _fulfill(route, {"projects": [], "summary": {"count": 0, "readable": 0, "totalEntries": 0}}),
    )
    _goto(workspace, "memoire")
    workspace.wait_for_selector("#view-seg button")

    labels = [b.inner_text() for b in workspace.locator("#view-seg button").all()]
    assert labels == ["Flotte"], "sans mémoire propre ni Store, ni Graphe, ni Couches"
    assert "Aucun projet lisible" in workspace.locator(".me-wrap").inner_text()
