"""UI-2 du cockpit : responsive, états vides actionnables, chargement, accessibilité, thème.

Ce qui se prouve ici ne se prouve pas par la lecture des feuilles : à 390 px, la
coque gardait ses deux panneaux latéraux épinglés et le centre tombait à 0 px de
large. ``document.documentElement.scrollWidth`` valait pourtant 390 : ``#shell``
est en ``overflow: hidden``, donc la mesure « pas de défilement horizontal de la
page » ne voit rien. Les tests ci-dessous mesurent ce qui trahit le défaut — la
largeur utile du centre, le contenu de la toile, les bords des barres de la coque.

Chaque test dit ce qu'il empêche.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

SPACES = ["piloter", "concevoir", "executer", "observer", "memoire", "source"]

PHONE = {"width": 390, "height": 844}

_MIN_RATIO = 4.5

#: Les mesures de débordement. Un élément de la coque qui dépasse le bord droit
#: n'est toléré que s'il vit dans un conteneur qui défile exprès (``overflow-x:
#: auto|scroll``) : ``hidden`` masquerait justement ce qu'on cherche.
_OVERFLOW_JS = """
() => {
  const iw = window.innerWidth;
  const scrolls = (node) => {
    for (let n = node.parentElement; n && n !== document.body; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  };
  const offenders = [];
  for (const el of document.querySelectorAll('#appbar *, #docbar *, #statusbar *, #rail *')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const box = el.getBoundingClientRect();
    if (!box.width) continue;
    if (box.right > iw + 1 && !scrolls(el)) {
      offenders.push((el.id || String(el.className.baseVal ?? el.className) || el.tagName) + ':' + Math.round(box.right));
    }
  }
  const canvas = document.getElementById('canvas');
  return {
    iw,
    doc: document.documentElement.scrollWidth,
    center: document.getElementById('center').getBoundingClientRect().width,
    canvasScroll: canvas.scrollWidth,
    canvasClient: canvas.clientWidth,
    offenders,
  };
}
"""

#: Contraste calculé sur le rendu (même méthode que test_workspace_shell.py).
_CONTRAST_JS = """
() => {
  const parse = (value) => {
    const n = value.match(/[\\d.]+/g).map(Number);
    return [n[0] / 255, n[1] / 255, n[2] / 255, n.length > 3 ? n[3] : 1];
  };
  const lum = ([r, g, b]) => {
    const f = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const backdrop = (el) => {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0.9) return bg;
    }
    return parse(getComputedStyle(document.body).backgroundColor);
  };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (el.closest('#dock-body')) continue;  // terminal : encre propre, mesurée ailleurs
    const text = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim())
      .map((n) => n.textContent.trim()).join('');
    if (!text) continue;
    const a = lum(parse(style.color)), b = lum(backdrop(el));
    out.push({ text: text.slice(0, 40), cls: String(el.className), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) });
  }
  return out;
}
"""


@pytest.fixture
def phone(browser: Browser, served: str) -> Iterator[Page]:
    """La coque à 390 px de large, prête."""
    context = browser.new_context(viewport=PHONE, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _settle(page: Page) -> None:
    """Attend que l'espace ait fini de se monter."""
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => document.getElementById('canvas').getAttribute('aria-busy') !== 'true'")
    page.wait_for_timeout(250)


# ── Responsive ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("space", SPACES)
def test_a_390_px_le_centre_garde_sa_largeur_et_rien_ne_deborde(phone: Page, space: str) -> None:
    """Sous 900 px la coque tient en une colonne : le centre occupe la largeur de
    l'écran, la toile ne défile pas à l'horizontale, aucune barre ne déborde.

    Défaut d'origine : explorateur (236 px) et inspecteur (300 px) épinglés
    laissaient 0 px au centre, et la barre d'application poussait ses onglets à
    820 px. ``scrollWidth`` du document, lui, restait à 390 (``overflow:
    hidden``) : c'est pourquoi la mesure porte sur le centre et la toile.
    """
    phone.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    phone.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    _settle(phone)

    measured = phone.evaluate(_OVERFLOW_JS)

    assert measured["doc"] <= measured["iw"], measured
    assert measured["center"] >= measured["iw"] - 2, f"centre écrasé : {measured}"
    assert measured["canvasScroll"] <= measured["canvasClient"] + 1, f"la toile défile à l'horizontale : {measured}"
    assert not measured["offenders"], f"éléments hors écran : {measured['offenders'][:8]}"


