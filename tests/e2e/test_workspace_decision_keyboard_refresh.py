"""Travail › Décisions : une carte redessinée garde ses raccourcis clavier (#1122).

Le test P93 de la carte v2 échouait par intermittence quand le pouls de la coque
redessinait la vue entre un raccourci de choix (``2``) et la recommandation
(``r``) : ``captureView`` ne reconnaissait que ``.ex-card`` et
``tr[data-task]``, jamais la carte de décision (``[data-role="decision"]``), donc
le focus partait sur ``body`` et ``r`` n'avait plus d'effet.

Ici le rafraîchissement n'est pas simulé dans le DOM : une écriture du ledger
faite HORS de la page fait bouger l'empreinte du pouls, le serveur la signale,
et la vue remplace réellement la carte. Le test vérifie alors que la nouvelle
carte a repris le focus et qu'un appui sur ``r`` sélectionne encore la
recommandation, sans rien envoyer.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le test y écrit une capture de la
carte après rafraîchissement ; sans la variable il ne fait rien.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect

from grimoire.missions.decisions import pending_decisions
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import Choice, Recommendation

#: Large marge : le flux recalcule l'empreinte toutes les 0,5 s, mais un onglet
#: peut se replier sur un sondage (jusqu'à 5 s par défaut) avant le redessin.
REDRAW_MS = 15_000


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
        attachments=(),
        recommendation=Recommendation("reprendre", "Le diff ne touche que des tests.", "Une session neuve repart."),
    )


def _open_decisions(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'decisions' })")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector("[data-role='decisions']")


def _card(page: Page, tid: str):  # type: ignore[no-untyped-def]
    return page.locator(f"[data-role='decision'][data-task='{tid}']")


def _remember_card(page: Page, tid: str, slot: str) -> None:
    """Retient le nœud de la carte avant l'écriture : le redessin doit le remplacer."""
    page.evaluate(
        """([tid, slot]) => {
          window[slot] = document.querySelector(`[data-role="decision"][data-task="${tid}"]`);
        }""",
        [tid, slot],
    )


def _wait_for_redraw(page: Page, tid: str, slot: str) -> None:
    """Attend que le pouls ait remplacé la carte retenue dans `slot`."""
    page.wait_for_function(
        """([tid, slot]) => {
          const now = document.querySelector(`[data-role="decision"][data-task="${tid}"]`);
          return !!now && now !== window[slot];
        }""",
        arg=[tid, slot],
        timeout=REDRAW_MS,
    )


def test_les_raccourcis_survivent_a_un_rafraichissement_du_ledger(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_old(service)
    tid = _running(service, "Cockpit")
    _reprise(service, tid)

    _open_decisions(workspace)
    card = _card(workspace, tid)
    options = card.locator("[data-role='answer-option']")
    expect(options.nth(0)).to_have_attribute("aria-checked", "true")

    # Un utilisateur au clavier : le focus est sur la carte, pas sur un bouton.
    card.focus()
    workspace.keyboard.press("2")
    expect(options.nth(1)).to_have_attribute("aria-checked", "true")

    # Rafraîchissement réel : une écriture du ledger hors de la page déplace
    # l'empreinte du pouls ; la vue redessine et REMPLACE le nœud de la carte.
    _remember_card(workspace, tid, "dcAvant")
    decoy = service.add(f"Témoin de rafraîchissement pour {tid}", ("critère",), actor="t", owner="amelia").task.id
    _wait_for_redraw(workspace, tid, "dcAvant")

    # La carte redessinée a repris le focus clavier ; sans ce focus, « r » part
    # dans le vide et la recommandation n'est plus sélectionnée.
    assert workspace.evaluate("() => document.activeElement?.dataset?.role") == "decision"
    assert workspace.evaluate("(tid) => document.activeElement?.dataset?.task === tid", tid)
    workspace.keyboard.press("r")
    card = _card(workspace, tid)
    expect(card.locator("[data-role='answer-option']").nth(0)).to_have_attribute("aria-checked", "true")
    expect(card.locator("[data-role='answer-option']").nth(1)).to_have_attribute("aria-checked", "false")

    # Couvre aussi le focus d'un contrôle `.dc-*` (un bouton de choix cliqué) :
    # il remonte à la carte qui porte les raccourcis, pas au `body`.
    card.locator("[data-role='answer-option']").nth(1).focus()
    assert workspace.evaluate("() => document.activeElement?.dataset?.value") == "abandonner"
    _remember_card(workspace, tid, "dcAvant2")
    service.ledger.transition_task(decoy, TaskState.READY, actor_id="t")
    _wait_for_redraw(workspace, tid, "dcAvant2")
    assert workspace.evaluate("() => document.activeElement?.dataset?.role") == "decision"
    workspace.keyboard.press("2")
    expect(_card(workspace, tid).locator("[data-role='answer-option']").nth(1)).to_have_attribute("aria-checked", "true")

    # Aucun raccourci n'envoie : la tâche attend toujours sa réponse humaine.
    assert TaskService(real_project).require(tid).status is TaskState.WAITING

    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if target:
        out = Path(target)
        out.mkdir(parents=True, exist_ok=True)
        workspace.screenshot(path=str(out / "decision-apres-rafraichissement.png"), full_page=False)
