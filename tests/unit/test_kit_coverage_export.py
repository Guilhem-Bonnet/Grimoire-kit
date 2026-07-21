"""Anti-drift guard: web/data/kit-coverage.json mirrors capability-map.

patterns.html shows how much of the normative catalog the kit actually
verifies. This export must follow every capability-map change, or the
Atelier would display a stale coverage figure — the exact claims-drift
the kit refuses elsewhere (README honesty passes, governed-controls).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "gen-kit-coverage.py"
EXPORT = ROOT / "web" / "data" / "kit-coverage.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_kit_coverage", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_matches_capability_map() -> None:
    gen = _load_generator()
    committed = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert committed == gen.build_payload(), (
        "web/data/kit-coverage.json is out of sync with capability-map.yaml "
        "— run: python scripts/gen-kit-coverage.py"
    )


def test_export_counts_are_consistent() -> None:
    committed = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert committed["verified_pattern_count"] == len(committed["verified_patterns"])
    assert committed["verified_pattern_count"] > 0
