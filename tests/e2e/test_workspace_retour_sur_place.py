"""Retour d'action sur place dans Concevoir et Source (suite de P62).

Depuis P62 le dock n'est visible qu'en mode atelier : Valider, Simuler,
Compiler, Enregistrer (Concevoir) et Enregistrer (Source) n'écrivaient leur
résultat que là, et un refus du serveur restait invisible pour qui n'ouvre pas
l'atelier. Chaque test ici garde l'atelier FERMÉ et lit la zone
``.action-status`` posée à côté du bouton.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page, Route

STATUS_CV = ".cv-graph > .action-status"
STATUS_SR = ".sr-editor > .action-status"


def _atelier_ferme(page: Page) -> None:
    assert page.evaluate("() => document.documentElement.dataset.atelier") == "off"
    assert not page.locator("#dock").is_visible(), "le dock doit rester masqué : le retour ne passe pas par lui"


# ── Concevoir ───────────────────────────────────────────────────────────────


@pytest.fixture
def workflow(workspace: Page, project_with_blueprint: tuple[Path, str]) -> Page:
    """Le niveau Workflow du blueprint de démonstration (lecture et calcul seulement)."""
    _root, bp_id = project_with_blueprint
    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.module === 'concevoir'")
    workspace.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
    workspace.wait_for_selector(".cv-node", timeout=15_000)
    return workspace


@pytest.fixture
def scratch_workflow(workspace: Page, real_project: Path) -> Iterator[Page]:
    """Un blueprint jetable pour les écritures : ``real_project`` est partagé par la session."""
    bp_id = f"retour-{uuid.uuid4().hex[:8]}"
    target = real_project / "_grimoire" / "blueprints" / f"{bp_id}.blueprint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "grimoire", "blueprint", "new", bp_id, "--out", str(target), "--template", "pipeline"],
        cwd=str(real_project), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60,
    )
    if not target.is_file():
        pytest.skip(f"`grimoire blueprint new` n'a pas produit de fichier ici : {result.stderr[-400:]}")
    try:
        workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
        workspace.wait_for_function("() => window.GrimoireWorkspace.module === 'concevoir'")
        workspace.locator(f'.cv-card[data-container-id="{bp_id}"]').dblclick()
        workspace.wait_for_selector(".cv-node", timeout=15_000)
        yield workspace
    finally:
        target.unlink(missing_ok=True)


def test_le_double_clic_ouvre_la_carte_visee_meme_quand_le_detail_apparait(
    workspace: Page, project_with_blueprint: tuple[Path, str], real_project: Path
) -> None:
    """Le premier clic remplissait le panneau de détail, masqué tant qu'il est vide : la
    grille rétrécissait sous le curseur et le second clic tombait sur une autre carte."""
    _root, bp_id = project_with_blueprint
    folder = real_project / "_grimoire" / "blueprints"
    extra = [folder / f"a{i}-retour-{uuid.uuid4().hex[:6]}.blueprint.json" for i in range(3)]
    source = json.loads((folder / f"{bp_id}.blueprint.json").read_text(encoding="utf-8"))
    for target in extra:
        copy_id = target.name.removesuffix(".blueprint.json")
        target.write_text(json.dumps({**source, "id": copy_id, "name": copy_id}), encoding="utf-8")
    try:
        workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
        workspace.wait_for_function("() => document.querySelectorAll('.cv-card').length >= 4")
        card = workspace.locator(f'.cv-card[data-container-id="{bp_id}"]')
        assert not workspace.locator("#panel-inspector").is_visible(), "le détail est masqué avant toute sélection"

        card.dblclick()

        workspace.wait_for_selector(".cv-node", timeout=15_000)
        assert workspace.locator('#zoom-seg button[aria-pressed="true"]').inner_text().strip() == "Workflow"
        assert workspace.locator(".cv-toolbar strong").inner_text() != ""
    finally:
        for target in extra:
            target.unlink(missing_ok=True)


def _add_node(page: Page) -> None:
    page.locator("body").press("2")
    page.wait_for_selector(".cv-prim", timeout=10_000)
    page.locator(".cv-prim").first.click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.cv-toolbar button')].some((b) => b.textContent.includes('modifié'))"
    )


def test_simuler_refuse_s_affiche_sous_la_barre_sans_ouvrir_l_atelier(workflow: Page) -> None:
    page = workflow
    page.route(
        "**/simulate*",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"ok": false, "error": "graphe invalide"}'
        ),
    )

    page.get_by_role("button", name="Simuler").click()

    status = page.locator(STATUS_CV)
    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.includes('graphe invalide')", arg=STATUS_CV)
    assert status.is_visible()
    assert status.get_attribute("data-tone") == "bad"
    assert status.get_attribute("role") == "status"
    assert "Simulation" in status.inner_text() and "Refusé" in status.inner_text()
    assert page.get_by_role("button", name="Simuler").is_enabled(), "le bouton redevient utilisable après le refus"
    _atelier_ferme(page)


def test_simuler_rend_son_verdict_sur_place(workflow: Page) -> None:
    page = workflow

    page.get_by_role("button", name="Simuler").click()

    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.startsWith('Simulation :')", arg=STATUS_CV)
    _atelier_ferme(page)


def test_le_bouton_se_desactive_avec_un_libelle_d_attente(workflow: Page) -> None:
    page = workflow
    held: list[Route] = []
    page.route("**/simulate*", lambda route: held.append(route))

    page.get_by_role("button", name="Simuler").click()

    page.wait_for_function("() => document.querySelector('.cv-toolbar [data-action=\"simulate\"]')?.getAttribute('aria-busy') === 'true'")
    busy = page.locator('.cv-toolbar [data-action="simulate"]')
    assert busy.inner_text() == "Simulation…"
    assert busy.is_disabled()
    assert page.locator('.cv-toolbar [data-action="compile"]').is_disabled(), "les actions voisines attendent aussi"
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    held[0].continue_()
    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.startsWith('Simulation :')", arg=STATUS_CV)
    assert page.locator('.cv-toolbar [data-action="simulate"]').inner_text() == "Simuler"


def test_compiler_en_lecture_seule_dit_comment_ecrire(workflow: Page) -> None:
    page = workflow
    page.route(
        "**/compile*",
        lambda route: route.fulfill(
            status=403, content_type="application/json", body='{"error": "hôte en lecture seule", "readOnly": true}'
        ),
    )

    page.get_by_role("button", name="Compiler").click()

    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.includes('lecture seule')", arg=STATUS_CV)
    text = page.locator(STATUS_CV).inner_text()
    assert text.startswith("Compilation — Refusé")
    assert "grimoire cockpit serve" in text, "le refus dit quoi faire, pas seulement non"


def test_le_retour_ne_recouvre_aucun_noeud(workflow: Page) -> None:
    page = workflow
    page.route(
        "**/simulate*",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"ok": false, "error": "' + "graphe invalide " * 30 + '"}'
        ),
    )
    page.get_by_role("button", name="Simuler").click()
    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.includes('graphe invalide')", arg=STATUS_CV)

    status_bottom = page.evaluate("(sel) => document.querySelector(sel).getBoundingClientRect().bottom", STATUS_CV)
    tops = page.evaluate("() => [...document.querySelectorAll('.cv-node')].map((n) => n.getBoundingClientRect().top)")
    assert tops and min(tops) >= status_bottom, "un message long pousse la scène, il ne passe pas sur les nœuds"


def test_valider_garde_le_brouillon_et_rend_son_verdict(scratch_workflow: Page) -> None:
    """Valider relisait le disque : les nœuds non enregistrés disparaissaient sans prévenir."""
    page = scratch_workflow
    before = page.locator(".cv-node").count()
    _add_node(page)
    assert "ajouté au brouillon" in page.locator(STATUS_CV).inner_text()

    with page.expect_response(lambda r: r.url.split("?")[0].endswith("/validate") and r.request.method == "POST"):
        page.get_by_role("button", name="Valider").click()

    page.wait_for_function("(sel) => /^Valid/.test(document.querySelector(sel)?.textContent || '')", arg=STATUS_CV)
    assert page.locator(".cv-node").count() == before + 1, "le nœud du brouillon est toujours là"
    assert page.locator(".cv-toolbar button", has_text="modifié").count() == 1
    _atelier_ferme(page)


def test_enregistrer_confirme_sur_place(scratch_workflow: Page) -> None:
    page = scratch_workflow
    _add_node(page)

    page.locator(".cv-toolbar button", has_text="Enregistrer").click()

    page.wait_for_function("(sel) => document.querySelector(sel)?.textContent.startsWith('Enregistré')", arg=STATUS_CV)
    assert page.locator(STATUS_CV).get_attribute("data-tone") in {"ok", "bad"}
    _atelier_ferme(page)


# ── Source ──────────────────────────────────────────────────────────────────


@pytest.fixture
def editable_file(real_project: Path) -> Iterator[str]:
    rel = "_grimoire/overrides/_e2e_retour_sur_place.md"
    target = real_project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# brouillon\n", encoding="utf-8")
    try:
        yield rel
    finally:
        target.unlink(missing_ok=True)


def _open_and_edit(page: Page, rel: str) -> None:
    page.evaluate("(file) => window.GrimoireWorkspace.goto('source', { file })", rel)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'source'")
    page.wait_for_selector(".sr-textarea")
    page.locator(".sr-textarea").click()
    page.keyboard.press("Control+End")
    page.keyboard.type("\nligne ajoutée\n")
    page.wait_for_function(
        "() => [...document.querySelectorAll('.sr-docrow button')].some((b) => b.textContent.includes('modifié'))"
    )


def test_source_enregistrer_refuse_s_affiche_et_garde_le_brouillon(
    workspace: Page, real_project: Path, editable_file: str
) -> None:
    kit_file = next((real_project / "_grimoire" / "kit").rglob("*.md")).relative_to(real_project).as_posix()
    _open_and_edit(workspace, editable_file)

    def _forge(route: Route) -> None:
        body = json.loads(route.request.post_data or "{}")
        body["path"] = kit_file
        route.continue_(post_data=json.dumps(body))

    workspace.route("**/api/workspace/file/write*", _forge)
    workspace.locator(".sr-docrow button", has_text="Enregistrer").click()

    workspace.wait_for_function("(sel) => document.querySelector(sel)?.textContent.includes('Refusé')", arg=STATUS_SR)
    status = workspace.locator(STATUS_SR)
    assert status.get_attribute("data-tone") == "bad"
    assert "override" in status.inner_text(), "le refus du serveur est lu en entier"
    assert workspace.locator(".sr-docrow button", has_text="modifié").is_enabled()
    assert "ligne ajoutée" in workspace.locator(".sr-textarea").input_value(), "l'éditeur n'a pas été reconstruit"
    _atelier_ferme(workspace)


def test_source_enregistrer_confirme_sur_place(workspace: Page, real_project: Path, editable_file: str) -> None:
    _open_and_edit(workspace, editable_file)

    workspace.locator(".sr-docrow button", has_text="Enregistrer").click()

    workspace.wait_for_function("(sel) => document.querySelector(sel)?.textContent.startsWith('Enregistré')", arg=STATUS_SR)
    assert workspace.locator(STATUS_SR).get_attribute("data-tone") == "ok"
    assert "ligne ajoutée" in (real_project / editable_file).read_text(encoding="utf-8")
    _atelier_ferme(workspace)
