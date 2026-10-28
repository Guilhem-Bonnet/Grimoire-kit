"""P06 — la Console de la coque, les entrées mortes, et aucun état qui fuit.

Ce que la tâche GAO-p06-coque-c-001 exige, vu du navigateur :

- la saisie de la Console passe par un vrai ``<input>`` libellé : deux Tab
  depuis l'onglet Console sortent du dock sans rien écrire (la Console
  simulée de Source capturait Tab pour sa complétion), Échap rend le focus,
  ↑ rappelle la commande précédente, les raccourcis d'une lettre se taisent
  pendant la saisie ;
- passer de Fichiers (Source) à Travail remet à zéro la pastille de la barre
  de document et la commande équivalente, et une réponse arrivée après le
  changement d'espace ne pose plus rien sur l'espace suivant ;
- le bouton de rail Bibliothèque n'est visible que là où il agit, désactivé
  avec sa raison quand l'espace l'enregistre sans pouvoir s'en servir ;
- le bandeau « Créer un override » n'apparaît que sur un fichier du kit dont
  le serveur a calculé l'override, et aucun chemin n'est inventé.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les captures
1440 × 900 (clair et sombre) de l'atelier ouvert, Console comprise.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import Locator, Page, Route

from .conftest import open_atelier

_ACTIVE_IN_DOCK = "() => !!document.activeElement && document.getElementById('dock').contains(document.activeElement)"
_INPUT_FOCUSED = "() => document.activeElement?.id === 'dock-console-input'"


def _console_tab(page: Page) -> None:
    open_atelier(page)
    page.locator('[data-dock-tab="console"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-dock-tab=\"console\"]').getAttribute('aria-selected') === 'true'"
    )


def _run_in_console(page: Page, command: str) -> None:
    page.locator("#dock-console-input").fill(command)
    page.locator("#dock-console-input").press("Enter")
    page.wait_for_function(
        "(c) => { const t = document.getElementById('dock-body').textContent;"
        " return t.includes('$ grimoire ' + c) && /code de sortie \\d+|refusé/.test(t.split('$ grimoire ' + c).pop()); }",
        arg=command,
        timeout=20_000,
    )


# ── Un vrai champ de saisie ─────────────────────────────────────────────────


def test_la_console_saisit_dans_un_input_libelle(workspace: Page) -> None:
    open_atelier(workspace)
    workspace.locator("body").press("`")
    workspace.wait_for_function(_INPUT_FOCUSED)
    # Avec ses suggestions (`list`), le champ est un combobox : on le trouve par son libellé.
    field = workspace.get_by_label(re.compile("sous-commande à lancer"))
    assert field.count() == 1
    assert field.evaluate("(el) => el.tagName") == "INPUT"
    # La touche qui ouvre la Console ne s'écrit pas dans le champ.
    assert field.input_value() == ""


def test_deux_tab_depuis_l_onglet_console_sortent_du_dock_sans_rien_ecrire(workspace: Page) -> None:
    _console_tab(workspace)
    before = workspace.locator("#dock-body").inner_text()
    workspace.locator('[data-dock-tab="console"]').focus()

    workspace.keyboard.press("Tab")
    workspace.keyboard.press("Tab")

    assert not workspace.evaluate(_ACTIVE_IN_DOCK), "le focus est resté dans le dock"
    assert workspace.locator("#dock-body").inner_text() == before
    assert workspace.locator("#dock-console-input").input_value() == ""


def test_tab_depuis_le_champ_ne_complete_pas_et_sort_du_dock(workspace: Page) -> None:
    _console_tab(workspace)
    before = workspace.locator("#dock-body").inner_text()
    workspace.locator("#dock-console-input").focus()
    workspace.keyboard.type("zzz")  # rien à proposer : Tab ne complète pas (P77)

    workspace.keyboard.press("Tab")
    workspace.keyboard.press("Tab")

    assert not workspace.evaluate(_ACTIVE_IN_DOCK)
    assert workspace.locator("#dock-console-input").input_value() == "zzz"
    assert workspace.locator("#dock-body").inner_text() == before


def test_echap_vide_le_champ_puis_rend_le_focus(workspace: Page) -> None:
    open_atelier(workspace)
    origin = workspace.locator("#palette-open")
    origin.focus()
    workspace.keyboard.press("`")
    workspace.wait_for_function(_INPUT_FOCUSED)
    workspace.keyboard.type("task li")

    workspace.keyboard.press("Escape")  # ferme d'abord la liste de propositions (P77)
    assert workspace.locator("#dock-console-input").input_value() == "task li"
    workspace.keyboard.press("Escape")
    assert workspace.locator("#dock-console-input").input_value() == ""
    assert workspace.evaluate(_INPUT_FOCUSED)

    workspace.keyboard.press("Escape")
    workspace.wait_for_function("() => document.activeElement?.id === 'palette-open'")


def test_les_raccourcis_d_une_lettre_se_taisent_pendant_la_saisie(workspace: Page) -> None:
    _console_tab(workspace)
    workspace.locator("#dock-console-input").focus()
    workspace.keyboard.type("2?jk1")
    assert workspace.locator("#dock-console-input").input_value() == "2?jk1"
    assert not workspace.evaluate("() => document.getElementById('shortcuts').open")
    assert workspace.evaluate("() => document.getElementById('dock').dataset.state") == "pinned"


def test_une_commande_s_execute_et_fleche_haut_la_rappelle(workspace: Page) -> None:
    _console_tab(workspace)
    _run_in_console(workspace, "version")
    body = workspace.locator("#dock-body").inner_text()
    assert "code de sortie 0" in body
    assert workspace.locator("#dock-echo").inner_text().endswith("grimoire version")

    field = workspace.locator("#dock-console-input")
    assert field.input_value() == ""
    field.press("ArrowUp")
    assert field.input_value() == "version"
    field.press("ArrowDown")
    assert field.input_value() == ""


def test_une_commande_hors_liste_blanche_est_refusee(workspace: Page) -> None:
    _console_tab(workspace)
    _run_in_console(workspace, "blueprint list")
    assert "refusé" in workspace.locator("#dock-body").inner_text().split("$ grimoire blueprint list").pop()


# ── Aucun état qui fuit d'un espace à l'autre ───────────────────────────────


def _kit_file(page: Page) -> str:
    tree = page.evaluate("() => fetch('/api/workspace/files?tier=kit').then((r) => r.json())")
    files = [f for f in tree["tiers"][0]["files"] if f["path"].startswith("_grimoire/kit/")]
    return str(files[0]["path"])


def _open_source(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('source')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'source'")
    page.wait_for_selector("#canvas .tree .sr-tree-file", timeout=10_000)


def _goto_travail(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('travail')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_function("() => document.getElementById('canvas').getAttribute('aria-busy') === 'false'")


def test_de_source_a_travail_la_pastille_et_la_commande_equivalente_repartent_a_zero(workspace: Page) -> None:
    open_atelier(workspace)
    _open_source(workspace)
    path = _kit_file(workspace)
    workspace.evaluate("(p) => window.GrimoireWorkspace.goto('source', { file: p })", path)
    workspace.wait_for_function("(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=path)
    assert workspace.locator("#doc-validation").is_visible()
    assert "lecture seule" in workspace.locator("#doc-validation").inner_text()
    workspace.locator('[data-dock-tab="console"]').click()
    _run_in_console(workspace, "version")
    assert "grimoire version" in workspace.locator("#dock-echo").inner_text()

    _goto_travail(workspace)

    assert workspace.locator("#doc-validation").is_hidden()
    echo = workspace.locator("#dock-echo").inner_text()
    assert "grimoire version" not in echo
    # Travail pose sa propre commande, jamais celle de l'espace quitté.
    assert echo == "" or "grimoire task" in echo


def test_une_reponse_arrivee_apres_le_changement_d_espace_est_ignoree(workspace: Page) -> None:
    open_atelier(workspace)
    _open_source(workspace)
    path = _kit_file(workspace)
    held: list[Route] = []
    workspace.route(lambda url: "/api/workspace/file?" in url, lambda route: held.append(route))
    workspace.locator("#canvas .tree .sr-tree-file", has_text=path.rsplit("/", 1)[-1]).first.dispatch_event("click")
    for _ in range(50):
        if held:
            break
        workspace.evaluate("() => new Promise(requestAnimationFrame)")
    assert held, "la lecture du fichier n'est jamais partie"

    _goto_travail(workspace)
    hash_before = workspace.evaluate("() => location.hash")
    with workspace.expect_response(lambda r: "/api/workspace/file?" in r.url) as late:
        held[0].continue_()
    late.value.finished()
    # Deux images : la réponse est lue et son `then` a tourné.
    workspace.evaluate("() => new Promise(requestAnimationFrame)")
    workspace.evaluate("() => new Promise(requestAnimationFrame)")

    assert workspace.locator("#doc-validation").is_hidden(), "la pastille de Source a fui sur Travail"
    assert workspace.locator("#panel-inspector .sr-insp-tabs").count() == 0, "l'inspecteur de Source a fui"
    assert workspace.evaluate("() => location.hash") == hash_before
    assert "file=" not in hash_before


# ── Le rail Bibliothèque ────────────────────────────────────────────────────


def _library(page: Page) -> Locator:
    return page.locator('.rail-btn[data-panel="library"]')


def test_le_rail_bibliotheque_n_est_visible_que_la_ou_il_agit(
    workspace: Page, project_with_blueprint: tuple[Path, str]
) -> None:
    open_atelier(workspace)
    # P78 : À traiter n'a pas d'arborescence, donc pas de bouton Explorateur.
    assert workspace.locator('.rail-btn[data-panel="explorer"]').is_hidden()
    assert _library(workspace).is_hidden(), "Bibliothèque promise sur À traiter, où elle ne fait rien"

    workspace.evaluate("() => window.GrimoireWorkspace.goto('concevoir')")
    workspace.wait_for_selector(".cv-card", timeout=15_000)
    library = _library(workspace)
    assert library.is_visible()
    assert library.get_attribute("aria-disabled") == "true"
    assert "blueprint" in (library.get_attribute("title") or "")
    assert "blueprint" in (library.get_attribute("aria-description") or "")
    workspace.locator("body").press("2")
    assert workspace.locator(".cv-palette[data-open='1']").count() == 0

    workspace.locator(".cv-card").first.dblclick()
    workspace.wait_for_selector(".cv-node", timeout=15_000)
    workspace.wait_for_function(
        "() => !document.querySelector('.rail-btn[data-panel=\"library\"]').hasAttribute('aria-disabled')"
    )
    library.click()
    workspace.wait_for_selector(".cv-palette[data-open='1']")

    _goto_travail(workspace)
    assert _library(workspace).is_hidden()


# ── Le bandeau d'override ───────────────────────────────────────────────────


def _fake_file(page: Page, entry: dict[str, object]) -> None:
    body = {
        "size": 12,
        "text": "# généré\n",
        "binary": False,
        "truncated": False,
        "shipped_by_kit": False,
        "kit_version": None,
        "digest": "0" * 8,
        **entry,
    }
    page.route(
        lambda url: "/api/workspace/file?" in url,
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(body)),
    )


def _show(page: Page, path: str) -> None:
    page.evaluate("(p) => window.GrimoireWorkspace.goto('source', { file: p })", path)
    page.wait_for_function("(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=path)


def test_pas_de_bandeau_d_override_sur_un_pack_de_preuve(workspace: Page) -> None:
    path = "_grimoire-output/evidence/GAO-x-001/evidence-pack.md"
    _fake_file(workspace, {"path": path, "tier": "evidence", "editable": False})
    _show(workspace, path)
    banner = workspace.locator(".sr-banner")
    assert banner.get_attribute("data-kind") == "evidence"
    assert banner.locator("button").count() == 0
    assert "Créer un override" not in workspace.locator("#canvas").inner_text()


def test_pas_de_bandeau_d_override_sur_une_projection(workspace: Page) -> None:
    path = ".claude/agents/concierge.md"
    _fake_file(workspace, {"path": path, "tier": "projections", "editable": False})
    _show(workspace, path)
    assert workspace.locator(".sr-banner").get_attribute("data-kind") == "projection"
    assert workspace.locator(".sr-banner button").count() == 0


def test_aucun_chemin_d_override_invente(workspace: Page) -> None:
    path = "_grimoire/standard/policies.yaml"
    _fake_file(workspace, {"path": path, "tier": "kit", "editable": False, "override_path": None, "overridden": False})
    _show(workspace, path)
    assert workspace.locator(".sr-banner").get_attribute("data-kind") == "generated"
    assert workspace.locator(".sr-banner button").count() == 0
    assert "à calculer" not in workspace.locator("body").inner_text()


def test_le_bandeau_d_override_reste_sur_un_fichier_du_kit(workspace: Page) -> None:
    _open_source(workspace)
    path = _kit_file(workspace)
    _show(workspace, path)
    banner = workspace.locator(".sr-banner")
    assert banner.get_attribute("data-kind") == "override"
    assert banner.locator("button.pri").inner_text() in {"Créer un override", "Ouvrir l’override"}
    assert "à calculer" not in banner.inner_text()


# ── Captures et débordement ─────────────────────────────────────────────────


def _no_overflow(page: Page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#dock-console, #dock-console *, #dock-tabs, #rail .rail-btn')]
            .filter((n) => n.offsetParent !== null && n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflowX === 'visible')
            .map((n) => n.id || n.className || n.tagName)"""
    )


