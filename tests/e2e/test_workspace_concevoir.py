"""Harnais Playwright de l'espace Concevoir (lot 3).

Complète `tests/e2e/test_workspace_shell.py` (qui ne teste que l'ouverture des
six espaces, encore vides au moment où il a été écrit) avec ce que la spec §4
demande de l'espace Concevoir : les trois niveaux de zoom, les trois vues,
sélection → inspecteur, validation → dock, et l'inspecteur à quatre onglets du
niveau Nœud.

Le projet servi porte un vrai blueprint multi-nœuds (`project_with_blueprint`,
créé par `grimoire blueprint new --template pipeline` — voir
`tests/conftest.py`) : sans lui, la toile n'aurait qu'un état vide à montrer,
et aucun de ces mécanismes ne serait exerçable.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")


@pytest.fixture
def concevoir(workspace: Page, project_with_blueprint: tuple[Path, str]) -> tuple[Page, str]:
    """La coque, avec un blueprint réel déjà sur disque, ouverte sur Concevoir."""
    _root, bp_id = project_with_blueprint
    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
    workspace.wait_for_selector(".cv-card", timeout=15_000)
    return workspace, bp_id


@pytest.fixture
def concevoir_scratch_blueprint(workspace: Page, real_project: Path) -> tuple[Page, str]:
    """Un blueprint jetable, dédié à l'écriture (issue #535).

    Jamais `workspace-demo` (`project_with_blueprint`) : ce fichier n'est créé
    qu'une fois pour toute la session e2e (`real_project` est session-scoped)
    et tous les autres tests de ce module comptent sur ses nœuds d'origine —
    un ajout de nœud réellement enregistré le polluerait pour le reste de la
    session. Un id unique par test garde l'écriture isolée.
    """
    bp_id = f"save-test-{uuid.uuid4().hex[:8]}"
    target = real_project / "_grimoire" / "blueprints" / f"{bp_id}.blueprint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "grimoire", "blueprint", "new", bp_id,
         "--out", str(target), "--template", "pipeline"],
        cwd=str(real_project), capture_output=True, text=True, check=False, timeout=60,
    )
    if not target.is_file():
        pytest.skip(f"`grimoire blueprint new` n'a pas produit de fichier ici : {result.stderr[-400:]}")
    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
    workspace.wait_for_selector(f'.cv-card[data-container-id="{bp_id}"]', timeout=15_000)
    return workspace, bp_id


# ── Niveau Projet : trois vues, sélection → inspecteur ──────────────────────


def test_concevoir_s_ouvre_sur_le_blueprint_reel_du_projet(concevoir: tuple[Page, str]) -> None:
    page, bp_id = concevoir

    assert page.locator(".cv-card").count() >= 1
    assert bp_id in page.locator("#canvas").inner_text()
    assert "démo" not in page.locator("#canvas").inner_text().lower()


def test_les_trois_vues_sont_proposees_au_niveau_projet(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir

    labels = [b.strip() for b in page.locator("#view-seg button").all_inner_texts()]
    assert labels == ["Carte", "Board", "Liste"]


def test_la_vue_liste_montre_les_six_colonnes_de_la_spec(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator('#view-seg button[data-value="liste"]').click()
    page.wait_for_selector("table.cv-table")

    headers = [h.strip() for h in page.locator("table.cv-table th").all_inner_texts()]
    assert headers == ["Nom", "Genre", "Agents", "Équipe", "Validation", "Dernière modification"]


def test_la_vue_board_groupe_par_genre(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator('#view-seg button[data-value="board"]').click()
    page.wait_for_selector(".cv-board-col")

    assert page.locator(".cv-board-col").count() >= 1


def test_la_selection_d_un_container_remplit_l_inspecteur(concevoir: tuple[Page, str]) -> None:
    page, bp_id = concevoir
    page.locator(".cv-card").first.click()

    page.wait_for_function(
        "(id) => document.getElementById('inspector-body').innerText.includes(id)", arg=bp_id
    )
    assert "Genre" in page.locator("#inspector-body").inner_text()


# ── Zoom Projet → Workflow : l'éditeur de graphe ────────────────────────────


def test_le_double_clic_zoome_sur_le_workflow_et_dessine_le_graphe(concevoir: tuple[Page, str]) -> None:
    page, _bp_id = concevoir
    page.locator(".cv-card").first.dblclick()

    page.wait_for_selector(".cv-node", timeout=15_000)

    assert page.locator('#zoom-seg button[aria-pressed="true"]').inner_text().strip() == "Workflow"
    assert page.locator(".cv-node").count() >= 1
    assert page.locator(".cv-svg path").count() >= 1, "un template `pipeline` a au moins une arête"


def test_valider_ecrit_le_verdict_dans_le_dock_problemes(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    page.get_by_role("button", name="Valider").click()
    page.wait_for_function(
        "() => document.querySelector('[data-dock-tab=\"problemes\"]').getAttribute('aria-selected') === 'true'"
    )
    dock_text = page.locator("#dock-body").inner_text()
    assert "blueprint validate" in dock_text


def test_la_bibliotheque_de_noeuds_liste_les_sept_primitives(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    # Scopé à la barre d'outils : le rail de la coque porte lui aussi une
    # icône « Bibliothèque » (raccourci 2, voir le test dédié plus bas), et
    # `get_by_role` ferait sinon une correspondance ambiguë.
    page.locator(".cv-toolbar").get_by_role("button", name="Bibliothèque").click()
    page.wait_for_selector(".cv-prim", timeout=10_000)

    assert page.locator(".cv-prim").count() == 7


def test_le_raccourci_2_du_rail_ouvre_la_bibliotheque_de_noeuds(concevoir: tuple[Page, str]) -> None:
    """Reste connu de l'intégration des cinq lots : le rail annonce « 2 »
    pour la bibliothèque (shell.js, `RAIL`), mais rien ne l'activait — ni le
    clic sur l'icône, ni le raccourci clavier — faute d'un espace qui
    l'enregistre (`ctx.rail.on('library', …)`). Concevoir est le seul espace
    à s'en servir ; ailleurs, « 2 » reste sans effet, ce que ce test ne
    couvre pas puisqu'aucune spec ne le demande."""
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    assert page.locator(".cv-palette[data-open='1']").count() == 0

    page.locator("body").press("2")
    page.wait_for_selector(".cv-palette[data-open='1']")
    page.wait_for_selector(".cv-prim", timeout=10_000)
    assert page.locator(".cv-prim").count() == 7

    page.locator("body").press("2")
    # `state="attached"` : le tiroir fermé (`data-open="0"`) est masqué en CSS,
    # pas retiré du DOM — `wait_for_selector` attend "visible" par défaut et
    # ne se résoudrait jamais.
    page.wait_for_selector(".cv-palette[data-open='0']", state="attached")


