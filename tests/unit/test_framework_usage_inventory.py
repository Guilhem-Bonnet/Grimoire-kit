"""Garde-fou de l'instrument de décision du gel (framework/FREEZE.md).

`scripts/framework-usage-inventory.py` classe les outils de `framework/tools/`
par usage réel. Un artefact généré qui énumère toute la zone gelée (plafonds du
ratchet, données du site) fait apparaître chaque outil comme référencé et rend
le classement dégénéré : tout REFERENCED, aucun candidat à la suppression.

C'est arrivé en 3.24.0 — `scripts/code-ratchet-baseline.json`, introduit pour
appliquer le gel, a aveuglé l'inventaire censé le drainer. Ces tests encodent
la régression pour qu'elle ne puisse pas revenir en silence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "framework-usage-inventory.py"


@pytest.fixture(scope="module")
def inventory():
    spec = importlib.util.spec_from_file_location("framework_usage_inventory", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_indexes_still_exist(inventory):
    """Un renommage d'artefact généré doit être signalé, pas subi."""
    assert inventory.check_generated_indexes() == []


def test_grep_hits_filters_generated_indexes(inventory, monkeypatch):
    """Les index générés ne comptent jamais comme référence."""
    hits = [*sorted(inventory.GENERATED_INDEXES), "src/grimoire/cli/app.py"]
    monkeypatch.setattr(inventory, "git", lambda *args: "\n".join(hits) + "\n")

    assert inventory.grep_hits("whatever.py", ["src"]) == {"src/grimoire/cli/app.py"}


def test_ratchet_baseline_alone_does_not_make_a_tool_referenced(inventory, monkeypatch):
    """Le scénario exact de la régression 3.24.0.

    Un outil cité uniquement par la baseline du ratchet et par ses propres
    tests doit rester TEST_ONLY — donc candidat à la suppression.
    """
    def fake_git(*args: str) -> str:
        paths = set(args)
        if "tests" in paths:
            return "tests/test_dead_tool.py\n"
        if "src" in paths:  # RUNTIME_PATHS
            return "scripts/code-ratchet-baseline.json\n"
        return ""

    monkeypatch.setattr(inventory, "git", fake_git)

    verdict, counts = inventory.classify("framework/tools/dead-tool.py")
    assert verdict == "TEST_ONLY"
    assert counts["runtime"] == 0
    assert counts["tests"] == 1


def test_real_runtime_reference_still_wins(inventory, monkeypatch):
    """Le filtre ne doit pas masquer un appelant légitime."""
    def fake_git(*args: str) -> str:
        paths = set(args)
        if "src" in paths:
            return "scripts/code-ratchet-baseline.json\nsrc/grimoire/cli/cmd_hooks.py\n"
        return ""

    monkeypatch.setattr(inventory, "git", fake_git)

    verdict, counts = inventory.classify("framework/tools/live-tool.py")
    assert verdict == "REFERENCED"
    assert counts["runtime"] == 1
