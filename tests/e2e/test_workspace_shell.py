"""Ce qui ne se prouve que dans un navigateur : la coque rendue.

Critères 1 à 4 et 8 de la spécification. Chaque test décrit ce qu'il empêche.

Ce module est le harnais que les cinq lots étendent :

- lot 1 y ajoute les trois états de panneau au clavier, les fontes embarquées et
  les captures avant/après par écran ;
- lot 2 la pile d'infobulles à trois niveaux ;
- lots 3 à 5 un test d'ouverture par écran réel, avec leurs données.

Il ne teste pas le contenu des espaces : au moment où il est écrit, ils sont des
stubs. Il teste que la coque les ouvre, que les tokens tiennent, et que les
mécaniques répondent.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

SPACES = ["piloter", "concevoir", "executer", "observer", "memoire", "source"]

#: Le contraste minimal exigé par la spec §2.2 et le critère §6.2.
MIN_RATIO = 4.5

#: Mesure du contraste dans la page : c'est le rendu qui compte, pas la valeur
#: déclarée — un `opacity` ou un fond hérité peut trahir un token conforme.
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
    const text = [...el.childNodes]
      .filter((n) => n.nodeType === 3 && n.textContent.trim())
      .map((n) => n.textContent.trim()).join('');
    if (!text) continue;
    const size = parseFloat(style.fontSize);
    const fg = parse(style.color);
    const bg = backdrop(el);
    const a = lum(fg), b = lum(bg);
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: el.className && el.className.toString ? el.className.toString() : '',
      text: text.slice(0, 40),
      size,
      ratio,
    });
  }
  return out;
}
"""

#: Un glyphe de touche n'est pas du texte courant : la spec l'autorise plus petit.
_FLOOR_EXEMPT = ("kbd",)


def _rendered(page: Page) -> list[dict]:
    return page.evaluate(_CONTRAST_JS)


def _apply(page: Page, *, theme: str | None = None, density: str | None = None) -> None:
    """Change le thème ou la densité, PUIS attend que le rendu ait suivi.

    Chromium ne recalcule pas les styles dans le même tour de boucle que la
    mutation d'attribut : ``getComputedStyle`` rend alors l'ancienne couleur.
    Mesurer sans attendre donnait des encres sombres sur des surfaces claires —
    un échec fabriqué par le harnais, exactement le genre de faux signal qui
    fait perdre confiance dans un test de contraste. Deux trames suffisent, et
    on les attend explicitement plutôt que de dormir une durée arbitraire.
    """
    page.evaluate(
        """([t, d]) => {
            const root = document.documentElement;
            if (t) root.dataset.theme = t;
            if (d) root.dataset.density = d;
            return new Promise((resolve) =>
              requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""",
        [theme, density],
    )


# ── Critère 1 : les six espaces s'ouvrent sur un projet réel ────────────────


@pytest.mark.parametrize("space", SPACES)
def test_chaque_espace_s_ouvre_sur_un_projet_reel(workspace: Page, space: str) -> None:
    """Le premier test que la spec demande : six espaces, aucune donnée de démo.

    Il échoue si un module d'espace lève, si son import casse, ou si la coque
    n'affiche plus l'onglet actif — donc il couvre le contrat `mount(root, ctx)`
    pour les cinq lots à la fois.
    """
    workspace.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    workspace.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)

    assert workspace.locator(f'[data-space="{space}"][aria-selected="true"]').count() == 1
    assert workspace.locator("#canvas").inner_text().strip(), f"l'espace {space} rend une toile vide"
    # Aucun écran ne doit annoncer de la donnée de démonstration (spec §5).
    assert "démo" not in workspace.locator("#canvas").inner_text().lower()


def test_la_coque_annonce_les_six_espaces_et_pas_un_de_plus(workspace: Page) -> None:
    assert workspace.evaluate("() => window.GrimoireWorkspace.spaces") == SPACES
    assert workspace.locator("#spaces .tab").count() == 6


