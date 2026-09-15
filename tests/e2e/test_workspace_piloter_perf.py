"""Perf du cockpit — fiche Piloter et Flotte (issue #541, suite du backend #542).

PR #542 a livré côté serveur ``GET /api/fleet``
(``grimoire.tools.project_health.fleet_status``) : santé + mémoire (mode
rapide, jamais de réseau) de TOUT le registre en une seule réponse, calculées
en parallèle côté serveur. Ce module (front) est la moitié qui restait :

1. ``loadFleet`` (``web/workspace/spaces/piloter.js``) appelait encore
   ``projects()`` PUIS ``health()``/``memoryStatus()`` par projet — un
   balayage 2×N que ``/api/fleet`` rend inutile. Remplacé par un unique
   appel ``ctx.api.fleet()``.
2. ``mount()``/``draw()`` appelait ``ctx.api.projects()`` (nom du projet,
   cockpit) en SÉRIE avant ``loadSheet()`` — deux tours réseau l'un après
   l'autre au lieu d'un seul. Les deux partent maintenant en même temps
   (``Promise.all``).
3. ``draw()`` peignait un écran vide jusqu'à la résolution complète des
   appels — une coquille « chargement… » s'affiche maintenant tout de suite.
4. Un refresh déclenché pendant qu'un tour précédent est encore en vol
   rejoint désormais la même promesse (``inFlight``) plutôt que d'en relancer
   un second en concurrence.

Ce test mesure — il ne suppose jamais — le nombre d'appels réseau, leur
parallélisme réel (débuts de requêtes quasi simultanés, pas espacés du temps
de réponse d'un appel précédent) et le temps jusqu'à interactivité, sur un
registre de projets jetables (jamais un vrai projet).

Budget de la fiche Piloter : ``loadSheet`` porte SEPT appels
(``health``, ``memoryStatus``, ``doctor``, ``agents``, ``proposals``,
``setupRun``, ``flowRuns('project-upgrade', ...)`` — ce dernier ajouté par
l'issue #513, un chantier antérieur et distinct de celui-ci) + UN appel
``projects()`` (nom du projet, cockpit uniquement) désormais parallèle : HUIT
appels au total pour une fiche cockpit, jamais SIX — ce nombre n'est pas dans
le périmètre de cette PR (réduire ``loadSheet`` lui-même serait un chantier
séparé). Ce qui compte ici, et que ce test vérifie, c'est qu'aucun de ces
huit appels n'attend la fin d'un autre pour démarrer.

Seuil d'interactivité de la fiche : mesuré sur ce harnais (projets jetables,
``grimoire init`` réel, chromium headless), le plancher est ~810 ms AVANT
comme APRÈS ce correctif — dominé par la réponse la plus lente des huit
endpoints (``doctor``/``health`` font de l'IO disque réelle), jamais par
l'ordonnancement front. Le vrai gain mesuré ici est l'étalement des DÉPARTS
de requêtes : ~12 ms avant (le petit aller-retour série de ``projects()``),
~1 ms après (huit départs quasi simultanés) — voir le tableau de la PR. Le
seuil ``SHEET_MAX_INTERACTIVE_MS`` est donc fixé avec une marge réaliste
au-dessus du plancher mesuré, pas au 700 ms visé initialement par l'issue
(inatteignable ici sans toucher le temps de réponse du backend, hors
périmètre de cette PR front-only).
"""

from __future__ import annotations

import time

from playwright.sync_api import Browser, Page

SHEET_MAX_CALLS = 8
SHEET_MAX_SPREAD_MS = 300
SHEET_MAX_INTERACTIVE_MS = 1500
FLEET_MAX_INTERACTIVE_MS = 900


def _instrument_api(page: Page) -> list[tuple[str, float]]:
    """Liste live ``(url, départ monotonic)`` de chaque requête ``/api/…``."""
    seen: list[tuple[str, float]] = []
    page.on(
        "request",
        lambda req: seen.append((req.url, time.monotonic())) if "/api/" in req.url else None,
    )
    return seen


def test_fiche_piloter_appels_paralleles_et_interactive_sous_700ms(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Ouvrir la fiche d'un projet depuis la Flotte : au plus HUIT appels
    API (budget documenté en tête de module), tous lancés à quasi le même
    instant (jamais un enchaînement), fiche interactive en moins de 700 ms."""
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
        assert len(urls) <= SHEET_MAX_CALLS, (
            f"fiche Piloter : {len(urls)} appel(s) API, attendu <= {SHEET_MAX_CALLS} — {urls}"
        )
        assert elapsed_ms < SHEET_MAX_INTERACTIVE_MS, (
            f"fiche Piloter interactive en {elapsed_ms:.0f} ms, attendu < {SHEET_MAX_INTERACTIVE_MS} ms"
        )

        # Parallélisme réel : un enchaînement séquentiel espace chaque départ
        # de requête du temps de réponse de la précédente (dizaines à
        # centaines de ms d'IO disque réelle pour health()/doctor()/etc. sur
        # ce harnais) ; un vrai `Promise.all` les démarre à quasi le même
        # instant. On borne l'étalement total, pas chaque paire.
        if len(seen) >= 2:
            starts = sorted(t for _, t in seen)
            spread_ms = (starts[-1] - starts[0]) * 1000
            assert spread_ms < SHEET_MAX_SPREAD_MS, (
                f"appels non parallèles : étalement de {spread_ms:.0f} ms entre le premier et le "
                f"dernier départ de requête (attendu < {SHEET_MAX_SPREAD_MS} ms) — {seen}"
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
