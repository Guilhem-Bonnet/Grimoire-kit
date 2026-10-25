"""Lot H du tri : contraste élevé 7:1 (P61), durée de transition (P54), hauteurs de ligne (P61).

Le calcul porte sur les valeurs déclarées de ``tokens.css`` ; la mesure sur le
DOM rendu est dans ``tests/e2e/test_workspace_tri_h.py``.
"""

from __future__ import annotations

import re

import pytest

from .test_workspace_tokens import (
    THEMES,
    TOKENS,
    WORKSPACE,
    _parse_block,
    _ratio,
    _strip_comments,
)

SEUIL = 7.0
SURFACES = ("--ground", "--panel", "--card", "--overlay", "--hover")
ENCRES = ("--ink", "--ink2", "--ink3", "--acc", "--rub", "--ok", "--warn", "--bad")
MARQUES = ("--ok-mark", "--warn-mark", "--bad-mark", "--s1", "--s2", "--s3")
SYNTAXE = ("--syn-key", "--syn-string", "--syn-tag", "--syn-placeholder", "--syn-path", "--syn-ident", "--syn-comment")
HC = {
    "sombre": ':root[data-contrast="high"][data-theme="dark"] {',
    "clair": ':root[data-contrast="high"][data-theme="light"] {',
}


def _resolved(theme: str) -> dict[str, str]:
    """Les tokens d'un thème en contraste élevé : le thème, recouvert par le bloc."""
    text = TOKENS.read_text(encoding="utf-8")
    tokens = {**_parse_block(text, THEMES[theme]), **_parse_block(text, HC[theme])}
    for name, value in list(tokens.items()):
        while value.startswith("var("):
            value = tokens[value[4:-1]]
        tokens[name] = value
    return tokens


@pytest.mark.parametrize("theme", HC)
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("ink", ENCRES)
def test_p61_chaque_encre_tient_7_pour_1_sur_chaque_surface(theme: str, surface: str, ink: str) -> None:
    tokens = _resolved(theme)

    ratio = _ratio(tokens[ink], tokens[surface])

    assert ratio >= SEUIL, f"{ink} sur {surface}, contraste élevé {theme} : {ratio:.2f}:1"


@pytest.mark.parametrize("theme", HC)
def test_p61_les_etats_et_l_accent_tiennent_7_pour_1_sur_leur_fond_doux(theme: str) -> None:
    tokens = _resolved(theme)
    pairs = [(f"--{s}", f"--{s}-soft") for s in ("ok", "warn", "bad")]
    pairs += [("--acc", "--acc-soft"), ("--ink", "--acc-soft"), ("--ink2", "--acc-soft")]

    failures = [f"{a} sur {b} : {_ratio(tokens[a], tokens[b]):.2f}:1" for a, b in pairs
                if _ratio(tokens[a], tokens[b]) < SEUIL]

    assert not failures, failures


@pytest.mark.parametrize("theme", HC)
def test_p61_l_encre_des_remplissages_et_du_terminal_tient_7_pour_1(theme: str) -> None:
    tokens = _resolved(theme)
    pairs = [("--on-acc", "--acc"), ("--termink", "--term"), *(("--onfill", m) for m in MARQUES)]

    failures = [f"{a} sur {b} : {_ratio(tokens[a], tokens[b]):.2f}:1" for a, b in pairs
                if _ratio(tokens[a], tokens[b]) < SEUIL]

    assert not failures, failures


@pytest.mark.parametrize("theme", HC)
@pytest.mark.parametrize("token", SYNTAXE)
def test_p61_la_colorisation_de_source_tient_7_pour_1(theme: str, token: str) -> None:
    tokens = _resolved(theme)

    assert _ratio(tokens[token], tokens["--bg"]) >= SEUIL, token


def test_p61_le_mode_standard_n_atteint_pas_7_pour_1_partout() -> None:
    """Le test ne mesure rien s'il passe aussi sans le bloc : le thème de base
    doit avoir au moins une encre sous 7:1, sinon le mode n'a pas d'objet."""
    text = TOKENS.read_text(encoding="utf-8")
    base = _parse_block(text, THEMES["sombre"])

    assert any(_ratio(base[i], base[s]) < SEUIL for i in ENCRES for s in SURFACES)


def test_p61_hauteurs_de_ligne_declarees_32_et_44() -> None:
    text = _strip_comments(TOKENS.read_text(encoding="utf-8"))

    assert re.search(r"--h-row:\s*44px", text)
    compact = text[text.index(':root[data-density="compacte"] {'):]
    assert re.search(r"--h-row:\s*32px", compact.split("}")[0])


def test_p54_la_transition_de_vue_dure_au_plus_200_ms() -> None:
    css = _strip_comments((WORKSPACE / "shell.css").read_text(encoding="utf-8"))
    block = re.search(r"::view-transition-old\(root\),\s*::view-transition-new\(root\)\s*\{([^}]*)\}", css)

    assert block, "la durée de la transition de vue doit être déclarée"
    durations = re.findall(r"animation-duration:\s*(\d+)ms", block.group(1))
    assert durations and all(int(d) <= 200 for d in durations), durations


def test_p54_prefers_reduced_motion_coupe_aussi_la_transition_de_vue() -> None:
    css = _strip_comments((WORKSPACE / "shell.css").read_text(encoding="utf-8"))

    reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, flags=re.DOTALL)
    assert any("::view-transition-old(root)" in body and "animation: none" in body for body in reduced)