def test_aucune_erreur_de_console_a_l_amorcage(browser, served: str) -> None:
    """La revue §4.4 a trouvé « un mur de zéros plus une erreur JS silencieuse ».

    Une erreur avalée est une erreur qui reviendra sous forme d'écran vide sans
    explication.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    for space in SPACES:
        page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
        page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    context.close()

    assert not errors, "erreurs de console :\n  " + "\n  ".join(errors)


# ── Critère 2 : plancher typographique et contraste, mesurés sur le DOM ─────


@pytest.mark.parametrize("theme,floor", [("dark", 13.0), ("light", 12.0)])
def test_aucun_texte_rendu_sous_le_plancher(workspace: Page, theme: str, floor: float) -> None:
    """13 px en sombre, 12 px en clair. Mesuré après rendu, pas dans la feuille.

    La revue a compté 162 éléments sous 10,5 px sur une seule page : le défaut
    ne vient jamais d'une règle assumée, il vient d'un `font-size` hérité que
    personne ne relit.
    """
    _apply(workspace, theme=theme)
    offenders = [
        node
        for node in _rendered(workspace)
        if node["size"] < floor and not any(x in node["cls"] for x in _FLOOR_EXEMPT)
    ]

    assert not offenders, "\n  ".join(f"{n['tag']}.{n['cls']} « {n['text']} » → {n['size']}px" for n in offenders[:20])


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_aucune_encre_rendue_sous_45(workspace: Page, theme: str) -> None:
    """Le contraste se mesure sur ce que l'œil reçoit, pas sur le token déclaré."""
    _apply(workspace, theme=theme)
    offenders = [n for n in _rendered(workspace) if n["ratio"] < MIN_RATIO]

    assert not offenders, "\n  ".join(
        f"{n['tag']}.{n['cls']} « {n['text']} » → {n['ratio']:.2f}:1" for n in offenders[:20]
    )


@pytest.mark.parametrize("density", ["decouverte", "concentration"])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_les_six_espaces_s_ouvrent_dans_les_deux_themes_et_les_deux_densites(
    workspace: Page, theme: str, density: str
) -> None:
    """Critère 1, dans ses quatre combinaisons."""
    _apply(workspace, theme=theme, density=density)
    for space in SPACES:
        workspace.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
        workspace.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
        assert workspace.locator("#canvas").inner_text().strip()


# ── Critère 3 : panneaux, raccourcis, palette, concentration ───────────────


def test_les_trois_etats_de_panneau_repondent(workspace: Page) -> None:
    """Replié, entrouvert, épinglé — et `peek` ne redimensionne pas la grille."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'pinned')")
    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "pinned"
    pinned_width = workspace.locator("#center").bounding_box()["width"]

    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")
    assert workspace.locator("#panel-explorer").is_hidden()
    collapsed_width = workspace.locator("#center").bounding_box()["width"]
    assert collapsed_width > pinned_width, "un panneau replié doit rendre sa place"

    workspace.evaluate(f"() => {api}.setPanel('explorer', 'peek')")
    assert workspace.locator("#panel-explorer").is_visible()
    assert workspace.locator("#center").bounding_box()["width"] == collapsed_width, (
        "entrouvert est une surimpression : la grille ne bouge pas"
    )


def test_le_clic_sur_le_rail_ouvre_en_surimpression(workspace: Page) -> None:
    """« Clic sur l'icône du rail : ouvre en surimpression » (spec §3.1) —
    pas besoin d'épingler pour un coup d'œil, et la grille ne bouge pas."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")
    before_width = workspace.locator("#center").bounding_box()["width"]

    workspace.locator('.rail-btn[data-panel="explorer"]').click()

    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "peek"
    assert workspace.locator("#panel-explorer").is_visible()
    assert workspace.locator("#center").bounding_box()["width"] == before_width, (
        "l'ouverture par clic est une surimpression : la grille ne bouge pas"
    )


