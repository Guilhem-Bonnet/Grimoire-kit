"""Le routeur branché sur ``run_dispatch`` : l'événement ``task.routed`` et le mode ``active`` (K9, #774).

Chaque dispatch laisse un événement ``task.routed`` au Mission Ledger **avant** sa cascade,
avec les entrées (de quoi rejouer la décision, :func:`replay_event`), l'empreinte de ces
entrées et les raisons. En mode ``shadow`` (le défaut) le dispatch suit l'ancien chemin et
l'événement ne sert qu'à comparer ; en mode ``active`` la décision gouverne le palier de
départ, l'ordre des fournisseurs et le modèle choisi dans le palier.

Le mode ne se lit que dans une politique **valide** : une politique illisible ne peut ni
activer le routeur ni être remplacée par le défaut — la décision tracée est ``refuse`` et le
dispatch **refuse la cascade** (``routing_policy_invalid`` : aucune route autonome). En mode
``active``, une décision ``session`` ou ``defer`` n'est pas contournée non plus
(``routed_to_session``, ``routed_defer``) : la cascade ne part pas, la session ou l'admission
décide. Seul un routeur **en panne** (exception) laisse le dispatch suivre l'ancien chemin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.missions.router import (
    ProviderView,
    RouteDecision,
    RouteHistory,
    RoutingTask,
    inputs_dict,
    inputs_digest,
    model_sort_key,
    provider_view,
    replay_route_history,
    required_currencies,
    route,
    routing_task_from,
    unit_cost_per_mtok,
)
from grimoire.missions.routing_policy import PolicyLoad, RoutingPolicy, load_policy, parse_policy

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService
    from grimoire.missions.verifiability import Verifiability
    from grimoire.providers.registry import ModelSpec, ProviderSpec

__all__ = ["ROUTED_EVENT", "DispatchRouting", "record_route", "replay_event"]

ROUTED_EVENT = "task.routed"


@dataclass(frozen=True, slots=True)
class DispatchRouting:
    """Ce que ``run_dispatch`` consulte : inactif (shadow, panne) ou actif, et les refus de la politique."""

    root: Path
    task: RoutingTask
    history: RouteHistory
    decision: RouteDecision | None
    active: bool = False
    #: Mode lu dans une politique valide (``shadow`` par défaut, aussi quand elle est illisible).
    mode: str = "shadow"
    #: Le fichier de politique existe mais ne se valide pas : ``on_invalid: refuse``.
    policy_invalid: bool = False
    #: Monnaies imposées par les règles qui correspondent ; plusieurs : aucun fournisseur admissible.
    currencies: tuple[str, ...] = ()
    #: ``max_attempts`` de la politique ; appliqué seulement quand le routeur gouverne (mode actif).
    max_attempts: int | None = None

    def exhausted(self, calls: int) -> bool:
        """Vrai quand *calls* appels ont déjà eu lieu et que la politique active n'en permet pas davantage."""
        return self.active and self.max_attempts is not None and calls >= self.max_attempts

    def refusal(self) -> tuple[str, str] | None:
        """``(code, raison)`` quand la cascade autonome ne doit pas partir, ``None`` sinon.

        Politique illisible : toujours. Mode ``active`` : une issue ``session`` ou ``defer``.
        En ``shadow`` la décision n'est qu'une trace, l'ancien chemin gouverne.
        """
        decision = self.decision
        if self.policy_invalid:
            return "routing_policy_invalid", "; ".join(decision.reasons) if decision is not None else "politique illisible"
        if self.mode == "active" and decision is not None and decision.outcome in ("session", "defer"):
            code = "routed_to_session" if decision.outcome == "session" else "routed_defer"
            return code, "routeur : " + "; ".join(decision.reasons[-2:])
        return None

    def start(self) -> tuple[str, str] | None:
        """``(palier, raison)`` de départ quand le routeur gouverne, ``None`` sinon."""
        if not self.active or self.decision is None or self.decision.tier is None:
            return None
        return self.decision.tier, "routeur : " + "; ".join(self.decision.reasons[-2:])

    def _key(self, provider: ProviderSpec, model: ModelSpec, position: int, order: int) -> tuple[Any, ...]:
        return model_sort_key(model.id, self.task, self.history, unit_cost_per_mtok(model.id), (order, position))

    def _admits(self, provider: ProviderSpec, tier: str) -> bool:
        """Le fournisseur est admis par la décision : monnaie requise, et, au palier retenu, son classement."""
        if self.currencies and (len(self.currencies) > 1 or provider.currency not in self.currencies):
            return False
        decision = self.decision
        if decision is not None and decision.tier == tier and decision.ranking:
            return any(pid == provider.id for pid, _ in decision.ranking)
        return True

    def order(self, tier: str, providers: tuple[ProviderSpec, ...]) -> tuple[ProviderSpec, ...]:
        """Les fournisseurs admis du palier, du mieux classé au moins bien — l'ordre déclaré hors mode actif."""
        if not self.active:
            return providers
        from datetime import UTC, datetime

        from grimoire.providers.state import available_models, load_state

        state, now = load_state(self.root), datetime.now(UTC)
        admitted = tuple(p for p in providers if self._admits(p, tier))

        def best(item: tuple[int, ProviderSpec]) -> tuple[Any, ...]:
            order, provider = item
            keys = [self._key(provider, m, i, order) for i, m in enumerate(available_models(provider, tier, state, now=now))]
            return min(keys) if keys else ((9,),)

        return tuple(p for _, p in sorted(enumerate(admitted), key=best))

    def pick(self, provider: ProviderSpec, tier: str, available: tuple[ModelSpec, ...]) -> str:
        """Le modèle à appeler : le premier non suspendu (ancien chemin), ou le mieux classé en mode actif."""
        if not self.active or len(available) < 2:
            return available[0].id
        return min(enumerate(available), key=lambda item: self._key(provider, item[1], item[0], 0))[1].id


