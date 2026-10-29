"""``notify.command`` depuis le cockpit (I1) : le lire, l'écrire, l'essayer.

La commande vit là où ``grimoire tick`` la lit déjà (:func:`~grimoire.missions.decisions.load_notify_command`) :
``$GRIMOIRE_NOTIFY_COMMAND``, sinon ``options.notify.command`` du profil du standard. Ce module n'invente
aucun second emplacement. Le « Tester » lance la commande avec une décision d'essai, sans rien écrire
au ledger : un test n'est pas une notification de question.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from grimoire.missions.decisions import NOTIFY_ENV, PendingDecision, _run, load_notify_command

MAX_COMMAND_LENGTH = 1000


def command_problem(command: str) -> str:
    """Pourquoi cette commande ne peut pas se lancer (``""`` si elle est lisible). Sert aussi à `doctor`."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"commande illisible : {exc}"
    return "" if argv else "commande vide"


def notify_state(project_root: Path) -> dict[str, Any]:
    """``GET /api/workspace/notify`` : la commande effective, sa provenance, et si elle est lisible."""
    command = load_notify_command(project_root)
    from_env = bool(os.environ.get(NOTIFY_ENV, "").strip())
    return {
        "command": command,
        "source": "env" if from_env else ("profile" if command else "none"),
        "problem": command_problem(command) if command else "",
    }


def write_notify_command(project_root: Path, command: str) -> dict[str, Any]:
    """``POST /api/workspace/notify`` : pose (ou retire, si vide) ``options.notify.command`` dans le profil.

    ``ValueError`` pour une commande illisible ou trop longue, ``FileNotFoundError`` sans profil du standard :
    une commande ne se pose pas sur un projet qui n'en a pas.
    """
    from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
    from grimoire.tools._common import load_yaml_roundtrip, save_yaml

    command = command.strip()
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError(f"commande trop longue ({MAX_COMMAND_LENGTH} caractères au plus)")
    if command and (problem := command_problem(command)):
        raise ValueError(problem)
    path = project_root.resolve() / STANDARD_PROFILE_FILE
    if not path.is_file():
        raise FileNotFoundError(f"profil du standard absent : {STANDARD_PROFILE_FILE}")
    data = load_yaml_roundtrip(path)
    if not isinstance(data, dict):
        raise ValueError("le profil du standard n'est pas une table YAML : commande non écrite")
    options = data.get("options")
    if not isinstance(options, dict):
        options = {}
        data["options"] = options
    notify = options.get("notify")
    if not isinstance(notify, dict):
        notify = {}
        options["notify"] = notify
    if command:
        notify["command"] = command
    else:
        notify.pop("command", None)
    save_yaml(data, path)
    return notify_state(project_root)


def test_notify_command(project_root: Path, command: str | None = None) -> dict[str, Any]:
    """``POST /api/workspace/notify/test`` : lance la commande configurée avec une décision d'essai.

    Renvoie ``{ok, code}`` (``code`` : ``0`` si réussi, ``1`` sinon) et ``error`` quand elle a échoué.
    ``ValueError`` (donc 400) sans commande : il n'y a rien à essayer.
    """
    from grimoire.missions.waiting import Question

    cmd = (command if command is not None else load_notify_command(project_root)).strip()
    if not cmd:
        raise ValueError("aucune commande de notification configurée : enregistrez-en une avant d'essayer")
    question = Question(id="notify-test", kind="test", text="Ceci est un essai de notification.")
    error = _run(cmd, PendingDecision("notify-test", "Essai de notification", question, "", ()))
    return {"ok": not error, "code": 0 if not error else 1, "error": error}


def notify_route(project_root: Path, _query: dict[str, list[str]]) -> Any:
    """``GET /api/workspace/notify`` — la commande de notification effective et sa provenance (I1)."""
    return notify_state(project_root)


def notify_write_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/notify`` — pose (ou retire) ``options.notify.command`` du profil du standard (I1)."""
    raw = body.get("command")
    if not isinstance(raw, str):
        raise ValueError("`command` doit être une chaîne (vide pour la retirer)")
    return write_notify_command(project_root, raw)


def notify_test_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/notify/test`` — lance la commande configurée avec une décision d'essai (I1)."""
    raw = body.get("command")
    return test_notify_command(project_root, raw if isinstance(raw, str) else None)


GET_ROUTES = {"/api/workspace/notify": notify_route}
POST_ROUTES = {
    "/api/workspace/notify": notify_write_route,
    "/api/workspace/notify/test": notify_test_route,
}
