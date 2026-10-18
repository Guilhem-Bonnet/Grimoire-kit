"""Le routeur de LLM : ``route(task, history, provider_state) -> RouteDecision`` (K9, issue #774).

Le routeur choisit un modèle **uniquement** d'après la tâche (classe V0/V1/V2, type,
difficulté), l'historique de réussite par modèle et la disponibilité du fournisseur.
Il **ne lit jamais** le quota, le budget ni les tokens restants, et ne change jamais
de modèle pour économiser : ces notions relèvent de l'admission (K10), pas du choix.
La garantie est structurelle — aucun des types qu'il reçoit (:class:`RoutingTask`,
:class:`RouteHistory`, :class:`ProviderView`) n'a un champ de quota, de budget ou de
tokens, et un test le vérifie sur la signature et sur les champs. Un fournisseur
suspendu pour forfait épuisé y apparaît comme **indisponible** (sa date de
réouverture), exactement comme un fournisseur injoignable : c'est le refus du
fournisseur qui compte, pas le niveau du forfait.

Ordre de la décision (voir :func:`route`) :

1. plancher de la classe (V0 ``cheap``, V1 ``mid``, V2 aucune route autonome) ;
2. règles de la politique : maximum des ``min_tier``, ``route: session`` absorbant ;
3. historique : peut seulement *monter* le palier (principe de ``--start-tier``) ;
4. filtrage des modèles du palier : suspendus, refroidis ou non sondés sont écartés
   et listés dans ``excluded`` avec leur raison ;
5. départage dans le palier : réussite au premier essai (>= 5 observations), puis
   coût unitaire connu croissant, puis ordre déclaré. Pas de tourniquet ;
6. aucun candidat : ``Defer`` — jamais une descente de palier.

Le routeur n'a ni modèle appris ni poids : l'historique est un tableau de compteurs
rejoué depuis le Mission Ledger (:func:`replay_route_history`), borné aux 50 dernières
observations par couple (type, classe, modèle).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.costs import Cost
from grimoire.missions.dispatch_history import MIN_OBSERVATIONS, recommend_start_tier
from grimoire.missions.routing_policy import (
    DEFAULT_POLICY,
    SESSION,
    RoutingPolicy,
    rule_matches,
)
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS
from grimoire.tools.cost_model import rate_for

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "FLOOR_BY_CLASS",
    "HISTORY_WINDOW",
    "ModelOption",
    "ProviderView",
    "RouteDecision",
    "RouteHistory",
    "RoutingTask",
    "inputs_digest",
    "model_sort_key",
    "provider_view",
    "replay_route_history",
    "route",
    "routing_task_from",
    "unit_cost_per_mtok",
]

#: Plancher de palier par classe : la classe décide qui a le droit d'appeler.
FLOOR_BY_CLASS: dict[str, str] = {"V0": "cheap", "V1": "mid"}

#: Fenêtre d'observations par couple (type, classe, modèle) — décision D13.
HISTORY_WINDOW = 50

#: Verdicts de ``task.dispatched`` qui disent quelque chose du modèle ; un 429, un
#: timeout ou une panne d'appel sont « non attribués » et ne comptent jamais comme
#: un succès (ni comme un échec du modèle).
_ATTRIBUTABLE = frozenset({"green", "red", "unrunnable"})


# ── Entrées : la tâche, l'historique, l'état des fournisseurs ───────────────────


@dataclass(frozen=True, slots=True)
class RoutingTask:
    """Ce que le routeur a le droit de savoir de la tâche. Rien d'autre."""

    task_id: str
    verifiability: str
    task_type: str
    risk_tags: tuple[str, ...] = ()
    exclusive_files: int = 0
    needs_local: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verifiability": self.verifiability,
            "task_type": self.task_type,
            "risk_tags": sorted(self.risk_tags),
            "exclusive_files": self.exclusive_files,
            "needs_local": self.needs_local,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingTask:
        return cls(
            task_id=str(data["task_id"]),
            verifiability=str(data["verifiability"]),
            task_type=str(data["task_type"]),
            risk_tags=tuple(sorted(str(t) for t in data.get("risk_tags", ()))),
            exclusive_files=int(data.get("exclusive_files", 0)),
            needs_local=bool(data.get("needs_local", False)),
        )


