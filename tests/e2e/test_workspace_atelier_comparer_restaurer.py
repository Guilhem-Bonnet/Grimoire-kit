"""Atelier de spec : comparer deux versions, restaurer un bloc ou une phase, annuler.

Un chantier à trois commits et une modification non commitée du brief. Comparer (deux versions cochées, Maj+clic, « avec la
version actuelle »), restaurer un bloc par la route d'édition, restaurer une phase (confirmation, accusé, « Annuler »), conflit
(409), projet hors dépôt git. Aucune lecture instantanée après un geste : toute vérification attend l'état voulu (le fichier
sur disque, un élément de la page).

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 ; sans la variable le test de capture ne fait rien (il ne se
skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
WAIT_S = 30.0
_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Ada Lovelace", "GIT_AUTHOR_EMAIL": "ada@example.org",
    "GIT_COMMITTER_NAME": "Ada Lovelace", "GIT_COMMITTER_EMAIL": "ada@example.org",
}
BRIEF_1 = "# Brief\n\nDicter une idée au micro.\nSans clavier.\n\nUn paragraphe stable.\n"
BRIEF_3 = "# Brief\n\nDicter une idée au micro.\nAvec ou sans clavier.\nEn moins de deux secondes.\n\nUn paragraphe stable.\n"
BRIEF_WORKING = "# Brief\n\nDicter une idée au micro, vite.\nAvec ou sans clavier.\nEn moins de deux secondes.\n\nUn paragraphe stable.\n"
EXIG_1 = "# Exigences\n\n- REQ-001 [Must] : dicter une idée\n"
EXIG_2 = EXIG_1 + "- REQ-002 [Should] : repli navigateur\n"


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, check=True, capture_output=True, text=True, env=_ENV)
    return done.stdout.strip()


def _wait_until(check: Callable[[], bool], what: str) -> None:
    """Attend l'état voulu (jamais de lecture instantanée après un geste)."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError(f"état jamais atteint : {what}")


class Project:
    def __init__(self, url: str, root: Path, slug: str) -> None:
        self.url, self.root, self.slug = url, root, slug
        base = root / "_grimoire" / "cadrage" / slug
        self.brief, self.exig = base / "01-brief.md", base / "04-exigences.md"

    def reset(self) -> None:
        """L'état de départ de chaque test : trois commits, brief modifié et non commité."""
        self.brief.write_text(BRIEF_WORKING, encoding="utf-8")
        self.exig.write_text(EXIG_2, encoding="utf-8")

    def text(self) -> str:
        return self.brief.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project_git(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Project]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-restaurer-git", roots):
        root = roots[0]
        workspace_atelier.set_atelier(root, True)
        made = new_chantier(root, "Dictée vocale")
        project = Project(url, root, made.slug)
        rels = [str(project.brief.relative_to(root)), str(project.exig.relative_to(root))]
        project.brief.write_text(BRIEF_1, encoding="utf-8")
        project.exig.write_text(EXIG_1, encoding="utf-8")
        _git(root, "add", "--", *rels)
        _git(root, "commit", "-q", "-m", "feat: premier jet du cadrage")
        project.exig.write_text(EXIG_2, encoding="utf-8")
        _git(root, "add", "--", rels[1])
        _git(root, "commit", "-q", "-m", "feat: ajoute le repli")
        project.brief.write_text(BRIEF_3, encoding="utf-8")
        _git(root, "add", "--", rels[0])
        _git(root, "commit", "-q", "-m", "fix: précise le brief")
        project.reset()
        yield project


@pytest.fixture(scope="module")
def project_nogit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-restaurer-nogit", roots):
        root = roots[0]
        shutil.rmtree(root / ".git", ignore_errors=True)
        workspace_atelier.set_atelier(root, True)
        yield url, new_chantier(root, "Sans dépôt").slug


