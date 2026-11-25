# ruff: noqa: F811, F401
"""Dictée (#1378), navigateur réel : ce que la page envoie à ``chat/send``.

Le micro est simulé (un flux d'``AudioContext``), ``voice/config`` et ``voice/transcribe`` sont interceptés : pas de
moteur vocal, pas de modèle. ``chat/send`` est intercepté aussi, pour lire le corps réellement envoyé :

- après une dictée, ``dictation: true`` et ``dictation_tokens`` (le jeton rendu par le serveur) ;
- après une saisie au clavier, ni l'un ni l'autre ;
- après une dictée entièrement réécrite au clavier, ni l'un ni l'autre.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, Route

from .test_workspace_chat_acp4 import pilot_project, pilot_url  # (fixtures de module réutilisées)

TOKEN = "jeton-" + "d" * 20
SAID = "ouvre la tâche douze"

#: Un micro simulé : le flux d'un ``AudioContext`` (silence), accepté par ``createMediaStreamSource``.
FAKE_MIC = """
() => {
  navigator.mediaDevices.getUserMedia = async () => {
    const context = new AudioContext();
    return context.createMediaStreamDestination().stream;
  };
}
"""


def _json(route: Route, payload: dict[str, Any]) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open(browser: Browser, url: str) -> tuple[Page, Any, list[dict[str, Any]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script("(" + FAKE_MIC + ")()")
    page = context.new_page()
    sent: list[dict[str, Any]] = []

    def chat_send(route: Route) -> None:
        sent.append(json.loads(route.request.post_data or "{}"))
        _json(route, {"ok": True, "accepted": True})

    page.route(
        "**/voice/config*",
        lambda r: _json(
            r,
            {"available": True, "reason": "", "mode": "voice", "max_seconds": 5, "sample_rate": 16000, "problems": []},
        ),
    )
    page.route(
        "**/voice/transcribe*",
        lambda r: _json(r, {"ok": True, "text": SAID, "language": "fr", "duration_s": 1.0, "dictation_token": TOKEN}),
    )
    page.route("**/chat/send*", chat_send)
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context, sent


def _dictate(page: Page) -> None:
    mic = page.locator(".chat-mic")
    mic.wait_for(state="visible", timeout=15_000)
    mic.click()  # début de l'enregistrement
    page.wait_for_function("() => document.querySelector('.chat-mic').getAttribute('aria-pressed') === 'true'")
    page.wait_for_timeout(400)  # quelques blocs audio
    mic.click()  # fin : transcription interceptée
    page.wait_for_function(
        f"() => document.querySelector('.chat-input').value.includes({json.dumps(SAID)})", timeout=15_000
    )


def _send(page: Page, sent: list[dict[str, Any]]) -> dict[str, Any]:
    before = len(sent)
    page.locator(".chat-input").press("Enter")
    for _ in range(50):
        if len(sent) > before:
            break
        page.wait_for_timeout(100)
    assert len(sent) == before + 1, "chat/send n'a pas été appelé"
    return sent[-1]


def test_a_dictation_sends_the_marker_and_the_token(
    browser: Browser, pilot_url: str, pilot_project: tuple[Path, dict[str, str]]
) -> None:
    page, context, sent = _open(browser, pilot_url)
    try:
        _dictate(page)
        body = _send(page, sent)
        assert body["text"] == SAID
        assert body["dictation"] is True
        assert body["dictation_tokens"] == [TOKEN]
    finally:
        context.close()


def test_a_dictation_corrected_in_part_still_sends_the_token(
    browser: Browser, pilot_url: str, pilot_project: tuple[Path, dict[str, str]]
) -> None:
    page, context, sent = _open(browser, pilot_url)
    try:
        _dictate(page)
        page.locator(".chat-input").fill("ouvre la tâche 12")  # une correction partielle garde la dictée
        body = _send(page, sent)
        assert body["dictation"] is True and body["dictation_tokens"] == [TOKEN]
    finally:
        context.close()


def test_a_message_typed_at_the_keyboard_sends_neither(
    browser: Browser, pilot_url: str, pilot_project: tuple[Path, dict[str, str]]
) -> None:
    page, context, sent = _open(browser, pilot_url)
    try:
        page.locator(".chat-input").fill("bonjour, un message tapé")
        body = _send(page, sent)
        assert body["text"] == "bonjour, un message tapé"
        assert "dictation" not in body and "dictation_tokens" not in body
    finally:
        context.close()


def test_a_dictation_entirely_rewritten_at_the_keyboard_sends_neither(
    browser: Browser, pilot_url: str, pilot_project: tuple[Path, dict[str, str]]
) -> None:
    page, context, sent = _open(browser, pilot_url)
    try:
        _dictate(page)
        page.locator(".chat-input").fill("tout autre chose")  # plus aucun mot dicté
        body = _send(page, sent)
        assert body["text"] == "tout autre chose"
        assert "dictation" not in body and "dictation_tokens" not in body
    finally:
        context.close()
