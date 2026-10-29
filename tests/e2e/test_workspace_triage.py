"""« À traiter » comme file de triage (GAO-i5-faire-d-001), dans un vrai navigateur.

Un projet « Atelier Triage » porte huit tâches bloquées dont quatre ont aussi une
consigne en attente de lecture — douze éléments sources pour huit tâches — et
une question à la formulation longue.

- une ligne par tâche : « À débloquer » compte huit lignes, sans doublon ;
- une tuile est un bouton-filtre : un clic ne laisse que sa section et pose
  ``aria-pressed``, Échap ou un second clic remet tout ;
- en 390×844 la première décision tient haut dans la page et sa question
  s'étend sur au moins deux lignes.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, un dernier test y écrit les captures
(clair, sombre, 390 px) ; sans la variable il ne fait rien.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from grimoire.missions.schemas import ORIGIN_HUMAN
from grimoire.missions.service import TaskService

from .conftest import _alive, _free_port, _wait_ready
from .test_workspace_a_traiter_p32 import _grimoire, _init, _task

NAME = "Atelier Triage"
QUESTION_TITLE = "Choisir le format d'export"
QUESTION_TEXT = (
    "Pour l'export des rapports mensuels, préfères-tu un CSV plat, facile à ouvrir dans un tableur, "
    "ou un JSON imbriqué qui garde la hiérarchie des lots et des tâches ?"
)


@pytest.fixture(scope="module")
def served_triage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("triage")
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-triage")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"
    home = base / NAME
    _init(home, NAME, env)
    for index in range(8):
        task = _task(home, f"Tâche bloquée {index + 1}", env)
        blocked = _grimoire(home, "task", "block", task, "--reason", f"attend la CI {index + 1}", env=env)
        if blocked.returncode != 0:
            pytest.skip(f"`grimoire task block` refusé : {blocked.stderr[-400:]}")
        if index < 4:
            # Une consigne ne vient que du canal humain : le CLI d'un agent la refuse.
            TaskService(home).comment(task, "Reprends par le lot B", "guilhem", kind="directive", origin=ORIGIN_HUMAN)
    question = _task(home, QUESTION_TITLE, env)
    asked = _grimoire(home, "task", "ask", question, QUESTION_TEXT, "-o", "CSV", "-o", "JSON", env=env)
    if asked.returncode != 0:
        pytest.skip(f"`grimoire task ask` refusé : {asked.stderr[-400:]}")
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


def _open(browser: Browser, served: dict[str, str], *, width: int = 1440, height: int = 900, scheme: str = "light"):
    context = browser.new_context(
        viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme
    )
    page = context.new_page()
    page.goto(f"{served['url']}/workspace/index.html#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")
    page.wait_for_selector(".at-row")
    return context, page


@pytest.fixture
def page(browser: Browser, served_triage: dict[str, str]) -> Iterator[Page]:
    context, page = _open(browser, served_triage)
    try:
        yield page
    finally:
        context.close()


def test_une_ligne_par_tache(page: Page) -> None:
    section = page.locator(".at-section[data-section='unblock']")
    rows = section.locator(".at-row")
    assert rows.count() == 8
    ids = [row.get_attribute("data-item") for row in rows.all()]
    assert len(set(ids)) == 8
    titles = [row.locator(".at-title").inner_text() for row in rows.all()]
    assert len(set(titles)) == 8
    assert section.locator(".at-section-head .at-n").inner_text() == "8"
    tile = page.locator(".at-tile[data-section='unblock']")
    assert tile.locator(".kpi-val").inner_text() == "8"
    # Les quatre tâches à consigne portent les deux motifs en puces-mots.
    assert sum(1 for row in rows.all() if row.locator(".at-chip").count() == 2) == 4
    assert "consigne" in section.inner_text()
    assert "consigne(s)" not in page.locator("#canvas").inner_text()


def test_tuile_filtre(page: Page) -> None:
    decide = page.locator(".at-tile[data-section='decide']")
    sections = page.locator(".at-section")
    assert sections.count() == 2
    assert decide.get_attribute("aria-pressed") == "false"
    decide.click()
    assert decide.get_attribute("aria-pressed") == "true"
    assert page.locator(".at-section:visible").count() == 1
    assert page.locator(".at-section:visible").get_attribute("data-section") == "decide"
    # Le focus reste sur la tuile ; Échap remet toutes les sections.
    assert page.evaluate("() => document.activeElement.dataset.section") == "decide"
    page.keyboard.press("Escape")
    assert page.locator(".at-section:visible").count() == 2
    assert decide.get_attribute("aria-pressed") == "false"
    decide.click()
    decide.click()
    assert page.locator(".at-section:visible").count() == 2
    assert page.evaluate("() => document.activeElement.dataset.section") == "decide"


def test_mobile_390(browser: Browser, served_triage: dict[str, str]) -> None:
    context, page = _open(browser, served_triage, width=390, height=844)
    try:
        first = page.locator(".at-row").first
        box = first.bounding_box()
        assert box is not None and box["y"] < 360, box
        reason = first.locator(".at-reason")
        lines = reason.evaluate(
            "el => el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)"
        )
        assert lines >= 2, lines
        assert first.locator(".at-reason").inner_text() == QUESTION_TEXT
        tile = page.locator(".at-tile").first.bounding_box()
        assert tile is not None and tile["height"] >= 44
    finally:
        context.close()


def test_captures(browser: Browser, served_triage: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    for name, width, height, scheme in (("clair", 1440, 900, "light"), ("sombre", 1440, 900, "dark"),
                                        ("390", 390, 844, "light")):
        context, page = _open(browser, served_triage, width=width, height=height, scheme=scheme)
        try:
            page.screenshot(path=str(folder / f"a-traiter-triage-{name}.png"))
        finally:
            context.close()
