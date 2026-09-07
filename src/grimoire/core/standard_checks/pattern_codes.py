"""Le catalogue de 78 patterns upstream (`web/data/catalogue-export.json`) —
la seule source de vérité des codes ``ORG-``/``ORC-``/``COG-``/``KNO-``/
``MOD-``/``GOV-``/``QUA-``/``RUN-`` que `tools/`, les extensions, le bridge
(`framework/agentic-standard/`) et les projets enrôlés se doivent tous de
citer sans en redéfinir un jeu séparé (#246 lot 4).

Extrait comme module partagé pour que la même définition serve au garde-fou
du dépôt kit (`tests/unit/test_pattern_code_convergence.py`, qui scanne
`tools/`, `evidence/`, `missions/` et les extensions) et au vérificateur de
projet (`standard_checks.verifiers._verify_pattern_catalog`, qui contrôle le
`catalog_ref` d'un `pattern-catalog.yaml` déployé) : deux lecteurs, un seul
catalogue, jamais deux listes qui divergent en silence.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

PATTERN_CODE_RE = re.compile(r"\b(?:ORG|ORC|COG|KNO|MOD|GOV|QUA|RUN)-\d{2}\b")


@lru_cache(maxsize=1)
def catalog_pattern_ids() -> frozenset[str]:
    """Every pattern id declared by the bundled upstream catalogue export.

    Reads through :func:`grimoire.data.web_path` so this resolves identically
    from an editable checkout and from an installed wheel (``force-include``
    ships ``web/`` at ``grimoire/data/web/``, catalogue-export.json included).
    """
    from grimoire.data import web_path

    catalogue_path = web_path() / "data" / "catalogue-export.json"
    data = json.loads(catalogue_path.read_text(encoding="utf-8"))
    return frozenset(str(p["id"]) for p in data.get("patterns", []))


def cited_codes(path: Path) -> set[str]:
    """Pattern-shaped tokens (``XXX-99``) found in one file, wherever they sit."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return set(PATTERN_CODE_RE.findall(text))
