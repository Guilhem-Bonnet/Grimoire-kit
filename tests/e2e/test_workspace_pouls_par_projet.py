"""I11 : le cockpit ne relit que le projet qui a bougé.

Deux projets A et B au registre. Le pouls en portée `portfolio` porte une empreinte par
projet ; un espace en portée `project` (À traiter, Exécuter…) ne recharge que si le
projet affiché a bougé, un espace en portée `portfolio` (Flotte) recharge toujours.
"""

from __future__ import annotations

import json
import os
import re
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

INTERVAL_S = 1
READS = re.compile(r"/api/workspace/(tasks|attention|sessions)\b")


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


def _touch_session(root: Path, n: int) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "session-x.json").write_text(json.dumps({"session_id": "x", "n": n}), encoding="utf-8")


@pytest.fixture(scope="module")
def deux_projets(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, Path]]:
    from grimoire.missions.service import TaskService

    base = tmp_path_factory.mktemp("pouls-par-projet")
    roots = {}
    for slug in ("projet-a", "projet-b"):
        root = base / slug
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        TaskService(root).add(f"Carte de {slug}", ("critère",), actor="t", owner="winston", ready=True)
        roots[slug] = root
    home = base / "cockpit-home"
    home.mkdir()
    (home / "registry.json").write_text(
        json.dumps([{"slug": s, "name": s, "path": str(r)} for s, r in roots.items()]), encoding="utf-8"
    )
    port = _free_port()
    env = dict(os.environ)
    env.update(GRIMOIRE_COCKPIT_HOME=str(home), GRIMOIRE_NO_COCKPIT="1", GRIMOIRE_PULSE_SECONDS=str(INTERVAL_S), NO_COLOR="1")
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", roots["projet-a"], roots["projet-b"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _open(browser: Browser, url: str, query: str) -> Page:
    page = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce").new_page()
    page.goto(f"{url}/workspace/index.html{query}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_timeout(2500)  # premier pouls reçu, premier dessin fini
    return page


def _watch(page: Page) -> list[str]:
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if READS.search(r.url) else None)
    return seen


def test_un_projet_qui_n_est_pas_affiche_ne_declenche_aucune_relecture(
    browser: Browser, deux_projets: tuple[str, Path, Path]
) -> None:
    url, a, b = deux_projets
    page = _open(browser, url, "?project=projet-a#travail")
    seen = _watch(page)
    for n in range(10):
        _touch_session(b, n)
        page.wait_for_timeout(1000)
    assert [u for u in seen if "project=projet-a" in u] == [], seen

    seen.clear()
    _touch_session(a, 1)
    page.wait_for_timeout(8000)
    mine = [u for u in seen if "project=projet-a" in u]
    for route in ("tasks", "attention", "sessions"):
        assert len([u for u in mine if f"/api/workspace/{route}" in u]) == 1, (route, mine)


def test_la_flotte_recharge_toujours_quand_un_projet_bouge(
    browser: Browser, deux_projets: tuple[str, Path, Path]
) -> None:
    url, _a, b = deux_projets
    page = _open(browser, url, "?scope=all#flotte")
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "/api/" in r.url and "pulse" not in r.url and "stream" not in r.url else None)
    _touch_session(b, 100)
    page.wait_for_timeout(8000)
    assert seen, "la Flotte n'a rien relu alors qu'un projet du registre a bougé"


def test_l_ecart_entre_deux_relectures_suit_la_duree_du_dernier(
    browser: Browser, deux_projets: tuple[str, Path, Path]
) -> None:
    url, _a, _b = deux_projets
    page = _open(browser, url, "?project=projet-a#travail")
    gaps = page.evaluate(
        "async () => { const m = await import('/workspace/live.js');"
        " return [m.reloadGap(4000), m.reloadGap(200), m.reloadGap(60000)]; }"
    )
    assert gaps == [8000, 3000, 15000]
