"""Travail › bloc Activité (P69v2, GAO-p69v2-bloc-a-001).

Le fil de l'agent dérivé du ledger, sur un projet dédié où une session vivante
tient une tâche (claim + ``task.session_attached`` posés par le même
``TaskService`` que l'interface, journal de session frais pour la vivacité) :

- l'inspecteur de la tâche rend le fil (question, progression, transitions)
  via ``GET /api/workspace/tasks/<id>/feed`` — le bloc partagé ``activity.js`` ;
- la carte de la tâche porte la dernière entrée du fil, en une ligne ;
- « Copier claude logs » copie la commande du contrat session — presse-papiers
  stubbé AVANT le clic (pattern déterministe de ``test_workspace_decisions_p68``) ;
- l'inspecteur de la session (Travail › Sessions) rend le même bloc.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

HELD_TITLE = "Brancher le fil d'activité"
SESSION = "41c89d23-0000-0000-0000-00000000e2e0"
NOTE = "plan arrêté, tests e2e en cours"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


@pytest.fixture(scope="module")
def p69_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("activite-p69") / "projet-activite-p69"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-activite-p69", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P69 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    held = service.add(HELD_TITLE, ("le fil se lit",), owner="guilhem", actor="t", ready=True).task.id
    service.ledger.transition_task(
        held, TaskState.CLAIMED, actor_id="winston", claim=TaskClaim("winston", "local")
    )
    service.ledger.attach_session(held, SESSION, session_host="claude-code-cli", actor_id="hook")
    service.ledger.transition_task(held, TaskState.RUNNING, actor_id="winston")
    service.ledger.note_progress(held, NOTE, actor_id="winston")
    # Pas de question ici : elle garerait la tâche en `waiting`, donc la carte
    # dans la section Bloquées repliée (P72v2) — le fil question/réponse est
    # couvert par les tests unitaires du module.

    # Un journal de session frais : la vivacité (liveness) voit la session vivante,
    # la carte porte alors la dernière entrée du fil.
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    (runs / f"session-{SESSION}.json").write_text(json.dumps({
        "session_id": SESSION, "task_id": held, "host": "claude-code-cli",
        "started_at": now, "updated_at": now,
    }), encoding="utf-8")
    return root


def _task_id(root: Path) -> str:
    from grimoire.missions.service import TaskService

    return next(t.id for t in TaskService(root).ledger.list_tasks() if t.title == HELD_TITLE)


@pytest.fixture(scope="module")
def p69_url(p69_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p69"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(p69_project), "--port", str(port), "--no-open"],
        cwd=str(p69_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def page(browser: Browser, p69_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p69_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    opened.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    opened.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    # Piège connu : wait_for_selector('.ex-card') ne regarde que le premier
    # nœud du DOM ; on attend une carte VISIBLE.
    opened.wait_for_selector(".ex-card:visible", timeout=APPEAR_MS)
    try:
        yield opened
    finally:
        context.close()


def test_la_carte_porte_la_derniere_entree_du_fil_en_une_ligne(page: Page) -> None:
    card = page.locator(".ex-card:visible").filter(has_text="Brancher le fil").first
    line = card.locator("[data-role='feed-last']")
    line.wait_for(timeout=APPEAR_MS)
    # La dernière entrée du fil : la note de progression.
    assert NOTE in line.inner_text(), line.inner_text()
    # Une seule ligne : le texte entier reste au survol.
    assert "fil d'activité" in (line.get_attribute("title") or "")


def test_l_inspecteur_de_tache_rend_le_fil_et_copie_claude_logs(page: Page, p69_project: Path) -> None:
    tid = _task_id(p69_project)
    card = page.locator(".ex-card:visible").filter(has_text="Brancher le fil").first
    with page.expect_response(lambda r: f"/tasks/{tid}/feed" in r.url) as feed_resp:
        card.dispatch_event("click")
    assert feed_resp.value.ok
    feed = feed_resp.value.json()
    assert feed["session"]["logs_command"] == f"claude logs {SESSION}"

    inspector = page.locator("#inspector-body")
    block = inspector.locator("[data-role='activity-block']")
    block.wait_for(timeout=APPEAR_MS)
    rows = block.locator("[data-role='activity-feed'] li")
    assert rows.count() >= 4  # créée, prête, réclamée, rattachée, en cours, progression
    body = block.inner_text()
    assert f"progression : {NOTE}" in body
    assert "session 41c89d23 rattachée au claim" in body

    # « Copier claude logs » : stub AVANT le clic (pattern P68), le contrat
    # testé est ce que l'UI a demandé de copier et le statut qui le dit.
    page.evaluate(
        "() => { window.__copied = []; navigator.clipboard.writeText ="
        " (t) => { window.__copied.push(t); return Promise.resolve(); }; }"
    )
    block.get_by_role("button", name="Copier claude logs").click()
    expect(block.locator(".action-status")).to_contain_text("Commande copiée")
    copied = page.evaluate("() => window.__copied.join('')")
    assert copied == f"claude logs {SESSION}", copied


def test_l_inspecteur_de_session_rend_le_meme_bloc(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'sessions' })")
    sessions_tab = page.get_by_role("tab", name="Sessions")
    if sessions_tab.count():
        sessions_tab.first.click()
    row = page.locator(f"[data-session='{SESSION}']").first
    if not row.count():
        # La vue Sessions liste les sessions par ligne : on clique celle du projet.
        row = page.get_by_text("41c89d23", exact=False).first
    row.wait_for(timeout=APPEAR_MS)
    row.click()
    holder = page.locator("[data-role='session-activity'] [data-role='activity-block']")
    holder.wait_for(timeout=APPEAR_MS)
    body = holder.inner_text()
    assert f"progression : {NOTE}" in body
    expect(holder.locator("[data-role='logs-command']")).to_have_text(f"claude logs {SESSION}")
