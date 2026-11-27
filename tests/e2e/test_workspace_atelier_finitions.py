"""Atelier de spec, finitions (#1197, #1342) : « Rattacher ici » et la dérive détaillée, à l'écran.

Un commentaire orphelin se rattache, sur le geste de l'humain, à la sélection du document ou au bloc courant, et le fil
garde la trace du rattachement. Une exigence validée puis modifiée montre le diff « validé -> courant » ; une validation
antérieure à la conservation du texte reste signalée sans détail, avec une phrase qui le dit.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ; sans la variable le test de capture ne
fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core import atelier_comments as store
from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.tools import workspace_atelier, workspace_atelier_comments

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
BRAINSTORM = "02-brainstorm.md"
PARA_A = "Le bouton maintenu sert à parler tant que la touche est tenue."
PARA_B = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "tant que la touche est tenue"
NEW_A = "Le bouton maintenu sert à parler jusqu'au relâchement de la touche."
NEW_PICK = "jusqu'au relâchement"
EXIGENCES = "# Exigences\n\n- REQ-001 [Must] : maintenir un bouton pour dicter une idée\n  - la transcription apparaît en moins de 2 secondes\n"
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _human_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """La MOA au terminal, simulée pour *préparer* l'état : l'écran, lui, n'a aucun moyen de valider."""
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    from ruamel.yaml import YAML

    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-finitions", roots):
        root = roots[0]
        yaml = YAML()
        path = root / "project-context.yaml"
        data = yaml.load(path.read_text(encoding="utf-8"))
        governance = data.get("governance") or {}
        governance["roles"] = {**(governance.get("roles") or {}), "moa": "Ada"}
        data["governance"] = governance
        with path.open("w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
        workspace_atelier.set_atelier(root, True)
        yield url, root


def _folder(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug


def _chantier(root: Path, titre: str) -> str:
    made = new_chantier(root, titre)
    path = _folder(root, made.slug) / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", f"{PARA_A}\n\n{PARA_B}"), encoding="utf-8")
    (_folder(root, made.slug) / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str, text: str, phase: str = "brainstorm") -> str:
    found = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == phase)
    return str(next(b for b in found["blocks"] if text in b["text"])["id"])


def _orphan(root: Path, slug: str, texte: str) -> str:
    made = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug, PARA_A), "texte": texte, "quote": QUOTE}, "humain")
    return str(made["comment"]["id"])


def _rewrite(root: Path, slug: str, old: str, new: str, name: str = BRAINSTORM) -> None:
    path = _folder(root, slug) / name
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


