"""Algèbre des primitives de node — classe sémantique `role` (P0.3).

Le blueprint n'introduit pas un bestiaire de `kind` : il exprime tout node par
une **classe sémantique orthogonale** (`role`) parmi 7 primitives, pilotée par
la config (RAFFINEMENT §2). Les ~20 cases de la palette XXL deviennent des
**paramètres** de ces 7 primitives — ce module en est la source de vérité,
exposée par ``/api/primitives`` et consommée par la re-catégorisation de la
palette.

Le `role` est orthogonal à ``node.kind`` (``pattern`` / ``artifact`` / …) :
`kind` dit *d'où vient* le node, `role` dit *ce qu'il fait* structurellement.
"""

from __future__ import annotations

from typing import Any

PRIMITIVES_SCHEMA_VERSION = "grimoire-node-primitives/v1"

# Les 7 primitives orthogonales (RAFFINEMENT §2). `compilesTo` résume la cible
# de compilation ; `doesWork` distingue l'unique primitive « qui fait ».
PRIMITIVES: dict[str, dict[str, Any]] = {
    "Unit": {
        "role": "La seule primitive « qui fait » : consomme des contrats, "
        "produit des contrats (agent, artefact, pattern, node d'extension).",
        "compilesTo": "Artefact gouverné (agent.md, étape de workflow).",
        "doesWork": True,
    },
    "Route": {
        "role": "Branchement déclaratif sur verdict / seuil / étiquette.",
        "compilesTo": "Règle de policy (GOV-01), sections conditionnelles.",
        "doesWork": False,
    },
    "Scatter": {
        "role": "Éclatement parallèle (map sur N items, borné).",
        "compilesTo": "Contrainte de parallélisme + garde de budget.",
        "doesWork": False,
    },
    "Gather": {
        "role": "Jointure : fan-in, quorum, consensus.",
        "compilesTo": "Contrat d'agrégation (handoff-packets multiples).",
        "doesWork": False,
    },
    "Gate": {
        "role": "Précondition universelle qui doit tenir pour passer.",
        "compilesTo": "GOV-xx / QUA-xx selon paramètre.",
        "doesWork": False,
    },
    "Boundary": {
        "role": "Annotation transversale posée sur une région ou un edge "
        "(ne « fait » rien).",
        "compilesTo": "Métadonnée de workflow.",
        "doesWork": False,
    },
    "Reference": {
        "role": "Pointeur vers quelque chose hors-graphe.",
        "compilesTo": "Section prérequis / config.",
        "doesWork": False,
    },
}

# Preuve P0.3 : chaque case de la palette XXL mappée à une primitive + ses
# paramètres (RAFFINEMENT §2). Plus de bestiaire — un tableau de configurations.
XXL_MAPPING: dict[str, dict[str, Any]] = {
    "decision-branch": {"primitive": "Route", "params": {}},
    "bounded-loop": {"primitive": "Boundary", "params": {"mode": "loop", "budgetMax": True}},
    "human-gate": {"primitive": "Gate", "params": {"mode": "human"}},
    "join-fanin": {"primitive": "Gather", "params": {"mode": "quorum"}},
    "critical-consensus": {
        "primitive": "Gather",
        "params": {"mode": "quorum", "devilsAdvocate": True},
    },
    "trigger": {"primitive": "Reference", "params": {"kind": "trigger"}},
    "mcp-toolbox": {
        "primitive": "Reference",
        "params": {"kind": "mcp", "requires": "Gate(mcp-trust)"},
    },
    "outbound-notification": {
        "primitive": "Unit",
        "params": {"sideEffect": True, "permission": "network"},
    },
    "resource-secret": {"primitive": "Reference", "params": {"kind": "resource"}},
    "doc-source": {"primitive": "Reference", "params": {"kind": "doc-source"}},
    "memory-rw": {"primitive": "Reference", "params": {"kind": "memory", "access": "r|w"}},
    "stigmergy-signal": {
        "primitive": "Reference",
        "params": {"kind": "signal", "mode": "emit|listen"},
    },
    "budget-guard": {"primitive": "Gate", "params": {"mode": "budget"}},
    "evidence-checkpoint": {"primitive": "Gate", "params": {"mode": "evidence"}},
    "observation-probe": {
        "primitive": "Boundary",
        "params": {"mode": "telemetry-probe"},
    },
    "output-contract": {"primitive": "Gate", "params": {"mode": "output-contract"}},
    "sub-blueprint": {"primitive": "Reference", "params": {"kind": "sub-blueprint"}},
}

PRIMITIVE_NAMES = tuple(PRIMITIVES)


def is_valid_role(role: str | None) -> bool:
    """Un `role` est valide s'il est absent ou l'une des 7 primitives."""
    return role is None or role in PRIMITIVES


def primitives_catalogue() -> dict[str, Any]:
    """Charge utile de ``/api/primitives`` : les 7 primitives + mapping XXL."""
    return {
        "schemaVersion": PRIMITIVES_SCHEMA_VERSION,
        "primitives": PRIMITIVES,
        "xxlMapping": XXL_MAPPING,
    }


# Pins par famille pour les blueprints du Studio (v2) : même heuristique que
# web/atelier-nav.js — à remplacer par une curation par pattern dans le
# catalogue quand elle existera. `handoff-packet` circule partout.
STUDIO_FAMILY_PINS: dict[str, dict[str, list[str]]] = {
    "ORG": {"in": ["handoff-packet"], "out": ["task-envelope"]},
    "ORC": {"in": ["task-envelope"], "out": ["task-envelope", "handoff-packet"]},
    "GOV": {"in": ["task-envelope"], "out": ["task-envelope"]},
    "MOD": {"in": ["task-envelope"], "out": ["handoff-packet"]},
    "COG": {"in": ["task-envelope", "context-pack"], "out": ["handoff-packet"]},
    "QUA": {
        "in": ["handoff-packet", "evidence-pack"],
        "out": ["evidence-pack", "verification-verdict"],
    },
    "KNO": {
        "in": ["handoff-packet", "evidence-pack"],
        "out": ["context-pack", "memory-record"],
    },
    "RUN": {"in": ["handoff-packet"], "out": ["telemetry-event"]},
}
