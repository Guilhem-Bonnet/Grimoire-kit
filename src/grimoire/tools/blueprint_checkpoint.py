"""Frontières de checkpoint — reprise après suspension (P3.2).

Une porte humaine **suspend** le flow : l'orchestrateur s'arrête, une personne
approuve, corrige ou fournit ce qui manque, et le travail reprend. Reprendre
suppose que quelque chose a survécu à l'attente. Sans état persisté, « reprise
après approbation » n'est pas une fonctionnalité dégradée : c'est une phrase
sans référent, et le run meurt avec le processus qui l'hébergeait.

Ce module rend cette dépendance déclarable et vérifiable. Une frontière de
checkpoint énumère les nodes dont l'état est persisté ; le flow est reprenable
à partir de là.

Le modèle réutilise les ``boundaries`` de haut niveau introduites par C3 pour
l'isolation — même forme (``{id, mode, members}``), autre mode. Rien de neuf
au format : un blueprint sans ``boundaries`` se comporte comme avant.

La règle qui compte, **R-K1**, relie les deux : toute porte humaine doit être
couverte par un checkpoint, sur elle-même ou en amont. C'est le seul contrôle
qui empêche de livrer un flow que personne ne pourra reprendre.

Ce module ne relinte pas les portes : il lit ``blueprint_gate`` comme source de
vérité, et n'ajoute que ce que la reprise exige.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import as_dict
from grimoire.tools.blueprint_gate import gate_policy, is_gate

__all__ = [
    "CHECKPOINT_SCOPES",
    "checkpoint_regions",
    "checkpoint_shape_errors",
    "checkpoints_covering",
    "compile_checkpoint_section",
    "suspending_gates",
]

#: Ce que le checkpoint garantit avoir persisté. ``state`` est le minimum
#: utile — sans lui, reprendre revient à relancer.
CHECKPOINT_SCOPES = ("state", "state+artifacts", "full")

DEFAULT_SCOPE = "state"


def checkpoint_regions(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    """Frontières ``mode: checkpoint`` déclarées, sous forme normalisée.

    Additif : un blueprint sans ``boundaries`` n'en a aucune.
    """
    regions: list[dict[str, Any]] = []
    raw = blueprint.get("boundaries")
    if not isinstance(raw, list):
        return regions
    for boundary in raw:
        b = as_dict(boundary)
        if b.get("mode") != "checkpoint":
            continue
        members = [m for m in b.get("members", []) if isinstance(m, str)]
        regions.append(
            {
                "id": b.get("id"),
                "mode": "checkpoint",
                "members": members,
                "scope": b.get("scope", DEFAULT_SCOPE),
            }
        )
    return regions


def checkpoint_shape_errors(blueprint: dict[str, Any]) -> list[str]:
    """Erreurs de forme des frontières de checkpoint.

    Un checkpoint qui désigne un node inexistant ne persiste rien : c'est une
    faute de forme, pas un avertissement.
    """
    errors: list[str] = []
    known = {
        str(n.get("id"))
        for n in blueprint.get("nodes", [])
        if isinstance(n, dict) and n.get("id")
    }
    for region in checkpoint_regions(blueprint):
        rid = region.get("id") or "?"
        if not region["members"]:
            errors.append(f"R-K2 : checkpoint {rid} ne couvre aucun node")
        for member in region["members"]:
            if member not in known:
                errors.append(f"R-K2 : checkpoint {rid} désigne un node inconnu — {member}")
        if region["scope"] not in CHECKPOINT_SCOPES:
            errors.append(
                f"R-K2 : checkpoint {rid} — scope invalide {region['scope']} "
                f"(attendu {' | '.join(CHECKPOINT_SCOPES)})"
            )
    return errors


def suspending_gates(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les portes qui font attendre une personne, donc suspendent le flow.

    Tous les modes humains suspendent au moins parfois : ``sample`` sur la
    fraction échantillonnée, ``escalate-on-uncertainty`` sous son seuil. Un
    flow qui ne sait pas reprendre casse sur ces runs-là, pas sur les autres —
    ce qui est la pire façon de casser, parce que ça n'arrive qu'en vrai.
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or not is_gate(node):
            continue
        if as_dict(gate_policy(node)).get("mode") == "human":
            out.append(node)
    return out


def _predecessors(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    pred: dict[str, set[str]] = {}
    for edge in edges:
        e = as_dict(edge)
        target, source = str(e.get("to", "")), str(e.get("from", ""))
        if target and source:
            pred.setdefault(target, set()).add(source)
    return pred


def _upstream_closure(node_id: str, pred: dict[str, set[str]]) -> set[str]:
    """Tous les ancêtres de `node_id`, cycles compris sans boucler."""
    seen: set[str] = set()
    stack = list(pred.get(node_id, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(pred.get(current, ()))
    return seen


def checkpoints_covering(
    node_id: str, blueprint: dict[str, Any]
) -> list[str]:
    """Ids des checkpoints qui couvrent `node_id`, lui-même ou en amont."""
    edges = blueprint.get("edges", [])
    pred = _predecessors(edges if isinstance(edges, list) else [])
    reachable = {node_id} | _upstream_closure(node_id, pred)
    return [
        str(region["id"])
        for region in checkpoint_regions(blueprint)
        if reachable & set(region["members"])
    ]


def checkpoint_lint(blueprint: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(erreurs, avertissements) — R-K1 en tête.

    R-K1 est une **erreur** et non un avertissement : un flow qui suspend sans
    pouvoir reprendre ne se dégrade pas, il perd le travail déjà fait.
    """
    errors: list[str] = []
    warnings: list[str] = []
    nodes = blueprint.get("nodes", [])
    if not isinstance(nodes, list):
        return errors, warnings

    for gate in suspending_gates(nodes):
        nid = str(gate.get("id", "?"))
        if not checkpoints_covering(nid, blueprint):
            label = gate.get("label") or gate.get("name") or nid
            errors.append(
                f"R-K1 : Gate(human) « {label} » suspend le flow, mais aucun "
                f"checkpoint ne le couvre — la reprise après approbation est "
                f"impossible (node {nid})"
            )

    declared = checkpoint_regions(blueprint)
    if declared and not suspending_gates(nodes):
        warnings.append(
            f"{len(declared)} checkpoint(s) déclaré(s) alors que rien ne suspend "
            f"le flow — persistance sans reprise à assurer"
        )
    return errors, warnings


def compile_checkpoint_section(blueprint: dict[str, Any]) -> list[str]:
    """Section « Reprise » du mission pack : où l'état survit, et pour qui.

    L'hôte exécute ; le blueprint déclare. Cette section dit à l'exécutant où
    persister et quelles portes en dépendent.
    """
    regions = checkpoint_regions(blueprint)
    if not regions:
        return []
    nodes = blueprint.get("nodes", [])
    gates = suspending_gates(nodes if isinstance(nodes, list) else [])
    lines = ["## Reprise", ""]
    for region in regions:
        rid = region.get("id") or "?"
        covered = [
            str(g.get("id"))
            for g in gates
            if rid in checkpoints_covering(str(g.get("id", "")), blueprint)
        ]
        lines.append(f"- **{rid}** — persiste `{region['scope']}` sur {len(region['members'])} node(s)")
        lines.append(
            f"  - portes reprenables : {', '.join(covered) if covered else 'aucune'}"
        )
    lines.append("")
    return lines
