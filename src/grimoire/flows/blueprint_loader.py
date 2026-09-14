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

import re
import shlex
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.execution_needs import EXECUTION_NEED_IDS, KNOWN_MARKERS, resolve_execution_needs, resolve_need
from grimoire.flows.schemas import AcceptanceEvidence, AcceptanceRun, NodeContract, PinRef
from grimoire.missions.verifiability import Verifiability, classify_criteria
from grimoire.tools.ext_manager import validate_blueprint_file

__all__ = [
    "GENRE_KINDS",
    "MAX_COMPOSITE_DEPTH",
    "SINGLE_REF_GENRE_KINDS",
    "build_node_contracts",
    "hardcoded_command_warnings",
    "load_blueprint",
    "resolve_composite_ref",
    "topo_order",
    "validate_flow_composition",
]

#: Profondeur maximale d'imbrication d'un node ``composite`` (issue #206) : un
#: flow racine (profondeur 1) qui référence un sous-flow (2) qui en référence
#: un troisième (3) est la limite — un quatrième niveau est un refus nommé au
#: chargement, jamais un débordement de pile silencieux sur un blueprint
#: pathologique.
MAX_COMPOSITE_DEPTH = 3

#: Même motif que l'``id`` d'un blueprint (``$defs/identifier`` du schéma) —
#: distingue un id de registre nu (``web-pipeline``) d'un chemin de fichier
#: dans :func:`resolve_composite_ref`, sans avoir à retenter une résolution
#: fichier d'abord.
_BLUEPRINT_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Dossier du registre local des flows (même convention que ``flow list``,
#: ``flow extract`` — voir ``registry/blueprints/*.json`` au dépôt).
_REGISTRY_BLUEPRINTS_RELPATH = Path("registry/blueprints")

#: Les sept genres de node de l'issue #207, dont ``ref`` désigne un unique
#: sous-flow résolu exactement comme un node ``composite`` (issue #206) — un
#: genre de plus n'est jamais qu'une politique d'agrégation différente sur le
#: même mécanisme « ce node lance un sous-flow ». ``composite`` lui-même y
#: figure : c'est le cas dégénéré (une seule tentative, aucune agrégation).
SINGLE_REF_GENRE_KINDS = frozenset(
    {"composite", "fanout", "verify-panel", "loop-until-dry", "judge", "replay-diff"}
)

#: Tous les genres de node de l'issue #207, ``budget`` (dont les refs vivent
#: dans ``config.budget.passes``, une liste, jamais dans ``ref``) et
#: ``checkpoint`` (aucune ref — une décision d'hôte, pas un sous-flow) inclus.
GENRE_KINDS = SINGLE_REF_GENRE_KINDS | {"budget", "checkpoint"}

#: Les seules clés qu'une entrée d'``acceptance`` structurée reconnaît (issue
#: #428, #205 pour ``run_need``). Une entrée doit en porter exactement une :
#: ni zéro (forme inconnue), ni deux ou plus (ambiguïté sur ce qu'il faut
#: exécuter) — les deux sont un refus nommé au chargement, jamais une garde
#: qui échouerait ouvert.
_ACCEPTANCE_STRUCTURED_KEYS = ("run", "run_need", "path_exists", "test")


def load_blueprint(path: Path, project_root: Path = Path(), _chain: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Charge et valide structurellement un ``.blueprint.json``.

    Lève :class:`GrimoireRuntimeError` (pas ``ExtensionError`` du sous-module
    ``tools`` : côté flows, une erreur de blueprint est une erreur de moteur,
    pas une erreur d'extension) en nommant chaque défaut trouvé.

    ``project_root``/``_chain`` (issue #206) : après la validation
    structurelle, tout node ``kind: "composite"`` est résolu et rechargé
    récursivement par :func:`validate_flow_composition` — une référence
    introuvable ou un cycle est donc un refus **au chargement**, jamais au
    moment où le node composite serait présenté à un exécuteur. ``_chain`` ne
    doit jamais être passé par un appelant hors de ce module : c'est l'état
    interne de la récursion (les chemins déjà résolus depuis la racine).
    """
    from grimoire.tools.ext_manager import ExtensionError

    try:
        blueprint, errors = validate_blueprint_file(path)
    except ExtensionError as exc:
        raise GrimoireRuntimeError(str(exc)) from exc
    if errors:
        raise GrimoireRuntimeError(f"{path} : blueprint invalide — " + "; ".join(errors))
    validate_flow_composition(blueprint, project_root=project_root, blueprint_path=path, _chain=_chain)
    return blueprint


def resolve_composite_ref(ref: str, *, project_root: Path, blueprint_dir: Path) -> Path:
    """Résout la ``ref`` d'un node ``kind: "composite"`` en chemin de fichier (issue #206).

    Trois formes reconnues, jamais une quatrième inventée :

    - ``use-case:<id>`` — expansion Studio/catalogue, hors périmètre du
      moteur de flows : refus nommé, cette forme ne désigne aucun fichier
      exécutable par ``FlowEngine``.
    - un chemin se terminant par ``.blueprint.json`` — résolu d'abord tel
      quel (absolu ou relatif au répertoire courant), sinon relatif au
      dossier du blueprint qui le référence, sinon relatif à
      ``project_root`` ; le premier qui existe gagne.
    - un id nu (motif d'id de blueprint) — résolu contre le registre local,
      ``<project_root>/registry/blueprints/<id>.blueprint.json``.

    Une référence introuvable, ou d'une forme qui n'est aucune des trois
    ci-dessus, est un :class:`GrimoireRuntimeError` nommé — jamais un chemin
    deviné.
    """
    if ref.startswith("use-case:"):
        raise GrimoireRuntimeError(
            f"ref composite {ref!r} : une expansion 'use-case:' est réservée au Studio/à la compilation, "
            "le moteur de flows ne l'exécute pas — utiliser un chemin .blueprint.json ou un id de registre"
        )
    if ref.endswith(".blueprint.json"):
        candidates = (Path(ref), blueprint_dir / ref, project_root / ref)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise GrimoireRuntimeError(
            f"sous-blueprint introuvable pour ref {ref!r} (essayé : {', '.join(str(c) for c in candidates)})"
        )
    if not _BLUEPRINT_ID_RE.match(ref):
        raise GrimoireRuntimeError(
            f"ref composite {ref!r} invalide — attendu use-case:<id>, un id de blueprint "
            "(minuscules/chiffres/traits d'union), ou un chemin .blueprint.json"
        )
    registry_path = project_root / _REGISTRY_BLUEPRINTS_RELPATH / f"{ref}.blueprint.json"
    if not registry_path.is_file():
        raise GrimoireRuntimeError(f"flow {ref!r} introuvable au registre local ({registry_path})")
    return registry_path


def _genre_refs(node: dict[str, Any]) -> list[str]:
    """Les ``ref`` qu'un node de genre (issue #207) doit résoudre au chargement."""
    kind = node.get("kind")
    if kind in SINGLE_REF_GENRE_KINDS:
        return [str(node.get("ref", ""))]
    if kind == "budget":
        passes = ((node.get("config") or {}).get("budget") or {}).get("passes")
        return [str(p) for p in passes] if isinstance(passes, list) else []
    return []  # "checkpoint" : aucune ref, une décision d'hôte, pas un sous-flow.


def _require_positive_int(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _require_angle_list(value: Any, *, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(isinstance(a, str) and a.strip() for a in value)


def _validate_genre_config(node: dict[str, Any]) -> None:
    """Refuse nommément un genre (issue #207) mal paramétré, au chargement.

    Chaque genre a exactement les clés qu'il déclare dans le tableau de
    l'issue — une clé manquante ou d'un mauvais type est un refus nommé ici,
    jamais une garde qui échouerait ouvert à l'exécution avec un
    ``TypeError``/``KeyError`` anonyme trois appels plus loin.
    """
    node_id = node.get("id")
    kind = node.get("kind")
    config = node.get("config") or {}

    if kind == "verify-panel":
        vp = config.get("verifyPanel") or {}
        k = vp.get("k")
        if not _require_positive_int(k, minimum=2):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.verifyPanel.k doit être un entier >= 2 "
                "(un panel à un seul vérificateur ne peut pas rendre de majorité, issue #207)"
            )
        assert isinstance(k, int)  # garanti par _require_positive_int ci-dessus
        if not _require_angle_list(vp.get("angles"), length=k):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.verifyPanel.angles doit être une liste de {k} chaînes non vides "
                "— un angle distinct par vérificateur, jamais moins que k"
            )
    elif kind == "loop-until-dry":
        lud = config.get("loopUntilDry") or {}
        if not _require_positive_int(lud.get("maxRounds"), minimum=1):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.loopUntilDry.maxRounds doit être un entier >= 1 — "
                "une borne dure, jamais une relance sans plafond"
            )
    elif kind == "judge":
        j = config.get("judge") or {}
        n = j.get("n")
        if not _require_positive_int(n, minimum=2):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.judge.n doit être un entier >= 2 "
                "(un juge n'a rien à départager sur une seule tentative)"
            )
        assert isinstance(n, int)  # garanti par _require_positive_int ci-dessus
        if not _require_angle_list(j.get("angles"), length=n):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.judge.angles doit être une liste de {n} chaînes non vides "
                "— un angle imposé par tentative"
            )
    elif kind == "budget":
        b = config.get("budget") or {}
        max_cost = b.get("maxCostUsd")
        if not isinstance(max_cost, (int, float)) or isinstance(max_cost, bool) or max_cost <= 0:
            raise GrimoireRuntimeError(
                f"node={node_id} : config.budget.maxCostUsd doit être un nombre strictement positif"
            )
        passes = b.get("passes")
        if not isinstance(passes, list) or not passes or not all(isinstance(p, str) and p.strip() for p in passes):
            raise GrimoireRuntimeError(
                f"node={node_id} : config.budget.passes doit être une liste non vide de refs "
                "(chacune résolue comme la ref d'un node composite)"
            )
    # "fanout" : aucune clé de config obligatoire — le plafond du nombre
    # d'éléments (pilot.max_fanout_n) est une politique de projet, vérifiée à
    # l'exécution (flows/dispatch_executor.py, DispatchExecutor._execute_fanout),
    # pas une forme du blueprint.
    # "checkpoint"/"replay-diff"/"composite" : pas de config dédiée requise.


def validate_flow_composition(
    blueprint: dict[str, Any], *, project_root: Path, blueprint_path: Path, _chain: tuple[Path, ...] = ()
) -> None:
    """Valide récursivement les nodes de genre (composite, issue #206 ; les sept, issue #207).

    Appelée par :func:`load_blueprint` juste après la validation structurelle
    — jamais indépendamment par un appelant externe. Refus possibles, tous
    nommés, tous au chargement :

    - un genre mal paramétré (:func:`_validate_genre_config`) ;
    - une référence qui ne se résout à aucun fichier (:func:`resolve_composite_ref`) ;
    - le fichier résolu est déjà un ancêtre de celui-ci dans la chaîne de
      composition en cours — un cycle ;
    - la profondeur de composition dépasse :data:`MAX_COMPOSITE_DEPTH`.

    Un sous-blueprint valide est rechargé (donc revalidé structurellement,
    et sa propre composition explorée) via :func:`load_blueprint` lui-même —
    la récursion couvre n'importe quelle profondeur de nesting sans qu'aucune
    fonction n'ait à connaître le graphe complet à l'avance.
    """
    resolved_self = blueprint_path.resolve()
    if resolved_self in _chain:
        chain_desc = " -> ".join(str(p) for p in (*_chain, resolved_self))
        raise GrimoireRuntimeError(f"cycle de composition détecté (issue #206) : {chain_desc}")
    new_chain = (*_chain, resolved_self)
    if len(new_chain) > MAX_COMPOSITE_DEPTH:
        chain_desc = " -> ".join(str(p) for p in new_chain)
        raise GrimoireRuntimeError(
            f"profondeur de composition dépassée (max {MAX_COMPOSITE_DEPTH}, issue #206) : {chain_desc}"
        )
    for node in blueprint.get("nodes", []):
        kind = node.get("kind")
        if kind not in GENRE_KINDS:
            continue
        _validate_genre_config(node)
        for ref in _genre_refs(node):
            sub_path = resolve_composite_ref(ref, project_root=project_root, blueprint_dir=blueprint_path.parent)
            load_blueprint(sub_path, project_root, new_chain)


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
    kind = node.get("kind")
    if kind == "composite":
        # Issue #206 : la frontière d'un node composite EST le sous-flow —
        # même convention qu'``extension-node``, pour que ``to_text()``
        # renseigne un hôte interactif (qui n'auto-lance rien, voir
        # ``flows.dispatch_executor``) sur la commande à lancer lui-même.
        return (f"composite:{node.get('ref', '')}",)
    if kind in ("fanout", "verify-panel", "loop-until-dry", "judge", "checkpoint", "budget", "replay-diff"):
        # Issue #207 : même principe que "composite" ci-dessus, un descriptif
        # par genre pour qu'un hôte interactif sache ce qu'il doit orchestrer
        # lui-même (aucun de ces genres n'est auto-lancé hors dispatch — voir
        # ``flows.dispatch_executor``).
        config = (node.get("config") or {}).get(
            {
                "fanout": "fanout",
                "verify-panel": "verifyPanel",
                "loop-until-dry": "loopUntilDry",
                "judge": "judge",
                "checkpoint": "checkpoint",
                "budget": "budget",
                "replay-diff": "replayDiff",
            }[kind],
            {},
        ) or {}
        if kind == "verify-panel":
            return (f"verify-panel(k={config.get('k')}):{node.get('ref', '')}",)
        if kind == "loop-until-dry":
            return (f"loop-until-dry(max_rounds={config.get('maxRounds')}):{node.get('ref', '')}",)
        if kind == "judge":
            return (f"judge(n={config.get('n')}):{node.get('ref', '')}",)
        if kind == "checkpoint":
            return ("checkpoint(approve/reject/amend)",)
        if kind == "budget":
            passes = config.get("passes") or []
            return (f"budget(max_cost_usd={config.get('maxCostUsd')}, passes={len(passes)})",)
        # "fanout" / "replay-diff" : juste la ref, rien d'autre à borner ici.
        return (f"{kind}:{node.get('ref', '')}",)
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


def _parse_run_like_entry(
    node_id: str, entry: dict[str, Any], *, raw: str, source_desc: str
) -> AcceptanceRun:
    """Les options communes à ``run`` et ``run_need`` (issue #205), une fois *raw* connu.

    Factorisé pour que les deux clés structurées produisent le même
    :class:`AcceptanceRun` — le gate (``flows.dispatch_executor``) ne voit
    ensuite jamais la différence entre une commande écrite en dur et une
    commande résolue depuis un besoin.
    """
    if not raw:
        raise GrimoireRuntimeError(f"node={node_id} : {source_desc} vide")
    try:
        argv = tuple(shlex.split(raw))
    except ValueError as exc:
        raise GrimoireRuntimeError(f"node={node_id} : {source_desc} illisible ({exc})") from exc
    if not argv:
        raise GrimoireRuntimeError(f"node={node_id} : {source_desc} vide après découpage")
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
    return AcceptanceRun(
        argv=argv,
        raw=raw,
        expect_exit=expect_exit,
        cwd=cwd,
        timeout_s=float(timeout_s),
        expect_stdout_contains=expect_stdout_contains,
    )


def _parse_acceptance_entry(
    node_id: str, entry: Any, project_root: Path
) -> tuple[str, AcceptanceRun | None, AcceptanceEvidence | None]:
    """Une entrée d'``acceptance`` : texte libre (inchangé), ou forme structurée (issue #428/#205).

    Rend ``(texte, run, evidence)`` — ``texte`` alimente toujours
    ``NodeContract.acceptance`` (donc ``verifiability.classify``), qu'il
    vienne de la prose de l'auteur ou soit dérivé d'une commande ; ``run``/
    ``evidence`` (au plus un des deux) alimentent le gate qui l'exécute
    réellement (``flows.dispatch_executor``).

    Une forme structurée reconnaît exactement une clé parmi ``run``,
    ``run_need``, ``path_exists``, ``test`` — zéro ou plusieurs est un refus
    nommé au chargement (:class:`GrimoireRuntimeError`), jamais une garde
    silencieuse : « aucune inférence de commande à partir de la prose » est un
    refus explicite de l'issue, une forme ambiguë ne doit pas se comporter
    comme si elle en désignait une par accident.

    ``run_need`` (issue #205) déclare un besoin du catalogue
    (:data:`grimoire.core.execution_needs.EXECUTION_NEED_IDS`) plutôt qu'une
    commande — résolu ici, à l'unique endroit qui connaît à la fois le
    blueprint et le projet qui l'exécute. Un besoin non résolvable refuse le
    chargement du blueprint entier en le nommant, **avant** que le premier
    node soit présenté à l'hôte : jamais une installation qui échouerait au
    troisième node.
    """
    if isinstance(entry, str):
        text = entry.strip()
        return (text, None, None) if text else ("", None, None)
    if not isinstance(entry, dict):
        raise GrimoireRuntimeError(
            f"node={node_id} : acceptance de forme inconnue ({entry!r}) — attendu une chaîne ou un objet "
            "{'run': ...} / {'run_need': ...} / {'path_exists': ...} / {'test': ...}"
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
        run = _parse_run_like_entry(node_id, entry, raw=raw, source_desc="acceptance.run")
        expect_exit = entry.get("expect_exit", 0)
        text = f"la commande « {raw} » retourne le code de sortie {expect_exit} (acceptance exécutée)"
        return text, run, None
    if key == "run_need":
        need_id = str(entry["run_need"]).strip()
        if not need_id:
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.run_need vide")
        resolved = resolve_need(need_id, project_root)
        if not resolved.resolved:
            if need_id not in EXECUTION_NEED_IDS:
                raise GrimoireRuntimeError(
                    f"node={node_id} : besoin « {need_id} » inconnu du catalogue "
                    f"(connus : {', '.join(EXECUTION_NEED_IDS)})"
                )
            raise GrimoireRuntimeError(
                f"node={node_id} : besoin « {need_id} » non résolvable pour ce projet — "
                f"déclarez needs.commands.{need_id} dans project-context.yaml, ou installez un marqueur reconnu "
                f"({', '.join(KNOWN_MARKERS)})"
            )
        args = entry.get("args")
        if args is not None and not isinstance(args, str):
            raise GrimoireRuntimeError(f"node={node_id} : acceptance.args doit être une chaîne")
        command = resolved.command
        assert command is not None  # resolved.resolved garantit command non None
        raw = command if not args else f"{command} {args}"
        run = _parse_run_like_entry(node_id, entry, raw=raw, source_desc="acceptance.run_need")
        expect_exit = entry.get("expect_exit", 0)
        text = (
            f"le besoin « {need_id} » (résolu {resolved.source} : « {raw} ») "
            f"retourne le code de sortie {expect_exit} (acceptance exécutée)"
        )
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
    node: dict[str, Any], outputs: tuple[PinRef, ...], project_root: Path
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
            text, run, ev = _parse_acceptance_entry(node["id"], entry, project_root)
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


def _verifiability_warning(node_id: str, acceptance_texts: tuple[str, ...], *, has_structured: bool) -> str | None:
    """« nœud V0 sans acceptance exécutable » — posé au chargement, jamais au gate (issue #428, suite).

    Un nœud dont le texte seul classe V0 (#309) mais qui ne déclare aucune
    commande exécutable est le fossé exact que le rejeu du 2026-09-11 a payé :
    l'auteur du blueprint a écrit un vocabulaire mécanique (« la suite de
    tests passe ») sans jamais donner au gate de quoi le vérifier — sans ce
    garde-fou, ``flows.dispatch_executor`` n'aurait toujours que le check
    d'enveloppe à faire tourner. La règle du kit (« un faux V0 est pire qu'un
    faux V2 », ``verifiability.py``) s'applique à l'identique ici : ce nœud
    est traité comme V1 par l'exécuteur de dispatch (jamais fermé sur la
    seule foi de l'ouvrier), et ce message nommé en dit la raison plutôt que
    de rétrograder en silence.
    """
    if has_structured:
        return None
    if classify_criteria(acceptance_texts) is not Verifiability.V0:
        return None
    return f"nœud {node_id} classé V0 sans acceptance exécutable : traité comme V1"


def build_node_contracts(blueprint: dict[str, Any], project_root: Path = Path()) -> dict[str, NodeContract]:
    """Un :class:`NodeContract` par node du blueprint, indexé par id.

    ``project_root`` (issue #205) : nécessaire pour résoudre une acceptance
    ``run_need`` — ignoré par tout le reste, d'où son défaut à ``Path(".")``
    (le répertoire courant, jamais consulté par les blueprints qui n'ont pas
    de ``run_need``, ce qui couvre tout le corpus antérieur à cette issue).
    """
    contracts: dict[str, NodeContract] = {}
    for node in blueprint["nodes"]:
        pins = node.get("pins", [])
        inputs = tuple(PinRef(p["id"], p["contract"]) for p in pins if p.get("direction") == "in")
        outputs = tuple(PinRef(p["id"], p["contract"]) for p in pins if p.get("direction") == "out")
        acceptance_texts, acceptance_runs, acceptance_evidence = _acceptance(node, outputs, project_root)
        has_structured = bool(acceptance_runs or acceptance_evidence)
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
            verifiability_warning=_verifiability_warning(node["id"], acceptance_texts, has_structured=has_structured),
            ref=str(node.get("ref", "")),
        )
    return contracts


def hardcoded_command_warnings(blueprint: dict[str, Any], project_root: Path) -> tuple[str, ...]:
    """Nodes dont l'``acceptance.run`` en dur pourrait devenir un ``run_need`` (issue #205).

    Rétrocompatibilité explicite : un blueprint à commandes en dur reste
    valide (aucun refus ici, jamais), mais s'il déclare mot pour mot la
    commande que le catalogue de besoins résout *pour ce projet*, l'auteur
    gagne à le savoir — un avertissement, jamais un chargement altéré. Appelé
    par la CLI (``grimoire flow run``), jamais par :func:`build_node_contracts`
    lui-même : cette fonction n'influence aucun :class:`NodeContract`.
    """
    resolved = resolve_execution_needs(project_root)
    command_to_need = {r.command: need_id for need_id, r in resolved.items() if r.command}
    warnings: list[str] = []
    for node in blueprint.get("nodes", []):
        for entry in node.get("acceptance") or []:
            if not (isinstance(entry, dict) and "run" in entry):
                continue
            raw = str(entry["run"]).strip()
            need_id = command_to_need.get(raw)
            if need_id is not None:
                warnings.append(
                    f"node {node.get('id')} : commande « {raw} » en dur correspond au besoin « {need_id} » "
                    f"résolu pour ce projet — envisager acceptance: [{{'run_need': '{need_id}'}}]"
                )
    return tuple(warnings)
