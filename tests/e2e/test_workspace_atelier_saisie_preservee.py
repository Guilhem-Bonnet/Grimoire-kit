"""Atelier de spec : aucun rechargement ne fait perdre une saisie ni sauter la lecture.

Un geste de la personne (accepter ou refuser une suggestion, poster ou résoudre un commentaire, « Actualiser », le retour sur
l'onglet) recharge le document. Le dessin est réconcilié par bloc : un bloc en cours d'édition, un commentaire ou une réponse en
cours ne sont JAMAIS remplacés (même zone de texte, même curseur, même sélection) ; si le serveur a changé le bloc entre-temps,
un bandeau le dit et le contrôle de conflit par bloc reste le juge à l'enregistrement. Filet de sécurité : le texte en cours est
gardé en `sessionStorage` et PROPOSÉ au retour (Reprendre / Jeter), jamais appliqué en silence.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import (
    APPEAR_MS,
    BRAINSTORM,
    CANVAS,
    PARA_A,
    _chantier,
    _folder,
    _suggest,
)

BLOCK_A = f".at-blk:has-text('{PARA_A[:24]}')"
TYPED = "Ma saisie en cours sur le premier paragraphe."
KEY_PREFIX = "grimoire.atelier.brouillon"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-saisie-preservee", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _open(browser: Browser, url: str, slug: str, *, block_session: bool = False) -> Iterator[tuple[Page, list[str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    if block_session:
        context.add_init_script("Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('bloqué'); } });")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator(".at-sheet .at-sec").first.wait_for(timeout=APPEAR_MS)
    try:
        yield page, errors
    finally:
        context.close()


def _reopen(page: Page) -> None:
    page.reload()
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator(".at-sheet .at-sec").first.wait_for(timeout=APPEAR_MS)


def _edit_a(page: Page, text: str = TYPED) -> None:
    """Ouvre le premier paragraphe en édition, y tape `text`, pose une sélection, et garde le nœud pour le comparer ensuite."""
    page.locator(BLOCK_A).click(position={"x": 8, "y": 8})
    area = page.locator("[data-role='block-edit'] textarea")
    area.wait_for(timeout=APPEAR_MS)
    area.fill(text)
    page.evaluate("() => { const a = document.querySelector(\"[data-role='block-edit'] textarea\"); a.focus(); a.setSelectionRange(3, 10); window.__area = a; }")


def _state(page: Page) -> list[object]:
    return page.evaluate(
        "() => { const a = window.__area; return [a.isConnected, a.value, a.selectionStart, a.selectionEnd, document.activeElement === a]; }"
    )


def _accept_elsewhere(page: Page) -> None:
    """Accepte la suggestion (sur un AUTRE bloc) sans faire défiler la page : le document est rechargé."""
    page.evaluate("() => document.querySelector(\"[data-act='accept']\").click()")
    page.wait_for_function("() => !document.querySelector('.at-sugg')", timeout=APPEAR_MS)
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")


def test_a_decision_elsewhere_keeps_the_block_being_edited(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Decision ailleurs", long=True)
    _suggest(root, slug, "Un second paragraphe plus court.")
    for page, errors in _open(browser, url, slug):
        _edit_a(page)
        _accept_elsewhere(page)
        assert _state(page) == [True, TYPED, 3, 10, True], "la zone de texte, son curseur et sa sélection sont restés"
        page.keyboard.type("XY")  # la saisie continue là où elle était
        assert page.locator("[data-role='block-edit'] textarea").input_value() == TYPED[:3] + "XY" + TYPED[10:]
        assert not errors


def test_a_comment_being_written_survives_a_decision_and_a_refresh(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Commentaire et actualiser", long=True)
    _suggest(root, slug, "Un second paragraphe plus court.")
    for page, errors in _open(browser, url, slug):
        page.locator(BLOCK_A).get_by_role("button", name="Commenter").click()
        area = page.locator("[data-role='comment-form'] textarea")
        area.fill("Un commentaire pas encore envoyé.")
        page.evaluate("() => { const a = document.querySelector(\"[data-role='comment-form'] textarea\"); a.focus(); a.setSelectionRange(2, 9); window.__area = a; }")
        _accept_elsewhere(page)
        assert _state(page) == [True, "Un commentaire pas encore envoyé.", 2, 9, True]
        # « Actualiser » (l'état des exigences) et le retour sur l'onglet (le document est relu) n'y touchent pas non plus.
        page.locator("[data-act='live-refresh']").click()
        with page.expect_response(lambda r: "/atelier/chantiers/" in r.url and r.request.method == "GET", timeout=APPEAR_MS):
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
        assert _state(page)[:4] == [True, "Un commentaire pas encore envoyé.", 2, 9]
        assert page.locator("[data-role='comment-form']").count() == 1
        assert not errors


def test_a_block_changed_on_the_server_while_editing_says_so_and_the_conflict_stays_the_judge(
    browser: Browser, project_on: tuple[str, Path]
) -> None:
    url, root = project_on
    slug = _chantier(root, "Conflit pendant saisie", long=True)
    path = _folder(root, slug) / BRAINSTORM
    for page, _errors in _open(browser, url, slug):
        _edit_a(page)
        path.write_text(path.read_text(encoding="utf-8").replace(PARA_A, "Version de l'agent, écrite pendant la saisie."), encoding="utf-8")
        with page.expect_response(lambda r: "/atelier/chantiers/" in r.url and r.request.method == "GET", timeout=APPEAR_MS):
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        banner = page.locator("[data-role='block-stale']")
        banner.wait_for(timeout=APPEAR_MS)
        assert banner.get_attribute("role") == "status" and "a changé" in banner.inner_text()
        assert _state(page)[:4] == [True, TYPED, 3, 10], "la saisie est intacte"
        assert page.locator(".is-editing").count() == 1, "le bloc modifié n'est pas dessiné en double"
        on_disk = path.read_bytes()
        page.locator("[data-role='block-edit'] textarea").press("Control+Enter")
        box = page.locator("[data-role='block-conflict']")
        box.wait_for(timeout=APPEAR_MS)
        assert TYPED in box.locator("[data-role='conflict-mine']").inner_text()
        assert "Version de l'agent" in box.locator("[data-role='conflict-theirs']").inner_text()
        assert path.read_bytes() == on_disk, "rien n'a été écrit : le conflit par bloc a jugé"


def test_a_block_deleted_on_the_server_while_editing_keeps_the_typing_and_says_so(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Suppression pendant saisie", long=True)
    path = _folder(root, slug) / BRAINSTORM
    for page, _errors in _open(browser, url, slug):
        _edit_a(page)
        path.write_text(path.read_text(encoding="utf-8").replace(f"{PARA_A}\n\n", ""), encoding="utf-8")
        with page.expect_response(lambda r: "/atelier/chantiers/" in r.url and r.request.method == "GET", timeout=APPEAR_MS):
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        page.locator("[data-role='block-stale']").wait_for(timeout=APPEAR_MS)
        assert "disparu" in page.locator("[data-role='block-stale']").inner_text()
        assert _state(page)[:4] == [True, TYPED, 3, 10]


def test_an_unsaved_block_draft_is_offered_after_a_page_reload_then_resumed_or_discarded(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Brouillon de bloc")
    path = _folder(root, slug) / BRAINSTORM
    before = path.read_bytes()
    for page, errors in _open(browser, url, slug):
        _edit_a(page)
        assert page.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }"), (
            "une saisie non enregistrée : la page avertit avant de se fermer"
        )
        _reopen(page)
        offer = page.locator(f"{BLOCK_A} [data-role='draft-offer']")
        offer.wait_for(timeout=APPEAR_MS)
        assert offer.get_attribute("role") == "status" and "Brouillon non enregistré" in offer.inner_text()
        assert page.locator(".is-editing").count() == 0, "jamais appliqué en silence"
        assert path.read_bytes() == before, "jamais envoyé au serveur"
        # Plus de saisie à l'écran : la page ne prévient plus.
        assert not page.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }")
        # Reprendre : au clavier.
        offer.get_by_role("button", name="Reprendre").focus()
        page.keyboard.press("Enter")
        area = page.locator("[data-role='block-edit'] textarea")
        area.wait_for(timeout=APPEAR_MS)
        assert area.input_value() == TYPED
        # Rechargée encore, puis jetée : effacé du stockage, plus proposé.
        _reopen(page)
        page.locator(f"{BLOCK_A} [data-role='draft-offer']").get_by_role("button", name="Jeter").click()
        assert page.locator("[data-role='draft-offer']").count() == 0
        assert page.evaluate("(p) => Object.keys(sessionStorage).filter((k) => k.startsWith(p)).length", KEY_PREFIX) == 0
        _reopen(page)
        assert page.locator("[data-role='draft-offer']").count() == 0
        assert not errors


def test_cancelling_or_saving_clears_the_draft(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Brouillon efface")
    count = "(p) => Object.keys(sessionStorage).filter((k) => k.startsWith(p)).length"
    for page, _errors in _open(browser, url, slug):
        _edit_a(page)
        assert page.evaluate(count, KEY_PREFIX) == 1
        page.locator("[data-role='block-edit'] textarea").press("Escape")
        assert page.evaluate(count, KEY_PREFIX) == 0
        _edit_a(page)
        assert page.evaluate(count, KEY_PREFIX) == 1
        page.locator("[data-role='block-edit'] textarea").press("Control+Enter")
        page.locator("[data-role='atelier-status']", has_text="Bloc enregistré").wait_for(timeout=APPEAR_MS)
        assert page.evaluate(count, KEY_PREFIX) == 0


def test_an_unsent_comment_draft_is_offered_on_the_block_after_a_reload(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Brouillon de commentaire")
    for page, _errors in _open(browser, url, slug):
        page.locator(BLOCK_A).get_by_role("button", name="Commenter").click()
        page.locator("[data-role='comment-form'] textarea").fill("Mon commentaire commencé.")
        _reopen(page)
        offer = page.locator(f"{BLOCK_A} [data-role='draft-offer'][data-kind='comment']")
        offer.wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='comment-form']").count() == 0, "jamais appliqué en silence"
        offer.get_by_role("button", name="Reprendre").click()
        assert page.locator("[data-role='comment-form'] textarea").input_value() == "Mon commentaire commencé."


def test_scroll_position_and_open_details_survive_a_reload_of_the_document(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Defilement conserve", long=True)
    _suggest(root, slug, "Un second paragraphe plus court.")
    for page, _errors in _open(browser, url, slug):
        page.wait_for_function("() => document.querySelector('[data-role=\"live-summary\"]')?.textContent.includes('REQ-001')", timeout=APPEAR_MS)
        toggle = page.locator("[data-live='REQ-001'] [data-act='toggle-detail']")
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        page.evaluate(f"{CANVAS}.scrollTop = 900")
        top = page.evaluate(f"{CANVAS}.scrollTop")
        assert top > 400
        page.evaluate("() => { window.__line = document.querySelector('[data-live=\"REQ-001\"]'); window.__last = document.querySelector('.at-sec:last-of-type .at-blk'); }")
        _accept_elsewhere(page)
        assert page.evaluate(f"{CANVAS}.scrollTop") == top, "la lecture n'a pas sauté"
        assert page.evaluate("() => document.querySelector('[data-live=\"REQ-001\"]') === window.__line"), "la ligne dépliée est le même nœud"
        assert page.locator("[data-live='REQ-001'] [data-act='toggle-detail']").get_attribute("aria-expanded") == "true"
        assert page.evaluate("() => !window.__last || document.querySelector('.at-sec:last-of-type .at-blk') === window.__last"), (
            "un bloc dont les données n'ont pas changé garde son nœud"
        )


def test_blocked_session_storage_does_not_break_the_editing(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Stockage bloque")
    for page, errors in _open(browser, url, slug, block_session=True):
        _edit_a(page)
        page.locator("[data-role='block-edit'] textarea").press("Control+Enter")
        page.locator("[data-role='atelier-status']", has_text="Bloc enregistré").wait_for(timeout=APPEAR_MS)
        assert TYPED in (_folder(root, slug) / BRAINSTORM).read_text(encoding="utf-8")
        assert not errors, errors
