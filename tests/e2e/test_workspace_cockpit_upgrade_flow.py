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

    # Le déroulé nœud par nœud doit apparaître ET rester affiché — issue #506 :
    # le constat terrain (« l'Inspecteur revient silencieusement à l'état
    # initial ») venait de `options.refresh()`, appelé juste après avoir
    # montré le résultat, qui redessinait toute la fiche et l'effaçait. Le
    # correctif retire cet appel du chemin de confirmation ; ce test vérifie
    # maintenant l'état stable ET le message transitoire, plutôt que
    # d'esquiver ce dernier comme avant.
    workspace.wait_for_function(
        "() => document.body.innerText.includes('monitoring')",
        timeout=90_000,
    )
    section = _proposals_section(workspace)
    section.wait_for(state="visible", timeout=15_000)
    assert "monitoring" in section.inner_text(), (
        "la fiche mémoire non raccordée doit apparaître comme proposition dans Piloter"
    )

    preview_text = preview.inner_text()
    assert "s'est arrêté au checkpoint final" in preview_text, (
        "le message de confirmation doit rester affiché, pas disparaître au refresh des propositions"
    )
    for node_label in ("Sauvegarde", "Aperçu", "Application", "Destructif"):
        assert node_label in preview_text, f"le déroulé nœud par nœud doit nommer « {node_label} »"
    assert "checkpoint destructif en attente" in workspace.locator("body").inner_text(), (
        "le badge Kit doit passer à un état explicite après un run arrêté au checkpoint"
    )


def test_observer_montre_le_run_de_flow_meme_traceledger_vide(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    """Issue #506 : Observer disait « TraceLedger vide » même juste après un
    run réel de `project-upgrade` — vrai du TraceLedger (dispatch/agent-miss),
    trompeur pour qui vient de confirmer une mise à jour depuis Piloter. Ce
    test s'appuie sur le run déjà confirmé par le test précédent dans ce même
    fichier (fixture de session partagée) : sans lui, aucun run n'existerait
    encore et ce test ne prouverait rien.
    """
    workspace = cockpit_upgrade_workspace
    _goto(workspace, "observer")
    workspace.wait_for_selector(".ob-runs", timeout=15_000)
    runs_text = workspace.locator(".ob-runs").inner_text()
    assert "project-upgrade" in runs_text, (
        "un run de project-upgrade existe (test précédent) : Observer doit le nommer, "
        "pas seulement dire le TraceLedger vide"
    )