def test_captures_et_aucun_debordement_a_1024(workspace: Page) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    for scheme in ("dark", "light"):
        workspace.emulate_media(color_scheme=scheme)
        _console_tab(workspace)
        _run_in_console(workspace, "version")
        workspace.locator("#dock-console-input").fill("task list")
        workspace.set_viewport_size({"width": 1440, "height": 900})
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            workspace.screenshot(path=str(folder / f"p06-console-{scheme}-1440.png"))
        workspace.set_viewport_size({"width": 1024, "height": 768})
        if target:
            workspace.screenshot(path=str(Path(target) / f"p06-console-{scheme}-1024.png"))
        assert _no_overflow(workspace) == [], scheme


# ── IntelliSense des commandes (P77) ────────────────────────────────────────

_OPTIONS = "#dock-console-listbox [role=option]"


def _names(page: Page) -> list[str]:
    return page.locator(_OPTIONS + " .opt-name").all_inner_texts()


def test_p77_tab_complete_task_puis_propose_show_avec_son_aide(workspace: Page) -> None:
    _console_tab(workspace)
    field = workspace.locator("#dock-console-input")
    field.focus()
    workspace.keyboard.type("grimoire ta")
    assert _names(workspace) == ["task"]
    workspace.keyboard.press("Tab")
    assert field.input_value() == "grimoire task "
    workspace.keyboard.type("sh")
    assert _names(workspace) == ["show"]
    assert workspace.locator(_OPTIONS + " .opt-help").inner_text() == "Détail d'une tâche et sa prochaine porte."


