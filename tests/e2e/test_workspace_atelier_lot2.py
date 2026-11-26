"""Atelier de spec, lot 2 (#1197, #1342) : l'agent suggère, l'humain accepte ou refuse à l'écran.

Une suggestion acceptée réécrit la seule plage du bloc dans le fichier de phase ; une refusée ne touche pas le
fichier ; une suggestion dont le bloc a changé depuis le dépôt répond 409 (périmée) et n'écrit rien ; drapeau
éteint, les routes répondent 404. Le dépôt passe par la fonction que l'outil MCP appelle, jamais par la page.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ; sans la variable le test de
capture ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core import atelier_suggestions as store
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_suggestions

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
BRAINSTORM = "02-brainstorm.md"
PISTES = "- Bouton maintenu pour parler.\n- Repli sur le navigateur."


def _phase_file(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug / BRAINSTORM


def _chantier(root: Path, titre: str) -> str:
    made = new_chantier(root, titre)
    path = _phase_file(root, made.slug)
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", PISTES), encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str, text: str) -> str:
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    return str(next(b for b in phase["blocks"] if b["text"] == text)["id"])


def _suggest(root: Path, slug: str, texte: str, **extra: str) -> dict[str, str]:
    """Dépose comme le fait l'outil MCP : un appel de fonction, jamais un clic."""
    body = {"block_id": _block_id(root, slug, PISTES), "texte": texte, **extra}
    return dict(workspace_atelier_suggestions.deposit(root, slug, body)["suggestion"])


def _stored(root: Path, slug: str) -> list[store.Suggestion]:
    return store.load(root / "_grimoire" / "cadrage" / slug)


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier2-off", roots):
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier2-on", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _open(browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def test_flag_off_routes_are_404(browser: Browser, project_off: tuple[str, Path]) -> None:
    url, _root = project_off
    for page in _open(browser, url):
        # Par l'API de la page (jeton de session et projet compris) : le 404 est celui du drapeau, pas d'une garde.
        script = """async (calls) => {
            const { api, ApiError } = await import('/workspace/api.js');
            const out = [];
            for (const run of calls) {
                try { await api[run[0]](...run.slice(1)); out.push(200); }
                catch (error) { out.push(error instanceof ApiError ? error.code : String(error)); }
            }
            return out;
        }"""
        calls = [["atelierDecision", "x", "sug-1", "accept", ""], ["atelierChantier", "x"]]
        assert page.evaluate(script, calls) == [404, 404]
        raw = page.evaluate("async () => (await fetch('/api/workspace/atelier/chantiers/x/suggestions')).status")
        assert raw == 404


def test_accept_rewrites_only_the_block(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Suggestion acceptée")
    made = _suggest(root, slug, "- Bouton maintenu pour parler, transcription locale.\n- Repli sur le navigateur.", motif="dire où ça tourne", posture="simplificateur")
    path = _phase_file(root, slug)
    before = path.read_bytes()
    assert made["etat"] == "proposee"
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        card = page.locator(f"[data-sugg='{made['id']}']")
        card.wait_for(timeout=APPEAR_MS)
        text = card.inner_text()
        assert "Simplificateur" in text and "dire où ça tourne" in text
        assert "Bouton maintenu pour parler." in card.locator("del").inner_text()
        assert "transcription locale" in card.locator("ins").inner_text()
        assert page.locator("[data-role='doc-sugg-count']").inner_text().strip() == "1 suggestion en attente."
        assert path.read_bytes() == before  # le dépôt et l'affichage n'ont rien écrit
        card.get_by_role("button", name="Accepter").click()
        page.locator("[data-role='atelier-status']", has_text="Suggestion acceptée").wait_for(timeout=APPEAR_MS)
        assert page.locator(f"[data-sugg='{made['id']}']").count() == 0
    after = path.read_bytes()
    assert after == before.replace(PISTES.encode(), b"- Bouton maintenu pour parler, transcription locale.\n- Repli sur le navigateur.", 1)
    assert [s.etat for s in _stored(root, slug)] == ["acceptee"]


def test_reject_leaves_the_file_alone_and_keeps_the_reason(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Suggestion refusée")
    made = _suggest(root, slug, "- Tout réécrire", posture="sceptique")
    path = _phase_file(root, slug)
    before = path.read_bytes()
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        card = page.locator(f"[data-sugg='{made['id']}']")
        card.wait_for(timeout=APPEAR_MS)
        card.get_by_role("button", name="Refuser").click()
        card.get_by_label("Motif du refus (facultatif)").fill("trop large")
        card.get_by_role("button", name="Confirmer le refus").click()
        page.locator("[data-role='atelier-status']", has_text="Suggestion refusée").wait_for(timeout=APPEAR_MS)
        assert page.locator(f"[data-sugg='{made['id']}']").count() == 0
    assert path.read_bytes() == before
    (kept,) = _stored(root, slug)
    assert kept.etat == "refusee" and kept.decide_motif == "trop large" and kept.texte == "- Tout réécrire"


def test_stale_suggestion_is_409_and_writes_nothing(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Suggestion périmée")
    made = _suggest(root, slug, "- Bouton maintenu pour parler, version agent.\n- Repli sur le navigateur.")
    path = _phase_file(root, slug)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        card = page.locator(f"[data-sugg='{made['id']}']")
        card.wait_for(timeout=APPEAR_MS)
        # Quelqu'un réécrit le bloc visé entre le dépôt et la décision.
        path.write_text(path.read_text(encoding="utf-8").replace("pour parler.", "pour parler, version humaine."), encoding="utf-8")
        concurrent = path.read_bytes()
        with page.expect_response(lambda r: "/suggestions/" in r.url and r.request.method == "POST") as seen:
            card.get_by_role("button", name="Accepter").click()
        assert seen.value.status == 409
        page.locator("[data-role='atelier-status']", has_text="elle est périmée").wait_for(timeout=APPEAR_MS)
        assert page.locator(f"[data-sugg='{made['id']}']").count() == 0
        assert path.read_bytes() == concurrent
    assert [s.etat for s in _stored(root, slug)] == ["perimee"]


def test_library_counts_pending_suggestions(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Chantier au compteur")
    _suggest(root, slug, "- Une première version.")
    _suggest(root, slug, "- Une seconde version.")
    for page in _open(browser, url, "#atelier"):
        row = page.locator(f"a.at-row[data-slug='{slug}']")
        row.wait_for(timeout=APPEAR_MS)
        assert row.locator("[data-role='sugg-count']").inner_text() == "2 suggestions"


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_captures(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = "dicter-une-idee-depuis-le-cockpit"
    if not _phase_file(root, slug).is_file():
        slug = _chantier(root, "Dicter une idée depuis le cockpit")
        _suggest(
            root,
            slug,
            "- Bouton maintenu pour parler, transcription locale sur le GPU du serveur.\n- Repli sur le navigateur sans GPU.",
            motif="Dire où tourne la transcription : c'est la contrainte qui décide du reste.",
            posture="simplificateur",
        )
        _chantier(root, "Chantiers multiples du cadrage")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, "#atelier", size):
        page.locator("[data-role='sugg-count']").first.wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(folder / f"atelier-rangement-suggestions-{size[0]}x{size[1]}.png"))
    for page in _open(browser, url, f"#atelier?chantier={slug}", size):
        card = page.locator(".at-sugg").first
        card.wait_for(timeout=APPEAR_MS)
        card.evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-suggestion-{size[0]}x{size[1]}.png"))
