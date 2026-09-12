"""Suggestions par un petit modèle local dans l'éditeur Source (#280, voie 2).

Un projet dédié, pas ``real_project`` : l'assistance a besoin de son propre
``OLLAMA_HOST`` (un faux serveur HTTP local, jamais le vrai Ollama de la
machine) et de son propre opt-in (``project-context.yaml:
source.assist.model``) — deux réglages que ``real_project`` ne porte pas et
que le partager avec les autres modules e2e de Source rendrait fragile.

Ce que prouve ce module, au clavier et à la souris : sans opt-in ni Ollama,
aucun bouton « Suggérer » n'apparaît (rien montré, rien tenté) ; avec les
deux, le bouton ouvre un panneau d'aperçu dont « Insérer » place le texte
dans l'éditeur au même chemin que la frappe clavier — diagnostics recalculés
compris.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

# Dupliqués de `conftest.py` plutôt qu'importés : `from tests.e2e.conftest
# import ...` résout en local (`python -m pytest` insère le cwd dans
# `sys.path`) mais casse sous l'invocation nue de la CI (`pytest tests/e2e`,
# sans `tests/__init__.py` à la racine — `ModuleNotFoundError: No module
# named 'tests'`). Trois fonctions de quelques lignes chacune ; les dupliquer
# coûte moins qu'un import fragile qui ne se voit qu'en CI.


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists() if sys.platform == "linux" else True


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


#: Le modèle « présent » côté faux Ollama — cité tel quel dans project-context.yaml.
_MODEL = "demo-coder:1b"

#: La suggestion canonique rendue par `/api/generate` : un chemin mort, pour
#: prouver après « Insérer » que les diagnostics se recalculent bien sur le
#: résultat (même critère que `test_workspace_source_language.py`).
_SUGGESTION = "Voir _grimoire/kit/ce-fichier-n-existe-pas.md pour la suite."


class _FakeOllamaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        self._send(json.dumps({"models": [{"name": _MODEL}]}).encode("utf-8"))

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._send(json.dumps({"response": _SUGGESTION}).encode("utf-8"))

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture(scope="session")
def fake_ollama_e2e() -> Iterator[http.server.HTTPServer]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture(scope="session")
def served_assist(
    fake_ollama_e2e: http.server.HTTPServer, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """`grimoire serve` sur un projet dédié, opt-in actif, Ollama sondé sur le faux serveur."""
    root = tmp_path_factory.mktemp("assist") / "projet-assist"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-assist"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")

    context_path = root / "project-context.yaml"
    with context_path.open("a", encoding="utf-8") as fh:
        fh.write(f'\nsource:\n  assist:\n    model: "{_MODEL}"\n')

    port = _free_port()
    ollama_port = fake_ollama_e2e.server_address[1]
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-assist"))
    env["NO_COLOR"] = "1"
    env["OLLAMA_HOST"] = f"http://127.0.0.1:{ollama_port}"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "serve",
            "--project-root", str(root), "--port", str(port), "--no-open",
        ],
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
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def assist_page(browser: Browser, served_assist: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served_assist}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('source')")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'source'")
    page.wait_for_selector(".tree .sr-tree-file", timeout=10_000)
    try:
        yield page
    finally:
        context.close()


def _pick_untouched_kit_file(page: Page) -> str:
    tree = page.evaluate("() => fetch('/api/workspace/files?tier=kit').then((r) => r.json())")
    files = tree["tiers"][0]["files"]
    candidate = next(f for f in files if not f["overridden"] and f["path"].endswith(".md"))
    return str(candidate["path"])


def _open_as_override(page: Page) -> str:
    kit_path = _pick_untouched_kit_file(page)
    name = kit_path.rsplit("/", 1)[-1]
    page.locator(".tree .sr-tree-file", has_text=name).first.click()
    page.wait_for_function(
        "(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=kit_path
    )
    page.locator(".sr-banner .btn.pri").click()
    page.wait_for_function(
        "() => document.querySelector('.sr-docrow .mono')?.textContent.includes('_grimoire/overrides/')"
    )
    return page.locator(".sr-docrow .mono").first.inner_text()


def test_le_bouton_suggerer_apparait_avec_opt_in_et_ollama(assist_page: Page) -> None:
    _open_as_override(assist_page)

    assist_page.wait_for_selector(".sr-assist-btn:not([hidden])", timeout=10_000)
    assert "demo-coder" in assist_page.locator(".sr-assist-status").inner_text()


def test_suggerer_affiche_l_apercu_et_inserer_recalcule_les_diagnostics(assist_page: Page) -> None:
    _open_as_override(assist_page)

    textarea = assist_page.locator(".sr-textarea")
    textarea.click()
    assist_page.keyboard.press("Control+End")

    assist_page.locator(".sr-assist-btn").click()
    assist_page.wait_for_selector(".sr-assist-panel:not([hidden])", timeout=10_000)
    assert _SUGGESTION in assist_page.locator(".sr-assist-text").inner_text()

    assist_page.locator(".sr-assist-panel-head .btn.pri", has_text="Insérer").click()
    assist_page.wait_for_function(
        "(s) => document.querySelector('.sr-textarea').value.includes(s)", arg=_SUGGESTION
    )
    # La suggestion insérée cite un chemin mort : les diagnostics doivent se
    # recalculer dessus, exactement comme sur du texte tapé au clavier
    # (`test_workspace_source_language.py::test_un_diagnostic_apparait...`).
    assist_page.wait_for_selector(".sr-gutter-dot.bad", timeout=10_000)
    assert assist_page.locator(".sr-assist-panel[hidden]").count() == 1