def test_p77_fleches_echap_et_entree_sans_liste(workspace: Page) -> None:
    _console_tab(workspace)
    field = workspace.locator("#dock-console-input")
    field.focus()
    workspace.keyboard.type("task ")
    names = _names(workspace)
    assert len(names) >= 3
    active = "() => document.getElementById(document.activeElement.getAttribute('aria-activedescendant')).textContent"
    first = workspace.evaluate(active)
    workspace.keyboard.press("ArrowDown")
    second = workspace.evaluate(active)
    assert second != first and second.startswith(names[1])
    workspace.keyboard.press("ArrowUp")
    assert workspace.evaluate(active) == first

    workspace.keyboard.press("Escape")
    assert workspace.locator(_OPTIONS).count() == 0
    assert field.input_value() == "task "

    field.fill("version")
    assert workspace.locator(_OPTIONS).count() == 0
    field.press("Enter")
    workspace.wait_for_function(
        "() => document.getElementById('dock-body').textContent.includes('$ grimoire version')"
    )


def test_p77_les_propositions_viennent_du_catalogue_du_serveur(workspace: Page) -> None:
    from grimoire.tools import workspace_exec

    _console_tab(workspace)
    field = workspace.locator("#dock-console-input")
    field.focus()
    firsts = {key.split(" ")[0] for key in workspace_exec.ALLOWED}
    seen: set[str] = set()
    for letter in "abcdefghijklmnopqrstuvwxyz":
        field.fill(letter)
        seen.update(_names(workspace))
    assert seen == firsts
    field.fill("task s")
    assert set(_names(workspace)) <= {k.split(" ")[1] for k in workspace_exec.ALLOWED if k.startswith("task ")}
    field.fill("blueprint ")
    assert workspace.locator(_OPTIONS).count() == 0


def test_p77_aria_listbox_et_activedescendant(workspace: Page) -> None:
    _console_tab(workspace)
    field = workspace.locator("#dock-console-input")
    field.focus()
    assert field.get_attribute("aria-autocomplete") == "list"
    assert field.get_attribute("aria-expanded") == "false"
    workspace.keyboard.type("ta")
    assert field.get_attribute("aria-expanded") == "true"
    assert workspace.locator("#dock-console-listbox").get_attribute("role") == "listbox"
    assert field.get_attribute("aria-controls") == "dock-console-listbox"
    descendant = field.get_attribute("aria-activedescendant")
    assert descendant and workspace.locator(f"#{descendant}").get_attribute("role") == "option"
