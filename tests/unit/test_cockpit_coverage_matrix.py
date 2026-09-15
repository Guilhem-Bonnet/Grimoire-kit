"""Anti-drift guard: le résumé de docs/cockpit-coverage-matrix.md n'est jamais estimé à la main.

`scripts/cockpit-coverage.py` compte les lignes réelles de chaque table de la
matrice (verdict `Couvert` en dernière colonne) et rend le résumé chiffré. Ce
test échoue si quelqu'un modifie une table sans régénérer le bloc résumé —
exactement la même doctrine anti-dérive que
`test_kit_coverage_export.py` pour `web/data/kit-coverage.json`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "cockpit-coverage.py"
MATRIX = ROOT / "docs" / "cockpit-coverage-matrix.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("cockpit_coverage", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["cockpit_coverage"] = module
    spec.loader.exec_module(module)
    return module


def test_summary_block_matches_the_detail_tables() -> None:
    gen = _load_generator()
    text = MATRIX.read_text(encoding="utf-8")
    counts = gen.parse(text)
    summary = gen.render_summary(counts)
    assert summary.strip() in text, (
        "le bloc résumé de docs/cockpit-coverage-matrix.md est désynchronisé — "
        "lancer : python scripts/cockpit-coverage.py"
    )


def test_matrix_has_real_rows() -> None:
    gen = _load_generator()
    text = MATRIX.read_text(encoding="utf-8")
    counts = gen.parse(text)
    assert counts["TOTAL"].total > 50, "la matrice doit rester une matrice, pas un squelette vide"
    assert counts["Routes API"].total > 0
