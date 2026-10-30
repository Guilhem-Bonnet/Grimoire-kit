"""Preuves › lecture pure et sous la seconde (I9, GAO-i9-rendre-l-001).

Un board de 200 tâches terminées : la touche 3 peint le panneau en moins de
3 s, avec au plus 8 lignes (les terminales sont repliées derrière « Tout voir »).
Les captures (clair, sombre, 390 px) vont dans ``$GRIMOIRE_I9_SHOTS`` si défini.
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
import yaml
from playwright.sync_api import Browser, Page, expect


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
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("preuves-i9") / "projet-preuves-i9"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-preuves-i9", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    path = root / "_grimoire" / "standard" / "task-board.yaml"
    if not path.is_file():
        pytest.fail("`grimoire standard init` indisponible : le board I9 ne peut pas être monté")
    board = yaml.safe_load(path.read_text(encoding="utf-8"))
    board["tasks"] = (
        [{"task_id": f"ACT-{i}", "title": f"Chantier actif {i}", "status": "in_progress"} for i in range(2)]
        + [{"task_id": f"OLD-{i:03d}", "title": f"Livraison {i}", "status": "archived"} for i in range(200)]
    )
    path.write_text(yaml.safe_dump(board, allow_unicode=True), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def url(project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-i9"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(project), "--port", str(port), "--no-open"],
        cwd=str(project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


def _open_panel(browser: Browser, url: str, width: int, height: int, scheme: str) -> tuple[object, Page, float]:
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    # Hors atelier : l'espace de départ masque les panneaux (cf. coque P62).
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    if width > 600:  # le rail n'existe qu'en atelier (⇧⌘L) sur grand écran
        page.keyboard.press("Control+Shift+L")
        page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    start = time.monotonic()
    page.keyboard.press("3")
    page.wait_for_selector("#evidence-body .ev-row", timeout=3_000)
    return context, page, time.monotonic() - start


@pytest.mark.parametrize(("name", "width", "height", "scheme"), [
    ("clair", 1440, 900, "light"), ("sombre", 1440, 900, "dark"), ("390", 390, 844, "light"),
])
def test_touche_3_peint_vite_et_replie(browser: Browser, url: str, name: str, width: int, height: int, scheme: str) -> None:
    context, page, elapsed = _open_panel(browser, url, width, height, scheme)
    try:
        assert elapsed < 3.0, f"{elapsed:.2f} s"
        rows = page.locator("#evidence-body .ev-row")
        assert rows.count() <= 8
        expect(page.locator("#evidence-body .ev-terminal")).to_contain_text("200 terminées")
        shots = os.environ.get("GRIMOIRE_I9_SHOTS")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / f"preuves-{name}.png"))
        page.locator("#evidence-body .ev-terminal").click()
        page.wait_for_function("() => document.querySelectorAll('#evidence-body .ev-row').length > 100")
    finally:
        context.close()  # type: ignore[attr-defined]
