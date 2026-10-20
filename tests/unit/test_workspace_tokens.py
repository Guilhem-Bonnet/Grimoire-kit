"""Les tokens sont la seule source de couleur, et ils tiennent le contraste.

Critère 2 de la spécification : « aucun texte rendu sous 13 px en sombre, 12 px
en clair ; aucune encre sous 4,5:1 sur ``--e1`` ; aucune couleur hors tokens.
Mesuré par un test. »

Ce module mesure ce qui se mesure sans navigateur — le fichier de tokens et
l'absence de littéraux ailleurs. La mesure sur le DOM rendu, dans les deux
thèmes et les deux densités, est dans ``tests/e2e/test_workspace_shell.py`` :
elle a besoin d'un moteur de rendu, et elle ne remplace pas celle-ci, qui tourne
dans la CI de tous les jours.

Le défaut que ces tests ferment est documenté : la revue de septembre 2026 a
trouvé 25 tailles de police rendues sur l'observatoire, 162 éléments sous
10,5 px sur la page mémoire, 36 styles en échec de contraste, et un
``kanban.html`` qui recopiait les valeurs des tokens dans un ``:root`` local.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
TOKENS = WORKSPACE / "tokens.css"

#: Le seul fichier autorisé à contenir une couleur littérale.
COLOR_HOME = "tokens.css"

#: Littéraux de couleur : hexadécimal, fonctions rgb/hsl, et les mots-clés que
#: l'on peut écrire par réflexe. `transparent`, `currentColor`, `inherit` et
#: `none` ne sont pas des couleurs choisies : elles n'engagent aucune palette.
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|color|oklch|lab)\s*\(", re.IGNORECASE)
_NAMED = re.compile(
    r"(?<![\w-])(?:white|black|red|green|blue|orange|grey|gray|silver|"
    r"yellow|purple|teal|cyan|magenta|navy|olive|maroon|lime|aqua|fuchsia)"
    r"(?![\w-])",
    re.IGNORECASE,
)

#: Les ombres portées de la spec §2.1 sont des noirs transparents nommés par un
#: token (`--shadow`, `--shadow-tip`) ; hors de tokens.css on ne doit voir que
#: le token.
_STYLED = ("*.css", "*.js", "*.html")


def _sources() -> list[Path]:
    found: list[Path] = []
    for pattern in _STYLED:
        found.extend(sorted(WORKSPACE.rglob(pattern)))
    return [p for p in found if p.name != COLOR_HOME]


def test_la_coque_existe_et_porte_son_fichier_de_tokens() -> None:
    assert TOKENS.is_file(), "sans tokens.css, la règle « une seule source » n'a pas d'objet"
    assert (WORKSPACE / "index.html").is_file()


@pytest.mark.parametrize("source", _sources(), ids=lambda p: str(p.relative_to(WORKSPACE)))
def test_aucune_couleur_litterale_hors_du_fichier_de_tokens(source: Path) -> None:
    """Une valeur recopiée est une valeur qui divergera.

    `kanban.html` l'a prouvé : il redéclarait les tokens dans un `:root` local,
    avec des noms différents, et toute refonte de la palette le laissait
    derrière.
    """
    text = source.read_text(encoding="utf-8")
    offenders: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("*", "//", "<!--", "#:")):
            continue
        for pattern in (_HEX, _FUNC, _NAMED):
            match = pattern.search(line)
            if match:
                offenders.append(f"{source.name}:{line_number} — {stripped[:90]}")
                break

    assert not offenders, (
        "couleur littérale hors de tokens.css :\n  " + "\n  ".join(offenders)
    )


# ── Le plancher typographique ───────────────────────────────────────────────

_FONT_SIZE = re.compile(r"font-size:\s*([0-9.]+)px")


def test_aucune_taille_de_police_sous_le_plancher() -> None:
    """13 px en sombre, 12 px en clair, avec une seule exception nommée.

    Le `.kbd` est un glyphe de touche, pas du texte courant : 11 px y est
    lisible parce que le contenu est un caractère unique. Toute autre valeur
    sous le plancher est un défaut — la revue en a compté 270 sur une seule
    page.
    """
    exceptions = {".kbd", "#palette-list .cmd"}
    offenders: list[str] = []
    for source in [TOKENS, *(_sources())]:
        if source.suffix != ".css":
            continue
        selector = ""
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if "{" in line and not line.strip().startswith(("@", "/*")):
                selector = line.split("{")[0].strip()
            match = _FONT_SIZE.search(line)
            if match and float(match.group(1)) < 12 and selector not in exceptions:
                offenders.append(f"{source.name}:{line_number} — {selector} → {match.group(1)}px")

    assert not offenders, "taille sous le plancher :\n  " + "\n  ".join(offenders)


def test_le_plancher_est_declare_dans_les_deux_themes() -> None:
    """Le plancher n'est pas la même valeur en sombre et en clair (spec §2.3)."""
    text = TOKENS.read_text(encoding="utf-8")

    assert "--t-min: 13px" in text, "plancher sombre"
    assert text.count("--t-min: 12px") >= 2, "plancher clair, dans les deux déclarations du thème"


