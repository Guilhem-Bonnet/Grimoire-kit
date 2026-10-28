"""P88 : la route d'acceptation du cockpit refuse une requête sans jeton de session ou d'une autre origine.

Jouée contre un vrai serveur HTTP : le refus vient du transport (jeton, origine), pas d'un appel direct. Rien ne
doit être écrit — la tâche reste ouverte.
"""

from __future__ import annotations

import json

import pytest

from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import request_guard
from grimoire.tools.request_guard import SESSION_HEADER

from .test_mutation_guard import EVIL, JSON, Host, cockpit  # noqa: F401

pytestmark = pytest.mark.no_session_token

BODY = json.dumps({"reason": "relu"}).encode()


def _path(task_id: str) -> str:
    return f"/api/workspace/tasks/{task_id}/accept"


def _open_task(host: Host) -> str:
    return TaskService(host.root).add("À accepter", ("critère",), owner="guilhem", actor="t").task.id


def test_accept_without_a_session_token_is_refused(cockpit: Host) -> None:  # noqa: F811
    tid = _open_task(cockpit)
    assert cockpit.request("POST", _path(tid), BODY, JSON) == 403
    assert TaskService(cockpit.root).require(tid).status is not TaskState.CLOSED


def test_accept_from_a_foreign_origin_is_refused(cockpit: Host) -> None:  # noqa: F811
    tid = _open_task(cockpit)
    headers = {**JSON, "Origin": EVIL, SESSION_HEADER: request_guard.session_token()}
    assert cockpit.request("POST", _path(tid), BODY, headers) == 403
    assert TaskService(cockpit.root).require(tid).status is not TaskState.CLOSED
