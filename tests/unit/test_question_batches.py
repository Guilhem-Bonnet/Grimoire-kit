"""W6-07 (issue #564, ADP-8) — les questions d'une tâche comme objets du ledger, posées par lot.

Constat sur la base (origin/main) : K6 (#771) gare une tâche sur **une** question à la fois
(``Waiting.question``) ; deux clarifications font deux interruptions et deux notifications. Une
question n'a ni « bloquante ou non », ni « pourquoi », ni identité stable : une reprise la repose.
Aucune réponse ne peut être déduite par l'agent avec sa source ; aucun registre ne dit, au bout de la
tâche, combien de questions ont été posées, à qui, pourquoi et qui a répondu.

Test de fait de l'issue : une tâche qui a besoin de deux clarifications (finition + arbitrage
irréversible) les pose en un seul groupe ; le registre de la tâche liste les questions et pourquoi.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.decisions import NOTIFY_ENV, notify_pending, pending_decisions
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.questions import QuestionSpec, questions_summary
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import Question, Waiting

from .test_mutation_guard import JSON, Host, cockpit  # noqa: F401

pytestmark = pytest.mark.covers("cli:task questions", "cli:task resolve", "cli:task ask")

runner = CliRunner()
S = TaskState

FINITION = QuestionSpec(
    "Maquette ou version peaufinée ?", "choice", ("maquette", "peaufiné"),
    why="la finition n'est pas déterminable depuis le dépôt",
)
IRREVERSIBLE = QuestionSpec(
    "Supprimer l'ancienne table `users_v1` ?", "permission", ("supprimer", "garder"),
    why="arbitrage irréversible",
)


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX", NOTIFY_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def _running(service: TaskService, title: str = "Tâche", actor: str = "agent-a") -> str:
    tid = service.add(title, ("critère",), actor="t", owner="guilhem", ready=True).task.id
    service.claim(tid, actor, host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, actor)
    return tid


def _human(service: TaskService, tid: str, text: str, qid: str = "") -> Any:
    return service.answer(tid, text, "guilhem", origin=ORIGIN_HUMAN, question_id=qid)


# ── le test de fait de l'issue : deux clarifications, un seul groupe ─────────


def test_two_clarifications_are_one_interruption_and_one_notification(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    outcome = service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a")
    assert outcome.task.status is S.WAITING
    assert [q.text for q in outcome.plan.asked] == [FINITION.text, IRREVERSIBLE.text]
    waiting = outcome.task.waiting
    assert waiting is not None and len(waiting.group) == 2
    transitions = [e for e in service.ledger.list_events(tid)
                   if e.event_type == "task.transitioned" and e.payload["to_state"] == "waiting"]
    assert len(transitions) == 1  # une interruption, pas deux

    [pending] = pending_decisions(service.ledger)
    assert [q.text for q in pending.questions] == [FINITION.text, IRREVERSIBLE.text]
    probe = tmp_path / "notified.txt"
    script = tmp_path / "notify.py"
    script.write_text(f"open({probe.as_posix()!r}, 'a', encoding='utf-8').write('x')\n", encoding="utf-8")
    command = f'"{sys.executable}" "{script.as_posix()}"'
    outcomes = notify_pending(service.ledger, tmp_path, command=command)
    assert [o.status for o in outcomes] == ["sent"]
    assert probe.read_text(encoding="utf-8") == "x"  # une notification pour le lot

    summary = questions_summary(service.require(tid))
    assert summary["interruptions"] == 1 and summary["asked_to_human"] == 2
    assert {q["why"] for q in summary["questions"]} == {FINITION.why, IRREVERSIBLE.why}


def test_the_task_is_released_only_when_every_blocking_question_has_its_answer(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    asked = service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a").plan.asked
    first, second = (q.id for q in asked)

    move = _human(service, tid, "peaufiné", second)
    assert move.task.status is S.WAITING  # la première reste ouverte
    [pending] = pending_decisions(service.ledger)
    assert [q.id for q in pending.questions] == [first]
    with pytest.raises(GrimoireMissionError, match="déjà tranchée"):
        _human(service, tid, "maquette", second)

    assert _human(service, tid, "garder", first).task.status is S.READY
    assert pending_decisions(service.ledger) == []
    rows = {q["id"]: q for q in questions_summary(service.require(tid))["questions"]}
    assert rows[second]["answer"] == "peaufiné" and rows[second]["answer_origin"] == "human"
    assert rows[first]["status"] == rows[second]["status"] == "answered"


def test_the_tick_releases_a_batch_answered_but_not_woken(tmp_path: Path) -> None:
    """Réponses écrites, réveil interrompu : le tick rend la tâche — mais pas tant qu'une bloquante manque."""
    service = TaskService(tmp_path)
    tid = _running(service)
    first, second = (q.id for q in service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a").plan.asked)
    for qid in (first,):
        service.ledger.append_event("task.human_answered", tid, "task", "guilhem",
                                    {"task_id": tid, "question_id": qid, "answer": "x", "origin": ORIGIN_HUMAN})
    assert [d.action for d in service.wake_due()] == ["keep"]
    service.ledger.append_event("task.human_answered", tid, "task", "guilhem",
                                {"task_id": tid, "question_id": second, "answer": "y", "origin": ORIGIN_HUMAN})
    assert [d.action for d in service.wake_due()] == ["wake"]
    assert service.require(tid).status is S.READY


def test_an_agent_cannot_answer_a_question_of_the_batch(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    qid = service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a").plan.asked[1].id
    with pytest.raises(GrimoireMissionError, match="origine"):
        service.answer(tid, "supprimer", "agent-a", origin="agent", question_id=qid)
    # Un événement forgé par l'agent ne compte pas comme réponse.
    service.ledger.append_event("task.human_answered", tid, "task", "agent-a",
                                {"task_id": tid, "question_id": qid, "answer": "supprimer", "origin": "agent"})
    row = next(q for q in questions_summary(service.require(tid))["questions"] if q["id"] == qid)
    assert row["status"] == "open"


# ── reprise : une question déjà répondue n'est pas reposée ───────────────────


def test_a_resumed_session_does_not_ask_an_answered_question_again(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    first, second = (q.id for q in service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a").plan.asked)
    _human(service, tid, "maquette", first)
    _human(service, tid, "garder", second)
    service.claim(tid, "agent-b", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "agent-b")

    # La nouvelle session repose les mêmes questions (casse et espaces près) et une neuve.
    again = QuestionSpec("  maquette OU version peaufinée ?  ", "choice", ("maquette", "peaufiné"))
    fresh = QuestionSpec("Quel nom pour le module ?", blocking=False)
    outcome = service.ask_batch(tid, [again, IRREVERSIBLE, fresh], "agent-b")
    assert [r.id for r in outcome.plan.reused] == [first, second]
    assert [r.answer for r in outcome.plan.reused] == ["maquette", "garder"]
    assert [q.text for q in outcome.plan.asked] == [fresh.text]
    assert outcome.task.status is S.RUNNING  # rien de bloquant de neuf : pas d'interruption
    asked_events = [e for e in service.ledger.list_events(tid) if e.event_type == "task.question_asked"]
    assert len(asked_events) == 3  # 2 du premier lot + la neuve


def test_the_register_survives_a_reload_and_includes_k6_questions(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    service.ask(tid, "Fusionner la PR #12 ?", "agent-a", kind="merge", options=("fusionner", "attendre"))
    qid = service.require(tid).waiting.question.id  # type: ignore[union-attr]
    _human(service, tid, "fusionner", qid)
    task = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger").get_task(tid)
    assert task is not None
    [row] = questions_summary(task)["questions"]
    assert row["id"] == qid and row["status"] == "answered" and row["answered_by"] == "guilhem"
    assert row["answered_at"]  # horodatage de l'événement, lu au rejeu


# ── bloquante ou non ; déduite, source citée ────────────────────────────────


def test_a_non_blocking_question_does_not_park_and_a_human_can_answer_it_later(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    outcome = service.ask_batch(tid, [QuestionSpec("Préférez-vous `snake_case` ?", blocking=False, why="style")], "agent-a")
    assert outcome.task.status is S.RUNNING and outcome.task.waiting is None
    qid = outcome.plan.asked[0].id
    assert pending_decisions(service.ledger) == []
    move = _human(service, tid, "oui", qid)
    assert move.task.status is S.RUNNING
    [row] = questions_summary(service.require(tid))["questions"]
    assert row["status"] == "answered" and row["blocking"] is False


def test_a_deduced_answer_cites_its_source_and_never_settles_a_blocking_question(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    deduced = QuestionSpec("Quel gestionnaire de paquets ?", blocking=False, answer="uv", source="uv.lock")
    outcome = service.ask_batch(tid, [deduced], "agent-a")
    assert outcome.plan.asked == () and len(outcome.plan.deduced) == 1
    [row] = questions_summary(service.require(tid))["questions"]
    assert row["status"] == "deduced" and row["source"] == "uv.lock" and row["answer_origin"] == "agent"
    assert questions_summary(service.require(tid))["asked_to_human"] == 0

    with pytest.raises(ValueError, match="bloquante ne se déduit pas"):
        service.ask_batch(tid, [QuestionSpec("Supprimer ?", answer="oui", source="intuition")], "agent-a")
    with pytest.raises(ValueError, match="cite sa source"):
        service.ask_batch(tid, [QuestionSpec("Nom ?", blocking=False, answer="x")], "agent-a")

    open_q = service.ask_batch(tid, [QuestionSpec("Port par défaut ?", blocking=False)], "agent-a").plan.asked[0]
    with pytest.raises(ValueError, match="source"):
        service.resolve_question(tid, open_q.id, "8080", "", "agent-a")
    assert service.resolve_question(tid, open_q.id, "8080", "docker-compose.yml:12", "agent-a").status is S.RUNNING
    blocking = service.ask_batch(tid, [IRREVERSIBLE], "agent-a").plan.asked[0]
    with pytest.raises(GrimoireMissionError, match="seul un humain"):
        service.resolve_question(tid, blocking.id, "supprimer", "docs/plan.md", "agent-a")


def test_a_k6_waiting_line_keeps_its_exact_shape() -> None:
    """Les champs neufs ne s'écrivent que s'ils diffèrent du défaut : une ligne K6 se relit à l'identique."""
    raw = {"reason": "human", "question": {"id": "q-1", "kind": "merge", "text": "Fusionner ?",
                                           "options": ["oui"], "origin": "agent"}, "since": "t"}
    assert Waiting.from_dict(raw).to_dict() == raw
    assert Question.from_dict(raw["question"]).blocking is True  # type: ignore[arg-type]


# ── surfaces : CLI, MCP, cockpit ─────────────────────────────────────────────


def test_cli_ask_a_batch_list_and_resolve(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service, actor="cli")
    batch = tmp_path / "lot.yaml"
    batch.write_text(
        "questions:\n"
        "  - text: Maquette ou peaufiné ?\n    options: [maquette, peaufiné]\n    why: finition indéterminable\n"
        "  - text: Supprimer users_v1 ?\n    kind: permission\n"
        "  - text: Port par défaut ?\n    blocking: false\n",
        encoding="utf-8",
    )
    base = ["--project-root", str(tmp_path)]
    out = runner.invoke(app, ["-o", "json", "task", "ask", tid, "--from", batch.as_posix(), *base])
    assert out.exit_code == 0, out.output
    data = json.loads(out.stdout)
    assert data["parked"] is True and len(data["asked"]) == 3 and data["status"] == "waiting"
    port = next(q["id"] for q in data["asked"] if q["text"] == "Port par défaut ?")

    out = runner.invoke(app, ["-o", "json", "task", "resolve", tid, port, "8080", "--source", "compose.yml", *base])
    assert out.exit_code == 0, out.output
    out = runner.invoke(app, ["-o", "json", "task", "questions", tid, *base])
    assert out.exit_code == 0, out.output
    summary = json.loads(out.stdout)
    assert (summary["total"], summary["open"], summary["deduced"], summary["interruptions"]) == (3, 2, 1, 1)
    out = runner.invoke(app, ["-o", "json", "task", "questions", "--open", *base])
    assert [len(s["questions"]) for s in json.loads(out.stdout)] == [2]
    # Une réponse CLI sans terminal n'est pas humaine : refusée.
    first = summary["questions"][0]["id"]
    assert runner.invoke(app, ["task", "answer", tid, "maquette", "--question", first, *base]).exit_code == 1
    # Un lot illisible est refusé sans rien écrire.
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert runner.invoke(app, ["task", "ask", tid, "--from", bad.as_posix(), *base]).exit_code == 1


def test_cli_single_ask_with_why_goes_through_the_register(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service, actor="cli")
    out = runner.invoke(app, ["-o", "json", "task", "ask", tid, "Garder l'API v1 ?", "--why", "contrat public",
                              "--project-root", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["parked"] is True
    [row] = questions_summary(TaskService(tmp_path).require(tid))["questions"]  # relu : la CLI a écrit à côté
    assert row["why"] == "contrat public" and row["status"] == "open"


def _body(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        return dict(json.loads(result))
    return dict(json.loads("".join(getattr(block, "text", "") for block in result.content)))


def test_mcp_asks_a_batch_resolves_and_shows_but_never_answers(tmp_path: Path) -> None:
    from grimoire.mcp import server

    service = TaskService(tmp_path)
    tid = _running(service, actor="mcp-agent")
    common = {"project_path": str(tmp_path)}
    lot = json.dumps([
        {"text": "Maquette ou peaufiné ?", "why": "finition"},
        {"text": "Supprimer users_v1 ?", "kind": "permission"},
        {"text": "Port ?", "blocking": False},
        {"text": "Gestionnaire ?", "blocking": False, "answer": "uv", "source": "uv.lock"},
    ])
    out = _body(server.task_update(tid, "ask", questions=lot, **common))
    assert out["parked"] is True and len(out["asked"]) == 3 and len(out["deduced"]) == 1
    port = next(q["id"] for q in out["asked"] if q["text"] == "Port ?")
    assert "error" in _body(server.task_update(tid, "resolve", question_id=port, text="80", **common))  # sans source
    assert _body(server.task_update(tid, "resolve", question_id=port, text="80", source="compose.yml", **common))
    blocking = out["asked"][0]["id"]
    assert "error" in _body(server.task_update(tid, "resolve", question_id=blocking, text="x", source="y", **common))
    assert "error" in _body(server.task_update(tid, "answer", text="oui", **common))
    shown = _body(server.task_show(tid, **common))
    assert shown["questions_summary"]["deduced"] == 2 and shown["questions_summary"]["open"] == 2
    assert "error" in _body(server.task_update(tid, "ask", questions="pas du json", **common))


def test_the_cockpit_answers_one_question_of_the_batch_then_the_other(cockpit: Host) -> None:  # noqa: F811
    from grimoire.tools import workspace_api

    from .test_waiting_surfaces import _post

    service = TaskService(cockpit.root)
    tid = _running(service)
    first, second = (q.id for q in service.ask_batch(tid, [FINITION, IRREVERSIBLE], "agent-a").plan.asked)
    card = next(t for t in workspace_api.tasks_view(cockpit.root)["tasks"] if t["id"] == tid)
    assert [q["id"] for q in card["waiting"]["batch"]] == [second]

    status, body = _post(cockpit, f"/api/workspace/tasks/{tid}/answer", {"text": "garder", "question_id": second}, {**JSON})
    assert status == 200, body
    assert body["status"] == "waiting"
    card = next(t for t in workspace_api.tasks_view(cockpit.root)["tasks"] if t["id"] == tid)
    answered = [r["question"]["id"] for r in card["questions"] if r.get("answer_origin") == "human"]
    assert answered == [second]  # ce que la carte du cockpit lit pour ne montrer que la question restante

    status, body = _post(cockpit, f"/api/workspace/tasks/{tid}/answer", {"text": "maquette", "question_id": first}, {**JSON})
    assert status == 200, body
    assert body["status"] == "ready"
