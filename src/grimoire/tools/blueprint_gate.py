"""Gate universel paramétré (P2.1) — une primitive, six modes.

Un ``Gate`` n'a jamais de sémantique de transformation : il **assère une
précondition** et gouverne le flux (SPEC-gate-universel). La présence de
``config.gate`` dérive ``role: "Gate"``. Le rejet réutilise les edges typés de
P0.2 : ``onReject`` route vers un edge ``escalation``, un edge ``failure``, ou
arrête net (``block``).

Un seul compilateur (:func:`compile_gate_section`) : ajouter un mode = une
branche du switch + un cas de test, pas un nouveau bestiaire.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from grimoire.tools.blueprint_context import DEFAULT_EDGE_CHANNEL, as_dict

GATE_MODES = (
    "human",
    "budget",
    "evidence",
    "output-contract",
    "guardrail",
    "mcp-trust",
)
ON_REJECT = ("escalation", "failure", "block")
DEFAULT_ON_REJECT = "block"
HUMAN_ACTIONS = ("approve", "edit", "input", "sample", "escalate-on-uncertainty")
BUDGET_SCOPES = ("node", "segment", "flow")
GUARDRAIL_DIRECTIONS = ("in", "out")

# Modes dont un rejet en ``block`` sans alternative mérite un avertissement
# (R-G3) : un humain, un budget ou un serveur non fiable ont presque toujours
# un plan B raisonnable.
_RG3_MODES = ("human", "budget", "mcp-trust")


def gate_policy(node: dict[str, Any]) -> dict[str, Any]:
    """``config.gate`` d'un node, ou {} si absent/mal formé."""
    config = node.get("config")
    if not isinstance(config, dict):
        return {}
    gate = config.get("gate")
    return gate if isinstance(gate, dict) else {}


def is_gate(node: dict[str, Any]) -> bool:
    return bool(gate_policy(node))


def gate_shape_errors(
    node: dict[str, Any],
    *,
    resolve_schema: Callable[[str], bool] | None = None,
) -> list[str]:
    """Erreurs de forme de ``config.gate`` (mode, onReject, params) + R-G4."""
    gate = gate_policy(node)
    if not gate:
        return []
    nid = node.get("id")
    errors: list[str] = []
    unknown = sorted(set(gate) - {"mode", "onReject", "params"})
    if unknown:
        errors.append(f"config.gate : clés inconnues {', '.join(unknown)} (node {nid})")
    mode = gate.get("mode")
    if mode not in GATE_MODES:
        errors.append(
            f"config.gate.mode invalide : {mode} "
            f"(attendu {' | '.join(GATE_MODES)}) — node {nid}"
        )
        return errors
    on_reject = gate.get("onReject", DEFAULT_ON_REJECT)
    if on_reject not in ON_REJECT:
        errors.append(
            f"config.gate.onReject invalide : {on_reject} "
            f"(attendu {' | '.join(ON_REJECT)}) — node {nid}"
        )
    # La présence de config.gate dérive role: Gate — un role contradictoire
    # est une erreur de forme.
    role = node.get("role")
    if role is not None and role != "Gate":
        errors.append(
            f"config.gate présent mais role={role} — un node gate a role Gate "
            f"(node {nid})"
        )
    params = as_dict(gate.get("params"))
    if mode == "human":
        action = params.get("action", "approve")
        if action not in HUMAN_ACTIONS:
            errors.append(
                f"Gate(human) : action invalide {action} "
                f"(attendu {' | '.join(HUMAN_ACTIONS)}) — node {nid}"
            )
        threshold = params.get("confidenceThreshold")
        if threshold is not None and not (
            isinstance(threshold, int | float)
            and not isinstance(threshold, bool)
            and 0 <= threshold <= 1
        ):
            errors.append(
                f"Gate(human) : confidenceThreshold hors [0,1] — node {nid}"
            )
        if action == "sample":
            pct = params.get("pct")
            if not (
                isinstance(pct, int | float)
                and not isinstance(pct, bool)
                and 0 < pct <= 100
            ):
                errors.append(
                    f"Gate(human, sample) : params.pct requis (0 < pct <= 100) "
                    f"— node {nid}"
                )
    elif mode == "budget":
        scope = params.get("scope")
        if scope not in BUDGET_SCOPES:
            errors.append(
                f"Gate(budget) : scope requis ({' | '.join(BUDGET_SCOPES)}) "
                f"— node {nid}"
            )
        if params.get("maxTokens") is None and params.get("maxUsd") is None:
            errors.append(
                f"Gate(budget) : maxTokens ou maxUsd requis — node {nid}"
            )
    elif mode == "evidence":
        require = params.get("require")
        if not (isinstance(require, list) and require):
            errors.append(
                f"Gate(evidence) : params.require (liste non vide) requis "
                f"— node {nid}"
            )
    elif mode == "output-contract":
        schema = params.get("schema")
        if not (isinstance(schema, str) and schema):
            errors.append(
                f"Gate(output-contract) : params.schema requis — node {nid}"
            )
        elif resolve_schema is not None and not resolve_schema(schema):
            errors.append(
                f"R-G4 : Gate(output-contract) — params.schema ne résout pas "
                f"({schema}) — node {nid}"
            )
    elif mode == "guardrail":
        direction = params.get("direction")
        if direction not in GUARDRAIL_DIRECTIONS:
            errors.append(
                f"Gate(guardrail) : direction requise (in | out) — node {nid}"
            )
        checks = params.get("checks")
        if not (isinstance(checks, list) and checks):
            errors.append(
                f"Gate(guardrail) : params.checks (liste non vide) requis "
                f"— node {nid}"
            )
    elif mode == "mcp-trust":
        if not (isinstance(params.get("server"), str) and params.get("server")):
            errors.append(
                f"Gate(mcp-trust) : params.server requis — node {nid}"
            )
    return errors


