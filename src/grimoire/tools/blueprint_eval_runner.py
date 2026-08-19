"""Exécuter les évals contre une trace d'exécution — enregistrer puis rejouer.

Les suites d'évals étaient déclarables et vérifiables, jamais exécutables :
aucun outil livré ne lisait le format, alors que la compilation en nommait un.

Le modèle retenu sépare l'exécution de la vérification. **L'hôte exécute une
fois** — vrais agents, vrai coût — et enregistre ce qui s'est passé : le
contrat produit, les compteurs de tokens, le verdict, le chemin suivi. **Les
évals vérifient l'enregistrement**, pas une exécution live.

Trois conséquences, et ce sont les raisons du choix :

- l'invariant du Studio tient sans effort — il n'exécute rien, il lit une
  trace produite ailleurs ;
- les évals tournent en intégration continue sans clé d'API, sans coût par
  run et de façon déterministe. Ce qui n'est pas automatique n'est pas fait ;
- ré-enregistrer devient un geste délibéré, comme mettre à jour un snapshot.

Un cas sans entrée dans la trace n'est **pas** un échec : il n'a pas été
exécuté. La distinction est portée jusqu'au rapport, parce que confondre « pas
prouvé » et « réfuté » est précisément ce qu'un système de preuve ne doit pas
faire.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import as_dict
from grimoire.tools.blueprint_evals import (
    BLUEPRINT_SCOPE,
    blueprint_eval_suite,
    evals_suite,
)

__all__ = [
    "RECORD_VERSION",
    "evaluate_case",
    "run_evals",
    "run_record_shape_errors",
]

RECORD_VERSION = 1


def run_record_shape_errors(record: dict[str, Any]) -> list[str]:
    """Erreurs de forme d'une trace d'exécution."""
    errors: list[str] = []
    version = record.get("recordVersion")
    if version != RECORD_VERSION:
        errors.append(
            f"trace : recordVersion attendu {RECORD_VERSION}, trouvé {version!r}"
        )
    runs = record.get("runs")
    if not isinstance(runs, dict):
        return [*errors, "trace : `runs` invalide (objet {scope: {caseId: …}} attendu)"]
    for scope, cases in runs.items():
        if not isinstance(cases, dict):
            errors.append(f"trace : runs[{scope!r}] invalide (objet attendu)")
            continue
        for cid, entry in cases.items():
            if not isinstance(entry, dict):
                errors.append(f"trace : runs[{scope!r}][{cid!r}] invalide (objet attendu)")
    return errors


def _total_tokens(entry: dict[str, Any]) -> int | None:
    tokens = as_dict(entry.get("tokens"))
    values = [tokens.get("input"), tokens.get("output")]
    nums = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return sum(nums) if nums else None


def _is_threshold(value: object) -> bool:
    """Un plafond exploitable : un nombre, et pas un booléen déguisé en 1."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate_case(
    assertions: list[dict[str, Any]], entry: dict[str, Any]
) -> tuple[bool, list[str]]:
    """(réussi, raisons d'échec) d'un cas contre son entrée de trace.

    Fail-closed : une assertion dont la trace ne porte pas l'information
    échoue. Une preuve absente n'est pas une preuve.
    """
    reasons: list[str] = []
    for assertion in assertions:
        a = as_dict(assertion)
        kind = a.get("kind")

        if kind == "contract":
            got = entry.get("contract")
            if got != a.get("contract"):
                reasons.append(
                    f"contrat attendu {a.get('contract')!r}, trace {got!r}"
                )

        elif kind == "cost":
            max_tokens, max_usd = a.get("maxTokens"), a.get("maxUsd")
            # Un plafond illisible ne contraint rien. Le laisser passer rendrait
            # l'éval verte parce qu'elle n'a rien vérifié — le contraire de ce
            # que fait le reste de ce module.
            if not _is_threshold(max_tokens) and not _is_threshold(max_usd):
                reasons.append(
                    f"coût : plafond illisible (maxTokens={max_tokens!r}, "
                    f"maxUsd={max_usd!r}) — l'assertion ne contraint rien"
                )
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
                total = _total_tokens(entry)
                if total is None:
                    reasons.append("coût : la trace ne porte aucun compteur de tokens")
                elif total > max_tokens:
                    reasons.append(f"coût : {total} tokens dépasse le plafond {max_tokens}")
            if isinstance(max_usd, (int, float)) and not isinstance(max_usd, bool):
                usd = entry.get("usd")
                if not isinstance(usd, (int, float)) or isinstance(usd, bool):
                    reasons.append("coût : la trace ne porte aucun montant")
                elif usd > max_usd:
                    reasons.append(f"coût : {usd} $ dépasse le plafond {max_usd} $")

        elif kind == "no-refusal":
            refused = entry.get("refused")
            if refused is not False:
                reasons.append(
                    "refus : la trace ne prouve pas l'absence de refus "
                    f"(refused={refused!r}, `false` attendu)"
                )

        elif kind == "verdict":
            got = entry.get("verdict")
            if got != a.get("expected"):
                reasons.append(f"verdict attendu {a.get('expected')!r}, trace {got!r}")

        elif kind == "path-taken":
            expected = a.get("path")
            got = entry.get("path")
            if got != expected:
                reasons.append(f"chemin attendu {expected!r}, trace {got!r}")

    return (not reasons), reasons


def _suites(blueprint: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Toutes les suites du blueprint, indexées par portée."""
    out: dict[str, dict[str, Any]] = {}
    bp_suite = blueprint_eval_suite(blueprint)
    if bp_suite:
        out[BLUEPRINT_SCOPE] = bp_suite
    nodes = blueprint.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            suite = evals_suite(node)
            if suite:
                out[str(node.get("id"))] = suite
    return out


def run_evals(
    blueprint: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    """Rejoue les évals déclarées contre une trace.

    Retourne ``{"results": {scope: {caseId: bool}}, "details": [...],
    "missing": [...]}``. ``results`` alimente directement
    :func:`grimoire.tools.blueprint_evals.evals_summary` — le rapport existait,
    il attendait un producteur.

    ``missing`` liste les cas déclarés qu'aucune trace ne couvre : non exécutés,
    et surtout pas comptés comme échoués.
    """
    runs = as_dict(record.get("runs"))
    results: dict[str, dict[str, bool]] = {}
    details: list[dict[str, Any]] = []
    missing: list[str] = []

    for scope, suite in _suites(blueprint).items():
        scope_runs = as_dict(runs.get(scope))
        cases = suite.get("cases")
        if not isinstance(cases, list):
            continue
        for case in cases:
            c = as_dict(case)
            cid = str(c.get("id", "?"))
            entry = scope_runs.get(cid)
            if not isinstance(entry, dict):
                missing.append(f"{scope}/{cid}")
                continue
            asserts = [a for a in c.get("assert", []) if isinstance(a, dict)]
            ok, reasons = evaluate_case(asserts, entry)
            results.setdefault(scope, {})[cid] = ok
            details.append({"scope": scope, "case": cid, "passed": ok, "reasons": reasons})

    return {"results": results, "details": details, "missing": sorted(missing)}
