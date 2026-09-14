"""Les genres de node du moteur de flows — composite (#206) et les sept de #207.

Compagnon interne de :mod:`grimoire.flows.dispatch_executor`, extrait de ce
module (issue #207) quand le fichier a dépassé le seuil de lignes du cliquet
de code («R2», `scripts/check-code-ratchet.py`) — même geste que la scission
historique de `forge_server.py`. Toutes les fonctions ci-dessous prennent un
``executor: DispatchExecutor`` explicite plutôt que ``self`` : ce ne sont plus
des méthodes, mais elles restent aussi couplées à `DispatchExecutor` que si
elles l'étaient encore — accès direct à ses attributs "privés"
(``_engine``, ``_pilot_policy``, ``node_outcomes``...), par construction, pas
par accident. ``dispatch_executor.DispatchExecutor.execute`` importe ce module
à l'intérieur de sa méthode (import différé) : ``genres`` importe
`DispatchExecutor` au niveau module, l'inverse romprait le cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import load_blueprint, resolve_composite_ref
from grimoire.flows.dispatch_executor import DispatchExecutor, NodeDispatchOutcome
from grimoire.flows.engine import FlowEngine
from grimoire.flows.schemas import NodeContract, NodeExecutionResult, ResumeOutcome

__all__ = ["execute_genre_node"]


@dataclass(frozen=True, slots=True)
class _ChildRun:
    """Ce qu'un run enfant a produit — la brique commune à ``composite`` et aux six autres genres (#206/#207)."""

    child_run_id: str
    status: str  # "finished" | "blocked" | "waiting_host" | "cost_capped"
    cost_usd: float | None
    faults: tuple[str, ...]
    host_reason: str | None
    attempts: int
    escalations: int
    #: La sortie soumise pour le dernier node de l'enfant, quand
    #: ``status == "finished"`` — ``None`` sinon. Un genre y lit une clé
    #: annexe (``novelty_key`` pour ``loop-until-dry``) que le contrat de
    #: pins ignore déjà.
    output: dict[str, Any] | None = None


def _launch_child(
    executor: DispatchExecutor,
    ref: str,
    *,
    node_id: str,
    run_id: str,
    blueprint_id: str,
    suffix: str,
    max_cost_usd: float | None,
    extra_context: str = "",
) -> _ChildRun:
    """Lance *ref* comme un run enfant complet, le pilote jusqu'à sa fin (issues #206/#207).

    Cœur partagé de ``composite`` et des six autres genres qui lancent un
    sous-flow (fanout, verify-panel, loop-until-dry, judge, budget,
    replay-diff) — seule la politique d'agrégation diffère d'un genre à
    l'autre, jamais ce mécanisme de lancement. ``max_cost_usd`` s'applique
    au run enfant lancé ICI seul : pour un genre à plusieurs enfants
    (verify-panel, judge, budget...), c'est l'appelant qui doit vérifier
    le cumul *avant* chaque appel — passer ``None`` ici et faire ce calcul
    soi-même (voir chaque ``_execute_<genre>``).
    """
    if executor._engine is None:
        raise GrimoireRuntimeError(
            f"node={node_id} : genre exécuté sans engine parent (usage interne invalide, "
            "DispatchExecutor doit être construit avec engine=...)"
        )
    try:
        sub_path = resolve_composite_ref(ref, project_root=executor._project_root, blueprint_dir=executor._blueprint_path.parent)
    except GrimoireRuntimeError as exc:
        # Ne devrait pas arriver pour composite/fanout/.../replay-diff :
        # déjà validé au chargement du blueprint parent
        # (`blueprint_loader.validate_flow_composition`). Pour `budget`,
        # dont les refs vivent dans une liste de config plutôt que dans
        # `ref`, la même validation au chargement s'applique déjà aussi
        # (`_genre_refs`). Refus défensif nommé, jamais une exception crue.
        raise GrimoireRuntimeError(f"node={node_id} : {exc}") from exc

    child_engine = executor._engine.child_engine()
    child_executor = DispatchExecutor(
        project_root=executor._project_root,
        blueprint_path=sub_path,
        max_tier=executor._max_tier,
        call_timeout=executor._call_timeout,
        actor=executor._actor,
        agent=executor._agent,
        engine=child_engine,
        extra_context=extra_context,
    )
    tag = f"-{suffix}" if suffix else ""
    # Le contrat du premier node de l'enfant n'est jamais réutilisé ici :
    # `_drive_composite_child` pilote sur `executor.last_result`/
    # `ResumeOutcome`, jamais sur un `NodeContract` porté d'un tour à
    # l'autre (contrairement à `_drive`, qui doit le présenter à l'hôte).
    wfi, _ = child_engine.run(
        sub_path,
        executor=child_executor,
        mission_id=f"MIS-flow-{blueprint_id}-{node_id}{tag}",
        task_id=f"FLOW-{run_id}-{node_id}{tag}",
        parent_run_id=run_id,
        parent_node_id=node_id,
    )
    child_run_id = wfi.id
    first_result = child_executor.last_result or NodeExecutionResult(pending=True)
    status, faults, cost, host_reason, last_output = _drive_composite_child(
        child_engine, child_run_id, child_executor, first_result, max_cost_usd=max_cost_usd
    )
    return _ChildRun(
        child_run_id=child_run_id,
        status=status,
        cost_usd=cost,
        faults=faults,
        host_reason=host_reason,
        attempts=len(child_executor.node_outcomes),
        escalations=sum(o.escalations for o in child_executor.node_outcomes.values()),
        output=last_output,
    )


