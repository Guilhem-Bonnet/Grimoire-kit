"""``grimoire route report`` et ``grimoire route eval`` — mesurer le routeur avant de l'activer (K9, #774).

``report`` ne fait que compter, depuis le Mission Ledger : réussite au premier essai (pass^1)
par couple (type, classe, modèle), escalade par couple, et — en mode ``shadow`` — l'accord
entre ce que le routeur aurait fait (``task.routed``) et ce que le dispatch a fait (la première
tentative de ``task.dispatched``). Seuls les événements ``shadow`` comptent ; une issue
``session``, ``defer`` ou ``refuse`` suivie d'un appel est un désaccord. Sous 5 observations, la réussite est « non mesuré », jamais 0.

``eval`` rejoue un jeu fixe de tâches V0/V1 closes, chacune avec ses résultats enregistrés par
modèle (``results``), contre la base « fixe » actuelle (V0 vers ``cheap``, V1 vers ``mid``,
premier modèle du palier). Le seuil d'activation (décision D16) : pass^1 du routeur >= pass^1
de la base - 1 tâche, **et** coût connu <= celui de la base. Un coût inconnu, un résultat
manquant ou un jeu de moins de 20 tâches rendent « non mesuré » : on n'active pas sur un
chiffre qu'on n'a pas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.costs import Cost
from grimoire.missions.dispatch_history import MIN_OBSERVATIONS, compute_dispatch_history
from grimoire.missions.route_dispatch import ROUTED_EVENT
from grimoire.missions.router import FLOOR_BY_CLASS, RouteHistory, RoutingTask, replay_route_history, route
from grimoire.missions.routing_policy import DEFAULT_POLICY, RoutingPolicy

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.router import ProviderView

__all__ = ["EVAL_MIN_TASKS", "NOT_MEASURED", "EvalSetError", "build_report", "evaluate"]

NOT_MEASURED = "non mesuré"

#: Taille du jeu d'évaluation : le seuil D16 est « une tâche sur 20 ».
EVAL_MIN_TASKS = 20


def build_report(ledger: MissionLedger) -> dict[str, Any]:
    """Le rapport du routeur, calculé depuis le ledger — rien n'est stocké à côté."""
    history = replay_route_history(ledger)
    models = [
        {
            "task_type": r.task_type,
            "verifiability": r.verifiability,
            "model": r.model,
            "observations": r.observations,
            "successes": r.successes,
            "pass1": r.rate,
            "pass1_label": NOT_MEASURED if r.rate is None else f"{r.rate:.0%}",
            "cost": r.mean_cost.to_dict(),
        }
        for r in sorted(history.rows, key=lambda r: (r.task_type, r.verifiability, r.model))
    ]
    escalation = [
        {
            "task_type": c.task_type,
            "verifiability": c.verifiability,
            "observations": c.observations,
            "escalation": {
                tier: stats.rate if stats.observations >= MIN_OBSERVATIONS else None
                for tier, stats in c.by_start_tier.items()
            },
            "escalation_label": {
                tier: NOT_MEASURED if stats.observations < MIN_OBSERVATIONS else f"{stats.rate:.0%}"
                for tier, stats in c.by_start_tier.items()
            },
        }
        for c in compute_dispatch_history(ledger)
    ]
    by_mode: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    pending: dict[str, dict[str, Any]] = {}
    compared: list[dict[str, Any]] = []
    for event in ledger.list_events(None):
        p = event.payload
        if event.event_type == ROUTED_EVENT:
            by_mode[str(p.get("mode"))] = by_mode.get(str(p.get("mode")), 0) + 1
            by_outcome[str(p.get("outcome"))] = by_outcome.get(str(p.get("outcome")), 0) + 1
            pending[str(p.get("task_id"))] = p
        elif event.event_type == "task.dispatched" and p.get("attempt") == 1 and str(p.get("task_id")) in pending:
            routed = pending.pop(str(p.get("task_id")))
            if routed.get("mode") != "shadow":
                continue  # l'accord se mesure en shadow : en `active` le dispatch suit le routeur, il s'accorde par construction
            decision = routed.get("decision", {})
            outcome = routed.get("outcome")
            routed_pair = [decision.get("tier"), decision.get("model")]
            compared.append(
                {
                    "task_id": p.get("task_id"),
                    "routed_outcome": outcome,
                    "routed": routed_pair,
                    "dispatched": [p.get("tier"), p.get("model")],
                    # `session`, `defer`, `refuse` suivis d'un appel : le routeur n'aurait pas appelé, ce n'est pas un accord.
                    "agree": outcome == "route" and routed_pair == [p.get("tier"), p.get("model")],
                }
            )
    agree = sum(1 for c in compared if c["agree"])
    return {
        "models": models,
        "escalation": escalation,
        "routed": {"events": sum(by_mode.values()), "by_mode": by_mode, "by_outcome": by_outcome},
        "shadow_comparison": {
            "compared": len(compared),
            "agree": agree,
            "agreement": NOT_MEASURED if not compared else f"{agree / len(compared):.0%}",
            "disagreements": [c for c in compared if not c["agree"]],
        },
    }