def test_a_390_px_les_panneaux_sont_fermes_au_chargement(phone: Page) -> None:
    """Un tiroir ouvert d'office masque le contenu : on l'ouvre, on ne le subit pas."""
    for panel in ("explorer", "inspector", "evidence"):
        assert phone.evaluate("(p) => window.GrimoireWorkspace.panelState(p)", panel) == "collapsed", panel


def test_les_tiroirs_s_ouvrent_et_se_ferment_au_clavier(phone: Page) -> None:
    """Le bouton du rail ouvre le tiroir à Entrée, le focus y entre, Échap le
    referme et rend le focus au bouton du rail ; un seul tiroir à la fois."""
    rail = phone.locator('.rail-btn[data-panel="explorer"]')
    rail.focus()
    phone.keyboard.press("Enter")
    explorer = phone.locator("#panel-explorer")
    explorer.wait_for(state="visible")

    box = explorer.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"] + 1
    assert phone.evaluate("() => document.getElementById('center').getBoundingClientRect().width") >= PHONE["width"] - 2, (
        "le tiroir doit recouvrir le centre, pas le comprimer"
    )
    assert phone.evaluate("() => document.getElementById('panel-explorer').contains(document.activeElement)"), (
        "le focus doit entrer dans le tiroir ouvert"
    )

    phone.keyboard.press("Escape")
    explorer.wait_for(state="hidden")
    assert phone.evaluate("() => document.activeElement?.dataset?.panel") == "explorer"

    phone.locator('.rail-btn[data-panel="inspector"]').focus()
    phone.keyboard.press("Enter")
    phone.locator("#panel-inspector").wait_for(state="visible")
    phone.locator('.rail-btn[data-panel="explorer"]').click()
    phone.locator("#panel-explorer").wait_for(state="visible")
    assert phone.locator("#panel-inspector").is_hidden(), "un seul tiroir à la fois"


_FOCUS_ON_RAIL_JS = "() => document.activeElement?.matches('.rail-btn[data-panel=\"inspector\"]') ?? false"


def _open_inspector_with_keyboard(page: Page) -> None:
    page.locator('.rail-btn[data-panel="inspector"]').focus()
    page.keyboard.press("Enter")
    page.locator("#panel-inspector").wait_for(state="visible")
    assert page.evaluate("() => document.activeElement?.classList.contains('panel-close')"), (
        "le focus doit être entré dans le tiroir"
    )


def test_un_tiroir_se_ferme_d_un_appui_hors_de_lui(phone: Page) -> None:
    """Un appui sur le voile referme le tiroir ET rend le focus au bouton du rail
    qui l'avait ouvert, comme Échap et « Fermer ».

    Défaut d'origine : le listener ``pointerdown`` fermait le panneau sans passer
    par ``leaveDrawer()`` : le focus, resté dans le tiroir devenu masqué,
    retombait sur ``<body>`` et ``drawerOpener`` gardait une référence périmée.
    """
    _open_inspector_with_keyboard(phone)
    # Le voile qui recouvre la toile reçoit l'appui (c'est lui que la personne touche).
    phone.mouse.click(10, 300)
    phone.locator("#panel-inspector").wait_for(state="hidden")
    assert phone.evaluate(_FOCUS_ON_RAIL_JS), phone.evaluate(
        "() => [document.activeElement?.tagName, document.activeElement?.className]"
    )
    assert phone.locator('.rail-btn[data-panel="inspector"]').get_attribute("aria-expanded") == "false"


def test_un_tiroir_se_ferme_d_un_appui_tactile_hors_de_lui_et_rend_le_focus(browser: Browser, served: str) -> None:
    """Même garde au toucher. L'appui doit tomber HORS du tiroir (x = 10) : le
    tiroir de droite occupe de 46,8 à 390 px, un appui à 370 px est dedans."""
    context = browser.new_context(viewport=PHONE, reduced_motion="reduce", has_touch=True)
    try:
        page = context.new_page()
        page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _open_inspector_with_keyboard(page)
        page.touchscreen.tap(10, 300)
        page.locator("#panel-inspector").wait_for(state="hidden")
        assert page.evaluate(_FOCUS_ON_RAIL_JS), page.evaluate(
            "() => [document.activeElement?.tagName, document.activeElement?.className]"
        )
    finally:
        context.close()


