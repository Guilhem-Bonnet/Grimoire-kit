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
    docs: list[str] = []  # les lectures du document du chantier (rechargements)
    page.on("response", lambda r: pulses.append(r.url) if r.url.split("?")[0].endswith(f"/chantiers/{slug}/entretien") else None)
    page.on("response", lambda r: docs.append(r.url) if r.request.method == "GET" and r.url.split("?")[0].endswith(f"/chantiers/{slug}") else None)
    page.docs = docs  # type: ignore[attr-defined]
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


def test_an_own_block_edit_with_a_comment_does_not_trigger_an_outside_reload(browser: Browser, project: tuple[str, Path]) -> None:
    """L'écriture propre met le document à jour sur place (avancement, fils) : ni le pouls ni la synchro ne rechargent en plus."""
    url, root = project
    slug = _chantier(root, "Statut Edition")
    workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug), "texte": "Une question", "quote": "Une piste"}, "humain")
    for page, pulses in _page(browser, url, slug):
        page.locator("[data-com]").first.wait_for(timeout=WAIT_MS)
        docs: list[str] = page.docs  # type: ignore[attr-defined]
        since = len(pulses)
        before = len(docs)
        page.locator(".at-blk", has_text="Une piste").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill(f"{PARA} Réécrite.")
        area.press("Control+Enter")
        expect(page.locator(STATUS)).to_have_text("Bloc enregistré.", timeout=WAIT_MS)
        _after_two_pulses(page, pulses, since)
        # Les deux lectures du geste lui-même (avancement, fils) ; une troisième serait un « dépôt extérieur » imaginaire.
        assert len(docs) - before <= 2, f"lectures du document après le geste : {len(docs) - before}"


def test_attaching_an_orphan_thread_announces_in_status_and_notification(browser: Browser, project: tuple[str, Path]) -> None:
    """« Rattacher ici » en choisissant le bloc : le mode de choix survit au pouls (aucun rechargement), puis le message est annoncé."""
    url, root = project
    slug = _chantier(root, "Statut Rattachement")
    path = root / "_grimoire" / "cadrage" / slug / BRAINSTORM
    first, second = "Un premier paragraphe sur la touche maintenue.", "Un second paragraphe indépendant."
    path.write_text(path.read_text(encoding="utf-8").replace(PARA, f"{first}\n\n{second}"), encoding="utf-8")
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    block = str(next(b for b in phase["blocks"] if b["text"] == first)["id"])
    made = workspace_atelier_comments.create(root, slug, {"block_id": block, "texte": "Pourquoi ?", "quote": "touche maintenue"}, "humain")
    cid = str(made["comment"]["id"])
    for page, pulses in _page(browser, url, slug):
        page.locator(f"[data-com='{cid}']").wait_for(timeout=WAIT_MS)
        # Réécrit hors du cockpit, le passage ET son voisin : le fil devient orphelin.
        path.write_text(path.read_text(encoding="utf-8").replace(first, "Un tout autre texte.").replace(second, f"{second} (revu)"), encoding="utf-8")
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_function(f"() => document.querySelector(\"[data-com='{cid}']\")?.getAttribute('data-anchor') === 'orphelin'", timeout=WAIT_MS)
        page.locator(f"[data-com='{cid}'] [data-act='attach']").click()
        expect(page.locator(".at-sheet.is-picking")).to_have_count(1)
        # Le mode de choix dure : aucune lecture du document (pouls, synchro) ne le défait ni ne remplace le bloc à choisir.
        docs: list[str] = page.docs  # type: ignore[attr-defined]
        since, before = len(pulses), len(docs)
        _after_two_pulses(page, pulses, since)
        assert len(docs) == before, "le document a été relu pendant le choix du bloc"
        page.locator(".at-blk", has_text="(revu)").focus()
        page.keyboard.press("Enter")
        expect(page.locator(STATUS)).to_have_text("Commentaire rattaché au bloc.", timeout=WAIT_MS)
        expect(page.locator(TOASTS)).to_contain_text("rattaché au bloc", timeout=WAIT_MS)
        expect(page.locator(f"[data-com='{cid}']")).to_have_attribute("data-anchor", "ancre", timeout=WAIT_MS)


def test_a_message_repeated_identically_is_announced_again(browser: Browser, project: tuple[str, Path]) -> None:
    """Deux refus de suite : même texte d'état. Le second repasse par le vide (lecteur d'écran) et re-pose la notification."""
    url, root = project
    slug = _chantier(root, "Statut Répété")
    path = root / "_grimoire" / "cadrage" / slug / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + "\n\n- Une autre piste.\n", encoding="utf-8")
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    ids = [str(next(b for b in phase["blocks"] if b["text"] == text)["id"]) for text in (PARA, "- Une autre piste.")]
    cards = [workspace_atelier_suggestions.deposit(root, slug, {"block_id": i, "texte": f"{t} Précisée."})["suggestion"]["id"] for i, t in zip(ids, (PARA, "- Une autre piste."), strict=True)]
    message = "Suggestion refusée : le document n’a pas changé."
    for page, _pulses in _page(browser, url, slug):
        page.evaluate(
            "() => { window.__seen = []; new MutationObserver(() => window.__seen.push(document.querySelector(\"[data-role='atelier-status']\").textContent))"
            ".observe(document.querySelector(\"[data-role='atelier-status']\"), { childList: true, characterData: true, subtree: true }); }"
        )
        toast_ids: list[str] = []
        for sid in cards:
            card = page.locator(f"[data-sugg='{sid}']")
            card.wait_for(timeout=WAIT_MS)
            card.get_by_role("button", name="Refuser").click()
            page.get_by_role("button", name="Confirmer le refus").click()
            expect(card).to_have_count(0, timeout=WAIT_MS)
            expect(page.locator(TOASTS)).to_contain_text(message, timeout=WAIT_MS)
            toast_ids.append(str(page.locator(f"{TOASTS} [data-toast]", has_text=message).get_attribute("data-toast")))
            expect(page.locator(STATUS)).to_have_text(message)
        assert toast_ids[0] != toast_ids[1], "le second message identique n'a pas été ré-annoncé en notification"
        seen: list[str] = page.evaluate("() => window.__seen")
        assert "" in seen[seen.index(message) + 1 :], "le texte d'état n'est pas repassé par le vide avant d'être ré-annoncé"
