"""Pilote automatique (P83) : l'interrupteur de la barre du haut, « À traiter » filtré, le journal « Décidé par le pilote ».

Le geste part par la vraie route (``POST autonomy/autopilot``, clé du cockpit lue dans le HOME de test) et l'état se
relit sur ``GET autonomy/autopilot`` après rechargement. Seule la mise en place (mandat, questions posées, décision du
pilote déjà prise) passe par l'API Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from grimoire.missions import mandate as md
from grimoire.missions.autopilot import DEFERRED_EVENT, AutopilotStore
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskClaim, TaskState
from grimoire.missions.service import TaskService

ACC = ("la PR est fusionnée",)
PROV = {"kind": "rule", "ref": "critere-1"}
SWITCH = "#autopilot-switch"


def _key() -> str:
    md.ensure_control_key()
    return md.control_key_path().read_text(encoding="utf-8").strip()


def _reload(page: Page) -> None:
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)


@pytest.fixture
def mandated(real_project: Path) -> Iterator[Path]:
    """Un mandat actif pour le test, révoqué (et le pilote coupé) à la fin : le projet est partagé par la session."""
    MandateStore(real_project).grant(
        MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d"), origin=ORIGIN_HUMAN, via="test"
    )
    try:
        yield real_project
    finally:
        AutopilotStore(real_project).set(False, origin=ORIGIN_HUMAN, via="test")
        MandateStore(real_project).revoke("manual", "fin du test")


def _ask(root: Path, title: str, text: str, **ask: object) -> tuple[str, str]:
    service = TaskService(root)
    tid = service.add(title, ACC, owner="w", actor="w").task.id
    # Mise en place seulement (comme test_workspace_decisions_p68) : la tâche est amenée en `running` par le ledger.
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("w", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="w", claim=claim)
    service.ask(tid, text, "w", **ask)  # type: ignore[arg-type]
    return tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def _titles(page: Page) -> list[str]:
    page.evaluate("() => window.GrimoireWorkspace.goto('a-traiter')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'a-traiter'")
    page.wait_for_selector(".at-wrap")
    return page.locator(".at-row .at-title").all_inner_texts()


def test_le_commutateur_active_puis_coupe_et_l_etat_est_relu_de_l_api(workspace: Page, mandated: Path) -> None:
    page = workspace
    _reload(page)
    switch = page.locator(SWITCH)
    expect(switch).to_have_attribute("role", "switch")
    expect(switch).to_be_enabled()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(page.locator("#st-autopilot")).to_be_hidden()  # coupé : aucune trace dans la barre d'état

    switch.click()
    page.locator("#autopilot-key-input").fill(_key())
    page.locator("#autopilot-key-ok").click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(page.locator("#st-autopilot")).to_contain_text("Pilote automatique : activé depuis")
    assert AutopilotStore(mandated).state().active

    _reload(page)  # l'état vient de l'API, pas de la page
    expect(page.locator(SWITCH)).to_have_attribute("aria-checked", "true")
    expect(page.locator("#st-autopilot")).to_contain_text("activé depuis")

    page.locator(SWITCH).click()
    page.locator("#autopilot-key-input").fill(_key())
    page.locator("#autopilot-key-ok").click()
    expect(page.locator(SWITCH)).to_have_attribute("aria-checked", "false")
    expect(page.locator("#st-autopilot")).to_be_hidden()
    assert not AutopilotStore(mandated).state().active

    _reload(page)  # la clé ne survit pas au rechargement : une clé fausse est refusée et rien n'est écrit
    page.locator(SWITCH).click()
    page.locator("#autopilot-key-input").fill("faux")
    page.locator("#autopilot-key-ok").click()
    expect(page.locator("#autopilot-key-note")).to_contain_text("Clé refusée")
    page.locator("#autopilot-key-cancel").click()
    expect(page.locator(SWITCH)).to_have_attribute("aria-checked", "false")


def test_sans_mandat_actif_le_commutateur_est_desactive_et_dit_pourquoi(workspace: Page, real_project: Path) -> None:
    store = MandateStore(real_project)
    if store.state().active:
        store.revoke("manual", "mise en place du test")
    _reload(workspace)
    switch = workspace.locator(SWITCH)
    expect(switch).to_be_disabled()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(workspace.locator("#autopilot-why")).to_contain_text("Aucun mandat actif")


def test_le_commutateur_se_regle_au_clavier(workspace: Page, mandated: Path) -> None:
    page = workspace
    _reload(page)
    switch = page.locator(SWITCH)
    assert "Pilote automatique" in (switch.inner_text() or "")
    switch.focus()
    page.keyboard.press("Space")  # la première activation demande la clé, gardée en mémoire de page
    page.locator("#autopilot-key-input").fill(_key())
    page.keyboard.press("Enter")
    expect(switch).to_have_attribute("aria-checked", "true")
    switch.focus()
    page.keyboard.press("Enter")
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.focus()
    page.keyboard.press("Space")
    expect(switch).to_have_attribute("aria-checked", "true")


def test_pilote_active_a_traiter_ne_garde_que_le_reserve_et_la_confiance_basse(workspace: Page, mandated: Path) -> None:
    _routine, _ = _ask(mandated, "Routine P83", "Quelle voie pour le correctif ?", options=("a", "b"), recommended="a")
    _reserve, _ = _ask(
        mandated, "Réservé P83", "Quel identifiant utiliser pour se connecter au registre ?", options=("alice", "bob")
    )
    douteux, qid = _ask(mandated, "Doute P83", "Quelle voie pour le refactor ?", options=("x", "y"))
    service = TaskService(mandated)
    service.ledger.append_event(
        DEFERRED_EVENT,
        douteux,
        "task",
        "orchestrator",
        {"task_id": douteux, "question_id": qid, "reason": "confiance basse : deux voies se valent"},
    )
    _reload(workspace)
    assert {"Routine P83", "Réservé P83", "Doute P83"} <= set(_titles(workspace))  # pilote coupé : tout est là

    AutopilotStore(mandated).set(True, origin=ORIGIN_HUMAN, via="test")
    _reload(workspace)
    titles = _titles(workspace)
    assert "Routine P83" not in titles
    assert "Réservé P83" in titles and "Doute P83" in titles
    reasons = workspace.locator("[data-role='pilot-reason']").all_inner_texts()
    assert any("identifiants" in r for r in reasons)
    assert any("confiance basse" in r for r in reasons)
    count = workspace.locator("#decisions-count")
    assert int(count.get_attribute("data-pending") or "0") == len(titles)


def test_le_journal_liste_la_decision_et_revenir_la_rend_a_l_humain(workspace: Page, mandated: Path) -> None:
    tid, qid = _ask(mandated, "Décidée P83", "Quelle voie pour le correctif ?", options=("a", "b"), recommended="a")
    AutopilotStore(mandated).set(True, origin=ORIGIN_HUMAN, via="test")
    service = TaskService(mandated)
    with md.acting_as_orchestrator():
        service.answer(
            tid,
            "a",
            "orchestrator",
            origin=ORIGIN_ORCHESTRATOR,
            question_id=qid,
            provenance=PROV,
            justification="l'option recommandée par la question",
            confidence="high",
        )
    workspace.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'decisions' })")
    workspace.wait_for_selector("[data-role='pilot-journal']")
    card = workspace.locator(f"[data-role='pilot-decision'][data-task='{tid}']")
    expect(card).to_be_visible()
    expect(card.locator("[data-role='pj-question']")).to_contain_text("Quelle voie pour le correctif")
    expect(card.locator("[data-role='pj-option']")).to_have_text("a")
    expect(card.locator("[data-role='pj-why']")).to_contain_text("recommandée")
    expect(card.locator("[data-role='pj-source']")).to_contain_text("option recommandée")
    card.locator("[data-role='pilot-revert']").click()
    expect(card).to_contain_text("Repris par l’humain")
    assert TaskService(mandated).require(tid).status is TaskState.BLOCKED
