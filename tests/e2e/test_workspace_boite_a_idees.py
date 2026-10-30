"""I13 : la boîte à idées de Travail, dans un vrai navigateur.

« Garder pour plus tard » écrit ``ideas.jsonl`` ; l'idée survit au rechargement ;
« Transformer en tâches » ouvre l'intake avec son texte et la fait disparaître de
« Idées » à la création ; « Écarter » puis « Annuler » (8 s) ne laisse qu'un
événement de plus au journal. Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les captures clair, sombre et 390 px
sont écrites.
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
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

APPEAR_MS = 30_000
IDEE = "Ajouter une page d'export CSV"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")


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


@pytest.fixture(scope="module")
def served_idea(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("idea") / "fake-home"
    root.mkdir(parents=True)
    forge = root / "forge-idea"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    init = _run([sys.executable, "-m", "grimoire", "init", str(forge), "-y", "--name", "forge-idea", "--backend", "local"],
                root, env_init)
    if not (forge / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    cockpit_home = Path(env["GRIMOIRE_COCKPIT_HOME"])
    cockpit_home.mkdir(parents=True)
    (cockpit_home / "registry.json").write_text(
        json.dumps([{"name": "forge-idea", "path": str(forge.resolve()), "slug": "forge-idea"}]), encoding="utf-8")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "forge": forge, "env": env_init}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def page_idea(browser: Browser, served_idea: dict[str, Any]) -> Iterator[tuple[Page, dict[str, Any]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield page, served_idea
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"


def _journal(srv: dict[str, Any]) -> list[dict[str, Any]]:
    path = srv["forge"] / "_grimoire-runtime-output" / "ledger" / "ideas.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _tasks(srv: dict[str, Any]) -> list[dict[str, Any]]:
    done = _run([sys.executable, "-m", "grimoire", "-o", "json", "task", "list"], srv["forge"], srv["env"])
    assert done.returncode == 0, done.stderr[-800:]
    data = json.loads(done.stdout)
    return list(data["tasks"] if isinstance(data, dict) else data)


def _open(page: Page, srv: dict[str, Any]) -> None:
    page.goto(f"{srv['base']}/workspace/index.html?project=forge-idea#travail", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=APPEAR_MS)


def test_garder_recharger_transformer_en_taches(page_idea: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_idea
    before = {t["id"] for t in _tasks(srv)}
    _open(page, srv)
    page.locator("[data-role='idea-open']").click()
    page.locator("[data-role='idea-text']").fill(IDEE)
    page.locator("[data-role='idea-keep']").click()
    page.locator("[data-role='idea-box-title']").wait_for(timeout=APPEAR_MS)
    assert "Idées (1)" in page.locator("[data-role='idea-box-title']").inner_text()
    assert [e["event"] for e in _journal(srv)] == ["add"]

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator("[data-role='idea-box-title']").wait_for(timeout=APPEAR_MS)
    page.locator("[data-role='idea-box-title']").click()
    entry = page.locator("[data-role='idea-entry']")
    assert entry.count() == 1 and IDEE in entry.inner_text()
    _capture(page, "idees-liste-clair")
    page.evaluate("() => document.querySelector('[data-theme-pref=\"dark\"]').click()")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
    _capture(page, "idees-liste-sombre")
    page.evaluate("() => document.querySelector('[data-theme-pref=\"light\"]').click()")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")

    entry.locator("[data-role='idea-promote']").click()
    items = page.locator("[data-role='idea-proposal']")
    items.first.wait_for(timeout=APPEAR_MS)
    assert page.locator("[data-role='idea-text']").input_value() == IDEE
    page.locator("[data-role='idea-create']").click()
    page.locator("[data-role='idea-done']").wait_for(timeout=APPEAR_MS)
    page.wait_for_function("() => !document.querySelector(\"[data-role='idea-box']\")", timeout=APPEAR_MS)
    created = [t for t in _tasks(srv) if t["id"] not in before]
    assert created
    promote = [e for e in _journal(srv) if e["event"] == "promote"]
    assert len(promote) == 1 and sorted(promote[0]["tasks"]) == sorted(t["id"] for t in created)


def test_ecarter_puis_annuler_ne_laisse_qu_un_evenement(page_idea: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_idea
    start = len(_journal(srv))
    _open(page, srv)
    page.locator("[data-role='idea-open']").click()
    page.locator("[data-role='idea-text']").fill("Penser au tri des couleurs")
    page.locator("[data-role='idea-keep']").click()
    page.locator("[data-role='idea-box-title']").wait_for(timeout=APPEAR_MS)
    page.locator("[data-role='idea-drop']").click()
    page.locator("[data-role='toast-undo']").wait_for(timeout=APPEAR_MS)
    assert page.locator(".toast-card").get_attribute("data-duration") == "8000"
    page.locator("[data-role='toast-undo']").click()
    page.locator("[data-role='idea-entry']").wait_for(timeout=APPEAR_MS)
    events = [e["event"] for e in _journal(srv)][start:]
    assert events == ["add", "drop", "restore"]


def test_mobile_390_cibles_de_44_px(browser: Browser, served_idea: dict[str, Any]) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served_idea['base']}/workspace/index.html?project=forge-idea#travail", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.keyboard.press("n")
        page.locator("[data-role='idea-text']").fill("Une idée pour le mobile")
        page.locator("[data-role='idea-keep']").click()
        page.locator("[data-role='idea-box-title']").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='idea-entry']").first.wait_for(timeout=APPEAR_MS)
        _capture(page, "idees-liste-390")
        for selector in ("[data-role='idea-keep']", "[data-role='idea-promote']", "[data-role='idea-drop']", "[data-role='idea-box-title']"):
            for i in range(page.locator(selector).count()):
                box = page.locator(selector).nth(i).bounding_box()
                assert box is not None and box["height"] >= 43.5, (selector, box)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()
