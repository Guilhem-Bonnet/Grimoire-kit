"""Atelier de spec, guidage : dire ce qui est attendu, rubrique par rubrique, sans IA (#1197).

Le premier vrai usage de l'Atelier : « je ne sais pas ce que je dois remplir, ni si j'ai mis les infos aux bonnes places ».
Ici : l'invite de chaque rubrique vide (ce qu'on y met, un exemple, son statut tiré du gate), « ce qu'il me reste à faire »,
une rubrique « sans objet » qui ne change pas le résultat de ``cadrage check``, l'entretien guidé lisible (reste en mots,
destination de la réponse, accusé de rangement) et le bouton « Agrandir » mémorisé par navigateur.

Chaque attente porte sur l'état voulu (jamais une lecture instantanée après un geste).
``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures (1440x900 et 480 px, clair et sombre) ; sans la variable, elles sont ignorées.
"""

from __future__ import annotations

import os
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

    root, _slug = _init(tmp_path_factory, "guidage-on", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root


def _open(
    browser: Browser, url: str, slug: str, *, size: tuple[int, int] = (1440, 900), dark: bool = False
) -> Iterator[Page]:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce",
        color_scheme="dark" if dark else "light",
    )
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
    return page.locator(f"[data-invite='{section}']")


def _centre(page: Page, section: str) -> None:
    """Met l'invite d'une rubrique au milieu de la fenêtre visible (sous la barre collante), pour la capture."""
    _invite(page, section).evaluate("(node) => node.scrollIntoView({ block: 'center' })")


def _chantier(root: Path, title: str) -> str:
    return new_chantier(root, title).slug


