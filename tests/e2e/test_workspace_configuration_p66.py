"""P66 — l'espace Configuration suit l'artboard « Configuration » (style Douce).

Rubriques verticales avec leurs comptes, atterrissage sur « Hôtes et hooks »,
tableau des hooks par hôte, fournisseurs dans le panneau de détail, arbre de
fichiers dans le contenu de sa rubrique. Lecture seule : aucun bouton
d'écriture. Les charges utiles des deux routes sont produites par les vraies
fonctions de lecture sur un dossier jetable (leurs branches sont couvertes par
``tests/unit/test_workspace_config.py``) puis servies à la page.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures de relecture ; sans la
variable le test de capture ne fait rien (il ne se skippe pas : le mode strict
de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route

from grimoire.tools import workspace_config

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

EVENTS = {
    "grimoire-session-start": "SessionStart",
    "grimoire-engagement": "UserPromptSubmit",
    "grimoire-control-surface-guard": "PreToolUse",
    "grimoire-board-transitions": "PostToolUse",
    "grimoire-pre-compact": "PreCompact",
    "grimoire-master-stop-hook": "Stop",
}
MODES = {
    "grimoire-pattern-consumption": ("SessionStart", "shadow"),
    "grimoire-doc-drift": ("PostToolUse", "canary"),
}

PROVIDERS = """\
providers:
  - id: anthropic
    enabled: true
    currency: quota
    models:
      - {id: haiku, tier: cheap}
      - {id: opus, tier: premium}
  - id: local
    enabled: true
    currency: local
    models:
      - {id: qwen3-coder, tier: cheap}
  - id: openai
    enabled: false
routing:
  default_provider: anthropic