def _doc(browser: Browser, url: str, slug: str, viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        yield page
    finally:
        context.close()


def _open_panel(page: Page) -> None:
    page.locator("[data-role='atelier-historique-bouton']").click()
    page.locator(".at-hist-row").first.wait_for(timeout=APPEAR_MS)


def _item(page: Page, subject: str):  # type: ignore[no-untyped-def]
    return page.locator(".at-hist-item", has_text=subject)


def _compare_with_current(page: Page, subject: str) -> None:
    _item(page, subject).locator("[data-role='hist-vs-actuelle']").click()
    page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
    page.locator("[data-role='hist-restaurer-phase']").first.wait_for(timeout=APPEAR_MS)


def _phase(page: Page, phase_id: str):  # type: ignore[no-untyped-def]
    return page.locator(f".at-cmp-phase[data-phase='{phase_id}']")


# ── Comparer ─────────────────────────────────────────────────────────────────────────────────────────────


def test_compare_two_chosen_versions_with_checkboxes(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        expect(page.locator(".at-hist-row")).to_have_count(4)  # modifications en cours + trois commits
        pair = page.locator("[data-role='hist-selection']")
        expect(pair).to_be_hidden()
        _item(page, "fix: précise le brief").locator("[data-role='hist-choisir']").check()
        pair.wait_for(state="visible", timeout=APPEAR_MS)
        expect(page.locator("[data-role='hist-comparer-deux']")).to_be_disabled()
        _item(page, "feat: premier jet").locator("[data-role='hist-choisir']").check()
        page.locator("[data-role='hist-comparer-deux']:not([disabled])").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='hist-comparer-deux']").click()
        banner = page.locator("[data-role='hist-bandeau']")
        banner.wait_for(timeout=APPEAR_MS)
        expect(banner).to_have_text(re.compile(r"^Tu compares la version du .* et la version du ", re.DOTALL), use_inner_text=True)
        expect(page.locator(".at-sheet")).to_be_hidden()
        expect(page.locator(".at-cmp-line.is-add")).to_have_count(3)  # brief +2 −1, REQ-002 +1
        expect(page.locator(".at-cmp-line.is-del")).to_have_count(1)
        # deux versions dont aucune n'est « actuelle » : on compare, on ne restaure pas
        expect(page.locator("[data-role='hist-restaurer-phase']")).to_have_count(0)
        expect(page.locator("[data-role='hist-restaurer-bloc']")).to_have_count(0)
        page.locator("[data-role='hist-retour']").click()
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        expect(page.locator(".at-sheet")).to_be_visible()


def test_compare_two_versions_with_shift_click(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        page.locator(".at-hist-row", has_text="feat: ajoute le repli").click(modifiers=["Shift"])
        page.locator("[data-role='hist-selection']").wait_for(state="visible", timeout=APPEAR_MS)
        expect(page.locator("[data-role='hist-comparaison']")).to_have_count(0)  # Maj+clic choisit, il n'ouvre rien (lu après l'effet visible du clic)
        page.locator(".at-hist-row", has_text="feat: premier jet").click(modifiers=["Shift"])
        page.locator("[data-role='hist-comparer-deux']:not([disabled])").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='hist-comparer-deux']").click()
        page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
        _phase(page, "exigences").wait_for(timeout=APPEAR_MS)
        expect(page.locator(".at-cmp [data-phase='brief']")).to_have_count(0)  # le brief n'a pas changé entre ces deux versions
        expect(_phase(page, "exigences")).to_contain_text("REQ-002", use_inner_text=True)


def test_compare_with_the_current_version(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        expect(_item(page, "Modifications en cours").locator("[data-role='hist-vs-actuelle']")).to_have_count(0)
        _compare_with_current(page, "feat: premier jet")
        expect(page.locator("[data-role='hist-bandeau']")).to_have_text(re.compile(r"^Tu compares la version du .*et la version actuelle\.", re.DOTALL), use_inner_text=True)
        expect(page.locator(".at-cmp-line.is-add").first).to_be_visible()
        # brief et exigences se restaurent ; les phases créées depuis (absentes de la version) restent en lecture seule, avec la raison
        expect(page.locator("[data-role='hist-restaurer-phase']:not([disabled])")).to_have_count(2)
        expect(page.locator("[data-role='hist-restaurer-phase'][disabled]")).to_have_count(3)
        expect(page.locator("[data-role='hist-refus']").first).to_contain_text("n'a pas ce fichier", use_inner_text=True)
        expect(page.locator("[data-role='hist-restaurer-bloc']").first).to_be_attached()


# ── Restaurer un bloc ────────────────────────────────────────────────────────────────────────────────────


def test_restore_a_block_then_undo(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _compare_with_current(page, "feat: premier jet")
        block = _phase(page, "brief").locator("[data-role='hist-restaurer-bloc']").first
        block.click()
        dialog = page.locator("[data-role='hist-confirmation']")
        dialog.wait_for(state="visible", timeout=APPEAR_MS)
        for part in ("Restaurer ce bloc", "+", "−", "Rien n’est commité"):
            expect(dialog).to_contain_text(part, use_inner_text=True)
        page.locator("[data-role='hist-renoncer']").click()
        dialog.wait_for(state="detached", timeout=APPEAR_MS)
        assert project_git.text() == BRIEF_WORKING  # renoncer n'écrit rien
        block.click()
        dialog.wait_for(state="visible", timeout=APPEAR_MS)
        page.locator("[data-role='hist-confirmer']").click()
        ack = page.locator("[data-role='hist-accuse']")
        ack.filter(has_text="Le bloc restauré").wait_for(timeout=APPEAR_MS)
        expect(ack).to_contain_text("pas commité", use_inner_text=True)
        _wait_until(lambda: project_git.text() == BRIEF_1, "le bloc revient à la version choisie")
        assert _git(project_git.root, "status", "--porcelain", "--", str(project_git.brief)).startswith("M")  # non commité
        page.locator("[data-role='hist-annuler']").click()
        ack.filter(has_text="Annulé").wait_for(timeout=APPEAR_MS)
        _wait_until(lambda: project_git.text() == BRIEF_WORKING, "l'annulation remet le texte d'avant")


# ── Restaurer une phase ──────────────────────────────────────────────────────────────────────────────────


def test_restore_a_phase_then_undo(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    head = _git(project_git.root, "rev-parse", "HEAD")
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _compare_with_current(page, "feat: premier jet")
        _phase(page, "brief").locator("[data-role='hist-restaurer-phase']").click()
        dialog = page.locator("[data-role='hist-confirmation']")
        dialog.wait_for(state="visible", timeout=APPEAR_MS)
        expect(dialog).to_contain_text("Restaurer la phase Brief", use_inner_text=True)
        expect(dialog).to_contain_text("+2 −3 lignes", use_inner_text=True)
        page.keyboard.press("Escape")  # Échap = renoncer
        dialog.wait_for(state="detached", timeout=APPEAR_MS)
        assert project_git.text() == BRIEF_WORKING
        _phase(page, "brief").locator("[data-role='hist-restaurer-phase']").click()
        dialog.wait_for(state="visible", timeout=APPEAR_MS)
        page.locator("[data-role='hist-confirmer']").click()
        ack = page.locator("[data-role='hist-accuse']")
        ack.filter(has_text="La phase Brief restaurée").wait_for(timeout=APPEAR_MS)
        expect(ack).to_contain_text("pas commité", use_inner_text=True)
        _wait_until(lambda: project_git.text() == BRIEF_1, "la phase revient à la version choisie")
        assert _git(project_git.root, "rev-parse", "HEAD") == head  # rien n'est commité par le kit
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(state="visible", timeout=APPEAR_MS)
        page.locator(".at-sheet", has_text="Sans clavier.").wait_for(timeout=APPEAR_MS)  # le document relu montre le texte restauré
        page.locator("[data-role='hist-annuler']").click()
        ack.filter(has_text="Annulé").wait_for(timeout=APPEAR_MS)
        _wait_until(lambda: project_git.text() == BRIEF_WORKING, "l'annulation remet le contenu d'avant")
        page.locator(".at-sheet", has_text="vite.").wait_for(timeout=APPEAR_MS)


def test_restore_conflict_when_the_file_changed(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _compare_with_current(page, "feat: premier jet")
        edited = BRIEF_WORKING + "\nAjout d'un autre auteur.\n"
        project_git.brief.write_text(edited, encoding="utf-8")  # le fichier change entre la lecture et le geste
        _phase(page, "brief").locator("[data-role='hist-restaurer-phase']").click()
        page.locator("[data-role='hist-confirmation']").wait_for(state="visible", timeout=APPEAR_MS)
        page.locator("[data-role='hist-confirmer']").click()
        ack = page.locator("[data-role='hist-accuse']")
        ack.filter(has_text="a changé depuis ta lecture").wait_for(timeout=APPEAR_MS)
        expect(ack).to_have_attribute("data-kind", "error")
        assert project_git.text() == edited  # rien n'est écrit
        # la comparaison est relue : la nouvelle empreinte permet de restaurer
        page.locator(".at-cmp", has_text="Ajout d'un autre auteur.").wait_for(timeout=APPEAR_MS)


# ── Pas de git ───────────────────────────────────────────────────────────────────────────────────────────


def test_no_git_stays_read_only(browser: Browser, project_nogit: tuple[str, str]) -> None:
    url, slug = project_nogit
    for page in _doc(browser, url, slug):
        page.locator("[data-role='atelier-historique-bouton']").click()
        page.locator("[data-role='hist-vide']").wait_for(timeout=APPEAR_MS)
        expect(page.locator("[data-role='hist-vide']")).to_contain_text("dépôt git", use_inner_text=True)
        expect(page.locator("[data-role='hist-vs-actuelle'], [data-role='hist-choisir'], [data-role='hist-restaurer-phase']")).to_have_count(0)
        status = page.evaluate(
            "async (slug) => (await fetch(`/api/workspace/atelier/chantiers/${slug}/compare?from=abcdef1&to=working`)).status", slug
        )
        assert status == 404


def test_capture(browser: Browser, project_git: Project) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _item(page, "fix: précise le brief").locator("[data-role='hist-choisir']").check()
        _item(page, "feat: premier jet").locator("[data-role='hist-choisir']").check()
        page.locator("[data-role='hist-comparer-deux']:not([disabled])").wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(Path(out) / "01-selection-de-deux-versions.png"))
        page.locator("[data-role='hist-comparer-deux']").click()
        page.locator("[data-role='hist-bandeau']").wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(Path(out) / "02-comparer-deux-versions.png"))
        page.locator("[data-role='hist-retour']").click()
        page.locator("[data-role='hist-comparaison']").wait_for(state="detached", timeout=APPEAR_MS)
        _compare_with_current(page, "feat: premier jet")
        page.screenshot(path=str(Path(out) / "03-comparer-avec-la-version-actuelle.png"))
        _phase(page, "brief").locator("[data-role='hist-restaurer-phase']").click()
        page.locator("[data-role='hist-confirmation']").wait_for(state="visible", timeout=APPEAR_MS)
        page.screenshot(path=str(Path(out) / "04-confirmation.png"))
        page.locator("[data-role='hist-confirmer']").click()
        page.locator("[data-role='hist-accuse']").filter(has_text="La phase Brief restaurée").wait_for(timeout=APPEAR_MS)
        _wait_until(lambda: project_git.text() == BRIEF_1, "la phase est restaurée")
        page.screenshot(path=str(Path(out) / "05-accuse-avec-annuler.png"))
        page.locator("[data-role='hist-annuler']").click()
        page.locator("[data-role='hist-accuse']").filter(has_text="Annulé").wait_for(timeout=APPEAR_MS)
        _wait_until(lambda: project_git.text() == BRIEF_WORKING, "l'annulation est faite")
    project_git.reset()
