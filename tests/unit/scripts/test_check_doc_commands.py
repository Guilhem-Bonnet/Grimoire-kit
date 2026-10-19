"""``scripts/check-doc-commands.py`` — une commande citée qui n'existe pas dans la CLI est refusée."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_doc_commands", REPO / "scripts" / "check-doc-commands.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()
ROOT, CTX = MOD.load_root()


@pytest.mark.parametrize(
    "citation",
    [
        "grimoire doctor",
        "grimoire init mon-projet --name x",
        "grimoire task add",
        "grimoire gates repo --profile release",
        "grimoire -o json doctor",  # option globale en tête : hors du motif, jamais jugée
        "python scripts/x.py",
    ],
)
def test_existing_or_foreign_citations_pass(citation: str) -> None:
    assert MOD.unknown_command(citation, ROOT, CTX) is None


@pytest.mark.parametrize(
    ("citation", "missing"),
    [
        ("grimoire commande-fantome", "grimoire commande-fantome"),
        ("grimoire task sous-commande-fantome", "grimoire task sous-commande-fantome"),
        ("grimoire gates repo-fantome", "grimoire gates repo-fantome"),
    ],
)
def test_a_missing_command_is_named(citation: str, missing: str) -> None:
    assert MOD.unknown_command(citation, ROOT, CTX) == missing


def test_citations_come_from_spans_and_fenced_lines_only() -> None:
    text = "Prose grimoire fantome.\n\nVoir `grimoire doctor`.\n\n```bash\n$ grimoire status\nls\n```\n"
    assert list(MOD.citations(text)) == ["grimoire doctor", "grimoire status", "ls"]


def test_decision_documents_are_out_of_scope_but_everything_else_is(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "adr.md").write_text("---\naudience: décision\n---\n`grimoire fantome`\n", encoding="utf-8")
    (docs / "guide.md").write_text("---\naudience: utilisateur\n---\n`grimoire fantome`\n", encoding="utf-8")
    (docs / "libre.md").write_text("`grimoire fantome`\n", encoding="utf-8")
    scope = {p.name for p in MOD.in_scope(tmp_path)}
    assert scope == {"guide.md", "libre.md"}


def test_the_real_documentation_only_cites_existing_commands() -> None:
    assert MOD.problems() == []
