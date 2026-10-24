"""P44 — l'écran Mise à jour plein canevas, en lecture.

Un vrai navigateur, un projet jetable (``served_cockpit_upgrade``). Ces tests
ne confirment JAMAIS une mise à jour : ils lancent l'aperçu (``dry_run``, qui
écrit une sauvegarde dans le projet jetable), lisent, et vérifient qu'aucune
requête de confirmation n'est partie.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, les captures de relecture y sont
écrites (clair et sombre) ; sans la variable, ``_capture`` ne fait rien.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

BOX_CHARS = "│─┌└├┐┘┤┬┴┼┃━┏┓┗┛┡┩╇"


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"{name}.png"), full_page=False)


def _theme(page: Page, pref: str) -> None:
    page.evaluate("(p) => document.querySelector(`#st-theme [data-theme-pref='${p}']`).click()", pref)


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


def _open_preview(page: Page) -> list[dict]:
    """Ouvre l'aperçu et rend la liste des corps POST vers la route de mise à jour."""
    posts: list[dict] = []

    def _on_request(request) -> None:
        if request.method == "POST" and "/api/projects/update" in request.url:
            posts.append(json.loads(request.post_data or "{}"))

    page.on("request", _on_request)
    _goto(page, "piloter")
    button = page.locator("button", has_text="Mettre à jour — aperçu")
    button.wait_for(state="visible", timeout=15_000)
    button.click()
    page.locator(".pl-update .pl-preview .tbl").first.wait_for(state="visible", timeout=90_000)
    return posts


def test_l_apercu_s_ouvre_en_plein_canevas_et_les_etapes_non_jouees_le_disent(
    cockpit_upgrade_workspace: Page,
) -> None:
    page = cockpit_upgrade_workspace
    _open_preview(page)

    screen = page.locator("#canvas .pl-update")
    assert screen.is_visible(), "l'aperçu doit occuper le canevas"
    assert page.locator("#inspector-body .pl-preview, #inspector-body .pl-update").count() == 0, (
        "l'aperçu ne doit plus vivre dans l'inspecteur"
    )
    assert page.locator("#canvas .pl-sheet").is_hidden(), "la fiche cède la place à l'écran de mise à jour"
    assert screen.locator("h2").inner_text().startswith("Mise à jour — ")

    text = screen.inner_text()
    assert "non exécutée en aperçu" in text
    assert "jamais atteint" not in text

    page.get_by_role("button", name="Retour à la fiche").click()
    assert page.locator("#canvas .pl-update").is_hidden()
    assert page.locator("#canvas .pl-sheet").is_visible()


def test_la_sortie_de_up_dry_run_est_un_vrai_tableau_francais_au_nom_du_projet(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    page = cockpit_upgrade_workspace
    _open_preview(page)
    screen = page.locator("#canvas .pl-update")

    tables = screen.locator("table.tbl")
    assert tables.count() >= 1
    headers = [h.strip() for h in tables.first.locator("thead th").all_inner_texts()]
    assert headers == ["Étape", "Statut", "Détail"]
    assert tables.first.locator("tbody tr").count() >= 3
    body = tables.first.inner_text()
    assert "Rafraîchissement du kit" in body
    assert "prévue" in body
    assert "would refresh" not in body, "le détail doit être traduit, pas la phrase anglaise de la commande"

    # Aucun caractère de boîte Rich, nulle part dans le DOM de l'écran.
    dom_text = page.evaluate("() => document.querySelector('#canvas .pl-update').textContent")
    leaked = [c for c in BOX_CHARS if c in dom_text]
    assert not leaked, f"caractères de boîte dans le DOM : {leaked}"

    assert "Project: x" not in dom_text
    _served, slug, _root = served_cockpit_upgrade
    assert f"Projet : {slug}" in dom_text, "le nom réel du projet doit être affiché"
    assert screen.locator("h2").inner_text() == f"Mise à jour — {slug}"


def test_changements_par_categorie_diff_au_clic_et_confirmation_conservee(
    cockpit_upgrade_workspace: Page,
) -> None:
    page = cockpit_upgrade_workspace
    posts = _open_preview(page)
    screen = page.locator("#canvas .pl-update")
    _theme(page, "light")
    screen.locator("h2").scroll_into_view_if_needed()
    _capture(page, "p44-apercu-clair")
    _theme(page, "dark")
    _capture(page, "p44-apercu-sombre")

    changes = screen.locator(".pl-changes")
    changes.locator(".pl-cat-head").first.wait_for(state="visible", timeout=30_000)
    category = changes.locator(".pl-cat-head").first
    files = changes.locator(".pl-cat-files").first
    assert category.get_attribute("aria-expanded") == "false"
    assert files.is_hidden(), "la liste d'une catégorie n'apparaît qu'au clic"

    category.click()
    assert category.get_attribute("aria-expanded") == "true"
    file_head = files.locator(".pl-file-head").first
    file_head.wait_for(state="visible")
    path = file_head.get_attribute("data-path")
    assert path and not path.startswith("/"), "chemin relatif au projet"

    assert changes.locator(".pl-diff").count() == 0, "le diff n'est chargé qu'au clic sur le fichier"
    file_head.click()
    diff = changes.locator(".pl-diff").first
    diff.wait_for(state="visible", timeout=15_000)
    assert diff.locator(".add, .del").count() >= 1, "un vrai diff porte des lignes ajoutées ou retirées"
    assert diff.locator(".hunk").count() >= 1
    category.scroll_into_view_if_needed()
    _theme(page, "light")
    _capture(page, "p44-categorie-et-diff-clair")

    # Le bouton de confirmation existant est conservé — et jamais cliqué ici.
    confirm = screen.get_by_role("button", name="Confirmer la mise à jour")
    assert confirm.count() == 1
    assert confirm.is_visible() and confirm.is_enabled()
    assert posts, "l'aperçu a bien appelé la route"
    assert all(body.get("confirm") is not True for body in posts), (
        f"aucune confirmation ne doit partir pendant ce test : {posts}"
    )

    _theme(page, "dark")
    _capture(page, "p44-categorie-et-diff-sombre")


def test_les_routes_de_lecture_refusent_un_chemin_non_concerne(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    served, slug, _root = served_cockpit_upgrade
    ok = cockpit_upgrade_workspace.request.get(f"{served}/api/workspace/upgrade/changes", params={"project": slug})
    assert ok.ok, ok.status
    assert "categories" in ok.json()
    refused = cockpit_upgrade_workspace.request.get(
        f"{served}/api/workspace/upgrade/diff", params={"project": slug, "path": "project-context.yaml"}
    )
    assert refused.status == 400
    # Et la lecture n'accepte pas d'écriture : POST sur ces chemins n'existe pas.
    post = cockpit_upgrade_workspace.request.post(
        f"{served}/api/workspace/upgrade/changes?project={slug}", data="{}"
    )
    assert post.status in {403, 404, 405}, post.status