def _autonomous() -> bool:
    return os.environ.get("GRIMOIRE_UNATTENDED", "").strip() == "1"


def _plan(root: Path, ledger: MissionLedger, task: MissionTask, verifiability: str) -> tuple[PolicyLoad, RoutingTask, RouteHistory, ProviderView | None, RouteDecision | None]:
    load = load_policy(root)
    facts = routing_task_from(task, verifiability)
    if load.policy is None:
        return load, facts, RouteHistory(), None, None
    history = replay_route_history(ledger)
    view = provider_view(root, autonomous=_autonomous(), invocable_only=True)
    return load, facts, history, view, route(facts, history.for_couple(facts.task_type, facts.verifiability), view, policy=load.policy)


def record_route(service: TaskService, task: MissionTask, verifiability: Verifiability, *, actor: str, write: bool = True) -> DispatchRouting:
    """Décide la route de *task* et la trace (``task.routed``) sauf si ``write`` est faux (``--dry-run``).

    Un routeur en panne ne casse jamais le dispatch : l'événement porte alors ``error`` et une
    décision ``refuse``, le dispatch suit l'ancien chemin. Une politique illisible, elle, est un
    refus (:meth:`DispatchRouting.refusal`) : on ne remplace pas par le défaut ce qu'on n'a pas lu.
    """
    root, ledger = service.project_root, service.ledger
    facts = routing_task_from(task, verifiability.value)
    history, decision, error = RouteHistory(), None, None
    invalid, currencies, max_attempts = False, tuple[str, ...](), None
    payload: dict[str, Any]
    try:
        load, facts, history, view, decision = _plan(root, ledger, task, verifiability.value)
        mode = load.policy.mode if load.policy is not None else "shadow"
        payload = {"policy_source": load.source, "policy_error": load.error}
        if load.policy is None or view is None or decision is None:
            reason = f"politique illisible : {load.error}"
            decision, invalid = RouteDecision("refuse", reasons=(reason,)), True
        else:
            currencies, max_attempts = required_currencies(facts, load.policy), load.policy.max_attempts
            payload["inputs"] = inputs_dict(facts, history, view, load.policy)
    except Exception as exc:
        mode, error = "shadow", f"{type(exc).__name__}: {exc}"
        decision = RouteDecision("refuse", reasons=(f"routeur en erreur : {error}",))
        payload = {"policy_source": "error", "policy_error": error}
    payload.update(task_id=task.id, mode=mode, outcome=decision.outcome, reasons=list(decision.reasons), decision=decision.to_dict())
    if write:
        ledger.append_event(ROUTED_EVENT, task.id, "task", actor, payload)
    return DispatchRouting(
        root, facts, history.for_couple(facts.task_type, facts.verifiability), decision,
        mode == "active" and decision.outcome == "route", mode, invalid, currencies, max_attempts,
    )


def replay_event(payload: dict[str, Any]) -> tuple[RouteDecision | None, bool, str]:
    """Rejoue un événement ``task.routed`` : ``(décision rejouée, identique à l'enregistrée ?, empreinte)``.

    Une décision sans ``inputs`` (politique illisible, routeur en erreur) ne se rejoue pas :
    ``(None, True, "")`` — il n'y a rien à vérifier, le refus est dans l'événement.
    """
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return None, True, ""
    task = RoutingTask.from_dict(inputs["task"])
    history = RouteHistory.from_dict(inputs["history"])
    view = ProviderView.from_dict(inputs["providers"])
    policy: RoutingPolicy = parse_policy(inputs["policy"])
    replayed = route(task, history, view, policy=policy)
    digest = inputs_digest(task, history, view, policy)
    recorded = payload.get("decision", {})
    same = digest == recorded.get("inputs_digest") and replayed.to_dict() == recorded
    return replayed, same, digest

