"""``scripts/check-doc-claims.py`` — un chiffre de vitrine sans source existante est refusé."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_doc_claims", REPO / "scripts" / "check-doc-claims.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()
BADGE = '<img src="https://img.shields.io/badge/{}?style=for-the-badge" alt="x">'


@pytest.mark.parametrize(
    "line",
    [
        "Un taux de réussite de 96,7 % sur le banc.",
        "Coût multiplié par ×4,6 face au nu.",
        "Une accélération de 3x mesurée.",
        "Réduit le coût de 2 fois.",
        "**5 990+ tests** (pytest)",
        BADGE.format("tests-5990+-58a6ff"),
        BADGE.format("tools-46-c9a0ff"),
        BADGE.format("coverage-75%25-green"),
    ],
)
def test_a_number_claim_is_detected(line: str) -> None:
    assert MOD.claims_in_line(line)


@pytest.mark.parametrize(
    "line",
    [
        '<img src="docs/assets/divider.svg" width="100%" alt="">',
        '<td width="50%">',
        BADGE.format("python-3.12+-3572A5"),
        BADGE.format("types-mypy%20strict-blue"),
        BADGE.format("license-MIT-f0c674"),
        "Compatible Python 3.12 et 3.13, versions 2.x.",
        "Voir le [rapport (2026-09-17)](bench/rapport.md) du lot A/B.",
        "Un identifiant `CVE-2026-45830` dans un span.",
    ],
)
def test_markup_versions_and_dates_are_not_claims(line: str) -> None:
    assert MOD.claims_in_line(line) == []


def _tree(tmp_path: Path, readme: str) -> list[str]:
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "rapport.md").write_text("# Rapport\n", encoding="utf-8")
    for name in ("README.md", "README.fr.md", "docs/index.md"):
        (tmp_path / name).write_text(readme if name == "README.md" else "Rien.\n", encoding="utf-8")
    return MOD.problems(tmp_path)


def test_an_unanchored_claim_is_refused(tmp_path: Path) -> None:
    found = _tree(tmp_path, "Réussite de 99 %.\n")
    assert len(found) == 1 and "README.md:1" in found[0] and "sans ancre" in found[0]


def test_an_anchor_to_an_existing_file_satisfies_the_claim(tmp_path: Path) -> None:
    assert _tree(tmp_path, "Réussite de 99 %. <!-- source: docs/rapport.md ; recompute: make bench -->\n") == []


def test_an_anchor_to_a_missing_file_is_refused(tmp_path: Path) -> None:
    found = _tree(tmp_path, "Réussite de 99 %. <!-- source: docs/disparu.md -->\n")
    assert len(found) == 1 and "docs/disparu.md" in found[0] and "n'existe pas" in found[0]


def test_an_empty_anchor_is_refused(tmp_path: Path) -> None:
    assert _tree(tmp_path, "Réussite de 99 %. <!-- source: -->\n")


def test_fenced_code_is_not_scanned(tmp_path: Path) -> None:
    assert _tree(tmp_path, "```text\n99 % de réussite\n```\n") == []


def test_a_missing_scope_file_is_not_a_pass(tmp_path: Path) -> None:
    assert "non vérifiée" in MOD.problems(tmp_path)[0]


def test_the_real_showcase_has_no_unsourced_number() -> None:
    assert MOD.problems() == []
