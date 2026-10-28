"""Fin du point polysémique (P30), prouvée dans un navigateur.

Un point ne porte plus qu'une présence ou une quantité. Sur une carte de
tâche, chaque sens prend sa forme : le type son icône, la priorité ses barres,
le risque son bouclier, la session son glyphe de vivacité, l'hôte son
pictogramme générique. Dans Source, le statut d'un fichier se lit en lettre
façon git (A, M, D, U, O) au lieu d'une pastille sans mot. Les états vides
portent leur composition du sprite.

Les cartes et l'arbre de Source sont servis par interception (``page.route``) :
la réponse réelle du serveur est relue puis complétée des cas à montrer, pour
que chaque forme apparaisse sans dépendre de l'état du projet partagé.

Captures (preuve d'interface) : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les
écrit ; absent, le test ne capture rien mais prouve tout le reste.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page, Route

_TASKS = re.compile(r".*/api/workspace/tasks(\?.*)?$")
_FILES = re.compile(r".*/api/workspace/files(\?.*)?$")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _hrefs(page: Page, selector: str) -> list[str]:
    return page.eval_on_selector_all(selector, "(nodes) => nodes.map((n) => n.getAttribute('href'))")


# ── Cartes de tâche ──────────────────────────────────────────────────────────


def _card(task_id: str, title: str, **fields: object) -> dict[str, object]:
    card: dict[str, object] = {
        "id": task_id,
        "title": title,
        "board": "in_progress",
        "status": "running",
        "owner": "winston",
        "expected_evidence": ["evidence_pack"],
        "cost": {"status": "unknown", "calls": 0},
    }
    card.update(fields)
    return card


def _board() -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "columns": ["proposed", "ready", "in_progress", "blocked", "review", "accepted", "released", "archived"],
        "states": [],
        "priorities": ["low", "medium", "high", "critical"],
        "ledger": True,
        "count": 2,
        "tasks": [
            _card(
                "GAO-p30-carte-001",
                "Migrer le registre des sessions",
                type="migration",
                risk_profile="security_critical",
                effective_priority="critical",
                session_id="sess-p30-vivante-0001",
                host="host-claude-code-cli",
                session={
                    "id": "sess-p30-vivante-0001",
                    "liveness": {"state": "vivant"},
                    "updated_at": now,
                    "duration_s": 120,
                },
            ),
            _card(
                "GAO-p30-carte-002",
                "Nettoyer les anciens rapports",
                type="cleanup",
                risk_profile="light",
                effective_priority="low",
                session_id="sess-p30-inconnue-0002",
                host="host-github-copilot",
                session={"id": "sess-p30-inconnue-0002", "liveness": {"state": "inconnu"}},
            ),
        ],
    }


def test_la_carte_porte_une_forme_par_sens(workspace: Page) -> None:
    def fulfil(route: Route) -> None:
        if route.request.method != "GET":
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_board()))

    workspace.route(_TASKS, fulfil)

    def fulfil_detail(route: Route) -> None:
        # Le panneau de détail relit la tâche ouverte (P64) : même carte, aucune porte déclarée.
        if route.request.method != "GET":
            route.fallback()
            return
        task_id = route.request.url.split("/tasks/")[1].split("?")[0]
        card = next(t for t in _board()["tasks"] if t["id"] == task_id)  # type: ignore[attr-defined]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({**card, "next_moves_require": {}}))

    workspace.route(re.compile(r".*/api/workspace/tasks/GAO-p30-carte-00\d(\?.*)?$"), fulfil_detail)
    _goto(workspace, "executer")
    workspace.wait_for_selector(".ex-card[data-task='GAO-p30-carte-001']", timeout=10_000)
    first = ".ex-card[data-task='GAO-p30-carte-001']"
    second = ".ex-card[data-task='GAO-p30-carte-002']"

    # Type : l'icône seule, le libellé français pour les lecteurs d'écran et au survol.
    assert _hrefs(workspace, f"{first} .ex-card-meta use")[0] == "icons.svg#i-task-migration"
    assert workspace.locator(f"{first} .ex-card-meta [aria-label='type : Migration']").count() == 1
    assert "migration" not in workspace.locator(f"{first} .ex-card-meta").inner_text()
    assert "icons.svg#i-task-cleanup" in _hrefs(workspace, f"{second} .ex-card-meta use")

    # Risque : octogone pour security_critical, bouclier nu pour light ; le mot reste.
    assert "icons.svg#i-risk-critical" in _hrefs(workspace, f"{first} .ex-card-meta use")
    assert "icons.svg#i-risk-light" in _hrefs(workspace, f"{second} .ex-card-meta use")
    assert "security_critical" in workspace.locator(f"{first} .ex-card-meta").inner_text()

    # Priorité : trois barres teintées pour critique, une seule pour basse.
    critical = workspace.locator(f"{first} .ex-card-meta .bars")
    assert critical.get_attribute("data-level") == "3"
    assert "bad" in (critical.get_attribute("class") or "")
    assert critical.get_attribute("aria-label") == "priorité critique"
    assert workspace.locator(f"{first} .ex-card-meta .bars i.on").count() == 3
    assert workspace.locator(f"{second} .ex-card-meta .bars i.on").count() == 1

    # Plus aucun glyphe d'état dans la ligne méta : ni risque ni priorité en pastille.
    assert workspace.locator(f"{first} .ex-card-meta .sico").count() == 0

    # Session : glyphe de vivacité (une forme par valeur) et pictogramme d'hôte générique.
    assert "icons.svg#i-session-live" in _hrefs(workspace, f"{first} [data-role='live-facts'] use")
    assert "icons.svg#i-session-unknown" in _hrefs(workspace, f"{second} [data-role='live-facts'] use")
    assert "icons.svg#i-host-cli" in _hrefs(workspace, f"{first} .ex-session use")
    assert "icons.svg#i-host-ide" in _hrefs(workspace, f"{second} .ex-session use")
    assert "Claude Code" in workspace.locator(f"{first} .ex-session").inner_text()

    # La preuve attendue prend le sceau, plus une pastille « info » sans sens. Depuis P64 la carte
    # dit ses preuves par l'anneau (conditions du passage) ; les preuves attendues, avec leur
    # sceau, sont dans le panneau de détail de la carte ouverte.
    workspace.locator(first).click()
    workspace.wait_for_selector("#inspector-body [data-role='gate-card']", state="attached")
    assert "icons.svg#seal" in _hrefs(workspace, "#inspector-body .ex-card-chips use")
    _capture(workspace, "p30-cartes")


def test_le_monogramme_tire_deux_lettres_du_nom(workspace: Page) -> None:
    result = workspace.evaluate(
        """async () => {
            const shell = await import('./shell.js');
            const node = shell.monogram('Grimoire-Forge');
            return {
                forge: shell.monogramText('Grimoire-Forge'),
                house: shell.monogramText('Terraform-HouseServer'),
                single: shell.monogramText('kit'),
                none: shell.monogramText(''),
                cls: node.className,
                hidden: node.getAttribute('aria-hidden'),
            };
        }"""
    )
    assert result == {"forge": "GF", "house": "TH", "single": "KI", "none": "?", "cls": "mgram", "hidden": "true"}


# ── Source : lettres façon git ───────────────────────────────────────────────

#: Un override par cas, plus un fichier du kit masqué ; le reste de l'arbre
#: est la réponse réelle du serveur.
_OVERRIDES = [
    ("U", {"masks_kit": True, "diverges": True, "shipped_by_kit": True, "kit_version": "3.0.0"}),
    ("M", {"masks_kit": True, "diverges": True, "shipped_by_kit": False}),
    ("O", {"masks_kit": True, "diverges": False, "shipped_by_kit": True}),
    ("D", {"masks_kit": False, "diverges": False, "shipped_by_kit": True}),
    ("A", {"masks_kit": False, "diverges": False, "shipped_by_kit": False}),
]


def _tree_with_cases(real: dict[str, object]) -> dict[str, object]:
    tiers = real["tiers"]
    assert isinstance(tiers, list)
    overrides = next(t for t in tiers if t["id"] == "overrides")
    for letter, fields in _OVERRIDES:
        rel = f"agents/p30-cas-{letter.lower()}.md"
        overrides["files"].append(
            {
                "path": f"_grimoire/overrides/{rel}",
                "tier": "overrides",
                "size": 1,
                "digest": letter,
                "editable": True,
                "kit_version": None,
                "kit_counterpart": f"_grimoire/kit/{rel}",
                **fields,
            }
        )
    overrides["exists"] = True
    overrides["count"] = len(overrides["files"])
    return real


def test_source_affiche_des_lettres_facon_git(workspace: Page) -> None:
    def fulfil(route: Route) -> None:
        if "tier=" in route.request.url:
            route.fallback()
            return
        response = route.fetch()
        route.fulfill(response=response, body=json.dumps(_tree_with_cases(response.json())))

    workspace.route(_FILES, fulfil)
    _goto(workspace, "source")
    workspace.wait_for_selector("#canvas .tree .sr-git", timeout=10_000)

    letters = {
        row["name"]: row
        for row in workspace.eval_on_selector_all(
            "#canvas .tree .sr-tree-file",
            """(rows) => rows.filter((r) => r.querySelector('.sr-git')).map((r) => ({
                name: r.querySelector('span').textContent,
                letter: r.querySelector('.sr-git').textContent,
                title: r.querySelector('.sr-git').title,
            }))""",
        )
    }
    for letter, _ in _OVERRIDES:
        row = letters[f"p30-cas-{letter.lower()}.md"]
        assert row["letter"] == letter
        assert row["title"], f"la lettre {letter} se dit aussi en clair"
    assert "3.0.0" in letters["p30-cas-u.md"]["title"]
    # Aucune pastille sans mot ne reste dans l'arbre.
    assert workspace.locator("#canvas .tree .sr-tree-file .sico").count() == 0
    workspace.locator("#canvas .tree .sr-tree-file", has_text="p30-cas-a.md").scroll_into_view_if_needed()
    _capture(workspace, "p30-source")


# ── États vides ──────────────────────────────────────────────────────────────


#: `served_empty` est partagé par la suite : un test qui y migre les tâches du
#: standard (bouton « Migrer les tâches ») lui ouvre un ledger. Les deux
#: réponses lues sont donc ramenées à ce que le serveur rend pour un projet
#: neuf (mêmes clés, `tasks_view()` et `blueprint_containers()` sans données).
_EMPTY_ANSWERS = {
    re.compile(r".*/api/workspace/tasks(\?.*)?$"): {
        "ledger": False,
        "tasks": [],
        "count": 0,
        "note": "aucun Mission Ledger — `grimoire task add` en ouvre un",
    },
    re.compile(r".*/api/workspace/blueprints(\?.*)?$"): {"blueprints": []},
}


def _answer_with(patch: dict[str, object]) -> Callable[[Route], None]:
    def fulfil(route: Route) -> None:
        if route.request.method != "GET":
            route.fallback()
            return
        response = route.fetch()
        body = {**response.json(), **patch}
        body.pop("migration_available", None)
        route.fulfill(response=response, body=json.dumps(body))

    return fulfil


def _answer_empty(page: Page) -> None:
    for pattern, patch in _EMPTY_ANSWERS.items():
        page.route(pattern, _answer_with(patch))


def test_les_etats_vides_portent_leur_composition(empty_workspace: Page) -> None:
    _answer_empty(empty_workspace)
    for space, art in (("executer", "i-empty-tasks"), ("concevoir", "i-empty-blueprints")):
        _goto(empty_workspace, space)
        block = empty_workspace.wait_for_selector(f".empty[data-art='{art}']", timeout=10_000)
        assert block is not None
        assert _hrefs(empty_workspace, f".empty[data-art='{art}'] svg.empty-art use") == [f"icons.svg#{art}"]
        # L'encre vient du CSS (le symbole n'en porte aucune) : sans elle, la scène serait invisible.
        stroke = empty_workspace.eval_on_selector(
            f".empty[data-art='{art}'] svg.empty-art", "(n) => getComputedStyle(n).stroke"
        )
        assert stroke not in ("", "none")
        # La composition remplace la petite icône de bac du titre.
        assert empty_workspace.locator(f".empty[data-art='{art}'] h2 svg").count() == 0
        _capture(empty_workspace, f"p30-vide-{space}")