def _record_genre_outcome(
    executor: DispatchExecutor,
    node_id: str,
    run_id: str,
    *,
    verifiability_label: str,
    verdict: str,
    cost_usd: float | None,
    attempts: int,
    escalations: int,
    child_run_id: str | None = None,
) -> None:
    executor.node_outcomes[node_id] = NodeDispatchOutcome(
        node_id=node_id,
        task_id=f"FLOW-{run_id}-{node_id}",
        verifiability=verifiability_label,
        verdict=verdict,
        needs_review=False,
        provider=None,
        attempts=attempts,
        escalations=escalations,
        cost_usd=cost_usd,
        uncertainties=(),
        acceptance_status=verifiability_label,
        child_run_id=child_run_id,
    )


def _execute_composite(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """Lance le sous-flow d'un node ``kind: "composite"`` comme son propre run (issue #206).

    Pas de tâche de dispatch pour CE node : sa preuve est le run enfant
    entier (son propre Mission Ledger, sa propre entrée dans
    ``TraceLedger.dispatch_outcome_stats().by_flow`` sous l'id du
    sous-blueprint — voir #473, aucun code neuf requis pour que ``flow
    list --require-measure`` couvre un sous-flow, il est mesuré comme
    n'importe quel flow dispatché). Trois issues, jamais une quatrième :
    terminé (vert), bloqué (rouge), ou suspendu à l'hôte (V2 dans
    l'enfant) — voir :meth:`_launch_child`.

    Le plafond de coût du pilote (issue #209) s'applique ici au **total**
    du sous-flow, pas à une seule tentative : vérifié entre chaque node
    de l'enfant (:func:`_drive_composite_child`), jamais après un node
    déjà vert (même garantie que ``run_dispatch`` pour un node simple).
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")

    child = _launch_child(executor, 
        contract.ref,
        node_id=node_id,
        run_id=run_id,
        blueprint_id=blueprint_id,
        suffix="",
        max_cost_usd=executor._pilot_policy.max_cost_usd_per_node,
    )

    if child.status == "waiting_host":
        executor.host_node = node_id
        executor.host_reason = (
            f"sous-flow {child.child_run_id} (node {node_id}) suspendu : "
            f"{child.host_reason or 'nœud du sous-flow en attente de l’hôte'}"
        )
        _record_genre_outcome(executor, 
            node_id,
            run_id,
            verifiability_label="composite",
            verdict="waiting_host",
            cost_usd=child.cost_usd,
            attempts=child.attempts,
            escalations=child.escalations,
            child_run_id=child.child_run_id,
        )
        result = NodeExecutionResult(
            pending=True,
            extra={"dispatch_refused": "composite_waiting_host", "node_id": node_id, "child_run_id": child.child_run_id},
        )
        executor.last_result = result
        return result

    verdict = {"finished": "green", "blocked": "red", "cost_capped": "cost_capped"}[child.status]
    _record_genre_outcome(executor, 
        node_id,
        run_id,
        verifiability_label="composite",
        verdict=verdict,
        cost_usd=child.cost_usd,
        attempts=child.attempts,
        escalations=child.escalations,
        child_run_id=child.child_run_id,
    )
    if verdict != "green":
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False,
            output=None,
            extra={
                "composite_child_run_id": child.child_run_id,
                "composite_status": child.status,
                "composite_faults": list(child.faults),
            },
        )
        executor.last_result = result
        return result

    output = {
        "pins": {pin.pin_id: {"contract": pin.contract, "child_run_id": child.child_run_id} for pin in contract.outputs}
    }
    result = NodeExecutionResult(
        pending=False,
        output=output,
        extra={"composite_child_run_id": child.child_run_id, "composite_status": child.status},
    )
    executor.last_result = result
    return result


def _execute_fanout(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "fanout"`` (issue #207) : un node produit N éléments, N sous-flows instanciés.

    Deux phases, jamais confondues : (1) le node lui-même est dispatché
    normalement (:meth:`_execute_plain`) — son acceptance/verdict décide
    de fermer *ce* palier, et sa sortie doit porter, en plus de ses pins
    déclarées, une clé ``fanout_items`` (liste) que ce genre seul lit ;
    (2) une fois ``fanout_items`` connue, un sous-flow enfant est lancé
    par élément (:meth:`_launch_child`, ``ref`` = celui du node). N est
    borné par ``pilot.max_fanout_n`` (issue #209/#207) — **absent**, le
    fan-out est refusé : jamais une politique manquante ne vaut un
    plafond infini implicite.

    Vert seulement si tous les N enfants finissent verts : un fan-out
    n'est jamais « majoritairement » recollé, comme un ``verify-panel``
    n'est jamais vert sur une minorité — recoller un résultat partiel
    comme s'il était le tout serait l'invention que #207 refuse.
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")

    phase1 = executor._execute_plain(contract, context_pack)
    if phase1.pending or phase1.output is None:
        return phase1  # V2/host, ou rouge : le fan-out hérite tel quel.

    items = phase1.output.get("fanout_items")
    if not isinstance(items, list) or not items:
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False,
            output=None,
            extra={"fanout_error": "sortie sans 'fanout_items' (liste non vide attendue)", "node_id": node_id},
        )
        executor.last_result = result
        return result

    max_n = executor._pilot_policy.max_fanout_n
    if max_n is None or len(items) > max_n:
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False,
            output=None,
            extra={
                "fanout_error": (
                    f"{len(items)} éléments demandés, plafond du pilote "
                    f"(pilot.max_fanout_n) = {max_n!r} — un plafond absent ou dépassé refuse le fan-out"
                ),
                "node_id": node_id,
            },
        )
        executor.last_result = result
        return result

    children: list[_ChildRun] = []
    total_cost = 0.0
    cap = executor._pilot_policy.max_cost_usd_per_node
    cost_capped = False
    for i in range(len(items)):
        if cap is not None and total_cost > cap:
            cost_capped = True
            break
        child = _launch_child(executor, 
            contract.ref,
            node_id=node_id,
            run_id=run_id,
            blueprint_id=blueprint_id,
            suffix=f"fanout{i}",
            max_cost_usd=None,
            extra_context=f"Élément de fan-out {i + 1}/{len(items)}. [genre_attempt_index={i}]",
        )
        children.append(child)
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"fanout : élément {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="fanout", verdict="waiting_host",
                cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                escalations=sum(c.escalations for c in children),
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "fanout_waiting_host", "node_id": node_id})
            executor.last_result = result
            return result

    attempts = sum(c.attempts for c in children)
    escalations = sum(c.escalations for c in children)
    all_green = len(children) == len(items) and all(c.status == "finished" for c in children)
    verdict = "cost_capped" if cost_capped else ("green" if all_green else "red")
    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="fanout", verdict=verdict, cost_usd=total_cost,
        attempts=attempts, escalations=escalations,
    )
    fanout_results = [
        {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
        for i, c in enumerate(children)
    ]
    if verdict != "green":
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False, output=None, extra={"fanout_results": fanout_results, "fanout_status": verdict}
        )
        executor.last_result = result
        return result
    output = dict(phase1.output)
    output["fanout_results"] = fanout_results
    result = NodeExecutionResult(pending=False, output=output, extra={"fanout_status": verdict})
    executor.last_result = result
    return result


def _execute_verify_panel(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "verify-panel"`` (issue #207) : k vérificateurs indépendants, majorité requise.

    k et les angles imposés (un par vérificateur, distincts) sont validés
    au chargement (:func:`grimoire.flows.blueprint_loader._validate_genre_config`).
    Coût = k tentatives, **toujours** — jamais une cascade qui s'arrête au
    premier consensus apparent (l'issue le nomme explicitement : « jamais
    un vert sur 1/k »). Un plafond de coût qui empêche de compléter les k
    tentatives referme le node en ``cost_capped``, jamais sur un vote
    partiel : un panel incomplet n'a pas d'avis.
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")
    vp = _node_config(executor, node_id).get("verifyPanel") or {}
    k = int(vp["k"])
    angles = list(vp["angles"])

    children: list[_ChildRun] = []
    total_cost = 0.0
    cap = executor._pilot_policy.max_cost_usd_per_node
    for i in range(k):
        if cap is not None and total_cost > cap:
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="verify-panel", verdict="cost_capped",
                cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                escalations=sum(c.escalations for c in children),
            )
            executor.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None,
                extra={"verify_panel_error": f"plafond dépassé après {len(children)}/{k} vérificateurs"},
            )
            executor.last_result = result
            return result
        child = _launch_child(executor, 
            contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"verifier{i}",
            max_cost_usd=None,
            extra_context=(
                f"Angle de vérification imposé ({i + 1}/{k}) : {angles[i]}\n"
                "Instruction : cherche activement à réfuter, ne confirme jamais par défaut.\n"
                f"[genre_attempt_index={i}]"
            ),
        )
        children.append(child)
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"verify-panel : vérificateur {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="verify-panel", verdict="waiting_host",
                cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                escalations=sum(c.escalations for c in children),
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "verify_panel_waiting_host"})
            executor.last_result = result
            return result

    greens = sum(1 for c in children if c.status == "finished")
    majority = greens > k / 2
    verdict = "green" if majority else "red"
    attempts = sum(c.attempts for c in children)
    escalations = sum(c.escalations for c in children)
    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="verify-panel", verdict=verdict, cost_usd=total_cost,
        attempts=attempts, escalations=escalations,
    )
    panel_results = [
        {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
        for i, c in enumerate(children)
    ]
    if verdict != "green":
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False, output=None,
            extra={"verify_panel_results": panel_results, "verify_panel_greens": greens, "verify_panel_k": k},
        )
        executor.last_result = result
        return result
    output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
    output["verify_panel_results"] = panel_results
    output["verify_panel_greens"] = greens
    result = NodeExecutionResult(pending=False, output=output)
    executor.last_result = result
    return result


def _execute_loop_until_dry(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "loop-until-dry"`` (issue #207) : relance jusqu'à k tours sans nouveauté.

    Chaque tour doit porter, dans sa sortie, une clé ``novelty_key``
    (chaîne) — la déduplication porte contre **tout le vu** depuis le
    début (un ``set`` cumulatif sur tous les tours), jamais seulement
    contre le dernier tour retenu, comme l'issue le nomme explicitement.
    ``maxRounds`` (validé au chargement) est une borne dure : atteinte
    sans jamais sécher, le node ferme quand même — documenté comme
    distinct d'un séchage réel via ``stop_reason``.
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")
    lud = _node_config(executor, node_id).get("loopUntilDry") or {}
    max_rounds = int(lud["maxRounds"])

    seen: set[str] = set()
    rounds: list[dict[str, Any]] = []
    total_cost = 0.0
    cap = executor._pilot_policy.max_cost_usd_per_node
    stop_reason = "max_rounds"
    for i in range(max_rounds):
        if cap is not None and total_cost > cap:
            stop_reason = "cost_capped"
            break
        child = _launch_child(executor, 
            contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"round{i}",
            max_cost_usd=None, extra_context=f"Tour {i + 1}/{max_rounds}. [genre_attempt_index={i}]",
        )
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"loop-until-dry : tour {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="loop-until-dry", verdict="waiting_host",
                cost_usd=total_cost, attempts=child.attempts, escalations=child.escalations,
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "loop_until_dry_waiting_host"})
            executor.last_result = result
            return result
        if child.status != "finished":
            rounds.append({"index": i, "child_run_id": child.child_run_id, "status": child.status, "novel": False})
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="loop-until-dry", verdict="red",
                cost_usd=total_cost, attempts=child.attempts, escalations=child.escalations,
            )
            executor.blocked_node = node_id
            result = NodeExecutionResult(pending=False, output=None, extra={"loop_until_dry_rounds": rounds})
            executor.last_result = result
            return result
        raw_key = child.output.get("novelty_key") if isinstance(child.output, dict) else None
        novelty_key = raw_key if isinstance(raw_key, str) and raw_key else f"round-{i}"
        novel = novelty_key not in seen
        seen.add(novelty_key)
        rounds.append(
            {"index": i, "child_run_id": child.child_run_id, "status": child.status, "novel": novel, "novelty_key": novelty_key}
        )
        if not novel:
            stop_reason = "dry"
            break

    verdict = "cost_capped" if stop_reason == "cost_capped" else "green"
    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="loop-until-dry", verdict=verdict, cost_usd=total_cost,
        attempts=len(rounds), escalations=0,
    )
    output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
    output["loop_until_dry_rounds"] = rounds
    output["loop_until_dry_stop_reason"] = stop_reason
    if verdict != "green":
        executor.blocked_node = node_id
        result = NodeExecutionResult(pending=False, output=None, extra={"loop_until_dry_stop_reason": stop_reason})
        executor.last_result = result
        return result
    result = NodeExecutionResult(pending=False, output=output)
    executor.last_result = result
    return result


def _execute_judge(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "judge"`` (issue #207) : N tentatives sous angles imposés, un juge choisit.

    Les N tentatives sont des sous-flows (:meth:`_launch_child`, comme
    ``verify-panel``) ; le juge, lui, n'en est pas un — c'est le node lui
    décrivant un vocabulaire de revue (V1, « un humain choisit ») dispatché
    une seconde fois via :meth:`_execute_plain`, avec le résumé des N
    tentatives injecté dans le contexte de la tâche. La sortie du juge
    doit porter ``judge_winner`` (index 0..N-1) : sans lui, refus nommé.
    La trace des N tentatives (leurs ``child_run_id``) survit dans la
    sortie du node ET dans le Mission Ledger de chaque enfant.
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")
    j = _node_config(executor, node_id).get("judge") or {}
    n = int(j["n"])
    angles = list(j["angles"])

    candidates: list[_ChildRun] = []
    total_cost = 0.0
    cap = executor._pilot_policy.max_cost_usd_per_node
    for i in range(n):
        if cap is not None and total_cost > cap:
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="judge", verdict="cost_capped", cost_usd=total_cost,
                attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
            )
            executor.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None,
                extra={"judge_error": f"plafond dépassé après {len(candidates)}/{n} tentatives"},
            )
            executor.last_result = result
            return result
        child = _launch_child(executor, 
            contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"candidate{i}",
            max_cost_usd=None,
            extra_context=f"Angle imposé ({i + 1}/{n}) : {angles[i]} [genre_attempt_index={i}]",
        )
        candidates.append(child)
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"judge : tentative {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="judge", verdict="waiting_host", cost_usd=total_cost,
                attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "judge_waiting_host"})
            executor.last_result = result
            return result

    if not all(c.status == "finished" for c in candidates):
        _record_genre_outcome(executor, 
            node_id, run_id, verifiability_label="judge", verdict="red", cost_usd=total_cost,
            attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
        )
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False, output=None,
            extra={"judge_error": "une ou plusieurs tentatives n'ont pas fini vertes — rien à départager"},
        )
        executor.last_result = result
        return result

    trace = [
        {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
        for i, c in enumerate(candidates)
    ]
    summary = "\n".join(f"  [{row['index']}] run {row['child_run_id']} — coût {row['cost_usd']}" for row in trace)
    saved_context = executor._extra_context
    executor._extra_context = (
        f"{saved_context}\n\nTentatives à départager (indices 0..{n - 1}) :\n{summary}\n\n"
        "Réponds avec 'judge_winner' (l'index du gagnant) dans l'objet de sortie."
    ).strip()
    try:
        judge_result = executor._execute_plain(contract, context_pack)
    finally:
        executor._extra_context = saved_context

    if judge_result.pending or judge_result.output is None:
        # V2/host, ou rouge sur la phase de jugement elle-même : hérité
        # tel quel — les N tentatives restent dans le Mission Ledger de
        # leurs propres runs, rien n'est perdu même si le juge coince.
        return judge_result

    winner = judge_result.output.get("judge_winner")
    if not isinstance(winner, int) or isinstance(winner, bool) or not (0 <= winner < n):
        executor.blocked_node = node_id
        result = NodeExecutionResult(
            pending=False, output=None,
            extra={"judge_error": f"'judge_winner' absent ou hors bornes (reçu {winner!r}, attendu 0..{n - 1})"},
        )
        executor.last_result = result
        return result

    existing = executor.node_outcomes.get(node_id)
    judge_cost = existing.cost_usd if existing else None
    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="judge",
        verdict="green" if existing is None or existing.verdict == "green" else existing.verdict,
        cost_usd=total_cost + (judge_cost or 0.0),
        attempts=sum(c.attempts for c in candidates) + (existing.attempts if existing else 0),
        escalations=sum(c.escalations for c in candidates) + (existing.escalations if existing else 0),
    )
    output = dict(judge_result.output)
    output["judge_trace"] = trace
    output["judge_winner"] = winner
    result = NodeExecutionResult(pending=False, output=output)
    executor.last_result = result
    return result


def _execute_checkpoint(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "checkpoint"`` (issue #207) : approuver, refuser, amender — jamais auto-décidé.

    Toujours ``pending=True``, quelle que soit la cascade : un
    checkpoint est une décision d'hôte par construction, jamais
    rubber-stampée par un palier bon marché. La décision arrive via
    ``flow resume --result`` (forme interactive, même run) ; le contrat
    exact — ``checkpoint_decision`` ``approve``/``reject``/``amend`` et le
    motif — est vérifié par :func:`grimoire.flows.engine._checkpoint_faults`,
    pas ici : un refus (``reject``) bloque le node en nommant le motif
    dans le graphe de décision (l'événement ``STEP_FAILED`` du kernel),
    une reprise ultérieure rouvre exactement ce même node.
    """
    node_id = contract.node_id
    executor.host_node = node_id
    executor.host_reason = "checkpoint (issue #207) : décision d'hôte obligatoire — jamais auto-décidée par la cascade"
    result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "checkpoint", "node_id": node_id})
    executor.last_result = result
    return result