def routing_task_from(task: MissionTask, verifiability: str) -> RoutingTask:
    """Les faits de routage d'une tâche du ledger.

    Les étiquettes de risque viennent du type de la tâche, de son profil de risque
    critique et des ``labels`` du board ; la taille, de ses fichiers exclusifs.
    """
    tags = {task.type.value}
    if task.risk_profile.value == "security_critical":
        tags.add("security")
    labels = task.extra.get("labels")
    if isinstance(labels, (list, tuple)):
        tags.update(str(label).strip().lower() for label in labels if str(label).strip())
    return RoutingTask(
        task_id=task.id,
        verifiability=verifiability,
        task_type=task.type.value,
        risk_tags=tuple(sorted(tags)),
        exclusive_files=len(task.claim.exclusive_files) if task.claim is not None else 0,
        needs_local=bool(task.extra.get("needs_local")),
    )


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Les compteurs d'un couple (type, classe, modèle) sur la fenêtre."""

    task_type: str
    verifiability: str
    model: str
    observations: int
    successes: int
    cost_known_usd: float = 0.0
    cost_priced: int = 0
    cost_unpriced: int = 0

    @property
    def rate(self) -> float | None:
        """Réussite au premier essai (pass^1), ``None`` sous le seuil d'observations."""
        return self.successes / self.observations if self.observations >= MIN_OBSERVATIONS else None

    @property
    def mean_cost(self) -> Cost:
        """Coût moyen observé par tentative — inconnu si aucune tentative n'a rendu le sien."""
        if self.cost_priced == 0:
            return Cost.unpriced(self.cost_unpriced or 1)
        return Cost.from_parts(self.cost_known_usd / self.cost_priced, self.cost_unpriced)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "verifiability": self.verifiability,
            "model": self.model,
            "observations": self.observations,
            "successes": self.successes,
            "cost_known_usd": round(float(self.cost_known_usd), 6),
            "cost_priced": self.cost_priced,
            "cost_unpriced": self.cost_unpriced,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryRow:
        return cls(
            task_type=str(data["task_type"]),
            verifiability=str(data["verifiability"]),
            model=str(data["model"]),
            observations=int(data["observations"]),
            successes=int(data["successes"]),
            cost_known_usd=float(data.get("cost_known_usd", 0.0)),
            cost_priced=int(data.get("cost_priced", 0)),
            cost_unpriced=int(data.get("cost_unpriced", 0)),
        )


