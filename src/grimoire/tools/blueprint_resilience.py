"""Famille résilience — comment un flow échoue, en format (P2.2).

Un système agentique se juge à ses échecs. Sans node neuf (SPEC-failure-edges-
et-evals §1.4-1.6), on rend déclarables et compilables les quatre motifs :

- **retry borné** — node-local (``config.resilience.retry``), pour ne pas
  polluer le graphe ;
- **fallback** — edge ``channel:"failure"`` du pin d'erreur vers un Unit de
  secours ;
- **compensation** — edge ``failure`` vers un Unit d'annulation +
  ``onExhaustion:"compensate"`` ;
- **dead-letter / escalade** — edge ``channel:"escalation"`` terminal vers un
  ``Gate(human)`` ou un ``Reference(signal ALERT)``.

Le plan de défaillance transporte un contrat d'erreur (``error-envelope``),
distinct du plan nominal — c'est ce qui le sépare d'un simple ``Route``. Le
blueprint **déclare** la politique ; l'hôte (orchestrateur, CI) reste
l'exécutant — il ne réessaie rien lui-même.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import DEFAULT_EDGE_CHANNEL, as_dict

ERROR_CONTRACT = "error-envelope"
RETRY_STRATEGIES = ("fixed", "linear", "exponential")
ON_EXHAUSTION = ("escalate", "deadletter", "compensate")
ERROR_CLASSES = (
    "timeout",
    "contract-violation",
    "guardrail-block",
    "budget-exceeded",
    "tool-error",
    "refusal",
    "unknown",
)
RETRY_MAX_CAP = 10  # borne dure : interdit les boucles quasi-libres
_FAILURE_CHANNELS = ("failure", "escalation")


def resilience_policy(node: dict[str, Any]) -> dict[str, Any]:
    """``config.resilience`` d'un node, ou {} si absent/mal formé."""
    config = node.get("config")
    if not isinstance(config, dict):
        return {}
    pol = config.get("resilience")
    return pol if isinstance(pol, dict) else {}


def resilience_shape_errors(node: dict[str, Any]) -> list[str]:
    """Forme de ``config.resilience`` — R-F1 (retry sans max) + enums/bornes."""
    pol = resilience_policy(node)
    if not pol:
        return []
    nid = node.get("id")
    errors: list[str] = []
    unknown = sorted(set(pol) - {"retry", "timeoutMs", "onExhaustion"})
    if unknown:
        errors.append(
            f"config.resilience : clés inconnues {', '.join(unknown)} (node {nid})"
        )
    retry = pol.get("retry")
    if retry is not None:
        if not isinstance(retry, dict):
            errors.append(f"config.resilience.retry invalide (objet attendu) : node {nid}")
        else:
            max_val = retry.get("max")
            # R-F1 : retry sans max ne compile pas (invariant de la boucle bornée).
            if max_val is None:
                errors.append(
                    f"R-F1 : config.resilience.retry sans `max` — borner "
                    f"(ex. max: 2) — node {nid}"
                )
            elif not (
                isinstance(max_val, int)
                and not isinstance(max_val, bool)
                and 1 <= max_val <= RETRY_MAX_CAP
            ):
                errors.append(
                    f"config.resilience.retry.max hors [1,{RETRY_MAX_CAP}] : "
                    f"{max_val} — node {nid}"
                )
            backoff = retry.get("backoffMs")
            if backoff is not None and not (
                isinstance(backoff, int) and not isinstance(backoff, bool) and backoff >= 0
            ):
                errors.append(
                    f"config.resilience.retry.backoffMs invalide (entier >= 0) : node {nid}"
                )
            strategy = retry.get("strategy")
            if strategy is not None and strategy not in RETRY_STRATEGIES:
                errors.append(
                    f"config.resilience.retry.strategy invalide : {strategy} "
                    f"(attendu {' | '.join(RETRY_STRATEGIES)}) — node {nid}"
                )
    timeout = pol.get("timeoutMs")
    if timeout is not None and not (
        isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 1
    ):
        errors.append(
            f"config.resilience.timeoutMs invalide (entier >= 1) : node {nid}"
        )
    on_exh = pol.get("onExhaustion")
    if on_exh is not None and on_exh not in ON_EXHAUSTION:
        errors.append(
            f"config.resilience.onExhaustion invalide : {on_exh} "
            f"(attendu {' | '.join(ON_EXHAUSTION)}) — node {nid}"
        )
    return errors


