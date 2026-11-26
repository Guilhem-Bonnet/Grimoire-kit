"""Le choix de modèle d'un worker lancé par le tick, et la trace qui le dit.

Avant ce module, un projet sans réglage de modèle lançait ses workers sur le modèle par défaut de l'hôte, et le ledger
ne disait pas pourquoi un modèle avait été pris. Trois règles, une seule source :

**Défauts (aucun réglage).** Quand la politique ne pose ni ``model``, ni ``tier``, ni ``provider``, ni
``model_by_class`` (:attr:`~grimoire.missions.orchestrator_policy.WorkerRole.default_routing`), le palier suit la
classe de vérifiabilité : V0 → ``cheap`` (un verdict mécanique juge seul), V1 → ``mid``, V2 → ``strong`` (rien ne
juge le travail d'un modèle faible). Une tâche qui cite un chemin sensible part au palier fort (``sensitive_route``,
défaut ``strong`` sous les mêmes conditions). Un seul réglage explicite suffit à écarter ces défauts : le routage
d'un projet qui a déjà ses réglages ne change pas.

**Trace.** Chaque lancement note, dans ``task.session_launched``, un bloc ``routing`` (:class:`RoutingTrace`) : classe,
palier, modèle et raison. ``grimoire task show`` l'affiche.

**Montée en gamme.** Après un refus de relecture, le worker repart au palier au-dessus (:func:`step_up`), en session
neuve sur le même worktree (``claude --resume`` ne reçoit pas de modèle) : une marche par refus, plafonnée au palier
fort, tracée dans ``task.worker_session_resumed`` et ``task.session_launched``. Jamais de montée quand le
modèle est imposé (``worker.model``), quand la session passe par une route de fournisseur, ou quand le palier courant
n'est pas connu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.missions.verifiability import Verifiability

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from grimoire.missions.orchestrator_policy import WorkerRole

__all__ = [
    "REASON_DEFAULT_CLASS",
    "REASON_HOST_DEFAULT",
    "REASON_HOST_MODEL",
    "REASON_POLICY_CLASS",
    "REASON_POLICY_MODEL",
    "REASON_POLICY_TIER",
    "REASON_PROVIDER_ROUTE",
    "REASON_ROUTER",
    "REASON_SENSITIVE_PATH",
    "REASON_STEP_UP",
    "RoutingTrace",
    "StepUp",
    "class_tier",
    "describe_routing",
    "step_up",
    "tier_for",
]

#: Aucun réglage : le palier de la classe (V0 cheap, V1 mid, V2 strong).
REASON_DEFAULT_CLASS = "default_class"
#: La politique règle ``model_by_class`` : V0 au palier cheap, le reste au palier du rôle.
REASON_POLICY_CLASS = "policy_class"
#: La politique règle ``tier``.
REASON_POLICY_TIER = "policy_tier"
#: La politique impose ``model``.
REASON_POLICY_MODEL = "policy_model"
#: La tâche cite un chemin sensible : palier fort.
REASON_SENSITIVE_PATH = "sensitive_path"
#: Un modèle de la politique de routage (``routing-policy``) l'emporte sur le palier.
REASON_ROUTER = "router"
#: La session passe par un fournisseur du registre.
REASON_PROVIDER_ROUTE = "provider_route"
#: Ni palier ni modèle : l'hôte prend son modèle par défaut.
REASON_HOST_DEFAULT = "host_default"
#: Hôte qui choisit son propre modèle (opencode, gemini).
REASON_HOST_MODEL = "host_model"
#: Reprise après un refus de relecture : un palier plus haut.
REASON_STEP_UP = "step_up"

_CLASS_TIERS = {Verifiability.V0: "cheap", Verifiability.V1: "mid", Verifiability.V2: "strong"}
_LADDER = ("cheap", "mid", "strong")

_REASON_TEXT = {
    REASON_DEFAULT_CLASS: "défaut du kit pour cette classe",
    REASON_POLICY_CLASS: "politique : model_by_class",
    REASON_POLICY_TIER: "politique : tier",
    REASON_POLICY_MODEL: "politique : model",
    REASON_SENSITIVE_PATH: "chemin sensible",
    REASON_ROUTER: "politique de routage",
    REASON_PROVIDER_ROUTE: "route de fournisseur",
    REASON_HOST_DEFAULT: "modèle par défaut de l'hôte",
    REASON_HOST_MODEL: "modèle choisi par l'hôte",
    REASON_STEP_UP: "montée en gamme après un refus de relecture",
}


@dataclass(frozen=True, slots=True)
class RoutingTrace:
    """Ce que le lancement a décidé : la classe, le palier, le modèle et pourquoi (``detail`` : le complément)."""

    klass: str
    tier: str
    model: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.klass, "tier": self.tier, "model": self.model, "reason": self.reason,
            **({"detail": self.detail} if self.detail else {}),
        }


def class_tier(klass: Verifiability) -> str:
    """Le palier par défaut de *klass* : V0 ``cheap``, V1 ``mid``, V2 ``strong``."""
    return _CLASS_TIERS[klass]


def tier_for(role: WorkerRole, klass: Verifiability, *, sensitive: bool = False) -> tuple[str, str]:
    """``(palier, raison)`` du worker de classe *klass* sous *role* ; palier vide : l'hôte prend son défaut.

    Chemin sensible d'abord (palier fort), puis ``model_by_class`` (V0), puis les défauts du kit
    (:attr:`~grimoire.missions.orchestrator_policy.WorkerRole.default_routing`), enfin ``tier``. Un ``model`` imposé
    ne change pas le palier, seulement la raison.
    """
    if sensitive:
        return "strong", REASON_SENSITIVE_PATH
    if role.model_by_class and klass is Verifiability.V0:
        tier, reason = "cheap", REASON_POLICY_CLASS
    elif role.default_routing:
        tier, reason = class_tier(klass), REASON_DEFAULT_CLASS
    else:
        tier, reason = role.tier, REASON_POLICY_TIER if role.tier else REASON_HOST_DEFAULT
    return tier, REASON_POLICY_MODEL if role.model else reason


@dataclass(frozen=True, slots=True)
class StepUp:
    """Une montée en gamme décidée : le modèle et le palier de la reprise, la trace à noter, et d'où l'on part."""

    model: str
    tier: str
    previous_tier: str
    trace: RoutingTrace


def _tier_of(launch: Mapping[str, Any], resolve: Callable[[str], str]) -> str:
    """Le palier du lancement : celui que sa trace nomme, à défaut celui dont le modèle est le modèle noté."""
    routing = launch.get("routing")
    if isinstance(routing, dict) and routing.get("tier") in _LADDER:
        return str(routing["tier"])
    model = str(launch.get("model") or "")
    return next((tier for tier in _LADDER if model and resolve(tier) == model), "")


def step_up(launch: Mapping[str, Any], resolve: Callable[[str], str]) -> StepUp | None:
    """La reprise après un refus de relecture, un palier plus haut ; ``None`` quand rien ne monte.

    *launch* : la charge du dernier lancement ; *resolve* : palier → modèle de l'hôte. Une marche par appel, au plus
    jusqu'à ``strong``. Rien ne monte si le modèle est imposé par la politique, si la session passe par un fournisseur
    (son modèle n'est pas celui de l'hôte), si le palier n'est pas connu, ou s'il est déjà fort.
    """
    routing = launch.get("routing")
    if launch.get("provider") or (isinstance(routing, dict) and routing.get("reason") == REASON_POLICY_MODEL):
        return None
    current = _tier_of(launch, resolve)
    if not current or current == _LADDER[-1]:
        return None
    tier = _LADDER[_LADDER.index(current) + 1]
    model = resolve(tier)
    if not model:
        return None
    klass = str(routing.get("class") or "") if isinstance(routing, dict) else ""
    trace = RoutingTrace(klass, tier, model, REASON_STEP_UP, f"{current} → {tier}")
    return StepUp(model, tier, current, trace)


def describe_routing(routing: Mapping[str, Any]) -> str:
    """La ligne que ``grimoire task show`` affiche : « sonnet (palier mid, classe V1) — défaut du kit… »."""
    model = str(routing.get("model") or "") or "modèle par défaut de l'hôte"
    facts = ", ".join(
        f"{label} {routing[key]}" for key, label in (("tier", "palier"), ("class", "classe")) if routing.get(key)
    )
    reason = _REASON_TEXT.get(str(routing.get("reason") or ""), str(routing.get("reason") or ""))
    detail = f" ({routing['detail']})" if routing.get("detail") else ""
    return f"{model}{f' ({facts})' if facts else ''} — {reason}{detail}"
