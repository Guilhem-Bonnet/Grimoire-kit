"""Politique de sécurité du cockpit (#1366) : zéro violation CSP dans un vrai navigateur, sur les vues principales.

Le serveur est le vrai (`grimoire serve`), le navigateur aussi ; la page reçoit donc l'en-tête strict. Deux
collecteurs, pour ne rien rater : l'évènement `securitypolicyviolation` (posé avant tout script de la page) et
les messages de la console qui nomment la politique. Vues : accueil, chaque rubrique des espaces, atelier
(drapeau allumé), chat du dock avec un vrai flux (EventSource), lecture d'un message vocal (moteur simulé :
une réponse `data:audio/wav` jouée par un `Audio`).
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, Route

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .test_workspace_a_traiter_moa import served_moa  # noqa: F401  (fixture réutilisée)
from .test_workspace_chat_acp4 import pilot_project, pilot_url  # noqa: F401  (fixtures réutilisées)
from .test_workspace_voice_question import _messages, _ref

AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "chat_agent.py"
APPEAR_MS = 30_000

_COLLECT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push(e.violatedDirective + ' ← ' + (e.blockedURI || e.sample || '(en ligne)') + ' @ ' + e.sourceFile);
});
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


@pytest.fixture(scope="module")
def blueprint(url: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = _ROOTS[url]
    target = root / "_grimoire" / "blueprints" / "csp-demo.blueprint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grimoire",
            "blueprint",
            "new",
            "csp-demo",
            "--out",
            str(target),
            "--template",
            "pipeline",
        ],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if not target.is_file():
        pytest.skip("`grimoire blueprint new` indisponible ici")
    return target


_ROOTS: dict[str, Path] = {}


@pytest.fixture(scope="module")
def url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("csp") / "projet-csp"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-csp", "--backend", "local"],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=180,
    )
    workspace_atelier.set_atelier(root, True)
    new_chantier(root, "Chantier de la politique de sécurité")
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-csp"))
    env["GRIMOIRE_CHAT_AGENT_COMMAND"] = f"{sys.executable} {AGENT} {tmp_path_factory.mktemp('agent') / 'log.jsonl'}"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        _ROOTS[f"http://127.0.0.1:{port}"] = root
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


class _Watched:
    """Une page dont on relève les violations (évènement du navigateur et console)."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.console: list[str] = []
        page.on(
            "console",
            lambda m: self.console.append(m.text) if "content security policy" in m.text.lower() else None,
        )
        page.on(
            "pageerror",
            lambda e: self.console.append(f"erreur de page : {e}") if "security" in str(e).lower() else None,
        )

    def violations(self) -> list[str]:
        try:
            seen = self.page.evaluate("() => window.__csp || []")
        except Exception:
            seen = []
        return [*seen, *self.console]


@pytest.fixture
def watched(browser: Browser, url: str) -> Iterator[_Watched]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(_COLLECT)
    page = context.new_page()
    try:
        yield _Watched(page)
    finally:
        context.close()


def _open(watched: _Watched, url: str, hash_: str = "") -> Page:
    page = watched.page
    response = page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    assert response is not None
    policy = response.headers.get("content-security-policy", "")
    assert "script-src 'self';" in policy and "unsafe-inline" not in policy, policy
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    return page


def _assert_clean(watched: _Watched, where: str) -> None:
    assert watched.violations() == [], f"violations CSP sur {where}"


def test_l_accueil_et_les_espaces_sans_violation(watched: _Watched, url: str) -> None:
    page = _open(watched, url)
    page.wait_for_selector("#spaces [role='tab']", timeout=APPEAR_MS)
    page.wait_for_timeout(800)
    _assert_clean(watched, "accueil")
    sections = page.evaluate("() => Object.values(window.GrimoireWorkspace.sections).flat()")
    assert len(sections) >= 10, sections
    for section in sections:
        page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", section)
        page.wait_for_timeout(500)
        _assert_clean(watched, f"rubrique {section}")
    for theme in ("light", "dark"):
        page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
    page.keyboard.press("Control+k")
    page.wait_for_timeout(200)
    _assert_clean(watched, "palette de commandes")


