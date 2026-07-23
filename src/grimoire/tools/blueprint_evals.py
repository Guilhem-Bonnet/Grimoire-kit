"""Évals comportementaux first-class (P1.2) — preuve attachée au graphe.

Les tests de blueprint valident la *structure* (« tout chemin vers déploiement
passe par une porte QUA »). Nécessaire, insuffisant : ça ne dit rien du
*comportement* des agents compilés. La grille 2026 exige une suite d'évals
attachée au flow ou au node — cas d'entrée + assertion sur la sortie.

Le Studio ne sait toujours pas exécuter (invariant §1) : il **attache** la
preuve comportementale, la **valide**, en affiche le **taux de réussite** par
node, et la **compile** en checks que l'hôte (``agent-test``, ``standard gate``)
exécute. Une suite s'attache à un ``Unit`` via ``config.evals`` ou au blueprint
entier via ``evals`` top-level ; elle est versionnée avec le ``.blueprint.json``.

Cinq genres d'assertion : ``contract`` (la sortie honore un contrat nommé),
``cost`` (sous un seuil de tokens/coût), ``no-refusal`` (l'agent ne refuse pas),
``verdict`` (verdict attendu) et ``path-taken`` — sous injection d'échec, le
chemin réellement suivi ; ce dernier se recoupe avec le plan de défaillance
déclaré (P3.1) et le lint signale toute divergence.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_context import as_dict
from grimoire.tools.blueprint_resilience import ERROR_CLASSES, trace_failure

EVAL_ASSERTION_KINDS = ("contract", "cost", "no-refusal", "verdict", "path-taken")
VERDICT_VALUES = ("pass", "fail", "partial", "block")
BLUEPRINT_SCOPE = "__blueprint__"  # clé de résumé pour les évals top-level


def evals_suite(node: dict[str, Any]) -> dict[str, Any]:
    """`config.evals` d'un node, ou {} si absent/mal formé (lint tolérant)."""
    config = node.get("config")
    if not isinstance(config, dict):
        return {}
    suite = config.get("evals")
    return suite if isinstance(suite, dict) else {}


def blueprint_eval_suite(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Suite d'évals top-level (`evals`), ou {} si absente/mal formée."""
    suite = blueprint.get("evals")
    return suite if isinstance(suite, dict) else {}


def _assertion_errors(assertion: Any, where: str) -> list[str]:
    """Forme d'une assertion unique — genre connu + champs du genre."""
    if not isinstance(assertion, dict):
        return [f"{where} : assertion invalide (objet attendu)"]
    kind = assertion.get("kind")
    if kind not in EVAL_ASSERTION_KINDS:
        return [
            f"{where} : genre d'assertion invalide {kind!r} "
            f"(attendu {' | '.join(EVAL_ASSERTION_KINDS)})"
        ]
    errors: list[str] = []
    if kind == "contract":
        if not isinstance(assertion.get("contract"), str):
            errors.append(f"{where} : assertion `contract` sans `contract` (chaîne)")
    elif kind == "cost":
        max_tokens = assertion.get("maxTokens")
        max_usd = assertion.get("maxUsd")
        tokens_ok = isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens >= 1
        usd_ok = isinstance(max_usd, (int, float)) and not isinstance(max_usd, bool) and max_usd > 0
        if not (tokens_ok or usd_ok):
            errors.append(
                f"{where} : assertion `cost` sans seuil valide "
                f"(`maxTokens` entier >= 1 ou `maxUsd` > 0)"
            )
    elif kind == "verdict":
        expected = assertion.get("expected")
        if expected not in VERDICT_VALUES:
            errors.append(
                f"{where} : assertion `verdict.expected` invalide {expected!r} "
                f"(attendu {' | '.join(VERDICT_VALUES)})"
            )
    elif kind == "path-taken":
        inject = as_dict(assertion.get("inject"))
        if not isinstance(inject.get("node"), str):
            errors.append(f"{where} : assertion `path-taken.inject.node` (chaîne) manquant")
        if inject.get("class") not in ERROR_CLASSES:
            errors.append(
                f"{where} : assertion `path-taken.inject.class` invalide "
                f"{inject.get('class')!r} (attendu {' | '.join(ERROR_CLASSES)})"
            )
        path = assertion.get("path")
        if not (isinstance(path, list) and all(isinstance(p, str) for p in path)):
            errors.append(f"{where} : assertion `path-taken.path` (liste de chaînes) manquante")
    return errors


def suite_shape_errors(suite: dict[str, Any], scope: str) -> list[str]:
    """Forme d'une suite d'évals : version, cas, id uniques, assertions.

    *scope* nomme l'origine dans les messages (``node n2`` ou ``blueprint``).
    """
    if not suite:
        return []
    errors: list[str] = []
    cases = suite.get("cases")
    if not isinstance(cases, list):
        return [f"evals ({scope}) : `cases` invalide (liste attendue)"]
    # R-E1 : une suite non vide est versionnée (traçabilité de la preuve).
    if cases and not isinstance(suite.get("version"), str):
        errors.append(f"R-E1 : evals ({scope}) sans `version` (chaîne) — versionner la suite")
    unknown_keys = sorted(set(suite) - {"version", "cases"})
    if unknown_keys:
        errors.append(f"evals ({scope}) : clés inconnues {', '.join(unknown_keys)}")
    seen_ids: set[str] = set()
    for i, case in enumerate(cases):
        where = f"evals ({scope}) cas #{i}"
        if not isinstance(case, dict):
            errors.append(f"{where} : cas invalide (objet attendu)")
            continue
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"{where} : `id` (chaîne non vide) manquant")
        elif cid in seen_ids:
            errors.append(f"{where} : `id` dupliqué {cid!r}")
        else:
            seen_ids.add(cid)
        if not isinstance(case.get("input"), dict):
            errors.append(f"{where} ({cid}) : `input` (objet) manquant")
        asserts = case.get("assert")
        if not (isinstance(asserts, list) and asserts):
            errors.append(f"{where} ({cid}) : `assert` (liste non vide) manquant")
            continue
        for assertion in asserts:
            errors.extend(_assertion_errors(assertion, f"{where} ({cid})"))
    return errors


