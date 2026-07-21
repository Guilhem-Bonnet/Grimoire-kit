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
