"""Atelier de spec : prévenir avant de défaire la validation d'une exigence.

L'exigence est validée par la VRAIE garde (``requirement_gate.approve_requirement``, la MOA simulée au terminal pour *préparer*
l'état, comme le font les autres tests de l'Atelier) : l'écran n'a aucun moyen de valider. Chaque preuve est lue à l'écran, dans un
vrai navigateur, avec une attente sur l'état voulu, et sur le disque : « Annuler » ne change rien, « Modifier quand même »
enregistre et l'état passe à « Non validée », une exigence non validée (ou un changement que la validation ne lit pas) ne demande rien.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Locator, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.tools import workspace_atelier, workspace_atelier_suggestions

from .conftest import _serve_empty_project

WAIT_MS = 40_000
BRAINSTORM = "02-brainstorm.md"
EXIGENCES_FILE = "04-exigences.md"
NOTE = "Voir REQ-001 pour le détail."
EXIGENCES = (
    "# Exigences\n\n- REQ-001 [Must] : maintenir un bouton pour dicter une idée\n  - la transcription apparaît en moins de 2 secondes\n"
    "  - [ ] le texte reste local\n\n"
    "- REQ-002 [Should] : replier sur le navigateur\n  - le texte reste dicté sans le moteur du kit\n"
)
CONFIRM = "[data-role='devalidation-confirm']"
TOASTS = "[data-role='atelier-toasts']"
EDIT_FORM = "[data-section='exigences'] [data-role='block-edit']"
EDITOR = f"{EDIT_FORM} textarea"

_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _human_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


def _declare_moa(root: Path, name: str) -> None:
    from ruamel.yaml import YAML

    yaml = YAML()
    path = root / "project-context.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"))
    governance = data.get("governance") or {}
    governance["roles"] = {**(governance.get("roles") or {}), "moa": name}
    data["governance"] = governance
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-devalid", roots):
        _declare_moa(roots[0], "Ada")
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _folder(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug


def _chantier(root: Path, titre: str, *, validate: tuple[str, ...] = ("REQ-001",)) -> str:
    made = new_chantier(root, titre)
    brain = _folder(root, made.slug) / BRAINSTORM
    brain.write_text(brain.read_text(encoding="utf-8").replace("> À compléter — pistes.", NOTE), encoding="utf-8")
    (_folder(root, made.slug) / EXIGENCES_FILE).write_text(EXIGENCES, encoding="utf-8")
    for req in validate:
        rg.approve_requirement(root, f"{made.slug}/{req}", actor="ada")  # la vraie garde
    return made.slug


def _file(root: Path, slug: str) -> str:
    return (_folder(root, slug) / EXIGENCES_FILE).read_text(encoding="utf-8")


def _open(browser: Browser, url: str, slug: str, *, block_trace: bool = False) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if block_trace:  # la trace du découpage est indisponible : l'état de validation reste inconnu
        page.route(re.compile(r".*/atelier/chantiers/[^/]+/trace.*"), lambda route: route.abort())
    page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
    page.locator("[data-section='exigences'] .at-blk[data-req]").first.wait_for(timeout=WAIT_MS)
    try:
        yield page
        assert not errors, errors
    finally:
        context.close()


def _block(page: Page, req: str) -> Locator:
    return page.locator(f".at-blk[data-req='{req}']")


def _edit(page: Page, req: str, text: str) -> Locator:
    """Ouvre l'éditeur du bloc de l'exigence, remplace son texte, demande l'enregistrement ; rend la zone de texte."""
    _block(page, req).click()
    area = page.locator(EDITOR)
    expect(area).to_be_focused(timeout=WAIT_MS)
    area.fill(text)
    page.get_by_role("button", name="Enregistrer").click()
    return area


def _validated(page: Page) -> None:
    """Ce que la page sait : REQ-001 est validée (la carte d'une référence le dit, sans retenir de réponse instantanée)."""
    page.locator("a.at-ref-req").first.hover()
    expect(page.locator("[data-role='ref-card-state']")).to_contain_text("Validée par", timeout=WAIT_MS)
    page.mouse.move(5, 5)


