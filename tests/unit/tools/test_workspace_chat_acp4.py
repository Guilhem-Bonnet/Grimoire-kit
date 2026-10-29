"""ACP4 : le chat d'orchestrateur face au pilote automatique — questions, résumé, gestes (humains seulement)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions import mandate as md
from grimoire.missions.autopilot import DECIDED_EVENT, AutopilotStore
from grimoire.missions.mandate import MandateRefusedError, MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_chat, workspace_chat_pilot

AGENT = Path(__file__).resolve().parents[2] / "fixtures" / "acp" / "chat_agent.py"
ACC = ("la PR est fusionnée",)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(workspace_chat.AGENT_COMMAND_ENV, f"{sys.executable} {AGENT} {tmp_path / 'agent.log'}")
    monkeypatch.setenv(workspace_chat.GRACE_ENV, "0")
    yield
    workspace_chat.shutdown_all()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _pilot_on(root: Path) -> None:
    MandateStore(root).grant(MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d"), origin=ORIGIN_HUMAN, via="test")
    AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via="test")


def _ask(root: Path, text: str) -> tuple[TaskService, str]:
    service = TaskService(root)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, text, "w")
    return service, tid


def test_une_question_reservee_est_posee_dans_le_chat_puis_tranchee_par_le_canal_humain(project: Path) -> None:
    _pilot_on(project)
    service, tid = _ask(project, "Quel mot de passe utiliser pour le serveur ?")
    rows = workspace_chat_pilot.pending_questions(project)
    assert [r["task_id"] for r in rows] == [tid] and "identifiants" in rows[0]["kept"]

    conv = workspace_chat._conversation(project)
    events = conv.events_after(0, 0.01)
    assert [e["type"] for e in events if e["type"] == "question"] == ["question"]

    workspace_chat.chat_send(project, {
        "text": "utilise le coffre", "answer_to": {"task_id": tid, "question_id": rows[0]["question_id"]},
    })
    answered = [e for e in service.ledger.events_for(tid) if e.event_type == "task.human_answered"]
    assert answered and answered[-1].payload["origin"] == ORIGIN_HUMAN
    assert any(e["type"] == "question_resolved" for e in conv.events_after(0, 0.01))


def test_une_question_que_le_pilote_decide_n_est_pas_posee(project: Path) -> None:
    _pilot_on(project)
    _ask(project, "Quelle voie pour le correctif ?")
    assert workspace_chat_pilot.pending_questions(project) == []


def test_pilote_coupe_aucune_question_dans_le_chat(project: Path) -> None:
    _ask(project, "Quel mot de passe ?")
    assert workspace_chat_pilot.pending_questions(project) == []


def test_repondre_a_une_question_non_posee_est_refuse(project: Path) -> None:
    _pilot_on(project)
    _service, tid = _ask(project, "Quelle voie ?")
    with pytest.raises(ValueError, match="plus posée"):
        workspace_chat_pilot.answer(project, tid, "inconnue", "oui")


def test_le_resume_liste_les_decisions_depuis_la_derniere_visite(project: Path) -> None:
    _pilot_on(project)
    service, tid = _ask(project, "Quelle voie ?")
    assert workspace_chat_pilot.summary(project) is None
    service.ledger.append_event(
        DECIDED_EVENT, tid, "task", "orch",
        {"task_id": tid, "question": "Quelle voie ?", "option": "A", "justification": "reco", "confidence": "high"},
    )
    digest = workspace_chat_pilot.summary(project)
    assert digest and digest["count"] == 1 and digest["decisions"][0]["option"] == "A"
    time.sleep(0.01)
    workspace_chat_pilot.mark_visit(project)
    assert workspace_chat_pilot.summary(project) is None


def test_l_agent_du_chat_ne_peut_ni_activer_ni_couper_le_pilote(project: Path) -> None:
    _pilot_on(project)
    before = AutopilotStore(project).history()
    conv = workspace_chat._conversation(project)
    workspace_chat.chat_send(project, {"text": "geste"})
    deadline = time.monotonic() + 15
    seen: list[dict[str, Any]] = []
    while time.monotonic() < deadline and not any(e["type"] == "turn_ended" for e in seen):
        seen += conv.events_after(len(seen) and seen[-1]["seq"], 0.2)
    gestures = [e for e in seen if e["type"] == "pilot_gesture"]
    assert gestures == [{"seq": gestures[0]["seq"], "type": "pilot_gesture", "on": True}]
    assert AutopilotStore(project).history() == before  # une proposition : rien n'est écrit
    for origin in (ORIGIN_ORCHESTRATOR, "agent", ""):
        with pytest.raises(MandateRefusedError):
            AutopilotStore(project).set(False, origin=origin, via="chat")
    assert AutopilotStore(project).state().active


def test_les_marqueurs_de_geste() -> None:
    assert workspace_chat_pilot.proposals("a [[pilote:on]] b [[pilote:off]]") == [True, False]
    assert workspace_chat_pilot.strip_markers("x[[pilote:on]]y") == "xy"
