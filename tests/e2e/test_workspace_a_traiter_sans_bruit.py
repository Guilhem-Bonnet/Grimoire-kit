"""« À traiter » sans bruit (I10), dans un vrai navigateur, sur un vrai cockpit.

Le projet porte une question (une décision), une tâche bloquée et six tâches
closes qui gardent chacune une consigne non lue. Seul ce qui demande un geste
compte : le badge du rail et le titre de l'onglet disent 2, les consignes des
tâches closes ne sont nulle part, et une section vide n'est pas rendue.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test y écrit les captures
(clair, sombre, 390 px) pour la relecture ; sans la variable il ne fait rien.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService

from .conftest import _alive, _free_port, _wait_ready
from .test_workspace_a_traiter_p32 import _block, _grimoire, _init, _task

NAME = "Atelier Sobre"
CLOSED = 6


@pytest.fixture(scope="module")
def served_quiet(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("a-traiter-sans-bruit")
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-sans-bruit")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"

    home = base / NAME
    _init(home, NAME, env)
    question = _task(home, "Choisir le format d'export", env)
    asked = _grimoire(home, "task", "ask", question, "CSV ou JSON ?", "-o", "CSV", "-o", "JSON", env=env)
    if asked.returncode != 0:
        pytest.skip(f"`grimoire task ask` refusé : {asked.stderr[-400:]}")
    _block(home, _task(home, "Brancher la CI", env), "la CI est rouge", env)

    service = TaskService(home)
    for n in range(CLOSED):
        tid = _task(home, f"Tâche finie numéro {n}", env)
        for text in ("première", "seconde"):
            service.comment(tid, text, "guilhem", kind="directive", origin=ORIGIN_HUMAN)
        # Le ledger brut : la clôture du service acquitterait déjà les consignes, et c'est le cas du
        # ledger hérité, écrit avant ce correctif, que l'écran doit aussi savoir taire.
        for state in (TaskState.RUNNING, TaskState.NEEDS_VERIFICATION, TaskState.CLOSED):
            service.ledger.transition_task(tid, state, actor_id="t")

    _grimoire(base, "cockpit", "add", str(home), env=env)
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--project-root", str(home),
         "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(home), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}"}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


def _open(page: Page, served: dict[str, str]) -> None:
    page.goto(f"{served['url']}/workspace/index.html#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def test_l_api_ne_compte_que_les_gestes(served_quiet: dict[str, str]) -> None:
    with urllib.request.urlopen(f"{served_quiet['url']}/api/workspace/attention", timeout=30)  # noqa: S310 — http local as response:
        data = json.load(response)
    assert data["count"] == 2
    assert data["counts"].get("directive_unacked", 0) == 0
    assert data["maintainCount"] == 0


def test_titre_badge_et_sections(page: Page, served_quiet: dict[str, str]) -> None:
    _open(page, served_quiet)
    page.wait_for_function("() => document.title.startsWith('(2)')")
    assert page.title().startswith("(2)")
    assert page.locator("#decisions-count").inner_text() == "2"
    sections = [page.locator(".at-section").nth(i).get_attribute("data-section") for i in range(page.locator(".at-section").count())]
    assert sections == ["decide", "unblock"]
    assert page.locator(".at-row[data-kind='directive_unacked']").count() == 0
    assert re.search(r"1 décision, 1 blocage", page.locator("#canvas").inner_text())


def test_captures_de_relecture(browser: Browser, served_quiet: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for name, viewport, scheme in (
        ("sans-bruit-clair", {"width": 1440, "height": 900}, "light"),
        ("sans-bruit-sombre", {"width": 1440, "height": 900}, "dark"),
        ("sans-bruit-390", {"width": 390, "height": 844}, "light"),
    ):
        context = browser.new_context(viewport=viewport, color_scheme=scheme, reduced_motion="reduce")
        page = context.new_page()
        try:
            _open(page, served_quiet)
            page.screenshot(path=str(out / f"{name}.png"))
        finally:
            context.close()
