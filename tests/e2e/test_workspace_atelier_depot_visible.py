"""Atelier de spec : une suggestion ou un commentaire déposé par l'agent s'affiche sans reprendre le focus.

Un tour d'agent plus court que le pouls (5 s) n'est jamais vu « en cours » et ne change pas les angles couverts : seule l'empreinte
de ``suggestions.json`` / ``comments.json`` (portée par la lecture d'entretien déjà sondée) le révèle. Le dépôt est fait côté
serveur, comme le fait l'outil MCP de l'agent, PENDANT que la page est ouverte et sans aucun tour d'agent visible : rien n'est
cliqué, aucun évènement ``focus`` ni rechargement de page ne se produit. Chaque preuve attend l'état voulu à l'écran.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_comments, workspace_atelier_suggestions

from .conftest import _serve_empty_project

WAIT_MS = 40_000
BRAINSTORM = "02-brainstorm.md"
PARA = "- Une piste à challenger plus tard."
SECTION = "[data-section='brainstorm']"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-depot", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _chantier(root: Path, titre: str) -> str:
    made = new_chantier(root, titre)
    path = root / "_grimoire" / "cadrage" / made.slug / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", PARA), encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str) -> str:
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    return str(next(b for b in phase["blocks"] if b["text"] == PARA)["id"])


def _open(browser: Browser, url: str, slug: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
    page.locator(f"{SECTION} .at-md").first.wait_for(timeout=WAIT_MS)
    try:
        yield page
        assert not errors, errors
    finally:
        context.close()


def test_a_suggestion_deposited_server_side_appears_without_focus_or_reload(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dépôt Suggestion")
    for page in _open(browser, url, slug):
        expect(page.locator(".at-sugg")).to_have_count(0)
        # Les évènements qui relisent le document sont comptés : aucun ne doit servir ici.
        page.evaluate("() => { window.__focusSeen = 0; window.addEventListener('focus', () => { window.__focusSeen += 1; }); }")
        done = workspace_atelier_suggestions.suggest(str(root), slug, _block_id(root, slug), "- Une piste, précisée", "plus net")
        assert done["suggestion"]["etat"] == "proposee"
        expect(page.locator(".at-sugg")).to_have_count(1, timeout=WAIT_MS)
        expect(page.locator(".at-sugg")).to_contain_text("Une piste, précisée")
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("1 suggestion de l’assistant", timeout=WAIT_MS)
        assert page.evaluate("() => window.__focusSeen") == 0, "le document s'est mis à jour sans évènement focus"


def test_a_comment_deposited_server_side_appears_and_is_announced(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dépôt Commentaire")
    for page in _open(browser, url, slug):
        expect(page.locator(f"{SECTION} [data-com]")).to_have_count(0)
        made = workspace_atelier_comments.agent_comment(str(root), slug, "Pourquoi cette piste ?", _block_id(root, slug), "Une piste", 2)
        assert made["ok"] is True, made
        expect(page.locator(f"{SECTION} [data-com]")).to_have_count(1, timeout=WAIT_MS)
        expect(page.locator(f"{SECTION} [data-com]")).to_contain_text("Pourquoi cette piste ?")
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("1 nouveau commentaire", timeout=WAIT_MS)


def test_a_deposit_does_not_cost_the_text_being_typed(browser: Browser, project: tuple[str, Path]) -> None:
    """Un autre bloc en cours d'édition garde sa saisie et son focus quand le dépôt arrive."""
    url, root = project
    slug = _chantier(root, "Dépôt Saisie")
    other = root / "_grimoire" / "cadrage" / slug / BRAINSTORM
    other.write_text(other.read_text(encoding="utf-8") + "\n- Une seconde piste.\n", encoding="utf-8")
    for page in _open(browser, url, slug):
        page.locator(f"{SECTION} .at-blk", has_text="Une seconde piste").click()
        area = page.locator(f"{SECTION} [data-role='block-edit'] textarea")
        expect(area).to_be_focused(timeout=WAIT_MS)
        area.fill("- Une seconde piste, en cours de saisie")
        workspace_atelier_suggestions.suggest(str(root), slug, _block_id(root, slug), "- Une piste, précisée", "plus net")
        expect(page.locator(".at-sugg")).to_have_count(1, timeout=WAIT_MS)
        expect(area).to_have_value("- Une seconde piste, en cours de saisie")
        expect(area).to_be_focused()
