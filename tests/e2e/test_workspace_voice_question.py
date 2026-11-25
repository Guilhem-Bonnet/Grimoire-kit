# ruff: noqa: F811, F401
"""Boîte vocale, questions (#1343 V4), navigateur réel : texte complet, repli, aucun envoi par un clic d'option.

Le projet et le serveur sont ceux d'ACP4 (une question réservée « mot de passe » est posée dans le fil). La boîte
vocale est alimentée par une réponse simulée de ``voice/outbox`` : pas de moteur vocal, pas de modèle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, Route

from .test_workspace_chat_acp4 import pilot_project, pilot_url  # (fixtures de module réutilisées)

LONG = "Faut-il pousser en force ? " + "Détail de la commande. " * 40 + "\nDERNIÈRE LIGNE DANGEREUSE : tout supprimer."


def _ref(project: tuple[Path, dict[str, str]]) -> dict[str, str]:
    """La question réservée posée par le projet semé (HOME du projet de test, pas celui de la session)."""
    code = (
        "import json; from pathlib import Path; from grimoire.tools import workspace_chat_pilot as p; "
        "r = p.pending_questions(Path('.').resolve())[0]; print(json.dumps({'task_id': r['task_id'], 'question_id': r['question_id']}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(project[0]),
        env=project[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def _messages(ref: dict[str, str]) -> list[dict[str, object]]:
    base = {
        "kind": "question",
        "answered": False,
        "played": False,
        "revisions": 1,
        "duration_s": 5,
        "source": "question",
        "ref": ref,
    }
    return [
        {
            **base,
            "id": "v1",
            "text": "Pousser ? Options : Oui ou Non.",
            "full_text": LONG,
            "options": ["Oui", "Non"],
            "reserved": False,
            "truncated": False,
            "options_hidden": 0,
        },
        {
            **base,
            "id": "v2",
            "text": "Mot de passe ?",
            "full_text": "Reste à vous : réservé\n\nQuel mot de passe ?",
            "options": ["Oui", "Non"],
            "reserved": True,
            "truncated": False,
            "options_hidden": 0,
        },
    ]


def _open(browser: Browser, url: str, messages: list[dict[str, object]]) -> tuple[Page, object, list[str]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)

    def outbox(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "messages": messages, "older_questions": 0}),
        )

    page.route("**/voice/outbox?*", outbox)
    page.route("**/voice/outbox", outbox)
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
    page.wait_for_function("() => document.documentElement.dataset.atelier === 'on'")
    page.locator("[data-dock-tab='orchestrateur']").click()
    page.wait_for_selector(".chat-input")
    return page, context, posts


def test_long_question_has_no_option_button_until_fully_shown_and_a_click_only_prefills(
    browser: Browser,
    pilot_url: str,
    pilot_project: tuple[Path, dict[str, str]],
) -> None:
    page, context, posts = _open(browser, pilot_url, _messages(_ref(pilot_project)))
    try:
        page.locator(".chat-question").wait_for(timeout=15_000)  # la carte du fil existe
        first = page.locator(".chat-voice-msg[data-kind='question']").first
        first.wait_for(timeout=15_000)
        assert first.locator(".chat-voice-option").count() == 0 or not first.locator(".chat-voice-options").is_visible()
        first.get_by_role("button", name="Tout afficher").click()
        assert "DERNIÈRE LIGNE DANGEREUSE" in first.locator(".chat-voice-text").inner_text()
        options = first.locator(".chat-voice-option")
        options.first.wait_for(state="visible")
        before = len(posts)
        options.first.click()
        assert page.locator(".chat-input").input_value() == "Oui"
        assert len(posts) == before  # un clic sur une option n'envoie rien
        first.get_by_role("button", name="Replier").click()
        assert not first.locator(".chat-voice-options").is_visible()
    finally:
        context.close()  # type: ignore[attr-defined]


def test_reserved_question_has_no_option_button(
    browser: Browser,
    pilot_url: str,
    pilot_project: tuple[Path, dict[str, str]],
) -> None:
    page, context, _posts = _open(browser, pilot_url, _messages(_ref(pilot_project))[1:])
    try:
        card = page.locator(".chat-voice-msg[data-kind='question']").first
        card.wait_for(timeout=15_000)
        assert "Reste à vous : réservé" in card.locator(".chat-voice-text").inner_text()
        assert card.locator(".chat-voice-option").count() == 0
    finally:
        context.close()  # type: ignore[attr-defined]