# ── Zoom Workflow → Nœud : inspecteur à quatre onglets ──────────────────────


def test_le_niveau_noeud_montre_un_inspecteur_a_quatre_onglets(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    page.locator(".cv-node").first.dblclick()

    page.wait_for_function(
        "() => document.querySelector('#zoom-seg button[aria-pressed=\"true\"]').textContent.trim() === 'Nœud'"
    )
    tabs = [t.strip() for t in page.locator(".cv-tab").all_inner_texts()]
    assert tabs == ["Propriétés", "Validation", "Coût", "Preuves"]


def test_les_quatre_onglets_du_noeud_changent_le_contenu_affiche(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    page.locator(".cv-node").first.dblclick()
    page.wait_for_selector(".cv-tab")

    first_panel = page.locator(".cv-tab-body").inner_text()
    page.get_by_role("button", name="Coût").click()
    page.wait_for_function(
        "(prev) => document.querySelector('.cv-tab-body').innerText !== prev", arg=first_panel
    )


# ── Critère 2, sur l'écran que ce lot ajoute ────────────────────────────────
#
# `test_workspace_shell.py` mesure le plancher sur l'espace par défaut
# (Piloter) : il ne verrait jamais un `font-size` posé en dur dans la toile de
# Concevoir. C'était le cas de `.cv-node .kind` avant correction — trouvé ici,
# pas à la lecture de la feuille.


def test_aucun_texte_du_graphe_sous_le_plancher_sombre(concevoir: tuple[Page, str]) -> None:
    page, _ = concevoir
    page.locator(".cv-card").first.dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    sizes = page.eval_on_selector_all(
        "#canvas .cv-node, #canvas .cv-node *",
        "(els) => els.map((el) => parseFloat(getComputedStyle(el).fontSize))",
    )
    assert sizes, "aucun texte mesuré dans le graphe"
    assert min(sizes) >= 13.0, f"un texte du graphe rend sous le plancher sombre : {min(sizes)}px"


# ── Ajouter un nœud depuis la Bibliothèque persiste (issue #535) ───────────
#
# Avant correctif : `addNode` ne mutait que `state.blueprint` en mémoire —
# aucun appel d'écriture, le nœud disparaissait au rechargement malgré le
# message de succès affiché dans le dock. Reproduit ici avec un blueprint
# jetable (`concevoir_scratch_blueprint`), jamais `workspace-demo`.


def test_ajouter_un_noeud_affiche_modifie_non_enregistre(
    concevoir_scratch_blueprint: tuple[Page, str],
) -> None:
    """Le message ne doit plus jamais affirmer un succès qui n'existe pas sur
    disque tant que « Enregistrer » n'a pas été cliqué."""
    page, bp_id = concevoir_scratch_blueprint
    page.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    before = page.locator(".cv-node").count()

    page.locator("body").press("2")
    page.wait_for_selector(".cv-prim", timeout=10_000)
    page.locator(".cv-prim").first.click()

    page.wait_for_function(
        "() => [...document.querySelectorAll('.cv-toolbar button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )
    assert page.locator(".cv-node").count() == before + 1
    save_btn = page.locator(".cv-toolbar button", has_text="Enregistrer")
    assert save_btn.is_enabled(), "le bouton doit s'activer dès qu'il y a quelque chose à enregistrer"


def test_enregistrer_apres_ajout_de_noeud_persiste_apres_rechargement(
    concevoir_scratch_blueprint: tuple[Page, str], real_project: Path
) -> None:
    """Rouge avant le correctif : le nœud disparaissait au rechargement.

    Vert après : « Enregistrer » écrit réellement `blueprintPut`
    (`PUT /api/blueprints/<id>`, déjà servi côté serveur), et le nœud
    survit à un rechargement complet de la page.
    """
    page, bp_id = concevoir_scratch_blueprint
    page.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)
    before = page.locator(".cv-node").count()

    page.locator("body").press("2")
    page.wait_for_selector(".cv-prim", timeout=10_000)
    page.locator(".cv-prim").first.click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.cv-toolbar button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )

    page.locator(".cv-toolbar button", has_text="Enregistrer").click()
    page.wait_for_function(
        "() => ![...document.querySelectorAll('.cv-toolbar button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )

    target = real_project / "_grimoire" / "blueprints" / f"{bp_id}.blueprint.json"
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert len(saved["nodes"]) == before + 1, "le nœud doit être écrit sur disque, pas seulement en mémoire"

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'concevoir'")
    page.wait_for_selector(f'.cv-card[data-container-id="{bp_id}"]', timeout=15_000)
    page.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    assert page.locator(".cv-node").count() == before + 1, "le nœud doit survivre au rechargement"


def test_changer_de_blueprint_avec_des_modifications_non_enregistrees_demande_confirmation(
    concevoir_scratch_blueprint: tuple[Page, str],
) -> None:
    """Même mécanisme que Source (`source.js::openFile`) : quitter un
    blueprint modifié sans l'avoir enregistré doit demander confirmation,
    puisque rouvrir importe quoi (même ce même blueprint) relit le disque et
    écraserait le brouillon en mémoire."""
    page, bp_id = concevoir_scratch_blueprint
    page.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    page.wait_for_selector(".cv-node", timeout=15_000)

    page.locator("body").press("2")
    page.wait_for_selector(".cv-prim", timeout=10_000)
    page.locator(".cv-prim").first.click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.cv-toolbar button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )

    dialog_messages: list[str] = []
    page.on("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.dismiss()))
    # Revenir au niveau Projet ne relit rien (`setZoom` seul) : aucune
    # confirmation attendue ici. C'est rouvrir un blueprint — même celui-ci —
    # qui relirait le disque et écraserait le brouillon.
    page.locator("#zoom-seg button", has_text="Projet").click()
    page.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    page.wait_for_timeout(300)

    assert dialog_messages, "rouvrir un blueprint modifié doit demander confirmation avant d'écraser le brouillon"
    # `dismiss()` = Annuler : `zoomToWorkflow` doit s'être arrêté avant son
    # propre `setZoom('workflow')` — le zoom reste sur Projet, pas Workflow.
    assert page.locator("#zoom-seg button[aria-pressed='true']").inner_text().strip() == "Projet"
