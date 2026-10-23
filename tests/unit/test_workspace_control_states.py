"""Matrice contrôle × état sur les valeurs déclarées (#536), sans navigateur.

Pour chaque paire (contrôle, état) de la coque, la règle de ``shell.css`` est
résolue comme le navigateur le ferait — déclarations cumulées des sélecteurs
qui s'appliquent, fond du contrôle ou, à défaut, surface hôte — puis l'encre
et le fond sont mesurés dans les trois déclarations de thème de ``tokens.css``
(sombre, clair explicite, clair système) : texte ≥ 4,5:1, icône ≥ 3:1, anneau
de focus ≥ 3:1 sur chaque surface.

La même matrice, rendue et forcée état par état (survol, focus, actif…) dans
Chromium, est dans ``tests/e2e/test_workspace_control_states.py`` ; ce fichier
est la part qui tourne dans la CI de tous les jours. Les deux paires qu'elle a
trouvées défaillantes — ``select`` natif de 21 px, placeholder à 3,55:1 en
sombre — ont leur garde en bas de ce fichier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
TOKENS = (WORKSPACE / "tokens.css").read_text(encoding="utf-8")
SHELL = (WORKSPACE / "shell.css").read_text(encoding="utf-8")

THEMES = {
    "sombre": ":root {",
    "clair": ':root[data-theme="light"] {',
    "clair (système)": ':root:not([data-theme="dark"]) {',
}

MIN_TEXT = 4.5
MIN_NON_TEXT = 3.0


# ── Lecture des feuilles (même logique que test_workspace_tokens.py) ─────────


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _parse_block(text: str, header: str) -> dict[str, str]:
    text = _strip_comments(text)
    start = text.index(header)
    body = text[start + len(header) : text.index("}", start)]
    found: dict[str, str] = {}
    for line in body.splitlines():
        if ":" in line and line.strip().startswith("--"):
            name, _, value = line.strip().partition(":")
            found[name.strip()] = value.strip().rstrip(";")
    for name, value in found.items():
        alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
        while alias and alias.group(1) in found:
            value = found[alias.group(1)]
            alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
        found[name] = value
    return found


def _rgb(value: str) -> tuple[float, float, float]:
    raw = value.strip()[1:]
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(color: tuple[float, float, float]) -> float:
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _ratio(fg: str, bg: str) -> float:
    a, b = _luminance(_rgb(fg)), _luminance(_rgb(bg))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _rule(selector: str) -> dict[str, str]:
    """Déclarations cumulées des règles de shell.css dont la liste de
    sélecteurs contient exactement *selector*."""
    found: dict[str, str] = {}
    matched = False
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _strip_comments(SHELL)):
        if selector in [s.strip() for s in match.group(1).split(",")]:
            matched = True
            for decl in match.group(2).split(";"):
                name, _, value = decl.partition(":")
                if value:
                    found[name.strip()] = value.strip()
    assert matched, f"aucune règle pour {selector} dans shell.css"
    return found


def _token(value: str) -> str | None:
    found = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return found.group(1) if found else None


# ── La matrice ───────────────────────────────────────────────────────────────

#: (paire, sélecteurs cumulés dans l'ordre de la cascade, surface hôte, texte ?)
#: La surface hôte est celle où la coque monte le contrôle (`#canvas` → --ground,
#: `#docbar`/`#statusbar`/`.panel-head` → --bar, `.panel-body`/`#rail` → --panel) ;
#: elle ne sert que si le contrôle ne pose pas son propre fond.
PAIRS: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    ("btn · normal", (".btn",), "--ground", True),
    ("btn · survol", (".btn", ".btn:hover"), "--ground", True),
    ("btn · actif", (".btn", ".btn.on"), "--ground", True),
    ("btn · désactivé", (".btn", ".btn:disabled"), "--ground", True),
    ("btn.pri · normal", (".btn", ".btn.pri"), "--ground", True),
    ("btn.pri · désactivé", (".btn", ".btn.pri", ".btn.pri:disabled"), "--ground", True),
    ("chip · normal", (".chip",), "--bar", True),
    ("chip · survol", (".chip", ".chip:hover"), "--bar", True),
    ("chip · désactivé", (".chip", ".chip:disabled"), "--bar", True),
    *((f"chip.pill.{t} · normal", (".chip", f".chip.pill.{t}"), "--bar", True) for t in ("ok", "warn", "bad", "acc", "s1", "s2", "s3")),
    ("select · normal", ("select",), "--ground", True),
    ("select · désactivé", ("select", "select:disabled"), "--ground", True),
    ("input · normal", ("input",), "--ground", True),
    ("input · désactivé", ("input", "input:disabled"), "--ground", True),
    ("input · vide (placeholder)", ("input", "::placeholder"), "--ground", True),
    (".input · normal", (".input",), "--panel", True),
    ("pin · normal", (".pin",), "--bar", False),
    ("pin · actif", (".pin", '.pin[aria-pressed="true"]'), "--bar", False),
    ("tab · normal", (".tab",), "--bar", True),
    ("tab · survol", (".tab", ".tab:hover"), "--bar", True),
    ("tab · actif", (".tab", ".tab.on"), "--bar", True),
    ("seg · normal", (".seg > button",), "--panel", True),
    ("seg · actif", (".seg > button", '.seg > button[aria-pressed="true"]'), "--panel", True),
    ("seg · désactivé", (".seg > button", ".seg > button:disabled"), "--panel", True),
    ("rail-btn · normal", (".rail-btn",), "--panel", False),
    ("rail-btn · actif", (".rail-btn", '.rail-btn[aria-pressed="true"]'), "--panel", False),
    ("tree · normal", (".tree > button",), "--panel", True),
    ("tree · actif", (".tree > button", ".tree > .sel"), "--panel", True),
    ("ev-row · normal", (".ev-row",), "--panel", True),
    ("ev-row · survol", (".ev-row", ".ev-row:hover"), "--panel", True),
    ("kbd · normal", (".kbd",), "--bar", True),
    ("lbl · normal", (".lbl",), "--panel", True),
    ("empty · vide", (".empty",), "--ground", True),
    ("statusbar · normal", ("#statusbar",), "--bar", True),
    ("statusbar · erreur", ("#statusbar", '#st-live[data-state="error"]'), "--bar", True),
    ("statusbar · pause", ("#statusbar", '#st-live[data-state="paused"]'), "--bar", True),
    ("tip · normal", (".tip",), "--ground", True),
    ("tip · libellé", (".tip", ".tip .lbl"), "--ground", True),
    ("palette · sélection", ("#palette-box", '#palette-list li[aria-selected="true"]'), "--ground", True),
    ("dock · normal", ("#dock-body",), "--term", True),
)


def _resolve(selectors: tuple[str, ...], host: str) -> tuple[str, str]:
    """``(token d'encre, token de fond)`` d'une paire : la cascade cumulée, le
    fond du contrôle s'il en pose un (`background` ou `background-color` par
    token), sinon la surface hôte."""
    rule: dict[str, str] = {}
    for selector in selectors:
        rule.update(_rule(selector))
    fg = _token(rule.get("color", "")) or "--ink"
    bg = host
    for prop in ("background", "background-color"):
        value = rule.get(prop, "")
        if value in ("none", "transparent"):
            bg = host
        elif _token(value):
            bg = _token(value)  # type: ignore[assignment]
    return fg, bg


@pytest.mark.parametrize("theme,header", THEMES.items())
@pytest.mark.parametrize("pair,selectors,host,is_text", PAIRS, ids=[p[0] for p in PAIRS])
def test_chaque_paire_controle_etat_tient_son_contraste(
    theme: str, header: str, pair: str, selectors: tuple[str, ...], host: str, is_text: bool
) -> None:
    tokens = _parse_block(TOKENS, header)
    fg, bg = _resolve(selectors, host)
    floor = MIN_TEXT if is_text else MIN_NON_TEXT

    ratio = _ratio(tokens[fg], tokens[bg])

    assert ratio >= floor, f"{pair} en thème {theme} : {fg} sur {bg} = {ratio:.2f}:1 < {floor}"


# ── Focus visible : un seul anneau, lisible sur chaque surface ───────────────


@pytest.mark.parametrize("theme,header", THEMES.items())
@pytest.mark.parametrize("surface", ("--ground", "--panel", "--card", "--overlay", "--bar"))
def test_l_anneau_de_focus_se_voit_sur_chaque_surface(theme: str, header: str, surface: str) -> None:
    """`:focus-visible` pose `outline: 2px solid var(--acc)` pour tout contrôle ;
    l'anneau est un indicateur non textuel : 3:1 (WCAG 1.4.11) sur la surface
    qui l'entoure, dans chaque thème."""
    rule = _rule(":focus-visible")
    outline = rule["outline"].split()
    tokens = _parse_block(TOKENS, header)

    assert float(outline[0].removesuffix("px")) >= 1 and outline[1] != "none"
    assert _token(outline[2]) == "--acc", "l'anneau est l'accent, pas une teinte propre"
    assert _ratio(tokens["--acc"], tokens[surface]) >= MIN_NON_TEXT, f"--acc sur {surface} en thème {theme}"


def test_le_champ_focalise_change_sa_bordure_en_plus_de_l_anneau() -> None:
    assert _token(_rule(".input:focus-within")["border-color"]) == "--acc"


# ── Les deux paires trouvées défaillantes par la mesure rendue ───────────────


def test_le_select_natif_atteint_la_cible_minimale() -> None:
    """Rendu à 21 px de haut avant #536 ; 24 px est la cible minimale (WCAG 2.5.8)."""
    assert float(_rule("select")["min-height"].removesuffix("px")) >= 24


def test_le_placeholder_porte_une_encre_du_theme_sans_fondu() -> None:
    """L'encre par défaut du navigateur tombait à 3,55:1 sur --card en sombre."""
    rule = _rule("::placeholder")

    assert _token(rule["color"]) == "--ink3"
    assert rule["opacity"] == "1", "une opacité < 1 ferait retomber --ink3 sous 4,5:1"
