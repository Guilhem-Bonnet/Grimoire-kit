"""Atelier de spec, lot 4 (#1197, #1342) : le document vivant et « Valider et découper », à l'écran.

Chaque exigence du document affiche l'état réel de ce qui la sert (critère -> tâche -> état) ; « Valider » donne la
commande du terminal (la MOA seule valide, le cockpit n'a aucun canal qui prouve l'humain) ; « Valider et découper »
montre un aperçu puis crée les tâches, sans doublon au rejeu. Drapeau éteint : 404.

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

from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
EXIGENCES = """# Exigences

- REQ-001 [Must] : maintenir un bouton pour dicter une idée
  - la transcription apparaît en moins de 2 secondes
- REQ-002 (Should) : repli sur le navigateur sans GPU
  - `pytest tests/test_repli.py`
- REQ-003 (Should) : historique des dictées
  - les 20 dernières dictées sont listées
- REQ-004 (Could) : choix de la langue
- REQ-005 (Won't) : traduction automatique
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _human_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """La MOA au terminal, simulée pour *préparer* l'état : l'écran, lui, n'a aucun moyen de valider."""
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier4-off", roots):
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    from ruamel.yaml import YAML

    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier4-on", roots):
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


def _chantier(root: Path, titre: str, text: str = EXIGENCES) -> str:
    made = new_chantier(root, titre)
    (root / "_grimoire" / "cadrage" / made.slug / "04-exigences.md").write_text(text, encoding="utf-8")
    return made.slug


def _validate(root: Path, slug: str, *reqs: str) -> None:
    for req in reqs:
        rg.approve_requirement(root, f"{slug}/{req}", actor="ada")


def _open(
    browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900), *, clipboard: bool = False
) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    if clipboard:
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _line(page: Page, req: str) -> object:
    return page.locator(f".at-live[data-live='{req}']:not([hidden])")


def _plan_tasks(root: Path) -> list[str]:
    return sorted(t.id for t in TaskService(root).list_tasks() if (t.extra or {}).get("cadrage") == "plan")


def test_flag_off_routes_are_404(browser: Browser, project_off: tuple[str, Path]) -> None:
    url, _root = project_off
    for page in _open(browser, url):
        script = """async (calls) => {
            const { api, ApiError } = await import('/workspace/api.js');
            const out = [];
            for (const run of calls) {
                try { await api[run[0]](...run.slice(1)); out.push(200); }
                catch (error) { out.push(error instanceof ApiError ? error.code : String(error)); }
            }
            return out;
        }"""
        calls = [["atelierTrace", "x"], ["atelierDecoupage", "x"], ["atelierDecouper", "x", "abc"]]
        assert page.evaluate(script, calls) == [404, 404, 404]
        raw = page.evaluate("async () => (await fetch('/api/workspace/atelier/chantiers/x/trace')).status")
        assert raw == 404


