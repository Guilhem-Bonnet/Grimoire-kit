# ruff: noqa: F811
"""Les lignes de liste : actions au survol (P56), clavier d'abord (P58), squelettes (P60).

Un vrai cockpit, celui de ``test_workspace_a_traiter_p32`` : « Atelier Nord »
(trois éléments qui attendent, deux tâches), « Studio Pixel », « Calme » et
« Banc d'essai » au registre — la Flotte et la rubrique Projets ont donc quatre
lignes, le tableau de Travail au moins deux cartes.

Ce que chaque test prouve :

- P58 : un seul contrôleur de liste (``listNav`` de shell.js) — j et k
  parcourent les cartes du tableau de Travail, les lignes de sa liste, la
  table de la Flotte et celle des Projets ; Entrée lance l'action principale
  de la ligne tenue ; ⌘K ouvre la palette depuis une liste ; rien ne s'active
  pendant une saisie (champ de filtre, zone ``contenteditable``) ;
- P56 : les actions secondaires d'une ligne apparaissent au survol ET au focus
  clavier, restent dans l'ordre de tabulation et toutes atteignables par le
  menu « … » de la ligne, qui est le composant du menu de l'inspecteur (P11) ;
  sur écran tactile (``hover: none``, ``pointer: coarse``) elles sont visibles
  sans survol ; l'indicateur de focus tient 3:1 dans les deux thèmes ;
- P60 : pendant le chargement, chaque rubrique montre un squelette à la forme
  de sa liste (colonnes de cartes, lignes de table, résultats, tuiles).

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les captures de
relecture (clair et sombre, 1440x900) ; sans la variable il ne fait rien (il ne
se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route

from .test_workspace_a_traiter_p32 import (  # noqa: F401  (fixture de session partagée)
    served_inbox,
)

ACTIVE = "() => { const a = document.activeElement; return a ? (a.dataset.task || a.dataset.project || a.dataset.slug || '') : ''; }"


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", reduced_motion="reduce")
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    shot = context.new_page()
    try:
        yield shot
    finally:
        context.close()


def _open(page: Page, served: dict[str, str], suffix: str) -> None:
    page.goto(f"{served['url']}/workspace/index.html{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


def _travail(page: Page, served: dict[str, str], view: str = "") -> None:
    _open(page, served, f"?project={served['home']}#travail" + (f"?view={view}" if view else ""))
    rows = ".ex-list tbody tr[data-task]" if view == "liste" else ".ex-card"
    page.wait_for_function(f"() => document.querySelectorAll('{rows}').length >= 2", timeout=30_000)
    if view != "liste":
        # P72v2 : la tâche bloquée du seed vit désormais dans la section
        # « Bloqué » repliée par défaut du tableau compact — la déplier garde
        # deux cartes VISIBLES (l'hypothèse de P56/P58 : j/k, survol, focus
        # parcourent les cartes affichées, dont la bloquée).
        page.locator("[data-role='blocked-count']").click()
        page.locator("[data-role='blocked-section']").wait_for(state="visible", timeout=30_000)
        page.wait_for_function(
            "() => [...document.querySelectorAll('.ex-card')].filter((n) => n.getClientRects().length).length >= 2",
            timeout=30_000,
        )


def _flotte(page: Page, served: dict[str, str]) -> None:
    _open(page, served, "?scope=all#flotte")
    page.wait_for_function("() => document.querySelectorAll('.pl-fleet tbody tr[data-project]').length >= 2", timeout=30_000)


def _visible_ids(page: Page, selector: str, attr: str) -> list[str]:
    return page.evaluate(
        "([sel, attr]) => [...document.querySelectorAll(sel)].filter((n) => n.getClientRects().length).map((n) => n.dataset[attr])",
        [selector, attr],
    )


def _opacity(page: Page, locator: str) -> float:
    return float(page.locator(locator).first.evaluate("(n) => getComputedStyle(n).opacity"))


# ── P58 : j / k / Entrée, un seul contrôleur pour toutes les listes ────────


def test_p58_j_et_k_parcourent_les_cartes_de_travail_et_entree_ouvre_la_carte(
    page: Page, served_inbox: dict[str, str]
) -> None:
    _travail(page, served_inbox)
    ids = _visible_ids(page, ".ex-card", "task")
    page.keyboard.press("j")
    assert page.evaluate(ACTIVE) == ids[0]
    page.keyboard.press("j")
    assert page.evaluate(ACTIVE) == ids[1]
    page.keyboard.press("k")
    page.keyboard.press("k")  # déjà sur la première : on y reste
    assert page.evaluate(ACTIVE) == ids[0]
    page.keyboard.press("j")
    # j / k ne font que déplacer le focus : rien n'est encore ouvert.
    assert page.locator(".ex-card[aria-current='true']").count() == 0
    page.keyboard.press("Enter")
    page.wait_for_function("(id) => document.querySelector(`.ex-card[data-task='${id}']`)?.getAttribute('aria-current') === 'true'", arg=ids[1])
    page.wait_for_function("(id) => document.querySelector('#inspector-body').innerText.includes(id)", arg=ids[1])
    # Le focus reste sur la carte ouverte, après le re-rendu du tableau.
    assert page.evaluate(ACTIVE) == ids[1]


def test_p58_j_et_k_parcourent_la_liste_de_travail(page: Page, served_inbox: dict[str, str]) -> None:
    _travail(page, served_inbox, "liste")
    ids = _visible_ids(page, ".ex-list tbody tr[data-task]", "task")
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.evaluate(ACTIVE) == ids[1]
    page.keyboard.press("Enter")
    page.wait_for_function(
        "(id) => document.querySelector(`.ex-list tr[data-task='${id}']`)?.getAttribute('aria-current') === 'true'", arg=ids[1]
    )
    assert page.locator(".ex-list tbody tr[aria-current='true']").count() == 1


def test_p58_la_flotte_marque_la_ligne_active_et_entree_ouvre_le_projet(page: Page, served_inbox: dict[str, str]) -> None:
    _flotte(page, served_inbox)
    slugs = _visible_ids(page, ".pl-fleet tbody tr[data-project]", "project")
    current = "() => [...document.querySelectorAll('.pl-fleet tbody tr[aria-current=\"true\"]')].map((r) => r.dataset.project)"
    page.keyboard.press("j")
    assert page.evaluate(current) == [slugs[0]]
    page.keyboard.press("j")
    assert page.evaluate(current) == [slugs[1]]
    assert page.evaluate(ACTIVE) == slugs[1]
    page.keyboard.press("k")
    assert page.evaluate(current) == [slugs[0]]
    # ⌘K depuis une ligne tenue : la palette s'ouvre, j n'y bouge plus rien.
    page.keyboard.press("Control+k")
    assert page.evaluate("() => window.GrimoireWorkspace.paletteOpen") is True
    page.keyboard.press("j")
    assert page.evaluate(current) == [slugs[0]]
    page.keyboard.press("Escape")
    # « ? » affiche l'aide depuis une ligne tenue ; elle annonce J / K pour toutes les listes.
    page.locator(f".pl-fleet tbody tr[data-project='{slugs[0]}']").focus()
    page.keyboard.press("Shift+?")
    assert "toutes les listes" in page.locator("#shortcuts-list").inner_text()
    page.keyboard.press("Escape")
    page.locator(f".pl-fleet tbody tr[data-project='{slugs[0]}']").focus()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(rf"project={re.escape(slugs[0])}"), timeout=30_000)
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    assert page.evaluate("() => window.GrimoireWorkspace.scope.project") == slugs[0]


def test_p58_la_rubrique_projets_se_parcourt_au_clavier(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#configuration/projets")
    page.wait_for_function("() => document.querySelectorAll('.tbl tbody tr[data-slug]').length >= 2", timeout=30_000)
    slugs = _visible_ids(page, ".tbl tbody tr[data-slug]", "slug")
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.evaluate(ACTIVE) == slugs[1]
    assert page.locator(f".tbl tbody tr[data-slug='{slugs[1]}']").get_attribute("aria-current") == "true"
    assert page.locator(".tbl tbody tr[aria-current='true']").count() == 1


def test_p58_aucun_raccourci_de_ligne_pendant_une_saisie(page: Page, served_inbox: dict[str, str]) -> None:
    _flotte(page, served_inbox)
    field = page.locator(".pl-search")
    field.focus()
    page.keyboard.press("j")
    page.keyboard.press("k")
    assert field.input_value() == "jk"
    assert page.locator(".pl-fleet tbody tr[aria-current='true']").count() == 0
    field.fill("")
    page.evaluate("() => document.querySelector('.pl-fleet').insertAdjacentHTML('afterbegin', '<div id=\"t-ce\" contenteditable=\"true\"></div>')")
    page.locator("#t-ce").focus()
    for key in ("j", "k", "Enter", "Shift+?"):
        page.keyboard.press(key)
    assert page.locator(".pl-fleet tbody tr[aria-current='true']").count() == 0
    assert page.locator("#shortcuts").is_hidden()
    assert page.evaluate("() => window.GrimoireWorkspace.scope.all") is True


# ── P56 : actions au survol, au focus, et par le menu « … » ────────────────


def test_p56_actions_au_survol_et_au_focus_clavier(page: Page, served_inbox: dict[str, str]) -> None:
    _travail(page, served_inbox)
    ids = _visible_ids(page, ".ex-card", "task")
    first = f".ex-card[data-task='{ids[0]}']"
    second = f".ex-card[data-task='{ids[1]}']"
    page.mouse.move(1, 1)
    assert _opacity(page, f"{first} .row-actions") == 0
    # Au survol.
    page.locator(first).hover()
    assert _opacity(page, f"{first} .row-actions") == 1
    assert _opacity(page, f"{second} .row-actions") == 0
    page.mouse.move(1, 1)
    # Au focus clavier : la carte tenue au clavier montre ses actions.
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.evaluate(ACTIVE) == ids[1]
    assert _opacity(page, f"{second} .row-actions") == 1
    assert _opacity(page, f"{first} .row-actions") == 0
    # Elles restent dans l'ordre de tabulation : Tab depuis la carte y entre.
    reached = False
    for _ in range(6):
        page.keyboard.press("Tab")
        if page.evaluate(f"() => document.activeElement.matches(\"{second} .row-act, {second} .more-btn\")"):
            reached = True
            break
    assert reached, "Tab depuis la carte n'atteint pas ses actions"
    assert _opacity(page, f"{second} .row-actions") == 1


def test_p56_le_menu_de_ligne_porte_toutes_les_actions_et_reprend_le_composant_p11(
    page: Page, served_inbox: dict[str, str]
) -> None:
    _travail(page, served_inbox)
    ids = _visible_ids(page, ".ex-card", "task")
    card = page.locator(f".ex-card[data-task='{ids[0]}']")
    labels = card.locator(".row-act").evaluate_all("(ns) => ns.map((n) => n.getAttribute('aria-label'))")
    assert len(labels) >= 2
    toggle = card.locator("[data-role='row-more']")
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    panel = card.locator("[data-role='row-more-panel']")
    assert panel.is_visible()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert panel.locator(".more-item").all_inner_texts() == labels
    # Un clic dans le menu n'ouvre pas la carte.
    assert page.locator(".ex-card[aria-current='true']").count() == 0
    # Échap referme et rend le focus au bouton « … ».
    panel.locator(".more-item").first.focus()
    page.keyboard.press("Escape")
    assert panel.is_hidden()
    assert page.evaluate("() => document.activeElement.dataset.role") == "row-more"
    # Une action depuis le menu : copier l'identifiant.
    toggle.click()
    panel.locator(".more-item[data-action='copy-id']").click()
    assert panel.is_hidden()
    assert page.evaluate("() => navigator.clipboard.readText()") == ids[0]
    # Le même composant que le menu de l'inspecteur (P11) : mêmes classes de la coque.
    card.click()
    page.wait_for_function("(id) => document.querySelector('#inspector-body').innerText.includes(id)", arg=ids[0])
    inspector_more = page.locator("#inspector-body [data-role='more']")
    assert "more-btn" in (inspector_more.get_attribute("class") or "").split()
    assert "more-panel" in (page.locator("#inspector-body [data-role='more-panel']").get_attribute("class") or "").split()
    assert "more-panel" in (panel.get_attribute("class") or "").split()


def test_p56_les_lignes_de_la_flotte_et_d_a_traiter_ont_leurs_actions(page: Page, served_inbox: dict[str, str]) -> None:
    _flotte(page, served_inbox)
    row = page.locator(".pl-fleet tbody tr[data-project]").first
    row.hover()
    assert _opacity(page, ".pl-fleet tbody tr[data-project]:hover .row-actions") == 1
    actions = row.locator(".row-act").evaluate_all("(ns) => ns.map((n) => n.dataset.action)")
    # P57 a ajouté « Retirer de la flotte » (absent sur le projet servi).
    assert actions[:2] == ["copy-path", "open-work"] and set(actions[2:]) <= {"remove"}
    row.locator("[data-role='row-more']").click()
    assert row.locator("[data-role='row-more-panel'] .more-item").count() == len(actions)
    # Ouvrir le menu n'a pas ouvert la fiche du projet.
    assert page.evaluate("() => window.GrimoireWorkspace.scope.all") is True

    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    page.wait_for_selector(".at-row")
    first = page.locator(".at-row").first
    assert first.locator(".row-act[data-action='copy-link']").count() == 1
    # La ligne active (la première, choisie au rendu) montre ses actions.
    assert first.get_attribute("aria-current") == "true"
    assert float(first.locator(".row-actions").evaluate("(n) => getComputedStyle(n).opacity")) == 1


def test_p56_sur_ecran_tactile_les_actions_sont_visibles_sans_survol(browser: Browser, served_inbox: dict[str, str]) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", reduced_motion="reduce", has_touch=True)
    try:
        page = context.new_page()
        session = context.new_cdp_session(page)
        session.send(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "hover", "value": "none"}, {"name": "pointer", "value": "coarse"}]},
        )
        _travail(page, served_inbox)
        assert page.evaluate("() => matchMedia('(hover: none)').matches && matchMedia('(pointer: coarse)').matches")
        ids = _visible_ids(page, ".ex-card", "task")
        assert page.locator(".ex-card[aria-current='true']").count() == 0
        for task_id in ids:
            assert _opacity(page, f".ex-card[data-task='{task_id}'] .row-actions") == 1
        size = page.locator(".ex-card .row-act").first.bounding_box()
        assert size is not None and size["width"] >= 24 and size["height"] >= 24
    finally:
        context.close()


_FOCUS_CONTRAST = """(sel) => {
  const node = document.querySelector(sel);
  const parse = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ring = parse(getComputedStyle(node).outlineColor);
  let host = node.parentElement;
  let bg = parse(getComputedStyle(host).backgroundColor);
  while (host && (bg.length < 3 || bg[3] === 0)) { host = host.parentElement; bg = host ? parse(getComputedStyle(host).backgroundColor) : [255, 255, 255]; }
  const [a, b] = [lum(ring), lum(bg)].sort((x, y) => y - x);
  return { ratio: (a + 0.05) / (b + 0.05), width: parseFloat(getComputedStyle(node).outlineWidth), style: getComputedStyle(node).outlineStyle };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_p56_l_indicateur_de_focus_de_la_ligne_tient_3_pour_1(browser: Browser, served_inbox: dict[str, str], scheme: str) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
    try:
        page = context.new_page()
        _travail(page, served_inbox)
        page.keyboard.press("j")
        ring = page.evaluate(_FOCUS_CONTRAST, ".ex-card:focus")
        assert ring["style"] != "none" and ring["width"] >= 2, ring
        assert ring["ratio"] >= 3.0, ring
        _flotte(page, served_inbox)
        page.keyboard.press("j")
        ring = page.evaluate(_FOCUS_CONTRAST, ".pl-fleet tbody tr:focus")
        assert ring["style"] != "none" and ring["ratio"] >= 3.0, ring
    finally:
        context.close()


# ── P60 : un squelette à la forme de chaque liste ─────────────────────────

_SKELETON_PROBE = """async (id) => {
  const done = window.GrimoireWorkspace.goto(id);
  const sk = document.querySelector('#canvas > .skeleton');
  const count = (sel) => (sk ? sk.querySelectorAll(sel).length : 0);
  const snap = {
    shape: sk ? sk.dataset.shape : null,
    hidden: sk ? sk.getAttribute('aria-hidden') : null,
    cols: count('.sk-col'),
    cardsPerCol: sk ? [...sk.querySelectorAll('.sk-col')].map((c) => c.querySelectorAll('.sk-card').length) : [],
    rows: count('.sk-trow:not(.sk-thead)'),
    cells: sk ? [...sk.querySelectorAll('.sk-trow')].map((r) => r.querySelectorAll('.sk-cell').length) : [],
    results: count('.sk-result'),
    tiles: count('.sk-tile'),
    inbox: count('.sk-inbox-row'),
  };
  await done;
  return snap;
}"""


def _skeleton(page: Page, section: str) -> dict[str, Any]:
    snap = page.evaluate(_SKELETON_PROBE, section)
    if snap["shape"] is None:  # rubrique déjà montée : on part d'ailleurs
        page.evaluate(_SKELETON_PROBE, "preferences")
        snap = page.evaluate(_SKELETON_PROBE, section)
    return snap


def test_p60_chaque_liste_a_un_squelette_a_sa_forme(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, "?scope=all#preferences")
    board = _skeleton(page, "travail")
    assert board["shape"] == "board" and board["hidden"] == "true"
    assert board["cols"] == 4 and all(n >= 2 for n in board["cardsPerCol"]), board

    fleet = _skeleton(page, "flotte")
    assert fleet["shape"] == "table" and fleet["rows"] >= 4 and set(fleet["cells"]) == {5}, fleet

    inbox = _skeleton(page, "a-traiter")
    assert inbox["shape"] == "inbox" and inbox["tiles"] == 3 and inbox["inbox"] >= 4, inbox

    results = _skeleton(page, "memoire")
    assert results["shape"] == "results" and results["results"] >= 4, results

    health = _skeleton(page, "sante")
    assert health["shape"] == "kpi" and health["tiles"] == 4, health

    for section in ("projets", "journal"):
        table = _skeleton(page, section)
        assert table["shape"] == "table" and table["rows"] >= 4, (section, table)

    # Une liste ouverte en vue Liste attend des lignes, pas des cartes.
    page.evaluate(_SKELETON_PROBE, "preferences")
    listed = page.evaluate(
        """async () => {
          const done = window.GrimoireWorkspace.goto('travail', { view: 'liste' });
          const shape = document.querySelector('#canvas > .skeleton')?.dataset.shape;
          await done;
          return shape;
        }"""
    )
    assert listed == "table"


def test_p60_la_fiche_d_un_projet_attend_des_tuiles(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#preferences")
    sheet = _skeleton(page, "flotte")
    assert sheet["shape"] == "kpi" and sheet["tiles"] == 4, sheet


# ── Captures pour la relecture ──────────────────────────────────────────────


def _held(page: Page, pattern: str) -> list[Route]:
    held: list[Route] = []
    page.route(pattern, lambda route: held.append(route))
    return held


def test_captures_de_relecture_p56_p58_p60(browser: Browser, served_inbox: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for scheme, word in (("light", "clair"), ("dark", "sombre")):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
        try:
            page = context.new_page()
            # Ligne active au clavier, actions révélées.
            _travail(page, served_inbox)
            page.mouse.move(1, 1)
            page.keyboard.press("j")
            page.keyboard.press("j")
            page.screenshot(path=str(out / f"p58-ligne-active-{word}.png"))
            names.append(f"p58-ligne-active-{word}")
            # Menu « … » ouvert.
            page.locator(".ex-card:focus [data-role='row-more']").click()
            page.screenshot(path=str(out / f"p56-menu-ligne-{word}.png"))
            names.append(f"p56-menu-ligne-{word}")
            page.keyboard.press("Escape")
            # Flotte : ligne active et ses actions.
            _flotte(page, served_inbox)
            page.mouse.move(1, 1)
            page.keyboard.press("j")
            page.screenshot(path=str(out / f"p58-flotte-ligne-active-{word}.png"))
            names.append(f"p58-flotte-ligne-active-{word}")
            # Squelettes : les données retenues le temps de la capture.
            for section, pattern, suffix in (
                ("travail", "**/api/workspace/tasks*", f"?project={served_inbox['home']}#preferences"),
                ("flotte", "**/api/fleet*", "?scope=all#preferences"),
            ):
                _open(page, served_inbox, suffix)
                held = _held(page, pattern)
                page.evaluate("(id) => { window.GrimoireWorkspace.goto(id); }", section)
                page.wait_for_selector("#canvas > .skeleton", timeout=30_000)
                page.mouse.move(1, 1)
                page.screenshot(path=str(out / f"p60-squelette-{section}-{word}.png"))
                names.append(f"p60-squelette-{section}-{word}")
                for route in held:
                    route.continue_()
                page.unroute(pattern)
                page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")
        finally:
            context.close()
        # Écran tactile : actions visibles sans survol.
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce", has_touch=True)
        try:
            page = context.new_page()
            context.new_cdp_session(page).send(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "hover", "value": "none"}, {"name": "pointer", "value": "coarse"}]},
            )
            _flotte(page, served_inbox)
            page.screenshot(path=str(out / f"p56-tactile-{word}.png"))
            names.append(f"p56-tactile-{word}")
        finally:
            context.close()
    assert all((out / f"{name}.png").is_file() for name in names)
