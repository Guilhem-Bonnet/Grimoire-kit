"""Le script d'essai de la conversation de cadrage (sans modèle) : un parcours dans le terminal, sur un vrai chantier."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType

import pytest

from grimoire.core import cadrage_interview as interview
from grimoire.core.chantiers import new_chantier

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cadrage-converse.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cadrage_converse", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def test_un_parcours_dans_le_terminal(root: Path) -> None:
    out = io.StringIO()
    code = _load().main(["--chantier", "un", "--root", str(root)],
                        io.StringIO("Les mainteneurs\n\n/etat\noui\n/pause\n"), out)
    text = out.getvalue()
    assert code == 0 and "Dis-moi : qui va se servir de ça" in text
    assert "Si je comprends bien : Les mainteneurs." in text and "On a noté" in text and "pause" in text.lower()
    assert "Les mainteneurs" in (root / "_grimoire/cadrage/un/01-brief.md").read_text(encoding="utf-8")
    assert (root / "_grimoire/cadrage/un/conversation.json").is_file()


def test_quitter_arrete_la_boucle_et_un_chantier_inconnu_est_refuse(root: Path) -> None:
    out = io.StringIO()
    assert _load().main(["--chantier", "un", "--root", str(root)], io.StringIO("/quitter\nignoré\n"), out) == 0
    assert "ignoré" not in out.getvalue()
    bad = io.StringIO()
    assert _load().main(["--chantier", "inconnu", "--root", str(root)], io.StringIO(""), bad) == 2
    assert "Chantier inconnu" in bad.getvalue()


def test_un_refus_du_programme_est_dit_sans_plantage(root: Path) -> None:
    (root / "_grimoire/cadrage/un/conversation.json").write_text("{pas du json", encoding="utf-8")
    out = io.StringIO()
    assert _load().main(["--chantier", "un", "--root", str(root)], io.StringIO("x\n"), out) == 1
    assert "Refusé" in out.getvalue()
