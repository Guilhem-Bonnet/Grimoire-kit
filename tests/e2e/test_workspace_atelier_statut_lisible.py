"""Atelier de spec : le message d'un geste de la personne reste lisible, et son propre geste n'est jamais pris pour un dépôt extérieur.

La ligne d'état (``[data-role='atelier-status']``) est une zone d'annonce pour lecteurs d'écran : un rechargement sans message
(synchro, pouls) ne doit jamais la vider ni la remplacer. Et après une écriture propre (décision sur une suggestion, fil résolu,
réponse guidée), le pouls suivant ne recharge pas le document pour un « dépôt extérieur » et n'annonce ni « suggestion de
l'assistant » ni « nouveau commentaire ».

Fenêtre d'observation bornée : deux réponses du pouls (``…/entretien``) APRÈS le geste. La seconde ne part qu'une fois la
première traitée (le pouls se réarme après l'avoir traitée) : tout rechargement que la première aurait déclenché a eu lieu.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_comments, workspace_atelier_suggestions

from .conftest import _serve_empty_project
from .test_workspace_atelier_assistant import _init, _serve

WAIT_MS = 40_000
BRAINSTORM = "02-brainstorm.md"
PARA = "- Une piste à discuter."
STATUS = "[data-role='atelier-status']"
TOASTS = "[data-role='atelier-toasts']"
OUTSIDE = ("suggestion de l’assistant", "suggestions de l’assistant", "nouveau commentaire", "nouveaux commentaires")


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-statut", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


@pytest.fixture(scope="module")
def sans_agent(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, str]]:
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    root, slug = _init(tmp_path_factory, "statut-guide", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root, slug


def _chantier(root: Path, titre: str) -> str:
    made = new_chantier(root, titre)
    path = root / "_grimoire" / "cadrage" / made.slug / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", PARA), encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str) -> str:
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    return str(next(b for b in phase["blocks"] if b["text"] == PARA)["id"])


def _page(browser: Browser, url: str, slug: str) -> Iterator[tuple[Page, list[str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    pulses: list[str] = []
    page.on("response", lambda r: pulses.append(r.url) if r.url.split("?")[0].endswith(f"/chantiers/{slug}/entretien") else None)
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
    page.locator("[data-section='brainstorm'] .at-md, [data-section='brief'] .at-md").first.wait_for(timeout=WAIT_MS)
    try:
        yield page, pulses
    finally:
        context.close()


def _after_two_pulses(page: Page, pulses: list[str], since: int) -> None:
    """Attend deux réponses du pouls arrivées après `since` (borne : le pouls lent est à 5 s, plus la marge d'un runner chargé)."""
    deadline = time.monotonic() + 45
    while len(pulses) < since + 2:
        assert time.monotonic() < deadline, "le pouls de l'assistant n'a pas répondu deux fois"
        page.wait_for_timeout(200)


def _check_message_kept_and_no_outside(page: Page, pulses: list[str], since: int, message: str) -> None:
    _after_two_pulses(page, pulses, since)
    expect(page.locator(STATUS)).to_have_text(message)  # ni vidée ni remplacée par le rechargement du pouls
    text = page.locator(TOASTS).inner_text()
    assert not any(phrase in text for phrase in OUTSIDE), f"son propre geste annoncé comme dépôt extérieur : {text!r}"


def test_a_decision_keeps_its_message(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Statut Décision")
    made = workspace_atelier_suggestions.deposit(root, slug, {"block_id": _block_id(root, slug), "texte": f"{PARA} Précisée."})
    for page, pulses in _page(browser, url, slug):
        card = page.locator(f"[data-sugg='{made['suggestion']['id']}']")
        card.wait_for(timeout=WAIT_MS)
        since = len(pulses)
        card.get_by_role("button", name="Refuser").click()
        page.get_by_role("button", name="Confirmer le refus").click()
        message = "Suggestion refusée : le document n’a pas changé."
        expect(page.locator(STATUS)).to_have_text(message, timeout=WAIT_MS)
        expect(card).to_have_count(0, timeout=WAIT_MS)
        _check_message_kept_and_no_outside(page, pulses, since, message)


def test_a_resolved_thread_keeps_its_message(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Statut Fil")
    made = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug), "texte": "Une question", "quote": "Une piste"}, "humain")
    for page, pulses in _page(browser, url, slug):
        thread = page.locator(f"[data-com='{made['comment']['id']}']")
        thread.wait_for(timeout=WAIT_MS)
        since = len(pulses)
        thread.get_by_role("button", name="Résoudre").click()
        expect(page.locator(STATUS)).to_contain_text("Fil résolu", timeout=WAIT_MS)
        message = page.locator(STATUS).inner_text()
        _check_message_kept_and_no_outside(page, pulses, since, message)


def test_a_guided_answer_keeps_its_message(browser: Browser, sans_agent: tuple[str, Path, str]) -> None:
    url, _root, slug = sans_agent
    for page, pulses in _page(browser, url, slug):
        page.locator("[data-act='start']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=WAIT_MS)
        since = len(pulses)
        page.locator(".at-asst-area").fill("Les mainteneurs du kit.")
        page.locator(".at-asst-area").press("Enter")
        page.locator(".at-sheet", has_text="Les mainteneurs du kit.").wait_for(timeout=WAIT_MS)
        expect(page.locator(STATUS)).not_to_be_empty(timeout=WAIT_MS)
        message = page.locator(STATUS).inner_text()
        _check_message_kept_and_no_outside(page, pulses, since, message)