def test_le_survol_du_rail_450ms_entrouvre(workspace: Page) -> None:
    """Le délai de la spec §3.1, mesuré : rien avant, entrouvert après."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")

    workspace.locator('.rail-btn[data-panel="explorer"]').hover()
    workspace.wait_for_timeout(200)
    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "collapsed"

    workspace.wait_for_timeout(400)
    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "peek"


def test_le_survol_du_contenu_n_ouvre_jamais_un_panneau(workspace: Page) -> None:
    """« Jamais au survol du contenu » (spec §3.1) : traverser la toile,
    largement plus longtemps que le délai du rail, n'ouvre rien."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")
    workspace.evaluate(f"() => {api}.setPanel('inspector', 'collapsed')")

    workspace.locator("#canvas").hover()
    workspace.wait_for_timeout(700)

    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "collapsed"
    assert workspace.evaluate(f"() => {api}.panelState('inspector')") == "collapsed"


def test_le_cadenas_epingle_dans_la_grille(workspace: Page) -> None:
    """« Cadenas … épingle dans la grille, le contenu se redimensionne »."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")
    collapsed_width = workspace.locator("#center").bounding_box()["width"]
    # Le cadenas vit dans l'en-tête du panneau : il n'est cliquable qu'une
    # fois le panneau visible — entrouvert, ici, puisque c'est le clic sur le
    # rail qui l'amène à l'écran.
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'peek')")

    workspace.locator('[data-pin="explorer"]').click()

    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "pinned"
    assert workspace.locator("#center").bounding_box()["width"] < collapsed_width, (
        "épingler dans la grille doit rendre la toile plus étroite"
    )


def test_le_cmd_clic_sur_le_rail_epingle_sans_passer_par_l_entrouvert(workspace: Page) -> None:
    """« … ou ⌘ + clic : épingle dans la grille » (spec §3.1)."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('inspector', 'collapsed')")

    workspace.locator('.rail-btn[data-panel="inspector"]').click(modifiers=["Control"])

    assert workspace.evaluate(f"() => {api}.panelState('inspector')") == "pinned"


def test_la_poignee_redimensionne_le_panneau_epingle(workspace: Page) -> None:
    """« le contenu se redimensionne » : la poignée tire la largeur à la souris."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'pinned')")
    box = workspace.locator("#panel-explorer").bounding_box()

    workspace.mouse.move(box["x"] + box["width"] - 1, box["y"] + 40)
    workspace.mouse.down()
    workspace.mouse.move(box["x"] + box["width"] + 80, box["y"] + 40, steps=5)
    workspace.mouse.up()

    new_box = workspace.locator("#panel-explorer").bounding_box()
    assert new_box["width"] > box["width"] + 40, "la poignée doit élargir le panneau"


@pytest.mark.parametrize("key,panel", [("1", "explorer"), ("4", "inspector")])
def test_les_raccourcis_de_panneau_basculent(workspace: Page, key: str, panel: str) -> None:
    before = workspace.evaluate("(p) => window.GrimoireWorkspace.panelState(p)", panel)
    workspace.locator("body").press(key)
    after = workspace.evaluate("(p) => window.GrimoireWorkspace.panelState(p)", panel)

    assert before != after


def test_le_mode_concentration_replie_tout(workspace: Page) -> None:
    """⇧⌘F : la toile prend l'écran. Sans ça, le mode n'est qu'un libellé."""
    workspace.evaluate("() => window.GrimoireWorkspace.setPanel('explorer', 'pinned')")
    before = workspace.locator("#canvas").bounding_box()

    workspace.locator("body").press("Shift+ControlOrMeta+f")

    assert workspace.evaluate("() => window.GrimoireWorkspace.focus") == "on"
    assert workspace.locator("#canvas").bounding_box()["width"] > before["width"]


def test_la_palette_se_navigue_au_clavier(workspace: Page) -> None:
    """Flèches, Entrée, Échap — le clavier complet que la spec §3.3 exige."""
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")
    workspace.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 1")

    first = workspace.locator("#palette-list li[aria-selected='true']").inner_text()
    workspace.locator("body").press("ArrowDown")
    second_selected = workspace.locator("#palette-list li[aria-selected='true']")
    assert second_selected.count() == 1
    assert second_selected.inner_text() != first, "la flèche bas doit déplacer la sélection"

    workspace.locator("body").press("ArrowUp")
    assert workspace.locator("#palette-list li[aria-selected='true']").inner_text() == first, (
        "la flèche haut doit revenir à l'entrée précédente"
    )
    workspace.locator("body").press("Escape")


