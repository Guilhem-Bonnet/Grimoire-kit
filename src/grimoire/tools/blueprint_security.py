"""Verdict de sécurité — surface d'attaque agrégée (P2.4).

`Gate(output-contract)` valide un schéma ; il ne protège de rien côté sécurité.
La couche guardrails (famille de `Gate`, §4.E) est déjà déclarable et lintée par
:mod:`grimoire.tools.blueprint_gate` (R-G1 accès externe sans `mcp-trust`, R-G2
contenu externe atteignant une sortie sans guardrail d'entrée, R-G5 sortie sans
guardrail de sortie). Ce module ne relinte rien : il **agrège** ces signaux en
une *vue de synthèse* — les points d'entrée (surface d'attaque), les points de
sortie, les points de filtrage déclarés, et les expositions résiduelles.

Source de vérité unique : on réutilise les helpers de graphe et de politique de
:mod:`blueprint_gate`, donc le verdict ne peut pas diverger des règles qui
refusent la compilation. Le blueprint **déclare** la surface ; l'hôte reste
l'exécutant des filtres.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import as_dict
from grimoire.tools.blueprint_gate import (
    _happy_graph,
    _reaches_exit_unguarded,
    _upstream,
    gate_policy,
)


def _guardrails(
    by_id: dict[str, dict[str, Any]], direction: str
) -> set[str]:
    return {
        nid
        for nid, n in by_id.items()
        if gate_policy(n).get("mode") == "guardrail"
        and as_dict(gate_policy(n).get("params")).get("direction") == direction
    }


def security_verdict(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> dict[str, Any]:
    """Synthèse de la surface d'attaque (P2.4) — agrège les signaux de gate.

    Retourne ``entryPoints`` (sources externes + couverture mcp-trust/guardrail
    d'entrée), ``exitPoints`` (sorties + couverture guardrail de sortie),
    ``filterPoints`` (guardrails et mcp-trust déclarés), ``exposures`` (mêmes
    règles bloquantes que ``gate_lint`` : R-G1/R-G2, plus R-G5 en info) et un
    ``verdict`` global. Cohérent par construction avec ``gate_lint``.
    """
    succ, pred = _happy_graph(nodes, edges)
    by_id = {str(n.get("id")): n for n in nodes}
    externals = [nid for nid, n in by_id.items() if n.get("kind") == "extension-node"]
    mcp_gates = {nid for nid, n in by_id.items() if gate_policy(n).get("mode") == "mcp-trust"}
    guardrails_in = _guardrails(by_id, "in")
    guardrails_out = _guardrails(by_id, "out")
    exits = [nid for nid, s in succ.items() if not s]

    entry_points: list[dict[str, Any]] = []
    exposures: list[dict[str, Any]] = []
    for ext in sorted(externals):
        trust_gate = bool(_upstream(ext, pred) & mcp_gates)
        reaches_unguarded = _reaches_exit_unguarded(ext, succ, guardrails_in)
        entry_points.append(
            {"id": ext, "kind": "extension-node",
             "trustGate": trust_gate, "inputGuardrail": not reaches_unguarded}
        )
        if not trust_gate:
            exposures.append(
                {"rule": "R-G1", "node": ext, "severity": "blocking",
                 "detail": "accès externe sans Gate(mcp-trust) en amont"}
            )
        if reaches_unguarded:
            exposures.append(
                {"rule": "R-G2", "node": ext, "severity": "blocking",
                 "detail": "contenu externe atteint une sortie sans guardrail d'entrée"}
            )

    exit_points: list[dict[str, Any]] = []
    for xid in sorted(exits):
        covered = xid in guardrails_out or bool(_upstream(xid, pred) & guardrails_out)
        exit_points.append({"id": xid, "outputGuardrail": covered})
        if externals and not covered:
            exposures.append(
                {"rule": "R-G5", "node": xid, "severity": "info",
                 "detail": "sortie sans Gate(guardrail, out) en amont"}
            )

    filter_points: list[dict[str, Any]] = []
    for nid in sorted(by_id):
        pol = gate_policy(by_id[nid])
        mode = pol.get("mode")
        if mode == "mcp-trust":
            filter_points.append({"id": nid, "type": "mcp-trust"})
        elif mode == "guardrail":
            params = as_dict(pol.get("params"))
            direction = params.get("direction")
            filter_points.append(
                {"id": nid, "type": f"guardrail-{direction}",
                 "checks": params.get("checks") or []}
            )

    blocking = [e for e in exposures if e["severity"] == "blocking"]
    return {
        "entryPoints": entry_points,
        "exitPoints": exit_points,
        "filterPoints": filter_points,
        "exposures": exposures,
        "verdict": "exposed" if blocking else "secure",
        "counts": {
            "attackSurface": len(externals) + len(exits),
            "filterPoints": len(filter_points),
            "blockingExposures": len(blocking),
        },
    }


def compile_security_section(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[str]:
    """Section « Surface d'attaque » du mission pack (P2.4).

    Documente, dans l'artefact compilé, les frontières de confiance et les
    points de filtrage déclarés. N'apparaît que si le flow a une surface
    (source externe ou point de filtrage) — les flows sans frontière restent
    inchangés.
    """
    verdict = security_verdict(nodes, edges)
    if not verdict["entryPoints"] and not verdict["filterPoints"]:
        return []
    icon = "OK" if verdict["verdict"] == "secure" else "EXPOSÉ"
    lines = ["", "### Surface d'attaque (sécurité)", "", f"- Verdict : **{icon}**"]
    for ep in verdict["entryPoints"]:
        marks = []
        marks.append("mcp-trust" if ep["trustGate"] else "SANS mcp-trust")
        marks.append("guardrail entrée" if ep["inputGuardrail"] else "SANS guardrail entrée")
        lines.append(f"- Entrée externe `{ep['id']}` : {', '.join(marks)}")
    for fp in verdict["filterPoints"]:
        checks = fp.get("checks")
        suffix = f" ({', '.join(checks)})" if checks else ""
        lines.append(f"- Filtre `{fp['id']}` : {fp['type']}{suffix}")
    for exp in verdict["exposures"]:
        if exp["severity"] == "blocking":
            lines.append(f"- **{exp['rule']}** sur `{exp['node']}` : {exp['detail']}")
    return lines
