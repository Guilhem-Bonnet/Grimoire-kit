"""Source — « Enregistrer » refusé par le serveur (#536).

Deux refus réels de ``POST /api/workspace/file/write`` que l'interface doit
rendre lisibles au lieu de les avaler :

- l'étage kit n'est pas éditable : le 403 nomme l'override à créer ;
- un chemin hors de la racine du projet (``WorkspacePathError``).

Le bouton « Enregistrer » n'envoie jamais ces chemins de lui-même (il est
absent sur un fichier non éditable). On ouvre donc un fichier éditable, puis on
réécrit le corps de la requête en vol (``route.continue_``) : le refus vient du
vrai serveur, pas d'une réponse fabriquée. Ce que l'on vérifie ici est ce que
la page en fait : le message dans la console du dock, et le brouillon qui reste
« modifié » (rien n'est perdu).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page, Route

from .conftest import open_atelier


@pytest.fixture
def editable_file(real_project: Path) -> Iterator[str]:
    """Un fichier de l'étage overrides, retiré en sortie : ``real_project`` est partagé."""
    rel = "_grimoire/overrides/_e2e_save_refused.md"
    target = real_project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# brouillon\n", encoding="utf-8")
    try:
        yield rel
    finally:
        target.unlink(missing_ok=True)


def _open_editable(page: Page, rel: str) -> None:
    page.evaluate("(file) => window.GrimoireWorkspace.goto('source', { file })", rel)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'source'")
    page.wait_for_selector(".sr-textarea")


def _edit(page: Page) -> None:
    page.locator(".sr-textarea").click()
    page.keyboard.press("Control+End")
    page.keyboard.type("\nligne en trop\n")
    page.wait_for_function(
        "() => [...document.querySelectorAll('.sr-docrow button')].some((b) => b.textContent.includes('modifié'))"
    )


def _rewrite_path(page: Page, forged: str) -> list[int]:
    """Fait partir la requête d'écriture vers ``forged`` ; rend les statuts reçus."""
    statuses: list[int] = []

    def _route(route: Route) -> None:
        body = json.loads(route.request.post_data or "{}")
        body["path"] = forged
        route.continue_(post_data=json.dumps(body))

    page.route("**/api/workspace/file/write*", _route)
    page.on(
        "response",
        lambda resp: statuses.append(resp.status) if "/api/workspace/file/write" in resp.url else None,
    )
    return statuses


def _open_console(page: Page) -> None:
    """Rouvre l'atelier (le dock est replié par défaut, P62) puis choisit l'onglet Console."""
    open_atelier(page)
    page.locator('[data-dock-tab="console"]').click()


def _console_text(page: Page) -> str:
    _open_console(page)
    return page.locator("#dock-body").inner_text()


def test_enregistrer_vers_l_etage_kit_est_refuse_et_nomme_l_override(
    workspace: Page, real_project: Path, editable_file: str
) -> None:
    kit_file = next((real_project / "_grimoire" / "kit").rglob("*.md")).relative_to(real_project).as_posix()
    _open_editable(workspace, editable_file)
    _edit(workspace)
    statuses = _rewrite_path(workspace, kit_file)

    workspace.locator(".sr-docrow button", has_text="Enregistrer").click()

    _open_console(workspace)
    workspace.wait_for_function("() => document.getElementById('dock-body')?.textContent.includes('refusé')")
    assert statuses == [403]
    text = _console_text(workspace)
    assert "refusé" in text
    assert "override" in text, "le refus nomme l'override à créer"
    assert workspace.locator(".sr-docrow button", has_text="modifié").count() == 1, "le brouillon n'est pas perdu"


@pytest.mark.parametrize("forged", ["../evade-e2e.md", "/tmp/evade-e2e.md"])
def test_enregistrer_hors_de_la_racine_est_refuse(
    workspace: Page, real_project: Path, editable_file: str, forged: str
) -> None:
    _open_editable(workspace, editable_file)
    _edit(workspace)
    statuses = _rewrite_path(workspace, forged)

    workspace.locator(".sr-docrow button", has_text="Enregistrer").click()

    _open_console(workspace)
    workspace.wait_for_function("() => document.getElementById('dock-body')?.textContent.includes('refusé')")
    assert statuses == [403]
    assert "chemin" in _console_text(workspace)
    assert not (real_project.parent / "evade-e2e.md").exists()
    assert not Path("/tmp/evade-e2e.md").exists()
    assert workspace.locator(".sr-docrow button", has_text="modifié").count() == 1