def _happy_out(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    """node_id -> successeurs sur le canal nominal happy."""
    out: dict[str, set[str]] = {}
    for e in edges:
        if e.get("channel", DEFAULT_EDGE_CHANNEL) != DEFAULT_EDGE_CHANNEL:
            continue
        src = str(e.get("from", "")).split(".")[0]
        dst = str(e.get("to", "")).split(".")[0]
        out.setdefault(src, set()).add(dst)
    return out


def resilience_lint(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """R-F2/R-F4 (bloquants) et R-F3 (avertissement)."""
    errors: list[str] = []
    warnings: list[str] = []
    by_id = {str(n.get("id")): n for n in nodes}
    happy_out = _happy_out(edges)

    for e in edges:
        channel = e.get("channel", DEFAULT_EDGE_CHANNEL)
        if channel not in _FAILURE_CHANNELS:
            continue
        # R-F2 : un edge de défaillance transporte un contrat d'erreur.
        contract = e.get("contract")
        if contract != ERROR_CONTRACT:
            errors.append(
                f"R-F2 : edge {channel} {e.get('from')} -> {e.get('to')} de "
                f"contrat `{contract}` — un plan de défaillance transporte "
                f"`{ERROR_CONTRACT}`"
            )
        # R-F4 : un edge escalation doit être terminal (ne repart pas vers happy).
        if channel == "escalation":
            dst = str(e.get("to", "")).split(".")[0]
            if happy_out.get(dst):
                errors.append(
                    f"R-F4 : escalation vers {dst} non terminal — {dst} repart "
                    f"vers le plan happy ; une escalade doit terminer "
                    f"(dead-letter / Gate(human))"
                )

    # R-F3 : un node externe (effectful) sans chemin de défaillance ni
    # resilience — avertissement (le contenu externe échoue silencieusement).
    failure_srcs = {
        str(e.get("from", "")).split(".")[0]
        for e in edges
        if e.get("channel", DEFAULT_EDGE_CHANNEL) in _FAILURE_CHANNELS
    }
    for nid, n in by_id.items():
        if n.get("kind") != "extension-node":
            continue
        if nid in failure_srcs or resilience_policy(n):
            continue
        warnings.append(
            f"R-F3 : node externe {nid} sans chemin de défaillance ni "
            f"`config.resilience` — suggérer un edge escalation ou un retry borné"
        )
    return errors, warnings


def trace_failure(
    blueprint: dict[str, Any], target: str, error_class: str
) -> dict[str, Any]:
    """Injection d'échec (P3.1) : suit le plan de défaillance depuis *target*.

    What-if de résilience — déterministe. Retrace le chemin réel quand *target*
    échoue sur *error_class* : retry (borné par ``config.resilience``), puis
    fallback (edge ``failure``), puis escalade (edge ``escalation``), puis la
    terminaison ``onExhaustion``. ``path`` liste les nodes traversés (pour
    l'assertion ``path-taken`` des évals).
    """
    nodes = {str(n.get("id")): n for n in blueprint.get("nodes", [])}
    edges = blueprint.get("edges", [])
    steps: list[dict[str, Any]] = []
    notes: list[str] = []
    result: dict[str, Any] = {
        "target": target,
        "class": error_class,
        "valid": target in nodes,
        "steps": steps,
        "path": [],
        "notes": notes,
    }
    if target not in nodes:
        notes.append(f"node cible inconnu : {target}")
        return result
    if error_class not in ERROR_CLASSES:
        notes.append(
            f"classe d'erreur inconnue : {error_class} "
            f"(attendu {' | '.join(ERROR_CLASSES)})"
        )

    path: list[str] = [target]
    pol = resilience_policy(nodes[target])
    retry = as_dict(pol.get("retry"))
    max_attempts = retry.get("max") if isinstance(retry.get("max"), int) else 1
    steps.append(
        {"kind": "attempt", "nodeId": target, "attempts": max_attempts,
         "detail": f"échec {error_class} — {max_attempts} tentative(s)"}
    )

    def _target_via(channel: str) -> str | None:
        for e in edges:
            if (
                str(e.get("from", "")).split(".")[0] == target
                and e.get("channel", DEFAULT_EDGE_CHANNEL) == channel
            ):
                return str(e.get("to", "")).split(".")[0]
        return None

    fallback = _target_via("failure")
    if fallback:
        steps.append({"kind": "fallback", "nodeId": fallback, "via": "failure"})
        path.append(fallback)
    escalation = _target_via("escalation")
    if escalation:
        steps.append({"kind": "escalation", "nodeId": escalation, "via": "escalation"})
        path.append(escalation)
    on_exh = pol.get("onExhaustion")
    if on_exh:
        steps.append({"kind": "onExhaustion", "policy": on_exh})
    if not fallback and not escalation:
        notes.append(
            f"aucun chemin de défaillance depuis {target} — l'échec {error_class} "
            f"n'est pas géré (dead-letter implicite)"
        )
    result["path"] = path
    return result


def compile_resilience_section(
    node: dict[str, Any], edges: list[dict[str, Any]]
) -> list[str]:
    """Section ``on_failure`` d'un node résilient (retry, timeout, plan)."""
    pol = resilience_policy(node)
    nid = str(node.get("id"))
    outgoing = [
        e
        for e in edges
        if str(e.get("from", "")).split(".")[0] == nid
        and e.get("channel", DEFAULT_EDGE_CHANNEL) in _FAILURE_CHANNELS
    ]
    if not pol and not outgoing:
        return []
    lines = ["", "#### Résilience (on_failure)", ""]
    retry = as_dict(pol.get("retry"))
    if retry:
        strat = retry.get("strategy", "fixed")
        backoff = retry.get("backoffMs")
        detail = f", backoff {strat}" + (f" {backoff} ms" if backoff else "")
        lines.append(f"- Retry : réessayer au plus {retry.get('max')} fois{detail}")
    if pol.get("timeoutMs") is not None:
        lines.append(f"- Garde de temps : {pol['timeoutMs']} ms")
    for e in outgoing:
        channel = e.get("channel")
        dst = str(e.get("to", "")).split(".")[0]
        if channel == "failure":
            lines.append(
                f"- En cas d'échec : invoquer `{dst}` avec l'`error-envelope`"
            )
        else:  # escalation
            lines.append(
                f"- Escalade (dead-letter) : router l'`error-envelope` vers `{dst}` "
                f"(checkpoint humain / signal ALERT)"
            )
    on_exh = pol.get("onExhaustion")
    if on_exh:
        term = {
            "escalate": "escalade humaine",
            "deadletter": "dead-letter (arrêt tracé)",
            "compensate": "compensation (annulation d'effet)",
        }
        lines.append(f"- À épuisement du retry : {term.get(on_exh, on_exh)}")
    return lines