def test_a_1440_px_les_panneaux_restent_en_colonnes(workspace: Page) -> None:
    """Le bureau ne change pas : explorateur et inspecteur épinglés à côté du centre."""
    assert workspace.evaluate("() => window.GrimoireWorkspace.panelState('explorer')") == "pinned"
    widths = workspace.evaluate(
        "() => ['panel-explorer', 'center', 'panel-inspector'].map((i) => document.getElementById(i).getBoundingClientRect().width)"
    )
    assert widths[0] > 200 and widths[2] > 250 and widths[1] > 600, widths


# ── Onglets ─────────────────────────────────────────────────────────────────


def test_les_espaces_forment_un_tablist_navigable_aux_fleches(workspace: Page) -> None:
    """``role="tablist"``, ``aria-selected`` sur le courant, un seul onglet dans
    l'ordre de tabulation, flèches gauche/droite (avec rebouclage), Début/Fin ;
    l'activation reste manuelle (Entrée) car monter un espace coûte des requêtes."""
    assert workspace.locator('#spaces[role="tablist"]').count() == 1
    tabs = workspace.locator('#spaces [role="tab"]')
    assert tabs.count() == 6

    focusable = workspace.evaluate("() => [...document.querySelectorAll('#spaces [role=\"tab\"]')].map((t) => t.tabIndex)")
    assert focusable.count(0) == 1 and focusable.count(-1) == 5, focusable
    selected = workspace.evaluate("() => document.querySelector('#spaces [aria-selected=\"true\"]').dataset.space")
    assert workspace.evaluate("() => document.querySelector('#spaces [tabindex=\"0\"]').dataset.space") == selected

    workspace.locator(f'#spaces [data-space="{selected}"]').focus()
    workspace.keyboard.press("ArrowRight")
    after_right = workspace.evaluate("() => document.activeElement.dataset.space")
    assert after_right == SPACES[(SPACES.index(selected) + 1) % 6]
    workspace.keyboard.press("End")
    assert workspace.evaluate("() => document.activeElement.dataset.space") == SPACES[-1]
    workspace.keyboard.press("ArrowRight")
    assert workspace.evaluate("() => document.activeElement.dataset.space") == SPACES[0], "rebouclage"
    workspace.keyboard.press("ArrowLeft")
    assert workspace.evaluate("() => document.activeElement.dataset.space") == SPACES[-1]
    workspace.keyboard.press("Home")
    assert workspace.evaluate("() => document.activeElement.dataset.space") == SPACES[0]

    # Le focus déplacé n'a pas changé d'espace ; Entrée le fait.
    assert workspace.evaluate("() => window.GrimoireWorkspace.space") == selected
    target = SPACES[2] if selected != SPACES[2] else SPACES[3]
    workspace.locator(f'#spaces [data-space="{target}"]').focus()
    workspace.keyboard.press("Enter")
    workspace.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=target)
    assert workspace.evaluate(
        "() => document.querySelector('#spaces [aria-selected=\"true\"]').dataset.space"
    ) == target
    assert workspace.evaluate("() => document.querySelector('#spaces [tabindex=\"0\"]').dataset.space") == target


def test_la_toile_est_le_tabpanel_de_l_espace_courant(workspace: Page) -> None:
    labelled = workspace.evaluate(
        "() => { const c = document.getElementById('canvas'); const t = document.getElementById(c.getAttribute('aria-labelledby')); "
        "return { role: c.getAttribute('role'), tab: t && t.dataset.space }; }"
    )
    assert labelled["role"] == "tabpanel"
    assert labelled["tab"] == workspace.evaluate("() => window.GrimoireWorkspace.space")


def test_les_onglets_du_dock_s_activent_aux_fleches(workspace: Page) -> None:
    """Dans le dock, la flèche active l'onglet (le contenu est local, sans requête)."""
    assert workspace.locator('#dock-tabs[role="tablist"]').count() == 1
    current = workspace.evaluate("() => document.querySelector('[data-dock-tab][aria-selected=\"true\"]').dataset.dockTab")
    workspace.locator(f'[data-dock-tab="{current}"]').focus()
    workspace.keyboard.press("ArrowRight")
    nxt = workspace.evaluate("() => document.activeElement.dataset.dockTab")
    assert nxt and nxt != current
    assert workspace.evaluate("(id) => document.querySelector(`[data-dock-tab=\"${id}\"]`).getAttribute('aria-selected')", nxt) == "true"
    assert workspace.evaluate("() => document.querySelectorAll('[data-dock-tab][aria-selected=\"true\"]').length") == 1
    assert workspace.evaluate("() => document.getElementById('dock-body').getAttribute('aria-labelledby')") is not None


