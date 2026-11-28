"""Atelier de spec, guidage : les écarts relevés par un rejeu d'usage de la version publiée (#1481, #1477).

Un test par écart, chacun écrit pour échouer sur l'état d'avant :

1. les « points restants » de l'en-tête ne comptent plus une rubrique marquée « sans objet » ;
2. le bloc « Ce qu'il me reste à faire » montre l'avancement de ce qui est utile et l'étape suivante conseillée ;
3. la consigne (explication, exemple) reste sous les yeux pendant la saisie, en police de lecture, avec une invite humaine ;
4. « Sans objet » annonce sa raison facultative, et Entrée valide directement ;
5. « Agrandir » agrandit aussi la question et la zone de réponse entre 600 et 1100 px ;
6. le bloc est replié à 900 px de conteneur, inclus ;
7. en lecture, ni le bloc ni les invites d'écriture ; une ligne dit que le guidage est masqué ;
8. le bandeau de l'entretien rassure en une phrase, le détail technique est replié.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures ; sans la variable, elles sont ignorées. Aucune attente à durée fixe.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Locator, Page, expect

from grimoire.core import cadrage
from grimoire.core.chantiers import new_chantier

from .test_workspace_atelier_assistant import APPEAR_MS, _init, _serve


@pytest.fixture(scope="module")
def guidage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    root, _slug = _init(tmp_path_factory, "guidage-ecarts", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root


def _open(browser: Browser, url: str, slug: str, *, size: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _shot(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _invite(page: Page, section: str) -> Locator:
    return page.locator(f"[data-invite='{section}'][data-role='empty-slot']")


def _chantier(root: Path, title: str) -> str:
    return new_chantier(root, title).slug


def _points(page: Page) -> int:
    """Le nombre de « points restants » annoncé par l'en-tête (la phrase qui déplie la liste des rubriques au gabarit)."""
    summary = page.locator("[data-role='avancement-detail'] > summary")
    summary.wait_for(state="attached", timeout=APPEAR_MS)
    found = re.search(r"(\d+)", summary.text_content() or "")
    assert found, summary.text_content()
    return int(found.group(1))


