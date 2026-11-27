"""Quel modèle pour quel usage, maintenant ? La décision de routage par geste (épique #1477, lot B1).

Fonction PURE : aucune lecture, aucune écriture, aucun appel de modèle. Elle reçoit un :class:`DirectState` (ce qui est
détecté, ce que l'humain a autorisé, ce qui est consommé, la règle d'usage qui retient peut-être les gestes, ce que la
politique de données refuse) et rend une :class:`Decision` lisible : qui, pourquoi, ce qui a été écarté.
:mod:`grimoire.providers.direct_use_settings` assemble l'état ; ce module-ci ne connaît que des valeurs.

La table de décision (ordre d'évaluation, premier motif qui s'applique) :

1. Usage inconnu : rien.
2. Aucun modèle autorisé pour l'usage : rien (« Grimoire fonctionne sans modèle »).
3. Pour chaque modèle autorisé, dans l'ordre de préférence de l'humain, les motifs d'écart sont, dans cet ordre :
   inconnu de la détection (ignoré) ; indisponible (raison de la détection) ; règle d'usage ``hold``/``pause``
   (seulement pour un modèle qui n'est pas ``gratuit-local`` : un modèle local ne consomme aucun quota) ; plafond du
   jour atteint (borne basse) ; politique de données ; puis, à part, capacité minimale non satisfaite.
4. **Choix manuel** (*manual*) : prioritaire sur l'ordre et sur la capacité. Il ne passe jamais sur une garde de
   sûreté (non autorisé, indisponible, règle d'usage, plafond, données). Un choix manuel trop léger est utilisé avec un
   avertissement ; un choix manuel écarté par une garde est traité comme un modèle courant qui tombe (point 6).
5. **Sans modèle courant** : le premier modèle autorisé qui convient est retenu.
6. **Modèle courant** (*current*, ou le choix manuel) :
   - il convient : on le garde (aucun changement sans raison) ;
   - il tombe (indisponible, plafond, règle, données, plus autorisé) : **repli automatique** vers le premier modèle
     autorisé qui convient ET qui n'est NI plus coûteux (gratuit-local < abonnement < payant) NI plus exposé
     (rien < texte) ; à défaut, une **proposition** du premier modèle qui convient, avec ce qui l'empêche de basculer seul ;
     à défaut, rien ;
   - il est trop léger pour le geste (capacité minimale non satisfaite) : jamais de bascule seule : une
     **proposition** (« trop léger pour brainstormer ») vers le premier modèle autorisé assez capable, sinon rien.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from grimoire.providers.availability import ModelAvailability

__all__ = [
    "COST_RANK",
    "DEFAULT_MIN",
    "EGRESS_RANK",
    "USAGES",
    "USAGE_LABELS",
    "Authorization",
    "Decision",
    "DirectState",
    "MinCapability",
    "TraceLine",
    "choose_for",
]

USAGES: tuple[str, ...] = ("conversation", "brainstorm", "redaction", "autres")
USAGE_LABELS: dict[str, str] = {
    "conversation": "Conversation (cadrage)",
    "brainstorm": "Brainstorm",
    "redaction": "Rédaction",
    "autres": "Autres usages",
}
_VERB: dict[str, str] = {
    "conversation": "converser",
    "brainstorm": "brainstormer",
    "redaction": "rédiger",
    "autres": "ce geste",
}
#: Du moins cher au plus cher ; du moins exposé au plus exposé. Une valeur inconnue compte comme la pire.
COST_RANK: dict[str, int] = {"gratuit-local": 0, "abonnement": 1, "payant": 2}
EGRESS_RANK: dict[str, int] = {"rien": 0, "texte": 1}
_TIER_RANK: dict[str, int] = {"cheap": 1, "mid": 2, "strong": 3}
_TIER_LABEL: dict[str, str] = {"cheap": "léger", "mid": "intermédiaire", "strong": "fort"}


@dataclass(frozen=True, slots=True)
class MinCapability:
    """Ce qu'un geste exige d'un modèle : un palier au moins, une fenêtre de contexte, des capacités établies."""

    tier: str = ""
    context_window: int = 0
    needs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier or None, "context_window": self.context_window or None, "needs": list(self.needs)}


#: Par défaut, converser et les autres usages n'exigent rien ; brainstormer et rédiger demandent au moins un modèle
#: intermédiaire. Un modèle dont le palier est inconnu ne le prouve pas : il ne convient alors pas à ces deux gestes.
DEFAULT_MIN: dict[str, MinCapability] = {
    "conversation": MinCapability(),
    "brainstorm": MinCapability(tier="mid"),
    "redaction": MinCapability(tier="mid"),
    "autres": MinCapability(),
}


@dataclass(frozen=True, slots=True)
class Authorization:
    """Un modèle autorisé pour un usage, avec son plafond de jetons par jour (``None`` : aucun plafond)."""

    key: str
    daily_cap_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class DirectState:
    """Tout ce que la décision lit, déjà rassemblé."""

    models: Mapping[str, ModelAvailability]
    allowed: Mapping[str, tuple[Authorization, ...]]
    used_today: Mapping[str, int]
    hold_reason: str = ""
    data_refusals: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class TraceLine:
    """Un modèle examiné : retenu, écarté, proposé ou ignoré, et pourquoi."""

    key: str
    label: str
    verdict: str
    why: str

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "verdict": self.verdict, "why": self.why}


@dataclass(frozen=True, slots=True)
class Decision:
    """``use`` : utiliser ce modèle ; ``propose`` : proposer, sans basculer ; ``none`` : aucun modèle (mode sans modèle)."""

    kind: str
    usage: str
    key: str
    label: str
    reason: str
    warning: str
    trace: tuple[TraceLine, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "usage": self.usage,
            "key": self.key or None,
            "label": self.label or None,
            "reason": self.reason,
            "warning": self.warning or None,
            "trace": [line.to_dict() for line in self.trace],
        }


def _label(state: DirectState, key: str) -> str:
    model = state.models.get(key)
    return model.label if model is not None else key


def _block(auth: Authorization, model: ModelAvailability, state: DirectState) -> str:
    """Le motif de sûreté qui écarte ce modèle maintenant, vide s'il peut servir."""
    if not model.available:
        return f"indisponible ({model.reason or 'raison inconnue'}) : {model.detail}".rstrip(" :")
    if state.hold_reason and model.cost != "gratuit-local":
        return f"règle d'usage : {state.hold_reason}"
    used = state.used_today.get(auth.key, 0)
    if auth.daily_cap_tokens is not None and used >= auth.daily_cap_tokens:
        return f"plafond du jour atteint ({used}/{auth.daily_cap_tokens} jetons, borne basse)"
    refusal = (state.data_refusals or {}).get(auth.key, "")
    return f"politique de données : {refusal}" if refusal else ""