def test_la_palette_execute_l_entree_selectionnee_a_l_entree(workspace: Page) -> None:
    """Entrée déclenche exactement ce que le clavier vient de mettre en évidence."""
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")
    workspace.locator("#palette-input").fill("Observer")
    workspace.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 0")

    workspace.locator("body").press("Enter")

    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'observer'")
    assert workspace.evaluate("() => window.GrimoireWorkspace.paletteOpen") is False


def test_la_palette_s_ouvre_au_clavier_et_montre_les_commandes(workspace: Page) -> None:
    """Chaque entrée montre sa commande `grimoire …` (spec §3.3) : c'est ainsi
    que le novice apprend le clavier sans qu'on le lui impose."""
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")

    assert workspace.evaluate("() => window.GrimoireWorkspace.paletteOpen") is True
    workspace.locator("#palette-input").fill("task")
    workspace.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 0")
    assert workspace.locator("#palette-list .cmd").first.inner_text().startswith("grimoire")

    workspace.locator("body").press("Escape")
    assert workspace.evaluate("() => window.GrimoireWorkspace.paletteOpen") is False


def test_la_palette_atteint_les_fichiers_de_source(workspace: Page) -> None:
    """spec §3.3 : la palette atteint aussi les fichiers de Source — un projet
    réel a toujours des fichiers d'étage kit (`_grimoire/kit/agents/…`)."""
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")
    workspace.locator("#palette-input").fill("_grimoire/kit/agents")
    workspace.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 0")

    hints = workspace.locator("#palette-list .lbl").all_inner_texts()
    assert any(hint.startswith("Fichier") for hint in hints)

    workspace.locator("body").press("Escape")


def test_choisir_un_fichier_dans_la_palette_l_ouvre_dans_source(workspace: Page) -> None:
    """Reste connu de l'intégration des cinq lots : le test précédent prouve
    que la palette *liste* les fichiers, mais son `run()` ne faisait que
    `goto('source')` — `shell.js` écrasait `location.hash` avant que
    `source.js` n'ait vu de chemin, et aucun fichier ne s'ouvrait jamais.
    `goto(id, { file })` répare le relais (shell.js, spaces/source.js)."""
    workspace.locator("body").press("ControlOrMeta+k")
    workspace.wait_for_selector("#palette:not([hidden])")
    workspace.locator("#palette-input").fill("_grimoire/kit/agents")
    workspace.wait_for_function("() => document.querySelectorAll('#palette-list li').length > 0")

    chosen_path = workspace.locator("#palette-list li").first.locator("span").first.inner_text()
    workspace.locator("body").press("Enter")

    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'source'")
    workspace.wait_for_selector(".sr-docrow .mono", timeout=10_000)
    assert workspace.locator(".sr-docrow .mono").inner_text() == chosen_path


def test_le_theme_et_la_densite_se_choisissent_et_survivent_au_rechargement(workspace: Page, served: str) -> None:
    """L'état est mémorisé par projet, côté client (spec §3.1)."""
    workspace.locator("#st-theme").click()
    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "light"

    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")

    assert workspace.evaluate("() => window.GrimoireWorkspace.theme") == "light"


def test_la_densite_se_choisit_et_survit_au_rechargement(workspace: Page, served: str) -> None:
    """Le second réglage de la spec §3.4, mémorisé comme le thème."""
    workspace.locator("#st-density").click()
    assert workspace.evaluate("() => window.GrimoireWorkspace.density") == "concentration"

    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")

    assert workspace.evaluate("() => window.GrimoireWorkspace.density") == "concentration"


