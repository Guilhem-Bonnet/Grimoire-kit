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
