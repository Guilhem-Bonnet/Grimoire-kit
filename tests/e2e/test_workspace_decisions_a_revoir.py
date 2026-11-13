"""« À traiter » : le bloc « Décisions à revoir » (GAO-pilote-appli-001).

Sous ``autopilot.default_action: apply_recommended``, une décision que le pilote a prise par défaut est montrée dans un
bloc replié, hors des lignes et du compteur d'attente, et mène au journal « Décidé par le pilote ». La décision est prise
par la vraie voie (``TaskService.answer`` d'origine ``orchestrator``) ; seule la mise en place passe par l'API Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from grimoire.missions import mandate as md
from grimoire.missions.autopilot import DECIDED_EVENT, AutopilotStore
from grimoire.missions.mandate import ORCHESTRATION_FILE, MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskClaim, TaskState
from grimoire.missions.service import TaskService

ACC = ("le test passe",)
PROV = {"kind": "rule", "ref": "critere-1"}
TITLE = "Voie par défaut à revoir"


@pytest.fixture
def piloted(real_project: Path) -> Iterator[Path]:
    """Mandat, réglage ``apply_recommended`` et pilote actifs, défaits à la fin : le projet est partagé par la session."""
    policy = real_project / ORCHESTRATION_FILE
    before = policy.read_bytes() if policy.is_file() else None
    assert before is None or b"autopilot" not in before, "le projet de session porte déjà un réglage du pilote"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_bytes((before or b"").rstrip() + b"\nautopilot:\n  default_action: apply_recommended\n")
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


def _decided_by_default(root: Path) -> tuple[str, str]:
    """Une question qui recommande « b », à laquelle une réponse sans confiance propose « a » : le pilote applique « b »."""
    service = TaskService(root)
    tid = service.add(TITLE, ACC, owner="w", actor="w").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("w", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="w", claim=claim)
    service.ask(
        tid, "Quelle voie pour le correctif ?", "w", options=("a", "b"), recommended="b", why="les critères disent b"
    )
    qid = service.require(tid).waiting.question.id  # type: ignore[union-attr]
    with md.acting_as_orchestrator():
        service.answer(
            tid, "a", "orchestrator", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, confidence="low"
        )
    (decided,) = [e for e in service.ledger.events_for(tid) if e.event_type == DECIDED_EVENT]
    assert decided.payload["option"] == "b" and decided.payload["recommendation_source"] == "default"
    return tid, qid


def test_a_traiter_montre_la_decision_par_defaut_sans_la_compter(workspace: Page, piloted: Path) -> None:
    tid, qid = _decided_by_default(piloted)
    page = workspace
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('a-traiter')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'a-traiter'")
    page.wait_for_selector(".at-wrap")

    fold = page.locator("[data-role='review-decisions']")
    expect(fold.locator("summary")).to_contain_text("Décisions à revoir")
    expect(fold).not_to_have_attribute("open", "")  # replié : rien n'y attend
    fold.locator("summary").click()
    item = fold.locator(f"[data-role='review-decision'][data-task='{tid}'][data-question='{qid}']")
    expect(item).to_be_visible()
    expect(item).to_contain_text(TITLE)
    expect(item).to_contain_text("Quelle voie pour le correctif ?")
    expect(item.locator(".at-default-option b")).to_have_text("b")
    expect(item.locator(".at-default-why")).to_contain_text("les critères disent b")
    expect(item).to_have_attribute("data-source", "default")
    expect(item.locator(".at-chip")).to_have_text("appliquée par défaut")

    # Aucune attente de l'humain : pas de ligne « à décider » pour cette tâche, et le compteur ne compte que les gestes.
    deciding = page.locator(".at-section[data-section='decide'] .at-row .at-title").all_inner_texts()
    assert TITLE not in deciding
    gestures = page.locator(
        ".at-section:not([data-section='maintain']):not([data-section='resolved']) .at-row .at-title"
    ).count()
    assert int(page.locator("#decisions-count").get_attribute("data-pending") or "0") == gestures

    # Le bloc mène au journal, où la décision dit sa source et se reprend (« Revenir »).
    fold.locator("[data-role='review-journal']").click()
    page.wait_for_selector("[data-role='pilot-journal']")
    card = page.locator(f"[data-role='pilot-decision'][data-task='{tid}']")
    expect(card.locator("[data-role='pj-option']")).to_have_text("b")
    expect(card.locator("[data-role='pj-source']")).to_contain_text("appliquée par défaut")
    card.locator("[data-role='pilot-revert']").click()
    expect(card).to_contain_text("Repris par l’humain")
    assert TaskService(piloted).require(tid).status is TaskState.BLOCKED

    page.evaluate("() => window.GrimoireWorkspace.goto('a-traiter')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'a-traiter'")
    page.wait_for_selector(".at-wrap")
    expect(page.locator(f"[data-role='review-decision'][data-task='{tid}']")).to_have_count(0)  # reprise : sortie du bloc
