"""Atelier de spec, lot 1 (#1240) : drapeau, onglet, bibliothèque, document en blocs, édition, 409.

Drapeau éteint : aucun onglet, ``GET atelier/chantiers`` en 404. Drapeau allumé : onglet « Atelier » avec son
badge « Test », bandeau, bibliothèque de deux chantiers créés dans la fixture, document en blocs, édition d'un
bloc qui ne change que sa plage dans le fichier de phase, écriture concurrente refusée en 409.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ; sans la variable le test de
capture ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
BRAINSTORM = "02-brainstorm.md"


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-off", roots):
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-on", roots):
        root = roots[0]
        workspace_atelier.set_atelier(root, True)
        premier = new_chantier(root, "Dicter une idée depuis le cockpit")
        new_chantier(root, "Chantiers multiples du cadrage")
        phase = root / "_grimoire" / "cadrage" / premier.slug / BRAINSTORM
        phase.write_text(
            phase.read_text(encoding="utf-8").replace(
                "> À compléter — pistes.",
                "- Bouton maintenu pour parler.\n- Repli sur le navigateur.",
            ),
            encoding="utf-8",
        )
        yield url, root


def _open(browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def test_flag_off_no_tab_and_404(browser: Browser, project_off: tuple[str, Path]) -> None:
    url, _root = project_off
    assert _status(f"{url}/api/workspace/atelier/chantiers") == 404
    assert _status(f"{url}/api/workspace/atelier/chantiers/principal") == 404
    for page in _open(browser, url):
        assert page.locator("#tab-space-atelier").count() == 0
        assert "atelier" not in page.evaluate("window.GrimoireWorkspace.spaces")
        loaded = page.evaluate("performance.getEntriesByType('resource').map(e => e.name)")
        assert not [name for name in loaded if "/spaces/atelier" in name]
        # La rubrique de réglage, elle, existe et propose l'interrupteur.
        page.evaluate("window.GrimoireWorkspace.goto('fonctions-test')")
        switch = page.locator("[data-feature='atelier']")
        switch.wait_for(timeout=APPEAR_MS)
        assert switch.get_attribute("aria-checked") == "false"


def test_flag_on_tab_banner_library(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, _root = project_on
    for page in _open(browser, url):
        tab = page.locator("#tab-space-atelier")
        tab.wait_for(timeout=APPEAR_MS)
        assert tab.locator(".badge").inner_text() == "Test"
        order = page.evaluate("window.GrimoireWorkspace.spaces")
        assert order.index("atelier") == order.index("configuration") - 1
        tab.click()
        banner = page.locator("[data-role='atelier-test']")
        banner.wait_for(timeout=APPEAR_MS)
        assert "Atelier en test." in banner.inner_text()
        assert "Aucune tâche n'est lancée tant que tu n'as pas validé le découpage." in banner.inner_text()
        rows = page.locator("a.at-row")
        rows.first.wait_for(timeout=APPEAR_MS)
        assert rows.count() == 2
        titles = rows.locator(".at-name b").all_inner_texts()
        assert sorted(titles) == ["Chantiers multiples du cadrage", "Dicter une idée depuis le cockpit"]
        assert page.locator("[data-role='new-chantier']").is_visible()
        # « Désactiver » mène à la rubrique des fonctions en test.
        banner.get_by_text("Désactiver").click()
        page.locator("[data-feature='atelier']").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-feature='atelier']").get_attribute("aria-checked") == "true"


def test_new_chantier_and_kit_error(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    for page in _open(browser, url, "#atelier"):
        page.locator("[data-role='new-chantier']").click()
        field = page.get_by_label("Titre du chantier")
        field.fill("Chantiers multiples du cadrage")
        field.press("Enter")
        alert = page.locator("[data-role='new-form'] [role='alert']")
        alert.wait_for(timeout=APPEAR_MS)
        assert "existe déjà" in alert.inner_text()
        field.fill("Un troisième chantier")
        field.press("Enter")
        page.locator("[data-role='atelier-status']").wait_for(state="attached", timeout=APPEAR_MS)
        assert (root / "_grimoire" / "cadrage" / "un-troisieme-chantier" / "chantier.yaml").is_file()


def test_block_edit_only_changes_its_range(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    path = root / "_grimoire" / "cadrage" / "dicter-une-idee-depuis-le-cockpit" / BRAINSTORM
    for page in _open(browser, url, "#atelier?chantier=dicter-une-idee-depuis-le-cockpit"):
        block = page.locator(".at-blk", has_text="Bouton maintenu pour parler").first
        block.wait_for(timeout=APPEAR_MS)
        before = path.read_bytes()
        block.focus()
        block.press("Enter")
        area = page.get_by_label("Modifier le bloc", exact=False).locator("xpath=self::textarea")
        area.wait_for(timeout=APPEAR_MS)
        # Échap ferme sans rien écrire.
        area.press("Escape")
        assert path.read_bytes() == before
        block.press("Enter")
        area.fill("- Bouton maintenu pour parler, transcription locale.\n- Repli sur le navigateur.")
        area.press("Control+Enter")
        page.locator("[data-role='atelier-status']", has_text="Bloc enregistré").wait_for(timeout=APPEAR_MS)
        after = path.read_bytes()
        start = before.index(b"- Bouton maintenu")
        end = before.index(b"- Repli sur le navigateur.") + len(b"- Repli sur le navigateur.")
        assert after == before[:start] + b"- Bouton maintenu pour parler, transcription locale.\n- Repli sur le navigateur." + before[end:]


def test_concurrent_write_is_409(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    path = root / "_grimoire" / "cadrage" / "chantiers-multiples-du-cadrage" / "01-brief.md"
    for page in _open(browser, url, "#atelier?chantier=chantiers-multiples-du-cadrage"):
        block = page.locator(".at-blk", has_text="à compléter").first
        block.wait_for(timeout=APPEAR_MS)
        block.click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.wait_for(timeout=APPEAR_MS)
        area.fill("Ma version")
        # Quelqu'un d'autre écrit le fichier entre-temps.
        path.write_text(path.read_text(encoding="utf-8") + "\nAjout concurrent.\n", encoding="utf-8")
        concurrent = path.read_bytes()
        area.press("Control+Enter")
        status = page.locator("[data-role='atelier-status']", has_text="a changé entre-temps")
        status.wait_for(timeout=APPEAR_MS)
        assert path.read_bytes() == concurrent


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_captures(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, _root = project_on
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, "#atelier", size):
        page.locator("a.at-row").first.wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(folder / f"atelier-rangement-{size[0]}x{size[1]}.png"))
    for page in _open(browser, url, "#atelier?chantier=dicter-une-idee-depuis-le-cockpit", size):
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(folder / f"atelier-feuille-{size[0]}x{size[1]}.png"))
