"""Le cockpit pilote les tâches par la route, jamais par une écriture directe (issue #638, lot B).

Le critère de l'issue, joué contre un vrai serveur HTTP (``ForgeAPI`` +
``ThreadingHTTPServer``, le même que ``grimoire serve``) : un commentaire posé
par ``POST /api/workspace/tasks/<id>/comment`` apparaît dans le contexte du
``UserPromptSubmit`` suivant ; la priorité changée par la route se lit dans
``tasks_view`` et dans l'ordre du board ; annuler sans raison est refusé en
200 ``blocked: true`` — jamais un 500, jamais un état changé.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from grimoire.data import web_path
from grimoire.hosts.decisions import HookInput, decide_task_context
from grimoire.hosts.events import HookEvent
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.workspace_routes import TASK_ACTIONS, _task_action

ACCEPTATION = "le cockpit pilote la tache"
BOARD = Path("_grimoire/standard/task-board.yaml")


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _get(port: int, path: str) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def atelier(real_project: Path) -> Iterator[tuple[int, Path]]:
    api = ForgeAPI(real_project, Path(__file__).resolve().parents[2], web_path())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], real_project
    finally:
        httpd.shutdown()
        httpd.server_close()


def _reclame(projet: Path, titre: str, actor: str = "claude-session") -> str:
    service = TaskService(projet)
    missions = service.ledger.list_missions()
    mission = missions[0] if missions else service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, titre, acceptance=(ACCEPTATION,), owner="amelia")
    service.transition(task.id, TaskState.READY, "amelia")
    # Le claim passe par le ledger, pas par le gate `ready_to_in_progress` du
    # projet réel (context bundle, fournisseur activé) : ce fichier prouve le
    # pilotage d'une tâche tenue, pas la porte du claim — `test_service.py` s'en charge.
    service.ledger.claim_task(task.id, actor, "claude")
    service.project_board()
    return task.id


def _prompt(projet: Path) -> str:
    return decide_task_context(
        HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=projet, session_id="s-http")
    ).context


def test_les_quatre_gestes_sont_des_actions_de_tache() -> None:
    assert {"prioritize", "comment", "cancel", "ack"} <= set(TASK_ACTIONS)


def test_un_commentaire_pose_par_la_route_http_arrive_au_prompt_suivant(atelier: tuple[int, Path]) -> None:
    port, projet = atelier
    tid = _reclame(projet, "Consigne par HTTP")

    status, body = _post(
        port,
        f"/api/workspace/tasks/{tid}/comment",
        {"text": "lis d'abord le test de fait", "kind": "directive", "actor": "guilhem"},
    )
    assert status == 200, body
    assert body["directive"]["kind"] == "directive"
    assert body["directives_pending"] == 1

    context = _prompt(projet)
    assert "[Grimoire — consigne de l'orchestrateur] guilhem," in context
    assert "lis d'abord le test de fait" in context

    vue = _get(port, f"/api/workspace/tasks/{tid}")
    assert vue["directives_pending"] == 0 and vue["directives"][0]["delivered_at"]

    status, ack = _post(
        port, f"/api/workspace/tasks/{tid}/ack", {"directive_id": vue["directives"][0]["id"], "actor": "claude-session"}
    )
    assert status == 200 and ack["directive"]["acknowledged_at"]
    # Une session qui a déjà reçu la consigne ne la reçoit pas deux fois.
    assert "lis d'abord le test de fait" not in _prompt(projet)


def test_la_priorite_changee_par_la_route_se_lit_dans_tasks_view_et_dans_l_ordre_du_board(
    atelier: tuple[int, Path],
) -> None:
    port, projet = atelier
    a = _reclame(projet, "Priorite A")
    b = _reclame(projet, "Priorite B")

    status, body = _post(
        port, f"/api/workspace/tasks/{b}/prioritize", {"to": "critical", "reason": "bloque la release"}
    )
    assert status == 200 and body["effective_priority" if "effective_priority" in body else "priority"] == "critical"

    vue = _get(port, "/api/workspace/tasks")
    assert "critical" in vue["priorities"]
    par_id = {t["id"]: t for t in vue["tasks"]}
    assert par_id[b]["effective_priority"] == "critical"
    assert par_id[a]["effective_priority"] == "medium"

    board = YAML(typ="safe").load(projet / BOARD)
    en_cours = [t["task_id"] for t in board["tasks"] if t["status"] == "in_progress"]
    assert en_cours.index(b) < en_cours.index(a), "la plus pressante d'abord dans la colonne"

    status, refus = _post(port, f"/api/workspace/tasks/{a}/prioritize", {"to": "urgent"})
    assert status == 400 and "urgent" in json.dumps(refus)


def test_annuler_sans_raison_est_refuse_en_200_blocked_et_ne_change_rien(atelier: tuple[int, Path]) -> None:
    port, projet = atelier
    tid = _reclame(projet, "Annulation sans raison")

    status, body = _post(port, f"/api/workspace/tasks/{tid}/cancel", {})
    assert status == 200
    assert body["blocked"] is True
    assert [r["evidence"] for r in body["refusals"]] == ["reason"]
    assert TaskService(projet).require(tid).status is TaskState.CLAIMED

    # Tenue par une autre session : refus nommé, puis `force` explicite.
    status, body = _post(port, f"/api/workspace/tasks/{tid}/cancel", {"reason": "issue close", "actor": "guilhem"})
    assert status == 200 and body["blocked"] is True and body["refusals"][0]["evidence"] == "claim"
    assert "claude-session" in body["refusals"][0]["reason"]
    status, body = _post(
        port, f"/api/workspace/tasks/{tid}/cancel", {"reason": "issue close", "actor": "guilhem", "force": True}
    )
    assert status == 200 and body["transition"] == "claimed → cancelled"
    assert TaskService(projet).require(tid).status is TaskState.CANCELLED


def test_appel_direct_de_task_action_meme_contrat_que_la_route(real_project: Path) -> None:
    tid = _reclame(real_project, "Appel direct")
    note = _task_action(real_project, tid, "comment", {"text": "par appel direct", "actor": "guilhem"})
    assert note["directive"]["text"] == "par appel direct"
    with pytest.raises(ValueError, match="text"):
        _task_action(real_project, tid, "comment", {"text": "  "})
    with pytest.raises(ValueError, match="directive_id"):
        _task_action(real_project, tid, "ack", {})
