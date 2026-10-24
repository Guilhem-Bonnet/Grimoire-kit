"""Concevoir — les segments du zoom (#536).

Régression : sélectionner un blueprint d'un simple clic puis cliquer le
segment « Workflow » du zoom laissait l'écran sur « Chargement du blueprint… »
pour toujours. Seul le double-clic (``zoomToWorkflow``) chargeait le blueprint ;
le segment ne faisait que changer de niveau.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page


@pytest.fixture
def concevoir(workspace: Page, project_with_blueprint: tuple[Path, str]) -> tuple[Page, str]:
    _root, bp_id = project_with_blueprint
    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
    workspace.wait_for_selector(".cv-card", timeout=15_000)
    return workspace, bp_id


def _zoom(page: Page) -> str:
    return page.locator('#zoom-seg button[aria-pressed="true"]').inner_text().strip()


def test_les_segments_du_zoom_changent_de_niveau(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    assert [b.inner_text().strip() for b in page.locator("#zoom-seg button").all()] == [
        "Flotte",
        "Projet",
        "Workflow",
        "Nœud",
    ], "`grimoire serve` est un alias du cockpit : quatre niveaux"
    assert _zoom(page) == "Projet"
    page.locator(".cv-card").first.click()

    page.locator("#zoom-seg button", has_text="Workflow").click()
    page.wait_for_selector(".cv-node", timeout=15_000)
    assert _zoom(page) == "Workflow"

    page.locator(".cv-node").first.click()
    page.locator("#zoom-seg button", has_text="Nœud").click()
    page.wait_for_selector(".cv-tab")
    assert _zoom(page) == "Nœud"

    page.locator("#zoom-seg button", has_text="Projet").click()
    page.wait_for_selector(".cv-card")
    assert _zoom(page) == "Projet"
