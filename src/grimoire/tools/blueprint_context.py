"""Politique de contexte du blueprint — constantes et helpers de forme.

Extrait de :mod:`grimoire.tools.forge_server` pour garder ce module sous son
seuil de taille (cliquet R2). Regroupe la surface « ingénierie de contexte » :
constantes du modèle de pression (tranche C1), canaux d'edge (P0.2) et les
helpers de lecture / validation de forme de ``config.context``.
"""

from __future__ import annotations

from typing import Any

# Ingénierie de contexte (tranche C1) : politique déclarative par node
# (`config.context`), validée, lintée, simulée et compilée. Constantes du
# modèle de pression — volontairement simples, calibrables par P2.3.
CONTEXT_WINDOW_TOKENS = 200_000
NODE_BASE_TOKENS = 8_000
DIGEST_TOKENS = 2_000
DIGEST_CONTRACTS = ("handoff-packet", "context-pack")
CONTEXT_TIERS = ("tiny", "small", "medium", "deep")
COMPACTION_STRATEGIES = ("digest", "selective", "index-guided", "full")
# Canaux d'edge (P0.2) : chemin nominal, chemins d'échec et d'escalade.
# Absence == ``happy`` — les blueprints existants migrent sans perte.
EDGE_CHANNELS = ("happy", "failure", "escalation")
DEFAULT_EDGE_CHANNEL = "happy"
ISOLATION_MODES = ("shared", "isolated")
# Régions d'isolation (C3) : une Boundary transversale groupe plusieurs nodes
# dans une même fenêtre quarantinée (patron orchestrateur-worker). Le cas
# dégénéré à un node est l'isolation de node C1.
BOUNDARY_MODES = ("isolation",)


