"""Atelier de spec : références cliquables, chantiers dans la palette ⌘K, mode lecture, document qui suit son fichier.

Chaque preuve est lue à l'écran, dans un vrai navigateur, avec une attente sur l'état voulu (jamais de lecture instantanée après un
geste) : une référence ``REQ-001`` est un lien vers la ligne de l'exigence (défilement, surlignage, adresse), une tâche du kit un
lien vers Travail, ``[[chantier]]`` un lien vers ce chantier (ou un texte « introuvable », jamais un lien mort), rien dans le code ;
la palette trouve les chantiers (sans requête à chaque frappe, sans accents) et, chantier ouvert, ses phases et rubriques ;
le mode lecture masque les gestes d'écriture, garde l'adresse et le navigateur à jour et sort par Échap ; un fichier modifié hors
du cockpit met le document à jour, sans toucher au bloc que l'on est en train d'écrire.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900, clair et sombre ; sans la variable le test de capture ne fait rien.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_comments

from .conftest import _serve_empty_project

WAIT_MS = 30_000
BRAINSTORM = "02-brainstorm.md"
PARA = "Voir REQ-001 et REQ-099, la tâche GAO-demo-001, le chantier [[Chantier Cible]] et [[Fantôme]]."
CODE = "Du code `REQ-002` en ligne."
FENCE = "```\nREQ-002 dans un bloc\n```"
TASKS = "- [ ] une case à cocher"
BODY = f"{PARA}\n\n{CODE}\n\n{FENCE}\n\n{TASKS}"
EXIGENCES = (
    "# Exigences\n\n- REQ-001 [Must] : maintenir un bouton pour dicter une idée\n  - la transcription apparaît en moins de 2 secondes\n"
    "- REQ-002 [Should] : replier sur le navigateur\n  - le texte reste dicté sans le moteur du kit\n"
)
SECTION = "[data-section='brainstorm']"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-liens", roots):
        workspace_atelier.set_atelier(roots[0], True)
        new_chantier(roots[0], "Chantier Cible")  # celui que visent les références `[[Chantier Cible]]`
        yield url, roots[0]


def _folder(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug


def _chantier(root: Path, titre: str, body: str = BODY) -> str:
    made = new_chantier(root, titre)
    path = _folder(root, made.slug) / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", body), encoding="utf-8")
    (_folder(root, made.slug) / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return made.slug


def _open(browser: Browser, url: str, hash_: str, *, width: int = 1440, height: int = 900, scheme: str = "light") -> Iterator[Page]:
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
    page.locator(f"{SECTION} .at-md").first.wait_for(timeout=WAIT_MS)
    try:
        yield page
        assert not errors, errors
    finally:
        context.close()


def _doc(browser: Browser, url: str, slug: str, extra: str = "", **kwargs: object) -> Iterator[Page]:
    return _open(browser, url, f"#atelier?chantier={slug}{extra}", **kwargs)  # type: ignore[arg-type]


# ── Références ──────────────────────────────────────────────────────────────


def test_references_become_links_and_only_where_they_point_somewhere(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    target = "chantier-cible"
    slug = _chantier(root, "Chantier Source")
    for page in _doc(browser, url, slug):
        section = page.locator(SECTION)
        req = section.locator("a.at-ref-req")
        expect(req).to_have_count(1, timeout=WAIT_MS)  # REQ-099 n'existe pas : du texte ; REQ-002 est dans du code
        assert req.get_attribute("href") == f"#atelier?chantier={slug}&phase=exigences&req=REQ-001"
        task = section.locator("a.at-ref-task")
        assert task.get_attribute("href") == "#travail?task=GAO-demo-001"
        chantier = section.locator("a.at-ref-chantier")
        assert chantier.get_attribute("href") == f"#atelier?chantier={target}"
        missing = section.locator(".at-ref-missing")
        expect(missing).to_have_count(1)
        assert missing.evaluate("e => e.tagName") == "SPAN", "jamais de lien mort"
        assert missing.get_attribute("title") == "chantier introuvable"
        assert "REQ-099" in section.inner_text()
        assert section.locator("code.at-code a, pre a, code a").count() == 0, "aucun lien dans le code"
        assert "REQ-002" in section.locator("code.at-code").inner_text()


def test_a_requirement_link_scrolls_highlights_and_keeps_the_address(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Défilement")
    for page in _doc(browser, url, slug):
        page.locator("a.at-ref-req").click()
        row = page.locator(".at-blk[data-req='REQ-001']")
        expect(row).to_have_class(re.compile(r"is-ref-target"), timeout=WAIT_MS)
        page.wait_for_function("() => location.hash.includes('req=REQ-001') && location.hash.includes('phase=exigences')", timeout=WAIT_MS)
        assert page.evaluate("(sel) => { const b = document.querySelector(sel).getBoundingClientRect(); return b.top >= 0 && b.bottom <= window.innerHeight; }", ".at-blk[data-req='REQ-001']")
        expect(page.locator("[data-role='block-edit']")).to_have_count(0)  # suivre un lien n'ouvre pas l'éditeur du bloc
        expect(page.locator(".at-phase[data-phase='exigences']")).to_have_attribute("aria-current", "step", timeout=WAIT_MS)


def test_a_shared_link_opens_on_the_requirement(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Partagé")
    for page in _doc(browser, url, slug, "&phase=exigences&req=REQ-002"):
        expect(page.locator(".at-blk[data-req='REQ-002']")).to_have_class(re.compile(r"is-ref-target"), timeout=WAIT_MS)
        page.wait_for_function(
            "() => { const b = document.querySelector(\".at-blk[data-req='REQ-002']\").getBoundingClientRect(); return b.top >= 0 && b.bottom <= window.innerHeight; }",
            timeout=WAIT_MS,
        )


def test_hover_and_focus_show_the_requirement_card(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Carte")
    for page in _doc(browser, url, slug):
        card = page.locator("[data-role='ref-card']")
        expect(card).to_be_hidden()
        page.locator("a.at-ref-req").hover()
        expect(card).to_be_visible(timeout=WAIT_MS)
        expect(card).to_contain_text("REQ-001")
        expect(card).to_contain_text("maintenir un bouton pour dicter une idée")
        expect(card).to_contain_text("Must")
        expect(card).to_contain_text("Non validée", timeout=WAIT_MS)  # la trace du découpage arrive après le document
        page.mouse.move(5, 5)
        expect(card).to_be_hidden(timeout=WAIT_MS)
        page.locator("a.at-ref-req").focus()
        expect(card).to_be_visible(timeout=WAIT_MS)
        page.keyboard.press("Escape")
        expect(card).to_be_hidden(timeout=WAIT_MS)


def test_a_chantier_link_opens_that_chantier(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    target = "chantier-cible"
    slug = _chantier(root, "Chantier Départ")
    for page in _doc(browser, url, slug):
        page.locator("a.at-ref-chantier").click()
        page.wait_for_function("(s) => location.hash.includes('chantier=' + s)", arg=target, timeout=WAIT_MS)
        expect(page.locator(".at-head-doc .at-title")).to_contain_text("Chantier Cible", timeout=WAIT_MS)


# ── Palette ─────────────────────────────────────────────────────────────────


def _palette(page: Page, query: str = "") -> None:
    page.keyboard.press("Control+k")
    expect(page.locator("#palette")).to_have_attribute("open", "", timeout=WAIT_MS)
    if query:
        page.locator("#palette-input").fill(query)


def test_the_palette_finds_chantiers_without_accents_and_without_a_request_per_keystroke(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    accent = _chantier(root, "Éléphant rose")
    other = _chantier(root, "Chantier Autre")
    for page in _doc(browser, url, other):
        calls: list[str] = []
        page.on("request", lambda r, seen=calls: seen.append(r.url) if r.url.endswith("/atelier/chantiers") else None)
        _palette(page)
        expect(page.locator("#palette-list .palette-section", has_text="Chantiers")).to_be_visible(timeout=WAIT_MS)
        seen = len(calls)
        for text in ("e", "el", "ele", "elep", "elephant"):
            page.locator("#palette-input").fill(text)
        row = page.locator("#palette-list li[role='option']", has_text="Éléphant rose")
        expect(row).to_have_count(1, timeout=WAIT_MS)
        expect(row).to_contain_text("Chantier ·")
        assert len(calls) == seen, "la liste est chargée une fois, puis filtrée"
        row.click()
        page.wait_for_function("(s) => location.hash.includes('chantier=' + s)", arg=accent, timeout=WAIT_MS)
        expect(page.locator(".at-head-doc .at-title")).to_contain_text("Éléphant rose", timeout=WAIT_MS)


def test_the_palette_goes_to_a_phase_or_a_section_of_the_open_chantier(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Navigation")
    for page in _doc(browser, url, slug):
        _palette(page, "aller a exigences")
        row = page.locator("#palette-list li[role='option']", has_text="Aller à : Exigences")
        expect(row).to_have_count(1, timeout=WAIT_MS)
        row.click()
        expect(page.locator(".at-phase[data-phase='exigences']")).to_have_attribute("aria-current", "step", timeout=WAIT_MS)
        page.wait_for_function("() => location.hash.includes('phase=exigences')", timeout=WAIT_MS)
        rub = page.locator(f"{SECTION} > .at-rub").first
        name = rub.inner_text().strip()
        _palette(page, "aller a")
        entry = page.locator("#palette-list li[role='option']", has_text=f"Brainstorm › {name}")
        expect(entry).to_have_count(1, timeout=WAIT_MS)
        entry.click()
        expect(rub).to_be_focused(timeout=WAIT_MS)
        expect(page.locator(".at-phase[data-phase='brainstorm']")).to_have_attribute("aria-current", "step", timeout=WAIT_MS)


def test_the_palette_has_no_aller_a_entries_outside_a_document(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    _chantier(root, "Chantier Liste")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html#atelier", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
        _palette(page, "aller a")
        expect(page.locator("#palette-list li[role='option']", has_text="Aller à :")).to_have_count(0)
    finally:
        context.close()


# ── Mode lecture ────────────────────────────────────────────────────────────


def test_reading_mode_hides_writing_gestures_and_is_shareable(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Lecture")
    for page in _doc(browser, url, slug):
        button = page.locator("[data-role='atelier-lecture']")
        expect(button).to_have_text("Lecture")
        button.click()
        expect(page.locator(".at-page")).to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        expect(button).to_have_text("Modifier")
        expect(button).to_have_attribute("aria-pressed", "true")
        for selector in (".at-cm-add", ".at-ch", ".at-asst", ".at-asst-pill", ".at-live", ".at-live-bar"):
            expect(page.locator(selector).first).to_be_hidden(timeout=WAIT_MS)
        page.wait_for_function("() => location.hash.includes('lecture=1')", timeout=WAIT_MS)
        page.wait_for_function("() => window.localStorage.getItem('grimoire.atelier.lecture') === '1'", timeout=WAIT_MS)
        box = page.locator(f"{SECTION} .at-task-box")
        expect(box).to_be_disabled(timeout=WAIT_MS)
        # Un clic sur un bloc n'ouvre pas l'éditeur : on attend un instant que l'éditeur éventuel ait eu le temps de paraître.
        page.locator(f"{SECTION} .at-blk").first.click(position={"x": 8, "y": 8})
        page.wait_for_timeout(400)
        expect(page.locator("[data-role='block-edit']")).to_have_count(0)
        # La page est centrée et étroite.
        width = page.locator(".at-sheet").evaluate("e => e.getBoundingClientRect().width")
        assert width <= 46 * 16 + 1
        page.keyboard.press("Escape")
        expect(page.locator(".at-page")).not_to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        expect(button).to_have_text("Lecture")
        expect(box).to_be_enabled(timeout=WAIT_MS)
        expect(page.locator(".at-cm-add").first).to_be_attached()
        page.wait_for_function("() => !location.hash.includes('lecture=1')", timeout=WAIT_MS)
        page.wait_for_function("() => window.localStorage.getItem('grimoire.atelier.lecture') === '0'", timeout=WAIT_MS)


def test_alt_l_toggles_reading_and_the_address_opens_in_reading(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Raccourci")
    for page in _doc(browser, url, slug):
        page.keyboard.press("Alt+l")
        expect(page.locator(".at-page")).to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        page.keyboard.press("Alt+l")
        expect(page.locator(".at-page")).not_to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
    for page in _doc(browser, url, slug, "&lecture=1"):
        expect(page.locator(".at-page")).to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        expect(page.locator("[data-role='atelier-lecture']")).to_have_attribute("aria-pressed", "true")
        expect(page.locator(f"{SECTION} .at-task-box")).to_be_disabled(timeout=WAIT_MS)
        page.locator(".at-phase[data-phase='exigences']").click()
        page.wait_for_function("() => location.hash.includes('phase=exigences') && location.hash.includes('lecture=1')", timeout=WAIT_MS)


def test_reading_mode_survives_blocked_storage(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Sans Stockage")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('bloqué'); } });")
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
        page.locator(f"{SECTION} .at-md").first.wait_for(timeout=WAIT_MS)
        page.locator("[data-role='atelier-lecture']").click()
        expect(page.locator(".at-page")).to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
    finally:
        context.close()


def test_reading_mode_is_refused_while_a_block_is_being_edited(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Saisie")
    for page in _doc(browser, url, slug):
        page.locator(f"{SECTION} .at-blk").first.click(position={"x": 8, "y": 8})
        expect(page.locator("[data-role='block-edit'] textarea")).to_be_visible(timeout=WAIT_MS)
        page.locator("[data-role='atelier-lecture']").click()
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("avant de passer en lecture", timeout=WAIT_MS)
        expect(page.locator(".at-page")).not_to_have_class(re.compile(r"is-reading"))
        expect(page.locator("[data-role='block-edit'] textarea")).to_be_visible()


def test_an_open_thread_is_a_discreet_dot_that_leaves_reading_on_the_thread(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Fil")
    doc = workspace_atelier.document(root, slug)
    block = next(b for p in doc["phases"] if p["id"] == "brainstorm" for b in p["blocks"] if "Voir REQ-001" in b["text"])
    workspace_atelier_comments.create(root, slug, {"block_id": block["id"], "texte": "Une remarque à garder.", "quote": "la tâche"}, "humain")
    for page in _doc(browser, url, slug, "&lecture=1"):
        dot = page.locator("[data-role='read-dot']")
        expect(dot).to_have_count(1, timeout=WAIT_MS)
        expect(dot).to_be_visible()
        expect(page.locator(".at-cm-margin").first).to_be_hidden()
        dot.click()
        expect(page.locator(".at-page")).not_to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        expect(page.locator("[data-com]").first).to_be_visible(timeout=WAIT_MS)
        expect(page.locator("[data-com]").first).to_contain_text("Une remarque à garder.")


@pytest.mark.parametrize("width", [480])
def test_reading_mode_on_a_narrow_screen_has_no_horizontal_scroll(browser: Browser, project_on: tuple[str, Path], width: int) -> None:
    url, root = project_on
    slug = _chantier(root, f"Chantier Étroit {width}")
    for page in _doc(browser, url, slug, "&lecture=1", width=width, height=900):
        expect(page.locator(".at-page")).to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


# ── Le document suit son fichier ────────────────────────────────────────────


def test_the_document_follows_a_file_changed_elsewhere(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Disque", body="Première piste.\n\nSeconde piste.")
    path = _folder(root, slug) / BRAINSTORM
    for page in _doc(browser, url, slug):
        expect(page.locator(SECTION)).to_contain_text("Première piste.")
        path.write_text(path.read_text(encoding="utf-8").replace("Première piste.", "Piste réécrite par un agent.").replace("Seconde piste.", "Seconde piste revue."), encoding="utf-8")
        expect(page.locator(SECTION)).to_contain_text("Piste réécrite par un agent.", timeout=WAIT_MS)
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("Document mis à jour : 2 blocs modifiés par ailleurs", timeout=WAIT_MS)
        expect(page.locator(f"{SECTION} .at-blk.is-ext-changed").first).to_be_attached(timeout=WAIT_MS)
        expect(page.locator(f"{SECTION} .at-blk", has_text="Seconde piste revue.")).to_have_count(1)


def test_a_block_being_edited_is_never_replaced_by_a_change_on_disk(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Disque Saisie", body="Piste à garder.\n\nPiste voisine.")
    path = _folder(root, slug) / BRAINSTORM
    for page in _doc(browser, url, slug):
        page.locator(f"{SECTION} .at-blk", has_text="Piste à garder.").click(position={"x": 8, "y": 8})
        area = page.locator("[data-role='block-edit'] textarea")
        expect(area).to_be_visible(timeout=WAIT_MS)
        area.fill("Ma saisie en cours")
        path.write_text(path.read_text(encoding="utf-8").replace("Piste à garder.", "Piste changée ailleurs.").replace("Piste voisine.", "Voisine changée aussi."), encoding="utf-8")
        expect(page.locator(f"{SECTION} .at-blk", has_text="Voisine changée aussi.")).to_have_count(1, timeout=WAIT_MS)
        expect(area).to_have_value("Ma saisie en cours")
        expect(page.locator("[data-role='block-stale']")).to_be_visible(timeout=WAIT_MS)
        expect(area).to_be_focused()


def test_a_hidden_tab_asks_nothing_and_catches_up_on_return(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Caché", body="Avant le changement.")
    path = _folder(root, slug) / BRAINSTORM
    for page in _doc(browser, url, slug):
        calls: list[str] = []
        page.on("request", lambda r, seen=calls: seen.append(r.url) if "/entretien" in r.url else None)
        page.evaluate("() => Object.defineProperty(document, 'hidden', { get: () => true, configurable: true })")
        page.wait_for_timeout(6500)  # plus qu'un tour du pouls (5 s) : s'il demandait, ce serait fait
        seen = len(calls)
        path.write_text(path.read_text(encoding="utf-8").replace("Avant le changement.", "Après le changement."), encoding="utf-8")
        page.wait_for_timeout(6500)
        assert len(calls) == seen, "onglet caché : aucune requête"
        expect(page.locator(SECTION)).to_contain_text("Avant le changement.")
        page.evaluate("() => { Object.defineProperty(document, 'hidden', { get: () => false, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); }")
        expect(page.locator(SECTION)).to_contain_text("Après le changement.", timeout=WAIT_MS)
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("1 bloc modifié par ailleurs", timeout=WAIT_MS)


def test_an_own_write_does_not_announce_a_change_elsewhere(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier Écriture", body="Piste personnelle.")
    for page in _doc(browser, url, slug):
        page.locator(f"{SECTION} .at-blk", has_text="Piste personnelle.").click(position={"x": 8, "y": 8})
        area = page.locator("[data-role='block-edit'] textarea")
        expect(area).to_be_visible(timeout=WAIT_MS)
        area.fill("Piste personnelle révisée.")
        area.press("Control+Enter")
        expect(page.locator("[data-role='atelier-toasts']")).to_contain_text("Bloc enregistré", timeout=WAIT_MS)
        page.wait_for_timeout(7000)  # un tour complet du pouls
        expect(page.locator(SECTION)).to_contain_text("Piste personnelle révisée.")
        expect(page.locator("[data-role='atelier-toasts']")).not_to_contain_text("par ailleurs")


# ── Captures ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_captures(browser: Browser, project_on: tuple[str, Path], scheme: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    url, root = project_on
    slug = _chantier(root, f"Exigences de la refonte {scheme}")
    doc = workspace_atelier.document(root, slug)
    block = next(b for p in doc["phases"] if p["id"] == "brainstorm" for b in p["blocks"] if "Voir REQ-001" in b["text"])
    workspace_atelier_comments.create(root, slug, {"block_id": block["id"], "texte": "Une remarque à garder.", "quote": "la tâche"}, "humain")
    for page in _doc(browser, url, slug, "&phase=brainstorm&lecture=1", scheme=scheme):
        expect(page.locator("[data-role='read-dot']")).to_be_visible(timeout=WAIT_MS)
        page.screenshot(path=str(out / f"lecture-{scheme}.png"))
        page.locator("[data-role='atelier-lecture']").click()
        expect(page.locator(".at-page")).not_to_have_class(re.compile(r"is-reading"), timeout=WAIT_MS)
        page.locator("a.at-ref-req").hover()
        expect(page.locator("[data-role='ref-card']")).to_contain_text("Non validée", timeout=WAIT_MS)
        page.screenshot(path=str(out / f"carte-exigence-{scheme}.png"))
        page.mouse.move(5, 5)
        page.keyboard.press("Control+k")
        expect(page.locator("#palette")).to_have_attribute("open", "", timeout=WAIT_MS)
        page.locator("#palette-input").fill("exigences")
        expect(page.locator("#palette-list .palette-section", has_text="Chantiers")).to_be_visible(timeout=WAIT_MS)
        expect(page.locator("#palette-list .palette-section", has_text="Dans ce chantier")).to_be_visible(timeout=WAIT_MS)
        page.screenshot(path=str(out / f"palette-{scheme}.png"))
