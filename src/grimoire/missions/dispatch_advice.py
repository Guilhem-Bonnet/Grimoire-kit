"""Le conseil de dispatch d'une tâche — vérifiabilité et modèle recommandé (#654).

Le mode interactif (Claude Code, Copilot — l'hôte tient la session, le défaut)
n'appelle jamais ``grimoire task dispatch`` ni ``grimoire flow run --executor
dispatch`` : c'est l'hôte lui-même qui exécute, et qui décide seul, en prose,
quel modèle donner à un sous-agent. La correspondance classe → modèle
n'existait jusqu'ici que dans le texte que ``hosts.emitters.claude_code``
injecte dans la persona d'entrée (``_dispatch_policy_section``) — un LLM devait
la lire et l'appliquer lui-même, sans qu'aucun outil ne la calcule pour lui.

Ce module ferme cet écart côté outil : une fonction unique qui rend, pour une
tâche, sa classe de vérifiabilité et le modèle que cette classe recommande —
calculées par :func:`grimoire.missions.verifiability.classify`, la même
fonction que celle que ``grimoire task dispatch`` (``missions.dispatch``)
consulte pour poser le plancher de sa cascade de paliers. Jamais de seconde
classification : un texte de critère n'a qu'une lecture possible dans tout le
kit.

``RECOMMENDED_MODEL_BY_CLASS`` est la table opposable — V0 → ``haiku`` (un
verdict mécanique n'exige pas de jugement), V1 → ``sonnet`` (une revue humaine
suit, un modèle plus capable dès le premier essai limite les allers-retours),
V2 → ``session`` (aucun verdict n'existe encore : seul le modèle qui tient
déjà la session, capable de clarifier avant de déléguer, a le droit d'y
toucher). Elle reprend exactement la prose de
``hosts.emitters.claude_code._dispatch_policy_section`` — ce module ne la
recopie pas — et :func:`~grimoire.hosts.emitters.claude_code._model_label` la
lit toujours telle quelle, ``"session"`` compris : c'est le contrat *interne*,
qui vaut pour composer une phrase à l'attention d'un humain ou d'une persona.

Le contrat *externe* — ce que ``dispatch_advice()`` rend, et que MCP/CLI
sérialisent tel quel — diverge sur ce seul point. ``"session"`` n'est pas une
valeur que l'outil ``Agent`` de Claude Code accepte pour son paramètre
``model`` : ce n'est le nom d'aucun modèle, c'est une consigne (« garde celui
de la session en cours »). Le rendre en ``recommended_model`` invitait
l'appelant à le passer tel quel à ``model=`` — un agent qui suit la donnée
plutôt que la prose (la politique dit justement de préférer la donnée, voir
``_dispatch_policy_section``) passerait une valeur qu'aucun host n'accepte.
Le contrat externe distingue donc :

- ``recommended_model`` : un nom de modèle valide pour ``model=`` (``haiku``,
  ``sonnet``), ou ``None`` quand aucun n'existe (V2) — ``None`` se lit sans
  ambiguïté comme « omets le paramètre ``model`` », jamais comme une valeur à
  transmettre.
- ``model_tier`` : le palier, toujours renseigné, dans le même vocabulaire que
  :func:`grimoire.missions.dispatch.start_tier_for` (``cheap``/``mid``) plus
  ``session`` pour V2 — que ``start_tier_for`` refuse (``None``, aucun palier
  automatique) alors qu'un dispatch interactif a toujours un repli : le
  modèle qui tient déjà la session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.missions.verifiability import Verifiability, as_dict, classify

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "MODEL_TIER_BY_CLASS",
    "RECOMMENDED_MODEL_BY_CLASS",
    "dispatch_advice",
    "model_hint",
    "model_tier_for",
    "recommended_model_for",
]

#: Le modèle que chaque classe de vérifiabilité recommande pour un sous-agent
#: dispatché en mode interactif (#654) — table opposable, à réutiliser
#: plutôt qu'à recopier (voir le docstring du module). Contrat *interne* :
#: ``_model_label`` (``hosts.emitters.claude_code``) la lit telle quelle,
#: ``"session"`` compris. Le contrat *externe* (:func:`recommended_model_for`,
#: :func:`dispatch_advice`) traduit ``"session"`` en ``None`` — voir le
#: docstring du module.
RECOMMENDED_MODEL_BY_CLASS: dict[Verifiability, str] = {
    Verifiability.V0: "haiku",
    Verifiability.V1: "sonnet",
    Verifiability.V2: "session",
}

#: Le palier de coût de chaque classe — toujours renseigné, contrairement à
#: ``recommended_model`` qui peut être ``None``. Même vocabulaire que
#: :func:`grimoire.missions.dispatch.start_tier_for` pour V0/V1
#: (``cheap``/``mid``) ; ``"session"`` pour V2, là où ``start_tier_for``
#: renvoie ``None`` (il refuse la classe plutôt que de poser un repli — un
#: dispatch automatique n'a personne à qui demander, un dispatch interactif
#: si).
MODEL_TIER_BY_CLASS: dict[Verifiability, str] = {
    Verifiability.V0: "cheap",
    Verifiability.V1: "mid",
    Verifiability.V2: "session",
}


def recommended_model_for(verifiability: Verifiability) -> str | None:
    """Le nom de modèle à passer à ``model=`` pour cette classe, ``None`` si aucun (V2).

    Traduit ``RECOMMENDED_MODEL_BY_CLASS[verifiability]`` : toute valeur sauf
    ``"session"`` passe telle quelle, ``"session"`` devient ``None`` — jamais
    une valeur qu'aucun host n'accepterait pour ce paramètre.
    """
    model = RECOMMENDED_MODEL_BY_CLASS[verifiability]
    return None if model == "session" else model


def model_tier_for(verifiability: Verifiability) -> str:
    """Le palier de cette classe — une entrée de :data:`MODEL_TIER_BY_CLASS`, toujours renseignée."""
    return MODEL_TIER_BY_CLASS[verifiability]


def model_hint(advice: dict[str, Any]) -> str:
    """Texte humain pour l'``advice`` rendu par :func:`dispatch_advice` — jamais ``None`` affiché tel quel.

    Utilisé par les surfaces texte (``grimoire task show``/``claim``/``move``
    ...) pour ne jamais imprimer littéralement ``recommended_model: None`` :
    la consigne qui l'accompagne (omettre ``model=``) doit être lisible sans
    relire ce module.
    """
    model = advice.get("recommended_model")
    if model:
        return str(model)
    return "aucun — omets le paramètre model, garde celui de la session"


def dispatch_advice(task: MissionTask) -> dict[str, Any]:
    """Vérifiabilité, modèle recommandé et palier pour *task* — un seul calcul, une seule fois.

    ``verifiability`` reprend le format de :func:`grimoire.missions.
    verifiability.as_dict` (classe, explication, détail par critère) : c'est
    déjà ce que ``task show`` et l'outil MCP ``task_show`` sérialisent, inutile
    d'en inventer un second format pour ``task_claim``/``task_context``.
    ``recommended_model``/``model_tier`` sont déduits de la même classe,
    jamais reclassée — voir le docstring du module pour le contrat exact
    (``recommended_model`` peut être ``None``, ``model_tier`` jamais).

    Une tâche sans critère exploitable (aucun critère du tout, ou un critère
    ambigu) est V2 par construction (voir ``classify_criteria`` — un faux V0
    est pire qu'un faux V2) : elle reçoit ici le palier le plus prudent,
    ``session`` (``recommended_model: None``), jamais une exception.
    """
    verifiability = classify(task)
    return {
        "verifiability": as_dict(task),
        "recommended_model": recommended_model_for(verifiability),
        "model_tier": model_tier_for(verifiability),
    }