# ── Le contraste, calculé sur les valeurs déclarées ─────────────────────────


def _strip_comments(text: str) -> str:
    """Retire les commentaires CSS avant toute lecture de déclaration.

    Sans ça, une phrase de commentaire qui commence par ``--bar`` est lue comme
    une déclaration et écrase la vraie valeur : le test mesure alors un
    contraste qui n'existe nulle part et accuse une palette saine.
    """
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
    # Un alias `var(--x)` vaut la valeur de --x dans le même bloc : sans cette
    # résolution, `--bg: var(--ground)` serait mesuré comme une chaîne.
    for name, value in found.items():
        alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
        while alias and alias.group(1) in found:
            value = found[alias.group(1)]
            alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
        found[name] = value
    return found


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.strip()
    if value.startswith("#"):
        raw = value[1:]
        if len(raw) == 3:
            raw = "".join(c * 2 for c in raw)
        return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    numbers = [float(n) for n in re.findall(r"[0-9.]+", value)[:3]]
    return (numbers[0] / 255, numbers[1] / 255, numbers[2] / 255)


def _luminance(color: tuple[float, float, float]) -> float:
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _ratio(fg: str, bg: str) -> float:
    a, b = _luminance(_rgb(fg)), _luminance(_rgb(bg))
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


THEMES = {
    "sombre": ":root {",
    "clair": ':root[data-theme="light"] {',
}


#: Les surfaces qui portent réellement du texte en encre secondaire : les
#: panneaux (`--e1`) et les barres (`--bar`, barre d'état et écho du dock). La
#: spec ne nomme que `--e1` ; le harnais Playwright, qui mesure sur le DOM, a
#: montré que `--bar` est la surface la plus exigeante — d'où sa présence ici,
#: pour que le défaut se voie sans navigateur.
INK_SURFACES = ("--e1", "--bar")


@pytest.mark.parametrize("surface", INK_SURFACES)
@pytest.mark.parametrize("theme,header", THEMES.items())
@pytest.mark.parametrize("ink", ["--ink", "--ink2", "--ink3"])
def test_chaque_encre_tient_45_sur_les_surfaces_de_texte(
    theme: str, header: str, ink: str, surface: str
) -> None:
    """`--ink3` est le cas limite : la spec l'annonce à 4,8 et 4,6 mesurés.

    Si quelqu'un l'éclaircit « pour la douceur », ce test le dit avant que la
    revue ne recompte 36 styles en échec.
    """
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens[ink], tokens[surface])

    assert ratio >= 4.5, f"{ink} sur {surface} en thème {theme} : {ratio:.2f}:1"


# ── Colorisation de l'éditeur Source (#280) ─────────────────────────────────

SYNTAX_TOKENS = (
    "--syn-key", "--syn-string", "--syn-tag", "--syn-placeholder",
    "--syn-path", "--syn-ident", "--syn-comment",
)


@pytest.mark.parametrize("theme,header", THEMES.items())
@pytest.mark.parametrize("token", SYNTAX_TOKENS)
def test_chaque_token_de_syntaxe_tient_45_sur_le_fond_de_l_editeur(
    theme: str, header: str, token: str
) -> None:
    """Les tokens de colorisation ajoutés pour l'IntelliSense de Source
    (#280) posent leur encre sur ``--bg`` — la toile de l'éditeur, pas un
    panneau — et doivent tenir le même seuil que le reste de l'interface."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens[token], tokens["--bg"])

    assert ratio >= 4.5, f"{token} sur --bg en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", THEMES.items())
def test_le_texte_de_l_action_primaire_tient_sur_son_fond(theme: str, header: str) -> None:
    """Une action primaire par écran : elle ne peut pas être celle qu'on ne lit pas."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens["--onpri"], tokens["--pri"])

    assert ratio >= 4.5, f"--onpri sur --pri en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", THEMES.items())
