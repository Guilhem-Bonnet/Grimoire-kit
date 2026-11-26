"""Piloter depuis la vue « Tous les projets » : le pilote d'un projet nommé, et le chat avec une cible choisie.

Un cockpit dédié sert deux projets enrôlés ; le mandat de ``projet-beta`` est accordé, celui d'``projet-alpha`` non. En
portée globale, sans changer de projet : chaque ligne dit son pilote et son mandat, le pilote de beta s'allume derrière
la clé de contrôle (alpha n'est pas touché), et le chat n'écrit à personne tant que la cible n'est pas choisie. Le chat
parle à un faux agent ACP (``tests/fixtures/acp/chat_agent.py``), pas à un banc payant.

Captures : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les écrit ; absent, rien n'est capturé.
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
from playwright.sync_api import Browser, Page, expect

from grimoire.missions import mandate as md
from grimoire.missions.autopilot import AutopilotStore
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN

from .conftest import open_scope_all

AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "chat_agent.py"
FLEET = "[data-panel='pilotage-flotte']"


def _key() -> str:
    md.ensure_control_key()
    return md.control_key_path().read_text(encoding="utf-8").strip()


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
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


@pytest.fixture
def served_globale(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    roots = {
        "alpha": tmp_path_factory.mktemp("globale-a") / "projet-alpha",
        "beta": tmp_path_factory.mktemp("globale-b") / "projet-beta",
    }
    for root in roots.values():
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    resolved = {k: v.resolve() for k, v in roots.items()}
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-globale")
    registry = [{"name": r.name, "slug": r.name, "path": str(r)} for r in resolved.values()]
    (cockpit_home / "registry.json").write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    # Échec fermé : le mandat et le pilote de test ne s'écrivent jamais sous le HOME réel de la personne.
    assert Path.home().resolve().is_relative_to(tmp_path_factory.getbasetemp().resolve().parent), (
        "HOME n'est pas isolé sous le dossier temporaire des tests : rien n'est accordé"
    )
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(resolved["beta"]).grant(request, origin=ORIGIN_HUMAN, via="test")
    agent_log = tmp_path_factory.mktemp("agent-log") / "agent.jsonl"
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["GRIMOIRE_CHAT_AGENT_COMMAND"] = f"{sys.executable} {AGENT} {agent_log}"
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "roots": resolved, "agent_log": agent_log}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def page_globale(browser: Browser, served_globale: dict[str, object]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served_globale['url']}/workspace/index.html?project=projet-alpha", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _row(page: Page, slug: str):  # type: ignore[no-untyped-def]
    return page.locator(f"{FLEET} .pi-row[data-project='{slug}']")


def test_le_pilote_d_un_projet_nomme_s_allume_depuis_la_vue_globale_sans_toucher_aux_autres(
    page_globale: Page, served_globale: dict[str, object]
) -> None:
    page = page_globale
    roots: dict[str, Path] = served_globale["roots"]  # type: ignore[assignment]
    open_scope_all(page, "#flotte")
    page.wait_for_selector(FLEET)
    page.wait_for_selector(f"{FLEET} .pi-row[data-project='projet-beta']")

    expect(_row(page, "projet-beta").locator("[data-role='authority-line']")).to_contain_text("Mandat : actif")
    expect(_row(page, "projet-alpha").locator("[data-role='authority-line']")).to_contain_text("Mandat : inactif")
    # Sans mandat, le pilote d'alpha ne s'allume pas d'ici : bouton désactivé, raison écrite, aucun octroi proposé.
    alpha_button = _row(page, "projet-alpha").locator(".pi-pilot button")
    expect(alpha_button).to_be_disabled()
    expect(_row(page, "projet-alpha").locator(".pi-pilot")).to_contain_text("Aucun mandat actif")
    _capture(page, "pilotage-globale-01-lignes")

    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    _row(page, "projet-beta").locator(".pi-pilot button").click()
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_contain_text("projet-beta")
    _capture(page, "pilotage-globale-02-dialogue")
    dialog.locator("input[type='password']").fill("pas-la-cle")
    dialog.locator("button[type='submit']").click()
    expect(dialog.locator(".pi-error")).to_contain_text("Clé refusée")
    assert not AutopilotStore(roots["beta"]).state().active

    dialog.locator("input[type='password']").fill(_key())
    dialog.locator("button[type='submit']").click()
    expect(_row(page, "projet-beta").locator("[data-role='authority-line']")).to_contain_text("Pilote automatique : activé")
    _capture(page, "pilotage-globale-03-pilote-allume")

    assert AutopilotStore(roots["beta"]).state().active
    assert not AutopilotStore(roots["alpha"]).state().active
    autopilot_posts = [u for u in posts if "autonomy/autopilot" in u]
    assert autopilot_posts and all("project=projet-beta" in u for u in autopilot_posts), autopilot_posts

    _row(page, "projet-beta").locator(".pi-pilot button").click()
    dialog.locator("input[type='password']").fill(_key())
    dialog.locator("button[type='submit']").click()
    expect(_row(page, "projet-beta").locator("[data-role='authority-line']")).to_contain_text("Pilote automatique : coupé")
    assert not AutopilotStore(roots["beta"]).state().active


def test_le_chat_de_la_vue_globale_n_ecrit_a_personne_tant_que_la_cible_n_est_pas_choisie(
    page_globale: Page, served_globale: dict[str, object]
) -> None:
    page = page_globale
    open_scope_all(page, "#flotte")
    chat_requests: list[tuple[str, str]] = []
    page.on("request", lambda r: chat_requests.append((r.method, r.url)) if "/chat/" in r.url else None)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    select = page.locator("select.chat-target")
    expect(select).to_be_visible()
    expect(page.locator(".chat-input")).to_be_disabled()
    expect(page.locator(".chat-status")).to_contain_text("Choisissez le projet")
    _capture(page, "pilotage-globale-04-chat-sans-cible")
    assert [u for _m, u in chat_requests if "project=" not in u] == [] and chat_requests == [], chat_requests

    select.select_option("projet-beta")
    expect(page.locator(".chat-input")).to_be_enabled()
    page.locator(".chat-input").fill("bonjour beta")
    page.locator("button.chat-send").click()
    page.locator(".chat-msg.is-user").filter(has_text="bonjour beta").wait_for()
    page.locator(".chat-msg.is-agent").first.wait_for()
    _capture(page, "pilotage-globale-05-chat-cible-choisie")
    assert chat_requests and all("project=projet-beta" in u for _m, u in chat_requests), chat_requests
    assert any(m == "POST" and "/chat/send" in u for m, u in chat_requests)
