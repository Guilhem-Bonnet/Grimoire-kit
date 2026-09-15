#!/usr/bin/env python3
"""Régénère le résumé chiffré de docs/cockpit-coverage-matrix.md.

La matrice liste, ligne par ligne, chaque couple (contrôle, état) du cockpit
et chaque route API avec sa colonne finale ``Couvert`` (``oui``/``non``). Ce
script ne fait AUCUNE estimation : il compte les lignes du document tel
qu'il est écrit, pour que le résumé en tête de fichier ne puisse jamais
diverger silencieusement du détail qui suit.

Deux familles de tables sont reconnues par leur section :
- chaque ``### Espace : <nom>`` introduit une table de contrôles, dernière
  colonne ``Couvert`` ;
- la section ``## Routes API`` introduit une table de routes, dernière
  colonne ``Couvert`` également.

Toute autre table du document est ignorée (ce n'est pas une table de
couverture) — reconnue par son en-tête qui ne finit pas par ``Couvert``.

Usage :
    python scripts/cockpit-coverage.py             # affiche le résumé
    python scripts/cockpit-coverage.py --check      # échoue si le résumé
                                                      # en tête de fichier
                                                      # est désynchronisé
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "cockpit-coverage-matrix.md"

_SECTION_ESPACE = re.compile(r"^###\s+Espace\s*:\s*(.+?)\s*$")
_SECTION_ROUTES = re.compile(r"^##\s+Routes API\s*$")
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
_SEPARATOR_ROW = re.compile(r"^\|[\s:|-]+\|\s*$")


@dataclass
class Counts:
    total: int = 0
    covered: int = 0

    @property
    def pct(self) -> float:
        return 100.0 * self.covered / self.total if self.total else 0.0


def _cells(line: str) -> list[str]:
    inner = _TABLE_ROW.match(line).group(1)  # type: ignore[union-attr]
    return [c.strip() for c in inner.split("|")]


def parse(text: str) -> dict[str, Counts]:
    """Retourne les compteurs par espace, plus 'Routes API' et 'TOTAL'."""
    per_espace: dict[str, Counts] = {}
    routes = Counts()
    current_espace: str | None = None
    in_routes = False
    header_cells: list[str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        m_espace = _SECTION_ESPACE.match(line)
        if m_espace:
            current_espace = m_espace.group(1)
            per_espace.setdefault(current_espace, Counts())
            in_routes = False
            header_cells = None
            continue
        if _SECTION_ROUTES.match(line):
            in_routes = True
            current_espace = None
            header_cells = None
            continue
        if line.startswith(("## ", "# ")):
            # toute autre section de niveau 1/2 sort du contexte routes
            if not _SECTION_ROUTES.match(line):
                in_routes = False
                current_espace = None
            header_cells = None
            continue
        if not _TABLE_ROW.match(line):
            header_cells = None
            continue
        if _SEPARATOR_ROW.match(line):
            continue
        cells = _cells(line)
        if header_cells is None:
            header_cells = cells
            # une table de couverture finit sa ligne d'en-tête par "Couvert"
            if not header_cells or header_cells[-1].lower() != "couvert":
                header_cells = ["__ignore__"]
            continue
        if header_cells == ["__ignore__"]:
            continue
        verdict = cells[-1].strip().lower()
        covered = verdict in ("oui", "yes", "✅", "true")
        if current_espace is not None:
            counts = per_espace[current_espace]
        elif in_routes:
            counts = routes
        else:
            continue
        counts.total += 1
        if covered:
            counts.covered += 1

    total = Counts()
    for c in per_espace.values():
        total.total += c.total
        total.covered += c.covered
    total.total += routes.total
    total.covered += routes.covered

    result = dict(per_espace)
    result["Routes API"] = routes
    result["TOTAL"] = total
    return result


def render_summary(counts: dict[str, Counts]) -> str:
    lines = ["| Section | Lignes | Couvertes | % |", "|---|---:|---:|---:|"]
    for name, c in counts.items():
        if name == "TOTAL":
            continue
        lines.append(f"| {name} | {c.total} | {c.covered} | {c.pct:.0f}% |")
    total = counts["TOTAL"]
    lines.append(f"| **TOTAL** | **{total.total}** | **{total.covered}** | **{total.pct:.0f}%** |")
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    text = MATRIX.read_text(encoding="utf-8")
    counts = parse(text)
    summary = render_summary(counts)

    if check:
        if summary.strip() not in text:
            print("DÉSYNCHRONISÉ : le résumé en tête de fichier ne correspond plus au détail.")
            print("Relancer sans --check, coller la sortie dans le bloc résumé, committer.")
            print()
            print(summary)
            return 1
        print("résumé synchronisé.")
        return 0

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