def _open(browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _select(page: Page, quote: str) -> None:
    done = page.evaluate(
        """(quote) => {
            const host = [...document.querySelectorAll('.at-blk')].find((h) => h.textContent.includes(quote));
            const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                const at = node.data.indexOf(quote);
                if (at < 0) continue;
                const range = document.createRange();
                range.setStart(node, at);
                range.setEnd(node, at + quote.length);
                const selection = getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                return true;
            }
            return false;
        }""",
        quote,
    )
    assert done, quote


def _thread(page: Page, comment_id: str) -> object:
    return page.locator(f"[data-com='{comment_id}']")


def test_an_orphan_is_attached_to_the_selection_and_keeps_the_trace(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Rattacher a la selection")
    cid = _orphan(root, slug, "Que se passe-t-il quand la touche est lâchée ?")
    _rewrite(root, slug, PARA_A, NEW_A)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        thread = _thread(page, cid)
        thread.wait_for(timeout=APPEAR_MS)
        assert thread.get_attribute("data-anchor") == "orphelin"
        page.locator(".at-blk", has_text=NEW_PICK).wait_for(timeout=APPEAR_MS)
        _select(page, NEW_PICK)
        thread.get_by_role("button", name="Rattacher ce commentaire à la sélection du document ou au bloc courant").click()
        page.locator("[data-role='atelier-status']", has_text="rattaché à la sélection").wait_for(timeout=APPEAR_MS)
        thread = _thread(page, cid)
        assert thread.get_attribute("data-anchor") == "ancre"
        assert thread.locator("[data-role='quote']").inner_text() == NEW_PICK
        assert "Rattaché" in thread.locator("[data-role='attach-history']").inner_text()
        assert page.evaluate("CSS.highlights.has('at-cm')") is True  # le passage est surligné de nouveau
    (saved,) = store.load(_folder(root, slug))
    assert saved.quote == NEW_PICK and saved.orphelin is False
    (trace,) = saved.historique
    assert trace["avant"]["quote"] == QUOTE and trace["apres"]["quote"] == NEW_PICK


def test_without_a_selection_the_orphan_is_attached_to_the_current_block(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Rattacher au bloc")
    cid = _orphan(root, slug, "Et le second paragraphe ?")
    _rewrite(root, slug, PARA_A, NEW_A)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        thread = _thread(page, cid)
        thread.wait_for(timeout=APPEAR_MS)
        attach = thread.get_by_role("button", name="Rattacher ce commentaire à la sélection du document ou au bloc courant")
        attach.click()
        # Mode explicite « choisis le bloc » : rien n'est écrit, les blocs deviennent des cibles.
        assert "Choisis le bloc" in thread.locator("[data-role='attach-pick']").inner_text()
        assert page.locator(".at-sheet.is-picking").count() == 1
        assert store.load(_folder(root, slug))[0].historique == []
        page.keyboard.press("Escape")  # Échap annule : plus de cibles, rien d'écrit, le focus revient au bouton
        assert page.locator(".at-sheet.is-picking").count() == 0
        assert thread.locator("[data-role='attach-pick']").count() == 0
        assert store.load(_folder(root, slug))[0].historique == []
        attach.click()
        page.locator(".at-blk", has_text=PARA_B).click()  # le clic choisit le bloc, il ne l'ouvre pas en édition
        page.locator("[data-role='atelier-status']", has_text="rattaché au bloc").wait_for(timeout=APPEAR_MS)
        assert page.locator(".is-editing").count() == 0
        assert _thread(page, cid).get_attribute("data-anchor") == "ancre"
    assert store.load(_folder(root, slug))[0].quote == PARA_B


def test_a_drifted_requirement_shows_what_changed(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Derive detaillee")
    rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")
    _rewrite(root, slug, "2 secondes", "1 seconde", "04-exigences.md")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        line = page.locator(".at-live[data-live='REQ-001']:not([hidden])")
        line.wait_for(timeout=APPEAR_MS)
        line.get_by_role("button", name="Détail de REQ-001").click()
        diff = line.locator("[data-role='drift-diff']")
        diff.wait_for(timeout=APPEAR_MS)
        assert "- critère : la transcription apparaît en moins de 2 secondes" in diff.inner_text()
        assert "+ critère : la transcription apparaît en moins de 1 seconde" in diff.inner_text()
        assert line.locator("[data-role='drift-note']").count() == 0


def test_a_requirement_validated_before_the_text_was_kept_drifts_without_detail(
    browser: Browser, project_on: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, root = project_on
    slug = _chantier(root, "Derive sans detail")
    monkeypatch.setattr(rg, "validated_text", lambda *_: "")  # une validation d'avant : aucun texte rangé
    rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")
    _rewrite(root, slug, "2 secondes", "1 seconde", "04-exigences.md")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        line = page.locator(".at-live[data-live='REQ-001']:not([hidden])")
        line.wait_for(timeout=APPEAR_MS)
        line.get_by_role("button", name="Détail de REQ-001").click()
        note = line.locator("[data-role='drift-note']")
        note.wait_for(timeout=APPEAR_MS)
        assert "validée avant la conservation du texte validé" in note.inner_text()
        assert line.locator("[data-role='drift-diff']").count() == 0
        assert line.get_by_text("modifiée depuis la validation").count() >= 1


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_captures(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = "finitions-de-l-atelier"
    if not (_folder(root, slug)).is_dir():
        slug = _chantier(root, "Finitions de l'atelier")
        _orphan(root, slug, "Que se passe-t-il quand la touche est lâchée en pleine phrase ?")
        _rewrite(root, slug, PARA_A, NEW_A)
        rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")
        _rewrite(root, slug, "2 secondes", "1 seconde", "04-exigences.md")
        _rewrite(root, slug, "moins de", "moins d'environ", "04-exigences.md")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}", size):
        page.locator(".at-cm.is-orphan").first.wait_for(timeout=APPEAR_MS)
        _select(page, NEW_PICK)
        page.locator(".at-cm.is-orphan").first.evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-rattacher-ici-{size[0]}x{size[1]}.png"))
        line = page.locator(".at-live[data-live='REQ-001']:not([hidden])")
        line.wait_for(timeout=APPEAR_MS)
        line.get_by_role("button", name="Détail de REQ-001").click()
        line.locator("[data-role='drift-diff']").wait_for(timeout=APPEAR_MS)
        line.evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-derive-detaillee-{size[0]}x{size[1]}.png"))
