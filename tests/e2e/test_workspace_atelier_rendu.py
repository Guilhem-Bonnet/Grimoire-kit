"""Atelier de spec : le rendu des blocs (tableaux, cases à cocher, gabarits vides) lu dans un vrai navigateur.

Un tableau Markdown se lit comme un tableau (en-têtes, alignements, liens dans les cellules, défilement interne si trop large) ;
une liste de cases s'affiche en vraies cases, cochables au clic et au clavier : la ligne visée du fichier est réécrite, tout le
reste à l'octet près, par la route d'édition de bloc et son contrôle de conflit (un conflit rétablit la case et montre le panneau
habituel) ; un bloc resté au gabarit « À compléter » est un emplacement vide dont l'éditeur s'ouvre vide, et le fichier garde le
gabarit si l'on annule ; la zone de conflit n'existe que lorsqu'il y a un conflit ; les libellés accessibles sont lisibles.

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
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import APPEAR_MS, BRAINSTORM, _folder

TABLE = (
    "| Piste | Coût | Durée |\n| :-- | --: | :-: |\n"
    "| **Export direct** | `3 j` | [doc TVA](https://exemple.fr/tva) |\n| Via un batch | 5 j | 2 sem |"
)
WIDE = "| " + " | ".join(f"Colonne {i} avec un titre long" for i in range(12)) + " |\n|" + "---|" * 12 + "\n| " + " | ".join("x" * 18 for _ in range(12)) + " |"
TASKS = "- [ ] écrire la spec\n- [x] relire le gabarit\n  - [ ] sous-tâche\n- [ ] publier"
TITLED = "Coût estimé : **trois jours** pour [doc TVA](https://exemple.fr/tva)."
MISC = "1. premier\n2. deuxième\n   - imbriqué"
PARTS = (
    TITLED, TABLE, WIDE, TASKS, "> - [ ] case dans une citation", MISC, "> une citation", "```\ncode = 1\n```",
    "un ~~barré~~ et <https://exemple.fr/auto>",
)
BODY = "\n\n".join(PARTS)
SECTION = "[data-section='brainstorm']"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-rendu", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _chantier(root: Path, titre: str, body: str | None = BODY) -> tuple[str, Path]:
    made = new_chantier(root, titre)
    path = _folder(root, made.slug) / BRAINSTORM
    if body is not None:
        path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", body), encoding="utf-8")
    return made.slug, path


def _open(browser: Browser, url: str, slug: str, *, width: int = 1440, height: int = 900, scheme: str = "light") -> Iterator[tuple[Page, list[str]]]:
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator(f"{SECTION} .at-md").first.wait_for(timeout=APPEAR_MS)
    try:
        yield page, errors
    finally:
        context.close()


def _put(page: Page) -> object:
    return page.expect_response(lambda r: r.request.method == "PUT" and "/blocs/" in r.url, timeout=APPEAR_MS)


def test_a_markdown_table_is_rendered_as_a_table(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, _path = _chantier(root, "Tableau rendu")
    for page, errors in _open(browser, url, slug):
        table = page.locator(f"{SECTION} table.at-md-table").first
        expect(table.locator("thead th")).to_have_text(["Piste", "Coût", "Durée"], use_inner_text=True)
        expect(table.locator("thead th").first).to_have_attribute("scope", "col")
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(table).not_to_contain_text("|", use_inner_text=True)  # plus de barres verticales en texte brut
        for at, side in enumerate(["left", "right", "center"]):
            expect(table.locator("tbody tr").first.locator("td").nth(at)).to_have_css("text-align", side)
        link = table.locator("td a")
        expect(link).to_have_attribute("href", "https://exemple.fr/tva")
        expect(link).to_have_attribute("rel", re.compile("noopener"))
        expect(link).to_have_attribute("rel", re.compile("noreferrer"))
        expect(table.locator("td strong")).to_have_text("Export direct", use_inner_text=True)
        expect(table.locator("td code")).to_have_text("3 j", use_inner_text=True)
        assert not errors


@pytest.mark.parametrize("width", [800, 480])
def test_a_wide_table_scrolls_inside_and_the_page_does_not_overflow(browser: Browser, project_on: tuple[str, Path], width: int) -> None:
    url, root = project_on
    slug, _path = _chantier(root, f"Tableau large {width}")
    for page, _errors in _open(browser, url, slug, width=width, height=900):
        wrap = page.locator(f"{SECTION} .at-md-tablewrap").nth(1)
        # La mise en page se pose en place : on attend l'état voulu (le tableau défile dans son cadre, la page ne déborde pas).
        page.wait_for_function(
            "(s) => { const w = document.querySelectorAll(s)[1]; return !!w && w.scrollWidth > w.clientWidth; }",
            arg=f"{SECTION} .at-md-tablewrap",
            timeout=APPEAR_MS,
        )
        expect(wrap).to_have_attribute("tabindex", "0")
        page.wait_for_function("() => document.documentElement.scrollWidth <= window.innerWidth", timeout=APPEAR_MS)  # aucun débordement de la page


def test_other_markdown_constructions_are_not_left_raw(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, _path = _chantier(root, "Constructions courantes")
    for page, _errors in _open(browser, url, slug):
        sec = page.locator(SECTION)
        expect(sec.locator("ol > li")).to_have_count(2)
        expect(sec.locator("ol li ul li")).to_have_text("imbriqué", use_inner_text=True)
        expect(sec.locator("blockquote.at-md-quote").nth(1)).to_be_attached()  # au moins deux citations
        expect(sec.locator("pre.at-md-code code")).to_have_text("code = 1", use_inner_text=True)
        expect(sec.locator("s")).to_have_text("barré", use_inner_text=True)
        auto = sec.locator("a[href='https://exemple.fr/auto']")
        expect(auto).to_have_count(1)
        expect(auto).to_have_attribute("target", "_blank")
        # Négatifs : posés après les attentes positives ci-dessus, donc sur un rendu terminé.
        for raw in ("**", "~~", "```", "](", "<https"):
            expect(sec).not_to_contain_text(raw, use_inner_text=True)


def test_checkboxes_are_real_and_toggle_on_click_and_space(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, path = _chantier(root, "Cases cochables")
    before = path.read_text(encoding="utf-8")
    for page, errors in _open(browser, url, slug):
        boxes = page.locator(f"{SECTION} .at-md > ul.at-tasks input[type='checkbox']")  # hors citation
        expect(boxes).to_have_count(4)
        for at, checked in enumerate([False, True, False, False]):
            if checked:
                expect(boxes.nth(at)).to_be_checked()
            else:
                expect(boxes.nth(at)).not_to_be_checked()
        expect(page.locator(f"{SECTION} .at-tasks").first).not_to_contain_text("[ ]", use_inner_text=True)
        with _put(page):
            boxes.nth(0).click()
        expected = before.replace("- [ ] écrire la spec", "- [x] écrire la spec")
        assert path.read_text(encoding="utf-8") == expected, "seule la ligne visée a changé"
        page.wait_for_function("() => document.querySelector(\"[data-role='atelier-status']\").textContent.includes('cochée')", timeout=APPEAR_MS)
        expect(page.locator(f"{SECTION} .at-tasks input").first).to_be_checked()
        expect(page.locator(f"{SECTION} [data-role='block-edit']")).to_have_count(0)  # cocher n'ouvre pas l'édition
        # Au clavier : le focus est resté sur la case, Espace la décoche (la sous-tâche se coche de même).
        sub = page.locator(f"{SECTION} .at-tasks input[data-line='2']")
        with _put(page):
            sub.focus()
            page.keyboard.press("Space")
        assert path.read_text(encoding="utf-8") == expected.replace("  - [ ] sous-tâche", "  - [x] sous-tâche")
        with _put(page):
            page.locator(f"{SECTION} .at-tasks input[data-line='0']").focus()
            page.keyboard.press("Space")
        assert path.read_text(encoding="utf-8") == before.replace("  - [ ] sous-tâche", "  - [x] sous-tâche")
        assert not errors


def test_checkboxes_are_read_only_where_the_block_cannot_be_written(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, path = _chantier(root, "Cases en lecture seule")
    before = path.read_text(encoding="utf-8")
    for page, _errors in _open(browser, url, slug):
        quoted = page.locator(f"{SECTION} blockquote input[type='checkbox']")
        expect(quoted).to_have_count(1)  # une case dans une citation ne s'écrit pas
        expect(quoted).to_be_disabled()
        page.locator(f"{SECTION} .at-blk:has(.at-md > ul.at-tasks)").click(position={"x": 4, "y": 4})
        page.locator(f"{SECTION} [data-role='block-edit'] textarea").wait_for(timeout=APPEAR_MS)
        expect(page.locator(f"{SECTION} [data-role='block-edit'] input[type='checkbox']")).to_have_count(0)  # en édition, pas de case active
        expect(page.locator(f"{SECTION} .at-conflict")).to_have_count(0)  # pas de zone de conflit sans conflit
        page.keyboard.press("Escape")
        expect(page.locator(f"{SECTION} [data-role='block-edit']")).to_have_count(0)  # fermé avant de relire le fichier
    assert path.read_text(encoding="utf-8") == before


def test_a_conflict_restores_the_box_and_shows_the_usual_panel(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, path = _chantier(root, "Case en conflit")
    for page, _errors in _open(browser, url, slug):
        changed = path.read_text(encoding="utf-8").replace("- [ ] publier", "- [ ] publier (version de l'agent)")
        path.write_text(changed, encoding="utf-8")  # le fichier change pendant que la page reste ouverte
        page.locator(f"{SECTION} .at-tasks input[data-line='0']").click()
        panel = page.locator(f"{SECTION} [data-role='block-conflict']")
        panel.wait_for(timeout=APPEAR_MS)
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("Version actuelle", use_inner_text=True)
        expect(panel.locator("[data-role='conflict-theirs']")).to_contain_text("(version de l'agent)", use_inner_text=True)
        expect(panel.locator("[data-role='conflict-mine']")).to_contain_text("[x] écrire la spec", use_inner_text=True)
        assert path.read_text(encoding="utf-8") == changed, "rien n'a été écrit"
        # « Prendre l'autre » : la case est à l'état du serveur.
        page.locator("[data-role='conflict-take-other']").click()
        page.locator(f"{SECTION} .at-tasks input[data-line='0']").wait_for(timeout=APPEAR_MS)
        expect(page.locator(f"{SECTION} [data-role='block-edit']")).to_have_count(0)  # l'édition est fermée
        expect(page.locator(f"{SECTION} .at-tasks input[data-line='0']")).not_to_be_checked()
        assert path.read_text(encoding="utf-8") == changed


def test_an_empty_template_opens_empty_and_leaves_the_file_untouched(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, path = _chantier(root, "Gabarit vide", body=None)
    before = path.read_bytes()
    for page, errors in _open_template(browser, url, slug):
        slot = page.locator(f"{SECTION} [data-role='empty-slot']").first
        # L'emplacement vide n'est plus le gabarit nu : il dit ce qu'on y met (invite du serveur), sans citation.
        expect(slot).to_contain_text("Les façons possibles de répondre au besoin", use_inner_text=True)
        expect(slot.locator("blockquote")).to_have_count(0)
        slot.click()
        area = page.locator(f"{SECTION} [data-role='block-edit'] textarea")
        area.wait_for(timeout=APPEAR_MS)
        expect(area).to_have_value("")
        expect(area).to_have_attribute("placeholder", "Écris ici, avec tes mots : pistes.")  # une invite humaine, plus le gabarit
        expect(page.locator(f"{SECTION} .at-conflict")).to_have_count(0)
        page.keyboard.press("Escape")
        expect(area).to_be_hidden()  # fermé avant de relire le fichier
        assert path.read_bytes() == before, "annuler laisse le gabarit"
        slot.click()
        area.wait_for(timeout=APPEAR_MS)
        area.fill("   ")
        page.keyboard.press("Control+Enter")
        page.locator(f"{SECTION} [data-role='block-edit']").wait_for(state="detached", timeout=APPEAR_MS)
        assert path.read_bytes() == before, "enregistrer vide laisse le gabarit"
        slot.click()
        area.wait_for(timeout=APPEAR_MS)
        area.fill("Une vraie piste.")
        with _put(page):
            page.keyboard.press("Control+Enter")
        assert "Une vraie piste." in path.read_text(encoding="utf-8") and "À compléter — pistes." not in path.read_text(encoding="utf-8")
        assert not errors


def _open_template(browser: Browser, url: str, slug: str) -> Iterator[tuple[Page, list[str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.locator(f"{SECTION} [data-role='empty-slot']").first.wait_for(timeout=APPEAR_MS)
    try:
        yield page, errors
    finally:
        context.close()


def test_accessible_labels_are_plain_text(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug, _path = _chantier(root, "Libelles lisibles")
    for page, _errors in _open(browser, url, slug):
        block = page.locator(f"{SECTION} .at-blk").first
        # Le libellé contient « trois jours » et ni marque Markdown ni adresse.
        plain = re.compile(r"^(?!.*(?:\*\*|\]\(|https://))(?=.*trois jours)", re.DOTALL)
        expect(block).to_have_attribute("aria-label", plain)
        expect(block.locator(".at-cm-add")).to_have_attribute("aria-label", plain)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_captures(browser: Browser, project_on: tuple[str, Path], scheme: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    url, root = project_on
    slug, _path = _chantier(root, f"Capture rendu {scheme}")
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for page, _errors in _open(browser, url, slug, scheme=scheme):
        for name, selector in (
            ("tableau", f"{SECTION} .at-md-table"),
            ("cases", f"{SECTION} .at-md > ul.at-tasks"),
            ("emplacement-vide", f"{SECTION} [data-role='empty-slot']"),
        ):
            page.locator(selector).first.evaluate("e => e.scrollIntoView({ block: 'center' })")
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / f"rendu-{name}-{scheme}.png"))