def test_l_etat_des_panneaux_est_memorise_par_espace_et_survit_au_rechargement(workspace: Page, served: str) -> None:
    """« mémorisé par espace de travail et par projet » (spec §3.1) : deux
    espaces gardent chacun leur propre état de panneau, y compris après recharge."""
    api = "window.GrimoireWorkspace"
    workspace.evaluate(f"(id) => {api}.goto(id)", "piloter")
    workspace.wait_for_function(f"(id) => {api}.space === id", arg="piloter")
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'collapsed')")

    workspace.evaluate(f"(id) => {api}.goto(id)", "concevoir")
    workspace.wait_for_function(f"(id) => {api}.space === id", arg="concevoir")
    workspace.evaluate(f"() => {api}.setPanel('explorer', 'pinned')")

    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    # Le hash de l'URL a survécu à la recharge : on est de retour sur concevoir.
    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "pinned"

    workspace.evaluate(f"(id) => {api}.goto(id)", "piloter")
    workspace.wait_for_function(f"(id) => {api}.space === id", arg="piloter")
    assert workspace.evaluate(f"() => {api}.panelState('explorer')") == "collapsed", (
        "l'état de piloter ne doit pas avoir été écrasé par celui de concevoir"
    )


# ── Critère 4 : les infobulles viennent du glossaire ────────────────────────


def test_aucun_terme_du_dom_n_est_absent_du_glossaire(workspace: Page) -> None:
    """Le pendant dynamique de tests/unit/test_workspace_glossary.py.

    Il voit les termes qu'un module d'espace pose à l'exécution, que l'analyse
    statique du HTML ne peut pas connaître.
    """
    for space in SPACES:
        workspace.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
        workspace.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
        missing = workspace.evaluate("() => window.GrimoireWorkspace.glossary.missingTerms()")
        assert not missing, f"espace {space} : termes sans entrée → {missing}"


def test_le_glossaire_est_charge_depuis_l_api(workspace: Page) -> None:
    assert workspace.evaluate("() => window.GrimoireWorkspace.glossary.size()") >= 15


def test_une_bulle_s_ouvre_se_fige_et_la_pile_se_ferme(workspace: Page) -> None:
    """Survol, Alt, Échap — la mécanique que la spec décrit en §3.2."""
    workspace.evaluate(
        "() => window.GrimoireWorkspace.glossary.open('porte-de-preuve', document.getElementById('brand'), 0, true)"
    )
    assert workspace.locator(".tip").count() == 1
    assert workspace.locator(".tip .t").inner_text() == "Porte de preuve"
    assert workspace.locator(".tip .term").count() >= 1, "les termes liés ouvrent les bulles enfants"

    workspace.locator(".tip .term").first.click()
    assert workspace.locator(".tip").count() == 2, "trois niveaux au plus, deux ici"

    workspace.locator("body").press("Escape")
    assert workspace.locator(".tip").count() == 0


#: `#project-chip` porte `data-term="projet"` en dur dans index.html (lot 1) —
#: un vrai bouton, toujours visible, sans mécanique de survol concurrente.
#: `#zoom-seg` est le seul `data-term` du squelette qui n'est *pas* un élément
#: nativement focusable : il vérifie que `glossary.attach()` lui donne un
#: `tabindex`, pas seulement les boutons qui n'en avaient pas besoin.
_HOVER_ANCHOR = "#project-chip"
_NON_FOCUSABLE_ANCHOR = "#zoom-seg"


def test_le_survol_ouvre_une_bulle_apres_le_delai(workspace: Page) -> None:
    """Un vrai survol, pas l'appel direct à `glossary.open()` des tests ci-dessus.

    Sans le délai, une bulle s'ouvrirait à chaque passage de souris — c'est le
    défaut que 500 ms existe pour éviter.
    """
    workspace.hover(_HOVER_ANCHOR)
    assert workspace.locator(".tip").count() == 0, "la bulle ne doit pas s'ouvrir avant le délai"

    workspace.wait_for_selector(".tip", timeout=2_000)
    assert workspace.locator(".tip").count() == 1
    assert workspace.locator(".tip .t").inner_text() == "Projet"
    assert workspace.locator(".tip .lbl").first.inner_text() == "Alt · épingler"


