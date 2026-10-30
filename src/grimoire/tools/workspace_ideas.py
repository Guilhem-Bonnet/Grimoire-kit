"""Routes de Travail pour « Une idée ? » et la boîte à idées (I2, I13).

Module voisin de :mod:`grimoire.tools.workspace_routes` (au plafond du cliquet de lignes), qui
fusionne :data:`GET_ROUTES` et :data:`POST_ROUTES` dans ses tables. Le cockpit ne câble les
écritures que pour son projet de lancement, derrière la garde d'écriture commune.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PREFIX = "/api/workspace/"


def task_intake(_project_root: Path, body: dict[str, Any]) -> Any:
    """« Une idée ? » de Travail : les propositions de l'intake déterministe, sans rien écrire ni LLM."""
    from grimoire.missions.intake_tasks import proposals_for

    return {"proposals": proposals_for(str(body.get("text") or "")), "written": False}


def task_intake_create(project_root: Path, body: dict[str, Any]) -> Any:
    """« Créer N tâches » : les propositions cochées par ``TaskService.add``, comme ``tasks/add``.

    Un refus du gate laisse la tâche en ``proposed`` ; la raison revient avec elle.
    """
    from grimoire.missions.intake_tasks import create_from_proposals
    from grimoire.missions.service import TaskService

    items = body.get("tasks")
    if not isinstance(items, list) or not items:
        raise ValueError("`tasks` doit lister au moins une proposition")
    created = create_from_proposals(
        TaskService(project_root.resolve()), items, actor=str(body.get("actor") or "workspace"), origin="cockpit",
    )
    return {"created": created}


def _ideas(project_root: Path, _query: dict[str, list[str]]) -> Any:
    """``GET /api/workspace/ideas`` — la boîte à idées, dans l'ordre où elles ont été gardées."""
    from grimoire.missions.ideas import IdeaStore

    return {"ideas": [i.to_dict() for i in IdeaStore(project_root.resolve()).list()]}


def _idea_add(project_root: Path, body: dict[str, Any]) -> Any:
    """« Garder pour plus tard » : une ligne de plus dans ``ideas.jsonl``, sans trier ni créer de tâche."""
    from grimoire.missions.ideas import IdeaError, IdeaStore

    try:
        return IdeaStore(project_root.resolve()).add(str(body.get("text") or ""), "cockpit").to_dict()
    except IdeaError as exc:
        raise ValueError(str(exc)) from exc


def _idea_drop(project_root: Path, body: dict[str, Any]) -> Any:
    """« Écarter » une idée ; ``undo: true`` la restaure (l'annulation est un événement de plus, pas une ligne retirée)."""
    from grimoire.missions.ideas import IdeaError, IdeaStore

    store, idea_id = IdeaStore(project_root.resolve()), str(body.get("id") or "")
    try:
        return (store.restore(idea_id) if body.get("undo") is True else store.drop(idea_id)).to_dict()
    except IdeaError as exc:
        raise ValueError(str(exc)) from exc


def _idea_promote(project_root: Path, body: dict[str, Any]) -> Any:
    """Promeut une idée : avec ``tasks`` (créées par l'intake du cockpit) elle est seulement marquée ;
    sans, l'intake déterministe crée les tâches, comme ``grimoire idea promote``."""
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.ideas import IdeaError, IdeaStore
    from grimoire.missions.service import TaskService

    store, idea_id = IdeaStore(project_root.resolve()), str(body.get("id") or "")
    try:
        tasks = body.get("tasks")
        if isinstance(tasks, list) and tasks:
            return {"idea": store.mark_promoted(idea_id, [str(t) for t in tasks]).to_dict(), "created": []}
        idea, created = store.promote(
            idea_id, TaskService(project_root.resolve()), actor=str(body.get("actor") or "workspace"), origin="cockpit",
        )
        return {"idea": idea.to_dict(), "created": created}
    except (IdeaError, GrimoireError) as exc:
        raise ValueError(str(exc)) from exc


GET_ROUTES = {f"{PREFIX}ideas": _ideas}
POST_ROUTES = {
    f"{PREFIX}ideas/add": _idea_add,
    f"{PREFIX}ideas/drop": _idea_drop,
    f"{PREFIX}ideas/promote": _idea_promote,
}
