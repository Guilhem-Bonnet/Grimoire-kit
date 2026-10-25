"""La coque aux maquettes (P62) : une barre, le contenu, un panneau de détail.

Les maquettes validées (StyleDouce et les cinq écrans) ne montrent ni rail
d'icônes, ni explorateur, ni dock Console/Traces : une barre du haut, le
contenu et, selon l'écran, un panneau de détail à droite. Ces tests tiennent la
coque à ce dessin tout en gardant les panneaux d'atelier — ils servent au
diagnostic, ils sont seulement fermés par défaut.

Ce que chaque test prouve :

- à 1440x900, chaque espace n'affiche que la barre du haut, le contenu et, si
  l'écran y met quelque chose, le panneau de détail ;
- la bascule (⇧⌘L, palette ⌘K, bouton du pied) rouvre rail, explorateur,
  inspecteur et dock, et le choix survit au rechargement ;
- le panneau de détail suit ``ctx.inspector`` : vide, il n'y a pas de colonne ;
- le style Douce est rendu : Instrument Sans (vendorisée, aucune requête
  tierce), rayons 10 / 16 / pilule, onglets en pilule, titre de 32 px.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les cinq
espaces (clair, 1440x900) pour la comparaison aux artboards ; sans la variable
il ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout
skip en échec).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page

from .conftest import open_atelier

SPACES = ("a-traiter", "travail", "flotte", "savoir", "configuration")
ATELIER = ("#rail", "#panel-evidence", "#dock")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=30_000)


def _visible(page: Page, selector: str) -> bool:
    return page.locator(selector).is_visible()


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


@pytest.mark.parametrize("space", SPACES)
def test_chaque_espace_n_affiche_que_la_barre_le_contenu_et_le_detail(
    cockpit_workspace: Page, space: str
) -> None:
    page = cockpit_workspace
    _goto(page, space)

    assert page.evaluate("document.documentElement.dataset.atelier") == "off"
    assert _visible(page, "#appbar")
    assert _visible(page, "#canvas")
    for selector in ATELIER:
        assert not _visible(page, selector), f"{selector} doit être masqué par défaut dans {space}"
    # L'arbre de Fichiers vit dans le contenu de sa rubrique (P66) : l'explorateur
    # n'est plus jamais là hors atelier.
    assert not _visible(page, "#panel-explorer")
    # Le panneau de détail n'est là que si l'écran y a mis du contenu.
    detail = page.locator("#panel-inspector").get_attribute("data-detail")
    assert _visible(page, "#panel-inspector") == (detail == "on")
    # Rien ne déborde : pas de défilement horizontal de la page.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_la_barre_du_haut_porte_la_marque_la_portee_les_cinq_espaces_et_la_recherche(
    cockpit_workspace: Page,
) -> None:
    page = cockpit_workspace
    assert page.locator("#brand").inner_text().replace("\n", "").strip() == "›Grimoire"
    assert _visible(page, "#project-chip")
    assert page.locator("#spaces [role='tab']").count() == 5
    assert _visible(page, "#top-live")
    assert "Rechercher ou agir" in page.locator("#palette-open").inner_text()


def test_la_bascule_rouvre_les_panneaux_d_atelier_et_se_souvient(cockpit_workspace: Page) -> None:
    page = cockpit_workspace
    _goto(page, "travail")

    page.keyboard.press("Control+Shift+L")
    page.wait_for_function("document.documentElement.dataset.atelier === 'on'")
    for selector in ("#rail", "#panel-explorer", "#dock", "#panel-inspector"):
        assert _visible(page, selector), f"{selector} doit se rouvrir avec la bascule"
    assert page.locator("#st-atelier").get_attribute("aria-pressed") == "true"

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    assert page.evaluate("document.documentElement.dataset.atelier") == "on"
    assert _visible(page, "#rail")

    page.locator("#st-atelier").click()
    page.wait_for_function("document.documentElement.dataset.atelier === 'off'")
    assert not _visible(page, "#rail")
    assert not _visible(page, "#dock")


def test_la_palette_propose_d_afficher_les_panneaux_d_atelier(cockpit_workspace: Page) -> None:
    page = cockpit_workspace
    page.keyboard.press("Control+k")
    page.fill("#palette-input", "panneaux d'atelier")
    entry = page.locator("#palette-list li[role='option']", has_text="Afficher les panneaux d'atelier")
    assert entry.count() == 1
    entry.click()
    page.wait_for_function("document.documentElement.dataset.atelier === 'on'")

    page.keyboard.press("Control+k")
    page.fill("#palette-input", "panneaux d'atelier")
    assert page.locator("#palette-list li[role='option']", has_text="Masquer les panneaux d'atelier").count() == 1


def test_le_panneau_de_detail_suit_le_contenu_de_ctx_inspector(cockpit_workspace: Page) -> None:
    page = cockpit_workspace
    _goto(page, "configuration")
    page.evaluate("window.GrimoireWorkspace.goto('preferences')")
    page.wait_for_function("window.GrimoireWorkspace.section === 'preferences'")
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=30_000)
    page.evaluate("document.getElementById('inspector-body').replaceChildren()")
    page.wait_for_function("document.getElementById('panel-inspector').dataset.detail === 'off'")
    assert not _visible(page, "#panel-inspector")

    # Une phrase « choisissez un élément » n'est pas un détail.
    page.evaluate(
        "const p = document.createElement('p'); p.className = 'lbl'; p.textContent = 'Sélectionnez une tâche';"
        "document.getElementById('inspector-body').replaceChildren(p)"
    )
    page.wait_for_timeout(100)
    assert not _visible(page, "#panel-inspector")

    page.evaluate(
        "const h = document.createElement('h3'); h.textContent = 'Détail';"
        "document.getElementById('inspector-body').replaceChildren(h)"
    )
    page.wait_for_function("document.getElementById('panel-inspector').dataset.detail === 'on'")
    assert _visible(page, "#panel-inspector")
    box = page.locator("#panel-inspector").bounding_box()
    assert box is not None and 360 <= box["width"] <= 390
    # Carte arrondie à ombre douce, pas un panneau collé au bord.
    style = page.evaluate(
        "(() => { const s = getComputedStyle(document.getElementById('panel-inspector'));"
        " return { radius: s.borderTopLeftRadius, shadow: s.boxShadow }; })()"
    )
    assert style["radius"] == "20px"
    assert style["shadow"] != "none"


def test_le_style_douce_est_rendu(cockpit_workspace: Page) -> None:
    page = cockpit_workspace
    _goto(page, "a-traiter")
    page.evaluate("document.fonts.ready")

    assert page.evaluate("getComputedStyle(document.body).fontFamily").startswith('"Instrument Sans"')
    assert page.evaluate("document.fonts.check('16px \"Instrument Sans\"')")

    root = page.evaluate(
        "(() => { const s = getComputedStyle(document.documentElement);"
        " return ['--r', '--r-l', '--r-pill'].map((k) => s.getPropertyValue(k).trim()); })()"
    )
    assert root == ["10px", "16px", "999px"]

    tab = page.locator("#spaces [role='tab'][aria-selected='true']")
    tab_style = tab.evaluate(
        "(el) => { const s = getComputedStyle(el); return { radius: s.borderTopLeftRadius, color: s.color,"
        " bg: s.backgroundColor, icon: el.querySelector('svg use').getAttribute('href') }; }"
    )
    assert float(tab_style["radius"].removesuffix("px")) >= 100, "onglet en pilule"
    assert tab_style["icon"] == "icons.svg#i-space-a-traiter"
    acc = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()")
    soft = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--acc-soft').trim()")
    assert tab_style["color"] == _rgb(page, acc) and tab_style["bg"] == _rgb(page, soft)

    title = page.locator(".space-title").first
    assert title.evaluate("(el) => getComputedStyle(el).fontSize") == "32px"
    kpi = page.locator(".kpi-item").first
    if kpi.count():
        assert kpi.evaluate("(el) => getComputedStyle(el).borderTopLeftRadius") == "16px"


def _rgb(page: Page, color: str) -> str:
    """Une valeur de token (#hex) telle que le navigateur la rend en `rgb()`."""
    return page.evaluate(
        "(c) => { const d = document.createElement('div'); d.style.color = c; document.body.append(d);"
        " const v = getComputedStyle(d).color; d.remove(); return v; }",
        color,
    )


def test_aucune_requete_tierce_pour_les_polices(cockpit_workspace: Page) -> None:
    """P05 : la coque se sert elle-même, polices comprises."""
    page = cockpit_workspace
    seen: list[str] = []
    page.on("request", lambda request: seen.append(request.url))
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("document.fonts.ready")

    local = urlparse(page.url).netloc
    foreign = [url for url in seen if urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc != local]
    assert not foreign, f"requêtes hors de la coque : {foreign}"
    assert any(url.endswith("InstrumentSans-Variable.woff2") for url in seen), "la police vendorisée est chargée"


def test_captures_des_cinq_espaces(cockpit_workspace: Page) -> None:
    """Preuve d'interface : clair, 1440x900, aucune assertion sans la variable."""
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    page = cockpit_workspace
    page.locator("#st-theme [data-theme-pref='light']").click()
    for space in SPACES:
        _goto(page, space)
        page.wait_for_timeout(600)
        _capture(page, f"p62-{space}-clair")
    open_atelier(page)
    _goto(page, "travail")
    _capture(page, "p62-travail-atelier-clair")