def test_alt_fige_la_bulle_et_le_pointeur_peut_y_entrer(workspace: Page) -> None:
    """Alt : le cadenas apparaît, et survoler la bulle elle-même ne la ferme pas."""
    workspace.hover(_HOVER_ANCHOR)
    workspace.wait_for_selector(".tip", timeout=2_000)

    workspace.locator("body").press("Alt")
    assert workspace.locator(".tip .lbl").first.inner_text() == "épinglée"
    assert workspace.locator(".tip svg.ico").count() == 1, "le cadenas de la maquette « Encre »"
    assert workspace.locator(".tip button[aria-label^='Fermer']").count() == 1

    # Le pointeur quitte l'ancre pour entrer dans la bulle : elle doit survivre.
    box = workspace.locator(".tip").bounding_box()
    workspace.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    workspace.wait_for_timeout(50)
    assert workspace.locator(".tip").count() == 1, "une bulle épinglée survit à l'entrée du pointeur"


def test_la_croix_ferme_la_bulle_epinglee(workspace: Page) -> None:
    """Le remède documenté par le README : la croix, ou Échap."""
    workspace.evaluate(
        "() => window.GrimoireWorkspace.glossary.open('porte-de-preuve', document.getElementById('brand'), 0, true)"
    )
    assert workspace.locator(".tip").count() == 1

    workspace.locator(".tip button[aria-label^='Fermer']").click()
    assert workspace.locator(".tip").count() == 0


def test_clic_ailleurs_ferme_les_bulles_non_epinglees_laisse_les_epinglees(workspace: Page) -> None:
    workspace.hover(_HOVER_ANCHOR)
    workspace.wait_for_selector(".tip", timeout=2_000)
    assert workspace.locator(".tip .lbl").first.inner_text() == "Alt · épingler"

    # Loin du coin haut-gauche où s'ancrent `#project-chip` et `#brand` : le
    # clic ne doit pas atterrir sur la bulle elle-même, sans quoi ce ne
    # serait plus « ailleurs ».
    workspace.mouse.click(700, 850)
    assert workspace.locator(".tip").count() == 0, "un clic ailleurs ferme une bulle non épinglée"

    workspace.evaluate(
        "() => window.GrimoireWorkspace.glossary.open('porte-de-preuve', document.getElementById('brand'), 0, true)"
    )
    workspace.mouse.click(700, 850)
    assert workspace.locator(".tip").count() == 1, "une bulle épinglée ne se ferme pas par un clic ailleurs"


def test_un_quatrieme_niveau_est_refuse(workspace: Page) -> None:
    """`porte-de-preuve` → `evidence-pack` → `tache` : trois bulles, pas de quatrième bouton."""
    api = "window.GrimoireWorkspace.glossary"
    workspace.evaluate(f"() => {api}.open('porte-de-preuve', document.getElementById('brand'), 0, true)")
    workspace.locator('.tip[data-term="porte-de-preuve"] .term[data-term="evidence-pack"]').click()
    workspace.locator('.tip[data-term="evidence-pack"] .term[data-term="tache"]').click()

    assert workspace.locator(".tip").count() == 3, "trois niveaux : porte, evidence pack, tâche"
    assert workspace.locator('.tip[data-term="tache"] .term').count() == 0, "le troisième niveau n'ouvre plus rien"

    # Et refusé même en contournant l'interface : `open()` lui-même le refuse.
    fourth = workspace.evaluate(f"() => {api}.open('porte-de-preuve', document.getElementById('brand'), 3, true)")
    assert fourth is None
    assert workspace.locator(".tip").count() == 3, "aucune quatrième bulle n'a été créée"


@pytest.mark.parametrize("stack_depth", [1, 2, 3])
def test_echap_ferme_toute_la_pile_quelle_que_soit_sa_hauteur(workspace: Page, stack_depth: int) -> None:
    api = "window.GrimoireWorkspace.glossary"
    workspace.evaluate(f"() => {api}.open('porte-de-preuve', document.getElementById('brand'), 0, true)")
    if stack_depth >= 2:
        workspace.locator('.tip[data-term="porte-de-preuve"] .term[data-term="evidence-pack"]').click()
    if stack_depth >= 3:
        workspace.locator('.tip[data-term="evidence-pack"] .term[data-term="tache"]').click()
    assert workspace.locator(".tip").count() == stack_depth

    workspace.locator("body").press("Escape")
    assert workspace.locator(".tip").count() == 0


