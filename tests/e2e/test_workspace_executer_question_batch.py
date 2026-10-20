"""Espace Exécuter — un lot de questions se tranche depuis la vue Décisions (W6-07, issue #564).

Deux clarifications posées en un lot font **une** carte, avec une réponse par
question. Répondre à l'une laisse la tâche en attente et la carte ne montre plus
que l'autre ; répondre à la dernière rend la tâche (``ready``). Chaque réponse
part en ``api.taskAction(id, 'answer', {question_id})`` : la page ne touche
jamais le ledger, ce qu'elle affiche ensuite est relu du serveur.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, expect

from grimoire.missions.questions import QuestionSpec
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService

TITRE = "Lot de questions depuis Exécuter"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _tache_en_attente(real_project: Path) -> tuple[str, str, str]:
    service = TaskService(real_project)
    tid = service.add(TITRE, ("les deux questions sont tranchées",), owner="amelia", actor="t").task.id
    # Mise en place seulement : le projet de la suite est enrôlé (gates de preuve, registre de fournisseurs
    # vide) ; la tâche est amenée en `running` par le ledger, l'objet du test est la vue Décisions.
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="agent-a", claim=claim)
    asked = service.ask_batch(tid, [
        QuestionSpec("Maquette ou peaufiné ?", "choice", ("maquette", "peaufiné"), why="finition indéterminable"),
        QuestionSpec("Supprimer users_v1 ?", "permission", ("supprimer", "garder"), why="arbitrage irréversible"),
    ], "agent-a").plan.asked
    return tid, asked[0].id, asked[1].id


def test_un_lot_de_questions_se_tranche_question_par_question(workspace: Page, real_project: Path) -> None:
    tid, first, second = _tache_en_attente(real_project)
    _goto(workspace, "executer")
    workspace.get_by_role("button", name="Voir les décisions").click()
    card = workspace.locator(f"[data-role='decision'][data-task='{tid}']")
    expect(card.locator("[data-role='decision-question']")).to_have_count(2)
    expect(card).to_contain_text("2 questions en un lot")
    expect(card).to_contain_text("pourquoi : arbitrage irréversible")

    second_box = card.locator(f"[data-role='decision-question'][data-question='{second}']")
    with workspace.expect_response(lambda r: "/answer" in r.url) as answered:
        second_box.get_by_role("button", name="garder").click()
    assert answered.value.ok
    assert TaskService(real_project).require(tid).status is TaskState.WAITING
    expect(card.locator("[data-role='decision-question']")).to_have_count(0)  # une seule question : carte simple
    expect(card).to_contain_text("Maquette ou peaufiné ?")
    expect(card).not_to_contain_text("Supprimer users_v1 ?")

    with workspace.expect_response(lambda r: "/answer" in r.url) as answered:
        card.get_by_role("button", name="maquette").click()
    assert answered.value.ok
    task = TaskService(real_project).require(tid)
    assert task.status is TaskState.READY
    assert {r.id: r.answer for r in task.questions} == {first: "maquette", second: "garder"}
