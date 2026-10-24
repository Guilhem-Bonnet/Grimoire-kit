"""Concevoir — navigation clavier, boutons et zoom restés sans test (#536).

Complète ``test_workspace_concevoir.py`` (qui ne navigue qu'à la souris) :
``Enter`` sur une carte et sur un nœud, le bouton « Ouvrir le workflow » de
l'inspecteur, « Simuler » et la carte projet du niveau Flotte du cockpit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page


@pytest.fixture
def concevoir(workspace: Page, project_with_blueprint: tuple[Path, str]) -> tuple[Page, str]:
    _root, bp_id = project_with_blueprint
    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
    workspace.wait_for_selector(".cv-card", timeout=15_000)
    return workspace, bp_id


def _zoom(page: Page) -> str:
    return page.locator('#zoom-seg button[aria-pressed="true"]').inner_text().strip()


def test_entree_sur_une_carte_ouvre_le_workflow(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    card = page.locator(".cv-card").first
    card.focus()

    card.press("Enter")

    page.wait_for_selector(".cv-node", timeout=15_000)
    assert _zoom(page) == "Workflow"
    assert page.locator(".cv-svg path").count() >= 1


def test_le_bouton_ouvrir_le_workflow_de_l_inspecteur_equivaut_au_double_clic(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    page.locator(".cv-card").first.click()

    page.locator("#inspector-body .btn", has_text="Ouvrir le workflow").click()

    page.wait_for_selector(".cv-node", timeout=15_000)
    assert _zoom(page) == "Workflow"


def test_simuler_ecrit_le_verdict_dans_le_dock_problemes(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    with page.expect_response(
        lambda r: r.url.split("?")[0].endswith("/simulate") and r.request.method == "POST"
    ) as seen:
        page.get_by_role("button", name="Simuler").click()

    assert seen.value.status == 200
    page.locator('[data-dock-tab="problemes"]').click()
    page.wait_for_function("() => document.querySelector('#dock-body')?.textContent.includes('verdict :')")
    dock = page.locator("#dock-body").inner_text()
    assert "blueprint simulate" in dock
    assert "verdict :" in dock


def test_simuler_refuse_est_dit_dans_le_dock(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    page.route(
        "**/simulate*",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"ok": false, "error": "graphe invalide"}'
        ),
    )

    page.get_by_role("button", name="Simuler").click()

    page.locator('[data-dock-tab="problemes"]').click()
    page.wait_for_function("() => document.querySelector('#dock-body')?.textContent.includes('simulation refusée')")
    assert "graphe invalide" in page.locator("#dock-body").inner_text()


def test_le_double_clic_sur_un_noeud_zoome_au_niveau_noeud(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    page.locator(".cv-node").first.dblclick()

    page.wait_for_selector(".cv-tab")
    assert _zoom(page) == "Nœud"


def test_entree_sur_un_noeud_zoome_au_niveau_noeud(concevoir: tuple[Page, str]) -> None:
    page, _bp = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    node = page.locator(".cv-node").first
    node.focus()

    node.press("Enter")

    page.wait_for_selector(".cv-tab")
    assert _zoom(page) == "Nœud"
    assert [t.strip() for t in page.locator(".cv-tab").all_inner_texts()] == [
        "Propriétés",
        "Validation",
        "Coût",
        "Preuves",
    ]


def test_la_carte_projet_de_la_flotte_ouvre_ce_projet(
    browser: Browser, served_cockpit_multi: tuple[str, str, str]
) -> None:
    served, slug_a, slug_b = served_cockpit_multi
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
        page.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
        page.locator("#zoom-seg button", has_text="Flotte").click()
        page.wait_for_selector(".cv-cards .cv-card")
        slugs = [c.locator(".mono").inner_text() for c in page.locator(".cv-cards .cv-card").all()]
        assert {slug_a, slug_b} <= set(slugs)

        page.locator(".cv-cards .cv-card", has_text=slug_b).click()

        page.wait_for_url(f"**?project={slug_b}*")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        assert page.evaluate("() => new URLSearchParams(location.search).get('project')") == slug_b
    finally:
        context.close()