def test_l_encre_du_terminal_tient_sur_le_terminal(theme: str, header: str) -> None:
    """Le terminal reste sombre dans les deux thèmes : son encre doit suivre."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens["--termink"], tokens["--term"])

    assert ratio >= 4.5, f"--termink sur --term en thème {theme} : {ratio:.2f}:1"


def test_les_deux_themes_declarent_exactement_les_memes_tokens() -> None:
    """Un token défini seulement en sombre laisse le clair emprunter une valeur
    qui n'a pas été choisie pour lui."""
    text = TOKENS.read_text(encoding="utf-8")
    light = set(_parse_block(text, THEMES["clair"]))
    media = set(_parse_block(text, ':root:not([data-theme="dark"]) {'))

    assert light == media, "le choix explicite et le réglage système doivent porter les mêmes tokens"
    assert light <= set(_parse_block(text, THEMES["sombre"])), (
        "un token du thème clair sans définition sur :root n'a pas de valeur par défaut"
    )


# ── Douce (P08) : surfaces, encres, accent, rubrique, états, color-scheme ───

SHELL_CSS = WORKSPACE / "shell.css"
LIGHT_MEDIA = ':root:not([data-theme="dark"]) {'

SURFACES = ("--ground", "--panel", "--card", "--overlay")
STATES = ("--ok", "--warn", "--bad")
DOUCE_TOKENS = (
    *SURFACES,
    "--ink",
    "--ink2",
    "--ink3",
    "--acc",
    "--on-acc",
    "--acc-soft",
    "--rub",
    *STATES,
    *(f"{s}-mark" for s in STATES),
    *(f"{s}-soft" for s in STATES),
)

#: Les trois déclarations du thème : sombre, clair explicite, clair par
#: réglage système.
ALL_BLOCKS = {**THEMES, "clair (système)": LIGHT_MEDIA}


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
def test_les_tokens_douce_sont_declares_dans_chaque_theme(theme: str, header: str) -> None:
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    missing = [name for name in DOUCE_TOKENS if name not in tokens]

    assert not missing, f"tokens Douce absents en thème {theme} : {missing}"


def test_color_scheme_est_declare_et_suit_le_theme() -> None:
    """Sans `color-scheme`, barres de défilement, listes natives et champs
    restent clairs dans le thème sombre."""
    text = _strip_comments(TOKENS.read_text(encoding="utf-8"))

    def block(header: str) -> str:
        start = text.index(header)
        return text[start : text.index("}", start)]

    assert "color-scheme: dark" in block(":root {")
    assert "color-scheme: light" in block(THEMES["clair"])
    assert "color-scheme: light" in block(LIGHT_MEDIA)


def _hue_gap(a: str, b: str) -> float:
    import colorsys

    ha = colorsys.rgb_to_hsv(*_rgb(a))[0] * 360
    hb = colorsys.rgb_to_hsv(*_rgb(b))[0] * 360
    gap = abs(ha - hb)
    return min(gap, 360 - gap)


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("fg", ["--ink", "--ink2", "--acc", "--rub", *STATES])
def test_le_texte_teinte_tient_45_sur_chaque_surface(
    theme: str, header: str, surface: str, fg: str
) -> None:
    """L'encre, l'accent, la rubrique et les trois états portent du texte :
    chacun tient 4,5:1 sur les quatre surfaces."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens[fg], tokens[surface])

    assert ratio >= 4.5, f"{fg} sur {surface} en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
@pytest.mark.parametrize("surface", SURFACES)
def test_ink3_tient_45_sur_chaque_surface(theme: str, header: str, surface: str) -> None:
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens["--ink3"], tokens[surface])

    assert ratio >= 4.5, f"--ink3 sur {surface} en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
@pytest.mark.parametrize("state", STATES)
def test_chaque_etat_tient_45_sur_son_fond_doux(theme: str, header: str, state: str) -> None:
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens[state], tokens[f"{state}-soft"])

    assert ratio >= 4.5, f"{state} sur {state}-soft en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
def test_le_texte_de_l_accent_tient_sur_le_fond_doux(theme: str, header: str) -> None:
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    assert _ratio(tokens["--acc"], tokens["--acc-soft"]) >= 4.5
    assert _ratio(tokens["--on-acc"], tokens["--acc"]) >= 4.5


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
@pytest.mark.parametrize("state", STATES)
def test_chaque_marque_d_etat_se_voit_sur_la_carte(theme: str, header: str, state: str) -> None:
    """La marque (point, aplat) n'est pas du texte : WCAG 1.4.11, 3:1."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens[f"{state}-mark"], tokens["--card"])

    assert ratio >= 3.0, f"{state}-mark sur --card en thème {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
