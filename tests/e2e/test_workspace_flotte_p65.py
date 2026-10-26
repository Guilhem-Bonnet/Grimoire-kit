# ruff: noqa: F811
"""La Flotte conforme à la maquette Douce (P65), dans un vrai navigateur.

Le cockpit est ouvert sur « Atelier Nord » (voir ``test_workspace_a_traiter_p32``) ;
le registre porte trois autres projets : « Studio Pixel » (une tâche bloquée),
« Calme » (rien n'y attend) et « Banc d'essai » (dossier disparu : illisible).
Le retard de kit de « Studio Pixel » est posé en interceptant ``/api/fleet`` —
aucun projet frais n'est en retard, et c'est la forme de la réponse qu'on
éprouve ici, pas le calcul du retard (``test_project_health``).

Ce que chaque test prouve :

- l'en-tête : titre de 32 px, sous-ligne de comptes réels, formule
  ``grimoire cockpit list`` ;
- la table : projet + chemin, badges d'attention lus sur l'API d'attention, kit
  et écart, sessions, dernier signe, ouverture de la fiche ;
- les segments Tous / Demandent une action / Kit en retard et le filtre texte ;
- aucune sélection multiple ni action groupée : elles n'existent pas encore ;
- le tableau reste lisible à 390 px sans défilement horizontal.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les captures
(clair, 1440x900 en portée « Tous les projets », et 390 px) ; sans la variable
il ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout
skip en échec).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .test_workspace_a_traiter_p32 import (  # noqa: F401  (fixture de session partagée)
    CALM,
    HOME_NAME,
    MISSING,
    STUDIO,
    served_inbox,
)

BEHIND = 24


def _late_kit(route) -> None:  # type: ignore[no-untyped-def]
    response = route.fetch()
    payload = response.json()
    for entry in payload["projects"]:
        if entry["name"] == STUDIO and isinstance(entry.get("health"), dict):
            entry["health"]["kit"] = {
                **entry["health"]["kit"],
                "scaffolded": True, "upToDate": False, "behind": BEHIND, "catalogAvailable": True,
                "aligned": "3.62.0",
            }
    route.fulfill(response=response, body=json.dumps(payload))


def _open(page: Page, served: dict[str, str]) -> None:
    page.route("**/api/fleet*", _late_kit)
    page.goto(f"{served['url']}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'flotte'")
    page.wait_for_selector(".pl-fleet .tbl tbody tr", timeout=30_000)


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", reduced_motion="reduce")
    shot = context.new_page()
    try:
        yield shot
    finally:
        context.close()


def _names(page: Page) -> list[str]:
    return page.locator(".pl-fleet .tbl tbody tr .pl-name").all_inner_texts()


def test_en_tete_titre_comptes_reels_et_formule(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox)
    title = page.locator(".pl-fleet .space-title")
    assert title.inner_text() == "Flotte"
    assert title.evaluate("e => getComputedStyle(e).fontSize") == "32px"
    sub = page.locator(".pl-fleet .space-sub").inner_text()
    # Quatre projets au registre ; trois demandent une action (Atelier Nord, Studio Pixel, Banc d'essai).
    assert sub.startswith("4 projets suivis")
    assert "3 demandent une action" in sub
    assert page.locator(".pl-fleet .pl-formula").inner_text().replace("\n", "").strip() == "›grimoire cockpit list"
    # Les gestes existants restent là.
    assert page.get_by_role("button", name="Nouveau projet").is_visible()
    assert page.get_by_role("button", name="Rafraîchir la flotte").is_visible()


def test_la_table_porte_projet_chemin_attention_kit_sessions_dernier_signe(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox)
    heads = page.locator(".pl-fleet .tbl thead th").all_inner_texts()
    assert [h.strip() for h in heads[:5]] == ["Projet", "Attention", "Kit", "Sessions", "Dernier signe"]
    assert sorted(_names(page)) == sorted([HOME_NAME, STUDIO, CALM, MISSING])

    home = page.locator(".pl-fleet .tbl tbody tr", has=page.locator(".pl-name", has_text=HOME_NAME))
    assert home.locator(".pl-path").inner_text().endswith(HOME_NAME)
    badges = home.locator("td").nth(1).locator(".badge").all_inner_texts()
    assert any("tâche bloquée" in b for b in badges)
    assert any("proposition" in b for b in badges)
    assert any("question" in b for b in badges)
    # Aucun emoji : les ouvertures sont des symboles du sprite.
    assert home.locator(".pl-open use").get_attribute("href").startswith("icons.svg#")

    studio = page.locator(".pl-fleet .tbl tbody tr", has=page.locator(".pl-name", has_text=STUDIO))
    kit = studio.locator("td").nth(2).inner_text()
    assert "3.62.0" in kit and f"{BEHIND} fichiers d’écart" in kit

    calm = page.locator(".pl-fleet .tbl tbody tr", has=page.locator(".pl-name", has_text=CALM))
    assert "—" in calm.locator("td").nth(1).inner_text()


def test_les_segments_et_le_filtre_texte_reduisent_la_table(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox)
    seg = page.locator(".pl-fleet .pl-filterbar .seg")
    assert [b.get_attribute("aria-pressed") for b in seg.locator("button").all()] == ["true", "false", "false"]

    seg.get_by_role("button", name="Kit en retard").click()
    assert _names(page) == [STUDIO]
    seg.get_by_role("button", name="Demandent une action").click()
    assert sorted(_names(page)) == sorted([HOME_NAME, STUDIO, MISSING])
    seg.get_by_role("button", name="Tous").click()
    assert len(_names(page)) == 4

    page.get_by_label("Filtrer les projets").fill("calme")
    assert _names(page) == [CALM]
    page.get_by_label("Filtrer les projets").fill("zzz-introuvable")
    assert page.locator(".pl-none").is_visible()
    assert page.locator(".pl-fleet .tbl-wrap").is_hidden()


def test_ouvrir_une_ligne_change_la_portee_vers_le_projet(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox)
    page.get_by_role("button", name=f"Ouvrir {STUDIO}").click()
    page.wait_for_url(lambda url: f"project={served_inbox['studio']}" in url, timeout=30_000)


def test_pas_de_selection_multiple_ni_d_action_groupee(page: Page, served_inbox: dict[str, str]) -> None:
    """Ni l'aperçu groupé ni « Masquer » n'existent : la maquette les montre, la page non."""
    _open(page, served_inbox)
    assert page.locator(".pl-fleet input[type='checkbox']").count() == 0
    text = page.locator("#canvas").inner_text()
    assert "Retirer du registre" not in text
    assert "Aperçu groupé" not in text


def test_a_390_px_la_flotte_tient_sans_defilement_horizontal(browser: Browser, served_inbox: dict[str, str]) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    mobile = context.new_page()
    try:
        mobile.route("**/api/fleet*", _late_kit)
        mobile.goto(f"{served_inbox['url']}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        mobile.wait_for_selector("body[data-ready='1']", timeout=30_000)
        mobile.wait_for_selector(".pl-fleet .pl-card", timeout=30_000)
        assert mobile.locator(".pl-fleet .pl-card").count() == 4
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_captures_de_relecture(browser: Browser, served_inbox: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for name, size in (("p65-flotte-tous-clair", (1440, 900)), ("p65-flotte-mobile", (390, 844))):
        context = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme="light", reduced_motion="reduce")
        shot = context.new_page()
        try:
            shot.route("**/api/fleet*", _late_kit)
            shot.goto(f"{served_inbox['url']}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
            shot.wait_for_selector("body[data-ready='1']", timeout=30_000)
            shot.evaluate("() => document.querySelector(`#st-theme [data-theme-pref='light']`).click()")
            shot.wait_for_selector(".pl-fleet .pl-badges", state="attached", timeout=30_000)
            shot.screenshot(path=str(out / f"{name}.png"), full_page=False)
        finally:
            context.close()
    assert len(list(out.glob("p65-flotte-*.png"))) == 2
