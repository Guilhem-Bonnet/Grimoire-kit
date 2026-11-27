"""Mascotte permanente du cockpit (#1197) : visible dès l'accueil, sans rien ouvrir, sur un VRAI `grimoire cockpit serve`.

Le cockpit sert un registre de DEUX projets dont un seul porte l'ancien drapeau par projet (`cockpit.test_features.mascotte`) :
la préférence de machine reprend ce choix pour tous les projets et la vue « Tous les projets ». HOME et état du cockpit
sont isolés, le port est choisi par le noyau (jamais 8420 ni 8431). `GRIMOIRE_E2E_CAPTURES=<dossier>` écrit les captures ;
sans la variable, le test de capture ne fait rien.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, BrowserContext, Page, expect

from grimoire.tools import workspace_atelier

from .conftest import _alive, _free_port, _wait_ready

READY_MS = 30_000
ANCHOR = "[data-role='mascotte-anchor']"
CAPTION = "[data-role='mascotte-anchor-caption']"


@pytest.fixture(scope="module")
def cockpit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str, str]]:
    """`(url, slug du projet SANS drapeau, slug du projet AVEC l'ancien drapeau)` sur un cockpit isolé."""
    roots = [tmp_path_factory.mktemp(f"anchor-{letter}") / f"projet-{letter}" for letter in ("a", "b")]
    for root in roots:
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", root.name, "--backend", "local"],
            cwd=str(root),
            check=False,
            capture_output=True,
            timeout=180,
        )
        if not (root / "_grimoire" / "kit").is_dir():
            pytest.skip("`grimoire init` indisponible ici")
    workspace_atelier.set_mascotte(roots[1], True)  # l'ancien drapeau, dans le seul projet B
    port = _free_port()
    assert port not in (8420, 8431)
    home = tmp_path_factory.mktemp("anchor-home")
    env = dict(os.environ)
    env.update(
        {"HOME": str(home), "GRIMOIRE_COCKPIT_HOME": str(home / "cockpit"), "GRIMOIRE_NO_COCKPIT": "1", "NO_COLOR": "1"}
    )
    for root in roots:
        subprocess.run(
            [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    registry = json.loads((home / "cockpit" / "registry.json").read_text(encoding="utf-8"))
    slugs = [next((str(e.get("slug", "")) for e in registry if e.get("path") == str(root)), "") for root in roots]
    if not all(slugs):
        pytest.skip("slugs introuvables au registre du cockpit après `add`")
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", slugs[0], slugs[1]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid)


def _context(browser: Browser, scheme: str = "dark", viewport: tuple[int, int] = (1440, 900)) -> BrowserContext:
    return browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        color_scheme=scheme,
        reduced_motion="no-preference",  # type: ignore[arg-type]
    )


def _open(context: BrowserContext, url: str, query: str) -> tuple[Page, list[str], list[str]]:
    page = context.new_page()
    requests: list[str] = []
    errors: list[str] = []
    page.on("request", lambda r: requests.append(f"{r.method} {r.url}") if "/api/" in r.url else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"{url}/workspace/index.html{query}#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=READY_MS)
    return page, requests, errors


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _mascotte_flag(page: Page, slug: str, wanted: bool) -> None:
    """Attend que le serveur donne ``wanted`` pour la mascotte de ``slug`` (la préférence est écrite à la suite du geste)."""
    page.wait_for_function(
        "async ([p, want]) => (await (await fetch(`/api/workspace/features?project=${p}`)).json()).mascotte === want",
        arg=[slug, wanted],
        timeout=READY_MS,
    )


def _anchor_ready(page: Page) -> None:
    page.locator(ANCHOR).wait_for(state="visible", timeout=READY_MS)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_visible_on_the_home_page_of_a_project_without_the_flag_and_nothing_opened(
    browser: Browser, cockpit: tuple[str, str, str], scheme: str
) -> None:
    url, slug_without, _slug_with = cockpit
    context = _context(browser, scheme)
    try:
        page, requests, errors = _open(context, url, f"?project={slug_without}")
        page.wait_for_function("(s) => window.GrimoireWorkspace.theme === s", arg=scheme, timeout=READY_MS)
        _anchor_ready(page)
        # Aucune requête de plus au premier rendu : la réponse de `features` est celle que la coque lit déjà (lue AVANT que
        # le test ne fasse ses propres appels).
        features = [r for r in requests if r.startswith("GET") and "/workspace/features" in r]
        assert len(features) == 1, features
        # Le projet servi N'a PAS l'ancien drapeau : c'est la préférence de machine (reprise du projet B) qui l'allume.
        assert (
            page.evaluate(
                "async (p) => (await (await fetch(`/api/workspace/features?project=${p}`)).json()).mascotte",
                slug_without,
            )
            is True
        )
        expect(page.locator(CAPTION)).to_have_text("Mascotte : au repos", use_inner_text=True)
        expect(page.locator(ANCHOR)).to_have_js_property("tagName", "BUTTON")  # un simple bouton, accessible au clavier
        expect(page.locator(f"{ANCHOR} canvas")).to_have_count(0)  # aucun canevas WebGL hors du chat et de l'Atelier
        expect(page.locator(".chat-mascotte")).to_have_count(0)  # le dock n'est pas ouvert : rien n'a été chargé dans le chat
        assert not [r for r in requests if r.endswith(("/mascotte.js", "/mascotte_shader.js"))], "ni rendu ni shader"
        _capture(page, f"accueil-projet-{'sombre' if scheme == 'dark' else 'clair'}")
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_visible_in_the_global_view(browser: Browser, cockpit: tuple[str, str, str], scheme: str) -> None:
    url, _a, _b = cockpit
    context = _context(browser, scheme)
    try:
        page, _requests, errors = _open(context, url, "?scope=all")
        page.wait_for_function("() => window.GrimoireWorkspace.scope.all === true", timeout=READY_MS)
        _anchor_ready(page)
        expect(page.locator(CAPTION)).to_have_text("Mascotte : au repos", use_inner_text=True)
        _capture(page, f"accueil-global-{'sombre' if scheme == 'dark' else 'clair'}")
        assert not errors, errors
    finally:
        context.close()


def test_the_chat_watch_starts_after_the_first_render_and_a_click_opens_the_chat(
    browser: Browser, cockpit: tuple[str, str, str]
) -> None:
    url, slug_without, _b = cockpit
    context = _context(browser)
    try:
        page, requests, errors = _open(context, url, f"?project={slug_without}")
        _anchor_ready(page)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not [r for r in requests if "/chat/stream" in r]:
            page.wait_for_timeout(200)
        assert len([r for r in requests if "/chat/stream" in r]) == 1, "la veille du fil part une fois, après le rendu"
        page.locator(ANCHOR).click()
        page.locator(".chat-input").wait_for(state="visible", timeout=READY_MS)
        # Un seul projet : le sélecteur « Parler au projet » n'existe pas à l'écran (il est réservé à la vue globale).
        expect(page.locator("select.chat-target")).to_be_hidden()
        expect(page.locator(".chat-targetbar")).to_be_hidden()
        assert len([r for r in requests if "/chat/stream" in r]) == 1, "ouvrir le chat ne rouvre pas un second flux"
        assert not errors, errors
    finally:
        context.close()


def test_the_target_selector_exists_in_the_global_view_only(browser: Browser, cockpit: tuple[str, str, str]) -> None:
    url, _a, _b = cockpit
    context = _context(browser)
    try:
        page, _requests, errors = _open(context, url, "?scope=all")
        _anchor_ready(page)
        page.locator(ANCHOR).click()
        page.locator("select.chat-target").wait_for(state="visible", timeout=READY_MS)
        expect(page.locator(".chat-targetbar")).to_be_visible()
        assert not errors, errors
    finally:
        context.close()


def test_the_launcher_stays_on_every_page_and_is_reachable_by_keyboard(
    browser: Browser, cockpit: tuple[str, str, str]
) -> None:
    url, slug_without, _b = cockpit
    context = _context(browser)
    try:
        page, _requests, errors = _open(context, url, f"?project={slug_without}")
        _anchor_ready(page)
        for space in ("flotte", "travail", "savoir", "fonctions-test"):
            page.evaluate("(s) => window.GrimoireWorkspace.goto(s)", space)
            expect(page.locator(ANCHOR), f"présent sur {space}").to_be_visible()
        page.locator(ANCHOR).focus()
        expect(page.locator(ANCHOR)).not_to_have_css("outline-style", "none")  # un anneau de focus est visible
        expect(page.locator(ANCHOR)).to_have_attribute("aria-label", re.compile(r"^Mascotte : au repos.*Ouvrir le chat", re.DOTALL))
        page.keyboard.press("Enter")
        page.locator(".chat-input").wait_for(state="visible", timeout=READY_MS)
        assert not errors, errors
    finally:
        context.close()


def test_the_settings_screen_says_the_scope_and_the_toggle_follows_everywhere(
    browser: Browser, cockpit: tuple[str, str, str]
) -> None:
    url, slug_without, slug_with = cockpit
    context = _context(browser)
    try:
        page, _requests, errors = _open(context, url, f"?project={slug_without}")
        _anchor_ready(page)
        page.evaluate("() => window.GrimoireWorkspace.goto('fonctions-test')")
        card = page.locator("section.ft-card[aria-label='Mascotte']")
        card.wait_for(timeout=READY_MS)
        expect(card).to_contain_text("Portée : toute cette machine", use_inner_text=True)
        expect(card).to_contain_text("tous les projets", use_inner_text=True)
        expect(page.locator("section.ft-card[aria-label='Atelier de spec']")).to_contain_text("Portée : ce projet seulement", use_inner_text=True)
        _capture(page, "fonctions-en-test-portee")
        switch = card.locator("button[role='switch']")
        expect(switch).to_have_attribute("aria-checked", "true")
        switch.click()  # éteint : partout, sans recharger
        page.locator(ANCHOR).wait_for(state="detached", timeout=READY_MS)
        for slug in (slug_without, slug_with):
            _mascotte_flag(page, slug, False)  # éteint pour ce projet aussi
        switch.click()  # rallumé : la mascotte revient tout de suite
        page.locator(ANCHOR).wait_for(state="visible", timeout=READY_MS)
        for slug in (slug_without, slug_with):
            _mascotte_flag(page, slug, True)
        assert not errors, errors
    finally:
        context.close()


def test_captures_1024(browser: Browser, cockpit: tuple[str, str, str]) -> None:
    if not os.environ.get("GRIMOIRE_E2E_CAPTURES"):
        return
    url, slug_without, _b = cockpit
    for scheme in ("dark", "light"):
        context = _context(browser, scheme, (1024, 768))
        try:
            page, _requests, _errors = _open(context, url, f"?project={slug_without}")
            _anchor_ready(page)
            page.wait_for_timeout(1500)
            _capture(page, f"accueil-projet-{'sombre' if scheme == 'dark' else 'clair'}-1024x768")
        finally:
            context.close()
