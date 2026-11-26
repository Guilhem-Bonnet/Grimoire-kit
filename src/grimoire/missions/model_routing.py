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
_DETAIL_LIMIT = 300
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
        detail = _clean(self.detail, _DETAIL_LIMIT)
        return {
            "class": self.klass, "tier": self.tier, "model": self.model, "reason": self.reason,
            **({"detail": detail} if detail else {}),
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


def step_up(role: WorkerRole, launch: Mapping[str, Any], resolve: Callable[[str], str]) -> StepUp | None:
    """La reprise après un refus de relecture, un palier au-dessus du modèle *réel* ; ``None`` quand rien ne monte.

    *role* : le rôle worker de la politique du moment, sans réglage de modèle (``default_routing``, ``model`` vide) :
    un projet qui a réglé ``tier``, ``model``, ``provider`` ou ``model_by_class`` ne monte jamais. *launch* : la
    charge du dernier lancement ; *resolve* : palier → modèle de l'hôte. Le palier courant se lit sur
    le modèle noté (``launch["model"]``), jamais sur la trace : un modèle que la table ne connaît pas, ou une session
    passée par un fournisseur, ne monte pas. Le plus haut palier dont le modèle est le modèle courant fait foi ; la
    reprise vise le premier palier au-dessus dont le modèle existe et diffère : jamais un modèle de rang inférieur ou
    égal, jamais plus d'une marche.     """
    model = str(launch.get("model") or "")
    if not role.default_routing or role.model or launch.get("provider") or not model:
        return None
    here = [i for i, tier in enumerate(_LADDER) if resolve(tier) == model]
    if not here:
        return None
    current = max(here)
    for index in range(current + 1, len(_LADDER)):
        tier, candidate = _LADDER[index], resolve(_LADDER[index])
        if candidate and candidate != model:
            routing = launch.get("routing")
            klass = str(routing.get("class") or "") if isinstance(routing, dict) else ""
            return StepUp(candidate, tier, _LADDER[current], RoutingTrace(klass, tier, candidate, REASON_STEP_UP, f"{_LADDER[current]} → {tier}"))
    return None


def _clean(text: object, limit: int) -> str:
    """*text* sans caractère de contrôle, borné à *limit* caractères."""
    flat = "".join(c if c.isprintable() else " " for c in str(text))
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def describe_routing(routing: Mapping[str, Any]) -> str:
    """La ligne que ``grimoire task show`` affiche : « sonnet (palier mid, classe V1) — défaut du kit… »."""
    model = _clean(routing.get("model") or "", 80) or "modèle par défaut de l'hôte"
    facts = ", ".join(
        f"{label} {_clean(routing[key], 20)}" for key, label in (("tier", "palier"), ("class", "classe")) if routing.get(key)
    )
    raw = str(routing.get("reason") or "")
    reason = _REASON_TEXT.get(raw) or _clean(raw, 60)
    detail = f" ({_clean(routing['detail'], _DETAIL_LIMIT)})" if routing.get("detail") else ""
    return f"{model}{f' ({facts})' if facts else ''} — {reason}{detail}"