@dataclass(frozen=True, slots=True)
class RouteHistory:
    """Compteurs rejoués depuis le ledger : le même ledger donne la même table."""

    rows: tuple[HistoryRow, ...] = ()
    #: ``(type, classe) -> (palier recommandé, pourquoi)``, de ``dispatch_history``.
    start_tiers: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    def row(self, task_type: str, verifiability: str, model: str) -> HistoryRow | None:
        for row in self.rows:
            if (row.task_type, row.verifiability, row.model) == (task_type, verifiability, model):
                return row
        return None

    def for_couple(self, task_type: str, verifiability: str) -> RouteHistory:
        """La tranche que la décision lit — c'est elle qui est tracée et rejouée."""
        rows = tuple(r for r in self.rows if (r.task_type, r.verifiability) == (task_type, verifiability))
        tiers = {k: v for k, v in self.start_tiers.items() if k == (task_type, verifiability)}
        return RouteHistory(rows, tiers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in sorted(self.rows, key=lambda r: (r.task_type, r.verifiability, r.model))],
            "start_tiers": [
                {"task_type": t, "verifiability": c, "tier": tier, "reason": why}
                for (t, c), (tier, why) in sorted(self.start_tiers.items())
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteHistory:
        tiers = {
            (str(e["task_type"]), str(e["verifiability"])): (str(e["tier"]), str(e["reason"]))
            for e in data.get("start_tiers", ())
        }
        return cls(tuple(HistoryRow.from_dict(r) for r in data.get("rows", ())), tiers)


def replay_route_history(ledger: MissionLedger) -> RouteHistory:
    """Les compteurs par (type, classe, modèle), rejoués depuis les ``task.dispatched``.

    Chaque tentative porte son propre résultat (``green``, ``red``, ``unrunnable``), compté
    pour le couple de **cette** tentative. Une tentative sans résultat propre (429, timeout,
    panne d'appel) est non attribuée : elle n'est jamais comptée comme un succès. La fenêtre
    garde les :data:`HISTORY_WINDOW` dernières observations par couple.
    """
    windows: dict[tuple[str, str, str], deque[tuple[bool, float | None]]] = defaultdict(
        lambda: deque(maxlen=HISTORY_WINDOW)
    )
    couples: set[tuple[str, str]] = set()
    for event in ledger.list_events(None):
        if event.event_type != "task.dispatched":
            continue
        p = event.payload
        task_type, verifiability, model, verdict = p.get("task_type"), p.get("verifiability"), p.get("model"), p.get("verdict")
        if not (task_type and verifiability and model) or verdict not in _ATTRIBUTABLE:
            continue
        cost = p.get("cost_usd")
        windows[(str(task_type), str(verifiability), str(model))].append(
            (verdict == "green", float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)
        )
        couples.add((str(task_type), str(verifiability)))
    rows: list[HistoryRow] = []
    for (task_type, verifiability, model), window in windows.items():
        priced = [c for _, c in window if c is not None]
        rows.append(
            HistoryRow(
                task_type, verifiability, model, len(window), sum(1 for ok, _ in window if ok),
                float(sum(priced)), len(priced), len(window) - len(priced),
            )
        )
    start_tiers = {
        (t, c): recommend_start_tier(ledger, task_type=t, verifiability=c, floor=FLOOR_BY_CLASS.get(c, "cheap"))
        for t, c in couples
    }
    return RouteHistory(tuple(rows), start_tiers)


@dataclass(frozen=True, slots=True)
class ModelOption:
    """Un modèle déclaré, et ce qu'on sait de sa disponibilité — pas de son forfait."""

    provider: str
    model: str
    tier: str
    currency: str | None = None
    order: int = 0
    available: bool = True
    reason: str = ""
    until: str | None = None
    unit_cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "currency": self.currency,
            "order": self.order,
            "available": self.available,
            "reason": self.reason,
            "until": self.until,
            "unit_cost": self.unit_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelOption:
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            tier=str(data["tier"]),
            currency=data.get("currency"),
            order=int(data.get("order", 0)),
            available=bool(data.get("available", True)),
            reason=str(data.get("reason", "")),
            until=data.get("until"),
            unit_cost=data.get("unit_cost"),
        )


