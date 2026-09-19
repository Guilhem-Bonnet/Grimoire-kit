"""L'espace Source dans un vrai navigateur — critères 5 et 6 de la spécification.

Le parcours que la spec décrit mot pour mot (§6.5) : ouvrir un fichier du kit,
l'éditer déclenche la prise d'override, le diff se lit contre le kit,
`grimoire doctor` reste vert après. Et la Console (§6.6) : une commande
`grimoire` exécutée depuis le dock, une commande hors `grimoire` refusée.

Ce module réutilise le harnais de ``tests/e2e/conftest.py`` (fixtures
``browser`` et ``served``, projet réel initialisé par ``real_project``) — pas
de projet ni de serveur à soi, pour ne pas dupliquer ce que le lot 1 a déjà
posé.

``real_project`` est partagé (portée session) avec les tests unitaires ET
avec les autres tests de ce module : un test qui prend un override change ce
que « le premier fichier de l'étage kit » désigne dans l'explorateur (l'étage
overrides, rendu en premier, cesse d'être vide). Le test qui mute est donc le
dernier du fichier, et il choisit lui-même un fichier encore vierge plutôt que
« le premier » — voir :func:`_pick_untouched_kit_file`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page


@pytest.fixture
def source(workspace: Page) -> Page:
    """La coque, déjà sur l'espace Source."""
    workspace.evaluate("() => window.GrimoireWorkspace.goto('source')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'source'")
    workspace.wait_for_selector(".tree .sr-tree-file", timeout=10_000)
    return workspace


def _pick_untouched_kit_file(page: Page) -> str:
    """Un chemin de l'étage kit qu'aucun override ne masque encore.

    Interroge l'API directement (même origine, ``fetch`` dans la page) plutôt
    que de supposer « le premier fichier affiché » — un test qui court après
    ce module a pu déjà en prendre un.
    """
    tree = page.evaluate("() => fetch('/api/workspace/files?tier=kit').then((r) => r.json())")
    files = tree["tiers"][0]["files"]
    candidate = next(f for f in files if not f["overridden"])
    return str(candidate["path"])


def _open(page: Page, path: str) -> None:
    name = path.rsplit("/", 1)[-1]
    # `dispatch_event`, pas `.click()` — même correctif que
    # `test_workspace_source_language.py::_open` (PR #617) : une dérive
    # sous-pixel de rendu Chromium sur une longue liste flex peut faire
    # atterrir le clic-coordonnées de Playwright (voire un vrai clic souris
    # émis au niveau CDP) sur la ligne suivante de l'arbre au lieu de celle
    # visée, une fois l'arbre assez long — reproduit sur
    # `_grimoire/kit/agents/stack-engineer.md` (repli `stack` de l'issue
    # onboarding-60s, sept skills de pile au lieu de deux). Sans lien avec
    # le contenu servi par `/api/workspace/*`, qui répond correctement.
    page.locator(".tree .sr-tree-file", has_text=name).first.dispatch_event("click")
    page.wait_for_function(
        "(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=path
    )


def test_l_espace_source_liste_les_trois_etages(source: Page) -> None:
    labels = source.locator(".tree .sr-tree-tier").all_inner_texts()
    assert any(label.startswith("Overrides") for label in labels)
    assert any(label.startswith("Kit") for label in labels)
    assert any(label.startswith("Projections") for label in labels)


def test_ouvrir_un_fichier_du_kit_montre_le_bandeau_lecture_seule(source: Page) -> None:
    path = _pick_untouched_kit_file(source)
    _open(source, path)

    assert source.locator(".sr-docrow .chip").inner_text().strip().endswith("lecture seule")
    assert source.locator(".sr-banner").count() == 1
    assert "Créer un override" in source.locator(".sr-banner .btn.pri").inner_text()


def test_l_inspecteur_expose_fichier_usage_et_historique(source: Page) -> None:
    path = _pick_untouched_kit_file(source)
    _open(source, path)

    tabs = source.locator("#panel-inspector .sr-insp-tabs .tab")
    assert tabs.all_inner_texts() == ["Fichier", "Utilisé par", "Historique"]

    tabs.nth(1).click()
    source.wait_for_function(
        "() => document.querySelector('#panel-inspector .sr-insp-body').textContent.includes('Chargé par')"
    )

    tabs.nth(2).click()
    source.wait_for_function(
        "() => { const t = document.querySelector('#panel-inspector .sr-insp-body').textContent;"
        " return t.includes('commit') || t.includes('dépôt'); }"
    )


def test_la_console_execute_grimoire_status_et_refuse_rm(source: Page) -> None:
    """Critère 6 de la spécification, vu du navigateur : sortie affichée pour
    une lecture autorisée, refus explicite pour tout le reste."""
    source.locator("body").press("`")
    source.wait_for_function(
        "() => document.querySelector('[data-dock-tab=\"console\"]').getAttribute('aria-selected') === 'true'"
    )

    source.keyboard.type("status")
    source.keyboard.press("Enter")
    source.wait_for_function(
        "() => document.querySelector('#dock-body').textContent.includes('code de sortie 0')",
        timeout=15_000,
    )
    assert "$ status" in source.locator("#dock-body").inner_text()

    source.keyboard.type("rm -rf /")
    source.keyboard.press("Enter")
    source.wait_for_function(
        "() => document.querySelector('#dock-body').textContent.toLowerCase().includes('refusé')"
    )
    assert "refusé" in source.locator("#dock-body").inner_text().lower()


def test_editer_un_fichier_du_kit_cree_l_override_diffuse_et_laisse_doctor_vert(
    source: Page, real_project: Path
) -> None:
    """Le parcours complet de la spec §6.5, dans l'ordre où elle le décrit.

    Dernier du fichier : c'est le seul qui mute ``real_project``, et les tests
    précédents doivent encore trouver un fichier kit vierge (voir l'en-tête du
    module)."""
    kit_path = _pick_untouched_kit_file(source)
    _open(source, kit_path)

    source.locator(".sr-banner .btn.pri").click()
    source.wait_for_function(
        "() => document.querySelector('.sr-docrow .mono')?.textContent.includes('_grimoire/overrides/')"
    )
    override_path = source.locator(".sr-docrow .mono").first.inner_text()
    assert override_path == "_grimoire/overrides/" + kit_path[len("_grimoire/kit/") :]
    assert (real_project / override_path).is_file(), "l'override doit exister réellement sur le disque"

    textarea = source.locator(".sr-textarea")
    textarea.click()
    source.keyboard.press("End")
    source.keyboard.type("\n<!-- édité depuis la vue de travail -->\n")
    source.wait_for_function(
        "() => [...document.querySelectorAll('.sr-docrow button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )
    source.locator(".sr-docrow button", has_text="Enregistrer").click()
    source.wait_for_function(
        "() => ![...document.querySelectorAll('.sr-docrow button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )
    assert "édité depuis la vue de travail" in (real_project / override_path).read_text(encoding="utf-8")

    # Diff contre le kit : l'override doit maintenant diverger.
    source.locator("#view-seg button", has_text="Diff contre le kit").click()
    source.wait_for_selector(".sr-diff .add")
    assert source.locator(".sr-diff .add").count() >= 1

    # `grimoire doctor` reste vert après l'override et l'écriture (spec §6.5).
    result = subprocess.run(
        [sys.executable, "-m", "grimoire", "doctor"],
        cwd=str(real_project),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
