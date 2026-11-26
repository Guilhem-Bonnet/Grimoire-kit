"""Le chat ne démarre jamais sur un hôte mort : faux exécutables d'hôtes sur un ``PATH`` maîtrisé.

Deux postes simulés. Le premier : ``copilot`` existe (comme le raccourci de VS Code) mais ne parle pas ACP, et
``opencode`` est un faux agent ACP vivant (``tests/fixtures/acp/chat_agent.py``) : le défaut du sélecteur est
``opencode``, ``copilot`` est grisé avec sa raison, et un choix explicite du hôte mort échoue dans le fil avec la cause et le
geste de réparation. Le second : aucun hôte, le chat le dit en clair avec la commande à lancer et ferme la saisie.

Captures : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` ; absent, rien n'est capturé.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.hosts import acp_hosts

from .conftest import throttle_cpu

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


def _exe(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


Serve = Callable[[bool], str]


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[Serve]:
    """Démarre un cockpit sur un PATH de faux hôtes ; ``alive`` ajoute un ``opencode`` qui parle ACP."""
    started: list[subprocess.Popen[bytes]] = []

    def start(alive: bool) -> str:
        bins = tmp_path / "bin"
        bins.mkdir(exist_ok=True)
        # `copilot` existe mais ne parle pas ACP : le raccourci de VS Code répond « non installé » et sort.
        _exe(bins, "copilot", 'echo "Cannot find GitHub Copilot CLI" >&2\nexit 1')
        if alive:
            _exe(bins, "opencode", f'exec "{sys.executable}" "{AGENT}" "{tmp_path / "agent.jsonl"}"')
        else:
            (bins / "opencode").unlink(missing_ok=True)
        root = tmp_path / "projet-hote"
        root.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        (root / "project-context.yaml").write_text(
            'project:\n  name: "projet-hote"\nhosts:\n  enabled: ["copilot", "opencode"]\n', encoding="utf-8"
        )
        env = dict(os.environ)
        env["PATH"] = f"{bins}:/usr/bin:/bin"  # aucun hôte réel du poste ne doit apparaître
        for name in ("GRIMOIRE_CHAT_AGENT_COMMAND", "GRIMOIRE_CHAT_HOST"):
            env.pop(name, None)
        env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path / "ck")
        env["GRIMOIRE_CHAT_GRACE_SECONDS"] = "0"
        env["NO_COLOR"] = "1"
        port = _free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
            cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        started.append(process)
        _wait_ready(port, time.monotonic() + 60)
        return f"http://127.0.0.1:{port}"

    try:
        yield start
    finally:
        for process in started:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _open_chat(browser: Browser, url: str) -> tuple[Page, object]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="dark")
    page = context.new_page()
    throttle_cpu(page)
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context


def _options(page: Page) -> dict[str, dict[str, object]]:
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('#chat-host option')].map((o) => [
            o.value, { text: o.textContent, disabled: o.disabled, selected: o.selected }]))"""
    )


def test_un_hote_mort_detecte_n_est_pas_le_defaut_et_l_hote_vivant_repond(browser: Browser, serve: Serve) -> None:
    page, context = _open_chat(browser, serve(True))
    try:
        page.wait_for_function("() => document.querySelectorAll('#chat-host option').length > 3", timeout=60_000)
        options = _options(page)
        assert options["opencode"]["selected"] and "détecté" in str(options["opencode"]["text"])
        assert not options["opencode"]["disabled"]
        assert options["copilot"]["disabled"] and "ne répond pas" in str(options["copilot"]["text"])
        assert not options["copilot"]["selected"]
        _capture(page, "chat-hote-01-defaut-vivant")

        page.locator(".chat-input").fill("bonjour")
        page.locator(".chat-input").press("Enter")
        page.locator(".chat-msg.is-agent").first.wait_for(timeout=60_000)
        assert "orchestrateur" in page.locator(".chat-msg.is-agent").first.inner_text()
    finally:
        context.close()  # type: ignore[attr-defined]


def test_un_hote_mort_choisi_explicitement_echoue_dans_le_fil_avec_la_cause_et_la_reparation(
    browser: Browser, serve: Serve, tmp_path: Path
) -> None:
    url = serve(True)
    page, context = _open_chat(browser, url)
    try:
        page.wait_for_function("() => document.querySelectorAll('#chat-host option').length > 3", timeout=60_000)
        # Le sélecteur grise l'hôte mort ; un réglage déjà posé (le fichier de réglage du projet) reste respecté.
        setting = tmp_path / "projet-hote" / acp_hosts.SETTING_RELPATH
        setting.parent.mkdir(parents=True, exist_ok=True)
        setting.write_text('{"host": "copilot"}\n', encoding="utf-8")
        page.locator(".chat-input").fill("bonjour")
        page.locator(".chat-input").press("Enter")

        note = page.locator(".chat-note.is-error").filter(has_text="npm install -g @github/copilot")
        note.first.wait_for(timeout=60_000)
        text = note.first.inner_text()
        assert "sélecteur « Hôte »" in text
        expect(page.locator(".chat")).to_have_attribute("aria-busy", "false")
        _capture(page, "chat-hote-02-echec-demarrage")
    finally:
        context.close()  # type: ignore[attr-defined]


def test_aucun_hote_utilisable_le_chat_le_dit_avec_la_commande_et_ferme_la_saisie(browser: Browser, serve: Serve) -> None:
    page, context = _open_chat(browser, serve(False))
    try:
        status = page.locator(".chat-status")
        expect(status).to_contain_text("Aucun hôte d'agent n'est utilisable", timeout=60_000)
        expect(status).to_contain_text("npm install -g")
        expect(page.locator(".chat-input")).to_be_disabled()
        expect(page.locator("button.chat-send")).to_be_disabled()
        options = _options(page)
        assert not any(o["selected"] and o["disabled"] for o in options.values())
        assert "Aucun hôte utilisable" in str(options[""]["text"])
        assert not options["kanban"]["disabled"], "le mode différé reste choisissable"
        _capture(page, "chat-hote-03-aucun-hote")

        page.locator("#chat-host").select_option("kanban")
        expect(page.locator(".chat-input")).to_be_enabled(timeout=30_000)
    finally:
        context.close()  # type: ignore[attr-defined]
