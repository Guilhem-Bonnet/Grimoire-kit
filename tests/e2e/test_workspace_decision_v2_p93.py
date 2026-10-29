"""Travail › Décisions : la carte v2 (P93) — pièces, choix riches, demandes précédentes.

Les questions sont posées par le vrai ``TaskService.ask`` (schéma P92) ; les
réponses partent par la vraie route (``POST tasks/<id>/answer``) et l'effet se
relit au ledger : le texte sérialisé et ``answer_data`` de ``task.human_answered``.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test y écrit les captures
(clair, sombre, 390 px) pour les comparer aux artboards du canevas ; sans la
variable il ne fait rien (il ne se skippe pas : le mode strict de la CI promeut
tout skip en échec).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Page, expect

from grimoire.missions.decisions import pending_decisions
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import Attachment, Choice, Recommendation

ICONS = tuple(f"icone-{n:02d}" for n in range(1, 9))
DIFF = "@@ -1,3 +1,4 @@\n def run():\n-    return 1\n+    return 2\n+    # corrigé\n"


def _answer_old(service: TaskService) -> None:
    for decision in pending_decisions(service.ledger):
        if decision.question.origin == "flow":
            continue
        service.answer(decision.task_id, "attendre", "guilhem", origin=ORIGIN_HUMAN, question_id=decision.question.id)


def _running(service: TaskService, title: str) -> str:
    tid = service.add(title, ("critère",), owner="amelia", actor="t").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="agent-a", claim=claim)
    return tid


def _reprise(service: TaskService, tid: str) -> None:
    service.ask(
        tid, "", "agent-a",
        title="Reprendre la tâche « Cockpit » ou l'abandonner ?",
        context="Le contrôle et l'agent ne sont pas d'accord depuis deux tentatives.",
        choices=(
            Choice("reprendre", "Reprendre la tâche", "Relance une session neuve avec ta consigne."),
            Choice("abandonner", "Abandonner", "Annule la tâche ; la branche reste en place.", danger=True),
        ),
        answer_mode="single_text",
        attachments=(
            Attachment("diff", "tests/e2e", primary=True, excerpt=DIFF, stats={"files": 1, "added": 2, "removed": 1}),
            Attachment("ci", "CI", excerpt="lint : succès\ne2e : échec (3 min)", state="failure", stats={"jobs": 2, "failed": 1}),
            Attachment("command", "Vérifier à la main", ref="git diff origin/main...HEAD -- tests/e2e/"),
        ),
        recommendation=Recommendation("reprendre", "Le diff ne touche que des tests.", "Une session neuve repart."),
    )


def _open_decisions(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'decisions' })")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector("[data-role='decisions']")


def _card(page: Page, tid: str):  # type: ignore[no-untyped-def]
    return page.locator(f"[data-role='decision'][data-task='{tid}']")


def _answered(service: TaskService, tid: str) -> dict:  # type: ignore[type-arg]
    events = [e for e in service.ledger.list_events(tid) if e.event_type == "task.human_answered"]
    assert events, "aucune réponse humaine au ledger"
    return events[-1].payload


def test_une_question_v2_se_repond_par_un_choix_unique_et_une_consigne(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_old(service)
    tid = _running(service, "Cockpit")
    _reprise(service, tid)

    _open_decisions(workspace)
    card = _card(workspace, tid)
    expect(card.locator("[data-role='decision-title']")).to_have_text("Reprendre la tâche « Cockpit » ou l'abandonner ?")
    expect(card.locator("[data-role='decision-context']")).to_contain_text("ne sont pas d'accord")
    # Les pièces : trois puces, la pièce primaire (le diff) est dépliée d'office.
    chips = card.locator("[data-role='attachment']")
    expect(chips).to_have_count(3)
    expect(chips.nth(0)).to_have_attribute("aria-expanded", "true")
    expect(card.locator("[data-role='attachment-detail'] .dc-add")).to_have_count(2)
    chips.nth(1).click()
    expect(chips.nth(1)).to_have_attribute("aria-expanded", "true")
    expect(chips.nth(0)).to_have_attribute("aria-expanded", "false")
    expect(card.locator("[data-role='attachment-detail']:visible")).to_contain_text("e2e : échec")
    # La recommandation est préremplie mais rien n'est envoyé.
    expect(card.locator("[data-role='recommendation']")).to_contain_text("Recommandé : Reprendre la tâche")
    options = card.locator("[data-role='answer-option']")
    expect(options.nth(0)).to_have_attribute("aria-checked", "true")
    assert TaskService(real_project).require(tid).status is TaskState.WAITING
    # R sélectionne la recommandation, sans l'envoyer ; 2 choisit l'autre option.
    card.focus()
    workspace.keyboard.press("2")
    expect(options.nth(1)).to_have_attribute("aria-checked", "true")
    workspace.keyboard.press("r")
    expect(options.nth(0)).to_have_attribute("aria-checked", "true")
    assert TaskService(real_project).require(tid).status is TaskState.WAITING

    card.get_by_label("Consigne (facultatif)").fill("Vérifie d'abord le critère 3.")
    with workspace.expect_response(lambda r: "/answer" in r.url) as sent:
        card.get_by_role("button", name="Envoyer").click()
    assert sent.value.ok
    body = json.loads(sent.value.request.post_data or "{}")
    assert body["text"] == "reprendre — Vérifie d'abord le critère 3."
    assert body["answer_data"] == {"choices": ["reprendre"], "note": "Vérifie d'abord le critère 3.", "question_id": body["question_id"]}
    payload = _answered(TaskService(real_project), tid)
    assert payload["answer"] == body["text"]
    assert payload["answer_data"]["choices"] == ["reprendre"]
    assert TaskService(real_project).require(tid).status is TaskState.READY


def test_un_choix_multiple_se_repond_avec_tout_cocher_puis_une_case_decochee(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_old(service)
    tid = _running(service, "Icônes")
    service.ask(
        tid, "", "agent-a",
        title="Accorder l'exception Lucide pour ces icônes ?",
        context="Il manque des icônes d'action au sprite maison.",
        choices=tuple(Choice(name, name) for name in ICONS),
        answer_mode="multi",
    )
    _open_decisions(workspace)
    card = _card(workspace, tid)
    options = card.locator("[data-role='answer-option']")
    expect(options).to_have_count(len(ICONS))
    send = card.get_by_role("button", name="Envoyer")
    expect(send).to_be_disabled()
    card.get_by_role("button", name="Tout cocher").click()
    expect(card.locator("[aria-checked='true'][data-role='answer-option']")).to_have_count(len(ICONS))
    expect(card).to_contain_text(f"{len(ICONS)} sélectionnées sur {len(ICONS)}")
    options.nth(2).click()
    expect(options.nth(2)).to_have_attribute("aria-checked", "false")
    expect(card).to_contain_text(f"{len(ICONS) - 1} sélectionnées sur {len(ICONS)}")
    with workspace.expect_response(lambda r: "/answer" in r.url) as sent:
        send.click()
    body = json.loads(sent.value.request.post_data or "{}")
    expected = [name for i, name in enumerate(ICONS) if i != 2]
    assert body["answer_data"]["choices"] == expected
    assert body["text"] == "Retenu : " + ", ".join(expected)
    assert _answered(TaskService(real_project), tid)["answer_data"]["choices"] == expected


def test_une_tache_qui_redemande_montre_ses_demandes_precedentes(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_old(service)
    tid = _running(service, "Cockpit")
    _reprise(service, tid)
    first = service.require(tid).waiting.question.id  # type: ignore[union-attr]
    service.answer(tid, "reprendre", "guilhem", origin=ORIGIN_HUMAN, question_id=first, answer_data={"choices": ["reprendre"]})
    for state in (TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="agent-a", claim=claim)
    _reprise(service, tid)

    _open_decisions(workspace)
    card = _card(workspace, tid)
    expect(card).to_contain_text("2e demande")
    rounds = card.locator("[data-role='round']")
    expect(rounds).to_have_count(1)
    expect(rounds.first).to_contain_text("1re")
    expect(rounds.first).to_contain_text("Tu as répondu : Reprendre la tâche")
    expect(rounds.first).to_contain_text("Suite :")


def test_captures_de_la_carte_v2(workspace: Page, real_project: Path) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    service = TaskService(real_project)
    _answer_old(service)
    tid = _running(service, "Cockpit")
    _reprise(service, tid)
    multi = _running(service, "Icônes")
    service.ask(
        multi, "", "agent-a", title="Accorder l'exception Lucide pour ces icônes ?",
        context="Il manque des icônes d'action au sprite maison.",
        choices=tuple(Choice(name, name, "Arrive dans le sprite.") for name in ICONS), answer_mode="multi",
    )
    _open_decisions(workspace)
    card, many = _card(workspace, tid), _card(workspace, multi)
    for scheme in ("light", "dark"):
        workspace.emulate_media(color_scheme=scheme)
        card.scroll_into_view_if_needed()
        card.screenshot(path=str(out / f"decision-v2-{'clair' if scheme == 'light' else 'sombre'}.png"))
        many.screenshot(path=str(out / f"decision-v2-multiple-{'clair' if scheme == 'light' else 'sombre'}.png"))
    workspace.emulate_media(color_scheme="light")
    workspace.set_viewport_size({"width": 390, "height": 844})
    for shown, name in ((card, "decision-v2-390.png"), (many, "decision-v2-multiple-390.png")):
        shown.scroll_into_view_if_needed()
        workspace.screenshot(path=str(out / name))
