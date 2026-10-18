"""Garde statique de la coque responsive et accessible (UI-2).

Le comportement se prouve dans un navigateur (``tests/e2e/
test_workspace_responsive_a11y.py``) ; ce module ne garde que ce qui se lit dans
les sources et qui, perdu, ne se verrait qu'à la prochaine capture : le seuil de
largeur, la structure des onglets, le sélecteur de thème, les zones annoncées.
Même méthode que ``test_workspace_appbar_buttons.py`` : l'analyse porte sur le
source, pas sur un rendu.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
INDEX = (WORKSPACE / "index.html").read_text(encoding="utf-8")
SHELL_CSS = (WORKSPACE / "shell.css").read_text(encoding="utf-8")
SHELL_JS = (WORKSPACE / "shell.js").read_text(encoding="utf-8")


def test_la_feuille_de_la_coque_declare_son_seuil_de_largeur_et_ses_tiroirs() -> None:
    """Avant UI-2 ni ``shell.css`` ni ``tokens.css`` n'avaient de ``@media`` de
    largeur : à 390 px, deux panneaux épinglés laissaient 0 px au centre."""
    assert "@media (max-width: 900px)" in SHELL_CSS
    assert "#middle[data-drawer]" in SHELL_CSS, "le voile du tiroir a disparu"
    assert ".panel-close" in SHELL_CSS


def test_le_seuil_de_la_feuille_et_celui_du_script_sont_les_memes() -> None:
    """Un tiroir est une affaire de CSS (disposition) ET de JS (focus, Échap,
    un seul à la fois) : deux seuils différents donnent une coque à moitié mobile."""
    css = set(re.findall(r"@media \(max-width: (\d+)px\)", SHELL_CSS))
    js = set(re.findall(r"matchMedia\('\(max-width: (\d+)px\)'\)", SHELL_JS))
    assert css == js == {"900"}, (css, js)


def test_la_barre_d_application_n_a_plus_de_largeur_fixe_en_ligne() -> None:
    """Le bouton de la palette portait ``style="width:250px"`` : un style en ligne
    ne se surcharge pas par une feuille, donc la barre ne pouvait pas se replier."""
    assert not re.search(r'style="[^"]*\bwidth\s*:\s*\d+px', INDEX)


def test_le_selecteur_de_theme_a_trois_etats_chacun_avec_icone_et_nom() -> None:
    group = re.search(r'<div[^>]*id="st-theme"[^>]*>(.*?)</div>', INDEX, re.DOTALL)
    assert group, "#st-theme n'est plus un groupe de boutons"
    buttons = re.findall(r"<button\b[^>]*>.*?</button>", group.group(1), re.DOTALL)
    assert len(buttons) == 3
    prefs = [re.search(r'data-theme-pref="([a-z]+)"', b).group(1) for b in buttons]  # type: ignore[union-attr]
    assert prefs == ["system", "light", "dark"]
    for button in buttons:
        assert 'aria-label="' in button and "<use " in button, button


def test_les_onglets_des_espaces_sont_un_tablist_dans_une_nav() -> None:
    assert re.search(r'<nav[^>]*aria-label="Espaces de travail"[^>]*>\s*<div id="spaces" role="tablist"', INDEX)
    assert 'role="tablist"' in INDEX and "bindTablist(nav, { activate: 'manual' })" in SHELL_JS
    assert "bindTablist(tabs, { activate: 'auto' })" in SHELL_JS
    assert "button.role = " not in SHELL_JS, "role par propriété IDL : fragile selon le navigateur, utiliser setAttribute"


def test_les_zones_de_donnees_sont_des_regions_polies() -> None:
    for zone in ("canvas", "explorer-body", "inspector-body", "evidence-body"):
        tag = re.search(rf'<[^>]*id="{zone}"[^>]*>', INDEX)
        assert tag and 'aria-live="polite"' in tag.group(0), zone
