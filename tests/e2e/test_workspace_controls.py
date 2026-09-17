"""Contrôles de la coque : segments de vue/zoom, chip projet, aide de Suggérer,
états vides — l'audit du 2026-09-14 (boutons inertes, sélection visuelle qui ne
suit pas, aucun moyen visible de changer de projet ou de revenir à un menu).

RÈGLE : tout nouveau contrôle de vue ou de zoom (``ctx.docbar.setViews`` /
``ctx.docbar.setZoom``, rendu par ``#view-seg``/``#zoom-seg``) DOIT passer par
``_assert_segment_toggles`` ci-dessous, sur au moins un espace qui l'utilise.
C'est ce test générique qui aurait attrapé les deux régressions de cet audit :

- Observer (``web/workspace/spaces/observer.js``) appelait `setViews(...)`
  SANS le callback ``onPick`` : les boutons Runtime/Activité/RTK/Bench ne
  faisaient rigoureusement rien (``aria-pressed`` figé).
- Mémoire (``web/workspace/spaces/memoire.js``) appelait `setViews(...)` UNE
  SEULE FOIS, hors de la boucle de rendu ``draw()`` : le contenu changeait au
  clic, mais le bouton actif restait visuellement figé sur le précédent.

Un simple test de présence des boutons (compter, lire les libellés) ne casse
pas sur ces deux bugs — seul un clic réel suivi d'une lecture de
``aria-pressed`` et du style calculé les attrape. D'où ce fichier séparé de
``test_workspace_lot4_spaces.py`` : centraliser la mécanique, pas la dupliquer
par espace.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _computed_signature(page: Page, handle: object) -> str:
    """Fond + soulignement + couleur : ce que la CSS de `.seg > button` fait
    varier entre un bouton actif (`aria-pressed="true"`) et ses voisins."""
    return page.evaluate(
        "(el) => { const s = getComputedStyle(el); "
        "return [s.backgroundColor, s.color, s.textDecorationLine].join('|'); }",
        handle,
    )


def _assert_segment_toggles(page: Page, selector: str) -> None:
    """Clique chaque bouton du groupe `selector`, deux fois chacun, dans deux
    ordres différents (croissant puis décroissant) — et vérifie après CHAQUE
    clic : `aria-pressed="true"` sur le bouton cliqué et lui seul, et un style
    calculé distinct de tous ses voisins.

    Exige au moins deux boutons : un seul ne prouverait ni l'exclusivité
    (`false` sur les autres) ni le contraste (rien à comparer).
    """
    buttons = page.locator(selector)
    count = buttons.count()
    assert count >= 2, f"{selector} : au moins deux boutons attendus, {count} trouvé(s)"

    def check(clicked: int) -> None:
        page.wait_for_function(
            "(a) => document.querySelectorAll(a[0])[a[1]]?.getAttribute('aria-pressed') === 'true'",
            arg=[selector, clicked],
        )
        pressed = [buttons.nth(i).get_attribute("aria-pressed") for i in range(count)]
        assert pressed[clicked] == "true", (
            f"{selector} : le bouton {clicked} cliqué doit porter aria-pressed=true "
            f"(observé : {pressed})"
        )
        for i, value in enumerate(pressed):
            if i != clicked:
                assert value == "false", (
                    f"{selector} : le bouton {i} doit rester aria-pressed=false "
                    f"pendant que {clicked} est actif (observé : {pressed})"
                )
        signatures = [_computed_signature(page, buttons.nth(i).element_handle()) for i in range(count)]
        for i, sig in enumerate(signatures):
            if i != clicked:
                assert sig != signatures[clicked], (
                    f"{selector} : le bouton actif {clicked} doit avoir un style calculé "
                    f"différent du voisin {i} — sinon la sélection visuelle ne se voit pas "
                    f"(bug memoire.js : {clicked} affiché actif, {i} resté actif à l'écran)"
                )

    # Deux passes, ordres différents (croissant puis décroissant) : chaque
    # bouton est cliqué deux fois au total, jamais dans le même ordre.
    for order in (range(count), reversed(range(count))):
        for index in order:
            buttons.nth(index).click()
            check(index)


# ── Régression directe des deux bugs (Observer, Mémoire) ────────────────────


def test_observer_view_seg_bascule_correctement(workspace: Page) -> None:
    _goto(workspace, "observer")
    workspace.wait_for_selector("#view-seg button")
    labels = workspace.locator("#view-seg button").all_inner_texts()
    assert [lbl.strip() for lbl in labels] == ["Runtime", "Activité", "RTK", "Bench"]
    _assert_segment_toggles(workspace, "#view-seg button")


def test_observer_activite_rtk_bench_ne_restent_jamais_muets(workspace: Page) -> None:
    """Avant cette issue, ces trois onglets n'avaient tout simplement aucun
    contenu de prévu (dead code) : cliquer dessus ne changeait rien à
    `#canvas`, sans le dire. Chacun doit désormais rendre quelque chose de
    lisible — une donnée réelle, ou l'aveu explicite qu'il n'y en a pas."""
    _goto(workspace, "observer")
    workspace.wait_for_selector("#view-seg button")
    for view_id in ("activite", "rtk", "bench"):
        workspace.locator(f'#view-seg button[data-value="{view_id}"]').click()
        workspace.wait_for_function(
            "(v) => document.querySelector(`#view-seg button[data-value=\"${v}\"]`)"
            ".getAttribute('aria-pressed') === 'true'",
            arg=view_id,
        )
        body = workspace.locator("#canvas").inner_text()
        assert body.strip(), f"onglet {view_id} : #canvas ne doit jamais rester vide"


