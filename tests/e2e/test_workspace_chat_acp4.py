"""ACP4 du cockpit : le pilote automatique dans le chat de l'orchestrateur, navigateur réel, faux agent ACP.

Le projet est semé avant le serveur : mandat accordé, pilote activé, une tâche qui attend une réponse réservée
à l'humain (un mot de passe) et une décision déjà prise par le pilote.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "chat_agent.py"


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
def pilot_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, str]]]:
    base = tmp_path_factory.mktemp("chat-acp4")
    root = base / "projet-pilote"
    root.mkdir()
    home = base / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), NO_COLOR="1")
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(var, None)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-pilote", "--backend", "local"],
        cwd=str(root), env=env, check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    seed = (
        "from pathlib import Path\n"
        "from grimoire.missions.autopilot import AutopilotStore, DECIDED_EVENT\n"
        "from grimoire.missions.mandate import MandateRequest, MandateStore\n"
        "from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState\n"
        "from grimoire.missions.service import TaskService\n"
        "root = Path('.').resolve()\n"
        "MandateStore(root).grant(MandateRequest.build(('answer','resume'),('implementation',),10,'+1d'),"
        " origin=ORIGIN_HUMAN, via='test')\n"
        "AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via='test')\n"
        "s = TaskService(root)\n"
        "def mk(title, q):\n"
        "    tid = s.add(title, ('ok',), owner='w', actor='w', ready=True).task.id\n"
        "    s.context(tid); s.claim(tid, 'w'); s.transition(tid, TaskState.RUNNING, 'w'); s.ask(tid, q, 'w')\n"
        "    return tid\n"
        "done = mk('Déjà tranchée', 'Quelle voie ?')\n"
        "s.ledger.append_event(DECIDED_EVENT, done, 'task', 'orch', {'task_id': done, 'question': 'Quelle voie ?',"
        " 'option': 'voie A', 'justification': 'recommandée', 'confidence': 'high'})\n"
        "mk('Serveur', 'Quel mot de passe utiliser pour le serveur ?')\n"
    )
    subprocess.run([sys.executable, "-c", seed], cwd=str(root), env=env, check=True, capture_output=True, timeout=120)
    yield root, env


@pytest.fixture(scope="module")
def pilot_url(pilot_project: tuple[Path, dict[str, str]], tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root, env = pilot_project
    port = _free_port()
    env = dict(
        env,
        GRIMOIRE_COCKPIT_HOME=str(tmp_path_factory.mktemp("cockpit-home-acp4")),
        GRIMOIRE_CHAT_AGENT_COMMAND=f"{sys.executable} {AGENT} {tmp_path_factory.mktemp('agent') / 'log.jsonl'}",
        GRIMOIRE_CHAT_GRACE_SECONDS="0",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


def _open(browser: Browser, url: str) -> tuple[Page, object]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context


def test_l_ouverture_du_chat_resume_les_decisions_du_pilote(browser: Browser, pilot_url: str) -> None:
    page, context = _open(browser, pilot_url)
    try:
        summary = page.locator(".chat-pilot")
        summary.wait_for(timeout=15_000)
        text = summary.inner_text()
        assert "1 décision prise" in text and "voie A" in text
        assert summary.locator("a.chat-pilot-link").get_attribute("href") == "#journal"
    finally:
        context.close()  # type: ignore[attr-defined]


def test_une_question_reservee_apparait_et_la_reponse_tapee_la_tranche(
    browser: Browser, pilot_url: str, pilot_project: tuple[Path, dict[str, str]]
) -> None:
    page, context = _open(browser, pilot_url)
    try:
        box = page.locator(".chat-question")
        box.wait_for(timeout=15_000)
        assert "mot de passe" in box.inner_text()
        box.get_by_role("button", name="Répondre ici").click()
        page.locator(".chat-input").fill("utilise le coffre du projet")
        page.locator(".chat-input").press("Enter")
        page.wait_for_function("() => document.querySelector('.chat-question.is-done')", timeout=15_000)
        from grimoire.missions.service import TaskService

        service = TaskService(pilot_project[0])
        answers = [
            e for t in service.list_tasks() for e in service.ledger.events_for(t.id)
            if e.event_type == "task.human_answered"
        ]
        assert [(e.payload["origin"], e.payload["answer"]) for e in answers] == [("human", "utilise le coffre du projet")]
    finally:
        context.close()  # type: ignore[attr-defined]


def test_l_agent_propose_un_geste_et_la_personne_clique(browser: Browser, pilot_url: str) -> None:
    page, context = _open(browser, pilot_url)
    try:
        page.locator(".chat-input").fill("geste")
        page.locator(".chat-input").press("Enter")
        button = page.locator(".chat-gesture-btn")
        button.wait_for(timeout=15_000)
        assert "[[pilote" not in page.locator(".chat-log").inner_text()
        assert button.get_attribute("data-pilot") == "on"
        button.click()  # sans clé de contrôle valide : refusé, rien n'est activé ni coupé
        page.wait_for_function("() => document.querySelector('.chat-gesture-result').innerText.includes('refusé')")
    finally:
        context.close()  # type: ignore[attr-defined]
