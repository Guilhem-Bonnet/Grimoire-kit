"""Composants partagés des espaces, mesurés sur le DOM rendu (P21).

Le pendant navigateur de ``tests/unit/test_workspace_components.py`` : la
feuille déclare ``nowrap`` et l'ellipse, ici on vérifie qu'un badge trop long
reste d'une ligne une fois peint ; la coque refuse ``aria-pressed`` à un
segment désactivé, ici on le constate sur les deux espaces qui en rendent
(Concevoir sans blueprint, Exécuter sans Mission Ledger).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

FIVE_SPACES = ("piloter", "concevoir", "executer", "observer", "memoire")
RETIRED = (
    ".pl-badge, .ex-badge, .pl-kpi, .ob-kpi, .me-kpi, "
    ".pl-table, .ob-table, .me-table, .cv-table"
)


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    page.wait_for_function("() => document.getElementById('canvas').getAttribute('aria-busy') === 'false'")


@pytest.mark.parametrize("space", FIVE_SPACES)
def test_aucun_espace_ne_rend_une_copie_prefixee(workspace: Page, space: str) -> None:
    _goto(workspace, space)
    assert workspace.locator(RETIRED).count() == 0


def test_un_badge_trop_long_reste_sur_une_ligne(workspace: Page) -> None:
    measured = workspace.evaluate(
        """async () => {
          const { badge } = await import('./shell.js');
          const box = document.createElement('div');
          box.style.width = '120px';
          const node = badge('mis à niveau, checkpoint destructif en attente', 'warn');
          box.append(node);
          document.body.append(box);
          const style = getComputedStyle(node);
          const result = {
            whiteSpace: style.whiteSpace,
            textOverflow: style.textOverflow,
            height: node.getBoundingClientRect().height,
            clipped: node.scrollWidth > node.clientWidth,
            fits: node.getBoundingClientRect().width <= 120,
            title: node.title,
          };
          box.remove();
          return result;
        }"""
    )
    assert measured["whiteSpace"] == "nowrap"
    assert measured["textOverflow"] == "ellipsis"
    assert measured["height"] == 22, "une seule ligne de 22 px, jamais deux"
    assert measured["clipped"] and measured["fits"], "le texte est coupé à la largeur du conteneur"
    assert measured["title"] == "mis à niveau, checkpoint destructif en attente"


@pytest.mark.parametrize("space", ["concevoir", "executer"])
def test_un_segment_desactive_n_est_jamais_presse(empty_workspace: Page, space: str) -> None:
    _goto(empty_workspace, space)
    disabled = empty_workspace.locator(".seg > button:disabled")
    disabled.first.wait_for()
    assert disabled.count() >= 1, "le projet vide doit rendre des segments désactivés"
    assert empty_workspace.locator('.seg > button[aria-pressed="true"]:disabled').count() == 0