def _execute_budget(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "budget"`` (issue #207) : plafond déclaré, passes optionnelles abandonnées avant dépassement.

    ``config.budget.passes`` (validé au chargement) est une liste de
    refs ; la première est **obligatoire** (son échec fait échouer tout
    le node) ; chacune des suivantes est **optionnelle** — lancée
    seulement si le coût cumulé après les précédentes reste sous
    ``config.budget.maxCostUsd`` ; sinon, abandonnée (jamais tentée),
    sans faire échouer le node. Différent de ``pilot.max_cost_usd_per_node``
    : ce plafond-ci est déclaré par le blueprint, propre à ce node.
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")
    b = _node_config(executor, node_id).get("budget") or {}
    max_cost_usd = float(b["maxCostUsd"])
    passes = list(b["passes"])

    rows: list[dict[str, Any]] = []
    total_cost = 0.0
    for i, ref in enumerate(passes):
        mandatory = i == 0
        if not mandatory and total_cost >= max_cost_usd:
            rows.append({"index": i, "ref": ref, "launched": False})
            continue
        child = _launch_child(executor, 
            ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"pass{i}", max_cost_usd=None,
        )
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"budget : passe {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="budget", verdict="waiting_host", cost_usd=total_cost,
                attempts=child.attempts, escalations=child.escalations,
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "budget_waiting_host"})
            executor.last_result = result
            return result
        rows.append(
            {"index": i, "ref": ref, "launched": True, "child_run_id": child.child_run_id, "status": child.status, "cost_usd": child.cost_usd}
        )
        if mandatory and child.status != "finished":
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="budget", verdict="red", cost_usd=total_cost,
                attempts=child.attempts, escalations=child.escalations,
            )
            executor.blocked_node = node_id
            result = NodeExecutionResult(pending=False, output=None, extra={"budget_passes": rows})
            executor.last_result = result
            return result

    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="budget", verdict="green", cost_usd=total_cost,
        attempts=sum(1 for r in rows if r.get("launched")), escalations=0,
    )
    output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
    output["budget_passes"] = rows
    result = NodeExecutionResult(pending=False, output=output)
    executor.last_result = result
    return result


