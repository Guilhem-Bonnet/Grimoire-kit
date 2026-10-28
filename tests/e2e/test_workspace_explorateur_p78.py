"""P78 — pas de panneau Explorateur dans un espace sans arborescence.

Dans l'atelier, un espace qui n'écrit rien dans l'Explorateur (Travail) ne
montre ni le panneau, ni « Pas d'arborescence ici », ni son bouton de rail : la
place revient au contenu. Configuration › Fichiers, qui a une arborescence, les
garde.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .conftest import open_atelier

RAIL_EXPLORER = ".rail-btn[data-panel='explorer']"


def _go(page: Page, target: str, section: str) -> None:
    page.evaluate("(t) => window.GrimoireWorkspace.goto(t)", target)
    page.wait_for_function("(s) => window.GrimoireWorkspace.section === s", arg=section)
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=30_000)
    page.wait_for_load_state("networkidle")


def test_travail_n_affiche_ni_panneau_ni_indication(workspace: Page) -> None:
    open_atelier(workspace)
    _go(workspace, "fichiers", "fichiers")
    workspace.wait_for_selector("#explorer-body .sr-tree-file", timeout=30_000)
    narrow = workspace.locator("#canvas").bounding_box()
    _go(workspace, "travail", "travail")
    assert workspace.locator("#panel-explorer").is_hidden()
    assert "Pas d'arborescence ici" not in workspace.locator("body").inner_text()
    wide = workspace.locator("#canvas").bounding_box()
    assert narrow is not None and wide is not None
    assert wide["width"] > narrow["width"], "le contenu doit occuper la largeur libérée"


def test_bouton_de_rail_absent_sur_travail_present_sur_fichiers(workspace: Page) -> None:
    open_atelier(workspace)
    _go(workspace, "travail", "travail")
    assert workspace.locator(RAIL_EXPLORER).is_hidden()
    _go(workspace, "fichiers", "fichiers")
    workspace.wait_for_selector("#explorer-body .sr-tree-file", timeout=30_000)
    assert workspace.locator(RAIL_EXPLORER).is_visible()
    assert workspace.locator("#panel-explorer").is_visible()
    assert workspace.locator("#panel-explorer .sr-tree-dir, #panel-explorer .sr-tree-file").count() > 0


def test_l_epinglage_revient_sans_fuite_d_un_espace_a_l_autre(workspace: Page) -> None:
    open_atelier(workspace)
    _go(workspace, "fichiers", "fichiers")
    workspace.wait_for_selector("#explorer-body .sr-tree-file", timeout=30_000)
    assert workspace.locator("#panel-explorer").get_attribute("data-state") == "pinned"
    _go(workspace, "travail", "travail")
    assert workspace.locator("#panel-explorer").is_hidden()
    assert workspace.locator("#explorer-body .sr-tree-file").count() == 0
    _go(workspace, "fichiers", "fichiers")
    workspace.wait_for_selector("#explorer-body .sr-tree-file", timeout=30_000)
    assert workspace.locator("#panel-explorer").is_visible()
    assert workspace.locator("#panel-explorer").get_attribute("data-state") == "pinned"
