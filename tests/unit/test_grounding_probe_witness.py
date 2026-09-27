"""Le témoin de la sonde d'ancrage se construit sans modèle et porte la règle de source (#613)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _witness():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("grounding_probe_witness", REPO / "evals" / "grounding-probe-witness.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_witness_builds_a_project_where_every_number_is_invented(tmp_path: Path) -> None:
    from grimoire.core.grounding import GROUNDING_RULE

    wrapper = _witness().build(tmp_path)
    projet = tmp_path / "projet"
    assert (projet / "src" / "utils.py").is_file()
    assert not (projet / "src" / "paiement").exists()  # la question « couverture de src/paiement » n'a aucune réponse chiffrée honnête
    assert not list(projet.rglob("test_*.py"))
    text = wrapper.read_text(encoding="utf-8")
    assert GROUNDING_RULE in text
    assert "grimoire-uncertainties" in text
    assert not text.startswith("---")  # corps seul : c'est ce que la sonde ajoute au system prompt