def test_memoire_view_seg_bascule_correctement(workspace: Page) -> None:
    _goto(workspace, "memoire")
    workspace.wait_for_selector("#view-seg button")
    labels = [lbl.strip() for lbl in workspace.locator("#view-seg button").all_inner_texts()]
    if len(labels) < 2:
        pytest.skip("mémoire non initialisée ici : un seul onglet (Flotte)")
    _assert_segment_toggles(workspace, "#view-seg button")


# ── Couverture élargie : les autres segments de la coque ────────────────────


def test_executer_view_seg_bascule_correctement(workspace: Page, project_with_task: tuple) -> None:
    _goto(workspace, "executer")
    workspace.wait_for_selector("#view-seg button")
    _assert_segment_toggles(workspace, "#view-seg button")


def test_concevoir_view_seg_bascule_correctement(workspace: Page, project_with_blueprint: tuple) -> None:
    _goto(workspace, "concevoir")
    workspace.wait_for_selector("#view-seg button")
    _assert_segment_toggles(workspace, "#view-seg button")


def test_piloter_zoom_seg_bascule_correctement(cockpit_workspace: Page) -> None:
    _goto(cockpit_workspace, "piloter")
    cockpit_workspace.wait_for_selector("#zoom-seg button")
    _assert_segment_toggles(cockpit_workspace, "#zoom-seg button")


# ── #project-chip : jusqu'ici décoratif, aucun clic ne l'ouvrait ────────────


def test_le_chip_projet_ouvre_la_palette_sur_la_section_projets(cockpit_workspace: Page) -> None:
    page = cockpit_workspace
    assert page.locator("#project-chip").count() == 1

    page.locator("#project-chip").click()
    page.wait_for_selector("#palette:not([hidden])")
    page.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 1")

    first_item = page.locator("#palette-list li").first
    assert first_item.get_attribute("class") == "palette-section"
    # `.palette-section` est rendu en majuscules par la CSS
    # (`text-transform: uppercase`) : `inner_text()` reflète ce rendu, pas le
    # texte source — d'où la comparaison insensible à la casse.
    assert first_item.inner_text().strip().lower() == "projets"

    # Au moins un projet du registre (`served_cockpit` en enrôle un réel) doit
    # apparaître juste après l'en-tête « Projets », avant tout autre en-tête.
    second_item = page.locator("#palette-list li").nth(1)
    assert second_item.get_attribute("role") == "option"
    assert second_item.locator(".lbl").inner_text().strip() == "Projet"

    page.locator("body").press("Escape")


def test_le_chip_projet_a_un_indice_visuel_de_clic(cockpit_workspace: Page) -> None:
    """curseur, survol, chevron — sans ça, rien ne dit que le chip est
    cliquable (l'audit : « je ne vois pas comment jongler entre mes
    projets »)."""
    page = cockpit_workspace
    chip = page.locator("#project-chip")
    assert chip.evaluate("(el) => getComputedStyle(el).cursor") == "pointer"
    assert page.locator("#project-chip .chevron").count() == 1

    before = chip.evaluate("(el) => getComputedStyle(el).backgroundColor")
    chip.hover()
    after = chip.evaluate("(el) => getComputedStyle(el).backgroundColor")
    assert before != after, "le survol doit changer visuellement le chip"


# ── Bouton « Suggérer » : aide permanente, lisible sans survol ──────────────


def _pick_untouched_kit_file(page: Page) -> str:
    tree = page.evaluate("() => fetch('/api/workspace/files?tier=kit').then((r) => r.json())")
    files = tree["tiers"][0]["files"]
    candidate = next(f for f in files if not f["overridden"] and f["path"].endswith(".md"))
    return str(candidate["path"])


def _open_as_override(page: Page) -> None:
    kit_path = _pick_untouched_kit_file(page)
    name = kit_path.rsplit("/", 1)[-1]
    page.locator(".tree .sr-tree-file", has_text=name).first.click()
    page.wait_for_function(
        "(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=kit_path
    )
    page.locator(".sr-banner .btn.pri").click()
    page.wait_for_function(
        "() => document.querySelector('.sr-docrow .mono')?.textContent.includes('_grimoire/overrides/')"
    )


