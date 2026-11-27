"""Atelier de spec : la mascotte incarne l'assistant du cadrage (#1197, assistant #1409, mascotte #1408), navigateur réel.

Le drapeau ``cockpit.test_features.mascotte`` allume le même composant que celui du chat ; ici il reflète l'assistant :
« au repos », « réfléchit » (requête à l'agent en cours), « attend ta réponse » (question), « une suggestion en attente »
(avec compteur), « erreur de l'assistant ». Un clic met la barre de l'assistant au premier plan sur ce qui attend. Le
vocal n'est pas requis ; actif, un bouton lit la question avec la synthèse du navigateur, sans route nouvelle.

L'état de l'entretien (``GET atelier/chantiers/<slug>/entretien``) est fourni par le test : aucun agent, aucun modèle.
Les suggestions sont déposées comme le fait l'outil MCP. ``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures
1440x900 et 1024x768 ; sans la variable, le test de capture ne fait rien.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, BrowserContext, Page, Route, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_suggestions

from .conftest import _serve_empty_project

READY_MS = 30_000
BAR = "[data-role='assistant']"
MASCOTTE = "[data-role='assistant-mascotte']"
CAPTION = "[data-role='mascotte-caption']"
CANVAS = f"{MASCOTTE} canvas"
PISTES = "- Bouton maintenu pour parler.\n- Repli sur le navigateur."

VOICE_NONE = {
    "available": False,
    "reason": "moteur vocal absent",
    "mode": "text",
    "max_seconds": 30,
    "sample_rate": 16000,
    "problems": [],
}
VOICE_ON = {**VOICE_NONE, "available": True, "reason": "", "mode": "voice"}

#: Compte les images réellement dessinées (un appel de dessin WebGL2).
COUNT_DRAWS = """(() => {
  window.__draws = 0;
  const draw = WebGL2RenderingContext.prototype.drawArrays;
  WebGL2RenderingContext.prototype.drawArrays = function (...args) { window.__draws += 1; return draw.apply(this, args); };
})()"""

NO_WEBGL2 = """(() => {
  const get = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) { return kind === 'webgl2' ? null : get.call(this, kind, ...rest); };
})()"""

#: Une synthèse vocale simulée : elle note ce qu'on lui fait lire.
FAKE_SPEECH = """(() => {
  window.__spoken = [];
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  Object.defineProperty(window, 'speechSynthesis', { value: {
    speak(u) { window.__spoken.push(u.text); },
    cancel() { window.__spoken.push('<cancel>'); },
  } });
})()"""


def entretien(**turn: Any) -> dict[str, Any]:
    """L'état d'un entretien commencé, avec les faits du chat donnés en paramètres."""
    question = turn.pop("question", None)
    error = turn.pop("error", "")
    agent = turn.pop("agent", {"available": True, "reason": ""})
    return {
        "chantier": "x",
        "geste": [],
        "agent": agent,
        "equivalent": "grimoire cadrage interview",
        "started": True,
        "complete": False,
        "error": error,
        "covered": 1,
        "total": 7,
        "current": {"angle": "usage", "title": "Utilisateurs et usage réel", "phase": "brief"},
        "phases": {},
        "angles": [],
        "turn": {"mine": False, "busy": False, "permission": False, **turn},
        "question": question,
    }


QUESTION = {
    "text": "Qui utilisera cela au quotidien ?",
    "angle": "usage",
    "title": "Utilisateurs et usage réel",
    "phase": "brief",
}


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """Atelier allumé, mascotte éteinte."""
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-masc-off", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-masc-on", roots):
        workspace_atelier.set_atelier(roots[0], True)
        workspace_atelier.set_mascotte(roots[0], True)
        yield url, roots[0]


def _chantier(root: Path, titre: str) -> str:
    made = new_chantier(root, titre)
    path = root / "_grimoire" / "cadrage" / made.slug / "02-brainstorm.md"
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", PISTES), encoding="utf-8")
    return made.slug


def _suggest(root: Path, slug: str) -> None:
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    block = str(next(b for b in phase["blocks"] if b["text"] == PISTES)["id"])
    workspace_atelier_suggestions.deposit(
        root, slug, {"block_id": block, "texte": "- Bouton maintenu.\n- Repli.", "motif": "plus court"}
    )


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open(
    browser: Browser,
    url: str,
    slug: str,
    state: dict[str, Any],
    *,
    voice: dict[str, object] = VOICE_NONE,
    scripts: tuple[str, ...] = (),
    viewport: tuple[int, int] = (1440, 900),
    reduced: bool = False,
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
    for script in scripts:
        page.add_init_script(script)
    page.route("**/api/workspace/voice/config*", lambda route: _json(route, voice))
    page.route("**/api/workspace/atelier/chantiers/*/entretien*", lambda route: _json(route, state))
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=READY_MS)
    return page, context, requests, errors


def _caption(page: Page) -> str:
    return (page.locator(CAPTION).text_content() or "").strip()


def _wait_caption(page: Page, text: str) -> None:
    page.wait_for_function(
        "(t) => (document.querySelector(\"[data-role='mascotte-caption']\")?.textContent || '').trim() === t",
        arg=text,
        timeout=READY_MS,
    )


def _draws(page: Page) -> int:
    return int(page.evaluate("() => window.__draws"))


def _settle(page: Page, quiet_ms: int = 1500) -> None:
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


def test_flag_off_no_mascotte_and_modules_never_requested(browser: Browser, project_off: tuple[str, Path]) -> None:
    url, root = project_off
    slug = _chantier(root, "Sans mascotte")
    page, context, requests, errors = _open(browser, url, slug, entretien())
    try:
        page.locator(BAR).wait_for(timeout=READY_MS)
        # Assertion négative (« rien ne se charge, rien ne se pose ») : aucun état à attendre, seulement une fenêtre d'observation
        # bornée et explicite, laissée après que la barre de l'assistant est là pour qu'un chargement tardif ait pu se produire.
        page.wait_for_timeout(800)
        expect(page.locator(MASCOTTE)).to_have_count(0)
        heavy = [r for r in requests if r.endswith(("/atelier-mascotte.js", "/mascotte.js", "/mascotte_shader.js"))]
        assert not heavy, f"drapeau éteint : aucun module de la mascotte ne se charge : {heavy}"
        assert not errors, errors
    finally:
        context.close()


def test_visible_at_rest_without_voice_and_rendering_stops(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Repos sans voix")
    page, context, _requests, errors = _open(browser, url, slug, entretien(), scripts=(COUNT_DRAWS,))
    try:
        page.locator(CANVAS).wait_for(state="visible", timeout=READY_MS)
        _wait_caption(page, "au repos")
        expect(page.locator(f"{MASCOTTE} [data-act='speak']")).to_be_hidden()  # sans vocal : rien de vocal
        expect(page.locator(CAPTION)).to_have_attribute("aria-live", "polite")
        expect(page.locator(f"{MASCOTTE} button[data-act='mascotte']")).to_have_attribute("aria-label", re.compile(r"^Assistant du cadrage : au repos"))
        page.wait_for_function("() => window.__draws >= 1", timeout=10_000)
        _settle(page)
        before = _draws(page)
        # Assertion négative (« au repos, aucune image n'est dessinée ») : fenêtre d'observation bornée et explicite.
        page.wait_for_timeout(800)
        assert _draws(page) == before, "au repos, le rendu est arrêté"
        assert not errors, errors
    finally:
        context.close()


def test_busy_turn_shows_thinking(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Réfléchit")
    page, context, _requests, errors = _open(browser, url, slug, entretien(mine=True, busy=True))
    try:
        _wait_caption(page, "réfléchit")
        expect(page.locator(MASCOTTE)).to_have_attribute("data-action", "reflechit")
        assert not errors, errors
    finally:
        context.close()


def test_question_waits_counts_and_click_focuses_the_answer_and_rendering_stops(
    browser: Browser, project_on: tuple[str, Path]
) -> None:
    url, root = project_on
    slug = _chantier(root, "Question en attente")
    page, context, _requests, errors = _open(browser, url, slug, entretien(question=QUESTION), scripts=(COUNT_DRAWS,))
    try:
        _wait_caption(page, "attend ta réponse")
        expect(page.locator("[data-role='mascotte-count']")).to_have_text("1", use_inner_text=True)
        page.wait_for_function("() => window.__draws >= 20", timeout=15_000)
        _settle(page)  # l'entrée finie, le rendu s'arrête : une question qui attend ne tourne pas en continu
        before = _draws(page)
        # Assertion négative (« plus aucune image une fois l'entrée finie ») : fenêtre d'observation bornée et explicite.
        page.wait_for_timeout(1500)
        assert _draws(page) == before
        page.locator(f"{MASCOTTE} button[data-act='mascotte']").click()
        # Le clic amène à la réponse : le focus arrive sur la zone de réponse.
        page.wait_for_function("() => document.activeElement?.classList.contains('at-asst-area')", timeout=READY_MS)
        assert not errors, errors
    finally:
        context.close()


def test_keyboard_activation_and_visible_focus(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Clavier")
    page, context, _requests, errors = _open(browser, url, slug, entretien(question=QUESTION))
    try:
        _wait_caption(page, "attend ta réponse")
        button = page.locator(f"{MASCOTTE} button[data-act='mascotte']")
        button.focus()
        expect(button).not_to_have_css("outline-style", "none")  # un anneau de focus est visible
        button.press("Enter")
        page.wait_for_function("() => document.activeElement?.classList.contains('at-asst-area')", timeout=READY_MS)
        assert not errors, errors
    finally:
        context.close()


def test_pending_suggestion_is_counted_and_click_reaches_it(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Suggestion en attente")
    _suggest(root, slug)
    page, context, _requests, errors = _open(browser, url, slug, entretien())
    try:
        _wait_caption(page, "une suggestion en attente")
        expect(page.locator(MASCOTTE)).to_have_attribute("data-action", "propose")
        expect(page.locator("[data-role='mascotte-count']")).to_have_text("1", use_inner_text=True)
        page.locator(f"{MASCOTTE} button[data-act='mascotte']").click()
        # Le focus arrive dans la suggestion.
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.at-sugg'))", timeout=READY_MS)
        assert not errors, errors
    finally:
        context.close()


def test_error_is_said(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Erreur")
    page, context, _requests, errors = _open(browser, url, slug, entretien(error="entretien.json illisible"))
    try:
        _wait_caption(page, "erreur de l’assistant")
        expect(page.locator(MASCOTTE)).to_have_attribute("data-tone", "error")
        errs = [e for e in errors if "404" in e or "500" in e]
        assert not errs, errs
    finally:
        context.close()


def test_voice_active_reads_the_question_without_any_new_route(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Voix active")
    page, context, requests, errors = _open(
        browser, url, slug, entretien(question=QUESTION), voice=VOICE_ON, scripts=(FAKE_SPEECH,)
    )
    try:
        speak = page.locator(f"{MASCOTTE} [data-act='speak']")
        speak.wait_for(state="visible", timeout=READY_MS)
        speak.click()
        _wait_caption(page, "lit la question")
        assert page.evaluate("() => window.__spoken") == [QUESTION["text"]]
        speak.click()  # arrête la lecture
        _wait_caption(page, "attend ta réponse")
        posts = [r for r in requests if r.startswith("POST ")]
        assert not posts, f"la lecture n'envoie rien : {posts}"
        assert not [r for r in requests if "voice/" in r and "voice/config" not in r], "aucune autre route vocale"
        assert not errors, errors
    finally:
        context.close()


def test_without_webgl2_caption_and_dot(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Sans WebGL")
    page, context, _requests, errors = _open(browser, url, slug, entretien(question=QUESTION), scripts=(NO_WEBGL2,))
    try:
        _wait_caption(page, "attend ta réponse")
        page.locator(f"{MASCOTTE} .at-mascotte-dot").wait_for(state="visible", timeout=READY_MS)
        expect(page.locator(CANVAS)).to_have_count(0)
        assert not errors, errors
    finally:
        context.close()


def test_reduced_motion_caps_the_cadence_while_thinking(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Mouvement réduit")
    page, context, _requests, errors = _open(
        browser, url, slug, entretien(mine=True), scripts=(COUNT_DRAWS,), reduced=True
    )
    try:
        _wait_caption(page, "réfléchit")
        page.wait_for_function("() => window.__draws >= 5", timeout=15_000)
        page.wait_for_timeout(500)
        before = _draws(page)
        # Mesure d'une cadence : la fenêtre de 1 s fait partie de la définition (images par seconde), elle est donc explicite.
        page.wait_for_timeout(1000)
        assert _draws(page) - before <= 26, "prefers-reduced-motion plafonne la cadence"
        assert not errors, errors
    finally:
        context.close()


def test_captures(browser: Browser, project_on: tuple[str, Path]) -> None:
    """Captures de la mascotte dans l'Atelier, aux deux tailles de référence. Sans `GRIMOIRE_E2E_CAPTURES`, ne fait rien."""
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    url, root = project_on
    slug = _chantier(root, "Captures")
    with_sugg = _chantier(root, "Captures suggestion")
    _suggest(root, with_sugg)
    states = [
        ("atelier-repos", "au repos", entretien(), slug),
        ("atelier-reflechit", "réfléchit", entretien(mine=True), slug),
        ("atelier-question", "attend ta réponse", entretien(question=QUESTION), slug),
        ("atelier-erreur", "erreur de l’assistant", entretien(error="entretien.json illisible"), slug),
        ("atelier-suggestion", "une suggestion en attente", entretien(), with_sugg),
    ]
    for viewport in ((1440, 900), (1024, 768)):
        for name, caption, state, which in states:
            page, context, _requests, _errors = _open(browser, url, which, state, viewport=viewport)
            try:
                _wait_caption(page, caption)
                page.wait_for_function(
                    "() => document.querySelector('[data-role=\"assistant-mascotte\"] canvas')", timeout=READY_MS
                )
                page.wait_for_timeout(2500)
                _capture(page, name, viewport)
            finally:
                context.close()