# ── Thème ───────────────────────────────────────────────────────────────────


def _pick_theme(page: Page, pref: str) -> None:
    page.locator(f'#st-theme [data-theme-pref="{pref}"]').click()


def test_le_theme_est_un_selecteur_a_trois_etats_avec_icone(workspace: Page) -> None:
    group = workspace.locator("#st-theme")
    assert group.get_attribute("role") == "group"
    buttons = group.locator("[data-theme-pref]")
    assert buttons.count() == 3
    names = [buttons.nth(i).get_attribute("aria-label") for i in range(3)]
    assert sorted(n.lower() for n in names if n) == ["clair", "sombre", "système"], names
    for i in range(3):
        assert buttons.nth(i).locator("svg use").count() == 1, "chaque état porte son icône"
    assert group.locator('[aria-pressed="true"]').count() == 1


def test_le_theme_clair_ou_sombre_se_memorise(workspace: Page) -> None:
    _pick_theme(workspace, "light")
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "light"
    assert workspace.locator('#st-theme [data-theme-pref="light"]').get_attribute("aria-pressed") == "true"
    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    assert workspace.evaluate("() => window.GrimoireWorkspace.themePref") == "light"
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "light"
    _pick_theme(workspace, "dark")
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "dark"


def test_le_theme_systeme_suit_le_reglage_du_systeme_en_direct(workspace: Page) -> None:
    workspace.emulate_media(color_scheme="dark")
    _pick_theme(workspace, "system")
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "dark"
    workspace.emulate_media(color_scheme="light")
    workspace.wait_for_function("() => window.GrimoireWorkspace.theme === 'light'")
    assert workspace.evaluate("() => window.GrimoireWorkspace.themePref") == "system"

    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    assert workspace.evaluate("() => window.GrimoireWorkspace.themePref") == "system"
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "light"
    assert workspace.locator('#st-theme [data-theme-pref="system"]').get_attribute("aria-pressed") == "true"


@pytest.mark.parametrize("pref", ["dark", "light"])
def test_a_390_px_aucune_encre_sous_45_avec_un_tiroir_ouvert(phone: Page, pref: str) -> None:
    """Contraste mesuré sur le rendu, tiroir ouvert, dans les deux thèmes."""
    _pick_theme(phone, pref)
    phone.locator('.rail-btn[data-panel="explorer"]').click()
    phone.locator("#panel-explorer").wait_for(state="visible")
    _settle(phone)
    low = [m for m in phone.evaluate(_CONTRAST_JS) if m["ratio"] < _MIN_RATIO and "kbd" not in m["cls"]]
    assert not low, f"{pref} : {[(m['text'], round(m['ratio'], 2)) for m in low[:6]]}"


# ── Chargement ──────────────────────────────────────────────────────────────

#: Retarde toute requête de l'API : le temps de voir l'état de chargement.
_SLOW_FETCH = """
(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.includes('/api/workspace/')) return real(input, init);
    return new Promise((resolve) => setTimeout(resolve, 1200)).then(() => real(input, init));
  };
})();
"""


def test_un_espace_qui_charge_montre_un_squelette_annonce_aux_lecteurs_d_ecran(browser: Browser, served: str) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.add_init_script(_SLOW_FETCH)
        # Pas d'attente de `data-ready` : il n'est posé qu'une fois l'espace monté,
        # donc après la disparition du squelette que ce test veut voir.
        page.goto(f"{served}/workspace/index.html#executer", wait_until="domcontentloaded")
        page.wait_for_selector("#canvas .skeleton", timeout=15_000)
        assert page.locator("#canvas").get_attribute("aria-busy") == "true"
        assert page.locator("#canvas .skeleton").first.get_attribute("aria-hidden") == "true"
        assert "Chargement" in page.locator("#canvas").inner_text()
        page.wait_for_function("() => !document.querySelector('#canvas .skeleton')", timeout=30_000)
        assert page.locator("#canvas").get_attribute("aria-busy") == "false"
    finally:
        context.close()


def test_les_zones_de_donnees_sont_des_regions_polies(workspace: Page) -> None:
    for region in ("canvas", "explorer-body", "inspector-body", "evidence-body"):
        assert workspace.locator(f"#{region}").get_attribute("aria-live") == "polite", region


