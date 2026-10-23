"""Composants partagés des espaces : une seule implémentation chacun (P21).

La revue UI d'octobre 2026 a compté six badges, cinq tableaux et trois copies
de la tuile KPI, chacun recopié sous le préfixe de son espace avec ses propres
rayons, hauteurs et couleurs. Les cinq composants (badge, tableau, tuile KPI,
état vide, en-tête d'espace) vivent désormais dans ``shell.css`` et sont
construits par ``shell.js`` ; ce module verrouille trois choses sans
navigateur :

- aucune copie préfixée ne revient, ni dans le code, ni dans les tests, ni
  dans la convention de design ;
- le badge tient sur une ligne (``nowrap`` + ellipse) ;
- un segment n'est jamais pressé et désactivé à la fois.

Le rendu réel (style calculé, DOM des cinq espaces) est vérifié par
``tests/e2e/test_workspace_components.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
SHELL_CSS = WORKSPACE / "shell.css"
SHELL_JS = WORKSPACE / "shell.js"
SPACES = WORKSPACE / "spaces"

#: Les copies retirées par P21 : leur retour est une régression.
RETIRED = (
    "pl-badge", "ex-badge",
    "pl-kpi", "ob-kpi", "me-kpi",
    "pl-table", "ob-table", "me-table", "cv-table",
)
_RETIRED = re.compile(r"\.?\b(?:" + "|".join(re.escape(name) for name in RETIRED) + r")\b")

#: Où une copie pourrait revenir : le code du cockpit, ses tests, la
#: convention que lisent les outils de design.
_SCANNED = (
    (ROOT / "web", ("*.css", "*.js", "*.html")),
    (ROOT / "tests", ("*.py",)),
    (ROOT / ".design-sync", ("*.md", "*.mjs")),
)

#: Les deux gardes nomment les copies retirées pour les interdire.
_GUARDS = {Path(__file__).resolve(), ROOT / "tests" / "e2e" / "test_workspace_components.py"}


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _rule(css: str, selector: str) -> str:
    """Corps de la règle dont la liste de sélecteurs contient ``selector``."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css)):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    raise AssertionError(f"règle {selector!r} introuvable")


def _declarations(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for declaration in body.split(";"):
        if ":" in declaration:
            name, value = declaration.split(":", 1)
            found[name.strip()] = value.strip()
    return found


def test_aucune_copie_prefixee_d_un_composant_partage() -> None:
    """Le grep du critère d'acceptation, rejoué à chaque CI."""
    offenders: list[str] = []
    for base, patterns in _SCANNED:
        for pattern in patterns:
            for path in sorted(base.rglob(pattern)):
                if path.resolve() in _GUARDS:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if _RETIRED.search(line):
                        offenders.append(f"{path.relative_to(ROOT)}:{number} — {line.strip()[:100]}")
    assert not offenders, "copie préfixée d'un composant partagé :\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("selector", [".badge", ".kpi", ".kpi-item", ".tbl", ".tbl-wrap", ".space-head", ".empty"])
def test_chaque_composant_est_defini_une_fois_dans_la_coque_et_jamais_dans_un_espace(selector: str) -> None:
    css = _strip_comments(SHELL_CSS.read_text(encoding="utf-8"))
    # Règle de base : en colonne 0 (une variante sous @media est indentée).
    base = re.compile(r"^" + re.escape(selector) + r"\s*[{,]", re.MULTILINE)
    assert len(base.findall(css)) == 1, f"{selector} doit avoir une seule règle de base dans shell.css"
    opening = re.compile(r"(?:^|[},])\s*" + re.escape(selector) + r"\s*[{,]", re.MULTILINE)
    for space in sorted(SPACES.glob("*.js")):
        assert not opening.search(space.read_text(encoding="utf-8")), f"{space.name} redéfinit {selector}"


def test_le_badge_tient_sur_une_ligne_avec_une_ellipse() -> None:
    """Défaut no 1 de la revue : une pastille de hauteur fixe sans ``nowrap``
    passait sur deux lignes et débordait de son bandeau."""
    badge = _declarations(_rule(SHELL_CSS.read_text(encoding="utf-8"), ".badge"))
    assert badge.get("white-space") == "nowrap"
    assert badge.get("text-overflow") == "ellipsis"
    assert badge.get("overflow") == "hidden", "sans overflow: hidden, l'ellipse ne s'applique pas"
    assert badge.get("max-width") == "100%", "sans largeur maximale, rien ne déclenche l'ellipse"
    assert badge.get("display") in {"inline-block", "block"}, "l'ellipse ne coupe pas le texte d'une boîte flex"


def test_le_badge_porte_son_texte_complet_au_survol() -> None:
    source = SHELL_JS.read_text(encoding="utf-8")
    body = source[source.index("export function badge("):]
    body = body[: body.index("\n}\n")]
    assert "title" in body, "un badge ellipsé doit garder son texte complet dans `title`"


def test_un_segment_desactive_n_est_jamais_presse() -> None:
    """Défaut no 4 de la revue : pressé et désactivé à la fois, « Carte »
    sortait à 1,66:1. La coque ne pose plus jamais les deux, et la feuille
    fait gagner le désactivé si un appel les cumule quand même."""
    source = SHELL_JS.read_text(encoding="utf-8")
    segment = source[source.index("function segment("):]
    segment = segment[: segment.index("\n}\n")]
    assert re.search(r"'aria-pressed',\s*String\(!item\.disabled &&", segment), (
        "segment() doit refuser aria-pressed=true à un bouton désactivé"
    )

    css = SHELL_CSS.read_text(encoding="utf-8")
    both = _declarations(_rule(css, '.seg > button[aria-pressed="true"]:disabled'))
    pressed = _declarations(_rule(css, '.seg > button[aria-pressed="true"]'))
    assert both.get("background") != pressed.get("background"), "le désactivé ne doit pas garder le fond pressé"
    assert both.get("color") == "var(--ink3)"
    assert "opacity" not in both, "jamais d'opacité sur un désactivé (contraste)"
