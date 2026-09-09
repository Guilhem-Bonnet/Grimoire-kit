"""Parcours utilisateur : sélectionner un projet côté cockpit, puis agir dessus.

Régression du 2026-09-09, corrigée par la PR #358 : dès qu'un projet
était sélectionné, l'interface ajoutait `?project=<slug>` à **toute** requête
— y compris les écritures. `do_POST` comparait ce chemin, query string
comprise, à des routes exactes : plus aucune ne correspondait, et 100 % des
écritures du cockpit répondaient 404. La reproduction citée par la PR #358
elle-même est très précisément celle-ci : « POST /api/projects/update?project=
grimoire-forge répondait 404 Not Found ».

`tests/test_cmd_cockpit.py::test_post_routes_survivent_a_une_query_string`
garde déjà ce défaut — au niveau HTTP, avec un client `urllib` fabriqué à la
main sur quatre routes choisies pour ne pas dépendre d'un projet scaffoldé.
Cette version-ci ne le refait pas : elle prouve la même propriété par le seul
chemin qui compte pour un utilisateur — un vrai navigateur, un vrai clic sur
le bouton « Mettre à jour » de l'espace Piloter (spec §4), qui appelle la
route citée par la PR (`/api/projects/update`) que la garde existante ne
couvre pas. C'est l'étage « parcours utilisateur » que l'issue #353 demande de
rebrancher sur les parcours qui ont cassé.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def test_mettre_a_jour_depuis_piloter_ne_repond_pas_404_sur_un_projet_selectionne(
    cockpit_workspace: Page,
) -> None:
    workspace = cockpit_workspace
    _goto(workspace, "piloter")

    # Le niveau Flotte est le défaut côté cockpit (`level = cockpit ? 'flotte' : 'projet'`
    # dans `piloter.js`) — bien que l'URL porte déjà `?project=<slug>` depuis la
    # fixture. Passer au niveau Projet est ce qu'un utilisateur ferait pour
    # atteindre le bouton, et c'est ce qui exerce vraiment la route posée en
    # query string.
    workspace.wait_for_selector("#zoom-seg button")
    workspace.locator("#zoom-seg button", has_text="Projet").click()

    update_button = workspace.locator("button", has_text="Mettre à jour — aperçu")
    update_button.wait_for(state="visible", timeout=15_000)
    update_button.click()

    preview = workspace.locator(".pl-preview")
    preview.wait_for(state="visible", timeout=15_000)
    preview_text = preview.inner_text()

    # Avant le correctif, `ctx.api.updateProject` retombait dans le `catch` de
    # `piloter.js` avec `ApiError('HTTP 404', ...)`, rendu ici comme
    # `"refusé : HTTP 404"`. Le verdict retenu est « pas de refus HTTP », dans
    # le même esprit que la garde existante : ces routes peuvent légitimement
    # refuser une charge incomplète, ce n'est jamais leur injoignabilité qui
    # est en jeu ici.
    assert "refusé" not in preview_text, f"le clic « Mettre à jour » a échoué : {preview_text!r}"
    assert "404" not in preview_text, f"la route a répondu 404 malgré la query string : {preview_text!r}"
