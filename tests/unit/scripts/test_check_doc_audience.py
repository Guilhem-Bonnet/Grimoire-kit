"""``scripts/check-doc-audience.py`` — un fichier de docs/ non classé est refusé."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-doc-audience.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_doc_audience", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()


@pytest.mark.parametrize("audience", ["utilisateur", "contributeur", "référence", "décision"])
def test_the_four_audiences_are_accepted(audience: str) -> None:
    assert MOD.audience_of(f"---\naudience: {audience}\n---\n\n# T\n") == (audience, None)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("# Sans front-matter\n", "pas de front-matter"),
        ("---\ntitle: x\n---\n\n# T\n", "sans clé `audience`"),
        ("---\naudience: tout le monde\n---\n", "hors de"),
        ("---\naudience:\n---\n", "hors de"),
    ],
)
def test_a_missing_or_unknown_audience_is_refused(text: str, fragment: str) -> None:
    audience, problem = MOD.audience_of(text)
    assert audience is None
    assert problem is not None and fragment in problem


def test_problems_lists_every_unclassified_file(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "ok.md").write_text("---\naudience: référence\n---\n", encoding="utf-8")
    (docs / "sub" / "ko.md").write_text("# rien\n", encoding="utf-8")
    assert MOD.problems(docs) == ["docs/sub/ko.md : pas de front-matter (--- ... ---) en tête de fichier"]


def test_an_empty_docs_tree_is_not_a_pass(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    assert "non vérifiée" in MOD.problems(tmp_path / "docs")[0]


def test_the_real_docs_tree_is_fully_classified() -> None:
    assert MOD.problems() == []
