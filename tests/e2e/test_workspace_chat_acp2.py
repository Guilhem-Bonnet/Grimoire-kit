"""ACP2 du cockpit : l'onglet « Orchestrateur » du dock, contre un faux agent ACP.

Le serveur du cockpit lance ``tests/fixtures/acp/chat_agent.py`` (un faux agent interactif, pas un
banc payant) à la place de ``claude-agent-acp`` ; le navigateur est le vrai. Chaque test lit ce que
l'agent a *reçu* dans son journal : c'est la preuve que le refus et le Stop arrivent jusqu'à lui.

Captures (preuve d'interface, thèmes clair et sombre) : ``GRIMOIRE_E2E_CAPTURES=<dossier>``.
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


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


@pytest.fixture(scope="module")
def chat_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("chat-acp2") / "projet-chat"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-chat", "--backend", "local"],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    return root


@pytest.fixture(scope="module")
def agent_log(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("chat-agent") / "agent-log.jsonl"


@pytest.fixture(scope="module")
def chat_url(chat_project: Path, agent_log: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-chat"))
    env["GRIMOIRE_CHAT_AGENT_COMMAND"] = f"{sys.executable} {AGENT} {agent_log}"
    env["GRIMOIRE_CHAT_GRACE_SECONDS"] = (
        "0"  # la page partie, l'agent s'arrête : la réouverture reprend par session/load
    )
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "grimoire",
            "serve",
            "--project-root",
            str(chat_project),
            "--port",
            str(port),
            "--no-open",
        ],
        cwd=str(chat_project),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def _show_chat(page: Page) -> None:
    """Le dock fait partie des panneaux d'atelier (⇧⌘L) : on les ouvre, puis l'onglet Orchestrateur."""
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")


def _open(browser: Browser, url: str, *, scheme: str = "dark") -> tuple[Page, object]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _show_chat(page)
    return page, context


@pytest.fixture
def page(browser: Browser, chat_url: str) -> Iterator[Page]:
    opened, context = _open(browser, chat_url)
    try:
        yield opened
    finally:
        context.close()  # type: ignore[attr-defined]


def _received(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def _say(page: Page, text: str) -> None:
    page.locator(".chat-input").fill(text)
    page.locator(".chat-input").press("Enter")


def _reset(chat_url: str, page: Page, agent_log: Path) -> None:
    """Une conversation neuve et un journal vide : chaque test part de zéro."""
    page.evaluate("() => fetch('/api/workspace/chat/stop', { method: 'POST', body: '{}' })")
    time.sleep(0.2)
    agent_log.unlink(missing_ok=True)


# ── Réponse en flux, puis fin de tour ───────────────────────────────────────


def test_un_message_affiche_la_reponse_en_flux_puis_la_fin_de_tour(page: Page, agent_log: Path) -> None:
    log = page.locator(".chat-log")
    assert log.get_attribute("role") == "log"
    assert log.get_attribute("aria-live") == "polite"

    _say(page, "bonjour")

    page.wait_for_selector(".chat-msg.is-user")
    assert "bonjour" in page.locator(".chat-msg.is-user").first.inner_text()
    agent = page.locator(".chat-msg.is-agent .chat-body")
    page.wait_for_function("() => document.querySelector('.chat-msg.is-agent .chat-body strong')")
    assert "l'orchestrateur" in agent.inner_text()
    assert agent.locator("li").count() == 2  # le markdown est rendu, pas affiché brut
    page.wait_for_function("() => !document.querySelector('.chat-stop') || document.querySelector('.chat-stop').hidden")
    assert page.locator(".chat-input").input_value() == ""
    _capture(page, "acp2-chat-reponse-sombre")


def test_entree_envoie_et_maj_entree_saute_une_ligne(page: Page, agent_log: Path) -> None:
    box = page.locator(".chat-input")
    box.fill("première")
    box.press("Shift+Enter")
    box.type("seconde")
    assert box.input_value() == "première\nseconde"
    assert (
        page.locator(".chat-msg.is-user").count() == 0
        or "seconde" not in page.locator(".chat-msg.is-user").last.inner_text()
    )
    box.press("Enter")
    page.wait_for_function(
        "() => [...document.querySelectorAll('.chat-msg.is-user')].some((n) => n.innerText.includes('seconde'))"
    )


def test_appels_d_outils_diffs_et_plans_se_replient_et_se_lisent(page: Page) -> None:
    _say(page, "outils")
    tool = page.locator("details.chat-tool")
    tool.wait_for()
    page.wait_for_function("() => document.querySelector('.chat-tool-status.is-completed')")
    assert tool.get_attribute("open") is None  # replié par défaut
    tool.locator("summary").click()
    assert tool.get_attribute("open") is not None
    assert "nouveau titre" in tool.locator(".chat-diff .is-add").inner_text()
    assert "ancien titre" in tool.locator(".chat-diff .is-del").inner_text()
    plan = page.locator(".chat-plan")
    assert plan.locator("li").count() == 2
    _capture(page, "acp2-chat-outils-sombre")


# ── Permissions et Stop ─────────────────────────────────────────────────────


def test_une_permission_s_affiche_en_boutons_et_refuser_revient_a_l_agent(page: Page, agent_log: Path) -> None:
    _say(page, "permission")
    perm = page.locator(".chat-perm")
    perm.wait_for()
    assert perm.get_attribute("role") == "group"
    assert "Écrire notes.md" in perm.inner_text()
    labels = [b.inner_text() for b in perm.locator("button").all()]
    assert labels == ["Autoriser une fois", "Toujours", "Refuser"]
    _capture(page, "acp2-chat-permission-sombre")

    perm.get_by_role("button", name="Refuser").click()

    page.wait_for_function("() => document.querySelector('.chat-perm.is-done')")
    assert all(b.is_disabled() for b in perm.locator("button").all())
    page.wait_for_function("() => document.body.innerText.includes('Permission reçue : no')")
    answers = [m for m in _received(agent_log) if m.get("id") == 900]
    assert answers[-1]["result"]["outcome"] == {"outcome": "selected", "optionId": "no"}


def test_stop_envoie_session_cancel_a_l_agent(page: Page, agent_log: Path) -> None:
    before = len([m for m in _received(agent_log) if m.get("method") == "session/cancel"])
    _say(page, "lent")
    stop = page.locator(".chat-stop")
    page.wait_for_function("() => document.querySelector('.chat-stop') && !document.querySelector('.chat-stop').hidden")
    page.wait_for_selector(".chat-msg.is-agent")

    stop.click()

    page.wait_for_function("() => document.body.innerText.includes('Tour arrêté.')")
    cancels = [m for m in _received(agent_log) if m.get("method") == "session/cancel"]
    assert len(cancels) == before + 1
    assert stop.is_hidden()


# ── Reprise ─────────────────────────────────────────────────────────────────


def test_apres_rechargement_la_conversation_est_reprise_par_session_load(
    browser: Browser, chat_url: str, agent_log: Path
) -> None:
    first, context = _open(browser, chat_url)
    try:
        _say(first, "bonjour")
        first.wait_for_selector(".chat-msg.is-agent")
        first.wait_for_function("() => document.querySelector('.chat-stop').hidden")
        loads_before = len([m for m in _received(agent_log) if m.get("method") == "session/load"])

        # La page part ; avec un délai de grâce nul, le serveur arrête l'agent.
        first.goto("about:blank")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with urllib.request.urlopen(f"{chat_url}/api/workspace/chat/state", timeout=5) as reply:  # noqa: S310 — loopback de test
                state = json.loads(reply.read())
            if not state["running"]:
                break
            time.sleep(0.1)
        assert state["running"] is False

        first.goto(f"{chat_url}/workspace/index.html", wait_until="domcontentloaded")
        first.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _show_chat(first)
        first.wait_for_function("() => document.body.innerText.includes('Conversation reprise.')", timeout=15_000)
        assert "Avant la fermeture : bonjour" in first.locator(".chat-msg.is-user").first.inner_text()
        assert "Historique rejoué par l'agent." in first.locator(".chat-log").inner_text()
    finally:
        context.close()  # type: ignore[attr-defined]
    loads = [m for m in _received(agent_log) if m.get("method") == "session/load"]
    assert len(loads) == loads_before + 1 and loads[-1]["params"]["sessionId"] == "chat-sess-1"


# ── Thème clair ─────────────────────────────────────────────────────────────


def test_capture_du_theme_clair(browser: Browser, chat_url: str) -> None:
    page, context = _open(browser, chat_url, scheme="light")
    try:
        page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
        _say(page, "outils")
        page.wait_for_function("() => document.querySelector('.chat-tool-status.is-completed')")
        page.locator("details.chat-tool summary").click()
        _capture(page, "acp2-chat-outils-clair")
        _say(page, "permission")
        page.wait_for_selector(".chat-perm:not(.is-done)")
        _capture(page, "acp2-chat-permission-clair")
        page.get_by_role("button", name="Refuser").last.click()
        page.wait_for_function("() => document.querySelector('.chat-perm.is-done')")
    finally:
        context.close()  # type: ignore[attr-defined]
