"""Historique des dispatchs par couple (type de tâche, classe) — issue #312, lot 4.

Le lot 3 (#311/#323) laisse un événement ``task.dispatched`` au Mission
Ledger pour chaque tentative de la cascade — le palier essayé, le
fournisseur, le verdict. Ce module ne fait rien d'autre que les compter :
pour chaque couple (type de tâche, classe de vérifiabilité), combien de
dispatchs, et sur combien d'entre eux le palier de départ n'a pas suffi
(la cascade a dû monter d'un palier). **Pas de classifieur, pas
d'entraînement** — l'issue le pose en critère d'arrêt, et le vocabulaire du
module s'y tient : des compteurs, une règle de seuil documentée en
constantes, rien qui ressemble à un modèle appris.

La règle qui en sort — :func:`recommend_start_tier` — ajuste le palier de
départ du *prochain* dispatch d'un couple, sans jamais franchir le plancher
que la classe de vérifiabilité impose (``cheap`` pour V0, ``mid`` pour V1,
jamais plus bas — une tâche V1 reste jugée par un être humain, pas par
l'historique). Trois mouvements, dans cet ordre de priorité :

1. Si le couple compte au moins :data:`MIN_OBSERVATIONS` dispatchs partis de
   ``cheap`` et que plus de 40 % ont dû escalader, le prochain part de
   ``mid``.
2. Sinon, si au moins :data:`MIN_OBSERVATIONS` dispatchs sont partis de
   ``mid`` et que plus de 40 % ont dû escalader, le prochain part de
   ``strong``.
3. Sinon, si le couple a été poussé à ``mid`` mais que ses
   :data:`MIN_OBSERVATIONS` dernières observations à ce palier n'ont jamais
   escaladé et que ``cheap`` n'a pas été re-tenté depuis autant
   d'observations, on redescend d'un palier pour re-sonder — seulement pour
   un couple dont le plancher de classe autorise ``cheap`` (jamais pour V1).

Sans assez d'historique, ou hors de ces trois cas, le palier de départ reste
celui que la classe impose. ``run_dispatch`` applique cette règle sauf
``--start-tier`` explicite ; ``grimoire providers history`` l'affiche.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grimoire.providers.registry import SUPPORTED_MODEL_TIERS

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger

__all__ = [
    "ESCALATION_THRESHOLD",
    "MIN_OBSERVATIONS",
    "CoupleHistory",
    "TierObservations",
    "compute_dispatch_history",
    "recommend_start_tier",
]

#: Nombre minimal de dispatchs — pour un couple entier, ou partis d'un palier
#: donné — avant qu'un taux d'escalade ne veuille dire quoi que ce soit.
#: En dessous, deux ou trois dispatchs malchanceux suffiraient à faire
#: monter un couple pour de mauvaises raisons.
MIN_OBSERVATIONS = 5

#: Au-delà de ce taux d'escalade, le palier de départ n'a manifestement plus
#: suffi assez souvent pour rester le premier essayé.
ESCALATION_THRESHOLD = 0.4

#: Plancher de palier par classe de vérifiabilité — même table que
#: ``dispatch._START_TIER``, dupliquée ici plutôt qu'importée : ``dispatch``
#: importe ce module pour la recommandation, l'importer en retour créerait un
#: cycle. V2 n'apparaît jamais dans l'historique — la cascade ne l'atteint
#: jamais, aucun événement ``task.dispatched`` n'est jamais écrit pour elle.
_FLOOR_BY_VERIFIABILITY: dict[str, str] = {"V0": "cheap", "V1": "mid"}


@dataclass(frozen=True, slots=True)
class TierObservations:
    """Ce qu'on sait des dispatchs d'un couple partis d'UN palier donné."""

    observations: int
    escalations: int

    @property
    def rate(self) -> float | None:
        """Le taux d'escalade — ``None`` si aucune observation (division par zéro évitée)."""
        if self.observations == 0:
            return None
        return self.escalations / self.observations

    def to_dict(self) -> dict[str, Any]:
        return {"observations": self.observations, "escalations": self.escalations, "escalation_rate": self.rate}


@dataclass(frozen=True, slots=True)
class CoupleHistory:
    """L'historique complet d'un couple (type de tâche, classe) — une ligne de ``providers history``."""

    task_type: str
    verifiability: str
    observations: int
    by_start_tier: dict[str, TierObservations] = field(default_factory=dict)
    recommended_start_tier: str = "cheap"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "verifiability": self.verifiability,
            "observations": self.observations,
            "by_start_tier": {tier: stats.to_dict() for tier, stats in self.by_start_tier.items()},
            "recommended_start_tier": self.recommended_start_tier,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _Run:
    """Un dispatch complet d'une tâche : la cascade d'un ``run_dispatch`` du début à sa fin.

    Reconstruit depuis les événements ``task.dispatched`` — un par tentative
    — plutôt que porté par un identifiant de run explicite : aucun n'existe
    au ledger, et ``attempt == 1`` marque sans ambiguïté le début d'une
    nouvelle cascade pour une même tâche.
    """

    task_type: str
    verifiability: str
    start_tier: str
    escalated: bool


