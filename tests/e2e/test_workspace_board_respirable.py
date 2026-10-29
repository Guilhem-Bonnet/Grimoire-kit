"""Travail › le tableau respirable (I4, GAO-i4-rendre-l-001).

Avant : « Fait » rendait 111 cartes d'un bloc (~26 000 px), les titres de
colonne disparaissaient au défilement, et à 390 px on voyait une colonne et
demie avec des défilements imbriqués. Chaque test relit le DOM rendu par
``grimoire serve`` sur un projet réel de 111 tâches closes.
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
from playwright.sync_api import Browser, BrowserContext, Page, expect

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""


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



DONE_COUNT = 111


@pytest.fixture(scope="module")
def i4_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("respirable-i4") / "projet-respirable-i4"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-respirable-i4", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet I4 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    # Mise en place seulement : les états sont posés par le ledger, l'objet du
    # test est le board rendu.
    for n in range(25):
        service.add(f"Chantier à faire {n:02d}", ("fait",), owner="kit-dev", actor="t", ready=True)
    for n in range(DONE_COUNT):
        tid = service.add(f"Livraison {n:03d}", ("livré",), owner="claude", actor="t").task.id
        for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION, TaskState.CLOSED):
            claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
            service.ledger.transition_task(tid, state, actor_id="kit-dev", claim=claim)
    return root


@pytest.fixture(scope="module")
def i4_url(i4_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-i4"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(i4_project), "--port", str(port), "--no-open"],
        cwd=str(i4_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


def _open(browser: Browser, url: str, width: int, height: int, scheme: str = "light") -> tuple[BrowserContext, Page]:
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card", timeout=APPEAR_MS)
    return context, page


@pytest.fixture
def page(browser: Browser, i4_url: str) -> Iterator[Page]:
    context, opened = _open(browser, i4_url, 1440, 900)
    try:
        yield opened
    finally:
        context.close()


@pytest.fixture
def mobile(browser: Browser, i4_url: str) -> Iterator[Page]:
    context, opened = _open(browser, i4_url, 390, 844)
    try:
        yield opened
    finally:
        context.close()


def test_fait_replie(page: Page) -> None:
    done = page.locator(".ex-col[data-group='done']")
    assert done.locator(".ex-card").count() <= 6
    toggle = done.locator("[data-role='older-toggle']")
    expect(toggle).to_have_text(f"Voir les {DONE_COUNT - 6} autres")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(done.locator(".ex-older")).to_be_hidden()
    toggle.click()
    toggle = done.locator("[data-role='older-toggle']")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(done.locator(".ex-older-row")).to_have_count(DONE_COUNT - 6)
    assert done.locator(".ex-card").count() <= 6


def test_entetes_collants(page: Page) -> None:
    board = page.locator(".ex-board")
    page.locator(".ex-col[data-group='done'] [data-role='older-toggle']").click()
    assert board.evaluate("(n) => n.scrollHeight - n.clientHeight") > 700, "le board doit pouvoir défiler"
    board.evaluate("(n) => { n.scrollTop = 600; }")
    assert board.evaluate("(n) => n.scrollTop") >= 590
    top = board.bounding_box()["y"]  # type: ignore[index]
    heads = page.locator(".ex-col-head")
    assert heads.count() >= 4
    for i in range(heads.count()):
        y = heads.nth(i).bounding_box()["y"]  # type: ignore[index]
        assert abs(y - top) <= 1, (i, y, top)


def test_mobile_390(mobile: Page) -> None:
    tabs = mobile.locator(".ex-tabs[role='tablist'] [role='tab']")
    assert tabs.count() == 5
    assert mobile.locator(".ex-tabs[role='tablist']").is_visible()

    def visible_columns() -> int:
        return mobile.evaluate(
            """() => [...document.querySelectorAll('.ex-col')].filter((c) => {
                const r = c.getBoundingClientRect();
                return r.left >= -1 && r.right <= window.innerWidth + 1 && r.width > 0;
            }).length"""
        )

    assert visible_columns() == 1
    assert mobile.evaluate("() => document.scrollingElement.scrollWidth") <= 390
    for i in range(tabs.count()):
        box = tabs.nth(i).bounding_box()
        assert box is not None and box["height"] >= 44 and box["width"] >= 44, box
    expect(tabs.nth(0)).to_have_attribute("aria-selected", "true")
    tabs.nth(0).focus()
    mobile.keyboard.press("ArrowRight")
    expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
    expect(tabs.nth(0)).to_have_attribute("aria-selected", "false")
    mobile.wait_for_function("() => document.querySelector('.ex-board').scrollLeft > 100")
    assert visible_columns() == 1
    mobile.keyboard.press("ArrowLeft")
    expect(tabs.nth(0)).to_have_attribute("aria-selected", "true")
    # Défiler à la main change l'onglet actif.
    mobile.evaluate("() => { const b = document.querySelector('.ex-board'); b.scrollLeft = b.clientWidth * 2 + 20; }")
    expect(tabs.nth(2)).to_have_attribute("aria-selected", "true")
    assert mobile.evaluate("() => document.scrollingElement.scrollWidth") <= 390
