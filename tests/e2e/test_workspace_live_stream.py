"""P10 du cockpit : l'espace ouvert suit le flux du pouls (Server-Sent Events).

Le serveur déclare ici un intervalle de 30 s (``GRIMOIRE_PULSE_SECONDS=30``) : une
mise à jour vue en quelques secondes ne peut venir que du flux, jamais d'un
sondage. Le repli sur le sondage (navigateur sans ``EventSource``) reste prouvé
dans ``test_workspace_live_pilot.py``.

Captures (preuve d'interface) : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les écrit.
"""

from __future__ import annotations

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

# Recopiés de `tests/e2e/conftest.py` plutôt qu'importés : sans `tests/__init__.py`, un
# `from tests.e2e.conftest import …` réexécuterait le conftest sous un second nom de module.


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


INTERVAL_S = 30
#: Bien sous l'intervalle déclaré : ce délai ne laisse aucune place à un sondage.
APPEAR_MS = 6_000


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


@pytest.fixture(scope="module")
def stream_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("live-stream") / "projet-en-direct"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-en-direct", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    TaskService(root).add("Carte de départ du flux", ("critère",), actor="t", owner="winston", ready=True)
    return root


@pytest.fixture(scope="module")
def stream_url(stream_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-stream"))
    env["GRIMOIRE_PULSE_SECONDS"] = str(INTERVAL_S)
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(stream_project), "--port", str(port), "--no-open"],
        cwd=str(stream_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


@pytest.fixture
def page(browser: Browser, stream_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    opened = context.new_page()
    opened.goto(f"{stream_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield opened
    finally:
        context.close()


@pytest.fixture
def service(stream_project: Path):  # type: ignore[no-untyped-def]
    from grimoire.missions.service import TaskService

    return TaskService(stream_project)


def _card(page: Page, title: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title).first


def _api_requests(page: Page) -> list[str]:
    seen: list[str] = []
    page.on("request", lambda request: seen.append(request.url) if "/api/" in request.url else None)
    return seen


_HIDE = """() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  document.dispatchEvent(new Event('visibilitychange'));
}"""
_SHOW = """() => {
  delete document.visibilityState;
  delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
}"""
_STAMP = "() => document.getElementById('st-live-text').textContent"


def test_l_espace_ouvert_suit_le_flux_sans_rechargement_et_dit_sa_fraicheur(page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Une carte créée HORS du navigateur paraît bien avant l'intervalle de 30 s, la page n'ayant pas rechargé."""
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    assert page.evaluate("() => window.GrimoireWorkspace.live.state.transport") == "stream"
    page.evaluate("() => { window.__marqueur = 'jamais rechargee'; }")
    navigations = page.evaluate("() => performance.getEntriesByType('navigation').length")
    title = "Carte poussée par le flux"
    assert _card(page, title).count() == 0

    written_at = time.monotonic()
    service.add(title, ("critère",), actor="t", owner="winston", ready=True)
    _card(page, title).wait_for(timeout=APPEAR_MS)
    elapsed = time.monotonic() - written_at

    assert elapsed < INTERVAL_S / 2, f"carte vue {elapsed:.1f} s après l'écriture : ce n'est pas le flux"
    assert page.evaluate("() => window.__marqueur") == "jamais rechargee", "la page a été rechargée"
    assert page.evaluate("() => performance.getEntriesByType('navigation').length") == navigations
    stamp = page.evaluate(_STAMP)
    match = re.fullmatch(r"mis à jour il y a (\d+) s", stamp)
    assert match, stamp
    assert int(match.group(1)) <= 5, f"la fraicheur doit suivre l'événement reçu : {stamp}"
    title_attr = page.locator("#st-live").get_attribute("title") or ""
    assert f"Flux en direct, confirmé au moins toutes les {INTERVAL_S} s" in title_attr, title_attr
    _capture(page, "flux-carte-poussee")


def test_le_compteur_avance_seul_entre_deux_evenements(page: Page) -> None:
    """« mis à jour il y a N s » avance d'une seconde à l'autre sans aucune requête."""
    page.wait_for_function("() => /mis à jour il y a \\d+ s/.test(document.getElementById('st-live-text').textContent)")
    requests = _api_requests(page)
    first = int(re.findall(r"\d+", page.evaluate(_STAMP))[0])
    page.wait_for_timeout(2500)
    second = int(re.findall(r"\d+", page.evaluate(_STAMP))[0])

    assert second >= first + 2, (first, second)
    assert requests == [], f"le compteur ne fait aucune requête : {requests}"


def test_sans_changement_le_flux_ne_redessine_rien_et_ne_relit_rien(page: Page) -> None:
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1000)
    requests = _api_requests(page)
    page.evaluate("() => { document.querySelector('.ex-card').dataset.sentinelle = 'intacte'; }")

    page.wait_for_timeout(3000)

    others = [u for u in requests if "/api/workspace/" in u and "/glossary" not in u]
    assert not others, f"rien n'a bougé, rien n'est relu ni sondé : {others}"
    assert page.evaluate("() => document.querySelector('.ex-card').dataset.sentinelle") == "intacte"


def test_un_onglet_cache_ferme_le_flux_puis_le_rouvre_et_rattrape(page: Page, service) -> None:  # type: ignore[no-untyped-def]
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.evaluate(_HIDE)
    page.wait_for_timeout(300)
    requests = _api_requests(page)
    title = "Carte née pendant que le flux était fermé"

    service.add(title, ("critère",), actor="t", owner="winston", ready=True)
    page.wait_for_timeout(2500)

    assert requests == [], f"requêtes émises onglet masqué : {requests}"
    assert _card(page, title).count() == 0
    assert "suspendue" in page.locator("#st-live-text").inner_text()

    page.evaluate(_SHOW)
    _card(page, title).wait_for(timeout=APPEAR_MS)
    assert any("/api/workspace/stream" in u for u in requests), "le flux se rouvre dès le retour"
    assert not any("/api/workspace/pulse" in u for u in requests), "le flux n'est pas remplacé par un sondage"
