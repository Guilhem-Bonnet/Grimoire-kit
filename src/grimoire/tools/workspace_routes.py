"""Le dispatch de ``/api/workspace/…`` — une table, deux hôtes, une cible.

La vue de travail est la même coque pour ``grimoire serve`` (un projet) et
``grimoire cockpit serve`` (une flotte). Ce module est le point où cette
promesse devient vérifiable : les lectures sont une table pure
``(méthode, chemin) → fonction(project_root, …)``, sans état ni notion d'hôte.
L'atelier l'appelle avec sa racine unique, le cockpit avec celle qu'il a
résolue depuis ``?project=`` — et le test
``tests/unit/test_workspace_routes.py`` prouve que chaque route honore la
cible qu'on lui donne.

Les écritures sont ailleurs dans le même fichier, mais derrière une porte
différente : :func:`workspace_post` n'est câblé que sur l'hôte mono-projet. Le
cockpit se déclare ``readOnly`` depuis sa création ; lui donner de quoi
réclamer une tâche ou créer un override dans un dépôt qu'il ne sert pas serait
une régression de gouvernance, pas une commodité.

Ajouter une lecture : une entrée dans :data:`GET_ROUTES`. Ajouter une écriture :
une entrée dans :data:`POST_ROUTES`. Les deux tables sont énumérées par les
tests, donc une route ajoutée sans test de cible se voit.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.tools import workspace_api, workspace_exec

__all__ = [
    "GET_ROUTES",
    "POST_ROUTES",
    "PREFIX",
    "WORKSPACE_UNHANDLED",
    "workspace_get",
    "workspace_post",
]

#: Tout ce que la vue de travail ajoute vit sous ce préfixe : aucune collision
#: possible avec les routes héritées, et un seul `startswith` à câbler par hôte.
PREFIX = "/api/workspace/"

#: Sentinelle, même contrat que ``forge_routes.API_GET_UNHANDLED`` : distingue
#: « chemin inconnu » d'une route qui rendrait légitimement ``None``.
WORKSPACE_UNHANDLED = object()

_Query = dict[str, list[str]]
_GetHandler = Callable[[Path, _Query], Any]
_PostHandler = Callable[[Path, dict[str, Any]], Any]


def _one(query: _Query, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _translate(exc: Exception) -> Exception:
    """Traduit une erreur du domaine en erreur que le transport sait coder.

    Les hôtes n'attrapent que ``FileNotFoundError`` (404), ``PermissionError``
    (403) et ``ValueError`` (400). Une :class:`GrimoireError` qui remonterait
    telle quelle deviendrait un 500 avec une trace — donc une panne là où il y
    a un refus explicable. La traduction se fait ici, dans notre couche, plutôt
    qu'en élargissant le ``except`` de deux fichiers hérités.
    """
    from grimoire.core.exceptions import GrimoireError, GrimoireMissionError

    if isinstance(exc, GrimoireMissionError):
        return FileNotFoundError(str(exc))
    if isinstance(exc, GrimoireError):
        return ValueError(str(exc))
    return exc


# ── Lectures ────────────────────────────────────────────────────────────────


def _glossary(project_root: Path, _query: _Query) -> Any:
    return workspace_api.glossary_view(project_root)


def _tasks(project_root: Path, query: _Query) -> Any:
    return workspace_api.tasks_view(
        project_root, mission=_one(query, "mission"), status=_one(query, "status")
    )


def _files(project_root: Path, query: _Query) -> Any:
    return workspace_api.files_view(project_root, tier=_one(query, "tier"))


def _file(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_view(project_root, _one(query, "path"))


def _file_diff(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_diff(project_root, _one(query, "path"))


def _blueprints(project_root: Path, _query: _Query) -> Any:
    return workspace_api.blueprints_view(project_root)


def _file_usage(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_usage(project_root, _one(query, "path"))


def _file_history(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_history(project_root, _one(query, "path"))


def _commands(_project_root: Path, _query: _Query) -> Any:
    return {"commands": workspace_exec.catalogue(), "count": len(workspace_exec.ALLOWED)}


def _doctor(project_root: Path, _query: _Query) -> Any:
    return workspace_exec.doctor_view(project_root)


#: Chemins exacts. Les routes paramétrées par un identifiant de tâche sont
#: traitées à part dans :func:`workspace_get`, parce qu'un identifiant de ledger
#: n'est pas un segment fixe.
GET_ROUTES: dict[str, _GetHandler] = {
    f"{PREFIX}glossary": _glossary,
    f"{PREFIX}tasks": _tasks,
    f"{PREFIX}files": _files,
    f"{PREFIX}file": _file,
    f"{PREFIX}file/diff": _file_diff,
    f"{PREFIX}file/usage": _file_usage,
    f"{PREFIX}file/history": _file_history,
    f"{PREFIX}commands": _commands,
    f"{PREFIX}doctor": _doctor,
    f"{PREFIX}blueprints": _blueprints,
}


def _task_id(path: str, suffix: str = "") -> str:
    """Extrait l'identifiant de ``/api/workspace/tasks/<id>[/<suffix>]``."""
    rest = path[len(f"{PREFIX}tasks/") :]
    if suffix:
        rest = rest[: -(len(suffix) + 1)]
    task_id = rest.strip("/")
    if not task_id or "/" in task_id:
        raise ValueError("identifiant de tâche invalide")
    return task_id


