"""Lot H du tri : thème système, clavier, transitions, chiffres, contraste (P67, P58, P54, P60, P61).

Chaque test ouvre son propre contexte : le thème système, le mouvement réduit et
la mémoire du navigateur sont des réglages de contexte, pas de page.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from .test_workspace_shell import _CONTRAST_JS, SPACES

SPACE_ROUTES = ["a-traiter", "travail", "flotte", "memoire", "preferences"]


@contextmanager
def _page(browser: Browser, served: str, init_script: str | None = None, **context: Any) -> Iterator[Page]:
    options: dict[str, Any] = {"viewport": {"width": 1440, "height": 900}}
    options.update(context)
    ctx = browser.new_context(**options)
    try:
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        yield page
    finally:
        ctx.close()


def _goto(page: Page, section: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", section)
    page.wait_for_function("(id) => window.GrimoireWorkspace.section === id", arg=section)
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


# ── P67 : le thème par défaut suit le système ──────────────────────────────


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_p67_sans_choix_memorise_le_theme_suit_le_systeme(browser: Browser, served: str, scheme: str) -> None:
    with _page(browser, served, color_scheme=scheme, reduced_motion="reduce") as page:
        assert page.evaluate("() => window.GrimoireWorkspace.themePref") == "system"
        assert page.evaluate("() => window.GrimoireWorkspace.theme") == scheme


def test_p67_un_choix_explicite_reste_prioritaire_et_memorise(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="light", reduced_motion="reduce") as page:
        _goto(page, "preferences")
        page.locator("#canvas [data-theme-pref='dark']").click()
        assert page.evaluate("() => window.GrimoireWorkspace.theme") == "dark"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']")
        assert page.evaluate("() => window.GrimoireWorkspace.themePref") == "dark"
        assert page.evaluate("() => window.GrimoireWorkspace.theme") == "dark"


# ── P58 : clavier d'abord ──────────────────────────────────────────────────
# j / k / Entrée sur les lignes : test_workspace_a_traiter_p32.py
# (test_j_et_k_parcourent_les_lignes_et_entree_ouvre_l_action).


def test_p58_cmd_k_ouvre_la_palette_depuis_chaque_espace(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        for section in SPACE_ROUTES:
            _goto(page, section)
            page.keyboard.press("Control+k")
            assert page.evaluate("() => window.GrimoireWorkspace.paletteOpen") is True, section
            page.keyboard.press("Escape")
            assert page.evaluate("() => window.GrimoireWorkspace.paletteOpen") is False, section


def test_p58_le_point_d_interrogation_affiche_l_aide_puis_echap_la_ferme(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        for section in SPACE_ROUTES:
            _goto(page, section)
            page.keyboard.press("Shift+?")
            assert page.locator("#shortcuts").is_visible(), section
            keys = page.locator("#shortcuts-list dt").all_inner_texts()
            assert "⌘K" in keys and "?" in keys and "J / K" in keys, keys
            page.keyboard.press("Escape")
            assert page.locator("#shortcuts").is_hidden(), section


_PANELS = "() => [...document.querySelectorAll('.panel')].map((p) => p.dataset.state).join()"


def test_p58_aucun_raccourci_ne_s_active_pendant_une_saisie(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        page.evaluate(
            """() => {
              for (const html of ['<input id="t-in">', '<textarea id="t-ta"></textarea>',
                                  '<select id="t-se"><option>a</option></select>']) {
                document.body.insertAdjacentHTML('beforeend', html);
              }
            }"""
        )
        panels = page.evaluate(_PANELS)
        for field in ("#t-in", "#t-ta", "#t-se"):
            page.locator(field).focus()
            for key in ("Shift+?", "1", "j", "k"):
                page.keyboard.press(key)
            assert page.locator("#shortcuts").is_hidden(), field
            assert page.evaluate("() => window.GrimoireWorkspace.paletteOpen") is False, field
        assert page.evaluate(_PANELS) == panels
        # Les frappes sont allées au champ, pas à la coque.
        assert page.locator("#t-in").input_value() == "?1jk"


# ── P54 : transitions entre espaces ────────────────────────────────────────

_COUNT_VT = """
(() => {
  window.__vt = 0;
  const original = Document.prototype.startViewTransition;
  if (typeof original === 'function') {
    Document.prototype.startViewTransition = function (...args) {
      window.__vt += 1;
      return original.apply(this, args);
    };
  }
})();
"""

_NO_VT = "delete Document.prototype.startViewTransition; window.__vt = 0;"


def _cross_space(page: Page) -> None:
    _goto(page, "a-traiter")
    _goto(page, "flotte")
    assert page.evaluate("() => window.GrimoireWorkspace.space") == "flotte"


def test_p54_changer_d_espace_passe_par_start_view_transition(browser: Browser, served: str) -> None:
    with _page(browser, served, _COUNT_VT, color_scheme="dark", reduced_motion="no-preference") as page:
        assert page.evaluate("() => typeof document.startViewTransition") == "function"
        before = page.evaluate("() => window.__vt")
        _cross_space(page)
        assert page.evaluate("() => window.__vt") > before


def test_p54_sans_l_api_le_changement_d_espace_marche_sans_erreur(browser: Browser, served: str) -> None:
    errors: list[str] = []
    with _page(browser, served, _NO_VT, color_scheme="dark", reduced_motion="no-preference") as page:
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        assert page.evaluate("() => typeof document.startViewTransition") == "undefined"
        _cross_space(page)
        assert page.locator("#canvas").inner_text().strip()
    assert not errors


def test_p54_prefers_reduced_motion_desactive_la_transition(browser: Browser, served: str) -> None:
    with _page(browser, served, _COUNT_VT, color_scheme="dark", reduced_motion="reduce") as page:
        _cross_space(page)
        assert page.evaluate("() => window.__vt") == 0


# ── P60 : squelettes et chiffres animés ────────────────────────────────────


def test_p60_chaque_rubrique_pose_un_squelette_pendant_le_chargement(browser: Browser, served: str) -> None:
    probe = """async (id) => {
      const done = window.GrimoireWorkspace.goto(id);
      const canvas = document.querySelector('#canvas');
      const snap = {
        lines: canvas.querySelectorAll('.skeleton .skeleton-line').length,
        label: (canvas.querySelector('.sr-only') || {}).textContent || '',
        hidden: canvas.querySelector('.skeleton')?.getAttribute('aria-hidden'),
      };
      await done;
      return snap;
    }"""
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        for section in ("travail", "flotte", "memoire", "journal", "a-traiter"):
            snap = page.evaluate(probe, section)
            # Une section déjà active ne recharge pas : on part d'une autre.
            if snap["lines"] == 0:
                _goto(page, "journal")
                snap = page.evaluate(probe, section)
            assert snap["lines"] >= 3, (section, snap)
            assert snap["label"].startswith("Chargement"), (section, snap)
            assert snap["hidden"] == "true", (section, snap)


_KPI_PROBE = """async (label) => {
  const shell = await import('./shell.js');
  const read = (value) => {
    const row = shell.kpiRow([{ label, value }]);
    document.body.append(row);
    const val = row.querySelector('.kpi-val');
    const style = getComputedStyle(val);
    const out = {
      changed: val.dataset.changed || null,
      anim: style.animationName,
      iterations: style.animationIterationCount,
    };
    row.remove();
    return out;
  };
  return { first: read('3'), same: read('3'), moved: read('4'), again: read('4'), word: read('absent') };
}"""


def test_p60_un_chiffre_ne_s_anime_que_sur_changement_reel(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="no-preference") as page:
        result = page.evaluate(_KPI_PROBE, "Tuile P60 test")
    assert result["first"]["changed"] is None and result["first"]["anim"] == "none", "premier rendu"
    assert result["same"]["changed"] is None and result["same"]["anim"] == "none", "même valeur"
    assert result["moved"]["changed"] == "true" and result["moved"]["anim"] == "kpi-tick", "valeur changée"
    assert result["moved"]["iterations"] == "1", "une passe, jamais en boucle"
    assert result["again"]["changed"] is None, "la valeur n'a plus bougé"
    assert result["word"]["changed"] is None, "un mot d'état ne s'anime pas"


def test_p60_aucune_animation_de_chiffre_sous_prefers_reduced_motion(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        result = page.evaluate(_KPI_PROBE, "Tuile P60 réduite")
    assert result["moved"]["changed"] is None
    assert result["moved"]["anim"] == "none"


# ── P61 : densité (32 / 44 px) et contraste élevé (7:1) ────────────────────

_ROW_HEIGHT = """(density) => {
  const root = document.documentElement;
  root.dataset.density = density;
  const holder = document.createElement('div');
  holder.innerHTML = '<table class="tbl"><tbody><tr><td>Une ligne</td><td>valeur</td></tr></tbody></table>';
  document.body.append(holder);
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
    const height = holder.querySelector('tr').getBoundingClientRect().height;
    holder.remove();
    resolve(height);
  })));
}"""


def test_p61_la_densite_fait_32_ou_44_px_par_ligne(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        assert page.evaluate(_ROW_HEIGHT, "confortable") == pytest.approx(44, abs=1)
        assert page.evaluate(_ROW_HEIGHT, "compacte") == pytest.approx(32, abs=1)


def test_p61_la_densite_persiste_par_navigateur(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        _goto(page, "preferences")
        page.locator("#canvas [data-density-pref='compacte']").click()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']")
        assert page.evaluate("() => window.GrimoireWorkspace.density") == "compacte"


def test_p61_le_contraste_eleve_se_choisit_et_persiste(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce") as page:
        assert page.evaluate("() => window.GrimoireWorkspace.contrast") == "standard"
        _goto(page, "preferences")
        page.locator("#canvas [data-contrast-pref='high']").click()
        assert page.evaluate("() => window.GrimoireWorkspace.contrast") == "high"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']")
        assert page.evaluate("() => window.GrimoireWorkspace.contrast") == "high"


def test_p61_prefers_contrast_more_active_le_contraste_eleve_par_defaut(browser: Browser, served: str) -> None:
    with _page(browser, served, color_scheme="dark", reduced_motion="reduce", contrast="more") as page:
        assert page.evaluate("() => window.GrimoireWorkspace.contrast") == "high"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_p61_en_contraste_eleve_tout_texte_rendu_tient_7_pour_1(browser: Browser, served: str, theme: str) -> None:
    """Mesuré sur le DOM rendu, espace par espace, pas sur les tokens seuls."""
    with _page(browser, served, color_scheme=theme, reduced_motion="reduce") as page:
        _goto(page, "preferences")
        page.locator("#canvas [data-contrast-pref='high']").click()
        offenders: list[str] = []
        for section in SPACE_ROUTES:
            _goto(page, section)
            page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            for node in page.evaluate(_CONTRAST_JS):
                if node["ratio"] < 7.0:
                    offenders.append(f"{section}: {node['tag']}.{node['cls']} « {node['text']} » {node['ratio']:.2f}:1")
        assert not offenders, "\n  ".join(offenders[:25])
        assert len(SPACES) == 5