"""


@pytest.fixture
def payloads(tmp_path: Path) -> dict[str, dict[str, Any]]:
    hooks = {hook: {"event": event, "mode": "enforced"} for hook, event in EVENTS.items()}
    hooks.update({hook: {"event": event, "mode": mode} for hook, (event, mode) in MODES.items()})
    registry = tmp_path / workspace_config.REGISTRY_RELPATH
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"version": 1, "hooks": hooks}), encoding="utf-8")
    github = tmp_path / ".github" / "hooks"
    github.mkdir(parents=True)
    for hook in ("grimoire-session-start", "grimoire-doc-drift", "grimoire-engagement"):
        (github / f"{hook}.json").write_text("{}", encoding="utf-8")
    providers = tmp_path / "_grimoire" / "standard" / "llm-provider-registry.yaml"
    providers.parent.mkdir(parents=True)
    providers.write_text(PROVIDERS, encoding="utf-8")
    return {
        "hooks": workspace_config.hooks_view(tmp_path),
        "providers": workspace_config.providers_view(tmp_path),
    }


def _fulfiller(body: dict[str, Any]) -> Callable[[Route], None]:
    def handler(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    return handler


def _serve(page: Page, payloads: dict[str, dict[str, Any]]) -> None:
    for name, body in payloads.items():
        page.route(f"**/api/workspace/{name}*", _fulfiller(body))


def _open_configuration(page: Page, payloads: dict[str, dict[str, Any]]) -> None:
    _serve(page, payloads)
    page.evaluate("() => { window.GrimoireWorkspace.goto('configuration'); }")
    page.wait_for_function("window.GrimoireWorkspace.section === 'hotes'")
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=30_000)
    page.wait_for_selector(".cf-wrap table.tbl tbody tr", timeout=30_000)


def test_les_rubriques_sont_verticales_avec_leurs_comptes(cockpit_workspace: Page, payloads: dict[str, dict[str, Any]]) -> None:
    page = cockpit_workspace
    _open_configuration(page, payloads)
    tabs = page.locator("#section-tabs .tab")
    assert [t.locator(".tab-lbl").inner_text() for t in tabs.all()] == [
        "Agents", "Fichiers", "Blueprints", "Hôtes et hooks", "Fournisseurs et budgets", "Projets", "Préférences",
    ]
    page.wait_for_selector("#section-tabs [data-count='hotes']")
    assert page.locator("#section-tabs [data-count='hotes']").inner_text() == "8"
    assert page.locator("#section-tabs [data-section='hotes']").get_attribute("aria-selected") == "true"
    assert page.locator("#section-tabs [data-count='fichiers']").count() == 0
    box = page.locator("#section-tabs").bounding_box()
    assert box is not None and box["height"] > 150 and box["width"] < 260


def test_hotes_et_hooks_atterrissage_sans_explorateur(cockpit_workspace: Page, payloads: dict[str, dict[str, Any]]) -> None:
    page = cockpit_workspace
    _open_configuration(page, payloads)
    assert page.locator("#panel-explorer").is_hidden()
    title = page.locator(".cf-wrap .space-title")
    assert title.inner_text() == "Hôtes et hooks"
    assert title.evaluate("e => getComputedStyle(e).fontSize") == "32px"
    sub = page.locator(".cf-wrap .space-sub").inner_text()
    assert sub == "6 appliqués · 1 en essai · 1 en observation"
    assert page.locator(".cf-wrap .formula").first.inner_text().replace("\n", "").strip() == "›grimoire hooks status"
    rows = page.locator(".cf-wrap tbody tr")
    assert rows.count() == 8
    first = rows.first.locator("td")
    assert first.nth(0).inner_text() == "grimoire-session-start"
    assert first.nth(1).inner_text() == "SessionStart"
    assert first.nth(2).locator(".badge.ok").inner_text() == "appliqué"
    assert page.locator(".cf-wrap tbody .badge.acc").inner_text() == "essai"
    # Lecture seule : aucun bouton d'écriture dans le contenu.
    assert page.locator(".cf-wrap tbody button").count() == 0
    for word in ("Promouvoir", "Passer en observation", "Remettre à zéro"):
        assert word not in page.locator("#canvas").inner_text()


def test_le_segment_par_hote_filtre_le_tableau(cockpit_workspace: Page, payloads: dict[str, dict[str, Any]]) -> None:
    page = cockpit_workspace
    _open_configuration(page, payloads)
    seg = page.locator(".cf-wrap .seg")
    assert [b.inner_text() for b in seg.locator("button").all()] == ["Tous", "Copilot"]
    seg.get_by_role("button", name="Copilot").click()
    assert page.locator(".cf-wrap tbody tr").count() == 3
    assert seg.get_by_role("button", name="Copilot").get_attribute("aria-pressed") == "true"
    seg.get_by_role("button", name="Tous").click()
    assert page.locator(".cf-wrap tbody tr").count() == 8


def test_les_fournisseurs_sont_dans_le_panneau_de_detail(cockpit_workspace: Page, payloads: dict[str, dict[str, Any]]) -> None:
    page = cockpit_workspace
    _open_configuration(page, payloads)
    panel = page.locator("#panel-inspector")
    panel.locator("[data-panel='providers']").wait_for()
    assert panel.is_visible()
    names = panel.locator(".cf-prov-name").all_inner_texts()
    assert names == ["anthropic", "local", "openai"]
    assert panel.locator(".cf-prov .badge").all_inner_texts() == ["disponible", "disponible", "désactivé"]
    assert "grimoire providers status" in panel.inner_text()
    assert "Remettre à zéro" not in panel.inner_text()


def test_fournisseurs_agents_et_fichiers_ont_leur_rubrique(cockpit_workspace: Page, payloads: dict[str, dict[str, Any]]) -> None:
    page = cockpit_workspace
    _open_configuration(page, payloads)

    page.locator("#section-tabs [data-section='fournisseurs']").click()
    page.wait_for_function("window.GrimoireWorkspace.section === 'fournisseurs'")
    page.wait_for_selector(".cf-wrap tbody tr")
    assert page.locator(".cf-wrap .space-title").inner_text() == "Fournisseurs et budgets"
    assert page.locator(".cf-wrap tbody tr").count() == 3
    assert page.locator("#panel-inspector").is_hidden()

    page.locator("#section-tabs [data-section='agents']").click()
    page.wait_for_function("window.GrimoireWorkspace.section === 'agents'")
    page.wait_for_selector(".cf-wrap .space-title")
    assert page.locator(".cf-wrap .space-title").inner_text() == "Agents"
    page.wait_for_selector(".cf-wrap tbody tr", timeout=30_000)
    assert page.locator(".cf-wrap thead th").all_inner_texts()[:3] == ["Agent", "Couche", "Outils"]

    # Fichiers : l'arbre est dans le contenu, jamais dans l'explorateur de la coque.
    page.locator("#section-tabs [data-section='fichiers']").click()
    page.wait_for_function("window.GrimoireWorkspace.section === 'fichiers'")
    page.wait_for_selector("#canvas .sr-tree .sr-tree-file", timeout=30_000)
    assert page.locator("#panel-explorer").is_hidden()
    assert page.locator("#explorer-body .sr-tree-file").count() == 0
    # L'atelier rouvre l'explorateur générique.
    page.keyboard.press("Control+Shift+L")
    page.wait_for_function("document.documentElement.dataset.atelier === 'on'")
    assert page.locator("#panel-explorer").is_visible()


def test_mobile_sans_debordement(browser: Browser, payloads: dict[str, dict[str, Any]], served_cockpit: tuple[str, str]) -> None:
    served, slug = served_cockpit
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", reduced_motion="reduce")
    page = context.new_page()
    try:
        _serve(page, payloads)
        page.goto(f"{served}/workspace/index.html?project={slug}#configuration", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_selector(".cf-wrap table.tbl tbody tr", timeout=30_000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_captures_de_relecture(browser: Browser, payloads: dict[str, dict[str, Any]], served_cockpit: tuple[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    served, slug = served_cockpit
    shots = (
        ("p66-hotes-et-hooks-clair", "configuration/hotes", (1440, 900)),
        ("p66-fichiers-clair", "configuration/fichiers", (1440, 900)),
        ("p66-hotes-et-hooks-mobile", "configuration/hotes", (390, 844)),
    )
    for name, section, size in shots:
        context = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme="light", reduced_motion="reduce")
        page = context.new_page()
        try:
            _serve(page, payloads)
            page.goto(f"{served}/workspace/index.html?project={slug}#{section}", wait_until="domcontentloaded")
            page.wait_for_selector("body[data-ready='1']", timeout=30_000)
            page.evaluate("() => document.querySelector(`#st-theme [data-theme-pref='light']`).click()")
            page.wait_for_selector(".cf-wrap table.tbl tbody tr, .sr-tree .sr-tree-file", timeout=30_000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / f"{name}.png"), full_page=False)
        finally:
            context.close()
    assert len(list(out.glob("p66-*.png"))) == 3
