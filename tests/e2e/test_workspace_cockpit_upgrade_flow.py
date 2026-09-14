"""Le bouton « Mettre à jour » depuis le cockpit lance le flow, pas `up` seul (#490).

Refs #490. La route ``POST /api/projects/update`` appelle désormais
``grimoire upgrade-flow run`` (``src/grimoire/tools/project_update.py``) :
aperçu par défaut (rapport ``preview.md`` renvoyé et affiché dans la fiche
projet de Piloter), ``confirm: true`` pour le flow complet (nœuds de
jugement mécaniques, checkpoint final laissé à l'humain). Ce module prouve
le seul chemin qui compte pour un utilisateur — un vrai navigateur, les deux
clics du bouton — et que ce que le flow complet propose (issue #490 : une
fiche mémoire non raccordée) apparaît bien dans la section Propositions de
Piloter, sans recharger la page à la main.

``tests/e2e/test_workspace_cockpit_update_route.py`` couvre déjà l'aperçu
seul (régression #358, query string). Ce module-ci ajoute la confirmation et
la proposition qui doit en sortir — la partie de l'issue #490 que l'aperçu
seul ne peut pas prouver.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _proposals_section(page: Page):
    return page.locator(".pl-section", has=page.locator("h3", has_text="Propositions"))


def test_confirmer_depuis_piloter_fait_apparaitre_une_proposition(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    workspace = cockpit_upgrade_workspace
    _goto(workspace, "piloter")

    # Le niveau Flotte est le défaut côté cockpit — passer au niveau Projet
    # est ce qu'un utilisateur ferait pour atteindre les actions du projet.
    workspace.wait_for_selector("#zoom-seg button")
    workspace.locator("#zoom-seg button", has_text="Projet").click()

    update_button = workspace.locator("button", has_text="Mettre à jour — aperçu")
    update_button.wait_for(state="visible", timeout=15_000)
    update_button.click()

    preview = workspace.locator(".pl-preview")
    preview.wait_for(state="visible", timeout=30_000)
    preview_text = preview.inner_text()
    assert "refusé" not in preview_text, f"l'aperçu a échoué : {preview_text!r}"
    assert "404" not in preview_text, f"la route a répondu 404 : {preview_text!r}"

    confirm_button = preview.get_by_role("button", name="Confirmer la mise à jour")
    confirm_button.wait_for(state="visible", timeout=15_000)
    confirm_button.click()

    # Assertion sur l'état stable — jamais sur le message transitoire que
    # `options.refresh()` remplace dès que la fiche se redessine avec les
    # données fraîches : c'est cette fiche redessinée, pas le message, qui
    # doit finir par montrer la proposition (issue #490 : la fiche mémoire
    # non raccordée semée par la fixture).
    workspace.wait_for_function(
        "() => document.body.innerText.includes('monitoring')",
        timeout=90_000,
    )
    section = _proposals_section(workspace)
    section.wait_for(state="visible", timeout=15_000)
    assert "monitoring" in section.inner_text(), (
        "la fiche mémoire non raccordée doit apparaître comme proposition dans Piloter"
    )
