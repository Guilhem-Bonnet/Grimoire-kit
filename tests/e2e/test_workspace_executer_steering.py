"""Espace Exécuter — pilotage humain depuis l'inspecteur (issue #638, lot B).

Sélecteur de priorité, zone de consigne, bouton « Annuler » avec raison
exigée, badge « consigne non lue » sur la carte. Chaque geste part en
``api.taskAction`` vers ``TaskService`` — la webview ne touche jamais le
ledger (ADR-007) ; ce que la page affiche ensuite est relu du serveur.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, expect

from grimoire.missions.service import TaskService

TITRE = "Piloter depuis Exécuter"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _tache(real_project: Path) -> str:
    """Une tâche neuve, ouverte par le CLI comme ``project_with_task`` le fait."""
    service = TaskService(real_project)
    existing = [t for t in service.list_tasks() if t.title == TITRE]
    if existing:
        return existing[0].id
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grimoire",
            "task",
            "add",
            TITRE,
            "-a",
            "la carte se pilote",
            "--owner",
            "amelia",
            "--project-root",
            str(real_project),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(real_project),
    )
    return next(t.id for t in TaskService(real_project).list_tasks() if t.title == TITRE)


def test_executer_priorite_consigne_et_annulation_depuis_l_inspecteur(workspace: Page, real_project: Path) -> None:
    task_id = _tache(real_project)
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card")
    inspector = workspace.locator("#inspector-body")
    workspace.locator(".ex-card").filter(has_text=TITRE).first.click()
    inspector.get_by_text(task_id, exact=True).wait_for()

    # Priorité : le sélecteur montre la valeur effective, la changer écrit au ledger.
    select = inspector.locator("select[data-role='priority']")
    assert select.input_value() == "medium"
    select.select_option("critical")
    workspace.wait_for_function(
        "(id) => fetch('/api/workspace/tasks/' + id).then(r => r.json()).then(t => t.priority === 'critical')",
        arg=task_id,
    )
    assert TaskService(real_project).require(task_id).priority == "critical"

    # Consigne : posée depuis la zone, elle apparaît « non lue » et la carte porte le badge.
    inspector.locator("textarea[data-role='directive-text']").fill("lis d'abord le test de fait")
    inspector.locator("button[data-role='directive-submit']").click()
    inspector.locator(".ex-directive.unread").filter(has_text="lis d'abord le test de fait").first.wait_for()
    card = workspace.locator(".ex-card").filter(has_text=TITRE).first
    card.locator("[data-role='directive-badge']").wait_for()
    assert "consigne non lue" in card.inner_text()
    assert [d.text for d in TaskService(real_project).require(task_id).directives] == ["lis d'abord le test de fait"]

    # Annuler : inerte sans raison, refusé par le serveur jamais contourné.
    cancel = inspector.locator("button[data-role='cancel-submit']")
    assert cancel.is_disabled()
    inspector.locator("input[data-role='cancel-reason']").fill("doublon d'une autre carte")
    assert cancel.is_enabled()
    cancel.click()
    workspace.wait_for_function(
        "(id) => fetch('/api/workspace/tasks/' + id).then(r => r.json()).then(t => t.status === 'cancelled')",
        arg=task_id,
    )
    task = TaskService(real_project).require(task_id)
    if task.status.value != "cancelled":
        import sys
        from grimoire.core.standard_state import LEDGER_RELPATH

        events_path = real_project.resolve() / LEDGER_RELPATH / "events.jsonl"
        lock_path = events_path.with_name(f".{events_path.name}.lock")
        print(f"[DBG] events_path={events_path} exists={events_path.exists()}", file=sys.stderr)
        print(f"[DBG] lock_path={lock_path} exists={lock_path.exists()}", file=sys.stderr)
        raw = events_path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        print(f"[DBG] total_lines={len(lines)}", file=sys.stderr)
        matching = [i for i, ln in enumerate(lines) if task_id in ln]
        print(f"[DBG] lines mentioning {task_id}: {matching}", file=sys.stderr)
        for i in matching:
            print(f"[DBG] line {i}: {lines[i][:400]}", file=sys.stderr)
        print(f"[DBG] last 3 lines overall:", file=sys.stderr)
        for ln in lines[-3:]:
            print(f"[DBG]   {ln[:400]}", file=sys.stderr)
    assert task.status.value == "cancelled"
    # Une tâche annulée n'offre plus le bloc « Annuler » — colonne terminale ;
    # `expect` attend le réaffichage de l'inspecteur qui suit l'écriture.
    expect(inspector.locator("button[data-role='cancel-submit']")).to_have_count(0)
    expect(inspector.locator("select[data-role='priority']")).to_have_value("critical")