def test_l_action_primaire_se_distingue_de_l_erreur(theme: str, header: str) -> None:
    """Avant Douce, `--pri` (orange) et `--bad` (rouge) se confondaient : la
    primaire ne peut pas ressembler à ce qui échoue. Teinte écartée d'au moins
    60° ; la couleur ne porte de toute façon jamais l'état seule."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    gap = _hue_gap(tokens["--pri"], tokens["--bad"])

    assert gap >= 60, f"--pri / --bad en thème {theme} : {gap:.0f}° d'écart de teinte"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
def test_ink2_et_ink3_restent_distincts(theme: str, header: str) -> None:
    """En clair, la hiérarchie d'encre était écrasée (1,23). Seuil : 1,25."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)

    ratio = _ratio(tokens["--ink2"], tokens["--ink3"])

    assert ratio >= 1.25, f"--ink2 / --ink3 en thème {theme} : {ratio:.2f}:1"


# ── Contrôles natifs, boutons désactivés, cibles ────────────────────────────


def _rule(css: str, selector: str) -> dict[str, str]:
    """Déclarations cumulées, dans l'ordre de la feuille, des règles dont la
    liste de sélecteurs contient exactement `selector`."""
    css = _strip_comments(css)
    found: dict[str, str] = {}
    matched = False
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            matched = True
            for decl in match.group(2).split(";"):
                name, _, value = decl.partition(":")
                if value:
                    found[name.strip()] = value.strip()
    assert matched, f"aucune règle pour {selector} dans shell.css"
    return found


def _token_of(value: str) -> str:
    found = re.fullmatch(r"var\((--[\w-]+)\)", value)
    assert found, f"« {value} » doit être un token, pas une valeur"
    return found.group(1)


#: (sélecteur, règles dont il hérite) : les natifs posent fond et encre par
#: token, et leur état désactivé surcharge la règle de base.
NATIVE_CONTROLS = (
    ("select", ()),
    ("option", ("select",)),
    ("input", ("select",)),
    ("input:disabled", ("select", "input:disabled")),
    ("select:disabled", ("select", "input:disabled")),
)


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
@pytest.mark.parametrize("selector,inherits", NATIVE_CONTROLS)
def test_les_controles_natifs_tiennent_45_dans_les_deux_themes(
    theme: str, header: str, selector: str, inherits: tuple[str, ...]
) -> None:
    """select, option, input (et leur état désactivé) posent fond et encre par
    token : mesuré sur les valeurs que ces tokens prennent dans le thème."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)
    css = SHELL_CSS.read_text(encoding="utf-8")
    rule: dict[str, str] = {}
    for name in (*inherits, selector):
        rule.update(_rule(css, name))

    bg, fg = tokens[_token_of(rule["background-color"])], tokens[_token_of(rule["color"])]

    assert _ratio(fg, bg) >= 4.5, f"{selector} en thème {theme} : {_ratio(fg, bg):.2f}:1"


@pytest.mark.parametrize("theme,header", ALL_BLOCKS.items())
def test_un_bouton_desactive_se_distingue_d_un_bouton_actif(theme: str, header: str) -> None:
    """Fond retiré, bordure en tirets et encre secondaire : l'écart de contraste
    du texte doit déjà être perceptible, et la règle couvre aussi le bouton
    primaire et le bouton basculé."""
    tokens = _parse_block(TOKENS.read_text(encoding="utf-8"), header)
    css = SHELL_CSS.read_text(encoding="utf-8")
    for selector in (".btn:disabled", ".btn.pri:disabled", '.btn[aria-pressed="true"]:disabled'):
        rule = _rule(css, selector)
        assert rule["background"] == "transparent", selector
        assert rule["border-style"] == "dashed", selector
        assert _token_of(rule["color"]) == "--ink3", selector

    active = _ratio(tokens["--ink"], tokens["--card"])
    disabled = _ratio(tokens["--ink3"], tokens["--card"])

    assert disabled >= 4.5, "désactivé reste lisible"
    assert active / disabled >= 1.3, (
        f"actif {active:.1f}:1 contre désactivé {disabled:.1f}:1 en thème {theme}"
    )


def test_le_cadenas_d_epinglage_mesure_au_moins_24_px() -> None:
    """Cible minimale WCAG 2.5.8 : la boîte du bouton porte la cible, pas le
    glyphe. La mesure rendue, sur toutes les cibles, est dans
    tests/e2e/test_workspace_target_size.py."""
    rule = _rule(SHELL_CSS.read_text(encoding="utf-8"), ".pin")

    assert float(rule["min-width"].removesuffix("px")) >= 24
    assert float(rule["min-height"].removesuffix("px")) >= 24