def evals_shape_errors(node: dict[str, Any]) -> list[str]:
    """Forme de `config.evals` d'un node (délègue à :func:`suite_shape_errors`)."""
    config = node.get("config")
    if isinstance(config, dict) and config.get("evals") is not None and not isinstance(
        config.get("evals"), dict
    ):
        return [f"config.evals invalide (objet attendu) : node {node.get('id')}"]
    return suite_shape_errors(evals_suite(node), f"node {node.get('id')}")


def _path_taken_assertions(
    suite: dict[str, Any], default_target: str | None
) -> list[tuple[str, str, str, list[str]]]:
    """(case_id, inject_node, inject_class, expected_path) des assertions path-taken.

    *default_target* : node porteur d'une suite node-level (cible implicite si
    ``inject.node`` absent). None pour une suite blueprint-level.
    """
    out: list[tuple[str, str, str, list[str]]] = []
    for case in suite.get("cases", []):
        if not isinstance(case, dict):
            continue
        for assertion in case.get("assert", []):
            if not isinstance(assertion, dict) or assertion.get("kind") != "path-taken":
                continue
            inject = as_dict(assertion.get("inject"))
            node = inject.get("node") or default_target
            klass = inject.get("class")
            path = assertion.get("path")
            if isinstance(node, str) and isinstance(klass, str) and isinstance(path, list):
                out.append((str(case.get("id")), node, klass, [str(p) for p in path]))
    return out


def evals_lint(blueprint: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Erreurs bloquantes (forme) et avertissements (preuve manquante, R-E3).

    R-E3 (avertissement) : une assertion ``path-taken`` dont le chemin attendu
    diverge du plan de défaillance déclaré (``trace_failure``, P3.1) — la preuve
    contredit le graphe, l'un des deux ment.
    """
    errors: list[str] = []
    warnings: list[str] = []
    nodes = blueprint.get("nodes", [])

    bp_suite = blueprint_eval_suite(blueprint)
    if blueprint.get("evals") is not None and not isinstance(blueprint.get("evals"), dict):
        errors.append("evals (blueprint) invalide (objet attendu)")
    errors.extend(suite_shape_errors(bp_suite, "blueprint"))

    covered: set[str] = set()
    for n in nodes:
        if evals_suite(n):
            covered.add(str(n.get("id")))

    # R-E2 (avertissement) : node externe effectful sans aucune éval — son
    # comportement n'est pas prouvé (ni node-level, ni ciblé par le blueprint).
    bp_targets = {node for _, node, _, _ in _path_taken_assertions(bp_suite, None)}
    for n in nodes:
        if n.get("kind") != "extension-node":
            continue
        nid = str(n.get("id"))
        if nid in covered or nid in bp_targets:
            continue
        warnings.append(
            f"R-E2 : node externe {nid} sans éval — comportement non prouvé "
            f"(attacher `config.evals` ou une éval blueprint qui le cible)"
        )

    # R-E3 : cohérence path-taken vs plan de défaillance déclaré (P3.1).
    checks = list(_path_taken_assertions(bp_suite, None))
    for n in nodes:
        checks.extend(_path_taken_assertions(evals_suite(n), str(n.get("id"))))
    for cid, node, klass, expected in checks:
        traced = trace_failure(blueprint, node, klass)
        if not traced["valid"]:
            errors.append(
                f"evals : assertion path-taken ({cid}) cible un node inconnu {node!r}"
            )
            continue
        if traced["path"] != expected:
            warnings.append(
                f"R-E3 : path-taken ({cid}) attend {expected} mais le plan de "
                f"défaillance déclaré suit {traced['path']} — aligner l'éval ou "
                f"le graphe (node {node}, échec {klass})"
            )
    return errors, warnings


def _suite_case_count(suite: dict[str, Any]) -> int:
    cases = suite.get("cases")
    return len(cases) if isinstance(cases, list) else 0


def _scope_summary(
    suite: dict[str, Any], results: dict[str, Any] | None
) -> dict[str, Any]:
    """Résumé d'une suite : total déclaré, exécutés, réussis, taux."""
    total = _suite_case_count(suite)
    entry: dict[str, Any] = {
        "version": suite.get("version"),
        "declared": total,
        "executed": 0,
        "passed": 0,
        "rate": None,
    }
    if isinstance(results, dict) and results:
        executed = len(results)
        passed = sum(1 for v in results.values() if v)
        entry["executed"] = executed
        entry["passed"] = passed
        entry["rate"] = round(passed / executed, 4) if executed else None
    return entry


def evals_summary(
    blueprint: dict[str, Any], results: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Données du panneau santé : taux de réussite d'éval par node + blueprint.

    *results* (optionnel, produit par l'hôte) : ``{scope: {caseId: bool}}`` où
    *scope* est un ``nodeId`` ou :data:`BLUEPRINT_SCOPE`. Sans résultats, le
    panneau montre les cas *déclarés* (``executed = 0``) — la preuve est
    attachée mais pas encore exécutée.
    """
    results = results or {}
    scopes: dict[str, dict[str, Any]] = {}

    bp_suite = blueprint_eval_suite(blueprint)
    if bp_suite:
        scopes[BLUEPRINT_SCOPE] = _scope_summary(bp_suite, results.get(BLUEPRINT_SCOPE))
    for n in blueprint.get("nodes", []):
        suite = evals_suite(n)
        if not suite:
            continue
        nid = str(n.get("id"))
        scopes[nid] = _scope_summary(suite, results.get(nid))

    declared = sum(s["declared"] for s in scopes.values())
    executed = sum(s["executed"] for s in scopes.values())
    passed = sum(s["passed"] for s in scopes.values())
    return {
        "scopes": scopes,
        "totals": {
            "declared": declared,
            "executed": executed,
            "passed": passed,
            "rate": round(passed / executed, 4) if executed else None,
        },
    }


def _assertion_label(assertion: dict[str, Any]) -> str:
    kind = assertion.get("kind")
    if kind == "contract":
        return f"honore le contrat `{assertion.get('contract')}`"
    if kind == "cost":
        if assertion.get("maxTokens") is not None:
            return f"coût <= {assertion['maxTokens']} tokens"
        return f"coût <= {assertion.get('maxUsd')} USD"
    if kind == "no-refusal":
        return "aucun refus"
    if kind == "verdict":
        return f"verdict attendu `{assertion.get('expected')}`"
    if kind == "path-taken":
        inject = as_dict(assertion.get("inject"))
        return (
            f"sous échec `{inject.get('class')}` du node "
            f"`{inject.get('node')}`, chemin {assertion.get('path')}"
        )
    return str(kind)


def compile_evals_section(node: dict[str, Any]) -> list[str]:
    """Section « Évals » d'un step compilé — checks exécutés par l'hôte.

    Chaque cas devient un check CI que ``agent-test`` / ``standard gate``
    exécute ; le Studio n'exécute rien, il déclare la preuve à exiger.
    """
    suite = evals_suite(node)
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        return []
    version = suite.get("version", "?")
    lines = ["", f"#### Évals (preuve comportementale, v{version})", ""]
    for case in cases:
        if not isinstance(case, dict):
            continue
        cid = case.get("id", "?")
        asserts = [a for a in case.get("assert", []) if isinstance(a, dict)]
        checks = " ; ".join(_assertion_label(a) for a in asserts)
        lines.append(f"- `{cid}` — {checks}")
    lines.append(
        "- Gate CI : exécuter via `agent-test` (optionnel puis requis à la "
        "promotion) ; le taux de réussite alimente le panneau santé."
    )
    return lines