def test_cancel_changes_nothing_then_modify_anyway_unvalidates(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dévalidation Annuler")
    before = _file(root, slug)
    new = before.split("\n\n- REQ-002")[0].split("# Exigences\n\n", 1)[1].replace("dicter une idée", "dicter une idée à voix haute")
    for page in _open(browser, url, slug):
        _validated(page)
        _edit(page, "REQ-001", new)
        dialog = page.locator(CONFIRM)
        expect(dialog).to_be_visible(timeout=WAIT_MS)
        expect(dialog).to_contain_text("REQ-001 est validée par Ada")
        expect(dialog).to_contain_text("La modifier annule cette validation")
        expect(dialog.locator("[data-role='devalidation-command']")).to_have_text(f"grimoire cadrage approve REQ-001 --chantier {slug}")
        expect(dialog.get_by_role("button", name="Annuler")).to_be_focused()  # le geste prudent par défaut
        # Annuler : rien n'est écrit, la saisie reste dans l'éditeur.
        dialog.get_by_role("button", name="Annuler").click()
        expect(dialog).to_have_count(0, timeout=WAIT_MS)
        assert _file(root, slug) == before
        expect(page.locator(EDITOR)).to_have_value(new)
        expect(page.get_by_role("button", name="Enregistrer")).to_be_enabled()
        # Modifier quand même : le fichier est écrit, l'état passe à « Non validée », la commande est rappelée.
        page.get_by_role("button", name="Enregistrer").click()
        expect(dialog).to_be_visible(timeout=WAIT_MS)
        dialog.get_by_role("button", name="Modifier quand même").click()
        expect(page.locator(EDIT_FORM)).to_have_count(0, timeout=WAIT_MS)
        expect(page.locator(".at-blk[data-req='REQ-001']")).to_contain_text("à voix haute")
        assert "à voix haute" in _file(root, slug)
        toast = page.locator(TOASTS)
        expect(toast).to_contain_text("REQ-001 n’est plus validée", timeout=WAIT_MS)
        expect(toast).to_contain_text(f"grimoire cadrage approve REQ-001 --chantier {slug}")
        expect(page.locator("[data-live='REQ-001']")).to_contain_text("modifiée depuis la validation", timeout=WAIT_MS)
        page.locator("a.at-ref-req").first.hover()
        expect(page.locator("[data-role='ref-card-state']")).to_have_text("Non validée", timeout=WAIT_MS)


def test_an_unvalidated_requirement_asks_nothing(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dévalidation Libre")  # REQ-002 n'est pas validée
    for page in _open(browser, url, slug):
        _validated(page)
        _edit(page, "REQ-002", "- REQ-002 [Should] : replier sur le navigateur, sans le moteur\n  - le texte reste dicté sans le moteur du kit")
        expect(page.locator(EDIT_FORM)).to_have_count(0, timeout=WAIT_MS)
        assert "replier sur le navigateur, sans le moteur" in _file(root, slug)
        expect(page.locator(CONFIRM)).to_have_count(0)
        expect(page.locator(TOASTS)).not_to_contain_text("n’est plus validée")


def test_a_change_the_validation_does_not_read_asks_nothing(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dévalidation Espace")
    for page in _open(browser, url, slug):
        _validated(page)
        head = "- REQ-001 [Must] : maintenir un bouton pour dicter une idée"
        crit = "  - la transcription apparaît en moins de 2 secondes\n  - [x] le texte reste local"
        _edit(page, "REQ-001", f"{head}   \n{crit}\n")  # espace de fin, case cochée : la validation ne lit pas cela
        expect(page.locator(EDIT_FORM)).to_have_count(0, timeout=WAIT_MS)
        expect(page.locator(CONFIRM)).to_have_count(0)
        assert "[x] le texte reste local" in _file(root, slug)
        expect(page.locator(TOASTS)).not_to_contain_text("n’est plus validée")
        page.locator("a.at-ref-req").first.hover()
        expect(page.locator("[data-role='ref-card-state']")).to_contain_text("Validée par", timeout=WAIT_MS)


def test_accepting_a_suggestion_that_rewrites_a_validated_requirement_asks_first(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dévalidation Suggestion")
    block = next(
        b for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "exigences" for b in p["blocks"] if b.get("req") == "REQ-001"
    )
    reworded = "- REQ-001 [Must] : maintenir un bouton pour dicter une idée, même hors ligne\n  - la transcription apparaît en moins de 2 secondes\n  - [ ] le texte reste local"
    workspace_atelier_suggestions.suggest(str(root), slug, str(block["id"]), reworded, "préciser le hors ligne")
    before = _file(root, slug)
    for page in _open(browser, url, slug):
        _validated(page)
        card = page.locator(".at-sugg")
        expect(card).to_have_count(1, timeout=WAIT_MS)
        card.get_by_role("button", name=re.compile("Accepter")).click()
        dialog = page.locator(CONFIRM)
        expect(dialog).to_be_visible(timeout=WAIT_MS)
        expect(dialog.get_by_role("button", name="Annuler")).to_be_focused()
        dialog.get_by_role("button", name="Annuler").click()
        expect(dialog).to_have_count(0, timeout=WAIT_MS)
        assert _file(root, slug) == before
        expect(page.locator(".at-sugg")).to_have_count(1)  # la suggestion reste en attente
        card.get_by_role("button", name=re.compile("Accepter")).click()
        expect(dialog).to_be_visible(timeout=WAIT_MS)
        dialog.get_by_role("button", name="Modifier quand même").click()
        expect(page.locator(".at-sugg")).to_have_count(0, timeout=WAIT_MS)
        assert "même hors ligne" in _file(root, slug)
        expect(page.locator(TOASTS)).to_contain_text("REQ-001 n’est plus validée", timeout=WAIT_MS)


def test_unknown_state_does_not_block_and_says_so(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Dévalidation Inconnue")
    for page in _open(browser, url, slug, block_trace=True):
        _edit(page, "REQ-001", "- REQ-001 [Must] : maintenir un bouton pour dicter\n  - la transcription apparaît en moins de 2 secondes\n  - [ ] le texte reste local")
        expect(page.locator(EDIT_FORM)).to_have_count(0, timeout=WAIT_MS)
        assert "dicter une idée" not in _file(root, slug)
        expect(page.locator(CONFIRM)).to_have_count(0)
        expect(page.locator(TOASTS)).to_contain_text("État de validation inconnu pour REQ-001", timeout=WAIT_MS)