def workspace_get(project_root: Path, path: str, query: _Query) -> Any:
    """Résout une lecture de la vue de travail pour *project_root*.

    Rend :data:`WORKSPACE_UNHANDLED` si le chemin n'est pas à nous, pour que
    l'hôte appelant poursuive sa propre chaîne (fichiers statiques, 404).
    """
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError

    try:
        handler = GET_ROUTES.get(path)
        if handler is not None:
            return handler(project_root, query)
        if path.startswith(f"{PREFIX}tasks/"):
            if path.endswith("/trace"):
                return workspace_api.task_trace_view(project_root, _task_id(path, "trace"))
            if path.endswith("/recall"):
                return workspace_api.task_recall_view(project_root, _task_id(path, "recall"))
            return workspace_api.task_view(project_root, _task_id(path))
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED


# ── Écritures — hôte mono-projet seulement ──────────────────────────────────


def _create_override(project_root: Path, body: dict[str, Any]) -> Any:
    """Copie un fichier de l'étage kit vers l'étage overrides.

    C'est le geste que la spécification attache à « éditer un fichier du kit » :
    on ne modifie pas le kit, on prend possession d'une copie qui prime et qui
    survit à ``grimoire up``. Un override déjà là n'est jamais écrasé — la
    réponse le dit et le geste est idempotent.
    """
    from grimoire.core import layout

    root = project_root.resolve()
    source = workspace_api.safe_relpath(root, body.get("path"))
    rel = source.relative_to(root).as_posix()
    prefix = f"{layout.KIT_DIR}/"
    if not rel.startswith(prefix):
        raise workspace_api.WorkspacePathError(
            "seul un fichier de l'étage kit se prend en override"
        )
    if not source.is_file():
        raise FileNotFoundError(f"introuvable : {rel}")
    dest_rel = f"{layout.OVERRIDES_DIR}/{rel[len(prefix) :]}"
    dest = root / dest_rel
    if dest.is_file():
        return {"created": False, "override_path": dest_rel, "note": "override déjà présent"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return {"created": True, "override_path": dest_rel, "from": rel}


def _write_file(project_root: Path, body: dict[str, Any]) -> Any:
    """Écrit un fichier d'un étage éditable — l'étage overrides, et lui seul.

    Écrire dans l'étage kit serait perdu à la prochaine mise à jour ; le refus
    nomme l'override à créer plutôt que de laisser l'utilisateur découvrir la
    perte trois jours plus tard.
    """
    root = project_root.resolve()
    target = workspace_api.safe_relpath(root, body.get("path"))
    tier = workspace_api.tier_of(root, target)
    if tier is None or not tier["editable"]:
        raise workspace_api.WorkspacePathError(
            "étage non éditable : prenez d'abord un override (POST /api/workspace/file/override)"
        )
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("`text` requis")
    if len(text.encode("utf-8")) > workspace_api.FILE_TEXT_LIMIT:
        raise ValueError("contenu trop volumineux")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return workspace_api.file_view(root, target.relative_to(root).as_posix())


def _command(project_root: Path, body: dict[str, Any]) -> Any:
    argv = body.get("argv")
    if isinstance(argv, str):
        argv = argv.split()
    if not isinstance(argv, list):
        raise ValueError("`argv` doit être une liste de mots")
    return workspace_exec.run_command(
        project_root, argv, allow_mutation=body.get("confirm") is True
    )


def _task_action(project_root: Path, task_id: str, action: str, body: dict[str, Any]) -> Any:
    """Réclame, déplace, bloque ou ferme une tâche — gate de preuve compris.

    Le service est le même que celui du CLI et du serveur MCP : un gate
    contourné ici le serait partout, donc il n'y a qu'un endroit où il pourrait
    l'être, et ce n'est pas celui-ci.
    """
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve())
    actor = str(body.get("actor") or "workspace")
    if action == "claim":
        move = service.claim(task_id, actor, str(body.get("host") or "workspace"))
    elif action == "close":
        move = service.transition(task_id, TaskState.CLOSED, actor, str(body.get("reason") or ""))
    elif action == "block":
        reason = str(body.get("reason") or "")
        if not reason:
            raise ValueError("`reason` requis pour bloquer une tâche")
        move = service.transition(task_id, TaskState.BLOCKED, actor, reason)
    elif action == "move":
        target = body.get("to")
        try:
            state = TaskState(str(target))
        except ValueError as exc:
            raise ValueError(
                f"état inconnu : {target!r} — parmi {', '.join(s.value for s in TaskState)}"
            ) from exc
        move = service.transition(task_id, state, actor, str(body.get("reason") or ""))
    else:  # pragma: no cover — garde de complétude
        raise ValueError(f"action inconnue : {action}")
    return move.to_dict()


#: Écritures à chemin fixe. Les actions de tâche sont paramétrées, cf. plus bas.
POST_ROUTES: dict[str, _PostHandler] = {
    f"{PREFIX}file/override": _create_override,
    f"{PREFIX}file/write": _write_file,
    f"{PREFIX}command": _command,
}

#: Les verbes qu'une tâche accepte depuis l'interface.
TASK_ACTIONS = ("claim", "move", "block", "close")


def workspace_post(project_root: Path, path: str, body: dict[str, Any]) -> Any:
    """Résout une écriture de la vue de travail. Hôte mono-projet uniquement."""
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.service import TaskRefusedError

    try:
        handler = POST_ROUTES.get(path)
        if handler is not None:
            return handler(project_root, body)
        if path.startswith(f"{PREFIX}tasks/"):
            tail = path[len(f"{PREFIX}tasks/") :].strip("/")
            task_id, _, action = tail.partition("/")
            if task_id and action in TASK_ACTIONS:
                return _task_action(project_root, task_id, action, body)
    except TaskRefusedError as exc:
        # Un gate rouge n'est pas une panne du serveur : c'est la réponse. On
        # la rend telle quelle, avec la preuve manquante et son remède, comme
        # le fait déjà l'outil MCP `task_update`.
        return exc.to_dict()
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED
