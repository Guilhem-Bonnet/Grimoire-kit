"""Dérive un ordre d'exécution et des contrats de node depuis un blueprint.

Un blueprint ne porte pas d'ordre d'exécution explicite — seulement des
``edges`` reliant des pins. Le tri topologique ci-dessous (porté depuis le
prototype jetable de la première étape de #204, une fois vérifié sur
``web-pipeline.blueprint.json``) le rend explicite une bonne fois, pour que le
moteur n'ait pas à le redériver à chaque lecture d'un chemin différent.

Le blueprint chargé ici est le même fichier que ``grimoire blueprint compile``
accepte : ``validate_blueprint_file`` (contrôle structurel léger) est la seule
vérification que ce module impose. Les contrôles d'état projet de ``compile``
(extensions installées, artefacts résolus) ne s'appliquent pas : exécuter un
node ne compile rien, c'est l'hôte qui l'exécute.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.schemas import AcceptanceEvidence, AcceptanceRun, NodeContract, PinRef
from grimoire.tools.ext_manager import validate_blueprint_file

__all__ = ["build_node_contracts", "load_blueprint", "topo_order"]

#: Les seules clés qu'une entrée d'``acceptance`` structurée reconnaît (issue
#: #428). Une entrée doit en porter exactement une : ni zéro (forme inconnue),
#: ni deux ou plus (ambiguïté sur ce qu'il faut exécuter) — les deux sont un
#: refus nommé au chargement, jamais une garde qui échouerait ouvert.
_ACCEPTANCE_STRUCTURED_KEYS = ("run", "path_exists", "test")


def load_blueprint(path: Path) -> dict[str, Any]:
    """Charge et valide structurellement un ``.blueprint.json``.

    Lève :class:`GrimoireRuntimeError` (pas ``ExtensionError`` du sous-module
    ``tools`` : côté flows, une erreur de blueprint est une erreur de moteur,
    pas une erreur d'extension) en nommant chaque défaut trouvé.
    """
    from grimoire.tools.ext_manager import ExtensionError

    try:
        blueprint, errors = validate_blueprint_file(path)
    except ExtensionError as exc:
        raise GrimoireRuntimeError(str(exc)) from exc
    if errors:
        raise GrimoireRuntimeError(f"{path} : blueprint invalide — " + "; ".join(errors))
    return blueprint


def topo_order(blueprint: dict[str, Any]) -> list[str]:
    """Ordre d'exécution des nodes, dérivé des ``edges`` (Kahn, déterministe).

    Déterministe : à dépendances égales, les nodes prêts sont pris dans
    l'ordre alphabétique de leur id, pas dans l'ordre d'apparition dans le
    fichier — deux lectures du même blueprint rendent le même ordre.
    """
    nodes = [n["id"] for n in blueprint["nodes"]]
    node_by_pin: dict[str, str] = {}
    for n in blueprint["nodes"]:
        for p in n.get("pins", []):
            node_by_pin[f"{n['id']}.{p['id']}"] = n["id"]
    deps: dict[str, set[str]] = {nid: set() for nid in nodes}
    for e in blueprint.get("edges", []):
        src = node_by_pin.get(e["from"])
        dst = node_by_pin.get(e["to"])
        if src is None or dst is None:
            raise GrimoireRuntimeError(f"edge non résoluble : {e['from']} -> {e['to']}")
        deps[dst].add(src)
    order: list[str] = []
    remaining = set(nodes)
    while remaining:
        ready = sorted(n for n in remaining if deps[n] <= set(order))
        if not ready:
            raise GrimoireRuntimeError(f"cycle ou dépendance non résolue dans le blueprint : {sorted(remaining)}")
        order.extend(ready)
        remaining -= set(ready)
    return order


def _tool_boundary(node: dict[str, Any]) -> tuple[str, ...]:
    """La frontière d'outils d'un node : ce qu'il touche hors du raisonnement.

    Un ``extension-node`` délègue à un service tiers (CrewAI, LangGraph...) :
    sa ``ref`` EST la frontière. Un ``pattern`` en rôle ``Gate`` porte
    ``config.gate`` (mode + params) : c'est une frontière de contrôle, pas
    d'exécution, mais l'hôte doit savoir qu'un gate le regarde. Tout le reste
    (patterns de raisonnement pur, artefacts, composites) n'a pas de
    frontière déclarée en v1 — un tuple vide dit explicitement "aucune".
    """
    if node.get("kind") == "extension-node":
        return (node.get("ref", ""),)
    gate = (node.get("config") or {}).get("gate")
    if isinstance(gate, dict):
        mode = gate.get("mode", "?")
        params = gate.get("params") or {}
        checks = params.get("checks")
        if checks:
            return (f"gate:{mode}(checks={','.join(checks)})",)
        server = params.get("server")
        if server:
            return (f"gate:{mode}(server={server})",)
        return (f"gate:{mode}",)
    return ()


def _parse_acceptance_entry(
    node_id: str, entry: Any
) -> tuple[str, AcceptanceRun | None, AcceptanceEvidence | None]:
    """Une entrée d'``acceptance`` : texte libre (inchangé), ou forme structurée (issue #428).

    Rend ``(texte, run, evidence)`` — ``texte`` alimente toujours
    ``NodeContract.acceptance`` (donc ``verifiability.classify``), qu'il
    vienne de la prose de l'auteur ou soit dérivé d'une commande ; ``run``/
    ``evidence`` (au plus un des deux) alimentent le gate qui l'exécute
    réellement (``flows.dispatch_executor``).

    Une forme structurée reconnaît exactement une clé parmi ``run``,
    ``path_exists``, ``test`` — zéro ou plusieurs est un refus nommé au
    chargement (:class:`GrimoireRuntimeError`), jamais une garde silencieuse :
    « aucune inférence de commande à partir de la prose » est un refus
    explicite de l'issue, une forme ambiguë ne doit pas se comporter comme si
    elle en désignait une par accident.
    """
    if isinstance(entry, str):
        text = entry.strip()
        return (text, None, None) if text else ("", None, None)
    if not isinstance(entry, dict):
        raise GrimoireRuntimeError(
            f"node={node_id} : acceptance de forme inconnue ({entry!r}) — attendu une chaîne ou un objet "
            "{'run': ...} / {'path_exists': ...} / {'test': ...}"
        )
    present = [k for k in _ACCEPTANCE_STRUCTURED_KEYS if k in entry]
    if len(present) != 1:
        raise GrimoireRuntimeError(
            f"node={node_id} : acceptance structurée invalide {entry!r} — attendu exactement une clé parmi "
            f"{_ACCEPTANCE_STRUCTURED_KEYS}, trouvé {present or 'aucune'}"
        )
    key = present[0]
    if key == "run":
        raw = str(entry["run"]).strip()
        if not raw:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.run vide")
        try:
            argv = tuple(shlex.split(raw))
        except ValueError as exc:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.run illisible ({exc})") from exc
        if not argv:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.run vide après découpage")
        expect_exit = entry.get("expect_exit", 0)
        if not isinstance(expect_exit, int) or isinstance(expect_exit, bool):
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.expect_exit doit être un entier")
        cwd = entry.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd.strip():
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.cwd doit être une chaîne non vide")
        timeout_s = entry.get("timeout_s", 120.0)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.timeout_s doit être un nombre positif")
        expect_stdout_contains = entry.get("expect_stdout_contains")
        if expect_stdout_contains is not None and not isinstance(expect_stdout_contains, str):
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.expect_stdout_contains doit être une chaîne")
        run = AcceptanceRun(
            argv=argv,
            raw=raw,
            expect_exit=expect_exit,
            cwd=cwd,
            timeout_s=float(timeout_s),
            expect_stdout_contains=expect_stdout_contains,
        )
        text = f"la commande « {raw} » retourne le code de sortie {expect_exit} (acceptance exécutée)"
        return text, run, None
    if key == "path_exists":
        value = str(entry["path_exists"]).strip()
        if not value:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.path_exists vide")
        text = f"le fichier « {value} » existe (acceptance exécutée)"
        return text, None, AcceptanceEvidence(kind="path_exists", value=value)
    value = str(entry["test"]).strip()
    if not value:
        raise GrimoireRuntimeError(f"node={node_id} : acceptance.test vide")
    text = f"le test « {value} » passe (suite de tests, acceptance exécutée)"
    return text, None, AcceptanceEvidence(kind="test", value=value)


def _acceptance(
    node: dict[str, Any], outputs: tuple[PinRef, ...]
) -> tuple[tuple[str, ...], tuple[AcceptanceRun, ...], tuple[AcceptanceEvidence, ...]]:
    """Critères d'acceptation : ``node.acceptance``, sinon les evals, sinon les pins.

    Trois sources, dans cet ordre de préférence :

    - ``node.acceptance`` — texte libre écrit par l'auteur du blueprint, ou
      (issue #428) une forme structurée exécutable, ou un mélange des deux
      dans la même liste. C'est la seule source qu'une classe de
      vérifiabilité (#309) peut vraiment lire : ``sortie conforme au contrat
      « c1 »`` ou ``verdict attendu : ...`` ne nomment ni verdict mécanique
      reconnu ni revue, et tombent donc toujours ambigus. Un node qui veut
      être dispatchable par cascade (#311) doit décrire son critère avec ce
      vocabulaire-là : ``la suite de tests passe`` (V0), ``revue humaine
      avant fusion`` (V1) — ou, désormais, déclarer directement la commande
      qui rend ce verdict.
    - ``config.evals`` — les cas ``assert`` (P1.2, rejoués par ``grimoire
      blueprint evals``), s'il n'y a pas d'``acceptance`` explicite.
    - À défaut des deux, un critère dérivé des pins de sortie : la
      conformité au contrat est le plancher, jamais rien.

    Une forme structurée invalide (clé inconnue, plusieurs clés, type
    incorrect) lève :class:`GrimoireRuntimeError` en nommant le node — un
    schéma refusé au chargement, pas un gate qui échouerait ouvert plus tard.
    """
    explicit = node.get("acceptance")
    if isinstance(explicit, list) and explicit:
        texts: list[str] = []
        runs: list[AcceptanceRun] = []
        evidence: list[AcceptanceEvidence] = []
        for entry in explicit:
            text, run, ev = _parse_acceptance_entry(node["id"], entry)
            if text:
                texts.append(text)
            if run is not None:
                runs.append(run)
            if ev is not None:
                evidence.append(ev)
        return tuple(texts), tuple(runs), tuple(evidence)
    evals = (node.get("config") or {}).get("evals")
    criteria: list[str] = []
    if isinstance(evals, dict):
        for case in evals.get("cases", []):
            for assertion in case.get("assert", []):
                kind = assertion.get("kind")
                if kind == "contract":
                    criteria.append(f"sortie conforme au contrat « {assertion.get('contract')} »")
                elif kind == "no-refusal":
                    criteria.append("aucun refus du modèle")
                elif kind == "cost":
                    criteria.append(f"coût ≤ {assertion.get('maxTokens')} tokens")
                elif kind == "verdict":
                    criteria.append(f"verdict attendu : {assertion.get('expected')}")
    if not criteria:
        criteria = [f"sortie du pin « {p.pin_id} » conforme au contrat « {p.contract} »" for p in outputs]
    return tuple(criteria), (), ()


def build_node_contracts(blueprint: dict[str, Any]) -> dict[str, NodeContract]:
    """Un :class:`NodeContract` par node du blueprint, indexé par id."""
    contracts: dict[str, NodeContract] = {}
    for node in blueprint["nodes"]:
        pins = node.get("pins", [])
        inputs = tuple(PinRef(p["id"], p["contract"]) for p in pins if p.get("direction") == "in")
        outputs = tuple(PinRef(p["id"], p["contract"]) for p in pins if p.get("direction") == "out")
        acceptance_texts, acceptance_runs, acceptance_evidence = _acceptance(node, outputs)
        contracts[node["id"]] = NodeContract(
            node_id=node["id"],
            kind=node.get("kind", ""),
            label=node.get("label", ""),
            description=node.get("description", ""),
            inputs=inputs,
            outputs=outputs,
            tool_boundary=_tool_boundary(node),
            acceptance=acceptance_texts,
            acceptance_runs=acceptance_runs,
            acceptance_evidence=acceptance_evidence,
        )
    return contracts
