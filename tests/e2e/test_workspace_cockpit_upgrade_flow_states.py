"""États de `POST /api/projects/update` que Piloter doit distinguer.

`tests/e2e/test_workspace_cockpit_update_route.py` et `tests/e2e/
test_workspace_cockpit_upgrade_flow.py` prouvent déjà le chemin heureux
(aperçu, confirmer → proposition visible). Ce module cible deux états
précis, réels dans le code de `src/grimoire/tools/project_update.py` et
`src/grimoire/cli/cmd_cockpit.py`, restés sans test navigateur :

- un aperçu qui échoue (le bouton se réactive, l'erreur est lisible) ;
- une confirmation dont le run se solde par ``state: "upgraded-but-failed"``
  — un état nommé explicitement par le flow, distinct d'un échec générique,
  et pour lequel le backend joint déjà `report`/`proposals` (voir le
  docstring de ``_run_upgrade_flow``).

Les deux scénarios interceptent la réponse HTTP (``page.route``) plutôt que
de casser réellement le flow `grimoire upgrade-flow run` : plus rapide,
déterministe, et suffisant puisque ce qui est sous test ici est le rendu de
Piloter face à une forme de réponse donnée, pas le moteur du flow lui-même
(déjà couvert par ``tests/unit/test_project_update.py`` et
``tests/unit/test_project_upgrade.py``).
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, Route

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def test_apercu_en_echec_affiche_l_erreur_et_reactive_le_bouton(workspace: Page) -> None:
    workspace.route(
        "**/api/projects/update*",
        lambda route: _fulfill(
            route,
            {
                "ok": False, "dryRun": True, "path": "/dev/null", "code": 1,
                "state": "failed", "error": "grimoire upgrade-flow a échoué",
                "output": "", "nodes": [], "done": [], "stoppedAt": "orphans",
            },
        ),
    )
    _goto(workspace, "piloter")
    workspace.wait_for_selector("#zoom-seg button")
    workspace.locator("#zoom-seg button", has_text="Projet").click()

    update_button = workspace.locator("button", has_text="Mettre à jour — aperçu")
    update_button.wait_for(state="visible", timeout=15_000)
    update_button.click()

    preview = workspace.locator(".pl-preview")
    workspace.wait_for_function(
        "() => document.querySelector('.pl-preview')?.textContent.includes('grimoire upgrade-flow a échoué')"
    )
    assert "grimoire upgrade-flow a échoué" in preview.inner_text()
    assert update_button.is_enabled(), "un aperçu en échec doit réactiver le bouton, pas le laisser bloqué"
    assert update_button.inner_text() == "Mettre à jour — aperçu"


@pytest.mark.xfail(strict=True, reason="#538 : Piloter ne rafraîchit jamais les propositions sur upgraded-but-failed")
def test_confirmer_upgraded_but_failed_devrait_rafraichir_les_propositions(workspace: Page) -> None:
    aperçu_payload = {
        "ok": True, "dryRun": True, "path": "/dev/null", "code": 0,
        "state": "preview-only", "nodes": [], "done": ["backup", "preview"],
        "stoppedAt": "preview", "output": "", "error": None, "preview": "aperçu de test",
    }
    confirm_payload = {
        "ok": False, "dryRun": False, "path": "/dev/null", "code": 1,
        "state": "upgraded-but-failed",
        "error": "apply a refusé après écriture",
        "output": "", "done": ["backup", "preview", "orphans"], "stoppedAt": "apply",
        "nodes": [{"id": "apply", "status": "erreur"}],
        "report": "# Rapport\n\napply a refusé.\n",
        "proposals": [{"slug": "deja-en-attente", "artifactType": "agent", "specialty": "deja-en-attente"}],
    }

    def _route(route: Route) -> None:
        body = json.loads(route.request.post_data or "{}")
        _fulfill(route, confirm_payload if body.get("confirm") else aperçu_payload)

    workspace.route("**/api/projects/update*", _route)
    _goto(workspace, "piloter")
    workspace.wait_for_selector("#zoom-seg button")
    workspace.locator("#zoom-seg button", has_text="Projet").click()

    update_button = workspace.locator("button", has_text="Mettre à jour — aperçu")
    update_button.wait_for(state="visible", timeout=15_000)
    update_button.click()

    confirm_button = workspace.locator("button", has_text="Confirmer la mise à jour")
    confirm_button.wait_for(state="visible", timeout=15_000)
    confirm_button.click()

    workspace.wait_for_function(
        "() => document.querySelector('.pl-preview')?.textContent.includes('Échec de la mise à jour')"
    )
    # Comportement attendu (issue #538) : le nombre de propositions en
    # attente doit être annoncé, exactement comme sur un succès — le champ
    # existe dans la réponse (`confirm_payload.proposals`), seul l'affichage
    # y est aveugle aujourd'hui.
    preview_text = workspace.locator(".pl-preview").inner_text()
    assert "proposition(s) en attente" in preview_text
