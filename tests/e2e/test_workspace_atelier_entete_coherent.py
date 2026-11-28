"""Atelier de spec, en-tête cohérent : trois défauts d'affichage relevés sur les captures de #1492 (#1477).

Un test par défaut, chacun écrit pour échouer sur l'état d'avant :

1. la liste dépliée de « Voir les N points restants » et la ligne « Marquées sans objet, non comptées » se suivent, sans recouvrement ;
2. la pastille d'en-tête reflète l'état réel dès la fin de l'écriture d'un bloc, sans relire le document (la réponse de l'écriture suffit) ;
3. la pastille et « Voir les N points restants » annoncent le même nombre : une rubrique sans objet ne compte pas (le gate ne change pas).

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures ; sans la variable, elles sont ignorées. Aucune attente à durée fixe.
"""

# Les fixtures de projet viennent du module voisin (importées pour être réutilisées, F811) ; le gestionnaire de route est défini
# et joué dans le tour de boucle de sa page (B023 sans objet : `for page in …` ne fait qu'un tour).
# ruff: noqa: F811, B023
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, Request, Route, expect

from grimoire.core import cadrage

from .test_workspace_atelier_assistant import APPEAR_MS
from .test_workspace_atelier_guidage_ecarts import (  # noqa: F401
    _chantier,
    _invite,
    _open,
    _points,
    _shot,
    guidage,
)

DETAIL = "[data-role='avancement-detail']"
PILL = "[data-role='avancement']"
OPEN_LIST = f"{DETAIL} ul:not([data-role='avancement-sans-objet'])"
NA_LIST = f"{DETAIL} [data-role='avancement-sans-objet']"


def _mark_sans_objet(page: Page, section: str) -> None:
    _invite(page, section).locator("[data-act='na-open']").click()
    page.locator("[data-role='na-reason']").press("Enter")
    page.locator(f"[data-invite='{section}'][data-etat='sans-objet']").wait_for(timeout=APPEAR_MS)


def _rect(page: Page, selector: str) -> dict[str, float]:
    return page.locator(selector).evaluate(
        "(n) => { const r = n.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; }",
    )


@pytest.mark.parametrize("size", [(1440, 900), (800, 900)])
def test_la_liste_et_la_ligne_sans_objet_se_suivent_sans_recouvrement(
    browser: Browser, guidage: tuple[str, Path], size: tuple[int, int],
) -> None:
    url, root = guidage
    slug = _chantier(root, f"Boites {size[0]}")
    for page in _open(browser, url, slug, size=size):
        _mark_sans_objet(page, "Pourquoi maintenant")
        page.locator(f"{DETAIL} > summary").click()
        expect(page.locator(NA_LIST)).to_be_visible()
        expect(page.locator(OPEN_LIST)).to_be_visible()
        _shot(page, f"entete-liste-{size[0]}")
        points, na = _rect(page, OPEN_LIST), _rect(page, NA_LIST)
        assert na["top"] >= points["bottom"] - 0.5, (points, na)  # la ligne vient après la liste, jamais par-dessus
        # Et rien de ce que la bulle contient ne sort d'une même boîte : la ligne reste dans le cadre de la liste.
        popover = _rect(page, f"{DETAIL} > [data-role='avancement-liste']")
        assert popover["top"] <= points["top"] + 0.5 and na["bottom"] <= popover["bottom"] + 0.5, (popover, points, na)


def test_la_pastille_suit_la_fin_de_l_ecriture_sans_relire_le_document(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Pastille apres ecriture")
    doc = re.compile(rf"/atelier/chantiers/{re.escape(slug)}(\?.*)?$")
    for page in _open(browser, url, slug):
        pill = page.locator(PILL)
        expect(pill).to_contain_text("rien n’est encore rempli", use_inner_text=True)
        _shot(page, "entete-pastille-avant")
        rereads: list[str] = []

        def refuse(route: Route, request: Request) -> None:  # toute relecture du document est comptée, et reste sans réponse
            rereads.append(request.url)
            route.abort()

        page.route(doc, refuse)
        _invite(page, "Le problème").locator("[data-act='invite-write']").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill("Je perds mes idées en réunion.")
        area.press("Control+Enter")
        # La réponse de l'écriture suffit : la pastille dit qu'une rubrique est remplie dès la fin de l'écriture.
        expect(pill).not_to_contain_text("rien n’est encore rempli", use_inner_text=True)
        expect(pill).to_contain_text("Cadrage en cours", use_inner_text=True)
        _shot(page, "entete-pastille-apres")
        assert rereads == [], rereads


def test_les_deux_nombres_de_points_concordent_et_le_gate_ne_bouge_pas(browser: Browser, guidage: tuple[str, Path]) -> None:
    url, root = guidage
    slug = _chantier(root, "Nombres concordants")
    for page in _open(browser, url, slug):
        _invite(page, "Le problème").locator("[data-act='invite-write']").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill("Je perds mes idées en réunion.")
        area.press("Control+Enter")
        expect(page.locator(PILL)).to_contain_text("Cadrage en cours", use_inner_text=True)
        before = cadrage.check(root, slug)
        _mark_sans_objet(page, "Pourquoi maintenant")
        listed = _points(page)
        expect(page.locator(PILL)).to_contain_text(f"{listed} points au gabarit", use_inner_text=True)
        pill = page.locator(PILL).text_content() or ""
        assert f"{listed} points au gabarit" in pill, (listed, pill)
        _shot(page, "entete-nombres")
        assert cadrage.check(root, slug) == before
