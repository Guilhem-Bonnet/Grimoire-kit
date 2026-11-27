"""Atelier de spec, lecture et navigation : le plan du document, la recherche Ctrl+F et le menu « Document » (copie, fichier, impression).

Chaque preuve est lue à l'écran, dans un vrai navigateur. Tout est en lecture seule : le Markdown copié ou téléchargé est celui du
fichier tel que stocké (comparé aux fichiers du chantier sur disque), la recherche ne laisse rien dans la feuille une fois fermée
(son HTML est identique), la feuille d'impression ne garde que le document (média ``print`` émulé, puis un vrai PDF).

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 (clair et sombre) ; sans la variable le test de capture ne fait rien
(il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import APPEAR_MS, _box, _chantier, _folder

PLAN_KEY = "grimoire.atelier.plan"
TOGGLE = "[data-role='plan-toggle']"
PLAN = "[data-role='atelier-plan']"
FIND = "[data-role='atelier-recherche']"
INPUT = "[data-role='find-input']"
COUNT = "[data-role='find-count']"
MENU = "[data-role='document-menu']"
# La feuille sans les lignes d'état réel des exigences, que le suivi en direct redessine de lui-même (rien à voir avec la recherche).
SNAPSHOT = "() => { const c = document.querySelector('.at-sheet').cloneNode(true); c.querySelectorAll('[data-live]').forEach((n) => n.remove()); return c.innerHTML; }"
NO_HIGHLIGHT_API ="Object.defineProperty(CSS, 'highlights', { value: undefined, configurable: true });"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-lecture", roots):
        workspace_atelier.set_atelier(roots[0], True)
        workspace_atelier.set_mascotte(roots[0], True)
        yield url, roots[0]


def _chantier_rempli(root: Path, titre: str) -> str:
    """Un chantier long : le brief a une rubrique remplie et trois au gabarit ; le brainstorm est long."""
    slug = _chantier(root, titre, long=True)
    brief = _folder(root, slug) / "01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("> À compléter — le problème.", "Les équipes perdent le fil des décisions de cadrage."), encoding="utf-8")
    return slug


def _stored(root: Path, slug: str) -> str:
    """Le document tel que le serveur doit le donner : les fichiers de phase, chacun terminé par un saut de ligne, séparés d'une ligne vide."""
    texts = [p.read_text(encoding="utf-8") for p in sorted(_folder(root, slug).glob("0*.md"))]
    return "\n".join(t if t.endswith("\n") else t + "\n" for t in texts if t.strip())


def _words(markdown: str) -> int:
    text = re.sub(r"\A﻿?---\r?\n.*?\r?\n---\r?\n?", "", markdown, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL).replace("```", " ")
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text))


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _enter(page: Page, slug: str) -> None:
    page.evaluate("(h) => { location.hash = h; }", f"#atelier?chantier={slug}")
    page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)


def _page(
    browser: Browser, url: str, slug: str, *, viewport: tuple[int, int] = (1440, 900), scheme: str = "light", highlight_api: bool = True, block_storage: bool = False
) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce", color_scheme=scheme, accept_downloads=True)  # type: ignore[arg-type]
    if not highlight_api:
        context.add_init_script(NO_HIGHLIGHT_API)
    if block_storage:
        context.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('bloqué'); } });")
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        page.locator(".at-plan-link").first.wait_for(state="attached", timeout=APPEAR_MS)
        yield page
    finally:
        context.close()


def _plan_open(page: Page) -> bool:
    return page.locator(TOGGLE).get_attribute("aria-expanded") == "true"


def _ensure_plan(page: Page) -> None:
    if not _plan_open(page):
        page.locator(TOGGLE).click()
    assert page.locator(PLAN).is_visible()


def _canvas_overflow(page: Page) -> bool:
    return bool(page.evaluate("() => { const c = document.getElementById('canvas'); return c.scrollWidth > c.clientWidth + 1 || document.documentElement.scrollWidth > document.documentElement.clientWidth + 1; }"))


# ── Plan du document ────────────────────────────────────────────────────────


