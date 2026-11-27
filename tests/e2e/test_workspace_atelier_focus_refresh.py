"""Atelier de spec : un rafraîchissement en direct ne fait jamais perdre sa place à la personne (clavier, lecteur d'écran).

Le pouls du cockpit, le retour sur l'onglet et la phase lue qui change au défilement redessinent des zones de l'Atelier.
Remplacer le nœud qui a le focus le renvoie au `body` : on perdait sa place au clavier (c'était la cause du test de la barre
collante, instable une fois sur trois), et une réponse en cours de saisie perdait son curseur.

Le rafraîchissement est déclenché à la demande (``focus`` de la fenêtre, comme au retour sur l'onglet : l'état en direct est
relu et le document rechargé), sans minuteur ni attente arbitraire.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_assistant import _init, _serve
from .test_workspace_atelier_mise_en_page import APPEAR_MS, CANVAS, _chantier, _open

ACTIVE = "() => { const a = document.activeElement; return a.getAttribute('data-act') || a.getAttribute('data-phase') || a.className || a.tagName; }"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-focus-refresh", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


@pytest.fixture(scope="module")
def sans_agent(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    root, _slug = _init(tmp_path_factory, "focus-guide", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root


def _ready(page: Page) -> None:
    page.locator(".at-sheet .at-sec").first.wait_for(timeout=APPEAR_MS)
    page.wait_for_function("() => document.querySelector('[data-role=\"live-summary\"]')?.textContent.includes('REQ-001')", timeout=APPEAR_MS)


def _refresh_live(page: Page) -> None:
    """Ce que fait le retour sur l'onglet : l'état en direct est relu (et dessiné) et le document est rechargé."""
    with page.expect_response(lambda r: "/trace" in r.url and r.request.method == "GET", timeout=APPEAR_MS):
        page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")  # le dessin suit la réponse


@pytest.mark.parametrize("selector", ["[data-act='split']", "[data-act='moa-why']", ".at-phase[data-phase='brainstorm']"])
def test_the_focus_survives_a_live_refresh(browser: Browser, project_on: tuple[str, Path], selector: str) -> None:
    url, root = project_on
    slug = _chantier(root, f"Focus rafraichi {len(selector)}", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        target = page.locator(selector)
        target.focus()
        before = page.evaluate(ACTIVE)
        page.evaluate("(s) => { window.__same = document.querySelector(s); }", selector)
        for _ in range(3):
            _refresh_live(page)
            assert page.evaluate(ACTIVE) == before, f"le focus a quitté {selector} : {page.evaluate(ACTIVE)}"
        # En place : le nœud qui porte le focus est le même, il n'a pas été remplacé.
        assert page.evaluate("(s) => document.querySelector(s) === window.__same", selector)
        page.keyboard.press("Tab")
        assert page.evaluate(ACTIVE) != before  # le clavier repart de là où il était, pas du début de la page


def test_the_live_line_keeps_its_focus_and_its_open_detail(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Ligne en direct", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        toggle = page.locator("[data-live='REQ-001'] [data-act='toggle-detail']")
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        toggle.focus()
        page.evaluate("() => { window.__line = document.querySelector('[data-live=\"REQ-001\"]'); }")
        _refresh_live(page)
        assert page.evaluate(ACTIVE) == "toggle-detail"
        assert page.evaluate("() => document.querySelector('[data-live=\"REQ-001\"]') === window.__line")  # pas redessinée
        assert page.locator("[data-live='REQ-001'] [data-act='toggle-detail']").get_attribute("aria-expanded") == "true"


def test_a_typed_guided_answer_keeps_its_text_and_its_cursor(browser: Browser, sans_agent: tuple[str, Path]) -> None:
    url, root = sans_agent
    slug = _chantier(root, "Entretien et rafraichissement", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator("[data-act='start']").click()
        area = page.locator(".at-asst-area")
        area.wait_for(timeout=APPEAR_MS)
        area.fill("Les mainteneurs du kit et leurs agents.")
        page.evaluate("() => { const a = document.querySelector('.at-asst-area'); a.focus(); a.setSelectionRange(4, 14); window.__old = a; }")
        # La phase lue change au défilement : le panneau de l'assistant est redessiné (la question dépend de la phase).
        page.evaluate(f"{CANVAS}.scrollTop = {CANVAS}.scrollHeight")
        page.wait_for_function("() => !window.__old.isConnected", timeout=APPEAR_MS)  # le champ a bien été refait
        state = page.evaluate(
            "() => { const a = document.activeElement; return [a.className, a.value, a.selectionStart, a.selectionEnd, a === document.querySelector('.at-asst-area')]; }"
        )
        assert state == ["at-asst-area", "Les mainteneurs du kit et leurs agents.", 4, 14, True], state
        # Et la saisie continue là où elle était.
        page.keyboard.type("XY")
        typed = "Les mainteneurs du kit et leurs agents."
        assert page.locator(".at-asst-area").input_value() == typed[:4] + "XY" + typed[14:]  # la sélection a été remplacée


def test_a_refresh_does_not_steal_the_focus(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Sans vol de focus", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        page.locator(".at-back").focus()
        _refresh_live(page)
        assert page.evaluate(ACTIVE) == "at-btn ghost at-back"
        page.evaluate("() => document.activeElement.blur()")
        _refresh_live(page)
        assert page.evaluate("() => document.activeElement === document.body")