def _gap(model: ModelAvailability, need: MinCapability, usage: str) -> str:
    """Ce qui manque au modèle pour ce geste, vide s'il convient."""
    verb = _VERB[usage]
    if need.tier:
        rank = _TIER_RANK.get(model.tier, 0)
        if rank == 0:
            return f"palier inconnu : rien ne prouve qu'il convient pour {verb}"
        if rank < _TIER_RANK[need.tier]:
            return f"trop léger pour {verb} (palier {_TIER_LABEL[model.tier]}, il faut au moins {_TIER_LABEL[need.tier]})"
    if need.context_window:
        window = model.capability("context_window")
        if window is None:
            return f"fenêtre de contexte inconnue : rien ne prouve qu'elle suffit pour {verb}"
        if int(window[0]) < need.context_window:
            return f"fenêtre de contexte trop courte pour {verb} ({window[0]} < {need.context_window})"
    for name in need.needs:
        cap = model.capability(name)
        if cap is None or not cap[0]:
            return f"capacité « {name} » non établie, nécessaire pour {verb}"
    return ""


def _worse(candidate: ModelAvailability, current: ModelAvailability) -> str:
    """Pourquoi *candidate* ne peut pas remplacer *current* seul (plus coûteux, plus exposé) ; vide sinon."""
    why: list[str] = []
    if COST_RANK.get(candidate.cost, 99) > COST_RANK.get(current.cost, 99):
        why.append(f"plus coûteux ({candidate.cost} au lieu de {current.cost})")
    if EGRESS_RANK.get(candidate.egress, 99) > EGRESS_RANK.get(current.egress, 99):
        why.append(f"plus exposé ({candidate.egress} sort au lieu de {current.egress})")
    return " et ".join(why)


