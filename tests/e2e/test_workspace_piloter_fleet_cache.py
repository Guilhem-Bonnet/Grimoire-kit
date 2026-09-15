"""La Flotte de Piloter ne rebalaye pas tout le registre à chaque changement
de projet (issue #510 point 5).

Cas réel : chaque changement de projet dans le cockpit lançait ``GET
/api/health`` + ``GET /api/memory/status`` pour CHAQUE projet du registre
(34 requêtes pour 17 projets, 20 pour 10, sur les neuf projets réels de
Guilhem). Le fan-out vit dans ``loadFleet`` (``web/workspace/spaces/
piloter.js``) : un cache mémoire par slug (60 s) absorbe les affichages
répétés de la Flotte dans une même session ; « Rafraîchir la flotte » et la
mise à jour d'un projet précis restent les deux seules façons de le
contourner. Sélectionner un projet (cliquer une ligne) n'a jamais touché
cette fonction — une seule lecture ciblée (``loadSheet``) suffit déjà.
"""

from __future__ import annotations

from playwright.sync_api import Browser, Page


def _health_requests(page: Page) -> list[str]:
    """Écoute et rend la liste live des requêtes `/api/health` observées."""
    seen: list[str] = []
    page.on("request", lambda req: seen.append(req.url) if "/api/health" in req.url else None)
    return seen


def test_le_changement_de_projet_n_interroge_que_le_projet_cible(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Critère d'acceptation de l'issue, à la lettre : un registre de 3 projets
    jetables, un changement de projet -> 1 requête `/api/health`, pas 3."""
    served, slug_a, slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
        page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")

        # Flotte par défaut (#351) : le chargement initial balaye les 3
        # projets — un coût attendu et non mesuré ici, qui n'a rien à voir
        # avec le défaut de l'issue.
        page.wait_for_selector(".pl-table tbody tr")
        assert page.locator(".pl-table tbody tr").count() == 3

        # Le changement de projet lui-même : cliquer la ligne d'un AUTRE
        # projet ouvre sa fiche. Le compteur n'écoute qu'à partir d'ici.
        seen = _health_requests(page)
        page.locator(".pl-table tbody tr", has_text=slug_b).first.click()
        page.wait_for_selector("button:has-text('Mettre à jour')")

        assert seen == [f"{served}/api/health?project={slug_b}"], (
            f"changement de projet : {len(seen)} requête(s) /api/health, attendu 1 (pas 3) — {seen}"
        )
    finally:
        context.close()


def test_revenir_sur_la_flotte_sert_le_cache_dans_la_fenetre_de_60s(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Un aller-retour Flotte -> Projet -> Flotte ne doit pas relancer le
    balayage complet tant que le cache (60 s) n'a pas expiré : c'est
    justement ce qui produisait les 34 requêtes constatées sur 17 projets."""
    served, slug_a, slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
        page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
        page.wait_for_selector(".pl-table tbody tr")

        page.locator(".pl-table tbody tr", has_text=slug_b).first.click()
        page.wait_for_selector("button:has-text('Mettre à jour')")

        seen = _health_requests(page)
        page.locator('#zoom-seg button[data-value="flotte"]').click()
        page.wait_for_selector(".pl-table tbody tr")

        assert seen == [], (
            f"retour sur la Flotte dans la fenêtre de cache : {len(seen)} requête(s) /api/health "
            f"inattendue(s) — {seen}"
        )

        # « Rafraîchir la flotte » reste la façon explicite de forcer le
        # balayage complet malgré le cache.
        seen_refresh = _health_requests(page)
        page.locator("button", has_text="Rafraîchir la flotte").click()
        page.wait_for_timeout(500)
        page.wait_for_selector(".pl-table tbody tr")

        assert len(seen_refresh) == 3, (
            f"« Rafraîchir la flotte » : {len(seen_refresh)} requête(s) /api/health, attendu 3 — {seen_refresh}"
        )
    finally:
        context.close()