def _execute_replay_diff(executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
    """``kind: "replay-diff"`` (issue #207) : rejoue le même flow sur la même entrée, compare les traces.

    Deux runs enfants du même ``ref``, sans variation d'un à l'autre
    (aucun ``extra_context`` différenciant) : la divergence éventuelle
    est le signal utile, jamais un défaut qui ferait échouer le node —
    ``replay_diverged`` est exposé dans la sortie, le verdict reste vert
    tant que les deux réplications finissent (peu importe si elles
    divergent l'une de l'autre).
    """
    node_id = contract.node_id
    run_id = str(context_pack.get("run_id") or "no-run")
    blueprint_id = str(context_pack.get("blueprint_id") or "flow")
    cap = executor._pilot_policy.max_cost_usd_per_node

    children: list[_ChildRun] = []
    total_cost = 0.0
    for i in range(2):
        if cap is not None and total_cost > cap:
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="replay-diff", verdict="cost_capped", cost_usd=total_cost,
                attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
            )
            executor.blocked_node = node_id
            result = NodeExecutionResult(pending=False, output=None, extra={"replay_diff_error": "plafond dépassé avant la seconde relecture"})
            executor.last_result = result
            return result
        child = _launch_child(executor, 
            contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"replay{i}", max_cost_usd=None,
        )
        children.append(child)
        total_cost += child.cost_usd or 0.0
        if child.status == "waiting_host":
            executor.host_node = node_id
            executor.host_reason = f"replay-diff : relecture {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
            _record_genre_outcome(executor, 
                node_id, run_id, verifiability_label="replay-diff", verdict="waiting_host", cost_usd=total_cost,
                attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
            )
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "replay_diff_waiting_host"})
            executor.last_result = result
            return result

    traces: list[tuple[str, tuple[str, ...]]] = []
    for c in children:
        status = executor._engine.status(c.child_run_id, include_contract=False) if executor._engine else None
        traces.append((status.status, status.completed_nodes) if status is not None else (c.status, ()))
    diverged = traces[0] != traces[1]
    both_finished = all(c.status == "finished" for c in children)
    verdict = "green" if both_finished else "red"
    _record_genre_outcome(executor, 
        node_id, run_id, verifiability_label="replay-diff", verdict=verdict, cost_usd=total_cost,
        attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
    )
    rows = [{"index": i, "child_run_id": c.child_run_id, "status": c.status} for i, c in enumerate(children)]
    if verdict != "green":
        executor.blocked_node = node_id
        result = NodeExecutionResult(pending=False, output=None, extra={"replay_diff_children": rows})
        executor.last_result = result
        return result
    output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
    output["replay_diff_children"] = rows
    output["replay_diverged"] = diverged
    result = NodeExecutionResult(pending=False, output=output)
    executor.last_result = result
    return result


