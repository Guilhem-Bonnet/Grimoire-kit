"""Mascotte du chat (#1197, vocal #1343), navigateur réel.

Le drapeau ``cockpit.test_features.mascotte`` est éteint par défaut : rien d'affiché, ni ``mascotte.js`` ni sa feuille
demandés. Allumé, vocal installé ou non, un canevas et un libellé visible (``aria-live="polite"``) apparaissent dans le
chat : « au repos », « réfléchit » pendant un tour, « écrit », « attend ta réponse » tant qu'une question ou une
permission est en suspens (rendu arrêté après l'entrée), « a terminé » à la fin d'un tour réussi ; avec le vocal,
« écoute » pendant une dictée et « parle » pendant la lecture d'un message vocal. Sans WebGL 2 : le libellé reste, avec
une pastille à la place du canevas, aucune erreur.

Le micro est simulé (un oscillateur) et le serveur vocal, le flux du chat et la lecture sont fournis par le test :
aucun moteur, aucun modèle, aucun agent. ``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ;
sans la variable, le test de capture ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip).
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import struct
import time
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, BrowserContext, Page, Route

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

READY_MS = 30_000
FIGURE = ".chat-mascotte"
CANVAS = ".chat-mascotte canvas"

VOICE_ON = {"available": True, "reason": "", "mode": "voice", "max_seconds": 30, "sample_rate": 16000, "problems": []}
VOICE_NONE = {**VOICE_ON, "available": False, "reason": "moteur vocal absent", "mode": "text"}
CAPTION = ".chat-mascotte-caption"

#: Un micro simulé : un oscillateur sur un flux média. Il compte les ouvertures et garde les pistes (pour vérifier leur fermeture).
FAKE_MIC = """(() => {
  window.__mic = { calls: 0, tracks: [] };
  navigator.mediaDevices.getUserMedia = async () => {
    window.__mic.calls += 1;
    const context = new AudioContext();
    const osc = context.createOscillator();
    osc.frequency.value = 220;
    const gain = context.createGain();
    gain.gain.value = 0.6;
    const dest = context.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    for (const track of dest.stream.getTracks()) window.__mic.tracks.push(track);
    return dest.stream;
  };
})()"""

#: Compte les images réellement dessinées par la mascotte (un appel de dessin WebGL2).
COUNT_DRAWS = """(() => {
  window.__draws = 0;
  const draw = WebGL2RenderingContext.prototype.drawArrays;
  WebGL2RenderingContext.prototype.drawArrays = function (...args) { window.__draws += 1; return draw.apply(this, args); };
})()"""

#: Sans WebGL 2 : le navigateur refuse le contexte.
NO_WEBGL2 = """(() => {
  const get = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) { return kind === 'webgl2' ? null : get.call(this, kind, ...rest); };
})()"""


def _wav_base64(seconds: float = 1.2, rate: int = 16000) -> str:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(int(seconds * rate))
        )
        wav.writeframes(frames)
    return base64.b64encode(out.getvalue()).decode("ascii")


MESSAGE = {
    "id": "m1",
    "kind": "report",
    "text": "Le chantier avance.",
    "played": False,
    "revisions": 1,
    "duration_s": 1,
    "source": "report",
    "fixed": False,
    "answered": False,
}


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _serve_empty_project(tmp_path_factory, "mascotte-off")


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "mascotte-on", roots):
        workspace_atelier.set_mascotte(roots[0], True)
        yield url


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open(
    browser: Browser,
    url: str,
    *,
    config: dict[str, object] = VOICE_ON,
    scripts: tuple[str, ...] = (),
    stream: list[dict[str, object]] | None = None,
    viewport: tuple[int, int] = (1440, 900),
    reduced: bool = False,
    messages: list[dict[str, object]] | None = None,
    stream_delay_s: float = 0.0,
) -> tuple[Page, BrowserContext, list[str], list[str]]:
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce" if reduced else "no-preference"
    )
    page = context.new_page()
    requests: list[str] = []
    errors: list[str] = []
    page.on("request", lambda r: requests.append(f"{r.method} {r.url}"))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for script in scripts:
        page.add_init_script(script)
    page.route("**/api/workspace/voice/config*", lambda route: _json(route, config))
    for pattern in ("**/api/workspace/voice/outbox", "**/api/workspace/voice/outbox?*"):
        page.route(
            pattern, lambda route: _json(route, {"enabled": True, "messages": messages or [], "older_questions": 0})
        )
    page.route(
        "**/api/workspace/voice/outbox/play*",
        lambda route: _json(route, {"message": {**MESSAGE, "played": True}, "audio": _wav_base64()}),
    )
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.route(
        "**/api/workspace/voice/transcribe*",
        lambda route: _json(route, {"text": "bonjour la forge", "dictation_token": "tok-1"}),
    )
    events = [{"seq": 0, "type": "hello", "state": {"running": True, "busy": False, "seq": 0}}] + (stream or [])
    body = "retry: 600000\n\n" + "".join(f"id: {e['seq']}\nevent: chat\ndata: {json.dumps(e)}\n\n" for e in events)

    def serve_stream(route: Route) -> None:
        if stream_delay_s:
            time.sleep(stream_delay_s)  # les événements du fil arrivent quand la mascotte est montée (captures)
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)

    page.route("**/api/workspace/chat/stream*", serve_stream)
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=READY_MS)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context, requests, errors


def _label(page: Page) -> str:
    return page.locator(CANVAS).get_attribute("aria-label") or ""


def _draws(page: Page) -> int:
    return int(page.evaluate("() => window.__draws"))


def _settle(page: Page, quiet_ms: int = 400) -> None:
    """Attend que la boucle s'arrête (au repos stable) : le nombre d'images ne bouge plus pendant `quiet_ms`."""
    page.wait_for_function(
        """(ms) => new Promise((resolve) => { const a = window.__draws; setTimeout(() => resolve(window.__draws === a), ms); })""",
        arg=quiet_ms,
        timeout=20_000,
    )


def _capture(page: Page, name: str, viewport: tuple[int, int]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}-{viewport[0]}x{viewport[1]}.png"), full_page=False)


def test_flag_off_nothing_visible_and_module_never_requested(browser: Browser, project_off: str) -> None:
    page, context, requests, errors = _open(browser, project_off)
    try:
        page.wait_for_selector(".chat-voice-out, .chat-form", state="attached")
        page.wait_for_timeout(800)
        assert page.locator(CANVAS).count() == 0
        assert page.locator(FIGURE).is_hidden()
        assert not [r for r in requests if "mascotte" in r and "mascotte_lazy.js" not in r], (
            "drapeau éteint : seule la porte (mascotte_lazy.js) peut être demandée"
        )
        assert not errors
    finally:
        context.close()


def _caption(page: Page) -> str:
    return (page.locator(CAPTION).text_content() or "").strip()


def _wait_caption(page: Page, text: str, timeout: int = READY_MS) -> None:
    page.wait_for_function(
        "(t) => (document.querySelector('.chat-mascotte-caption')?.textContent || '').trim() === t",
        arg=text,
        timeout=timeout,
    )


def test_flag_on_without_voice_shows_canvas_and_rest_caption_and_the_loop_stops(
    browser: Browser, project_on: str
) -> None:
    """Le vocal n'est pas installé : la mascotte est là quand même, au repos, rendu arrêté."""
    page, context, _requests, errors = _open(browser, project_on, config=VOICE_NONE, scripts=(COUNT_DRAWS,))
    try:
        page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
        _wait_caption(page, "au repos")
        assert page.locator(CAPTION).get_attribute("aria-live") == "polite"
        assert page.locator(CAPTION).is_visible()
        page.wait_for_function("() => window.__draws >= 1", timeout=10_000)
        _settle(page)
        before = _draws(page)
        page.wait_for_timeout(700)
        assert _draws(page) == before, "au repos stable la boucle de rendu doit être arrêtée"
        assert not errors
    finally:
        context.close()