# ── États vides ─────────────────────────────────────────────────────────────


def test_un_explorateur_vide_dit_pourquoi_et_propose_ou_aller(workspace: Page) -> None:
    """Cinq espaces sur six ne remplissent pas l'explorateur : il restait un
    cadre muet. Il explique, et un bouton mène à Source, où vit l'arborescence."""
    workspace.evaluate("() => window.GrimoireWorkspace.goto('observer')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'observer'")
    _settle(workspace)
    body = workspace.locator("#explorer-body")
    assert body.inner_text().strip(), "l'explorateur ne doit pas rester muet"
    button = body.get_by_role("button", name="Ouvrir Source")
    button.click()
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'source'")


def test_le_diagnostic_non_lance_se_lance_d_un_bouton(workspace: Page) -> None:
    """« diagnostic indisponible » était affiché à chaque fois, sans cause : la
    fiche ne calcule jamais le diagnostic au premier rendu (il coûte ~350 ms).
    Le bloc le dit et offre le geste qui le calcule."""
    workspace.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
    _settle(workspace)
    inspector = workspace.locator("#inspector-body")
    assert "diagnostic indisponible" not in inspector.inner_text()
    inspector.get_by_role("button", name="Lancer le diagnostic").click()
    workspace.wait_for_function(
        "() => /doctor (conforme|en écart)/.test(document.getElementById('inspector-body').innerText)",
        timeout=60_000,
    )


def test_un_etat_vide_dont_la_commande_est_ouverte_a_la_console_la_lance_d_un_bouton(workspace: Page) -> None:
    """La commande n'est plus seulement à recopier : le bouton l'exécute dans la
    Console du dock et la sortie y apparaît.

    Le bloc est construit par la fabrique de la coque (``GrimoireWorkspace.empty``,
    la même que ``ctx.empty``) : quel espace tombe dans l'état vide dépend des
    données du projet, la règle testée ici n'en dépend pas.
    """
    page = workspace
    page.evaluate(
        "() => document.getElementById('canvas').replaceChildren("
        "window.GrimoireWorkspace.empty('Mémoire', 'Rien à montrer.', 'grimoire memory status'))"
    )
    button = page.locator("#canvas .empty [data-empty-action]")
    assert button.inner_text() == "Lancer dans la Console"
    button.click()
    page.wait_for_function(
        "() => document.querySelector('[data-dock-tab=\"console\"]').getAttribute('aria-selected') === 'true'"
    )
    page.wait_for_function(
        "() => document.getElementById('dock-body').innerText.includes('$ grimoire memory status')", timeout=30_000
    )
    # La sortie de la commande suit (ou son refus, dit en clair) : jamais un bouton muet.
    page.wait_for_function(
        "() => document.getElementById('dock-body').innerText.split('\\n').length > 2", timeout=30_000
    )
    assert "refusé" not in page.locator("#dock-body").inner_text()


def test_un_etat_vide_dont_la_commande_demande_un_argument_n_a_pas_de_faux_bouton(untouched_workspace: Page) -> None:
    """Pas de bouton quand la commande ne peut pas s'exécuter telle quelle
    (`<id>` à fournir) : un bouton qui échouerait mentirait autant qu'un lien mort.

    Projet que rien n'écrit : Observer n'est vide que sans ledger, hook ni run (P07)."""
    page = untouched_workspace
    page.evaluate("() => window.GrimoireWorkspace.goto('observer')")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'observer'")
    _settle(page)
    empty = page.locator("#canvas .empty").first
    empty.wait_for(state="visible", timeout=10_000)
    assert "<" in empty.locator("code").inner_text()
    assert empty.locator("[data-empty-action]").count() == 0


def test_api_injoignable_propose_de_reessayer(browser: Browser, served: str) -> None:
    """Le bloc « API injoignable » était écrit puis aussitôt effacé par le
    premier ``goto()`` : il reste, avec le geste qui relance."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.route("**/api/**", lambda route: route.abort())
        page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        retry = page.locator("#canvas .empty").get_by_role("button", name="Réessayer")
        retry.wait_for(state="visible")
        assert "injoignable" in page.locator("#canvas .empty").first.inner_text().lower()
        page.unroute("**/api/**")
        retry.click()
        page.wait_for_function("() => document.getElementById('project-dot').classList.contains('ok')", timeout=30_000)
    finally:
        context.close()
