"""Garde statique des surcouches natives (P55) et de la mise en page par conteneur (P59).

Le comportement se prouve dans un navigateur
(``tests/e2e/test_workspace_overlays_p55_p57_p59.py``) ; ce module garde ce qui
se lit dans les sources et qui, perdu, reviendrait sans bruit : une palette
redevenue ``<div>``, un piège de focus réécrit à la main, une règle de mise en
page d'espace qui relit la largeur de l'écran.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
INDEX = (WORKSPACE / "index.html").read_text(encoding="utf-8")
SHELL_JS = (WORKSPACE / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (WORKSPACE / "shell.css").read_text(encoding="utf-8")
GLOSSARY_JS = (WORKSPACE / "glossary.js").read_text(encoding="utf-8")
SPACE_SOURCES = {
    path.name: path.read_text(encoding="utf-8")
    for path in sorted((WORKSPACE / "spaces").iterdir())
    if path.suffix in {".js", ".css"}
}


def test_la_palette_et_l_aide_sont_des_dialogues_natifs() -> None:
    for dialog in ("palette", "shortcuts"):
        tag = re.search(rf'<(\w+)[^>]*\bid="{dialog}"[^>]*>', INDEX)
        assert tag and tag.group(1) == "dialog", dialog
        assert 'closedby="any"' in tag.group(0), f"{dialog} : le clic sur le fond doit fermer"
        assert 'role="dialog"' not in tag.group(0) and "aria-modal" not in tag.group(0), "rôle implicite du <dialog>"
    assert "$('palette').showModal()" not in SHELL_JS  # ouvert via la variable locale
    assert "palette.showModal()" in SHELL_JS and "dialog.showModal()" in SHELL_JS


def test_les_menus_et_les_infobulles_sont_des_popovers() -> None:
    assert "panel.popover = 'auto'" in SHELL_JS
    assert "toggle.popoverTargetElement = panel" in SHELL_JS
    assert "tip.popover = 'manual'" in GLOSSARY_JS and "tip.showPopover()" in GLOSSARY_JS


def test_le_focus_et_la_fermeture_maison_ont_disparu() -> None:
    """Ce que ``<dialog>`` et ``popover`` font nativement n'est plus réécrit :
    page rendue inerte, Tab intercepté, focus rendu à la main, clic à côté et
    Échap du menu et de la palette."""
    gone = {
        "setBackgroundInert": "inertie posée à la main derrière l'aide",
        ".inert =": "inertie posée à la main",
        "shortcutsReturnFocus": "focus rendu à la main par l'aide",
        "closeMenuOutside": "clic à côté du menu réimplémenté",
        "event.key === 'Tab'": "piège de Tab réimplémenté",
        "menu.toggle.focus()": "focus rendu à la main par le menu",
        "keepOpen": "menu retenu ouvert à la main",
        "$('palette').hidden": "palette pilotée par `hidden`",
        "$('shortcuts').hidden": "aide pilotée par `hidden`",
    }
    for needle, why in gone.items():
        assert needle not in SHELL_JS, why
    assert "if (event.key === 'Escape') { event.preventDefault(); closePalette(); }" not in SHELL_JS
    # La Console simulée de Source lisait l'état de la palette pour se taire ;
    # c'est désormais un vrai champ de la coque (P06) : aucun espace n'écoute
    # plus le clavier de toute la page pour elle.
    assert "onConsoleKeydown" not in SPACE_SOURCES["source.js"]
    for name, source in SPACE_SOURCES.items():
        assert "getElementById('palette').hidden" not in source, f"{name} : palette lue à `hidden`"
    menu = SHELL_JS[SHELL_JS.index("export function moreMenu"):SHELL_JS.index("// ── Actions de ligne")]
    assert "Escape" not in menu, "Échap du menu : c'est le popover qui le gère"


def test_la_mise_en_page_des_panneaux_suit_les_conteneurs() -> None:
    assert "container: middle / inline-size" in SHELL_CSS
    assert "container: canvas / inline-size" in SHELL_CSS
    assert "@container middle" in SHELL_CSS
    for name, source in SPACE_SOURCES.items():
        assert not re.search(r"@media\s*\((?:max|min)-width", source), f"{name} : règle de mise en page sur la largeur de l'écran"
    converted = [name for name, source in SPACE_SOURCES.items() if "@container canvas" in source]
    assert len(converted) >= 10, converted
    # Le seul seuil d'écran restant est celui de la coque, partagé avec shell.js.
    assert set(re.findall(r"@media \(max-width: (\d+)px\)", SHELL_CSS)) == {"900"}
    assert "DETAIL_IN_FLOW_MIN = 600" in SHELL_JS and "(min-width: 600px) and (max-width: 1179px)" in SHELL_CSS


def test_le_toast_annuler_est_une_region_polie_et_son_delai_vaut_10_s() -> None:
    region = re.search(r'<div[^>]*\bid="toast"[^>]*>', INDEX)
    assert region and 'role="status"' in region.group(0) and 'aria-live="polite"' in region.group(0)
    assert "export const UNDO_DELAY_MS = 10000;" in SHELL_JS
    assert "toastClock" in SHELL_JS, "l'horloge du toast reste injectable"