def test_suggerer_a_une_aide_permanente_visible_sans_survol(workspace: Page) -> None:
    """Avant cette issue, seul le `title` (au survol) expliquait ce que fait
    « Suggérer ». `.sr-assist-help` porte désormais la phrase fixe en
    permanence — vérifié SANS jamais appeler `.hover()`."""
    workspace.route(
        "**/api/workspace/assist*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "model": "demo-coder", "available": True}),
        ),
    )
    _goto(workspace, "source")
    workspace.wait_for_selector(".tree .sr-tree-file", timeout=10_000)
    _open_as_override(workspace)

    workspace.wait_for_selector(".sr-assist-btn:not([hidden])", timeout=10_000)
    help_el = workspace.locator(".sr-assist-help")
    assert help_el.is_visible()
    help_text = help_el.inner_text()
    assert "source.assist.model" in help_text
    assert "jamais insérée sans votre clic" in help_text


def test_suggerer_indisponible_est_lisible_sans_survol(workspace: Page) -> None:
    """Avant cette issue : opt-in actif mais modèle indisponible (Ollama down,
    modèle absent…) faisait disparaître le bouton ET son statut EN ENTIER —
    aucun indice, même au survol, puisqu'il n'y avait plus rien à survoler."""
    workspace.route(
        "**/api/workspace/assist*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "enabled": True, "model": "demo-coder", "available": False,
                "reason": "Ollama injoignable sur 127.0.0.1:11434",
            }),
        ),
    )
    _goto(workspace, "source")
    workspace.wait_for_selector(".tree .sr-tree-file", timeout=10_000)
    _open_as_override(workspace)

    workspace.wait_for_selector(".sr-assist-btn:not([hidden])", timeout=10_000)
    assert workspace.locator(".sr-assist-btn").is_disabled()
    status_text = workspace.locator(".sr-assist-status").inner_text()
    assert "indisponible" in status_text.lower()
    assert "Ollama injoignable" in status_text
    assert workspace.locator(".sr-assist-help").is_visible()


# ── États vides : commande réelle, boutons présents mais désactivés ────────


def test_executer_etat_vide_montre_la_commande_et_desactive_les_vues(empty_workspace: Page) -> None:
    page = empty_workspace
    _goto(page, "executer")
    page.wait_for_selector(".empty")

    command = page.locator(".empty code").inner_text()
    assert command.startswith("grimoire task add")

    buttons = page.locator("#view-seg button")
    assert buttons.count() == 4, "les quatre vues restent visibles, jamais absentes"
    for i in range(buttons.count()):
        assert buttons.nth(i).is_disabled()
        assert buttons.nth(i).get_attribute("aria-pressed") == "false"


def test_executer_etat_non_migre_propose_le_bouton_migrer_les_taches(empty_workspace: Page) -> None:
    """ADR-007 (issue #559, lot 4.1 3/3) : un projet enrôlé au standard mais
    jamais migré vers le Mission Ledger propose l'action au lieu du seul
    rappel `grimoire task add` — et le clic ouvre réellement le ledger.

    Mute la fixture partagée `empty_workspace` (idempotent : rejouer la
    migration ne change plus rien ensuite), même patron que les tests
    d'acceptation de proposition de ce module qui écrivent aussi dans ce
    projet une fois pour de bon.
    """
    page = empty_workspace
    _goto(page, "executer")
    page.wait_for_selector(".empty")

    migrate_btn = page.locator("button", has_text="Migrer les tâches")
    assert migrate_btn.count() == 1
    assert migrate_btn.is_enabled()

    migrate_btn.click()
    page.wait_for_selector(".ex-card", timeout=10_000)

    assert page.locator(".empty").count() == 0
    # La tâche `bootstrap` scaffoldée par `standard init --profile governed`
    # (ADR-007 point 1) est désormais visible : la migration n'a rien inventé,
    # elle a ouvert le ledger sur ce que le board déclarait déjà.
    page.locator(".ex-card", has_text="Bootstrap agentic standard runtime").wait_for()


def test_concevoir_etat_vide_montre_la_commande_et_desactive_les_vues(empty_workspace: Page) -> None:
    page = empty_workspace
    _goto(page, "concevoir")
    page.wait_for_selector(".empty")

    command = page.locator(".empty code").inner_text()
    assert command.startswith("grimoire blueprint new")

    buttons = page.locator("#view-seg button")
    assert buttons.count() == 3, "Carte/Board/Liste restent visibles, jamais absentes"
    for i in range(buttons.count()):
        assert buttons.nth(i).is_disabled()