def _happy_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Adjacence (successeurs, prédécesseurs) sur le canal nominal happy."""
    ids = {str(n.get("id")) for n in nodes}
    succ: dict[str, set[str]] = {i: set() for i in ids}
    pred: dict[str, set[str]] = {i: set() for i in ids}
    for e in edges:
        if e.get("channel", DEFAULT_EDGE_CHANNEL) != DEFAULT_EDGE_CHANNEL:
            continue
        src = str(e.get("from", "")).split(".")[0]
        dst = str(e.get("to", "")).split(".")[0]
        if src in ids and dst in ids:
            succ[src].add(dst)
            pred[dst].add(src)
    return succ, pred


def _upstream(node_id: str, pred: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(pred.get(node_id, ()))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(pred.get(cur, ()))
    return seen


def _reaches_exit_unguarded(
    start: str,
    succ: dict[str, set[str]],
    guards: set[str],
) -> bool:
    """Vrai si un exit est atteignable depuis *start* sans traverser *guards*."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        nxt = succ.get(cur, set())
        if not nxt:
            return True  # exit atteint sans garde
        for n in nxt:
            if n not in guards:
                stack.append(n)
    return False


def gate_lint(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Règles R-G1/R-G2 (bloquantes) et R-G3/R-G5 (non bloquantes).

    Frontière de confiance (4.E) : la surface d'attaque ne peut plus exister
    sans point de filtrage déclaré.
    """
    errors: list[str] = []
    warnings: list[str] = []
    succ, pred = _happy_graph(nodes, edges)
    by_id = {str(n.get("id")): n for n in nodes}
    externals = [nid for nid, n in by_id.items() if n.get("kind") == "extension-node"]
    mcp_gates = {
        nid for nid, n in by_id.items() if gate_policy(n).get("mode") == "mcp-trust"
    }
    guardrails_in = {
        nid
        for nid, n in by_id.items()
        if gate_policy(n).get("mode") == "guardrail"
        and as_dict(gate_policy(n).get("params")).get("direction") == "in"
    }
    guardrails_out = {
        nid
        for nid, n in by_id.items()
        if gate_policy(n).get("mode") == "guardrail"
        and as_dict(gate_policy(n).get("params")).get("direction") == "out"
    }

    for ext in externals:
        # R-G1 : accès externe sans Gate(mcp-trust) en amont.
        if not (_upstream(ext, pred) & mcp_gates):
            errors.append(
                f"R-G1 : node externe {ext} sans Gate(mcp-trust) en amont — "
                f"insérer un gate mcp-trust"
            )
        # R-G2 : contenu externe qui atteint une sortie sans guardrail d'entrée.
        if _reaches_exit_unguarded(ext, succ, guardrails_in):
            errors.append(
                f"R-G2 : le contenu externe de {ext} atteint une sortie sans "
                f"Gate(guardrail, in) intermédiaire — insérer un guardrail"
            )

    # R-G3 : rejet en block sans alternative sur les modes à plan B naturel.
    reject_channels = {"failure", "escalation"}
    outgoing_reject: dict[str, set[str]] = {}
    for e in edges:
        channel = e.get("channel", DEFAULT_EDGE_CHANNEL)
        if channel in reject_channels:
            src = str(e.get("from", "")).split(".")[0]
            outgoing_reject.setdefault(src, set()).add(str(channel))
    for nid, n in by_id.items():
        gate = gate_policy(n)
        if not gate:
            continue
        on_reject = gate.get("onReject", DEFAULT_ON_REJECT)
        if (
            gate.get("mode") in _RG3_MODES
            and on_reject == "block"
            and not outgoing_reject.get(nid)
        ):
            warnings.append(
                f"R-G3 : Gate({gate.get('mode')}) {nid} en onReject:block sans "
                f"alternative — suggérer un edge escalation"
            )

    # R-G5 (information) : flux avec entrée externe dont une sortie n'est pas
    # couverte par un Gate(guardrail, out) en amont.
    if externals:
        exits = [nid for nid, s in succ.items() if not s]
        for exit_id in exits:
            if exit_id in guardrails_out:
                continue
            if not (_upstream(exit_id, pred) & guardrails_out):
                warnings.append(
                    f"R-G5 : sortie {exit_id} sans Gate(guardrail, out) en "
                    f"amont — suggérer un guardrail sortie"
                )
    return errors, warnings


def compile_gate_section(node: dict[str, Any]) -> list[str]:
    """Sous-section « Gate » d'un step compilé — le switch unique (SPEC §5)."""
    gate = gate_policy(node)
    if not gate:
        return []
    mode = str(gate.get("mode"))
    on_reject = str(gate.get("onReject", DEFAULT_ON_REJECT))
    params = as_dict(gate.get("params"))
    lines = ["", f"#### Gate ({mode})", ""]
    if mode == "human":
        action = params.get("action", "approve")
        lines.append(f"- Checkpoint d'escalade humaine (GOV-15) : action `{action}`")
        if params.get("approvers"):
            lines.append(f"- Approbateurs : {', '.join(params['approvers'])}")
        if params.get("confidenceThreshold") is not None:
            lines.append(
                f"- Ne bloque que si la confiance < {params['confidenceThreshold']}"
            )
        if action == "sample" and params.get("pct") is not None:
            lines.append(f"- Échantillonnage : {params['pct']} % des runs revus")
    elif mode == "budget":
        caps = []
        if params.get("maxTokens") is not None:
            caps.append(f"{params['maxTokens']} tokens")
        if params.get("maxUsd") is not None:
            caps.append(f"{params['maxUsd']} $")
        lines.append(
            f"- Enforcement de budget (token-budget) : plafond {' / '.join(caps)} "
            f"sur scope `{params.get('scope')}` — source /api/cost-model"
        )
    elif mode == "evidence":
        lines.append(
            f"- Evidence-pack exigé (QUA-04) : {', '.join(params.get('require', []))}"
        )
    elif mode == "output-contract":
        lines.append(
            f"- Validation de sortie (QUA-14) contre `{params.get('schema')}`"
        )
    elif mode == "guardrail":
        lines.append(
            f"- Filtre {params.get('direction')} (guardrails) : "
            f"{', '.join(params.get('checks', []))}"
        )
    elif mode == "mcp-trust":
        lines.append(
            f"- MCP Trust Gate (GOV-09) : serveur `{params.get('server')}`, "
            f"outils {params.get('allowedTools', [])}, "
            f"permissions {params.get('permissions', [])}"
        )
    reject_line = {
        "escalation": "- Rejet : route via l'edge `escalation` (error-envelope)",
        "failure": "- Rejet : route via l'edge `failure` (error-envelope)",
        "block": "- Rejet : arrêt dur — le flow se termine sur ce gate",
    }
    lines.append(reject_line[on_reject])
    return lines
