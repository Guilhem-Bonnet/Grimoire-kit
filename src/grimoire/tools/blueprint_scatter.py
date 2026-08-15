"""Éclatement parallèle borné — le symétrique manquant du Gather (P4.1).

Le format savait rassembler N branches (`Gather`) sans savoir les lancer. Le
motif map-reduce agentique — « analyser douze fichiers en parallèle, puis
agréger » — n'était donc pas exprimable, alors qu'il est l'un des plus
courants.

`Scatter` le comble, avec une contrainte qui n'est pas négociable : **le
parallélisme non borné est le premier facteur d'explosion de coût**. Douze
branches, c'est douze fois le contexte, douze fois les sorties, et l'addition
n'apparaît qu'après. Un éclatement sans plafond n'est donc pas une commodité
qu'on ajoutera plus tard : c'est la seule façon dont ce node peut nuire.

D'où deux refus à la compilation :

- **R-S1** — un `Scatter` sans `maxParallel` strictement positif ne compile
  pas. Le plafond est la primitive, pas une option.
- **R-S2** — un `Scatter` que ne couvre aucune garde de budget, sur lui-même
  ou en amont, ne compile pas non plus. Le plafond borne la largeur ; la garde
  borne la dépense. Les deux répondent à des questions différentes.

Et un avertissement, parce que l'erreur est plausible sans être fatale :

- **R-S3** — un éclatement dont aucune branche ne rejoint un `Gather` produit
  N résultats que personne ne rassemble.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import (
    as_dict,
    predecessors,
    upstream_nodes,
)
from grimoire.tools.blueprint_gate import gate_policy, is_gate

__all__ = [
    "compile_scatter_section",
    "is_scatter",
    "scatter_lint",
    "scatter_policy",
    "scatter_shape_errors",
]


def scatter_policy(node: dict[str, Any]) -> dict[str, Any]:
    """`config.scatter` d'un node, ou {} — lecture tolérante."""
    return as_dict(as_dict(node.get("config")).get("scatter"))


def is_scatter(node: dict[str, Any]) -> bool:
    return bool(scatter_policy(node)) or node.get("role") == "Scatter"


def scatter_shape_errors(node: dict[str, Any]) -> list[str]:
    """Forme de `config.scatter` — R-S1 comprise."""
    errors: list[str] = []
    policy = scatter_policy(node)
    if not policy and node.get("role") != "Scatter":
        return errors
    nid = node.get("id", "?")
    role = node.get("role")
    if policy and role not in (None, "Scatter"):
        errors.append(
            f"config.scatter présent mais role={role} — un node d'éclatement "
            f"a role Scatter (node {nid})"
        )

    over = policy.get("over")
    if not isinstance(over, str) or not over.strip():
        errors.append(
            f"R-S1 : Scatter — `over` requis (ce sur quoi on éclate) — node {nid}"
        )

    max_parallel = policy.get("maxParallel")
    if (
        not isinstance(max_parallel, int)
        or isinstance(max_parallel, bool)
        or max_parallel < 1
    ):
        errors.append(
            f"R-S1 : Scatter sans plafond — `maxParallel` entier ≥ 1 requis. "
            f"Un éclatement non borné multiplie le coût sans limite connue "
            f"(node {nid})"
        )
    return errors


def _budget_gates(nodes: list[dict[str, Any]]) -> set[str]:
    """Ids des nodes qui portent une garde de budget."""
    out: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not is_gate(node):
            continue
        if as_dict(gate_policy(node)).get("mode") == "budget":
            out.add(str(node.get("id")))
    return out


def scatter_lint(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(erreurs, avertissements) — R-S2 bloquante, R-S3 informative."""
    errors: list[str] = []
    warnings: list[str] = []
    scatters = [n for n in nodes if isinstance(n, dict) and is_scatter(n)]
    if not scatters:
        return errors, warnings

    budget = _budget_gates(nodes)
    pred = predecessors(edges)
    gathers = {
        str(n.get("id"))
        for n in nodes
        if isinstance(n, dict) and n.get("role") == "Gather"
    }
    # Successeurs, pour savoir si l'éclatement rejoint quelque chose.
    succ: dict[str, set[str]] = {}
    for edge in edges:
        e = as_dict(edge)
        src = str(e.get("from", "")).split(".")[0]
        dst = str(e.get("to", "")).split(".")[0]
        if src and dst:
            succ.setdefault(src, set()).add(dst)

    for node in scatters:
        nid = str(node.get("id", "?"))
        label = node.get("label") or node.get("name") or nid
        covered = ({nid} | upstream_nodes(nid, pred)) & budget
        if not covered:
            errors.append(
                f"R-S2 : Scatter « {label} » n'est couvert par aucune garde de "
                f"budget — le plafond borne la largeur, pas la dépense "
                f"(node {nid})"
            )
        if gathers and not _reaches(nid, succ, gathers):
            warnings.append(
                f"R-S3 : Scatter « {label} » n'atteint aucun Gather — "
                f"les branches produisent des résultats que rien ne rassemble"
            )
        elif not gathers:
            warnings.append(
                f"R-S3 : Scatter « {label} » sans Gather dans le flow — "
                f"les branches produisent des résultats que rien ne rassemble"
            )
    return errors, warnings


def _reaches(start: str, succ: dict[str, set[str]], targets: set[str]) -> bool:
    """`start` atteint-il l'une des cibles en aval ? (cycles tolérés)"""
    seen: set[str] = set()
    stack = list(succ.get(start, ()))
    while stack:
        current = stack.pop()
        if current in targets:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(succ.get(current, ()))
    return False


def compile_scatter_section(node: dict[str, Any]) -> list[str]:
    """Contrainte de parallélisme et plafond, pour l'exécutant hôte."""
    policy = scatter_policy(node)
    if not policy:
        return []
    lines = [
        "### Éclatement parallèle",
        "",
        f"- Éclate sur : `{policy.get('over', '?')}`",
        f"- Parallélisme maximal : **{policy.get('maxParallel', '?')}**",
    ]
    if policy.get("onItemFailure"):
        lines.append(f"- Échec d'un item : {policy['onItemFailure']}")
    lines.append(
        "- Le plafond est une contrainte d'exécution, pas une indication : "
        "l'hôte ne doit jamais lancer plus de branches simultanées."
    )
    lines.append("")
    return lines
