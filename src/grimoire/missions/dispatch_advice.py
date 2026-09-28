"""Le conseil de dispatch d'une tâche — vérifiabilité et modèle recommandé (issue #642).

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
recopie pas, un lot ultérieur la fera pointer ici pour qu'une seule table
existe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.missions.verifiability import Verifiability, as_dict, classify

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "RECOMMENDED_MODEL_BY_CLASS",
    "dispatch_advice",
    "recommended_model_for",
]

#: Le modèle que chaque classe de vérifiabilité recommande pour un sous-agent
#: dispatché en mode interactif (issue #642) — table opposable, à réutiliser
#: plutôt qu'à recopier (voir le docstring du module).
RECOMMENDED_MODEL_BY_CLASS: dict[Verifiability, str] = {
    Verifiability.V0: "haiku",
    Verifiability.V1: "sonnet",
    Verifiability.V2: "session",
}


def recommended_model_for(verifiability: Verifiability) -> str:
    """Le modèle recommandé pour cette classe — une entrée de :data:`RECOMMENDED_MODEL_BY_CLASS`."""
    return RECOMMENDED_MODEL_BY_CLASS[verifiability]


def dispatch_advice(task: MissionTask) -> dict[str, Any]:
    """Vérifiabilité et modèle recommandé pour *task* — un seul calcul, une seule fois.

    ``verifiability`` reprend le format de :func:`grimoire.missions.
    verifiability.as_dict` (classe, explication, détail par critère) : c'est
    déjà ce que ``task show`` et l'outil MCP ``task_show`` sérialisent, inutile
    d'en inventer un second format pour ``task_claim``/``task_context``.
    ``recommended_model`` est déduit de la même classe, jamais reclassée.

    Une tâche sans critère exploitable (aucun critère du tout, ou un critère
    ambigu) est V2 par construction (voir ``classify_criteria`` — un faux V0
    est pire qu'un faux V2) : elle reçoit ici le modèle le plus prudent,
    ``session``, jamais une exception.
    """
    verifiability = classify(task)
    return {
        "verifiability": as_dict(task),
        "recommended_model": recommended_model_for(verifiability),
    }
