"""Perf du cockpit — fiche Piloter et Flotte (issue #541 → #542 → #548).

PR #542 a livré côté serveur ``GET /api/fleet``
(``grimoire.tools.project_health.fleet_status``) : santé + mémoire (mode
rapide, jamais de réseau) de TOUT le registre en une seule réponse, calculées
en parallèle côté serveur. PR #547 (front, chantier antérieur et distinct de
celui-ci) avait ensuite paralléisé les HUIT appels que ``loadSheet()``
faisait encore un par un pour une fiche projet (``health``, ``memoryStatus``,
``doctor``, ``agents``, ``proposals``, ``setupRun``,
``flowRuns('project-upgrade', ...)`` + ``projects()`` pour le nom du projet),
sans en réduire le nombre — huit appels partaient toujours, seulement en
parallèle plutôt qu'en série.

Issue #548 referme ce chantier : ``GET /api/workspace/sheet``
(``workspace_api.sheet_view``) agrège désormais ces sept sous-vues côté
serveur (même principe que ``fleet_status`` : un ``ThreadPoolExecutor`` par
sous-vue plutôt que par projet), plus le nom du projet (résolu depuis le
registre, ``_sheet_project_name`` — remplace le huitième appel
``projects()``). ``doctor`` en est volontairement absent : mesuré comme le
vrai coût des sept (~350ms contre ~120ms pour ``health``, le reste quasi nul
une fois passé par le cache de ``view_cache``) — l'onglet Problèmes
(``ctx.api.doctor()``) le rend séparément, à la demande. Le premier rendu de
la fiche Piloter ne déclenche donc plus qu'UN SEUL appel réseau, jamais huit.

Ce test mesure — il ne suppose jamais — le nombre d'appels réseau au premier
rendu et le temps jusqu'à interactivité, sur un registre de projets jetables
(jamais un vrai projet). Seuil d'interactivité : 400 ms (critère d'arrêt de
l'issue), largement tenu ici puisque la seule route qui restait proche de ce
budget (``doctor``) n'est plus dans le chemin critique.
"""

from __future__ import annotations

import time

from playwright.sync_api import Browser, Page

SHEET_MAX_INTERACTIVE_MS = 400
FLEET_MAX_INTERACTIVE_MS = 900


def _instrument_api(page: Page) -> list[tuple[str, float]]:
    """Liste live ``(url, départ monotonic)`` de chaque requête ``/api/…``."""
    seen: list[tuple[str, float]] = []
    page.on(
        "request",
        lambda req: seen.append((req.url, time.monotonic())) if "/api/" in req.url else None,
    )
    return seen