def _node_config(executor: DispatchExecutor, node_id: str) -> dict[str, Any]:
    """Le ``config`` du node courant, relu depuis le blueprint (issue #207).

    Les genres n'ont pas leur config sur ``NodeContract`` (qui ne porte
    que ce que tout node partage) : elle est déjà validée au chargement
    (:func:`grimoire.flows.blueprint_loader._validate_genre_config`), il
    suffit de la relire dans le blueprint source, une fois par appel —
    même coût qu'une relecture de contrat, déjà fait ailleurs dans ce
    module (aucune E/S supplémentaire notable, le fichier est petit et
    déjà en cache disque après le premier chargement du run).
    """
    blueprint = load_blueprint(executor._blueprint_path, executor._project_root)
    for node in blueprint.get("nodes", []):
        if node.get("id") == node_id:
            return dict(node.get("config") or {})
    return {}


#: Table de dispatch par ``kind`` (issues #206/#207) — un genre de plus n'est
#: jamais un ``if/elif`` de plus dans :meth:`DispatchExecutor.execute`, juste
#: une entrée ici. Toutes les valeurs de
#: :data:`grimoire.flows.blueprint_loader.GENRE_KINDS` doivent y figurer —
#: leur absence serait un genre validé au chargement mais jamais exécutable,
#: un fossé que ``tests/unit/test_flows_genres.py`` couvre.
_GENRE_EXECUTORS: dict[str, Callable[[DispatchExecutor, NodeContract, dict[str, Any]], NodeExecutionResult]] = {
    "composite": _execute_composite,
    "fanout": _execute_fanout,
    "verify-panel": _execute_verify_panel,
    "loop-until-dry": _execute_loop_until_dry,
    "judge": _execute_judge,
    "checkpoint": _execute_checkpoint,
    "budget": _execute_budget,
    "replay-diff": _execute_replay_diff,
}