def test_le_theme_initial_est_pose_par_le_script_externe(browser: Browser, url: str) -> None:
    context = browser.new_context(color_scheme="dark")
    context.add_init_script(_COLLECT)
    page = context.new_page()
    try:
        page.add_init_script("localStorage.setItem('grimoire.workspace.theme', 'light')")
        page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        assert page.evaluate("window.__csp") == []
    finally:
        context.close()


def test_concevoir_sans_violation(watched: _Watched, url: str, blueprint: Path) -> None:
    """Le graphe d'un blueprint pose la position de ses nœuds : ils ne doivent pas se superposer."""
    page = _open(watched, url)
    page.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    page.locator(".cv-card").first.dblclick(timeout=APPEAR_MS)
    page.wait_for_selector(".cv-node", timeout=APPEAR_MS)
    boxes = page.locator(".cv-node").evaluate_all(
        "(els) => els.map((e) => e.getBoundingClientRect().x + ':' + e.getBoundingClientRect().y)"
    )
    assert len(boxes) >= 2 and len(set(boxes)) == len(boxes), boxes
    page.locator(".cv-node").first.dblclick()
    page.wait_for_selector(".cv-tab", timeout=APPEAR_MS)
    _assert_clean(watched, "concevoir")


def test_l_atelier_sans_violation(watched: _Watched, url: str) -> None:
    page = _open(watched, url, "#atelier")
    page.locator("a.at-row").first.wait_for(timeout=APPEAR_MS)
    page.locator("a.at-row").first.click()
    page.wait_for_timeout(800)
    _assert_clean(watched, "atelier")


def _wav_base64() -> str:
    rate, count = 8000, 800
    data = b"\x00\x00" * count
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    )
    return base64.b64encode(header + b"data" + struct.pack("<I", len(data)) + data).decode("ascii")


def test_le_chat_et_un_message_vocal_sans_violation(watched: _Watched, url: str) -> None:
    page = watched.page
    message = {"id": "vm-1", "text": "Résumé parlé de test.", "duration_s": 3, "played": False}

    def outbox(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "max_seconds": 30, "messages": [message]}),
        )

    def play(route: Route) -> None:
        played = {**message, "played": True}
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "message": played,
                    "client_side": False,
                    "text": message["text"],
                    "sample_rate": 8000,
                    "audio": _wav_base64(),
                }
            ),
        )

    page.route(re.compile(r"/api/workspace/voice/outbox/play"), play)
    page.route(re.compile(r"/api/workspace/voice/outbox(\?.*)?$"), outbox)
    page = _open(watched, url)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input", timeout=APPEAR_MS)
    # Un tour complet : le flux de réponse arrive par EventSource (connect-src 'self').
    page.locator(".chat-input").fill("bonjour")
    page.locator(".chat-input").press("Enter")
    page.wait_for_function("() => document.querySelector('.chat-msg.is-agent .chat-body strong')", timeout=APPEAR_MS)
    _assert_clean(watched, "chat")
    # Lecture du message vocal : un `Audio` sur une URL `data:audio/wav` (media-src data:).
    page.evaluate(
        """() => { window.__audioSrc = []; const A = window.Audio;
        window.Audio = function (src) { window.__audioSrc.push(String(src).slice(0, 20)); return new A(src); }; }"""
    )
    page.locator(".chat-voice-play").first.click(timeout=APPEAR_MS)
    page.wait_for_function("() => window.__audioSrc.length > 0", timeout=APPEAR_MS)
    assert page.evaluate("window.__audioSrc[0]").startswith("data:audio/wav")
    page.wait_for_timeout(500)
    _assert_clean(watched, "lecture d'un message vocal")


def test_le_collecteur_voit_une_vraie_violation(watched: _Watched, url: str) -> None:
    """Garde du garde : sans cela, « zéro violation » pourrait venir d'un collecteur muet."""
    page = _open(watched, url)
    page.evaluate(
        "() => { const s = document.createElement('style'); s.textContent = 'body{}'; document.head.append(s); }"
    )
    page.evaluate("() => { document.body.insertAdjacentHTML('beforeend', '<p style=\"color:red\">x</p>'); }")
    page.wait_for_timeout(300)
    seen = watched.violations()
    assert any("style-src" in line for line in seen), seen


