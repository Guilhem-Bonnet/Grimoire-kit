"""Mode conversation de la mascotte (chat d'un projet et Atelier), navigateur réel, micro et synthèse simulés.

Un geste explicite ouvre le mode (bouton « Converser » ou Alt+Maj+V) ; ensuite écoute (la dictée existante) → texte dicté
dans la saisie, JAMAIS envoyé tout seul (l'envoi reste un clic ou Entrée) → réponse lue → écoute. Arrêt immédiat (bouton,
Échap, perte de focus, permission), sortie après un silence prolongé. Le micro n'est ouvert qu'après le geste.

Le micro est un oscillateur dont le test règle le gain (la parole commence et finit à la demande), la synthèse du
navigateur et les routes vocales sont simulées, le flux du chat reçoit les événements poussés par le test : aucun
moteur, aucun modèle, aucun agent. ``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import struct
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, BrowserContext, Page, Route

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

READY_MS = 30_000
VOICE_ON = {"available": True, "reason": "", "mode": "voice", "max_seconds": 30, "sample_rate": 16000, "problems": []}
VOICE_NONE = {**VOICE_ON, "available": False, "reason": "moteur vocal absent", "mode": "text"}
TOGGLE = "[data-act='conversation']"
STATUS = "[data-role='conversation-status']"
MIC = "[data-role='conversation-mic']"

#: Un micro simulé : un oscillateur dont le gain (`window.__mic.gain`) règle la parole ; il compte les ouvertures.
FAKE_MIC = """(() => {
  window.__mic = { calls: 0, tracks: [], gain: null };
  navigator.mediaDevices.getUserMedia = async () => {
    window.__mic.calls += 1;
    const context = new AudioContext();
    const osc = context.createOscillator();
    osc.frequency.value = 220;
    const gain = context.createGain();
    gain.gain.value = 0;
    window.__mic.gain = gain;
    const dest = context.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    for (const track of dest.stream.getTracks()) window.__mic.tracks.push(track);
    return dest.stream;
  };
  window.__speak = (on) => { if (window.__mic.gain) window.__mic.gain.gain.value = on ? 0.6 : 0; };
})()"""

#: La synthèse du navigateur simulée : note ce qu'on lui fait lire, et finit la lecture au bout de 300 ms.
FAKE_SPEECH = """(() => {
  window.__spoken = [];
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  Object.defineProperty(window, 'speechSynthesis', { value: {
    speak(u) { window.__spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 300); },
    cancel() { window.__spoken.push('<cancel>'); },
  } });
})()"""

#: Capte l'écouteur du flux du chat : le test y pousse des événements, comme le ferait le serveur.
CAPTURE_STREAM = """(() => {
  const add = EventSource.prototype.addEventListener;
  EventSource.prototype.addEventListener = function (type, fn, ...rest) {
    if (type === 'chat') window.__chat = fn;
    return add.call(this, type, fn, ...rest);
  };
  window.__push = (event) => window.__chat({ data: JSON.stringify(event) });
})()"""


def _wav_base64(seconds: float = 0.8, rate: int = 16000) -> str:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(9000 * math.sin(2 * math.pi * 330 * i / rate)))
                for i in range(int(seconds * rate))
            )
        )
    return base64.b64encode(out.getvalue()).decode("ascii")


REPORT = {
    "id": "r1",
    "kind": "report",
    "text": "C'est fait.",
    "played": False,
    "revisions": 1,
    "duration_s": 1,
    "source": "report",
    "fixed": False,
    "answered": False,
}
TRANSCRIPT = "bonjour la forge"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "conversation", roots):
        workspace_atelier.set_atelier(roots[0], True)
        workspace_atelier.set_mascotte(roots[0], True)
        yield url, roots[0]


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _page(
    browser: Browser, viewport: tuple[int, int] = (1440, 900), reduced: bool = False
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
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    return page, context, requests, errors


def _voice_routes(page: Page, voice: dict[str, object]) -> None:
    page.route("**/api/workspace/voice/config*", lambda route: _json(route, voice))
    for pattern in ("**/api/workspace/voice/outbox", "**/api/workspace/voice/outbox?*"):
        page.route(pattern, lambda route: _json(route, {"enabled": True, "messages": [], "older_questions": 0}))
    page.route(
        "**/api/workspace/voice/outbox/play*",
        lambda route: _json(route, {"message": {**REPORT, "played": True}, "audio": _wav_base64()}),
    )
    page.route(
        "**/api/workspace/voice/transcribe*",
        lambda route: _json(route, {"text": TRANSCRIPT, "dictation_token": "tok-1"}),
    )


def _open_chat(
    browser: Browser,
    url: str,
    voice: dict[str, object] = VOICE_ON,
    viewport: tuple[int, int] = (1440, 900),
    reduced: bool = False,
) -> tuple[Page, BrowserContext, list[str], list[str]]:
    page, context, requests, errors = _page(browser, viewport, reduced)
    for script in (FAKE_MIC, FAKE_SPEECH, CAPTURE_STREAM):
        page.add_init_script(script)
    _voice_routes(page, voice)
    page.route("**/api/workspace/chat/send*", lambda route: _json(route, {"ok": True, "accepted": True}))
    body = (
        "retry: 600000\n\n"
        + f"id: 0\nevent: chat\ndata: {json.dumps({'seq': 0, 'type': 'hello', 'state': {'running': True, 'busy': False, 'seq': 0}})}\n\n"
    )
    page.route(
        "**/api/workspace/chat/stream*",
        lambda route: route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body),
    )
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=READY_MS)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context, requests, errors


def _status(page: Page, text: str, timeout: int = READY_MS) -> None:
    page.wait_for_function(
        "([sel, t]) => (document.querySelector(sel)?.textContent || '').includes(t)",
        arg=[STATUS, text],
        timeout=timeout,
    )


def _mic_calls(page: Page) -> int:
    return int(page.evaluate("() => window.__mic.calls"))


def _mic_closed(page: Page) -> bool:
    return bool(page.evaluate("() => window.__mic.tracks.every((t) => t.readyState === 'ended')"))


def _talk_once(page: Page) -> None:
    """La personne parle une phrase : le gain monte, puis retombe ; le silence qui suit clôt la phrase."""
    page.wait_for_function("() => window.__mic.gain !== null", timeout=READY_MS)
    page.evaluate("() => window.__speak(true)")
    page.wait_for_timeout(700)
    page.evaluate("() => window.__speak(false)")


def _capture(page: Page, name: str, viewport: tuple[int, int]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}-{viewport[0]}x{viewport[1]}.png"), full_page=False)


def test_chat_without_voice_has_no_conversation_and_loads_nothing(browser: Browser, project: tuple[str, Path]) -> None:
    url, _root = project
    page, context, requests, errors = _open_chat(browser, url, VOICE_NONE)
    try:
        page.wait_for_timeout(800)
        assert page.locator(f".chat {TOGGLE}").is_hidden() or page.locator(f".chat {TOGGLE}").count() == 0
        assert not [r for r in requests if r.endswith("/conversation.js")], "sans vocal, le module n'est pas demandé"
        assert _mic_calls(page) == 0
        assert not errors, errors
    finally:
        context.close()


def test_chat_loop_dictates_without_sending_then_reads_the_reply_then_listens(
    browser: Browser, project: tuple[str, Path]
) -> None:
    url, _root = project
    page, context, requests, errors = _open_chat(browser, url)
    try:
        toggle = page.locator(f".chat {TOGGLE}")
        toggle.wait_for(state="visible", timeout=READY_MS)
        assert _mic_calls(page) == 0, "le micro ne s'ouvre qu'après le geste"
        assert page.locator(MIC).is_hidden()
        assert toggle.get_attribute("aria-keyshortcuts") == "Alt+Shift+V"
        assert page.locator(STATUS).get_attribute("aria-live") == "polite"
        toggle.click()
        _status(page, "je t’écoute")
        page.locator(MIC).wait_for(
            state="visible", timeout=READY_MS
        )  # l'indicateur reste visible tant que le micro est ouvert
        assert _mic_calls(page) == 1
        _talk_once(page)
        _status(page, "relis puis envoie")
        assert page.locator(".chat-input").input_value() == TRANSCRIPT, "la dictée arrive dans la saisie"
        assert [r for r in requests if r.startswith("POST") and "chat/send" in r] == [], "rien n'est envoyé tout seul"
        assert _mic_closed(page) and page.locator(MIC).is_hidden(), "le micro est fermé pendant la relecture"
        # L'envoi est humain : Entrée.
        page.locator(".chat-input").press("Enter")
        page.wait_for_function(
            "() => document.querySelector('.chat-stop') && !document.querySelector('.chat-stop').hidden",
            timeout=READY_MS,
        )
        assert len([r for r in requests if r.startswith("POST") and "chat/send" in r]) == 1
        page.evaluate("(e) => window.__push(e)", {"seq": 1, "type": "turn_started"})
        _status(page, "réfléchit")
        page.evaluate("(e) => window.__push(e)", {"seq": 2, "type": "voice_message", "message": REPORT})
        page.evaluate("(e) => window.__push(e)", {"seq": 3, "type": "turn_ended", "stop_reason": "end_turn"})
        _status(page, "parle")
        _status(page, "je t’écoute")  # la lecture finie, le mode réécoute
        assert _mic_calls(page) == 2
        page.keyboard.press("Escape")
        _status(page, "Conversation arrêtée")
        page.wait_for_function("() => window.__mic.tracks.every((t) => t.readyState === 'ended')", timeout=READY_MS)
        assert page.locator(MIC).is_hidden()
        assert not errors, errors
    finally:
        context.close()


def test_chat_shortcut_opens_and_stops_and_focus_loss_stops_immediately(
    browser: Browser, project: tuple[str, Path]
) -> None:
    url, _root = project
    page, context, _requests, errors = _open_chat(browser, url)
    try:
        page.locator(f".chat {TOGGLE}").wait_for(state="visible", timeout=READY_MS)
        page.keyboard.press("Alt+Shift+V")
        _status(page, "je t’écoute")
        page.keyboard.press("Alt+Shift+V")
        _status(page, "Conversation arrêtée")
        page.keyboard.press("Alt+Shift+V")
        _status(page, "je t’écoute")
        page.wait_for_timeout(2300)  # passé l'invite du micro, une perte de focus coupe tout de suite
        page.evaluate("() => window.dispatchEvent(new Event('blur'))")
        _status(page, "perdu le focus")
        page.wait_for_function("() => window.__mic.tracks.every((t) => t.readyState === 'ended')", timeout=READY_MS)
        assert not errors, errors
    finally:
        context.close()


def test_chat_permission_closes_the_mode_only_a_click_decides(browser: Browser, project: tuple[str, Path]) -> None:
    url, _root = project
    page, context, _requests, errors = _open_chat(browser, url)
    try:
        page.locator(f".chat {TOGGLE}").click()
        _status(page, "je t’écoute")
        perm = {
            "seq": 1,
            "type": "permission",
            "request_id": "r1",
            "tool_call": {"title": "Écrire x"},
            "options": [{"optionId": "a", "kind": "allow_once", "name": "Oui"}],
        }
        page.evaluate("(e) => window.__push(e)", perm)
        _status(page, "permission")
        assert _mic_closed(page)
        assert not errors, errors
    finally:
        context.close()


def test_chat_long_silence_closes_the_mode(browser: Browser, project: tuple[str, Path]) -> None:
    url, _root = project
    page, context, _requests, errors = _open_chat(browser, url)
    try:
        page.locator(f".chat {TOGGLE}").click()
        _status(page, "je t’écoute")
        _status(page, "silence prolongé", timeout=25_000)
        assert _mic_closed(page)
        assert not errors, errors
    finally:
        context.close()


def test_chat_captures(browser: Browser, project: tuple[str, Path]) -> None:
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    url, _root = project
    for viewport in ((1440, 900), (1024, 768)):
        page, context, _requests, _errors = _open_chat(browser, url, viewport=viewport)
        try:
            page.locator(f".chat {TOGGLE}").click()
            _status(page, "je t’écoute")
            page.wait_for_timeout(1200)
            _capture(page, "chat-ecoute", viewport)
            _talk_once(page)
            _status(page, "relis puis envoie")
            page.wait_for_timeout(600)
            _capture(page, "chat-relecture", viewport)
        finally:
            context.close()


# ── Atelier ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _entretien(**turn: Any) -> dict[str, Any]:
    question = turn.pop("question", None)
    return {
        "chantier": "x",
        "geste": [],
        "agent": {"available": True, "reason": ""},
        "equivalent": "grimoire cadrage interview",
        "started": True,
        "complete": False,
        "error": "",
        "covered": 1,
        "total": 7,
        "current": {"angle": "usage", "title": "Utilisateurs et usage réel", "phase": "brief"},
        "phases": {},
        "angles": [],
        "turn": {"mine": False, "busy": False, "permission": False, **turn},
        "question": question,
    }


Q1 = {
    "text": "Qui utilisera cela au quotidien ?",
    "angle": "usage",
    "title": "Utilisateurs et usage réel",
    "phase": "brief",
}
Q2 = {
    "text": "À quoi voit-on que cela marche ?",
    "angle": "criteres",
    "title": "Critères de réussite",
    "phase": "brief",
}


def _chantier(root: Path, titre: str) -> str:
    return new_chantier(root, titre).slug


def _open_atelier(
    browser: Browser,
    url: str,
    slug: str,
    holder: list[dict[str, Any]],
    voice: dict[str, object] = VOICE_ON,
    viewport: tuple[int, int] = (1440, 900),
) -> tuple[Page, BrowserContext, list[str], list[str]]:
    page, context, requests, errors = _page(browser, viewport)
    for script in (FAKE_MIC, FAKE_SPEECH):
        page.add_init_script(script)
    _voice_routes(page, voice)
    page.route("**/api/workspace/atelier/chantiers/*/entretien*", lambda route: _json(route, holder[0]))
    page.route(
        "**/api/workspace/atelier/chantiers/*/assistant*", lambda route: _json(route, {"ok": True, "accepted": True})
    )
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=READY_MS)
    return page, context, requests, errors


def test_atelier_reads_the_question_dictates_into_the_field_and_never_sends(
    browser: Browser, project: tuple[str, Path]
) -> None:
    url, root = project
    slug = _chantier(root, "Conversation Atelier")
    holder = [_entretien(question=Q1)]
    page, context, requests, errors = _open_atelier(browser, url, slug, holder)
    try:
        toggle = page.locator(f"[data-role='assistant-mascotte'] {TOGGLE}")
        toggle.wait_for(state="visible", timeout=READY_MS)
        assert _mic_calls(page) == 0
        toggle.click()
        # D'abord la question de l'assistant est lue, puis le micro s'ouvre.
        page.wait_for_function("() => window.__spoken.length >= 1", timeout=READY_MS)
        assert page.evaluate("() => window.__spoken[0]") == Q1["text"]
        _status(page, "je t’écoute")
        assert _mic_calls(page) == 1
        _talk_once(page)
        _status(page, "relis puis envoie")
        assert page.locator(".at-asst-area").input_value() == TRANSCRIPT, "la dictée arrive dans le champ de réponse"
        assert [r for r in requests if r.startswith("POST") and "/assistant" in r] == [], "aucun envoi automatique"
        assert _mic_closed(page)
        # L'envoi est humain : Entrée. L'assistant réfléchit, puis pose sa question suivante : elle est lue, puis on réécoute.
        holder[0] = _entretien(mine=True, busy=True)
        page.locator(".at-asst-area").press("Enter")
        assert len([r for r in requests if r.startswith("POST") and "/assistant" in r]) == 1
        _status(page, "réfléchit")
        holder[0] = _entretien(question=Q2)
        page.wait_for_function("(t) => window.__spoken.includes(t)", arg=Q2["text"], timeout=READY_MS)
        _status(page, "je t’écoute")
        assert _mic_calls(page) == 2
        page.keyboard.press("Escape")
        _status(page, "Conversation arrêtée")
        assert not errors, errors
    finally:
        context.close()


def test_atelier_without_voice_no_conversation(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Sans voix")
    page, context, _requests, errors = _open_atelier(browser, url, slug, [_entretien(question=Q1)], VOICE_NONE)
    try:
        page.locator("[data-role='assistant-mascotte']").wait_for(timeout=READY_MS)
        page.wait_for_timeout(800)
        assert (
            page.locator(f"[data-role='assistant-mascotte'] {TOGGLE}").is_hidden() or page.locator(TOGGLE).count() == 0
        )
        assert _mic_calls(page) == 0
        assert not errors, errors
    finally:
        context.close()


def test_atelier_captures(browser: Browser, project: tuple[str, Path]) -> None:
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    url, root = project
    slug = _chantier(root, "Captures conversation")
    for viewport in ((1440, 900), (1024, 768)):
        page, context, _requests, _errors = _open_atelier(
            browser, url, slug, [_entretien(question=Q1)], viewport=viewport
        )
        try:
            page.locator(f"[data-role='assistant-mascotte'] {TOGGLE}").click()
            _status(page, "je t’écoute")
            page.wait_for_timeout(1500)
            _capture(page, "atelier-ecoute", viewport)
            _talk_once(page)
            _status(page, "relis puis envoie")
            page.wait_for_timeout(600)
            _capture(page, "atelier-relecture", viewport)
        finally:
            context.close()
