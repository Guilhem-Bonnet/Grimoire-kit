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

from playwright.sync_api import Browser, Page, expect

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


# Résolue quand le contenu du nœud n'a pas changé pendant 30 images d'animation de suite (un redessin en place est fini).
_QUIET = """(sel) => new Promise((resolve) => {
    let last = null;
    let same = 0;
    const tick = () => {
        const html = document.querySelector(sel)?.innerHTML ?? null;
        same = html !== null && html === last ? same + 1 : 0;
        last = html;
        if (same >= 30) resolve(true); else requestAnimationFrame(tick);
    };
    tick();
})"""


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
        # La feuille se redessine en place quand la trace arrive : on attend qu'elle ne bouge plus (30 images d'animation de suite
        # identiques) avant d'en prendre l'instantané, au lieu d'une minuterie.
        page.wait_for_function(_QUIET, arg=".at-sheet", timeout=APPEAR_MS)
        before = page.locator(".at-sheet").inner_html()
        page.locator("[data-role='atelier-historique-bouton']").click()
        panel = page.locator("[data-role='atelier-historique']")
        panel.wait_for(state="visible", timeout=APPEAR_MS)
        rows = panel.locator(".at-hist-row")
        expect(rows).to_have_count(3)
        expect(page.locator("[data-role='atelier-historique-bouton']")).to_have_attribute("aria-expanded", "true")
        expect(panel.locator(".at-hist-day-h").first).to_contain_text("Aujourd", use_inner_text=True)
        newest = rows.first
        expect(newest).to_contain_text("fix: précise le brief", use_inner_text=True)
        expect(newest).to_contain_text("+2 −1, Brief", use_inner_text=True)
        expect(newest).to_contain_text("Ada Lovelace", use_inner_text=True)
        expect(rows.nth(2)).to_contain_text("+", use_inner_text=True)
        expect(rows.nth(2)).to_contain_text("Brief et Exigences", use_inner_text=True)
        expect(page.locator(".at-asst")).to_be_hidden()  # le volet prend la place de l'assistant
        newest.click()
        banner = page.locator("[data-role='hist-bandeau']")
        banner.wait_for(timeout=APPEAR_MS)
        expect(banner).to_contain_text("Tu regardes la version du", use_inner_text=True)
        expect(banner).to_contain_text("Revenir au document", use_inner_text=True)
        expect(page.locator(".at-sheet")).to_be_hidden()
        expect(newest).to_have_attribute("aria-current", "true")
        added = page.locator(".at-cmp-line.is-add")
        removed = page.locator(".at-cmp-line.is-del")
        expect(added).to_have_count(2)
        expect(removed).to_have_count(1)
        expect(removed.first.locator(".at-cmp-text")).to_have_text("Sans clavier.", use_inner_text=True)
        expect(removed.first.locator(".at-cmp-mark")).to_have_text("−", use_inner_text=True)
        expect(added.first.locator(".at-cmp-mark")).to_have_text("+", use_inner_text=True)
        expect(removed.first).to_contain_text("ligne retirée")
        expect(removed.first.locator(".at-cmp-text")).to_have_css("text-decoration-line", "line-through")
        # le texte vient du serveur : jamais interprété comme du HTML
        expect(page.locator(".at-cmp img, .at-cmp script")).to_have_count(0)
        page.keyboard.press("Escape")
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        expect(page.locator(".at-sheet")).to_be_visible()
        page.wait_for_function("(before) => document.querySelector('.at-sheet').innerHTML === before", arg=before, timeout=APPEAR_MS)
        # la version la plus ancienne : tout est ajouté
        rows.nth(2).click()
        page.locator("[data-role='hist-retour']").wait_for(timeout=APPEAR_MS)
        expect(page.locator(".at-cmp-phase")).to_have_count(2)
        expect(page.locator(".at-cmp-line.is-del")).to_have_count(0)
        page.locator("[data-role='hist-retour']").click()
        expect(page.locator(".at-sheet")).to_be_visible()
        page.wait_for_function("(before) => document.querySelector('.at-sheet').innerHTML === before", arg=before, timeout=APPEAR_MS)
        page.locator("[data-role='hist-fermer']").click()
        expect(panel).to_be_hidden()
        expect(page.locator(".at-doc-grid.has-hist")).to_have_count(0)


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
        expect(rows.nth(1)).to_be_focused()
        page.keyboard.press("Enter")
        page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
        expect(page.locator("[data-role='hist-retour']")).to_be_focused()
        page.keyboard.press("Escape")
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        expect(page.locator(".at-sheet")).to_be_visible()
        page.locator("[data-role='atelier-historique-bouton']").focus()
        page.keyboard.press("Enter")  # le bouton referme le volet
        expect(page.locator("[data-role='atelier-historique']")).to_be_hidden()


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
                expect(rows).to_have_count(4)
                expect(rows.first).to_have_attribute("data-kind", "working")
                expect(rows.first).to_contain_text("Modifications en cours", use_inner_text=True)
                rows.first.click()
                page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
                expect(page.locator(".at-cmp-line.is-add")).to_have_count(1)
                # La mise en page se pose en place : on attend l'état voulu (aucun débordement), le test échoue s'il n'est jamais atteint.
                page.wait_for_function("() => { const e = document.scrollingElement; return e.scrollWidth - e.clientWidth <= 1; }", timeout=APPEAR_MS)
                page.wait_for_function("() => [...document.querySelectorAll('.at-cmp, .at-hist')].every((n) => n.scrollWidth - n.clientWidth <= 1)", timeout=APPEAR_MS)
    finally:
        exig.write_text(original, encoding="utf-8")


def test_no_git_says_so(browser: Browser, project_nogit: tuple[str, str]) -> None:
    url, slug = project_nogit
    for page in _doc(browser, url, slug):
        page.locator("[data-role='atelier-historique-bouton']").click()
        empty = page.locator("[data-role='hist-vide']")
        empty.wait_for(timeout=APPEAR_MS)
        expect(empty).to_contain_text("dépôt git", use_inner_text=True)
        expect(page.locator(".at-hist-row")).to_have_count(0)
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