def test_a_turn_without_voice_shows_thinking(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(
        browser, project_on, config=VOICE_NONE, stream=[{"seq": 1, "type": "turn_started"}]
    )
    try:
        _wait_caption(page, "réfléchit")
        assert not errors
    finally:
        context.close()


def test_edit_tool_call_shows_writing_then_a_finished_turn_delivers_then_rests(
    browser: Browser, project_on: str
) -> None:
    call = {
        "sessionUpdate": "tool_call",
        "toolCallId": "t1",
        "kind": "edit",
        "status": "in_progress",
        "title": "Edit x",
    }
    page, context, _requests, errors = _open(
        browser,
        project_on,
        config=VOICE_NONE,
        stream=[{"seq": 1, "type": "turn_started"}, {"seq": 2, "type": "update", "update": call}],
    )
    try:
        _wait_caption(page, "écrit")
        assert not errors
    finally:
        context.close()
    done = [
        {"seq": 1, "type": "turn_started"},
        {"seq": 2, "type": "turn_ended", "stop_reason": "end_turn"},
    ]
    page, context, _requests, errors = _open(browser, project_on, config=VOICE_NONE, stream=done)
    try:
        _wait_caption(page, "a terminé")
        _wait_caption(page, "au repos", timeout=10_000)
        assert not errors
    finally:
        context.close()


def test_a_pending_question_waits_for_the_human_and_rendering_stops_after_the_entry(
    browser: Browser, project_on: str
) -> None:
    question = {
        "seq": 2,
        "type": "question",
        "task_id": "T-1",
        "question_id": "q1",
        "text": "Quelle base ?",
        "kept": "",
        "options": [],
    }
    stream = [{"seq": 1, "type": "turn_started"}, question]
    page, context, _requests, errors = _open(
        browser, project_on, config=VOICE_NONE, scripts=(COUNT_DRAWS,), stream=stream
    )
    try:
        _wait_caption(page, "attend ta réponse")  # prime sur « réfléchit » (le tour est en cours)
        page.wait_for_function("() => window.__draws >= 60", timeout=15_000)  # l'animation d'entrée a bien tourné
        _settle(page, 1500)  # puis la boucle s'arrête (fenêtre large : l'hôte chargé peut retarder une image)
        before = _draws(page)
        assert before < 400, f"l'entrée ne doit pas durer : {before} images"
        page.wait_for_timeout(1500)
        assert _draws(page) == before, "une question en attente ne fait pas tourner le rendu en continu"
        assert _caption(page) == "attend ta réponse"
        assert not errors
    finally:
        context.close()


def test_a_pending_permission_also_waits_and_resolving_it_releases_the_state(browser: Browser, project_on: str) -> None:
    perm = {
        "seq": 2,
        "type": "permission",
        "request_id": "r1",
        "tool_call": {"title": "Écrire x"},
        "options": [{"optionId": "a", "kind": "allow_once", "name": "Oui"}],
    }
    page, context, _requests, errors = _open(
        browser, project_on, config=VOICE_NONE, stream=[{"seq": 1, "type": "turn_started"}, perm]
    )
    try:
        _wait_caption(page, "attend ta réponse")
        assert not errors
    finally:
        context.close()


def test_without_webgl2_the_caption_stays_with_a_dot_instead_of_the_canvas(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(
        browser, project_on, config=VOICE_NONE, scripts=(NO_WEBGL2,), stream=[{"seq": 1, "type": "turn_started"}]
    )
    try:
        _wait_caption(page, "réfléchit")
        page.locator(".chat-mascotte-dot").wait_for(state="visible", timeout=READY_MS)
        assert page.locator(FIGURE).is_visible(), "pas de bande cachée"
        assert page.locator(CANVAS).count() == 0, "pas de canevas vide"
        assert "WebGL" in (page.locator(".chat-mascotte-dot").get_attribute("title") or "")
        assert not errors, errors
    finally:
        context.close()


def test_flag_on_voice_mode_shows_the_mascotte_at_rest_and_the_loop_stops(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(browser, project_on, scripts=(COUNT_DRAWS,))
    try:
        canvas = page.locator(CANVAS)
        canvas.wait_for(state="visible", timeout=READY_MS)
        assert canvas.get_attribute("role") == "img"
        assert _label(page) == "Mascotte : au repos"
        page.wait_for_function("() => window.__draws >= 1", timeout=10_000)  # une image est posée : pas de canevas vide
        _settle(page)
        before = _draws(page)
        page.wait_for_timeout(700)
        assert _draws(page) == before, "au repos stable la boucle de rendu doit être arrêtée"
        box = canvas.bounding_box()
        assert box and 60 <= box["width"] <= 200 and 40 <= box["height"] <= 140, "petite, dans le panneau du chat"
        assert not errors
    finally:
        context.close()


def test_dictation_listens_then_returns_to_rest_and_the_mascotte_never_holds_the_mic(
    browser: Browser, project_on: str
) -> None:
    page, context, requests, errors = _open(browser, project_on, scripts=(FAKE_MIC, COUNT_DRAWS))
    try:
        page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
        _settle(page)
        mic = page.get_by_role("button", name="Dicter")
        mic.wait_for(state="visible")
        mic.click()
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : écoute'"
        )
        before = _draws(page)
        page.wait_for_timeout(600)
        assert _draws(page) > before + 5, "pendant l'écoute la mascotte s'anime"
        assert page.evaluate("() => window.__mic.calls") == 1, "seule la dictée ouvre le micro"
        page.get_by_role("button", name="Arrêter").click()
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : au repos'"
        )
        assert page.evaluate("() => window.__mic.tracks.every((t) => t.readyState === 'ended')"), (
            "le micro reste ouvert"
        )
        assert page.evaluate("() => window.__mic.calls") == 1
        _settle(page)  # puis la boucle s'arrête de nouveau
        posts = [r for r in requests if r.startswith("POST ")]
        assert all("voice/transcribe" in r for r in posts), f"la mascotte n'envoie rien : {posts}"
        assert not errors
    finally:
        context.close()


def test_a_turn_makes_it_think(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(browser, project_on, stream=[{"seq": 1, "type": "turn_started"}])
    try:
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas')?.getAttribute('aria-label') === 'Mascotte : réfléchit'",
            timeout=READY_MS,
        )
        assert not errors
    finally:
        context.close()


def test_playing_a_voice_message_makes_it_speak_then_rest(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(browser, project_on, scripts=(COUNT_DRAWS,), messages=[MESSAGE])
    try:
        page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
        _settle(page)
        page.locator(".chat-voice-play").first.click()
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : parle'",
            timeout=10_000,
        )
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : au repos'",
            timeout=15_000,
        )
        _settle(page)
        assert not errors
    finally:
        context.close()


