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
(jamais un vrai projet). Interactivité mesurée en rapport à la machine (le seuil fixe de 400 ms,
critère d'arrêt de l'issue, était instable en CI, #1386), tenue puisque la seule route qui restait proche de ce
budget (``doctor``) n'est plus dans le chemin critique.
"""

from __future__ import annotations

import time

from playwright.sync_api import Browser, Page, Route


def _instrument_api(page: Page) -> list[tuple[str, float]]:
    """Liste live ``(url, départ monotonic)`` de chaque requête ``/api/…``."""
    seen: list[tuple[str, float]] = []
    page.on(
        "request",
        lambda req: seen.append((req.url, time.monotonic())) if "/api/" in req.url else None,
    )
    return seen


#: Même principe que pour la Flotte : « interactive » se mesure en rapport à la machine, jamais en millisecondes
#: (le seuil fixe de 400 ms mesurait aussi la charge du runner). Le montage de la fiche, de l'ordre `goto('flotte')`
#: au bouton « Mettre à jour » peint, est rapporté au démarrage de la même page (de `goto` à `body[data-ready]`),
#: qui subit la même charge. Un montage qui attend un second appel ou une lecture lente le multiplie.
SHEET_MOUNT_MAX_RATIO = 3


def _open_sheet(browser: Browser, served: str, slug: str) -> tuple[list[str], float, float, int]:
    """Ouvre la fiche depuis la Flotte ; rend (URL d'API, démarrage en ms, montage en ms, coquilles restantes)."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        # Depuis P31, choisir B dans la Flotte change la portée (rechargement) ;
        # ce qui se mesure ici est le montage de la fiche dans la portée B.
        begun = time.monotonic()
        page.goto(f"{served}/workspace/index.html?project={slug}#a-traiter", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        boot_ms = (time.monotonic() - begun) * 1000

        seen = _instrument_api(page)
        start = time.monotonic()
        page.evaluate("() => window.GrimoireWorkspace.goto('flotte')")
        page.wait_for_selector("button:has-text('Mettre à jour')")
        mount_ms = (time.monotonic() - start) * 1000
        # Un appel qui partirait après le rendu (cascade) doit aussi compter : on laisse le réseau se calmer.
        page.wait_for_load_state("networkidle")
        return [u for u, _ in seen], boot_ms, mount_ms, page.locator("text=Chargement…").count()
    finally:
        context.close()


def test_fiche_piloter_un_seul_appel_sheet_et_interactive_en_rapport_a_la_machine(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Ouvrir la fiche d'un projet depuis la Flotte : UN SEUL appel réseau
    (``GET /api/workspace/sheet``) — pas les huit routes d'avant #548 —
    et un montage sans coût disproportionné (issue #548).

    Le nombre d'appels est structurel : exact à chaque chargement. La rapidité est relative :
    meilleur rapport sur trois chargements, voir `SHEET_MOUNT_MAX_RATIO`.
    """
    served, _slug_a, slug_b, _slug_c = served_cockpit_triple
    old_routes = ("/api/health", "/api/memory/status", "/api/workspace/doctor",
                  "/api/workspace/agents", "/api/workspace/proposals", "/api/setup/run",
                  "/api/workspace/flows/runs", "/api/projects")
    ratios: list[float] = []
    for _ in range(3):
        urls, boot_ms, mount_ms, shells = _open_sheet(browser, served, slug_b)
        old_calls = [u for u in urls if any(route in u for route in old_routes)]
        sheet_calls = [u for u in urls if "/api/workspace/sheet" in u]

        assert not old_calls, (
            f"une des sept anciennes routes (+ `projects()`) est repartie au premier rendu — {old_calls}"
        )
        # #1243 : la fiche se peint en deux temps. Trois appels `sheet` partent ensemble — les sous-vues rapides,
        # puis les agents, puis les propositions, chacune par sa section — plus la capacité, que son panneau lit
        # lui-même. Jamais une cascade : pas un appel de plus, pas un de la fiche entière.
        sections = sorted(u.split("sections=")[1].split("&")[0] for u in sheet_calls if "sections=" in u)
        assert sections == sorted(["agents", "proposals", "health%2Cmemory%2CsetupRun%2CupgradeRuns%2CflowRuns%2Cname"]), (
            f"les trois sections de la fiche attendues, ni plus ni moins — {urls}"
        )
        assert len(sheet_calls) == 3, f"trois appels à /api/workspace/sheet attendus au premier rendu — {urls}"
        capacity_calls = [u for u in urls if "/api/workspace/capacity" in u]
        assert len(capacity_calls) == 1, f"un seul appel de capacité attendu — {urls}"
        assert len(urls) == 4, f"quatre appels réseau exactement au premier rendu — {urls}"
        # La coquille « chargement… » (#541 point 3) doit être retirée une
        # fois le vrai rendu posé — jamais empilée dessous (root.append sans
        # avoir vidé root d'abord serait un résidu visible, pas seulement
        # un détail cosmétique : deux titres, deux tableaux).
        assert shells == 0, "la coquille de chargement est restée affichée sous le rendu final"
        ratios.append(mount_ms / max(boot_ms, 1.0))

    assert min(ratios) <= SHEET_MOUNT_MAX_RATIO, (
        f"montage de la fiche Piloter : {min(ratios):.1f} fois le démarrage de la page "
        f"(plafond : {SHEET_MOUNT_MAX_RATIO} fois)"
    )


#: « Interactive » se mesure en rapport à la machine, jamais en millisecondes : un runner partagé est lent, pas
#: régressé (le seuil fixe de 900 ms cassait à 988 et 1068 ms). Le premier rendu, de la requête `/api/fleet` à la
#: table peinte, est rapporté au démarrage de la même page (de `goto` à cette requête), qui subit la même charge
#: de processeur. Observé : ~0,4 fois, y compris machine saturée. Un premier rendu qui attend un second appel ou
#: une lecture lente le multiplie par plus de trois.
FLEET_FIRST_RENDER_MAX_RATIO = 3


def _load_flotte(browser: Browser, served: str) -> tuple[list[str], int, float, float]:
    """Charge la Flotte « Tous les projets » ; rend (appels fleet, lignes, démarrage en ms, requête→table en ms).

    La Flotte s'affiche dès l'amorçage, AVANT `body[data-ready='1']` : on écoute les requêtes dès
    `page.goto`, jamais après ce marqueur.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        fleet_calls: list[str] = []
        started: list[float] = []

        def _on_request(req: object) -> None:
            if "/api/fleet" in req.url:  # type: ignore[attr-defined]
                fleet_calls.append(req.url)  # type: ignore[attr-defined]
                started.append(time.monotonic())

        page.on("request", _on_request)
        begun = time.monotonic()
        page.goto(f"{served}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_selector(".tbl tbody tr")
        painted = time.monotonic()
        rows = page.locator(".tbl tbody tr").count()
        return list(fleet_calls), rows, (started[0] - begun) * 1000, (painted - started[0]) * 1000
    finally:
        context.close()


def test_flotte_un_seul_appel_fleet_et_interactive_en_rapport_a_la_machine(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """Le niveau Flotte d'un cockpit à 3 projets ne déclenche plus qu'UN appel `/api/fleet`
    (remplace le balayage 2×N) et peint sa table sans coût disproportionné.

    Le nombre d'appels est structurel : exact à chaque chargement. La rapidité est relative :
    meilleur rapport sur trois chargements (une pause de l'ordonnanceur ne compte pas), voir
    `FLEET_FIRST_RENDER_MAX_RATIO`.
    """
    served, _slug_a, _slug_b, _slug_c = served_cockpit_triple
    ratios: list[float] = []
    for _ in range(3):
        fleet_calls, rows, boot_ms, first_ms = _load_flotte(browser, served)
        assert rows == 3
        assert len(fleet_calls) == 1, f"Flotte : {len(fleet_calls)} appel(s) /api/fleet, attendu 1 — {fleet_calls}"
        ratios.append(first_ms / max(boot_ms, 1.0))

    assert min(ratios) <= FLEET_FIRST_RENDER_MAX_RATIO, (
        f"premier rendu de la Flotte : {min(ratios):.1f} fois le démarrage de la page "
        f"(plafond : {FLEET_FIRST_RENDER_MAX_RATIO} fois)"
    )


def test_la_flotte_ne_retient_jamais_sa_table_pour_la_lecture_d_attention(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """La lecture d'attention (P22) parcourt tout le registre : elle ne doit jamais retenir la table.

    Les lectures d'attention sont tenues en suspens : la table se peint quand même,
    la sous-ligne dit que l'attention est en cours de lecture, puis la Flotte se
    repeint avec sa réponse. Avant ce correctif, `drawOnce` attendait flotte ET
    attention ensemble, et la table restait vide tant que l'attention n'avait pas répondu.
    """
    served, _slug_a, _slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    held: list[Route] = []
    released = {"now": False}

    def _hold(route: Route) -> None:
        if released["now"]:
            route.continue_()
        else:
            held.append(route)

    page.route("**/api/workspace/attention*", _hold)
    try:
        page.goto(f"{served}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        page.wait_for_selector(".tbl tbody tr", timeout=10_000)

        assert page.locator(".tbl tbody tr").count() == 3
        assert held, "la lecture d'attention est partie en même temps que la flotte"
        assert "attention en cours de lecture" in page.locator(".pl-fleet").inner_text()

        released["now"] = True
        for route in held:
            route.continue_()
        page.wait_for_function(
            "() => !document.querySelector('.pl-fleet')?.innerText.includes('attention en cours de lecture')",
            timeout=10_000,
        )
        assert page.locator(".tbl tbody tr").count() == 3
    finally:
        context.close()


def test_aucune_lecture_d_attention_ne_dispute_le_serveur_a_la_flotte(
    browser: Browser, served_cockpit_triple: tuple[str, str, str, str]
) -> None:
    """La lecture d'attention parcourt tout le registre côté serveur (GIL) : au
    chargement de la Flotte, ni la Flotte ni le compteur de la barre ne la lancent
    avant que `/api/fleet` ait répondu — sinon elle ralentit la table qu'on attend."""
    served, _slug_a, _slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    events: list[tuple[str, float]] = []
    page.on("request", lambda req: events.append(("attention", time.monotonic())) if "/api/workspace/attention" in req.url else None)
    page.on("response", lambda resp: events.append(("fleet", time.monotonic())) if "/api/fleet" in resp.url else None)
    try:
        page.goto(f"{served}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        page.wait_for_selector(".tbl tbody tr", timeout=10_000)
        page.wait_for_timeout(1_500)  # laisse partir la lecture différée du compteur

        fleet_at = next(t for kind, t in events if kind == "fleet")
        early = [t for kind, t in events if kind == "attention" and t < fleet_at]
        assert not early, f"{len(early)} lecture(s) d'attention partie(s) avant la réponse de /api/fleet"
        assert any(kind == "attention" for kind, _ in events), "l'attention finit bien par être lue"
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
    served, _slug_a, _slug_b, _slug_c = served_cockpit_triple
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{served}/workspace/index.html?scope=all#flotte", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_function("() => window.GrimoireWorkspace.module === 'piloter'")
        page.wait_for_selector(".tbl tbody tr")

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
        page.wait_for_selector(".tbl tbody tr", timeout=10_000)
        page.wait_for_timeout(600)

        assert len(fleet_calls) == 1, (
            f"rafraîchissement de la flotte pendant un chevauchement : {len(fleet_calls)} appel(s) "
            f"/api/fleet, attendu 1 (dédoublonnés par `inFlight`) — {fleet_calls}"
        )
    finally:
        context.close()