def execute_genre_node(
    executor: DispatchExecutor, contract: NodeContract, context_pack: dict[str, Any]
) -> NodeExecutionResult | None:
    """Point d'entrée unique de ce module, appelé par ``DispatchExecutor.execute`` (import différé).

    Rend ``None`` (jamais un refus) quand ``contract.kind`` n'est pas un genre —
    l'appelant retombe alors sur le dispatch ordinaire (``_execute_plain``).
    """
    genre_fn = _GENRE_EXECUTORS.get(contract.kind)
    if genre_fn is None:
        return None
    return genre_fn(executor, contract, context_pack)


def _total_cost(executor: DispatchExecutor) -> float | None:
    known = [o.cost_usd for o in executor.node_outcomes.values() if o.cost_usd is not None]
    return sum(known) if known else None


def _drive_composite_child(
    engine: FlowEngine,
    run_id: str,
    executor: DispatchExecutor,
    result: NodeExecutionResult,
    *,
    max_cost_usd: float | None,
) -> tuple[str, tuple[str, ...], float | None, str | None, dict[str, Any] | None]:
    """Enchaîne les nodes du sous-flow d'un node composite (issue #206) — variante coût-plafonné de :func:`_drive`.

    Même boucle que :func:`_drive`, avec une différence : le plafond de coût
    du pilote (issue #209) est vérifié à chaque tour, **avant** de faire
    avancer l'enfant d'un node de plus — jamais après un node déjà vert
    (même garantie que ``run_dispatch`` applique à un dispatch simple). Un
    dépassement abandonne le run enfant (``FlowEngine.abort``, motif nommé)
    plutôt que de le laisser suspendu à mi-chemin sans qu'aucun futur
    ``resume`` ne le débloque.

    Rend ``(status, faults, total_cost, host_reason, last_output)`` où
    ``status`` vaut ``"finished"``, ``"blocked"``, ``"waiting_host"`` ou
    ``"cost_capped"``. ``last_output`` (issue #207) est la sortie soumise
    pour le DERNIER node exécuté de l'enfant quand ``status == "finished"``
    (``None`` sinon) — un genre à plusieurs tentatives (``loop-until-dry``)
    y lit une clé annexe (``novelty_key``) que ``check_output_against_contract``
    ignore déjà (il ne regarde jamais que ``"pins"``).
    """
    while True:
        if result.pending:
            return "waiting_host", (), _total_cost(executor), executor.host_reason, None
        if result.output is None:
            return "blocked", (), _total_cost(executor), None, None
        total = _total_cost(executor)
        if max_cost_usd is not None and total is not None and total > max_cost_usd:
            engine.abort(
                run_id,
                reason=f"plafond du pilote dépassé pour le sous-flow ({total} USD > {max_cost_usd} USD, issue #206/#209)",
            )
            return "cost_capped", (), total, None, None
        last_output = result.output
        outcome: ResumeOutcome = engine.resume(run_id, output=result.output, executor=executor)
        if not outcome.ok:
            return "blocked", outcome.faults, _total_cost(executor), None, None
        if outcome.finished:
            return "finished", (), _total_cost(executor), None, last_output
        assert outcome.contract is not None  # non fini : le moteur a rouvert le node suivant
        result = executor.last_result or NodeExecutionResult(pending=True)