def test_reduced_motion_caps_the_cadence(browser: Browser, project_on: str) -> None:
    page, context, _requests, _errors = _open(browser, project_on, scripts=(FAKE_MIC, COUNT_DRAWS), reduced=True)
    try:
        page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
        _settle(page)
        page.get_by_role("button", name="Dicter").click()
        page.wait_for_function(
            "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : écoute'"
        )
        before = _draws(page)
        page.wait_for_timeout(1000)
        assert _draws(page) - before <= 26, "prefers-reduced-motion plafonne la cadence"
        page.get_by_role("button", name="Arrêter").click()
    finally:
        context.close()


def test_without_webgl2_with_voice_the_form_still_works(browser: Browser, project_on: str) -> None:
    page, context, _requests, errors = _open(browser, project_on, scripts=(NO_WEBGL2,))
    try:
        _wait_caption(page, "au repos")
        page.locator(".chat-mascotte-dot").wait_for(state="visible", timeout=READY_MS)
        assert page.locator(".chat-form").is_visible()
        assert not errors, errors
    finally:
        context.close()


def test_captures(browser: Browser, project_on: str) -> None:
    """Captures de la mascotte dans le chat, aux deux tailles de référence. Sans `GRIMOIRE_E2E_CAPTURES`, ne fait rien."""
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    for viewport in ((1440, 900), (1024, 768)):
        page, context, _requests, _errors = _open(
            browser,
            project_on,
            scripts=(FAKE_MIC,),
            viewport=viewport,
            messages=[MESSAGE],
            stream=[{"seq": 1, "type": "turn_started"}],
        )
        try:
            page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
            page.wait_for_function(
                "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : réfléchit'"
            )
            page.wait_for_timeout(1500)
            _capture(page, "reflechit", viewport)
        finally:
            context.close()
        page, context, _requests, _errors = _open(
            browser, project_on, scripts=(FAKE_MIC,), viewport=viewport, messages=[MESSAGE]
        )
        try:
            page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
            page.wait_for_timeout(1200)
            _capture(page, "repos", viewport)
            page.get_by_role("button", name="Dicter").click()
            page.wait_for_function(
                "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : écoute'"
            )
            page.wait_for_timeout(1500)
            _capture(page, "ecoute", viewport)
            page.get_by_role("button", name="Arrêter").click()
            page.wait_for_timeout(500)
            page.locator(".chat-voice-play").first.click()
            page.wait_for_function(
                "() => document.querySelector('.chat-mascotte canvas').getAttribute('aria-label') === 'Mascotte : parle'"
            )
            page.wait_for_timeout(500)
            _capture(page, "parle", viewport)
        finally:
            context.close()


