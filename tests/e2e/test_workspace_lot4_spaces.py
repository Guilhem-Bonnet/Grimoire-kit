"""Lot 4 — Piloter, Exécuter, Observer, Mémoire, prouvés dans un navigateur.

Chaque test décrit un défaut qu'il empêche, dans l'esprit de
``test_workspace_shell.py``. Celui-ci ne reteste pas la coque (panneaux,
palette, thème) : il prouve le contenu propre aux quatre espaces du lot, sur
un projet réel avec un vrai ledger — jamais sur une donnée inventée.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


# ── Piloter — corrections de données §4.1 ───────────────────────────────────


def test_piloter_atelier_rend_ci_status_inconnue_en_gris(workspace: Page) -> None:
    """`ci_status` vaut toujours `"unknown"` aujourd'hui : la fiche doit le dire
    en mot, avec un point gris — jamais une couleur inventée ni `undefined`."""
    _goto(workspace, "piloter")
    workspace.wait_for_selector(".pl-sheet")
    canvas_text = workspace.locator("#canvas").inner_text()

    assert "inconnue" in canvas_text.lower()
    assert "undefined" not in canvas_text
    assert "NaN" not in canvas_text


def test_piloter_cockpit_flotte_montre_un_tableau_avec_inconnue(cockpit_workspace: Page) -> None:
    """Niveau Flotte : un tableau de projets, colonne CI rendue « inconnue »."""
    workspace = cockpit_workspace
    _goto(workspace, "piloter")

    workspace.locator(".pl-table-wrap, .pl-cards").first.wait_for()
    body_text = workspace.locator("#canvas").inner_text().lower()
    assert "inconnue" in body_text
    assert "à traiter" in body_text


def test_piloter_zoom_flotte_projet_est_disponible_sur_le_cockpit(cockpit_workspace: Page) -> None:
    _goto(cockpit_workspace, "piloter")
    cockpit_workspace.wait_for_selector("#zoom-seg button")
    zoom = cockpit_workspace.locator("#zoom-seg button")
    assert zoom.count() == 2
    assert {zoom.nth(i).inner_text() for i in range(2)} == {"Flotte", "Projet"}


def test_piloter_kit_reel_aligne_dit_aligne_pas_a_jour(workspace: Page) -> None:
    """Le projet réel de la fixture est scaffoldé par le kit qui tourne, mais la
    plupart de ses fichiers n'ont pas changé depuis une révision antérieure du
    catalogue : `aligned` (3.3x.0, la révision de ce contenu) et `installed`
    (3.39.0, le CLI qui répond) divergent légitimement — exactement le cas que
    #288 signalait, obtenu ici sans rien mocker. Le badge ne doit jamais dire
    « à jour » à côté de deux nombres différents."""
    _goto(workspace, "piloter")
    workspace.wait_for_selector(".pl-sheet")
    kit_block_text = workspace.locator(".pl-insp-block").first.inner_text()

    assert "aligné sur" in kit_block_text and "installé" in kit_block_text
    assert "à jour" not in kit_block_text, (
        "« à jour » à côté de deux versions différentes est la contradiction de #288"
    )
    assert "aligné" in kit_block_text.lower()


def test_piloter_kit_aligne_distinct_de_installe_ne_dit_pas_a_jour(browser: Browser, served: str) -> None:
    """Pin exact du couple de versions de #288 (`aligné sur 3.36.0, installé
    3.38.0`), indépendant des versions que la fixture réelle produira demain.
    `kit.upToDate` peut légitimement valoir `true` ici (aucun fichier livré
    n'a de révision plus récente au catalogue, cf.
    `project_health.kit_alignment`) : seule la réponse `/api/health` est
    fabriquée, tout le reste (ledger, activité, CI) vient du vrai serveur.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()

    def _divergent_kit(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["kit"] = {**payload["kit"], "scaffolded": True, "upToDate": True, "behind": 0, "aligned": "3.36.0", "installed": "3.38.0"}
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/health*", _divergent_kit)
    try:
        page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _goto(page, "piloter")
        page.wait_for_selector(".pl-sheet")
        kit_block = page.locator(".pl-insp-block").first
        kit_block_text = kit_block.inner_text()

        assert "3.36.0" in kit_block_text
        assert "3.38.0" in kit_block_text
        assert "à jour" not in kit_block_text, (
            "« à jour » à côté de deux versions différentes est la contradiction de #288"
        )
        assert "aligné" in kit_block_text.lower()
        dot = kit_block.locator(".dot").first
        assert "warn" not in (dot.get_attribute("class") or ""), "upToDate=true doit rester un point vert"
    finally:
        context.close()


def test_piloter_kit_exactement_synchronise_dit_a_jour(browser: Browser, served: str) -> None:
    """Miroir du test précédent : quand `aligned == installed`, le mot honnête
    redevient « à jour » (#288 ne demande pas de bannir le mot, seulement de
    ne plus l'employer à côté de deux versions différentes)."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()

    def _synced_kit(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["kit"] = {**payload["kit"], "scaffolded": True, "upToDate": True, "behind": 0, "aligned": "3.39.0", "installed": "3.39.0"}
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/health*", _synced_kit)
    try:
        page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _goto(page, "piloter")
        page.wait_for_selector(".pl-sheet")
        kit_block_text = page.locator(".pl-insp-block").first.inner_text()

        assert "à jour" in kit_block_text
    finally:
        context.close()


# ── Exécuter — les trois vues, un move réussi, un refus de gate ────────────


def test_executer_propose_trois_vues_et_le_board_par_defaut(
    workspace: Page, project_with_task: tuple[Path, str]
) -> None:
    _goto(workspace, "executer")
    workspace.wait_for_selector("#view-seg button")
    views = workspace.locator("#view-seg button")
    labels = {views.nth(i).inner_text() for i in range(views.count())}
    assert {"Board 4", "Board 8", "Liste", "Timeline"} <= labels

    workspace.wait_for_selector(".ex-col")
    assert workspace.locator(".ex-col").count() == 4, "Board 4 : quatre colonnes par défaut"

    views.filter(has_text="Liste").click()
    workspace.wait_for_selector(".ex-list-wrap")
    views.filter(has_text="Board 8").click()
    workspace.wait_for_selector(".ex-col")
    assert workspace.locator(".ex-col").count() == 8, "Board 8 : les huit colonnes du standard"


def test_executer_un_move_reussi_deplace_la_carte_puis_un_claim_est_refuse(
    workspace: Page, project_with_task: tuple[Path, str]
) -> None:
    """Le projet est en profil `governed` (hard_fail) : `proposed → ready` passe
    (critères d'acceptation et owner déclarés à la création de la tâche),
    `ready → in_progress` (claim) est refusé — aucun context bundle, aucun
    fournisseur activé au registre. Les deux faits viennent du disque, pas
    d'un scénario simulé : ``_grimoire/standard/evidence-gates.yaml`` du profil
    gouverné les déclare tels quels.
    """
    _, task_id = project_with_task
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card")

    inspector = workspace.locator("#inspector-body")
    workspace.locator(".ex-card").filter(has_text="Vérifier la vue de travail").first.click()
    inspector.get_by_text(task_id, exact=True).wait_for()

    # Première porte : proposed → ready, déclarée et satisfaite. Le succès
    # déclenche un réaffichage complet de l'inspecteur (nouvelle porte, preuves
    # à jour) : on attend cet état stable — « → En cours », la porte suivante —
    # plutôt que le message de confirmation transitoire, que le réaffichage
    # peut effacer avant que le harnais ne l'observe.
    inspector.locator("button", has_text="Réaliser").first.click()
    inspector.get_by_text("→ En cours").first.wait_for()

    # Deuxième porte, maintenant proposée : ready → in_progress (claim), refusée.
    inspector.locator("button", has_text="Réaliser").first.click()
    workspace.wait_for_selector(".ex-refusal")
    refusal_text = workspace.locator(".ex-refusal").inner_text().lower()
    assert "context bundle" in refusal_text or "fournisseur" in refusal_text
    assert "refusé" in refusal_text


# ── Observer — état vide honnête, jamais un mur de zéros ni une erreur ─────


def test_observer_sans_trace_rend_un_seul_bloc_vide_sans_erreur_console(workspace: Page) -> None:
    errors: list[str] = []
    workspace.on("pageerror", lambda exc: errors.append(str(exc)))
    workspace.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    _goto(workspace, "observer")
    workspace.wait_for_selector(".empty")

    body_text = workspace.locator("#canvas").inner_text()
    assert "Aucune trace" in body_text
    # Correction due (revue §4.4) : jamais un mur de zéros à la place de l'état vide.
    assert "$0.0000" not in body_text
    assert "0.0/min" not in body_text
    assert not errors, f"erreurs console à l'ouverture d'Observer : {errors}"


# ── Mémoire — le store d'abord, l'architecture derrière un onglet ──────────


def test_memoire_montre_le_store_avant_l_architecture(workspace: Page) -> None:
    _goto(workspace, "memoire")
    workspace.wait_for_selector(".me-wrap, .empty")
    views = workspace.locator("#view-seg button")
    if views.count():
        labels = [views.nth(i).inner_text() for i in range(views.count())]
        assert labels[0] == "Store", "le store est la vue par défaut, pas l'explication"
        assert "Architecture" in labels


# ── Cockpit : écritures désactivées (spec §5, critère 8) ───────────────────


def test_cockpit_desactive_les_ecritures_d_executer(cockpit_workspace: Page) -> None:
    workspace = cockpit_workspace
    assert workspace.evaluate("() => window.GrimoireWorkspace.host.readOnly") is True

    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-col, .empty")
    cards = workspace.locator(".ex-card")
    if cards.count() == 0:
        pytest.skip("aucune tâche visible côté cockpit pour ce projet")
    cards.first.click()
    button = workspace.locator("#inspector-body button", has_text="Écriture désactivée").first
    button.wait_for(state="visible")
    assert button.is_disabled()
