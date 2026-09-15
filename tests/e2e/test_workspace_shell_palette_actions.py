"""Exécuter une entrée de palette, pour chaque section — pas seulement l'ouvrir.

``tests/e2e/test_workspace_shell.py`` prouve déjà la mécanique générique de la
palette (navigation clavier, ouverture, entrées « Espaces »/« Commandes »
sélectionnées puis lancées, entrées « Fichiers » qui ouvrent Source). Trois
sections restaient non exercées jusqu'au bout — leur *présence* était testée
ailleurs (``test_workspace_controls.py`` pour le chip « Projets »), jamais le
clic qui déclenche vraiment leur ``run()`` (``web/workspace/shell.js::
buildPalette``) :

- « Tâches » — ``run: () => runCommand(['task', 'show', t.id])`` : exécute une
  vraie sous-commande de la Console, pas une navigation ;
- « Workflows » — ``run: () => goto('concevoir')`` ;
- « Projets » (cockpit seulement) — ``run: () => { location.search = '?project=' + … }`` :
  seul point de la palette qui recharge la page entière plutôt que de
  naviguer côté client.

Ce module ajoute aussi les deux raccourcis clavier du même bloc
(``bindShortcuts``, ``shell.js``) jamais pressés réellement par un test :
``⌘1``…``⌘6`` (changer d'espace) et `` ` `` (ouvrir la Console du dock).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

SPACES = ["piloter", "concevoir", "executer", "observer", "memoire", "source"]


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _open_palette(page: Page, query: str) -> None:
    page.locator("body").press("ControlOrMeta+k")
    page.wait_for_selector("#palette:not([hidden])")
    page.locator("#palette-input").fill(query)
    page.wait_for_function(
        "(q) => document.querySelectorAll('#palette-list li').length > 0", arg=query
    )


@pytest.mark.parametrize("index,space", list(enumerate(SPACES)))
def test_le_raccourci_meta_chiffre_change_d_espace(workspace: Page, index: int, space: str) -> None:
    """``⌘1``…``⌘6`` (``bindShortcuts``, ``shell.js``) — jamais pressé par un
    test jusqu'ici, seul ``GrimoireWorkspace.goto`` l'était directement."""
    workspace.locator("body").press(f"ControlOrMeta+{index + 1}")
    workspace.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    assert workspace.locator(f'[data-space="{space}"][aria-selected="true"]').count() == 1


def test_le_raccourci_accent_grave_ouvre_la_console_du_dock(workspace: Page) -> None:
    """`` ` `` (``bindShortcuts``) appelle toujours `selectDockTab('console')`
    puis `setDock('pinned')`, quel que soit l'état de départ du dock (pinned
    sur un autre onglet, ou replié) — pas besoin de le forcer avant."""
    workspace.locator("body").press("`")
    workspace.wait_for_function(
        "() => document.querySelector('[data-dock-tab=\"console\"]')?.getAttribute('aria-selected') === 'true'"
    )
    assert workspace.locator("#dock").get_attribute("data-state") == "pinned"


def test_choisir_une_tache_dans_la_palette_execute_task_show(
    workspace: Page, project_with_task: tuple
) -> None:
    """`buildPalette()` (`shell.js::main`) ne tourne qu'une fois, au chargement
    de la page — contrairement aux espaces, qui relisent l'API à chaque
    montage. `project_with_task` écrit la tâche directement sur disque, sans
    passer par le navigateur déjà chargé (fixture de session) : un rechargement
    est nécessaire pour que la palette la voie."""
    _, task_id = project_with_task
    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    _open_palette(workspace, "Vérifier la vue de travail")
    hints = workspace.locator("#palette-list .lbl").all_inner_texts()
    assert any("Tâche" in hint for hint in hints)

    workspace.locator("body").press("Enter")

    workspace.wait_for_function(
        "() => document.getElementById('dock-body')?.textContent.includes('grimoire task show')"
    )
    assert workspace.locator("#dock").get_attribute("data-state") == "pinned"
    body_text = workspace.locator("#dock-body").inner_text()
    assert f"$ grimoire task show {task_id}" in body_text


def test_choisir_un_workflow_dans_la_palette_va_sur_concevoir(
    workspace: Page, project_with_blueprint: tuple
) -> None:
    """Même mise en garde que ci-dessus : `real_project` n'a aucun blueprint
    par défaut, `project_with_blueprint` en écrit un sur disque après le
    premier chargement de la page — rechargement nécessaire."""
    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    _goto(workspace, "piloter")  # partir d'un espace différent, pour prouver que le choix navigue vraiment
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")
    workspace.wait_for_function(
        "() => Array.from(document.querySelectorAll('#palette-list li')).some((li) => li.textContent.includes('Workflow'))"
    )
    row = workspace.locator("#palette-list li", has=workspace.locator(".lbl", has_text="Workflow")).first
    row.wait_for(timeout=10_000)
    row.click()

    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")


def test_choisir_un_projet_dans_la_palette_recharge_sur_ce_projet(
    browser: Browser, served_cockpit: tuple[str, str]
) -> None:
    served, slug = served_cockpit
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)

        page.locator("#project-chip").click()
        page.wait_for_selector("#palette:not([hidden])")
        row = page.locator("#palette-list li[role='option']").first
        row.wait_for(timeout=10_000)
        row.click()

        # `run()` de cette section pose `location.search`, un vrai rechargement
        # (seul point de la palette qui n'est pas une navigation SPA) : on
        # attend que la coque se réamorce sur ce paramètre.
        page.wait_for_function("() => document.body.dataset.ready === '1'", timeout=15_000)
        assert f"project={slug}" in page.url
    finally:
        context.close()