class EvalSetError(ValueError):
    """Le jeu d'évaluation est illisible ou mal formé."""


@dataclass(frozen=True, slots=True)
class _EvalTask:
    task: RoutingTask
    results: dict[str, tuple[bool, float | None]]


def _parse_set(data: Any) -> tuple[_EvalTask, ...]:
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tasks"), list):
        raise EvalSetError("jeu d'évaluation : attendu `version: 1` et une liste `tasks`")
    tasks: list[_EvalTask] = []
    for entry in data["tasks"]:
        if not isinstance(entry, dict) or entry.get("class") not in FLOOR_BY_CLASS or not entry.get("id"):
            raise EvalSetError("jeu d'évaluation : chaque tâche a un `id` et une `class` V0 ou V1")
        results: dict[str, tuple[bool, float | None]] = {}
        for model, outcome in (entry.get("results") or {}).items():
            if not isinstance(outcome, dict) or not isinstance(outcome.get("passed"), bool):
                raise EvalSetError(f"tâche {entry['id']} : résultat de {model} sans `passed` booléen")
            cost = outcome.get("cost_usd")
            results[str(model)] = (outcome["passed"], None if cost is None else float(cost))
        risk = tuple(sorted({str(r).strip().lower() for r in entry.get("risk", ()) if str(r).strip()}))
        tasks.append(
            _EvalTask(
                RoutingTask(
                    str(entry["id"]), str(entry["class"]), str(entry.get("type", "implementation")).strip().lower(), risk,
                    int(entry.get("exclusive_files", 0)), bool(entry.get("needs_local", False)),
                ),
                results,
            )
        )
    return tuple(tasks)


def _arm(chosen: list[str | None], tasks: tuple[_EvalTask, ...]) -> dict[str, Any]:
    """Le score d'un bras : réussites, coût (jamais 0 quand il est inconnu) et tâches non mesurées."""
    passes, cost, unmeasured = 0, Cost.none(), []
    for task, model in zip(tasks, chosen, strict=True):
        outcome = task.results.get(model) if model is not None else None
        if outcome is None:
            unmeasured.append(task.task.task_id)
            continue
        passes += outcome[0]
        cost = cost + Cost.coerce(outcome[1])
    return {"pass": passes, "cost": cost.to_dict(), "unmeasured": unmeasured, "_cost": cost}


def evaluate(
    data: Any, view: ProviderView, *, policy: RoutingPolicy = DEFAULT_POLICY, tolerance: int = 1
) -> dict[str, Any]:
    """Compare le routeur à la base fixe sur *data* (le jeu déjà chargé) et rend un verdict D16."""
    tasks = _parse_set(data)
    history = RouteHistory()
    router_models = [route(t.task, history, view, policy=policy).model for t in tasks]
    base_models = []
    for t in tasks:
        tier = FLOOR_BY_CLASS[t.task.verifiability]
        base_models.append(next((o.model for o in view.models if o.tier == tier and o.available), None))
    router, base = _arm(router_models, tasks), _arm(base_models, tasks)
    reasons: list[str] = []
    if len(tasks) < EVAL_MIN_TASKS:
        reasons.append(f"jeu de {len(tasks)} tâches : moins de {EVAL_MIN_TASKS}")
    for name, arm in (("routeur", router), ("base", base)):
        if arm["unmeasured"]:
            reasons.append(f"{name} : résultat enregistré manquant pour {arm['unmeasured']}")
        elif arm["_cost"].status != "exact":
            reasons.append(f"{name} : coût inconnu ({arm['_cost'].render()})")
    if reasons:
        verdict = NOT_MEASURED
    elif router["pass"] >= base["pass"] - tolerance and router["_cost"].known_usd <= base["_cost"].known_usd:
        verdict = "pass"
        reasons.append("pass^1 du routeur >= base - 1 tâche, coût connu <= base")
    else:
        verdict = "fail"
        reasons.append(f"routeur {router['pass']}/{len(tasks)} à {router['_cost'].render()} contre base {base['pass']}/{len(tasks)} à {base['_cost'].render()}")
    for arm in (router, base):
        arm.pop("_cost")
    return {
        "verdict": verdict,
        "activable": verdict == "pass",
        "tasks": len(tasks),
        "tolerance_tasks": tolerance,
        "router": router,
        "baseline": base,
        "reasons": reasons,
    }