def test_the_outline_lists_phases_and_sections_with_their_state(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Plan etat")
    for page in _page(browser, url, slug):
        _ensure_plan(page)
        assert page.locator(f"{PLAN} nav").get_attribute("aria-label") == "Plan du document"
        phases = page.locator(f"{PLAN} .at-plan-phase-link")
        assert phases.count() == 5
        assert [_flat(t) for t in phases.all_text_contents()][:3] == ["Brief — à compléter", "Brainstorm — à compléter", "Compréhension — à compléter"]
        # Les rubriques viennent du fichier ; l'état, de l'avancement du serveur.
        assert page.locator(f"{PLAN} [data-plan='brief/0']").get_attribute("data-state") == "rempli"
        assert page.locator(f"{PLAN} [data-plan='brief/1']").get_attribute("data-state") == "vide"
        assert "Le problème — remplie" in _flat(page.locator(f"{PLAN} [data-plan='brief/0']").text_content())
        assert "L'utilisateur — à compléter" in _flat(page.locator(f"{PLAN} [data-plan='brief/1']").text_content())
        assert page.locator(f"{PLAN} [data-plan='brainstorm/0']").get_attribute("data-state") == "rempli"  # « Pistes »


def test_a_click_scrolls_to_the_section_and_the_current_item_follows_the_scroll(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Plan defilement")
    for page in _page(browser, url, slug):
        _ensure_plan(page)
        current = f"{PLAN} [aria-current='location']"
        current_is = "([s, id]) => document.querySelector(s)?.getAttribute('data-plan') === id"
        page.wait_for_function(current_is, arg=[current, "brief"], timeout=APPEAR_MS)
        # Clic sur « Questions ouvertes » du brainstorm : la rubrique arrive sous la barre collante et prend le focus.
        page.locator(f"{PLAN} [data-plan='brainstorm/1']").click()
        bar = _box(page, ".at-phasebar")
        heading = page.locator("[data-section='brainstorm'] .at-rub").nth(1)
        page.wait_for_function(
            "([sel, top]) => { const n = document.querySelectorAll(sel)[1]; const y = n.getBoundingClientRect().top; return y >= top - 4 && y < top + 220; }",
            arg=["[data-section='brainstorm'] .at-rub", bar["y"] + bar["height"]],
            timeout=APPEAR_MS,
        )
        assert heading.inner_text().strip().startswith("Questions")
        page.wait_for_function(current_is, arg=[current, "brainstorm/1"], timeout=APPEAR_MS)
        # Le focus arrive sur la rubrique à la fin du défilement : on l'attend, on ne le suppose pas déjà posé.
        page.wait_for_function("() => document.activeElement.classList.contains('at-rub')", timeout=APPEAR_MS)
        # Un clic sur une phase passe par la barre des phases : l'adresse suit.
        page.locator(f"{PLAN} [data-plan='exigences']").click()
        page.wait_for_function("() => location.hash.includes('phase=exigences')", timeout=APPEAR_MS)
        # Au défilement, l'élément courant suit : retour tout en haut, puis tout en bas.
        page.evaluate("() => { document.getElementById('canvas').scrollTop = 0; }")
        page.wait_for_function(current_is, arg=[current, "brief"], timeout=APPEAR_MS)
        page.evaluate("() => { const c = document.getElementById('canvas'); c.scrollTop = c.scrollHeight; }")
        last = page.evaluate("() => [...document.querySelectorAll('.at-plan-link')].pop().getAttribute('data-plan')")
        page.wait_for_function(current_is, arg=[current, last], timeout=APPEAR_MS)
        # Un seul élément courant à la fois.
        assert page.locator(current).count() == 1


def test_the_outline_fold_is_remembered_and_follows_the_width(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Plan repli")
    # Sous 1100 px : replié d'office, rien ne déborde, le bouton le rouvre au-dessus de la feuille.
    for page in _page(browser, url, slug, viewport=(1024, 768)):
        assert not _plan_open(page) and not page.locator(PLAN).is_visible()
        assert page.locator(TOGGLE).get_attribute("aria-pressed") == "false"
        page.locator(TOGGLE).click()
        assert _plan_open(page) and page.locator(PLAN).is_visible()
        assert page.evaluate("(k) => localStorage.getItem(k)", PLAN_KEY) == "open"
        assert not _canvas_overflow(page)
        page.reload()
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-plan-link").first.wait_for(state="attached", timeout=APPEAR_MS)
        assert _plan_open(page), "le choix mémorisé survit au rechargement"
        # Replier au clavier : Échap depuis le plan rend le focus au bouton.
        page.locator(f"{PLAN} [data-plan='brief']").focus()
        page.keyboard.press("Escape")
        assert not _plan_open(page) and page.evaluate("document.activeElement.dataset.role") == "plan-toggle"
        assert page.evaluate("(k) => localStorage.getItem(k)", PLAN_KEY) == "closed"
    # Toile assez large pour le plan, la feuille (900 px) et l'assistant : déplié d'office, la feuille ne se serre pas.
    for page in _page(browser, url, slug, viewport=(1920, 1000)):
        page.wait_for_function("(s) => !document.querySelector(s).hidden", arg=PLAN, timeout=APPEAR_MS)
        assert _plan_open(page)
        assert _box(page, ".at-sheet")["width"] >= 800 and not _canvas_overflow(page)
    # Sur 1440x900 avec l'assistant à droite, replié d'office : la feuille garde sa largeur d'avant.
    for page in _page(browser, url, slug):
        assert not _plan_open(page) and _box(page, ".at-sheet")["width"] > 900


@pytest.mark.parametrize("size", [(800, 700), (480, 800)])
def test_no_horizontal_overflow_with_the_outline_open_on_narrow_screens(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, f"Plan etroit {size[0]}")
    for page in _page(browser, url, slug, viewport=size):
        _ensure_plan(page)
        page.locator(TOGGLE).scroll_into_view_if_needed()
        assert not _canvas_overflow(page)
        box = _box(page, PLAN)
        assert box["x"] >= 0 and box["x"] + box["width"] <= size[0] + 1
        # La recherche ouverte non plus ne fait déborder.
        page.keyboard.press("Control+f")
        assert page.locator(FIND).is_visible() and not _canvas_overflow(page)


def test_the_outline_works_when_storage_is_blocked_and_in_dark_theme(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Plan sombre")
    for page in _page(browser, url, slug, scheme="dark", block_storage=True):
        _ensure_plan(page)
        link = page.locator(f"{PLAN} [data-plan='brief']")
        assert link.is_visible()
        color = page.evaluate("(s) => getComputedStyle(document.querySelector(s)).color", f"{PLAN} [data-plan='brief']")
        assert color != "rgb(0, 0, 0)"
        page.locator(TOGGLE).click()
        assert not _plan_open(page)


# ── Recherche dans le document ──────────────────────────────────────────────


@pytest.mark.parametrize("highlight_api", [True, False], ids=["css-highlight", "mark-fallback"])
def test_find_counts_steps_and_leaves_no_trace(browser: Browser, project_on: tuple[str, Path], highlight_api: bool) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, f"Recherche {'api' if highlight_api else 'mark'}")
    for page in _page(browser, url, slug, highlight_api=highlight_api):
        before = page.evaluate(SNAPSHOT)
        assert page.locator(FIND).is_hidden()
        page.locator("body").click(position={"x": 5, "y": 400})
        page.keyboard.press("Control+f")
        assert page.locator(FIND).is_visible() and page.evaluate("document.activeElement.dataset.role") == "find-input"
        page.locator(INPUT).fill("paragraphe")
        label = page.locator(COUNT).inner_text()
        found = re.fullmatch(r"1 sur (\d+)", label)
        assert found, label
        total = int(found.group(1))
        assert total >= 5
        if highlight_api:
            assert page.evaluate("CSS.highlights.get('at-find').size") == total
            assert page.evaluate("CSS.highlights.get('at-find-current').size") == 1
            assert page.locator(".at-hit").count() == 0
        else:
            assert page.evaluate("CSS.highlights") is None
            assert page.locator(".at-hit").count() == total and page.locator(".at-hit.is-current").count() == 1
        # Entrée : suivante ; Maj+Entrée : précédente, en boucle.
        page.keyboard.press("Enter")
        assert page.locator(COUNT).inner_text() == f"2 sur {total}"
        page.keyboard.press("Shift+Enter")
        page.keyboard.press("Shift+Enter")
        assert page.locator(COUNT).inner_text() == f"{total} sur {total}"
        page.keyboard.press("Enter")
        assert page.locator(COUNT).inner_text() == f"1 sur {total}"
        # L'occurrence courante est à l'écran.
        assert page.evaluate("(() => { const r = (document.querySelector('.at-hit.is-current') || {getBoundingClientRect: () => ({top: 300, bottom: 320})}).getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; })()")
        # Insensible à la casse et aux accents.
        page.locator(INPUT).fill("ECARTE")
        assert re.fullmatch(r"1 sur \d+", page.locator(COUNT).inner_text())
        page.locator(INPUT).fill("zzzzintrouvable")
        assert page.locator(COUNT).inner_text() == "Aucun résultat"
        page.locator(INPUT).fill("paragraphe")
        # Les boutons font comme le clavier.
        page.locator("[data-act='find-next']").click()
        assert page.locator(COUNT).inner_text() == f"2 sur {total}"
        page.locator("[data-act='find-prev']").click()
        assert page.locator(COUNT).inner_text() == f"1 sur {total}"
        # Échap ferme, retire tout, rend le focus ; la feuille est identique à celle d'avant.
        page.keyboard.press("Escape")
        assert page.locator(FIND).is_hidden()
        assert page.evaluate(SNAPSHOT) == before
        assert page.locator(".at-hit").count() == 0
        if highlight_api:
            assert page.evaluate("CSS.highlights.has('at-find') || CSS.highlights.has('at-find-current')") is False


def test_find_only_takes_ctrl_f_inside_the_document_and_leaves_the_browser_a_second_press(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Recherche raccourci")
    probe = "(el) => { const e = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true, cancelable: true }); el.dispatchEvent(e); return e.defaultPrevented; }"
    for page in _page(browser, url, slug):
        assert page.evaluate(probe, page.locator("body").element_handle()) is True  # le document est affiché : la barre s'ouvre
        assert page.locator(FIND).is_visible()
        # Barre déjà active : le second appui est laissé au navigateur (recherche native).
        assert page.evaluate(probe, page.locator(INPUT).element_handle()) is False
        # Maj+Ctrl+F (plein écran de la coque) n'ouvre pas la barre.
        page.locator(INPUT).press("Escape")
        assert page.locator(FIND).is_hidden()
        page.evaluate("() => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'F', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true }))")
        assert page.locator(FIND).is_hidden()
        # Hors de l'Atelier : rien n'est capturé.
        page.evaluate("() => { location.hash = '#hotes'; }")
        page.wait_for_function("() => document.body.dataset.activeArea === 'configuration'", timeout=APPEAR_MS)
        assert page.evaluate(probe, page.locator("body").element_handle()) is False
        assert page.locator(FIND).count() == 0


def test_find_follows_a_redraw_of_the_sheet(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Recherche redessin")
    for page in _page(browser, url, slug):
        page.keyboard.press("Control+f")
        page.locator(INPUT).fill("paragraphe")
        total = int(page.locator(COUNT).inner_text().split(" sur ")[1])
        # Un rechargement du document (une section redessinée) : les occurrences sont relues, rien ne se perd.
        page.evaluate("() => { const s = document.querySelector('.at-sheet'); const sec = s.querySelector('[data-section=\"brainstorm\"]'); sec.replaceWith(sec.cloneNode(true)); }")
        page.wait_for_function("(t) => document.querySelector('[data-role=find-count]').textContent.endsWith(' sur ' + t)", arg=str(total), timeout=APPEAR_MS)
        assert page.locator(FIND).is_visible()


# ── Menu « Document » : compteur, copie, fichier, impression ────────────────


def test_the_document_menu_counts_words_and_copies_the_stored_markdown(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Menu copie")
    expected = _stored(root, slug)
    for page in _page(browser, url, slug):
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        button = page.locator(MENU)
        assert button.get_attribute("aria-expanded") == "false" and page.locator("#at-docmenu-panel").is_hidden()
        button.click()
        assert button.get_attribute("aria-expanded") == "true"
        stats = page.locator("[data-role='doc-stats']").inner_text()
        words = _words(expected)
        found = re.fullmatch(r"([\d\s  ]+) mots · (\d+) min de lecture", stats)
        assert found, stats
        assert int(re.sub(r"\D", "", found.group(1))) == words
        assert int(found.group(2)) == max(1, -(-words // 230))
        page.locator("[data-role='doc-copy']").click()
        page.wait_for_function("() => document.querySelector('[data-role=doc-say]').textContent.includes('copié')", timeout=APPEAR_MS)
        copied = page.evaluate("navigator.clipboard.readText()")
        # Un document propre (voir test_workspace_atelier_lecture_correctifs) : le contenu y est, ni front matter ni gabarit.
        assert copied.startswith("# Menu copie\n") and "Les équipes perdent le fil des décisions de cadrage." in copied
        assert "phase:" not in copied and "À compléter" not in copied
        # Le menu s'est refermé après l'action et le focus est revenu au bouton.
        assert page.locator("#at-docmenu-panel").is_hidden()
        button.click()
        # Échap referme le menu et rend le focus au bouton.
        page.keyboard.press("Escape")
        assert page.locator("#at-docmenu-panel").is_hidden() and page.evaluate("document.activeElement.dataset.role") == "document-menu"
        # Un clic ailleurs le referme aussi.
        button.click()
        page.locator(".at-sheet").click(position={"x": 4, "y": 4})
        assert page.locator("#at-docmenu-panel").is_hidden()


def test_the_document_menu_downloads_the_same_markdown(browser: Browser, project_on: tuple[str, Path], tmp_path: Path) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Menu fichier")
    expected = _stored(root, slug)
    for page in _page(browser, url, slug):
        page.locator(MENU).click()
        with page.expect_download(timeout=APPEAR_MS) as info:
            page.locator("[data-role='doc-download']").click()
        download = info.value
        assert download.suggested_filename == f"{slug}.md"
        target = tmp_path / "export.md"
        download.save_as(target)
        saved = target.read_bytes().decode("utf-8")
        assert saved.startswith("# Menu fichier\n") and "phase:" not in saved and "À compléter" not in saved
        assert saved != expected  # les fichiers tels que stockés sont l'entrée « Fichiers bruts (.md) »


def test_the_print_sheet_keeps_only_the_document(browser: Browser, project_on: tuple[str, Path], tmp_path: Path) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Menu impression")
    for page in _page(browser, url, slug, scheme="dark"):  # le thème sombre ne passe pas à l'impression
        _ensure_plan(page)
        page.keyboard.press("Control+f")
        page.emulate_media(media="print")
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        hidden = [
            "#appbar", "#rail", "#panel-explorer", "#panel-inspector", "#dock", "#statusbar", ".at-test", ".at-head", ".at-phasebar", PLAN, FIND,
            "[data-role='assistant']", "[data-role='atelier-toasts']", ".at-cm-add", ".at-ch",
        ]
        for selector in hidden:
            shown = page.evaluate("(s) => [...document.querySelectorAll(s)].filter((n) => n.checkVisibility()).length", selector)
            assert shown == 0, selector
        # Tout ce qui est posé sur le corps de la page (mascotte, fenêtres) disparaît, la coque seule reste.
        stray = page.evaluate("[...document.body.children].filter((n) => n.id !== 'shell' && getComputedStyle(n).display !== 'none' && n.tagName !== 'SCRIPT').map((n) => n.tagName + '#' + n.id + '.' + n.className)")
        assert stray == [], stray
        assert page.locator(".at-sheet").is_visible()
        title = page.locator(".at-print-title")
        assert title.is_visible() and title.inner_text().strip() == "Menu impression"
        sheet = _box(page, ".at-sheet")
        assert sheet["x"] <= 2 and sheet["width"] > 600
        paper = page.evaluate("() => [getComputedStyle(document.body).backgroundColor, getComputedStyle(document.querySelector('.at-sheet p, .at-sheet .at-rub')).color]")
        assert paper == ["rgb(255, 255, 255)", "rgb(0, 0, 0)"], paper
        # Un vrai PDF : les phases s'enchaînent (aucun saut forcé), un document long tient sur plusieurs pages.
        pdf = tmp_path / "atelier.pdf"
        page.pdf(path=str(pdf), format="A4", print_background=True)
        data = pdf.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
        assert 2 <= pages <= 5, pages
        page.emulate_media(media="screen")
        assert page.locator(".at-phasebar").is_visible()
        assert page.locator(".at-print-title").is_hidden()


def test_ctrl_p_outside_the_atelier_is_left_alone(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier_rempli(root, "Impression ailleurs")
    for page in _page(browser, url, slug):
        page.evaluate("() => { location.hash = '#hotes'; }")
        page.wait_for_function("document.body.dataset.activeArea === 'configuration'", timeout=APPEAR_MS)
        page.emulate_media(media="print")
        assert page.locator("#appbar").is_visible()


def test_capture_lecture(browser: Browser, project_on: tuple[str, Path]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = _chantier_rempli(root, "Cadrage du cockpit")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for scheme in ("light", "dark"):
        for page in _page(browser, url, slug, scheme=scheme):
            _ensure_plan(page)
            page.locator(f"{PLAN} [data-plan='brainstorm/0']").click()
            page.keyboard.press("Control+f")
            page.locator(INPUT).fill("paragraphe")
            page.keyboard.press("Enter")
            page.locator(MENU).click()
            page.wait_for_timeout(400)
            page.screenshot(path=str(folder / f"lecture-{scheme}-1440x900.png"))