def test_une_rubrique_vide_dit_ce_qu_on_y_met_son_exemple_et_son_statut(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Invites des rubriques")
    for page in _open(browser, url, slug):
        later = _invite(page, "Pourquoi maintenant")
        later.wait_for(timeout=APPEAR_MS)
        expect(later.locator("[data-role='invite-quoi']")).to_contain_text("Ce qui rend le sujet urgent", use_inner_text=True)
        expect(later.locator("[data-role='invite-exemple']")).to_contain_text("Exemple", use_inner_text=True, ignore_case=True)
        expect(later.locator("[data-role='invite-statut']")).to_have_text("peut rester vide", use_inner_text=True)
        expect(_invite(page, "Le problème").locator("[data-role='invite-statut']")).to_have_text("utile", use_inner_text=True)
        need = _invite(page, "Fonctionnelles").locator("[data-role='invite-statut']")
        expect(need).to_have_text("nécessaire pour valider", use_inner_text=True)
        # Plus de « À compléter — … » nu : l'invite le remplace, et le jargon est expliqué entre parenthèses.
        expect(page.locator(".at-sheet")).not_to_contain_text("À compléter —", use_inner_text=True)
        expect(_invite(page, "Fonctionnelles")).to_contain_text("Must (indispensable)", use_inner_text=True)
        _centre(page, "Fonctionnelles")
        _shot(page, "01-rubrique-vide-invite-1440x900-clair")


def test_ce_qu_il_me_reste_a_faire_suit_les_ecritures(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Reste à faire")
    for page in _open(browser, url, slug):
        title = page.locator("[data-role='reste-titre']")
        expect(title).to_have_text("Il manque 11 choses pour pouvoir valider :", use_inner_text=True)
        expect(page.locator("[data-role='reste-facultatif']")).to_contain_text("Le reste est facultatif", use_inner_text=True)
        _shot(page, "02-reste-a-faire-1440x900-clair")
        # Un lien défile jusqu'à la rubrique.
        page.locator("[data-role='reste-necessaire'] [data-act='reste-go'][data-section='Jalons']").click()
        expect(page.locator(".at-rub", has_text="Jalons")).to_be_in_viewport()
        # Écrire une rubrique nécessaire : il en manque une de moins, sans recharger la page.
        _invite(page, "Non-fonctionnelles").locator("[data-act='invite-write']").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill("Réponse en moins d’une seconde.")
        area.press("Control+Enter")
        expect(title).to_have_text("Il manque 10 choses pour pouvoir valider :", use_inner_text=True)
        expect(page.locator("[data-role='reste-necessaire'] [data-section='Non-fonctionnelles']")).to_have_count(0)


def test_sans_objet_replie_sans_changer_le_gate(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Sans objet")
    before = cadrage.check(root, slug)
    for page in _open(browser, url, slug):
        title = page.locator("[data-role='reste-titre']")
        expect(title).to_have_text("Il manque 11 choses pour pouvoir valider :", use_inner_text=True)
        # Une rubrique facultative : repliée, sortie de ce qu'il reste à faire.
        _invite(page, "Pourquoi maintenant").locator("[data-act='na-open']").click()
        page.locator("[data-role='na-reason']").fill("Aucune date imposée")
        page.locator("[data-act='na-confirm']").click()
        folded = page.locator("[data-invite='Pourquoi maintenant'][data-etat='sans-objet']")
        folded.wait_for(timeout=APPEAR_MS)
        expect(folded.locator("[data-role='na-note']")).to_contain_text("Aucune date imposée", use_inner_text=True)
        expect(folded.locator("[data-role='na-note']")).to_contain_text("n’est plus comptée", use_inner_text=True)
        assert cadrage.check(root, slug) == before  # exactement le même verdict pour `cadrage check`
        text = (root / "_grimoire" / "cadrage" / slug / "01-brief.md").read_text(encoding="utf-8")
        assert "> À compléter — sans objet pour ce besoin : Aucune date imposée (à confirmer)" in text
        # Une rubrique nécessaire : « sans objet d'après toi », elle reste à confirmer et dans la liste.
        _invite(page, "Non-fonctionnelles").locator("[data-act='na-open']").click()
        page.locator("[data-role='na-reason']").press("Enter")
        need = page.locator("[data-invite='Non-fonctionnelles'][data-etat='sans-objet']")
        need.wait_for(timeout=APPEAR_MS)
        expect(need.locator("[data-role='na-note']")).to_contain_text("restera à confirmer pour valider", use_inner_text=True)
        assert cadrage.check(root, slug) == before
        expect(title).to_have_text("Il manque 11 choses pour pouvoir valider :", use_inner_text=True)
        expect(page.locator("[data-role='reste-necessaire'] li", has_text="Non-fonctionnelles")).to_contain_text(
            "sans objet d’après toi ; à confirmer", use_inner_text=True,
        )
        _shot(page, "03-sans-objet-1440x900-clair")
        # Remettre à compléter : l'invite revient, le gabarit du kit est réécrit.
        need.locator("[data-act='na-undo']").click()
        _invite(page, "Non-fonctionnelles").and_(page.locator("[data-etat]")).wait_for(state="detached", timeout=APPEAR_MS)
        expect(_invite(page, "Non-fonctionnelles").locator("[data-role='invite-statut']")).to_have_text(
            "nécessaire pour valider", use_inner_text=True,
        )
        assert cadrage.check(root, slug) == before
        assert "sans objet" not in (root / "_grimoire" / "cadrage" / slug / "04-exigences.md").read_text(encoding="utf-8")


def test_entretien_lisible_reste_en_mots_destination_et_accuse(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Entretien lisible")
    for page in _open(browser, url, slug):
        page.locator("[data-act='start']").click()
        question = page.locator("[data-role='guided-question']")
        question.wait_for(timeout=APPEAR_MS)
        remaining = page.locator("[data-role='guided-remaining']")
        expect(remaining).to_contain_text("Encore", use_inner_text=True)
        expect(remaining).to_contain_text("minute", use_inner_text=True)
        expect(question).not_to_contain_text(" sur ", use_inner_text=True)
        expect(page.locator("[data-role='guided-why']")).not_to_be_empty()
        expect(page.locator("[data-role='guided-dest']")).to_contain_text("Ta réponse ira dans : L'utilisateur", use_inner_text=True)
        # La zone de réponse a une vraie place : au moins cinq lignes, qui grandit avec le texte.
        area = page.locator(".at-asst-area")
        small = area.bounding_box()
        assert small is not None and small["height"] >= 5 * 18
        area.fill("\n".join(f"Ligne {n} de ma réponse." for n in range(1, 16)))
        page.wait_for_function(
            "(h) => document.querySelector('.at-asst-area').getBoundingClientRect().height > h", arg=small["height"] + 40,
        )
        _shot(page, "04-entretien-1440x900-clair")
        area.fill("Les mainteneurs du kit.")
        area.press("Enter")
        ack = page.locator("[data-role='guided-ack']")
        ack.wait_for(timeout=APPEAR_MS)
        expect(ack).to_contain_text("Ta réponse est rangée dans : L'utilisateur (Brief)", use_inner_text=True)
        expect(page.locator("[data-role='guided-dest']")).to_contain_text("Le problème", use_inner_text=True)  # la 2e question vise une autre rubrique
        _shot(page, "05-accuse-de-rangement-1440x900-clair")
        ack.locator("[data-act='ack-show']").click()
        expect(page.locator(".at-rub", has_text="L'utilisateur")).to_be_in_viewport()


def test_agrandir_met_l_entretien_au_centre_et_se_memorise(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Entretien agrandi")
    for page in _open(browser, url, slug):
        page.locator("[data-act='start']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=APPEAR_MS)
        grow = page.locator("[data-act='grow']")
        expect(grow).to_have_text("Agrandir", use_inner_text=True)
        grow.click()
        expect(page.locator(".at-doc-grid.at-asst-big")).to_have_count(1)
        expect(page.locator(".at-sheet")).to_be_hidden()
        expect(page.locator("[data-role='document-rail']")).to_be_visible()
        expect(grow).to_have_text("Réduire", use_inner_text=True)
        _shot(page, "06-entretien-agrandi-1440x900-clair")
        # Mémorisé par navigateur : il revient agrandi après un rechargement.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator("[data-role='guided-question']").wait_for(timeout=APPEAR_MS)
        expect(page.locator(".at-doc-grid.at-asst-big")).to_have_count(1)
        # Le rail rend le document ; le choix suit.
        page.locator("[data-role='document-rail']").click()
        expect(page.locator(".at-doc-grid.at-asst-big")).to_have_count(0)
        expect(page.locator(".at-sheet")).to_be_visible()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        expect(page.locator(".at-doc-grid.at-asst-big")).to_have_count(0)


def test_ecran_etroit_et_theme_sombre(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Écran étroit")
    for page in _open(browser, url, slug, size=(480, 900), dark=True):
        _invite(page, "Pourquoi maintenant").wait_for(timeout=APPEAR_MS)
        expect(page.locator("[data-role='reste-titre']")).to_contain_text("Il manque", use_inner_text=True)
        page.wait_for_function("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        _shot(page, "07-reste-a-faire-480-sombre")
        _centre(page, "Fonctionnelles")
        _shot(page, "08-rubrique-vide-invite-480-sombre")
    for page in _open(browser, url, slug, size=(1440, 900), dark=True):
        _centre(page, "Fonctionnelles")
        _shot(page, "09-rubrique-vide-invite-1440x900-sombre")
        page.locator("[data-act='start']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=APPEAR_MS)
        _shot(page, "10-entretien-1440x900-sombre")
        page.locator("[data-act='grow']").click()
        expect(page.locator(".at-doc-grid.at-asst-big")).to_have_count(1)
        _shot(page, "11-entretien-agrandi-1440x900-sombre")
        page.locator("[data-act='grow']").click()  # ne laisse pas le choix à la suite
    for page in _open(browser, url, slug, size=(480, 900)):
        page.locator("[data-role='assistant-pill']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=APPEAR_MS)
        page.wait_for_function("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        _shot(page, "12-entretien-480-clair")
        page.locator("[data-act='grow']").click()
        expect(page.locator("[data-act='grow']")).to_have_text("Réduire", use_inner_text=True)
        page.wait_for_function("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        _shot(page, "13-entretien-agrandi-480-clair")
        page.locator("[data-act='grow']").click()  # ne laisse pas le choix à la suite