def test_criterion_to_task_to_displayed_state(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Du critère à la tâche")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        first = _line(page, "REQ-001")
        first.wait_for(timeout=APPEAR_MS)
        assert first.get_attribute("data-state") == "proposee"
        assert "proposée" in first.inner_text() and "aucune tâche" in first.inner_text()
        assert _line(page, "REQ-004").inner_text().count("exigence sans critère") == 1  # le trou est dit
        assert page.locator(".at-live[data-live='REQ-005']:not([hidden])").count() == 0  # Won't : pas planifiée

        _validate(root, slug, "REQ-001")  # la MOA, au terminal
        page.get_by_role("button", name="Actualiser l’état des exigences").click()
        page.locator(".at-live[data-live='REQ-001'][data-state='validee']").wait_for(timeout=APPEAR_MS)
        assert "validée sans tâche" in _line(page, "REQ-001").inner_text()

        task = TaskService(root).add("Dictée", ("la transcription est rapide",), requirement=f"{slug}/REQ-001", owner="ada").task
        page.get_by_role("button", name="Actualiser l’état des exigences").click()
        row = _line(page, "REQ-001")
        row.get_by_text("1 tâche").wait_for(timeout=APPEAR_MS)
        assert "validée sans tâche" not in row.inner_text()
        row.get_by_role("button", name="Détail de REQ-001").click()
        assert task.id in row.locator("[data-role='live-detail']").inner_text()

        # La tâche avance : l'état réel suit (une tâche qui ne peut plus avancer compte comme en cours).
        service = TaskService(root)
        service.transition(task.id, TaskState.READY, "ada")
        service.transition(task.id, TaskState.BLOCKED, "ada", reason="attend la config du micro")
        page.get_by_role("button", name="Actualiser l’état des exigences").click()
        page.locator(".at-live[data-live='REQ-001'][data-state='en-cours']").wait_for(timeout=APPEAR_MS)


def test_an_edited_requirement_asks_to_be_validated_again(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Exigence modifiée")
    _validate(root, slug, "REQ-001")
    path = root / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _line(page, "REQ-001").wait_for(timeout=APPEAR_MS)
        assert page.locator(".at-live[data-live='REQ-001'][data-state='validee']").count() == 1
        path.write_text(path.read_text(encoding="utf-8").replace("2 secondes", "1 seconde"), encoding="utf-8")
        page.get_by_role("button", name="Actualiser l’état des exigences").click()
        row = _line(page, "REQ-001")
        row.get_by_text("modifiée depuis la validation").first.wait_for(timeout=APPEAR_MS)
        assert row.get_attribute("data-state") == "proposee"
        assert row.get_by_role("button", name="Revalider REQ-001 : copier la commande du terminal").count() == 1


def test_validate_gives_the_terminal_command_and_never_validates(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Validation au terminal")
    sealed = AuthorityJournal().path
    before = sealed.read_bytes() if sealed.exists() else b""
    for page in _open(browser, url, f"#atelier?chantier={slug}", clipboard=True):
        row = _line(page, "REQ-001")
        row.wait_for(timeout=APPEAR_MS)
        row.get_by_role("button", name="Valider REQ-001 : copier la commande du terminal").click()
        command = f"grimoire cadrage approve REQ-001 --chantier {slug}"
        assert row.locator("[data-role='approve-command']").inner_text() == command
        row.get_by_role("button", name="Copier la commande", exact=True).click()
        page.locator("[data-role='atelier-status']", has_text="Commande copiée").wait_for(timeout=APPEAR_MS)
        assert page.evaluate("() => navigator.clipboard.readText()") == command
        # Rien n'a été validé : l'écran n'a aucun geste qui scelle.
        assert (sealed.read_bytes() if sealed.exists() else b"") == before
        assert row.get_attribute("data-state") == "proposee"
        # Un agent qui poste sur une route de validation n'en trouve aucune (ou n'a pas le jeton de la page).
        status = page.evaluate(
            """async (slug) => (await fetch(`/api/workspace/atelier/chantiers/${slug}/exigences/REQ-001/valider`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ origin: 'human' }),
            })).status""",
            slug,
        )
        assert status in (403, 404)
        assert (sealed.read_bytes() if sealed.exists() else b"") == before


def test_split_preview_then_confirm_creates_once_and_replay_creates_nothing(
    browser: Browser, project_on: tuple[str, Path]
) -> None:
    url, root = project_on
    slug = _chantier(root, "Valider et découper")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _line(page, "REQ-001").wait_for(timeout=APPEAR_MS)
        split = page.get_by_role("button", name="Valider et découper")
        split.wait_for(timeout=APPEAR_MS)
        assert split.get_attribute("aria-disabled") == "true"  # le Must n'est pas validé
        assert "à valider d’abord REQ-001" in page.locator("[data-role='live-summary']").inner_text()

        _validate(root, slug, "REQ-001")
        page.get_by_role("button", name="Actualiser l’état des exigences").click()
        page.wait_for_function("() => document.querySelector('[data-act=\"split\"]').getAttribute('aria-disabled') === 'false'", timeout=APPEAR_MS)
        before = _plan_tasks(root)
        split.click()
        panel = page.locator("[data-role='decoupage']")
        panel.wait_for(timeout=APPEAR_MS)
        assert panel.locator("[data-create]").count() == 3  # REQ-001, 002, 003 : une tâche par exigence avec critère
        # Le texte des critères qui seront écrits se lit dans l'aperçu, sans geste.
        shown = panel.locator("[data-create='REQ-001'] [data-role='criteria']")
        assert "la transcription apparaît en moins de 2 secondes" in shown.inner_text()
        assert shown.locator("li").first.is_visible()
        assert panel.locator("[data-skipped='REQ-004']").inner_text().count("sans critère") == 1
        assert panel.locator("[data-skipped='REQ-005']").inner_text().count("hors périmètre") == 1
        assert _plan_tasks(root) == before  # l'aperçu n'écrit rien

        with page.expect_response(lambda r: "/decoupage" in r.url and r.request.method == "POST") as seen:
            panel.get_by_role("button", name="Créer 3 tâches").click()
        assert seen.value.status == 200
        page.locator("[data-role='atelier-status']", has_text="3 tâches créées").wait_for(timeout=APPEAR_MS)
        assert "Aucun travail n’est lancé" in page.locator("[data-role='atelier-status']").inner_text()
        created = _plan_tasks(root)
        assert len(created) == len(before) + 3
        page.locator(".at-live[data-live='REQ-002']").get_by_text("1 tâche").wait_for(timeout=APPEAR_MS)
        assert all(t.status.value in ("proposed", "ready") for t in TaskService(root).list_tasks() if t.id in created)

        # Rejouer : l'aperçu dit « rien à créer », et la confirmation n'ajoute aucun doublon.
        split.click()
        replay = page.locator("[data-role='decoupage']")
        replay.wait_for(timeout=APPEAR_MS)
        assert replay.locator("[data-create]").count() == 0 and "Rien à créer" in replay.inner_text()
        assert _plan_tasks(root) == created


def test_stale_preview_is_refused_with_a_409_and_creates_nothing(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Aperçu périmé")
    _validate(root, slug, "REQ-001")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.wait_for_function("() => document.querySelector('[data-act=\"split\"]').getAttribute('aria-disabled') === 'false'", timeout=APPEAR_MS)
        page.get_by_role("button", name="Valider et découper").click()
        panel = page.locator("[data-role='decoupage']")
        panel.wait_for(timeout=APPEAR_MS)
        before = _plan_tasks(root)
        # Entre l'aperçu et la confirmation, une exigence arrive dans le cadrage.
        path = root / "_grimoire" / "cadrage" / slug / "04-exigences.md"
        path.write_text(path.read_text(encoding="utf-8") + "- REQ-006 (Should) : nouvelle\n  - `pytest tests/x.py`\n", encoding="utf-8")
        with page.expect_response(lambda r: "/decoupage" in r.url and r.request.method == "POST") as seen:
            panel.get_by_role("button", name="Créer 3 tâches").click()
        assert seen.value.status == 409
        page.locator("[data-role='atelier-status']", has_text="Le plan a changé").wait_for(timeout=APPEAR_MS)
        assert _plan_tasks(root) == before


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_captures(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = "dicter-une-idee-depuis-le-cockpit"
    if not (root / "_grimoire" / "cadrage" / slug).is_dir():
        slug = _chantier(root, "Dicter une idée depuis le cockpit")
        _validate(root, slug, "REQ-001", "REQ-002", "REQ-003")
        service = TaskService(root)
        task = service.add(
            "Bouton maintenu pour dicter",
            ("la transcription apparaît en moins de 2 secondes",),
            requirement=f"{slug}/REQ-001",
            owner="ada",
            extra={"cadrage": "plan", "covers": [f"{slug}/REQ-001"]},
        ).task
        service.transition(task.id, TaskState.READY, "ada")
        service.transition(task.id, TaskState.BLOCKED, "ada", reason="attend la config du micro")
        path = root / "_grimoire" / "cadrage" / slug / "04-exigences.md"
        path.write_text(path.read_text(encoding="utf-8").replace("sans GPU", "sans GPU ni réseau"), encoding="utf-8")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}", size):
        _line(page, "REQ-001").wait_for(timeout=APPEAR_MS)
        page.get_by_role("button", name="Détail de REQ-001").click()
        page.locator("[data-role='live-bar']").scroll_into_view_if_needed()
        page.evaluate("() => document.querySelector('[data-section=\"exigences\"]')?.scrollIntoView({block: 'start'})")
        page.screenshot(path=str(folder / f"atelier-document-vivant-{size[0]}x{size[1]}.png"))
        page.get_by_role("button", name="Valider REQ-002 : copier la commande du terminal").click()
        page.screenshot(path=str(folder / f"atelier-valider-commande-{size[0]}x{size[1]}.png"))
        page.get_by_role("button", name="Valider et découper").click()
        page.locator("[data-role='decoupage']").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='decoupage']").scroll_into_view_if_needed()
        page.screenshot(path=str(folder / f"atelier-decoupage-apercu-{size[0]}x{size[1]}.png"))
