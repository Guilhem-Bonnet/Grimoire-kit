"""Garde de la vitrine : des affirmations fausses, un terminal inventé et des restes d'outil ne reviennent pas.

Trois familles, lues mécaniquement dans ``web/`` (hors ``workspace/``) :

- des formulations qui promettent ce que le produit ne fait pas (``--session``,
  « immuable », « signé », « EARLY ACCESS », « Trust scoring », …) ;
- des restes d'outil de design dans ``index.html`` (``tweaks-panel``, un
  ``postMessage`` vers ``'*'``) ;
- des pictogrammes Unicode dans ``index.html`` : les icônes sont des SVG maison.

La comparaison ignore la casse : « SCELLÉ » et « scellé » sont la même faute.

Deux exclusions : ``workspace/`` est un espace de travail, pas la vitrine ; les
revues datées ``DESIGN-REVIEW-*.md`` citent les pages telles qu'elles étaient
pour les critiquer (« multi-LLM » y nomme une section de l'ancienne page
Observatory), on ne réécrit pas une revue pour satisfaire la garde.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
INDEX = WEB / "index.html"

# Affirmations et formulations retirées de la vitrine et de ses pages sœurs.
FAUSSES_AFFIRMATIONS = [
    "--session",
    "--agents",
    "sog.dispatch",
    "immuable",
    "scellé",
    "signé",
    "aucun agent ne peut contourner",
    "EARLY ACCESS",
    "ROADMAP · Q3",
    "Multi-LLM",
    "Trust scoring",
]

RESTES_D_OUTIL = [
    "tweaks-panel",
    re.compile(r"postMessage\([^)]*'\*'\)"),
]

PICTOGRAMMES = ["▶", "↳", "✓", "⚙", "↓"]  # « → » (U+2192) est une flèche typographique, pas un pictogramme : le rejeu verbatim la garde

FICHIERS_EXCLUS = {"workspace"}
PREFIXES_EXCLUS = ("DESIGN-REVIEW-",)


def _fichiers_web() -> list[Path]:
    """Fichiers texte de ``web/`` ; ``workspace/`` et les revues datées sont hors vitrine."""
    return sorted(
        p
        for p in WEB.rglob("*")
        if p.is_file()
        and not (set(p.relative_to(WEB).parts[:-1]) & FICHIERS_EXCLUS)
        and not p.name.startswith(PREFIXES_EXCLUS)
        and p.suffix in {".html", ".js", ".json", ".md", ".css"}
    )


def _occurrences(texte: str, motif: str | re.Pattern[str]) -> list[int]:
    if isinstance(motif, re.Pattern):
        return [texte.count("\n", 0, m.start()) + 1 for m in motif.finditer(texte)]
    lignes = texte.lower()
    cible = motif.lower()
    numeros, debut = [], 0
    while (pos := lignes.find(cible, debut)) != -1:
        numeros.append(lignes.count("\n", 0, pos) + 1)
        debut = pos + len(cible)
    return numeros


def _trouve(fichiers: list[Path], motifs: list[str | re.Pattern[str]]) -> list[str]:
    trouvailles = []
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        for motif in motifs:
            label = motif.pattern if isinstance(motif, re.Pattern) else motif
            for ligne in _occurrences(texte, motif):
                trouvailles.append(f"{fichier.relative_to(WEB)}:{ligne} «{label}»")
    return trouvailles


def test_aucune_fausse_affirmation_dans_web() -> None:
    assert _trouve(_fichiers_web(), FAUSSES_AFFIRMATIONS) == []


def test_aucun_reste_d_outil_dans_index() -> None:
    assert _trouve([INDEX], RESTES_D_OUTIL) == []


def test_aucun_pictogramme_unicode_dans_index() -> None:
    assert _trouve([INDEX], PICTOGRAMMES) == []


@pytest.mark.parametrize("motif", ["tweaks-panel", "--session", "scellé", "▶"])
def test_la_garde_detecte_chaque_famille(tmp_path: Path, motif: str) -> None:
    """La garde elle-même : une occurrence injectée doit être rapportée."""
    fichier = tmp_path / "page.html"
    fichier.write_text(f"<p>avant</p>\n<p>{motif}</p>\n", encoding="utf-8")
    assert _occurrences(fichier.read_text(encoding="utf-8"), motif) == [2]
