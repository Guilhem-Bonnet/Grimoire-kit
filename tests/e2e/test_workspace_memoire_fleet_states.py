"""Savoir › Mémoire — interactions et états, sur des réponses fixées (#536, P24).

``test_workspace_memoire_overview.py`` et ``test_workspace_savoir_p24.py``
prouvent le parcours sur de vrais projets. Ce module ne rejoue pas le serveur :
il intercepte les routes ``/api/workspace/memory/*`` (``page.route``) pour
fixer la forme de réponse, et vérifie ce que la page en fait — le rendu et
l'interaction, pas le moteur (couvert par ``tests/unit/tools/``).

Couvre : recherche au clavier (Entrée), requête vide (aucun appel de
recherche), clic et clavier sur un résultat (le panneau de détail le montre),
et les projets qu'on n'a pas pu lire, nommés avec leur motif — jamais un zéro
muet.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page, Route


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


def _fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _entry(entry_id: str, title: str, *, rank: int, type_: str = "decision", score: float = 0.5) -> dict:
    return {
        "id": entry_id,
        "text": title,
        "title": title,
        "excerpt": "",
        "kind": "souvenir",
        "type": type_,
        "projectSlug": "alpha",
        "projectName": "Alpha",
        "date": "2026-10-05T10:00:00+00:00",
        "source": "saisie libre",
        "provenance": {"source": {"kind": "saisie libre", "uri": ""}, "fraicheur_libelle": "non vérifié"},
        "freshness": "non_verifie",
        "tags": [],
        "supersession": {"state": "live", "replacedBy": None, "replaces": []},
        "score": score,
        "scoreKind": "bm25",
        "projectRank": rank,
        "fleetScore": 1 / (60 + rank),
    }


_RESULTS = {
    "query": "cache",
    "ranking": "rrf",
    "count": 2,
    "projects": [{"slug": "alpha", "name": "Alpha", "reason": None, "count": 2, "hiddenStale": 0, "hiddenOther": []}],
    "results": [
        _entry("mem-001", "le cache de session bloque le ticket", rank=1, score=0.91),
        _entry("mem-002", "second résultat", rank=2),
    ],
}


def _settled(page: Page) -> None:
    page.wait_for_function(
        "() => { const t = document.querySelector('.kn-summary')?.textContent || '';"
        " return t && !t.startsWith('Lecture'); }",
        timeout=30_000,
    )


def _open(page: Page) -> None:
    _goto(page, "memoire")
    page.wait_for_selector(".kn-searchbar input[type='search']")
    _settled(page)


def _stub_search(page: Page, payload: dict) -> list[str]:
    """Intercepte la recherche et rend la liste des URL réellement appelées."""
    calls: list[str] = []

    def _route(route: Route) -> None:
        calls.append(route.request.url)
        _fulfill(route, payload)

    page.route("**/api/workspace/memory/search*", _route)
    return calls


def _search(page: Page, term: str) -> None:
    field = page.locator(".kn-searchbar input[type='search']")
    field.fill(term)
    field.press("Enter")
    _settled(page)


def test_la_touche_entree_lance_la_recherche(workspace: Page) -> None:
    calls = _stub_search(workspace, _RESULTS)
    _open(workspace)

    _search(workspace, "cache")

    assert workspace.locator(".kn-res").count() == 2
    assert len(calls) == 1
    assert "q=cache" in calls[0]
    assert "2 résultats" in workspace.locator(".kn-summary").inner_text()


def test_une_requete_vide_n_appelle_pas_la_recherche(workspace: Page) -> None:
    calls = _stub_search(workspace, _RESULTS)
    _open(workspace)

    field = workspace.locator(".kn-searchbar input[type='search']")
    field.fill("   ")
    field.press("Enter")

    assert calls == [], "une requête vide ne doit déclencher aucune recherche"
    assert "entrée" in workspace.locator(".kn-summary").inner_text(), "la vue reste la liste des entrées"


def test_le_clic_sur_un_resultat_l_ouvre_dans_le_detail(workspace: Page) -> None:
    _stub_search(workspace, _RESULTS)
    _open(workspace)
    _search(workspace, "cache")

    workspace.locator(".kn-res").first.click()

    inspector = workspace.locator("#panel-inspector")
    assert "le cache de session bloque le ticket" in inspector.inner_text()
    assert "rang 1 dans son projet" in inspector.inner_text()
    assert "BM25 0.910" in inspector.inner_text()
    assert workspace.locator(".kn-res").first.get_attribute("aria-current") == "true"


@pytest.mark.parametrize("key", ["Enter", " "])
def test_le_clavier_ouvre_un_resultat_dans_le_detail(workspace: Page, key: str) -> None:
    _stub_search(workspace, _RESULTS)
    _open(workspace)
    _search(workspace, "cache")

    second = workspace.locator(".kn-res").nth(1)
    second.focus()
    second.press(key)

    assert "second résultat" in workspace.locator("#panel-inspector").inner_text()
    assert workspace.locator(".kn-res").first.get_attribute("role") == "button"
    assert workspace.locator(".kn-res").first.get_attribute("tabindex") == "0"


def test_un_projet_illisible_est_nomme_avec_son_motif(workspace: Page) -> None:
    failed = {
        **_RESULTS,
        "results": [],
        "count": 0,
        "projects": [{"slug": "alpha", "name": "Alpha", "reason": "backend qdrant injoignable", "count": 0}],
    }
    _stub_search(workspace, failed)
    _open(workspace)
    _search(workspace, "cache")

    assert "backend qdrant injoignable" in workspace.locator(".kn-note[data-note='failed']").inner_text()
    assert "non interrogé : backend qdrant injoignable" in workspace.locator(".kn-filters").inner_text()
    assert "Aucun résultat pour « cache »" in workspace.locator("#canvas").inner_text()


def test_un_projet_sans_memoire_dit_pourquoi_au_lieu_d_une_liste_vide(workspace: Page) -> None:
    page = {
        "project": {"slug": "", "name": "neuf"},
        "reason": "projet non initialisé (pas de project-context.yaml)",
        "entries": [], "offset": 0, "limit": 30, "nextOffset": None, "exhausted": False, "scanned": 0,
        "sharedWith": [], "counts": {"kind": {}, "type": {}},
        "hidden": {"otherProjects": [], "unattributed": 0, "superseded": 0, "retracted": 0, "filtered": 0},
    }
    workspace.route("**/api/workspace/memory/entries*", lambda route: _fulfill(route, page))
    _open(workspace)

    assert "projet non initialisé" in workspace.locator(".kn-note[data-note='failed']").inner_text()
    assert workspace.locator(".kn-more").is_hidden(), "pas de « Plus d'entrées » sans curseur"