def test_une_rubrique_sans_objet_ne_compte_plus_dans_les_points_restants(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Compteurs")
    before = cadrage.check(root, slug)
    for page in _open(browser, url, slug):
        total = _points(page)
        _shot(page, "01-avant-sans-objet")
        _invite(page, "Pourquoi maintenant").locator("[data-act='na-open']").click()
        page.locator("[data-role='na-reason']").press("Enter")
        page.locator("[data-invite='Pourquoi maintenant'][data-etat='sans-objet']").wait_for(timeout=APPEAR_MS)
        expect(page.locator("[data-role='avancement-detail'] > summary")).to_contain_text(
            f"Voir les {total - 1} points restants", use_inner_text=False,
        )
        detail = page.locator("[data-role='avancement-detail']")
        page.locator("[data-role='avancement-detail'] > summary").click()
        # La liste dépliée ne cite plus la rubrique parmi les points à faire ; une ligne à part la dit sans objet.
        expect(detail.locator("ul:not([data-role='avancement-sans-objet'])")).not_to_contain_text("Pourquoi maintenant")
        expect(detail.locator("[data-role='avancement-sans-objet']")).to_contain_text("Pourquoi maintenant")
        _shot(page, "01-apres-sans-objet")
        assert cadrage.check(root, slug) == before  # le contrôle de validation ne bouge pas


def test_le_reste_a_faire_montre_l_avancement_de_l_utile_et_l_etape_suivante(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Avancement utile")
    for page in _open(browser, url, slug):
        title = page.locator("[data-role='reste-titre']")
        expect(title).to_have_text("Il manque 11 choses pour pouvoir valider :", use_inner_text=True)
        step = page.locator("[data-role='reste-etape']")
        expect(step).to_contain_text("Brief › Le problème", use_inner_text=True)
        _shot(page, "02-avant-saisie")
        _invite(page, "Le problème").locator("[data-act='invite-write']").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill("Je perds mes idées en réunion.")
        area.press("Control+Enter")
        progress = page.locator("[data-role='reste-avancement']")
        expect(progress).to_contain_text("Brief : 1 rubrique utile sur 3 remplie", use_inner_text=True)
        expect(step).to_contain_text("Brief › L'utilisateur", use_inner_text=True)
        expect(title).to_have_text("Il manque 11 choses pour pouvoir valider :", use_inner_text=True)  # le nécessaire ne bouge pas
        _shot(page, "02-apres-saisie")


def test_la_consigne_reste_visible_pendant_la_saisie(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Consigne pendant la saisie")
    for page in _open(browser, url, slug):
        _shot(page, "03-avant-saisie")
        _invite(page, "Le problème").locator("[data-act='invite-write']").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.wait_for(timeout=APPEAR_MS)
        guide = page.locator("[data-role='edit-guide']")
        expect(guide.locator("[data-role='invite-quoi']")).to_contain_text("Ce qui ne va pas aujourd'hui", use_inner_text=True)
        expect(guide.locator("[data-role='invite-exemple']")).to_contain_text("Exemple", use_inner_text=True, ignore_case=True)
        placeholder = area.get_attribute("placeholder") or ""
        assert placeholder and "À compléter" not in placeholder, placeholder
        families = area.evaluate(
            "(a) => [getComputedStyle(a).fontFamily, getComputedStyle(document.querySelector('.at-sheet')).fontFamily]",
        )
        assert families[0] == families[1], families  # police de lecture, pas la chasse fixe
        _shot(page, "03-apres-saisie")


def test_sans_objet_annonce_sa_raison_facultative(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Sans objet annoncé")
    for page in _open(browser, url, slug):
        skip = _invite(page, "Pourquoi maintenant").locator("[data-act='na-open']")
        assert "raison facultative" in (skip.get_attribute("title") or "")
        skip.click()
        expect(page.locator("[data-role='na-help']")).to_contain_text("facultatif", use_inner_text=True)
        expect(page.locator("[data-role='na-help']")).to_contain_text("Entrée valide directement", use_inner_text=True)
        _shot(page, "04-apres-sans-objet")
        page.locator("[data-role='na-reason']").press("Enter")
        page.locator("[data-invite='Pourquoi maintenant'][data-etat='sans-objet']").wait_for(timeout=APPEAR_MS)


def test_agrandir_grandit_la_question_et_la_reponse_sous_1100_px(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Agrandir moyen")
    for page in _open(browser, url, slug, size=(1000, 900)):
        pill = page.locator("[data-role='assistant-pill']")
        if pill.is_visible():
            pill.click()
        page.locator("[data-act='start']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=APPEAR_MS)
        area = page.locator(".at-asst-area")
        _shot(page, "05-avant-agrandir")
        page.locator("[data-act='grow']").click()
        expect(page.locator("[data-act='grow']")).to_have_text("Réduire", use_inner_text=True)
        measure = (
            "(a) => ({ size: parseFloat(getComputedStyle(a).fontSize), min: parseFloat(getComputedStyle(a).minHeight),"
            " line: parseFloat(getComputedStyle(a).lineHeight) })"
        )
        got = area.evaluate(measure)
        assert got["size"] >= 16, got
        assert got["min"] >= 8 * got["line"], got
        assert page.locator(".at-asst-qtext").evaluate("(q) => parseFloat(getComputedStyle(q).fontSize)") > 20
        _shot(page, "05-apres-agrandir")
        page.locator("[data-act='grow']").click()  # ne laisse pas le choix à la suite


def _width_gap(browser: Browser, url: str, slug: str) -> int:
    """L'écart entre la fenêtre et la largeur du conteneur de la page (barre latérale comprise)."""
    for page in _open(browser, url, slug, size=(1200, 900)):
        return 1200 - int(page.evaluate("() => document.querySelector('.at-page').parentElement.clientWidth"))
    raise AssertionError


def test_le_reste_a_faire_est_replie_a_900_px_inclus(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Seuil de repli")
    gap = _width_gap(browser, url, slug)
    for container, folded in ((900, True), (901, False)):
        for page in _open(browser, url, slug, size=(container + gap, 900)):
            width = page.evaluate("() => document.querySelector('.at-page').parentElement.clientWidth")
            assert width == container, (width, container)
            reste = page.locator("[data-role='reste-a-faire']")
            reste.wait_for(timeout=APPEAR_MS)
            if folded:
                expect(reste).not_to_have_attribute("open", re.compile(".*"))
                _shot(page, "06-900-replie")
            else:
                expect(reste).to_have_attribute("open", re.compile(".*"))


def test_en_lecture_ni_le_bloc_ni_les_invites_et_une_ligne_le_dit(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Lecture pure")
    for page in _open(browser, url, slug):
        expect(page.locator("[data-role='reste-a-faire']")).to_be_visible()
        _invite(page, "Le problème").wait_for(timeout=APPEAR_MS)
        _shot(page, "07-avant-lecture")
        page.locator("[data-role='atelier-lecture']").click()
        expect(page.locator("[data-role='reste-a-faire']")).to_be_hidden()
        expect(page.locator("[data-role='empty-slot'].at-invite:visible")).to_have_count(0)
        note = page.locator("[data-role='lecture-guidage']")
        expect(note).to_contain_text("Mode lecture : le guidage est masqué", use_inner_text=True)
        _shot(page, "07-apres-lecture")
        note.locator("[data-act='lecture-quitter']").click()
        expect(page.locator("[data-role='reste-a-faire']")).to_be_visible()
        expect(note).to_be_hidden()
        expect(_invite(page, "Le problème")).to_be_visible()


def test_le_bandeau_de_l_entretien_rassure_et_replie_le_technique(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Bandeau rassurant")
    for page in _open(browser, url, slug):
        off = page.locator("[data-role='assistant-off']")
        off.wait_for(timeout=APPEAR_MS)
        _shot(page, "08-bandeau")
        expect(off).to_contain_text("Les questions fonctionnent sans modèle", use_inner_text=True)
        expect(off).not_to_contain_text("Aucun agent", use_inner_text=True)  # le détail technique est replié
        more = page.locator("[data-role='assistant-off-detail']")
        expect(more).not_to_have_attribute("open", re.compile(".*"))
        more.locator("summary").click()
        expect(more).to_contain_text("grimoire cadrage interview", use_inner_text=True)