def test_densite_concentration_reduit_la_bulle_et_attend_800ms(workspace: Page) -> None:
    """Spec §3.4 : nom et raccourci seulement, 800 ms. Alt rouvre la définition.

    `[data-panel="inspector"]` cite `inspecteur`, qui a un raccourci (« 4 ») —
    contrairement à `#project-chip`, la bulle réduite a donc quelque chose à
    montrer *et* quelque chose à taire.
    """
    _apply(workspace, density="concentration")

    workspace.hover('[data-panel="inspector"]')
    workspace.wait_for_timeout(600)
    assert workspace.locator(".tip").count() == 0, "800 ms en Concentration, pas 500"

    workspace.wait_for_selector(".tip", timeout=2_000)
    reduced = workspace.locator(".tip").inner_text()
    assert "Inspecteur" in reduced
    assert workspace.locator(".tip .kbd").inner_text() == "4"
    assert "propriétés" not in reduced, "réduite : pas de définition tant qu'elle n'est pas épinglée"

    workspace.locator("body").press("Alt")
    expanded = workspace.locator(".tip").inner_text()
    assert "propriétés" in expanded, "Alt rouvre la définition"


def test_navigation_clavier_le_focus_ouvre_sans_delai_et_alt_epingle(workspace: Page) -> None:
    """Critère d'accessibilité : Tab atteint tout `[data-term]`, y compris un
    élément qui n'est pas nativement focusable, et `aria-describedby` le lie
    à sa bulle.

    Les touches passent par ``page.keyboard`` et non par
    ``locator("body").press()`` : ce dernier focus d'abord `<body>`, ce qui
    aurait déclenché un `focusout` sur l'ancre et fermé la bulle avant même
    qu'Alt ne soit lu — exactement le défaut qu'un test au clavier doit
    surprendre, pas contourner.
    """
    assert workspace.evaluate(f"() => document.querySelector('{_NON_FOCUSABLE_ANCHOR}').tabIndex") == 0, (
        "ensureFocusable doit rendre le segment de zoom atteignable au clavier"
    )

    workspace.locator(_NON_FOCUSABLE_ANCHOR).focus()
    workspace.wait_for_selector(".tip", timeout=1_000)
    assert workspace.locator(".tip .t").inner_text() == "Niveau de zoom"

    described_by = workspace.evaluate(
        f"() => document.querySelector('{_NON_FOCUSABLE_ANCHOR}').getAttribute('aria-describedby')"
    )
    assert described_by
    assert workspace.locator(f"#{described_by}").count() == 1

    workspace.keyboard.press("Alt")
    assert workspace.locator(".tip .lbl").first.inner_text() == "épinglée"

    workspace.keyboard.press("Escape")
    assert workspace.locator(".tip").count() == 0
    assert (
        workspace.evaluate(f"() => document.querySelector('{_NON_FOCUSABLE_ANCHOR}').hasAttribute('aria-describedby')")
        is False
    )


# ── Critère 8 : la même coque, deux hôtes ──────────────────────────────────


def test_la_coque_sait_quel_hote_la_sert(workspace: Page) -> None:
    """`grimoire serve` est un alias déprécié de `cockpit serve` depuis #351 :
    un seul binaire répond désormais des deux côtés, `status.host` vaut donc
    toujours `cockpit`. Ce qui distingue encore un lancement direct — celui de
    cette fixture, `grimoire serve --project-root <projet>` — d'une simple
    navigation cockpit vers un projet du registre, c'est `readOnly` : le
    projet qu'on sert en direct n'est pas en lecture seule, lui (#356). Le
    front lit ce champ, pas une supposition sur le port."""
    host = workspace.evaluate(
        "() => ({ kind: window.GrimoireWorkspace.host.kind, ro: window.GrimoireWorkspace.host.readOnly })"
    )

    assert host["kind"] == "cockpit"
    assert host["ro"] is False
