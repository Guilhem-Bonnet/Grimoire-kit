"""Atelier de spec : l'historique des versions, à l'écran (lecture seule).

Un chantier à trois commits : le bouton « Historique » ouvre un volet à la place de l'assistant, une version cliquée met la
feuille en mode comparaison (lignes ajoutées et retirées, signe + lecteur d'écran), Échap ramène le document intact. Un projet
hors dépôt git : le volet le dit clairement. Drapeau éteint : 404.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 ; sans la variable le test de capture ne fait rien (il ne se
skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Ada Lovelace", "GIT_AUTHOR_EMAIL": "ada@example.org",
    "GIT_COMMITTER_NAME": "Ada Lovelace", "GIT_COMMITTER_EMAIL": "ada@example.org",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, check=True, capture_output=True, env=_ENV)


@pytest.fixture(scope="module")
def project_git(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, str]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-hist-git", roots):
        root = roots[0]
        workspace_atelier.set_atelier(root, True)
        made = new_chantier(root, "Dictée vocale")
        base = root / "_grimoire" / "cadrage" / made.slug
        brief, exig = base / "01-brief.md", base / "04-exigences.md"
        brief.write_text("# Brief\n\nDicter une idée au micro.\nSans clavier.\n", encoding="utf-8")
        exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter une idée\n", encoding="utf-8")
        rels = [str(brief.relative_to(root)), str(exig.relative_to(root))]
        _git(root, "add", "--", *rels)
        _git(root, "commit", "-q", "-m", "feat: premier jet du cadrage")
        exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter une idée\n- REQ-002 [Should] : repli navigateur\n", encoding="utf-8")
        _git(root, "add", "--", rels[1])
        _git(root, "commit", "-q", "-m", "feat: ajoute le repli")
        brief.write_text("# Brief\n\nDicter une idée au micro.\nAvec ou sans clavier.\nEn moins de deux secondes.\n", encoding="utf-8")
        _git(root, "add", "--", rels[0])
        _git(root, "commit", "-q", "-m", "fix: précise le brief")
        yield url, root, made.slug


@pytest.fixture(scope="module")
def project_nogit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-hist-nogit", roots):
        root = roots[0]
        shutil.rmtree(root / ".git", ignore_errors=True)
        workspace_atelier.set_atelier(root, True)
        yield url, new_chantier(root, "Sans dépôt").slug


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _serve_empty_project(tmp_path_factory, "atelier-hist-off")


def _open(
    browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900), *, scheme: str = "light"
) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _doc(browser: Browser, url: str, slug: str, viewport: tuple[int, int] = (1440, 900), scheme: str = "light") -> Iterator[Page]:
    for page in _open(browser, url, f"#atelier?chantier={slug}", viewport, scheme=scheme):
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        yield page


def test_flag_off_route_is_404(browser: Browser, project_off: str) -> None:
    for page in _open(browser, project_off):
        status = page.evaluate("async () => (await fetch('/api/workspace/atelier/chantiers/x/history')).status")
        assert status == 404


def test_three_commits_list_compare_and_return(browser: Browser, project_git: tuple[str, Path, str]) -> None:
    url, _root, slug = project_git
    for page in _doc(browser, url, slug):
        page.locator(".at-live[data-live='REQ-002']").wait_for(state="attached", timeout=APPEAR_MS)  # la trace du document est lue
        page.wait_for_timeout(1500)
        before = page.locator(".at-sheet").inner_html()
        page.locator("[data-role='atelier-historique-bouton']").click()
        panel = page.locator("[data-role='atelier-historique']")
        panel.wait_for(state="visible", timeout=APPEAR_MS)
        rows = panel.locator(".at-hist-row")
        rows.first.wait_for(timeout=APPEAR_MS)
        assert rows.count() == 3
        assert page.locator("[data-role='atelier-historique-bouton']").get_attribute("aria-expanded") == "true"
        assert "Aujourd" in panel.locator(".at-hist-day-h").first.inner_text()
        newest = rows.first
        assert "fix: précise le brief" in newest.inner_text() and "+2 −1, Brief" in newest.inner_text() and "Ada Lovelace" in newest.inner_text()
        assert "+" in rows.nth(2).inner_text() and "Brief et Exigences" in rows.nth(2).inner_text()
        assert page.locator(".at-asst").is_hidden()  # le volet prend la place de l'assistant
        newest.click()
        banner = page.locator("[data-role='hist-bandeau']")
        banner.wait_for(timeout=APPEAR_MS)
        assert "Tu regardes la version du" in banner.inner_text() and "Revenir au document" in banner.inner_text()
        assert page.locator(".at-sheet").is_hidden()
        assert newest.get_attribute("aria-current") == "true"
        added = page.locator(".at-cmp-line.is-add")
        removed = page.locator(".at-cmp-line.is-del")
        assert added.count() == 2 and removed.count() == 1
        assert removed.first.locator(".at-cmp-text").inner_text() == "Sans clavier."
        assert removed.first.locator(".at-cmp-mark").inner_text() == "−" and added.first.locator(".at-cmp-mark").inner_text() == "+"
        assert "ligne retirée" in (removed.first.text_content() or "")
        assert page.evaluate("getComputedStyle(document.querySelector('.at-cmp-line.is-del .at-cmp-text')).textDecorationLine") == "line-through"
        # le texte vient du serveur : jamais interprété comme du HTML
        assert page.locator(".at-cmp img, .at-cmp script").count() == 0
        page.keyboard.press("Escape")
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        assert page.locator(".at-sheet").is_visible() and page.locator(".at-sheet").inner_html() == before
        # la version la plus ancienne : tout est ajouté
        rows.nth(2).click()
        page.locator("[data-role='hist-retour']").wait_for(timeout=APPEAR_MS)
        assert page.locator(".at-cmp-line.is-del").count() == 0 and page.locator(".at-cmp-phase").count() == 2
        page.locator("[data-role='hist-retour']").click()
        assert page.locator(".at-sheet").is_visible() and page.locator(".at-sheet").inner_html() == before
        page.locator("[data-role='hist-fermer']").click()
        assert panel.is_hidden() and page.locator(".at-doc-grid.has-hist").count() == 0


def test_keyboard_navigation(browser: Browser, project_git: tuple[str, Path, str]) -> None:
    url, _root, slug = project_git
    for page in _doc(browser, url, slug):
        button = page.locator("[data-role='atelier-historique-bouton']")
        button.focus()
        page.keyboard.press("Enter")
        rows = page.locator(".at-hist-row")
        rows.first.wait_for(timeout=APPEAR_MS)
        rows.first.focus()
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.getAttribute('data-version')") == rows.nth(1).get_attribute("data-version")
        page.keyboard.press("Enter")
        page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
        assert page.evaluate("document.activeElement.getAttribute('data-role')") == "hist-retour"
        page.keyboard.press("Escape")
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        assert page.locator(".at-sheet").is_visible()
        page.locator("[data-role='atelier-historique-bouton']").focus()
        page.keyboard.press("Enter")  # le bouton referme le volet
        assert page.locator("[data-role='atelier-historique']").is_hidden()


def test_working_tree_entry_and_no_overflow_at_800(browser: Browser, project_git: tuple[str, Path, str]) -> None:
    url, root, slug = project_git
    exig = root / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    original = exig.read_text(encoding="utf-8")
    exig.write_text(original + "- REQ-003 [Could] : choix de la langue\n", encoding="utf-8")
    try:
        for scheme in ("light", "dark"):
            for page in _doc(browser, url, slug, (800, 700), scheme):
                page.locator("[data-role='atelier-historique-bouton']").click()
                rows = page.locator(".at-hist-row")
                rows.first.wait_for(timeout=APPEAR_MS)
                assert rows.count() == 4 and rows.first.get_attribute("data-kind") == "working"
                assert "Modifications en cours" in rows.first.inner_text()
                rows.first.click()
                page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
                assert page.locator(".at-cmp-line.is-add").count() == 1
                overflow = page.evaluate("() => { const e = document.scrollingElement; return e.scrollWidth - e.clientWidth; }")
                assert overflow <= 1
                canvas = page.evaluate("() => [...document.querySelectorAll('.at-cmp, .at-hist')].map((n) => n.scrollWidth - n.clientWidth)")
                assert all(delta <= 1 for delta in canvas)
    finally:
        exig.write_text(original, encoding="utf-8")


def test_no_git_says_so(browser: Browser, project_nogit: tuple[str, str]) -> None:
    url, slug = project_nogit
    for page in _doc(browser, url, slug):
        page.locator("[data-role='atelier-historique-bouton']").click()
        empty = page.locator("[data-role='hist-vide']")
        empty.wait_for(timeout=APPEAR_MS)
        assert "dépôt git" in empty.inner_text()
        assert page.locator(".at-hist-row").count() == 0
        page.keyboard.press("Escape")


def test_capture(browser: Browser, project_git: tuple[str, Path, str]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root, slug = project_git
    exig = root / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    original = exig.read_text(encoding="utf-8")
    exig.write_text(original + "- REQ-003 [Could] : choix de la langue\n", encoding="utf-8")
    try:
        for scheme in ("light", "dark"):
            for page in _doc(browser, url, slug, (1440, 900), scheme):
                page.locator("[data-role='atelier-historique-bouton']").click()
                page.locator(".at-hist-row").first.wait_for(timeout=APPEAR_MS)
                page.wait_for_timeout(300)
                page.screenshot(path=str(Path(out) / f"historique-liste-{scheme}.png"))
                page.locator(".at-hist-row").nth(1).click()
                page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
                page.wait_for_timeout(300)
                page.screenshot(path=str(Path(out) / f"historique-comparaison-{scheme}.png"))
        for page in _doc(browser, url, slug):
            page.locator("[data-role='atelier-historique-bouton']").click()
            page.locator(".at-hist-row").first.wait_for(timeout=APPEAR_MS)
            page.keyboard.press("Escape")
    finally:
        exig.write_text(original, encoding="utf-8")