def isolation_regions(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    """Régions d'isolation déclarées (``boundaries`` top-level, mode isolation).

    Chaque région : ``{id, mode, members}``. Additif — un blueprint sans
    ``boundaries`` n'a aucune région (comportement inchangé).
    """
    regions: list[dict[str, Any]] = []
    for b in blueprint.get("boundaries", []):
        if not isinstance(b, dict) or b.get("mode") != "isolation":
            continue
        members = [m for m in b.get("members", []) if isinstance(m, str)]
        regions.append({"id": b.get("id"), "mode": "isolation", "members": members})
    return regions


def region_membership(regions: list[dict[str, Any]]) -> dict[str, str]:
    """Carte ``node_id -> region_id`` (première région qui contient le node)."""
    membership: dict[str, str] = {}
    for region in regions:
        rid = str(region.get("id"))
        for nid in region.get("members", []):
            membership.setdefault(nid, rid)
    return membership


def context_policy(node: dict[str, Any]) -> dict[str, Any]:
    """`config.context` d'un node, ou {} si absent/mal formé (lint tolérant)."""
    config = node.get("config")
    if not isinstance(config, dict):
        return {}
    ctx = config.get("context")
    return ctx if isinstance(ctx, dict) else {}


def as_dict(value: Any) -> dict[str, Any]:
    """`value` si c'est un dict, sinon {} — narrowing pour mypy strict."""
    return value if isinstance(value, dict) else {}


def context_shape_errors(node: dict[str, Any]) -> list[str]:
    """Erreurs de forme de `config.context` (enums, types) + règle R-C4."""
    errors: list[str] = []
    nid = node.get("id")
    config = node.get("config")
    if config is None:
        return errors
    if not isinstance(config, dict):
        return [f"config invalide (objet attendu) : node {nid}"]
    ctx = config.get("context")
    if ctx is None:
        return errors
    if not isinstance(ctx, dict):
        return [f"config.context invalide (objet attendu) : node {nid}"]
    unknown = sorted(set(ctx) - {"budget", "compaction", "isolation"})
    if unknown:
        errors.append(
            f"config.context : clés inconnues {', '.join(unknown)} (node {nid})"
        )
    isolation = ctx.get("isolation")
    if isolation is not None and isolation not in ISOLATION_MODES:
        errors.append(
            f"config.context.isolation invalide : {isolation} "
            f"(attendu {' | '.join(ISOLATION_MODES)}) — node {nid}"
        )
    budget = ctx.get("budget")
    if budget is not None and not isinstance(budget, dict):
        errors.append(f"config.context.budget invalide (objet attendu) : node {nid}")
    elif isinstance(budget, dict):
        tier = budget.get("tier")
        if tier is not None and tier not in CONTEXT_TIERS:
            errors.append(
                f"config.context.budget.tier invalide : {tier} "
                f"(attendu {' | '.join(CONTEXT_TIERS)}) — node {nid}"
            )
        max_tokens = budget.get("maxTokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens < 1
        ):
            errors.append(
                f"config.context.budget.maxTokens invalide "
                f"(entier >= 1 attendu) : node {nid}"
            )
        elif isinstance(max_tokens, int) and max_tokens > CONTEXT_WINDOW_TOKENS:
            errors.append(
                f"R-C4 : budget.maxTokens ({max_tokens}) dépasse la fenêtre du "
                f"modèle cible ({CONTEXT_WINDOW_TOKENS} tokens) — node {nid}"
            )
        justification = budget.get("justification")
        if justification is not None and not isinstance(justification, str):
            errors.append(
                f"config.context.budget.justification invalide "
                f"(chaîne attendue) : node {nid}"
            )
    compaction = ctx.get("compaction")
    if compaction is not None and not isinstance(compaction, dict):
        errors.append(
            f"config.context.compaction invalide (objet attendu) : node {nid}"
        )
    elif isinstance(compaction, dict):
        strategy = compaction.get("strategy")
        if strategy is not None and strategy not in COMPACTION_STRATEGIES:
            errors.append(
                f"config.context.compaction.strategy invalide : {strategy} "
                f"(attendu {' | '.join(COMPACTION_STRATEGIES)}) — node {nid}"
            )
        contract = compaction.get("digestContract")
        if contract is not None and contract not in DIGEST_CONTRACTS:
            errors.append(
                f"config.context.compaction.digestContract invalide : {contract} "
                f"(attendu {' | '.join(DIGEST_CONTRACTS)}) — node {nid}"
            )
    return errors


def context_section(node: dict[str, Any]) -> list[str]:
    """Sous-section « Contexte » d'un step compilé (C1, texte stable).

    Chaque déclaration mappe une pour une sur un mécanisme que l'hôte exécute
    déjà : stratégies ``discover_inputs`` du moteur de workflow (FULL_LOAD,
    SELECTIVE_LOAD, INDEX_GUIDED), handoff digest ORC-03 et capsule minimale
    d'injection subagent.
    """
    ctx = context_policy(node)
    if not ctx:
        return []
    budget = as_dict(ctx.get("budget"))
    compaction = as_dict(ctx.get("compaction"))
    tier = budget.get("tier", "medium")
    max_tokens = budget.get("maxTokens")
    strategy = compaction.get("strategy", "full")
    contract = compaction.get("digestContract", "handoff-packet")
    directives = {
        "digest": f"produire un `{contract}` (ORC-03) avant de passer la main",
        "selective": "chargement `SELECTIVE_LOAD` (variables ciblées) "
        "du moteur de workflow",
        "index-guided": "chargement `INDEX_GUIDED` (index puis shards pertinents)",
        "full": "chargement `FULL_LOAD` (contexte amont complet)",
    }
    lines = ["", "#### Contexte", ""]
    budget_line = f"- Budget : tier `{tier}`"
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
        budget_line += f", plafond {max_tokens} tokens"
    lines.append(budget_line)
    if budget.get("justification"):
        lines.append(f"- Justification du tier : {budget['justification']}")
    lines.append(f"- Compaction : {directives.get(strategy, directives['full'])}")
    if ctx.get("isolation") == "isolated":
        lines.append(
            "- Isolation : dispatch en sous-agent à capsule minimale ; "
            f"retour exclusivement via le contrat `{contract}`"
        )
    return lines