def _dispatch_runs(ledger: MissionLedger) -> tuple[_Run, ...]:
    """Reconstruit les cascades depuis les événements bruts, dans leur ordre chronologique.

    Un événement sans ``task_type``/``verifiability`` (écrit par une version
    du dispatch antérieure au lot 4) est ignoré plutôt que de faire échouer
    tout le calcul — un historique partiel reste plus utile qu'aucun.
    """
    raw_runs: list[dict[str, Any]] = []
    open_by_task: dict[str, dict[str, Any]] = {}
    for event in ledger.list_events(None):
        if event.event_type != "task.dispatched":
            continue
        payload = event.payload
        task_id = payload.get("task_id")
        if task_id is None:
            continue
        current = open_by_task.get(task_id)
        if current is None or payload.get("attempt") == 1:
            current = {"payloads": []}
            raw_runs.append(current)
            open_by_task[task_id] = current
        current["payloads"].append(payload)

    runs: list[_Run] = []
    for raw in raw_runs:
        payloads = raw["payloads"]
        first = payloads[0]
        task_type = first.get("task_type")
        verifiability = first.get("verifiability")
        start_tier = first.get("start_tier") or first.get("tier")
        if not task_type or not verifiability or not start_tier:
            continue
        escalated = any(p.get("tier") != start_tier for p in payloads)
        runs.append(_Run(task_type=task_type, verifiability=verifiability, start_tier=start_tier, escalated=escalated))
    return tuple(runs)


def _recommend_from_runs(runs: tuple[_Run, ...], floor: str) -> tuple[str, str]:
    """Le cœur de la règle — voir le docstring du module pour l'ordre de priorité."""
    total = len(runs)
    if total < MIN_OBSERVATIONS:
        return floor, f"moins de {MIN_OBSERVATIONS} observations ({total}) : palier plancher de la classe"

    by_tier: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        by_tier[run.start_tier].append(run)

    cheap_runs = by_tier.get("cheap", [])
    if len(cheap_runs) >= MIN_OBSERVATIONS:
        rate = sum(r.escalated for r in cheap_runs) / len(cheap_runs)
        if rate > ESCALATION_THRESHOLD:
            return "mid", (
                f"escalade depuis cheap à {rate:.0%} sur {len(cheap_runs)} observations "
                f"(seuil {ESCALATION_THRESHOLD:.0%}) : départ mid"
            )

    mid_runs = by_tier.get("mid", [])
    if len(mid_runs) >= MIN_OBSERVATIONS:
        rate = sum(r.escalated for r in mid_runs) / len(mid_runs)
        if rate > ESCALATION_THRESHOLD:
            return "strong", (
                f"escalade depuis mid à {rate:.0%} sur {len(mid_runs)} observations "
                f"(seuil {ESCALATION_THRESHOLD:.0%}) : départ strong"
            )

    # Re-sonde : un couple qui part de `mid` (poussé là par la règle 1, ou
    # natif d'une classe V1 promue... non, V1 ne redescend jamais, voir le
    # garde `floor == "cheap"` ci-dessous) et qui ne bronche plus mérite
    # qu'on retente `cheap` — à condition de ne pas déjà être en train de le
    # faire depuis peu.
    if floor == "cheap" and len(mid_runs) >= MIN_OBSERVATIONS:
        recent_mid = mid_runs[-MIN_OBSERVATIONS:]
        if not any(r.escalated for r in recent_mid):
            recent_overall = runs[-MIN_OBSERVATIONS:]
            if not any(r.start_tier == "cheap" for r in recent_overall):
                return "cheap", (
                    f"palier mid stable (0 escalade sur les {MIN_OBSERVATIONS} dernières observations à ce "
                    f"palier) et cheap non re-sondé depuis {MIN_OBSERVATIONS} observations : re-sonde depuis cheap"
                )

    return floor, "taux d'escalade sous le seuil (ou historique insuffisant par palier) : palier plancher conservé"


def recommend_start_tier(ledger: MissionLedger, *, task_type: str, verifiability: str, floor: str) -> tuple[str, str]:
    """Le palier de départ recommandé pour ce couple, et pourquoi (``start_tier_reason``).

    *floor* est le plancher que la classe de vérifiabilité impose — fourni
    par l'appelant (``dispatch.start_tier_for``) plutôt que recalculé ici,
    pour ne dupliquer cette règle nulle part ailleurs qu'au sommet de ce
    module (voir ``_FLOOR_BY_VERIFIABILITY``).
    """
    runs = tuple(r for r in _dispatch_runs(ledger) if r.task_type == task_type and r.verifiability == verifiability)
    return _recommend_from_runs(runs, floor)


def compute_dispatch_history(ledger: MissionLedger) -> tuple[CoupleHistory, ...]:
    """Une ligne par couple (type de tâche, classe) observé dans le ledger — ``grimoire providers history``."""
    by_couple: dict[tuple[str, str], list[_Run]] = defaultdict(list)
    for run in _dispatch_runs(ledger):
        by_couple[(run.task_type, run.verifiability)].append(run)

    histories: list[CoupleHistory] = []
    for (task_type, verifiability), couple_runs in by_couple.items():
        floor = _FLOOR_BY_VERIFIABILITY.get(verifiability, "cheap")
        by_start_tier: dict[str, TierObservations] = {}
        for tier in SUPPORTED_MODEL_TIERS:
            tier_runs = [r for r in couple_runs if r.start_tier == tier]
            if tier_runs:
                by_start_tier[tier] = TierObservations(
                    observations=len(tier_runs), escalations=sum(r.escalated for r in tier_runs)
                )
        recommended, reason = _recommend_from_runs(tuple(couple_runs), floor)
        histories.append(
            CoupleHistory(
                task_type=task_type,
                verifiability=verifiability,
                observations=len(couple_runs),
                by_start_tier=by_start_tier,
                recommended_start_tier=recommended,
                reason=reason,
            )
        )
    return tuple(sorted(histories, key=lambda h: (h.task_type, h.verifiability)))