def test_fiche_piloter_un_seul_appel_sheet_et_interactive_sous_400ms(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Ouvrir la fiche d'un projet depuis la Flotte : UN SEUL appel réseau
    (``GET /api/workspace/sheet``) — pas les huit routes d'avant #548 —
    fiche interactive en moins de 400 ms (issue #548)."""
    served, slug_a, slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
        page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
        page.wait_for_selector(".pl-table tbody tr")

        seen = _instrument_api(page)
        start = time.monotonic()
        page.locator(".pl-table tbody tr", has_text=slug_b).first.click()
        page.wait_for_selector("button:has-text('Mettre à jour')")
        elapsed_ms = (time.monotonic() - start) * 1000

        urls = [u for u, _ in seen]
        old_routes = ("/api/health", "/api/memory/status", "/api/workspace/doctor",
                      "/api/workspace/agents", "/api/workspace/proposals", "/api/setup/run",
                      "/api/workspace/flows/runs", "/api/projects")
        old_calls = [u for u in urls if any(route in u for route in old_routes)]
        sheet_calls = [u for u in urls if "/api/workspace/sheet" in u]

        assert not old_calls, (
            f"une des sept anciennes routes (+ `projects()`) est repartie au premier rendu — {old_calls}"
        )
        assert len(sheet_calls) == 1, (
            f"attendu exactement un appel à /api/workspace/sheet au premier rendu — {urls}"
        )
        assert len(urls) == 1, f"un seul appel réseau attendu au premier rendu — {urls}"
        assert elapsed_ms < SHEET_MAX_INTERACTIVE_MS, (
            f"fiche Piloter interactive en {elapsed_ms:.0f} ms, attendu < {SHEET_MAX_INTERACTIVE_MS} ms"
        )

        # La coquille « chargement… » (#541 point 3) doit être retirée une
        # fois le vrai rendu posé — jamais empilée dessous (root.append sans
        # avoir vidé root d'abord serait un résidu visible, pas seulement
        # un détail cosmétique : deux titres, deux tableaux).
        assert page.locator("text=Chargement…").count() == 0, (
            "la coquille de chargement est restée affichée sous le rendu final"
        )
    finally:
        context.close()


def test_flotte_un_seul_appel_fleet_et_interactive_sous_900ms(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Le niveau Flotte d'un cockpit à 3 projets ne déclenche plus qu'UN
    appel `/api/fleet` (remplace le balayage 2×N), interactif en < 900 ms.

    Piloter/Flotte est l'espace par défaut de la coque
    (``location.hash || '#piloter'``, ``shell.js::main``) : avec
    ``?project=<slug>`` mais sans lancement direct, elle s'affiche dès
    l'amorçage, AVANT que `body[data-ready='1']` ne soit posé — écouter les
    requêtes et démarrer le chronomètre APRÈS ce marqueur serait donc trop
    tard pour observer l'appel `/api/fleet` initial. On écoute et on
    chronomètre dès `page.goto`, pas de navigation explicite séparée à
    déclencher ensuite.
    """
    served, slug_a, _slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        fleet_calls: list[str] = []
        page.on("request", lambda req: fleet_calls.append(req.url) if "/api/fleet" in req.url else None)

        start = time.monotonic()
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_selector(".pl-table tbody tr")
        elapsed_ms = (time.monotonic() - start) * 1000

        assert page.locator(".pl-table tbody tr").count() == 3
        assert len(fleet_calls) == 1, (
            f"Flotte : {len(fleet_calls)} appel(s) /api/fleet, attendu 1 — {fleet_calls}"
        )
        assert elapsed_ms < FLEET_MAX_INTERACTIVE_MS, (
            f"Flotte interactive en {elapsed_ms:.0f} ms, attendu < {FLEET_MAX_INTERACTIVE_MS} ms"
        )
    finally:
        context.close()


def test_refresh_de_la_flotte_ne_double_jamais_les_appels_en_vol(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Deux déclenchements de « Rafraîchir la flotte » qui se chevauchent ne
    doivent produire qu'UN seul appel `/api/fleet` en vol — pas deux requêtes
    concurrentes pour le même rafraîchissement (#541 point 4, garde
    `inFlight` dans `mount()`/`draw()`).

    Un vrai double-clic Playwright (deux `.click()` qui re-cherchent
    l'élément par sélecteur) ne peut PAS reproduire le chevauchement : le
    tout premier effet synchrone de `draw({forceFleet: true})` est
    `root.replaceChildren(...)` (hérité du code d'avant cette PR, pas
    nouveau ici), qui détache le bouton du document avant qu'un second clic
    ne puisse jamais l'atteindre. Le chevauchement est donc reproduit au
    niveau DOM : `dispatchEvent('click')` sur la référence JS directe au
    nœud (qui reste valide même détaché), avec `disabled` remis à `false`
    entre les deux — exactement ce que ferait un utilisateur si le bouton ne
    se désactivait pas. `/api/fleet` est délibérément ralenti (`page.route`)
    pour rendre la fenêtre de chevauchement déterministe plutôt que
    dépendante de la vitesse du disque du harnais.
    """
    served, slug_a, _slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
        page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
        page.wait_for_selector(".pl-table tbody tr")

        fleet_calls: list[str] = []

        def _slow_fleet(route):
            fleet_calls.append(route.request.url)
            time.sleep(0.4)
            route.continue_()

        page.route("**/api/fleet*", _slow_fleet)

        # Chevauchement volontaire et FORCÉ, reproduit au niveau DOM plutôt
        # qu'au niveau Playwright : le clic sur « Rafraîchir la flotte »
        # déclenche `draw({forceFleet: true})`, dont le tout premier effet
        # SYNCHRONE (hérité du code d'avant cette PR, pas nouveau ici) est
        # `root.replaceChildren(...)` — le bouton, enfant de `root`, est donc
        # DÉTACHÉ du document dès le retour du premier clic, avant qu'un
        # second clic Playwright (qui re-cherche l'élément par sélecteur) ne
        # puisse jamais l'atteindre. Un vrai double-déclenchement forcé ne
        # peut donc être obtenu qu'en gardant une référence JS directe au
        # NŒUD (pas une requête DOM différée) et en lui redonnant
        # `disabled = false` avant de le solliciter une seconde fois — ce que
        # fait ce script, exécuté côté page.
        page.evaluate(
            """() => {
                const btn = [...document.querySelectorAll('button')]
                    .find((b) => b.textContent.includes('Rafraîchir la flotte'));
                btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                btn.disabled = false;
                btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            }"""
        )
        page.wait_for_selector(".pl-table tbody tr", timeout=10_000)
        page.wait_for_timeout(600)

        assert len(fleet_calls) == 1, (
            f"rafraîchissement de la flotte pendant un chevauchement : {len(fleet_calls)} appel(s) "
            f"/api/fleet, attendu 1 (dédoublonnés par `inFlight`) — {fleet_calls}"
        )
    finally:
        context.close()
