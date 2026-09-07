"""Garde-fou #246 lot 4 — un code de pattern cité doit exister au catalogue.

Avant cette passe, `tools/blueprint_*.py`, `tools/handoff.py`,
`tools/cost_model.py`, les huit extensions et le bridge du standard
(`framework/agentic-standard/`) citaient des codes `ORG-`/`ORC-`/`COG-`/
`KNO-`/`MOD-`/`GOV-`/`QUA-`/`RUN-` sans qu'aucun test ne les rapproche du
catalogue de 78 patterns (`web/data/catalogue-export.json`, la même révision
upstream que `profile-map.yaml`) — deux couches de traçabilité qui ne se
référençaient jamais l'une l'autre. `evidence/schemas.py` et
`missions/ledger.py` implémentent chacun un pattern du catalogue (QUA-04,
QUA-03) sans le citer du tout.

Ce module réplique le scan à l'identique pour que la convergence ne se
défasse pas en silence : un code cité qui n'existe pas au catalogue, ou un
`catalog_ref` du bridge qui pointe dans le vide, fait échouer la suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from grimoire.core.standard_checks.pattern_codes import catalog_pattern_ids, cited_codes

ROOT = Path(__file__).resolve().parents[2]

# Fichiers de src/grimoire dont les codes cités doivent tous exister au
# catalogue upstream. `tools/blueprint_*.py` couvert par glob.
_TOOLS_FILES = [
    *sorted((ROOT / "src/grimoire/tools").glob("blueprint_*.py")),
    ROOT / "src/grimoire/tools/handoff.py",
    ROOT / "src/grimoire/tools/cost_model.py",
    ROOT / "src/grimoire/tools/context_pack.py",
]

# evidence/schemas.py et missions/ledger.py implémentent un pattern du
# catalogue sans le citer avant #246 — la citation attendue une fois branchée.
_CONVERGED_SOURCES = {
    ROOT / "src/grimoire/evidence/schemas.py": {"QUA-04"},
    ROOT / "src/grimoire/missions/ledger.py": {"QUA-03"},
}

_EXTENSIONS_ROOT = ROOT / "extensions"


def _extension_dirs() -> list[Path]:
    return sorted(p for p in _EXTENSIONS_ROOT.iterdir() if p.is_dir())


def _codes_in_tree(root: Path) -> set[str]:
    codes: set[str] = set()
    for f in root.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".md", ".json", ".yaml", ".yml"}:
            codes.update(cited_codes(f))
    return codes


def test_catalog_has_the_eight_families_and_seventy_eight_patterns() -> None:
    ids = catalog_pattern_ids()
    assert len(ids) == 78
    families = {pattern_id.split("-")[0] for pattern_id in ids}
    assert families == {"ORG", "ORC", "COG", "KNO", "MOD", "GOV", "QUA", "RUN"}


@pytest.mark.parametrize("path", _TOOLS_FILES, ids=lambda p: p.name)
def test_tools_cite_only_real_catalog_codes(path: Path) -> None:
    codes = cited_codes(path)
    unknown = codes - catalog_pattern_ids()
    assert not unknown, f"{path.relative_to(ROOT)} cite un code hors catalogue : {sorted(unknown)}"


@pytest.mark.parametrize("ext_dir", _extension_dirs(), ids=lambda p: p.name)
def test_extensions_cite_only_real_catalog_codes(ext_dir: Path) -> None:
    codes = _codes_in_tree(ext_dir)
    unknown = codes - catalog_pattern_ids()
    assert not unknown, f"extensions/{ext_dir.name} cite un code hors catalogue : {sorted(unknown)}"


def test_bridge_templates_cite_only_real_catalog_codes() -> None:
    codes = _codes_in_tree(ROOT / "framework" / "agentic-standard")
    unknown = codes - catalog_pattern_ids()
    assert not unknown, f"framework/agentic-standard cite un code hors catalogue : {sorted(unknown)}"


def test_converged_sources_now_cite_their_pattern() -> None:
    for path, expected in _CONVERGED_SOURCES.items():
        cited = cited_codes(path)
        missing = expected - cited
        assert not missing, f"{path.relative_to(ROOT)} n'a pas convergé vers {sorted(missing)} (#246)"


def test_pattern_catalog_bridge_catalog_ref_points_at_real_codes() -> None:
    """Chaque `catalog_ref` du bridge (#246) doit nommer un code qui existe."""
    data = yaml.safe_load((ROOT / "framework/agentic-standard/templates/pattern-catalog.yaml").read_text(encoding="utf-8"))
    ids = catalog_pattern_ids()
    for pattern in data["patterns"]:
        for ref in pattern.get("catalog_ref", []):
            assert ref in ids, f"{pattern['id']}.catalog_ref cite {ref!r}, absent du catalogue"


def test_pattern_catalog_bridge_declares_a_catalog_ref_for_every_pattern() -> None:
    """Un pattern du bridge sans `catalog_ref` n'est ni convergé ni documenté comme natif au kit."""
    data = yaml.safe_load((ROOT / "framework/agentic-standard/templates/pattern-catalog.yaml").read_text(encoding="utf-8"))
    missing = [p["id"] for p in data["patterns"] if "catalog_ref" not in p]
    assert not missing, f"patterns sans catalog_ref (même vide) : {missing}"