def test_captures_without_voice(browser: Browser, project_on: str) -> None:
    """Les états du fil sans vocal installé, et le repli sans WebGL 2. Sans `GRIMOIRE_E2E_CAPTURES`, ne fait rien."""
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    call = {"sessionUpdate": "tool_call", "toolCallId": "t1", "kind": "edit", "status": "in_progress", "title": "Edit"}
    question = {
        "seq": 2,
        "type": "question",
        "task_id": "T-1",
        "question_id": "q1",
        "text": "Quelle base choisir ?",
        "kept": "En attente de ta décision.",
        "options": ["SQLite", "Postgres"],
    }
    turn = {"seq": 1, "type": "turn_started"}
    states: list[tuple[str, str, list[dict[str, object]], tuple[str, ...]]] = [
        ("sans-voix-repos", "au repos", [], ()),
        ("sans-voix-reflechit", "réfléchit", [turn], ()),
        ("sans-voix-ecrit", "écrit", [turn, {"seq": 2, "type": "update", "update": call}], ()),
        ("sans-voix-question", "attend ta réponse", [turn, question], ()),
        ("sans-voix-livre", "a terminé", [turn, {"seq": 2, "type": "turn_ended", "stop_reason": "end_turn"}], ()),
        ("sans-webgl2", "réfléchit", [turn], (NO_WEBGL2,)),
    ]
    for viewport in ((1440, 900), (1024, 768)):
        for name, caption, stream, scripts in states:
            page, context, _requests, _errors = _open(
                browser,
                project_on,
                config=VOICE_NONE,
                viewport=viewport,
                stream=stream,
                scripts=scripts,
                stream_delay_s=0.0 if not stream else 6.0,
            )
            try:
                _wait_caption(page, caption)
                if "webgl2" in name:
                    page.locator(".chat-mascotte-dot").wait_for(state="visible", timeout=READY_MS)
                else:
                    page.wait_for_function("() => document.querySelector('.chat-mascotte canvas')", timeout=READY_MS)
                # L'entrée de la forme (ressort) laisse le temps de se poser avant la prise de vue.
                page.wait_for_timeout(900 if name == "sans-voix-livre" else 2500)
                _capture(page, name, viewport)
            finally:
                context.close()
