"""Lot 4 — Piloter, Exécuter, Observer, Mémoire, prouvés dans un navigateur.

Chaque test décrit un défaut qu'il empêche, dans l'esprit de
``test_workspace_shell.py``. Celui-ci ne reteste pas la coque (panneaux,
palette, thème) : il prouve le contenu propre aux quatre espaces du lot, sur
un projet réel avec un vrai ledger — jamais sur une donnée inventée.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import open_scope_all


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


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


def test_piloter_cockpit_flotte_montre_un_tableau_de_projets(cockpit_workspace: Page) -> None:
    """Portée « Tous les projets » : la table de la maquette Flotte (P65), sans
    colonne CI (rien ne la mesure : elle ne disait qu'« inconnue ») ni valeur inventée."""
    workspace = cockpit_workspace
    open_scope_all(workspace)
    _goto(workspace, "piloter")

    workspace.locator(".tbl-wrap, .pl-cards").first.wait_for()
    body_text = workspace.locator("#canvas").inner_text().lower()
    assert "demandent une action" in body_text
    assert "undefined" not in body_text and "nan" not in body_text.split()


def test_piloter_n_a_plus_de_zoom_flotte_projet_la_portee_decide(cockpit_workspace: Page) -> None:
    """P31 : la portée de la coque remplace le zoom Flotte/Projet de Piloter."""
    _goto(cockpit_workspace, "piloter")
    cockpit_workspace.wait_for_selector(".pl-sheet")
    assert cockpit_workspace.locator("#zoom-seg button").count() == 0
    open_scope_all(cockpit_workspace)
    cockpit_workspace.locator(".tbl-wrap, .pl-cards").first.wait_for()
    assert cockpit_workspace.locator("#zoom-seg button").count() == 0


def test_piloter_kit_reel_a_jour_dit_a_jour_avec_le_kit_du_dernier_up(workspace: Page) -> None:
    """Régression #519 : le projet réel de la fixture vient d'être scaffoldé
    par le kit qui tourne — `grimoire init` écrit désormais le marqueur
    `_grimoire/kit/.up-version`, donc le serveur connaît `upVersion` (égal à
    `installed`, le même process). Avant #519, la plupart des fichiers
    n'ayant pas changé depuis une révision antérieure du catalogue,
    `aligned` (première introduction d'un contenu encore présent) restait
    souvent une version plus ancienne, et le badge affichait « aligné sur Y,
    installé X » — lu comme un retard alors que le projet est réellement à
    jour, obtenu ici sans rien mocker. Le badge doit désormais dire « à jour
    (kit X) » avec une seule version faisant autorité."""
    _goto(workspace, "piloter")
    workspace.wait_for_selector(".pl-sheet")
    kit_block_text = workspace.locator(".pl-insp-block").first.inner_text()

    match = re.search(r"à jour \(kit (\d+(?:\.\d+)*)\)", kit_block_text)
    assert match, f"format de bloc kit inattendu : {kit_block_text!r}"
    assert "aligné sur" not in kit_block_text, (
        "« aligné sur X, installé Y » est le libellé trompeur de #519 — "
        "« à jour (kit X) » doit le remplacer dès que le serveur connaît upVersion"
    )


def test_piloter_kit_aligne_distinct_de_installe_ne_dit_pas_a_jour(browser: Browser, served: str) -> None:
    """Pin exact du couple de versions de #288 (`aligné sur 3.36.0, installé
    3.38.0`), indépendant des versions que la fixture réelle produira demain.
    `kit.upToDate` peut légitimement valoir `true` ici (aucun fichier livré
    n'a de révision plus récente au catalogue, cf.
    `project_health.kit_alignment`) : seule la réponse `/api/health` est
    fabriquée, tout le reste (ledger, activité, CI) vient du vrai serveur.

    `upVersion` (issue #519) est explicitement forcé à `None` : le vrai
    serveur le connaît désormais (le projet de la fixture vient d'être
    scaffoldé), ce qui déclencherait la nouvelle branche « à jour (kit X) »
    plutôt que celle-ci. Ce test cible délibérément le repli — un projet qui
    n'a encore jamais tourné avec ce marqueur — qui reste le contrat #288
    inchangé : ne jamais dire « à jour » à côté de deux nombres différents.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()

    def _divergent_kit(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["health"]["kit"] = {
            **payload["health"]["kit"],
            "scaffolded": True, "upToDate": True, "behind": 0,
            "aligned": "3.36.0", "installed": "3.38.0", "upVersion": None,
        }
        route.fulfill(response=response, body=json.dumps(payload))

    # Depuis #548 : la fiche Piloter lit `health` via l'agrégat
    # `/api/workspace/sheet`, plus jamais `/api/health` directement.
    page.route("**/api/workspace/sheet*", _divergent_kit)
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
        # Seconde marche du thème (#525 suite) : le point + mot du badge Kit
        # est devenu une pastille pleine (`.chip.pill`, piloter.js::pill()) —
        # même assertion sur la même information, un sélecteur différent.
        badge = kit_block.locator(".chip.pill").first
        assert "warn" not in (badge.get_attribute("class") or ""), "upToDate=true doit rester une pastille verte"
    finally:
        context.close()


def test_piloter_kit_exactement_synchronise_dit_a_jour(browser: Browser, served: str) -> None:
    """Miroir du test précédent : quand `aligned == installed`, le mot honnête
    redevient « à jour » (#288 ne demande pas de bannir le mot, seulement de
    ne plus l'employer à côté de deux versions différentes).

    `upVersion` forcé à `None` pour la même raison que le test précédent —
    cible délibérément le repli sans marqueur connu (issue #519)."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()

    def _synced_kit(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["health"]["kit"] = {
            **payload["health"]["kit"],
            "scaffolded": True, "upToDate": True, "behind": 0,
            "aligned": "3.39.0", "installed": "3.39.0", "upVersion": None,
        }
        route.fulfill(response=response, body=json.dumps(payload))

    # Depuis #548 : la fiche Piloter lit `health` via l'agrégat
    # `/api/workspace/sheet`, plus jamais `/api/health` directement.
    page.route("**/api/workspace/sheet*", _synced_kit)
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
    assert {"Tableau", "Huit colonnes", "Liste", "Chronologie"} <= labels

    workspace.wait_for_selector(".ex-col")
    # Quatre colonnes de travail ; les tâches annulées ont la leur, repliée (P07).
    assert workspace.locator(".ex-col:not(.ex-col-folded)").count() == 4, "Board 4 : quatre colonnes par défaut"
    assert workspace.locator(".ex-col.ex-col-folded").count() == 1, "Board 4 : les annulées à part, repliées"

    views.filter(has_text="Liste").click()
    workspace.wait_for_selector(".ex-list-wrap")
    views.filter(has_text="Huit colonnes").click()
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


def test_executer_l_inspecteur_ne_montre_pas_de_rappel_vide(
    workspace: Page, project_with_task: tuple[Path, str]
) -> None:
    """#141, avec parcimonie : une tâche qui n'a rien traversé n'a rien à
    rappeler, et l'inspecteur ne doit pas afficher un bloc « Rappel » vide.

    Si le garde ``has_content`` de ``renderInspector`` disparaissait, ce test
    échouerait — le bloc apparaîtrait pour la tâche par défaut du fixture, qui
    n'est jamais réclamée avec succès dans ce lot (le claim y est refusé faute
    de context bundle et de fournisseur activé)."""
    _, task_id = project_with_task
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card")

    inspector = workspace.locator("#inspector-body")
    workspace.locator(".ex-card").filter(has_text="Vérifier la vue de travail").first.click()
    inspector.get_by_text(task_id, exact=True).wait_for()

    assert inspector.locator(".ex-recall").count() == 0
    assert "Rappel" not in inspector.locator(".ex-insp-block h4").all_inner_texts()


def test_executer_le_board_change_quand_on_change_de_projet(
    browser: Browser, served_cockpit_multi: tuple[str, str, str]
) -> None:
    """Issue #140, critère d'acceptation à la lettre : lire un autre projet
    doit montrer un autre board — pas celui du dernier projet sélectionné, pas
    un mélange des deux."""
    served, slug_a, slug_b = served_cockpit_multi
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _goto(page, "executer")
        page.wait_for_selector(".ex-card, .empty")
        board_a_text = page.locator("#canvas").inner_text()
        assert "projet A" in board_a_text
        assert "projet B" not in board_a_text

        page.goto(f"{served}/workspace/index.html?project={slug_b}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _goto(page, "executer")
        page.wait_for_selector(".ex-card, .empty")
        board_b_text = page.locator("#canvas").inner_text()
        assert "projet B" in board_b_text
        assert "projet A" not in board_b_text
    finally:
        context.close()


def test_choisir_un_projet_dans_la_flotte_change_la_portee_et_ouvre_sa_fiche(
    browser: Browser, served_cockpit_multi: tuple[str, str, str]
) -> None:
    """Issue #506, puis P31 : choisir un projet dans la Flotte ouvre SA fiche.

    Avant, le clic montrait la fiche du projet B tandis que la puce et le fil
    d'Ariane restaient sur A, et il fallait un second bouton « Ouvrir ce
    projet ». Désormais un seul niveau de sélection : le clic change la portée
    de la coque (URL `?project=<B>`), et la Flotte montre la fiche de B."""
    served, _slug_a, slug_b = served_cockpit_multi
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_selector(".tbl tbody tr")
        page.locator(".tbl tbody tr", has_text=slug_b).first.click()

        page.wait_for_url(f"**project={slug_b}**")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.locator("button", has_text="Mettre à jour").first.wait_for(state="visible", timeout=15_000)
        assert page.evaluate("() => window.GrimoireWorkspace.scope.project") == slug_b
        assert page.locator("button", has_text="Ouvrir ce projet").count() == 0
        assert page.locator("#zoom-seg button").count() == 0
    finally:
        context.close()


def test_executer_review_sans_evidence_pack_est_refuse_et_nomme_l_artefact(
    review_gate_workspace: Page, served_review_gate: tuple[str, str]
) -> None:
    """Issue #140, critère d'acceptation à la lettre : « déplacer une carte
    vers review sans evidence pack est refusé dans l'UI avec le nom de
    l'artefact manquant affiché ». La tâche de :func:`served_review_gate` est
    déjà ``running`` (``in_progress`` côté board) ; personne n'a produit
    d'evidence pack, donc ``in_progress -> review`` doit rester fermée."""
    workspace = review_gate_workspace
    _, task_id = served_review_gate
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card")

    inspector = workspace.locator("#inspector-body")
    workspace.locator(".ex-card").first.click()
    inspector.get_by_text(task_id, exact=True).wait_for()

    gate_row = inspector.locator(".ex-gate-row").filter(has_text="Revue")
    gate_row.locator("button", has_text="Réaliser").click()

    workspace.wait_for_selector(".ex-refusal")
    refusal_text = workspace.locator(".ex-refusal").inner_text().lower()
    assert "refusé" in refusal_text
    assert "evidence" in refusal_text or "evidence_pack" in refusal_text
    # La carte n'a pas bougé : la porte « Revue » reste celle à franchir.
    assert inspector.locator(".ex-gate-row", has_text="Revue").count() == 1


def test_executer_timeline_montre_dispatch_et_refus_et_se_filtre_par_source(
    timeline_workspace: Page, served_timeline: tuple[str, str]
) -> None:
    """Issue #139 : ouvrir la timeline d'une tâche qui a un dispatch d'agent et
    une transition refusée montre les deux lignes, et un filtre par source ne
    garde que celle demandée. Les deux faits viennent du disque (préparés par
    :func:`served_timeline` en CLI), rien n'est simulé côté navigateur."""
    workspace = timeline_workspace
    _, task_id = served_timeline
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card")

    inspector = workspace.locator("#inspector-body")
    workspace.locator(".ex-card").first.click()
    inspector.get_by_text(task_id, exact=True).wait_for()

    # Drill-down depuis la carte (#139) : le bouton de l'inspecteur, pas
    # l'onglet Timeline du docbar.
    inspector.locator("button", has_text="Voir la timeline").click()
    workspace.wait_for_selector(".ex-tl-entry")

    entries = workspace.locator(".ex-tl-entry")
    all_text = entries.all_inner_texts()
    assert any("gate" in t for t in all_text), f"aucune ligne « gate » (transition refusée) : {all_text}"
    assert any("hooks" in t and "grimoire-master" in t for t in all_text), (
        f"aucune ligne de dispatch d'agent : {all_text}"
    )
    total = entries.count()

    # Filtre par source : ne garder que les lignes de la gate.
    source_select = workspace.locator(".ex-tl-filters select").first
    source_select.select_option("gate")
    workspace.wait_for_function(
        "() => document.querySelectorAll('.ex-tl-entry').length > 0 && "
        "[...document.querySelectorAll('.ex-tl-entry')].every(e => e.textContent.includes('gate'))"
    )
    filtered = workspace.locator(".ex-tl-entry")
    assert filtered.count() < total, "le filtre doit réduire la liste sur ce projet"
    assert all("gate" in t for t in filtered.all_inner_texts())

    # Filtre par gravité : « causes seulement » ne garde que des lignes en échec.
    source_select.select_option("tous")
    gravite_select = workspace.locator(".ex-tl-filters select").nth(1)
    gravite_select.select_option("causes")
    workspace.wait_for_selector(".ex-tl-entry.fail")
    causes_only = workspace.locator(".ex-tl-entry")
    classes = [causes_only.nth(i).get_attribute("class") or "" for i in range(causes_only.count())]
    assert classes, "au moins une cause (la transition refusée) doit rester"
    assert all("fail" in cls for cls in classes)


# ── Observer — état vide honnête, jamais un mur de zéros ni une erreur ─────


def test_observer_sans_trace_rend_un_seul_bloc_vide_sans_erreur_console(untouched_workspace: Page) -> None:
    """Le projet vide que rien n'écrit (`served_untouched`) : ni ledger, ni
    hook, ni run de flow — le seul cas où « Aucune trace » est vrai."""
    errors: list[str] = []
    untouched_workspace.on("pageerror", lambda exc: errors.append(str(exc)))
    untouched_workspace.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    _goto(untouched_workspace, "observer")
    untouched_workspace.wait_for_selector(".empty")

    body_text = untouched_workspace.locator("#canvas").inner_text()
    assert "Aucune trace" in body_text
    # Correction due (revue §4.4) : jamais un mur de zéros à la place de l'état vide.
    assert "$0.0000" not in body_text
    assert "0.0/min" not in body_text
    assert not errors, f"erreurs console à l'ouverture d'Observer : {errors}"


def test_observer_ne_dit_pas_aucune_trace_quand_le_ledger_porte_des_evenements(
    workspace: Page, project_with_task: tuple[Path, str]
) -> None:
    """P07 (revue UX M4) : Observer annonçait « Aucune trace » alors que le
    ledger du projet portait des milliers d'événements. `real_project` a un
    Mission Ledger (tâche `bootstrap` de `standard init`, plus celle de
    `project_with_task`) mais aucun span OTel."""
    root, _task_id = project_with_task
    assert (root / "_grimoire-runtime-output" / "ledger" / "events.jsonl").is_file()

    _goto(workspace, "observer")
    panel = workspace.locator("[data-role='activity-without-spans']")
    panel.wait_for(timeout=15_000)
    assert "ledger" in panel.inner_text()
    assert "Aucune trace" not in workspace.locator("#canvas").inner_text()


# ── Mémoire — la recherche d'abord (P24) ────────────────────────────────────


def test_memoire_s_ouvre_sur_la_recherche(workspace: Page) -> None:
    # P24 a retiré les onglets Store / Graphe / Couches / Architecture : l'écran
    # d'entrée de Savoir › Mémoire est la recherche, filtres à gauche.
    _goto(workspace, "memoire")
    workspace.wait_for_selector(".kn-searchbar input[type='search']")
    assert workspace.locator("#view-seg button:visible").count() == 0
    legends = [t.strip() for t in workspace.locator(".kn-filters legend").all_inner_texts()]
    assert legends[:2] == ["Type", "Source"]


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
