"""« À traiter » et le journal du pilote : une décision prise d'après une réponse humaine (GAO-jurisprudenc-001).

Sous ``autopilot.precedents: on``, l'application d'un précédent est montrée dans « Décisions à revoir » avec la question
d'origine et la date de la réponse humaine, puis au journal « Décidé par le pilote », où « Revenir » la défait. La
réponse humaine et la décision passent par la vraie voie (``TaskService.answer``) ; seule la mise en place (tâches,
escalades telles que le relecteur les écrit) passe par l'API Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from grimoire.missions import mandate as md
from grimoire.missions import orchestrator_session as osess
from grimoire.missions import precedents as pr
from grimoire.missions.autopilot import DECIDED_EVENT, AutopilotStore
from grimoire.missions.mandate import ORCHESTRATION_FILE, MandateRequest, MandateStore
from grimoire.missions.reviewer import REVIEW_ESCALATED_EVENT
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskClaim, TaskState
from grimoire.missions.service import TaskService

ACC = ("le test passe",)
PROV = {"kind": "rule", "ref": "critere-1"}
WORKER = "abcd1234"
FIRST = "Désaccord persistant (2 relectures NOT-READY) : nommage — reprendre la tâche ou l'abandonner ?"
SECOND = "Désaccord persistant (3 relectures NOT-READY) : couverture — reprendre la tâche ou l'abandonner ?"
TITLE = "Tranchée d'après ta réponse"


@pytest.fixture
def ruled(real_project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Mandat, réglage ``precedents: on`` et pilote actifs, défaits à la fin : le projet est partagé par la session.
    ``claude agents`` n'est jamais lancé : la session du worker y est dite arrêtée."""
    monkeypatch.setattr(osess, "_read_agents", lambda *_a, **_k: [{"id": WORKER, "kind": "background", "state": "stopped"}])
    policy = real_project / ORCHESTRATION_FILE
    before = policy.read_bytes() if policy.is_file() else None
    assert before is None or b"autopilot" not in before, "le projet de session porte déjà un réglage du pilote"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_bytes((before or b"").rstrip() + b"\nautopilot:\n  precedents: on\n")
    MandateStore(real_project).grant(
        MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d"), origin=ORIGIN_HUMAN, via="test"
    )
    AutopilotStore(real_project).set(True, origin=ORIGIN_HUMAN, via="test")
    try:
        yield real_project
    finally:
        AutopilotStore(real_project).set(False, origin=ORIGIN_HUMAN, via="test")
        MandateStore(real_project).revoke("manual", "fin du test")
        if before is None:
            policy.unlink()
        else:
            policy.write_bytes(before)


def _escalated(service: TaskService, title: str, text: str, qid: str) -> str:
    """Une tâche lancée par le tick, dont le relecteur escalade (événement d'escalade avec sa cause, puis la question)."""
    tid = service.add(title, ACC, owner="w", actor="w").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("w", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="w", claim=claim)
    service.ledger.append_event(
        "task.session_launched", tid, "task", "tick", {"task_id": tid, "session": WORKER, "session_id": "s-1"}
    )
    service.ledger.append_event(
        REVIEW_ESCALATED_EVENT, tid, "task", "tick",
        {"task_id": tid, "question_id": qid, "why": "2 relecture(s) NOT-READY", "cause": "not_ready_cycles"},
    )
    service.ask(tid, text, "tick", options=("reprendre", "abandonner"), recommended="reprendre", question_id=qid)
    return tid


def _decided_by_precedent(root: Path) -> tuple[str, str, pr.Precedent]:
    service = TaskService(root)
    first = _escalated(service, "Première escalade", FIRST, "q-juris-1")
    service.answer(first, "reprendre", "guilhem", origin=ORIGIN_HUMAN, question_id="q-juris-1")
    ruling = next(p for p in pr.precedents(service) if p.question_id == "q-juris-1")
    tid = _escalated(service, TITLE, SECOND, "q-juris-2")
    with md.acting_as_orchestrator():
        service.answer(
            tid, "abandonner", "orchestrator", origin=ORIGIN_ORCHESTRATOR, question_id="q-juris-2", provenance=PROV,
            confidence="low",
        )
    (decided,) = [e for e in service.ledger.events_for(tid) if e.event_type == DECIDED_EVENT]
    assert decided.payload["option"] == "reprendre" and decided.payload["recommendation_source"] == "precedent"
    return tid, "q-juris-2", ruling


def _open_a_traiter(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('a-traiter')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'a-traiter'")
    page.wait_for_selector(".at-wrap")


def test_l_application_d_un_precedent_se_relit_et_se_reprend(workspace: Page, ruled: Path) -> None:
    tid, qid, ruling = _decided_by_precedent(ruled)
    page = workspace
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _open_a_traiter(page)

    fold = page.locator("[data-role='review-decisions']")
    expect(fold.locator("summary")).to_contain_text("Décisions à revoir")
    fold.locator("summary").click()
    item = fold.locator(f"[data-role='review-decision'][data-task='{tid}'][data-question='{qid}']")
    expect(item).to_be_visible()
    expect(item).to_have_attribute("data-source", "precedent")
    expect(item.locator(".at-chip")).to_have_text("d’après ta réponse")
    expect(item).to_contain_text(TITLE)
    expect(item.locator(".at-default-option b")).to_have_text("reprendre")
    origin = item.locator("[data-role='review-precedent']")
    expect(origin).to_contain_text("Ta réponse du")  # la date de la réponse humaine
    expect(origin).to_contain_text(FIRST)  # la question d'origine, pas celle qui vient d'être tranchée

    deciding = page.locator(".at-section[data-section='decide'] .at-row .at-title").all_inner_texts()
    assert TITLE not in deciding  # aucune attente de l'humain

    fold.locator("[data-role='review-journal']").click()
    page.wait_for_selector("[data-role='pilot-journal']")
    card = page.locator(f"[data-role='pilot-decision'][data-task='{tid}']")
    expect(card.locator("[data-role='pj-source']")).to_contain_text("réponse humaine antérieure")
    expect(card.locator("[data-role='pj-precedent']")).to_contain_text(ruling.id)
    card.locator("[data-role='pilot-revert']").click()
    expect(card).to_contain_text("Repris par l’humain")
    assert TaskService(ruled).require(tid).status is TaskState.BLOCKED
    assert next(p for p in pr.precedents(TaskService(ruled)) if p.id == ruling.id).state == pr.REVERTED

    _open_a_traiter(page)
    expect(page.locator(f"[data-role='review-decision'][data-task='{tid}']")).to_have_count(0)
