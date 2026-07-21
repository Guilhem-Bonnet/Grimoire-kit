#!/usr/bin/env python3
"""Generate web/data/kit-coverage.json — kit-verified pattern coverage.

The Atelier displays the normative catalog (78 patterns exported from
processus-developpement-agentique). The kit mechanically verifies a
subset (framework/agentic-standard/capability-map.yaml, enforced by
``grimoire standard verify``). This export makes that gap explicit on
patterns.html instead of letting the page read as "the kit covers all
of it".

No per-pattern mapping is emitted on purpose: capability-map anchors
patterns via ``source_normative`` (domain.item), while the catalog uses
family refs (ORG-01…); inventing a 1-to-1 correspondence here would be
exactly the kind of unverifiable claim the kit refuses. Only counts and
the kit-side list (with their real normative anchors) are published.

Regenerate after any capability-map change (drift is test-enforced):

    python scripts/gen-kit-coverage.py
"""

from __future__ import annotations

import json
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_MAP = ROOT / "framework/agentic-standard/capability-map.yaml"
OUTPUT = ROOT / "web/data/kit-coverage.json"

NOTE = (
    "Patterns vérifiés mécaniquement par `grimoire standard verify` "
    "(capability-map du kit). L'ancrage normatif se fait par "
    "source_normative ; il n'existe pas de correspondance pattern-par-"
    "pattern avec les références du catalogue (ORG-xx, ORC-xx…) — le "
    "compteur compare des périmètres, pas des identités."
)


def build_payload() -> dict[str, object]:
    yaml = YAML(typ="safe")
    capability_map = yaml.load(CAPABILITY_MAP.read_text(encoding="utf-8"))
    patterns = capability_map["patterns"]
    verified = [
        {
            "id": pattern_id,
            "category": spec.get("category", ""),
            "profile_min": spec.get("profile_min", ""),
        }
        for pattern_id, spec in sorted(patterns.items())
    ]
    return {
        "generated_by": "scripts/gen-kit-coverage.py",
        "source": "framework/agentic-standard/capability-map.yaml",
        "verified_pattern_count": len(verified),
        "verified_patterns": verified,
        "note": NOTE,
    }


def main() -> None:
    payload = build_payload()
    OUTPUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"written: {OUTPUT.relative_to(ROOT)} ({payload['verified_pattern_count']} patterns)")


if __name__ == "__main__":
    main()