def choose_for(
    usage: str,
    state: DirectState,
    *,
    min_capability: MinCapability | None = None,
    current: str | None = None,
    manual: str | None = None,
) -> Decision:
    """Le modèle à utiliser maintenant pour *usage* parmi les autorisés (voir le docstring du module pour la table)."""
    if usage not in USAGES:
        return Decision("none", usage, "", "", f"usage inconnu : {usage!r}", "", ())
    need = min_capability if min_capability is not None else DEFAULT_MIN[usage]
    order: list[Authorization] = []
    for auth in state.allowed.get(usage, ()):
        if all(auth.key != seen.key for seen in order):
            order.append(auth)
    if not order:
        return Decision(
            "none", usage, "", "", "Aucun modèle autorisé pour cet usage : Grimoire fonctionne sans modèle.", "", ()
        )

    trace: list[TraceLine] = []
    eligible: list[str] = []
    blocks: dict[str, str] = {}
    gaps: dict[str, str] = {}
    by_key = {a.key: a for a in order}
    for auth in order:
        model = state.models.get(auth.key)
        if model is None:
            blocks[auth.key] = "inconnu de la détection : autorisation ignorée"
            trace.append(TraceLine(auth.key, auth.key, "ignoré", blocks[auth.key]))
            continue
        block = _block(auth, model, state)
        gap = _gap(model, need, usage)
        if block:
            blocks[auth.key] = block
            trace.append(TraceLine(auth.key, model.label, "écarté", block))
        elif gap:
            gaps[auth.key] = gap
            trace.append(TraceLine(auth.key, model.label, "écarté", gap))
        else:
            eligible.append(auth.key)

    def finish(kind: str, key: str, reason: str, warning: str = "") -> Decision:
        verdict = "retenu" if kind == "use" else "proposé"
        lines = [line for line in trace if line.key != key]
        lines.append(TraceLine(key, _label(state, key), verdict, reason))
        ordered = tuple(sorted(lines, key=lambda ln: next((i for i, a in enumerate(order) if a.key == ln.key), 99)))
        return Decision(kind, usage, key, _label(state, key), reason, warning, ordered)

    def none(reason: str) -> Decision:
        return Decision("none", usage, "", "", reason, "", tuple(trace))

    def why_none() -> str:
        parts = [f"{ln.label} : {ln.why}" for ln in trace]
        return "Aucun modèle autorisé n'est utilisable maintenant (" + " ; ".join(parts) + ")."

    manual_key = (manual or "").strip() or None
    if manual_key is not None:
        if manual_key not in by_key:
            line = TraceLine(manual_key, _label(state, manual_key), "écarté", "n'est pas autorisé pour cet usage")
            return Decision("none", usage, "", "", f"« {line.label} » n'est pas autorisé pour cet usage.", "", (*trace, line))
        if manual_key in eligible:
            return finish("use", manual_key, "choix manuel")
        if manual_key in gaps:  # trop léger mais choisi à la main : la personne passe avant la capacité, pas avant les gardes
            return finish("use", manual_key, "choix manuel", f"{_label(state, manual_key)} : {gaps[manual_key]}")
    current_key = manual_key or ((current or "").strip() or None)

    if current_key is None:
        if eligible:
            return finish("use", eligible[0], "premier modèle de ta liste qui convient maintenant")
        return none(why_none())
    if current_key in eligible:
        return finish("use", current_key, "le modèle courant convient toujours : aucun changement")

    name = _label(state, current_key)
    pool = [key for key in eligible if key != current_key]
    if current_key in gaps and current_key not in blocks:  # trop léger : jamais de bascule seule
        if pool:
            return finish("propose", pool[0], f"{name} : {gaps[current_key]}")
        return none(f"{name} : {gaps[current_key]} ; aucun autre modèle autorisé n'est assez capable.")

    cause = blocks.get(current_key) or ("n'est plus autorisé pour cet usage" if current_key not in by_key else "")
    cause = cause or "écarté"
    current_model = state.models.get(current_key)
    for key in pool:
        candidate = state.models[key]
        if current_model is not None and not _worse(candidate, current_model):
            return finish("use", key, f"repli automatique : {name} {cause}")
    if pool:
        first = state.models[pool[0]]
        extra = _worse(first, current_model) if current_model is not None else "coût et exposition à vérifier"
        return finish("propose", pool[0], f"{name} {cause} ; {first.label} est {extra} : à confirmer")
    return none(f"{name} {cause} ; aucun autre modèle autorisé ne peut le remplacer.")