@dataclass(frozen=True, slots=True)
class ProviderView:
    """La disponibilité des modèles, dans l'ordre déclaré. Pas de quota, pas de budget."""

    models: tuple[ModelOption, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"models": [m.to_dict() for m in self.models]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderView:
        return cls(tuple(ModelOption.from_dict(m) for m in data.get("models", ())))


def unit_cost_per_mtok(model: str) -> float | None:
    """Prix de référence du modèle ($/MTok entrée + sortie), ``None`` s'il n'est pas dans la table.

    Indépendant du budget restant : c'est un prix catalogue, pas une dépense.
    """
    rate = rate_for(model)
    return None if rate is None else float(rate["in"]) + float(rate["out"])


def provider_view(
    root: Path, *, now: datetime | None = None, autonomous: bool = False, invocable_only: bool = False, offline: bool = False
) -> ProviderView:
    """La vue de disponibilité du registre, pour le routeur.

    La disponibilité d'un modèle est celle de ``providers.routing.candidates`` — la même
    notion de « disponible maintenant » que le dispatch. ``quota_observation`` de l'état
    runtime n'est **jamais** lu ici. ``offline`` ignore l'état runtime (évaluation hors ligne).
    """
    from grimoire.providers.registry import read_default_fallback_chain, read_registry
    from grimoire.providers.routing import _candidate_order, candidates
    from grimoire.providers.state import load_state

    moment = now if now is not None else datetime.now(UTC)
    providers = read_registry(root)
    by_id = {p.id: p for p in providers}
    order = {pid: i for i, pid in enumerate(_candidate_order(providers, read_default_fallback_chain(root)))}
    state = {} if offline else load_state(root)
    options: list[ModelOption] = []
    for tier in SUPPORTED_MODEL_TIERS:
        ok_ids = {p.id for p in (by_id.values() if offline else candidates(root, tier, now=moment, autonomous=autonomous))}
        for provider in sorted(providers, key=lambda p: order.get(p.id, len(order))):
            runtime = state.get(provider.id)
            for model in provider.models_for_tier(tier):
                reason, until = "", None
                if not provider.enabled:
                    reason = "provider_disabled"
                elif invocable_only and not provider.invocation:
                    reason = "no_invocation"
                elif runtime is not None and runtime.is_model_suspended(model.id, now=moment):
                    reason, until = "model_suspended", _iso(runtime.model_suspensions[model.id].until)
                elif provider.id not in ok_ids:
                    reason, until = _provider_reason(runtime, moment, autonomous)
                options.append(
                    ModelOption(
                        provider.id, model.id, tier, provider.currency, order.get(provider.id, len(order)),
                        reason == "", reason, until, unit_cost_per_mtok(model.id),
                    )
                )
    return ProviderView(tuple(options))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _provider_reason(runtime: Any, moment: datetime, autonomous: bool) -> tuple[str, str | None]:
    """Pourquoi un fournisseur n'est pas candidat, et jusqu'à quand quand on le sait."""
    if runtime is None:
        return ("provider_unprobed", None) if autonomous else ("provider_unavailable", None)
    if runtime.is_suspended(now=moment):
        return "provider_suspended", _iso(runtime.suspension.until)
    if runtime.is_cooling_down(now=moment):
        return "provider_cooling_down", _iso(runtime.cooldown_until)
    if not runtime.available:
        return "provider_unavailable", None
    if autonomous and runtime.probed_at is None:
        return "provider_unprobed", None
    return "provider_unavailable", None


# ── Sortie ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Ce que le routeur a décidé, et pourquoi.

    ``outcome`` : ``route`` (un modèle), ``session`` (aucune route autonome : la session
    décide), ``defer`` (palier retenu mais aucun modèle disponible) ou ``refuse`` (politique
    invalide : aucune route autonome). ``ranking`` liste tous les candidats disponibles du
    palier, dans l'ordre de préférence.
    """

    outcome: str
    tier: str | None = None
    provider: str | None = None
    model: str | None = None
    cost: Cost = field(default_factory=Cost.none)
    rule_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    excluded: tuple[dict[str, Any], ...] = ()
    ranking: tuple[tuple[str, str], ...] = ()
    defer_until: str | None = None
    inputs_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "cost": self.cost.to_dict(),
            "rule_ids": list(self.rule_ids),
            "reasons": list(self.reasons),
            "excluded": [dict(e) for e in self.excluded],
            "ranking": [list(r) for r in self.ranking],
            "defer_until": self.defer_until,
            "inputs_digest": self.inputs_digest,
        }


def inputs_digest(task: RoutingTask, history: RouteHistory, provider_state: ProviderView, policy: RoutingPolicy) -> str:
    """L'empreinte canonique des entrées : ce que ``route explain`` rejoue."""
    return hashlib.sha256(canonical_inputs(task, history, provider_state, policy).encode("utf-8")).hexdigest()


def canonical_inputs(task: RoutingTask, history: RouteHistory, provider_state: ProviderView, policy: RoutingPolicy) -> str:
    return json.dumps(inputs_dict(task, history, provider_state, policy), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def inputs_dict(task: RoutingTask, history: RouteHistory, provider_state: ProviderView, policy: RoutingPolicy) -> dict[str, Any]:
    policy_dict = policy.to_dict()
    policy_dict["rules"] = sorted(policy_dict["rules"], key=lambda r: r["id"])  # l'ordre des règles est sans effet
    return {
        "task": task.to_dict(),
        "history": history.for_couple(task.task_type, task.verifiability).to_dict(),
        "providers": provider_state.to_dict(),
        "policy": policy_dict,
    }


def model_sort_key(
    model: str, task: RoutingTask, history: RouteHistory, unit_cost: float | None, declared: tuple[int, int]
) -> tuple[Any, ...]:
    """La clé de départage dans un palier : réussite (>= 5 obs.), coût unitaire connu, ordre déclaré.

    Les modèles mesurés passent avant les non mesurés ; entre modèles de réussite égale
    seulement, le coût unitaire connu croissant (un coût inconnu passe après) ; puis l'ordre
    déclaré. Aucune entrée de budget : le coût est un prix catalogue.
    """
    row = history.row(task.task_type, task.verifiability, model)
    rate = row.rate if row is not None else None
    return (
        0 if rate is not None else 1,
        -(rate if rate is not None else 0.0),
        0 if unit_cost is not None else 1,
        unit_cost if unit_cost is not None else 0.0,
        declared,
    )


def route(
    task: RoutingTask,
    history: RouteHistory,
    provider_state: ProviderView,
    *,
    policy: RoutingPolicy = DEFAULT_POLICY,
) -> RouteDecision:
    """La décision de routage. Pure : mêmes entrées, même décision, quel que soit l'ordre des règles."""
    digest = inputs_digest(task, history, provider_state, policy)
    matched = sorted((r for r in policy.rules if rule_matches(r, task)), key=lambda r: r.id)
    rule_ids = tuple(r.id for r in matched)

    # V2 n'a aucune route autonome, quelle que soit la politique ; `route: session` absorbe tout.
    if task.verifiability not in FLOOR_BY_CLASS:
        return RouteDecision("session", rule_ids=rule_ids, inputs_digest=digest,
                             reasons=(f"classe {task.verifiability} : aucun verdict ne peut juger ce travail, la session décide",))
    if any(r.route == SESSION for r in matched):
        why = tuple(f"règle {r.id} : route vers la session" for r in matched if r.route == SESSION)
        return RouteDecision("session", rule_ids=rule_ids, reasons=why, inputs_digest=digest)
    if not matched:
        return RouteDecision("session", reasons=("aucune règle ne correspond : session (défaut de la politique)",), inputs_digest=digest)

    # Palier : maximum du plancher de classe, des min_tier des règles et de l'historique.
    index = SUPPORTED_MODEL_TIERS.index
    tier = FLOOR_BY_CLASS[task.verifiability]
    reasons = [f"plancher de la classe {task.verifiability} : {tier}"]
    for rule in matched:
        if rule.min_tier is not None:
            reasons.append(f"règle {rule.id} : palier minimal {rule.min_tier}")
            if index(rule.min_tier) > index(tier):
                tier = rule.min_tier
    recommended = history.start_tiers.get((task.task_type, task.verifiability))
    if recommended is not None and index(recommended[0]) > index(tier):
        tier = recommended[0]
        reasons.append(f"historique : {recommended[1]}")
    reasons.append(f"palier retenu : {tier}")

    currencies = {r.currency for r in matched if r.currency is not None}
    excluded: list[dict[str, Any]] = []
    pool: list[tuple[int, ModelOption]] = []
    for position, option in enumerate(provider_state.models):
        if option.tier != tier:
            continue
        if currencies and (len(currencies) > 1 or option.currency not in currencies):
            excluded.append({"provider": option.provider, "model": option.model, "reason": f"monnaie requise : {sorted(currencies)}"})
        elif not option.available:
            excluded.append({"provider": option.provider, "model": option.model, "reason": option.reason, "until": option.until})
        else:
            pool.append((position, option))

    if not pool:
        untils = sorted(e["until"] for e in excluded if e.get("until"))
        first = next((o for o in provider_state.models if o.tier == tier), None)
        reasons.append(f"aucun modèle disponible au palier {tier} : attente, jamais de descente de palier")
        return RouteDecision(
            "defer", tier=tier, provider=first.provider if first else None, rule_ids=rule_ids, reasons=tuple(reasons),
            excluded=tuple(excluded), defer_until=untils[0] if untils else None, inputs_digest=digest,
        )

    ranked = [
        option
        for _, option in sorted(
            pool, key=lambda item: model_sort_key(item[1].model, task, history, item[1].unit_cost, (item[1].order, item[0]))
        )
    ]
    best = ranked[0]
    row = history.row(task.task_type, task.verifiability, best.model)
    if row is not None and row.rate is not None:
        reasons.append(f"modèle {best.model} : réussite au premier essai {row.rate:.0%} sur {row.observations} observations")
    else:
        reasons.append(f"modèle {best.model} : réussite non mesurée (moins de {MIN_OBSERVATIONS} observations), ordre déclaré")
    return RouteDecision(
        "route", tier=tier, provider=best.provider, model=best.model,
        cost=row.mean_cost if row is not None else Cost.unpriced(), rule_ids=rule_ids,
        reasons=tuple(reasons), excluded=tuple(excluded), ranking=tuple((o.provider, o.model) for o in ranked),
        inputs_digest=digest,
    )