_WEB = Path(__file__).resolve().parents[2] / "web"
_PAGES = sorted(p.name for p in _WEB.glob("*.html"))


@pytest.mark.parametrize("mode", ["vitrine", "atelier"])
@pytest.mark.parametrize("legacy", [False, True], ids=["sans-legacy", "legacy"])
def test_chaque_page_html_servie_sans_violation(browser: Browser, url: str, legacy: bool, mode: str) -> None:
    """Pages historiques et vitrine (politique à nonce), dans les deux modes d'habillage, avec et sans `?legacy=1`."""
    assert len(_PAGES) >= 14, _PAGES
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(_COLLECT)
    context.add_init_script(f"try {{ localStorage.setItem('grimoire.mode', '{mode}'); }} catch (e) {{}}")
    page = context.new_page()
    watched = _Watched(page)
    try:
        failures: dict[str, list[str]] = {}
        for name in _PAGES:
            watched.console.clear()
            response = page.goto(f"{url}/{name}{'?legacy=1' if legacy else ''}", wait_until="load")
            assert response is not None and response.status == 200, (name, response and response.status)
            page.wait_for_timeout(700)
            seen = watched.violations()
            if seen:
                failures[name] = seen
        assert failures == {}, failures
    finally:
        context.close()


def test_a_traiter_avec_un_element_moa_sans_violation(browser: Browser, served_moa: str) -> None:  # noqa: F811
    """L'écran À traiter avec des items MOA (exigence à valider, à recetter) : commandes copiables, aucun style en ligne."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(_COLLECT)
    page = context.new_page()
    watched = _Watched(page)
    try:
        response = page.goto(f"{served_moa}/workspace/index.html#a-traiter", wait_until="domcontentloaded")
        assert response is not None and "script-src 'self';" in response.headers["content-security-policy"]
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.wait_for_selector(".at-row[data-kind='requirement_recette']", timeout=APPEAR_MS)
        for kind in ("requirement_recette",):
            page.locator(f".at-row[data-kind='{kind}']").first.click()
            page.locator(".at-detail [data-role='moa-guide']").wait_for(timeout=APPEAR_MS)
            assert page.locator(".at-moa-cmd").count() >= 1
            page.wait_for_timeout(300)
        _assert_clean(watched, "À traiter (MOA)")
    finally:
        context.close()


def test_boite_vocale_avec_question_et_lecture_sans_violation(
    browser: Browser,
    pilot_url: str,  # noqa: F811
    pilot_project: tuple[Path, dict[str, str]],  # noqa: F811
) -> None:
    """Boîte vocale : question dépliée, lecture d'un message (`Audio` sur `data:`), connexions de l'API (`connect-src`)."""
    messages = _messages(_ref(pilot_project))
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(_COLLECT)
    page = context.new_page()
    watched = _Watched(page)

    def outbox(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "messages": messages, "older_questions": 0}),
        )

    def play(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "message": {**messages[0], "played": True},
                    "client_side": False,
                    "text": "Pousser ?",
                    "sample_rate": 8000,
                    "audio": _wav_base64(),
                }
            ),
        )

    try:
        page.route(re.compile(r"/api/workspace/voice/outbox/play"), play)
        page.route(re.compile(r"/api/workspace/voice/outbox(\?.*)?$"), outbox)
        page.goto(f"{pilot_url}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
        page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
        page.locator("[data-dock-tab='orchestrateur']").click()
        first = page.locator(".chat-voice-msg[data-kind='question']").first
        first.wait_for(timeout=APPEAR_MS)
        first.get_by_role("button", name="Tout afficher").click()
        first.locator(".chat-voice-option").first.wait_for(state="visible")
        first.locator(".chat-voice-option").first.click()
        page.evaluate(
            """() => { window.__audioSrc = []; const A = window.Audio;
            window.Audio = function (src) { window.__audioSrc.push(String(src).slice(0, 20)); return new A(src); }; }"""
        )
        first.locator(".chat-voice-play").click()
        page.wait_for_function("() => window.__audioSrc.length > 0", timeout=APPEAR_MS)
        assert page.evaluate("window.__audioSrc[0]").startswith("data:audio/wav")
        page.wait_for_timeout(600)
        _assert_clean(watched, "boîte vocale avec question")
    finally:
        context.close()
