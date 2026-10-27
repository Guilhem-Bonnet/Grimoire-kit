"""P36 — premier lancement en lecture seule : la page détecte l'état, le cockpit guide sans écrire.

Critères de la tâche ``GAO-p36-premier-001`` :

1. servie par le cockpit, la page lit ``/api/health``, ``/api/memory/status``
   (sans sonde), ``/api/setup/run`` et ``/api/status`` et affiche l'état S0 à
   S3 avec sa source ; sur la vitrine publiée ou en ``file:``, aucune requête
   vers localhost ni vers une API ;
2. sur un projet non configuré, le cockpit affiche le guide (détection, profil
   mémoire avec les cartes impossibles grisées et leur raison, plan, commandes
   à copier) ; aucun POST émis ;
3. captures (``GRIMOIRE_E2E_CAPTURES=<dossier>``) : sans la variable, le test
   de capture ne fait rien (il ne se skippe pas : la CI promeut tout skip en
   échec).

Un seul cockpit sert trois projets : un dossier vierge (le projet de
lancement), un projet configuré en lexical et un projet dont le dernier
passage du wizard a échoué. Le cockpit tourne avec un ``PATH`` vide : Docker y
est donc introuvable sur toute machine, CI comprise, et les profils graphe et
complet sont impossibles pour une raison connue d'avance. Les autres sondes
(TCP vers localhost) restent réelles ; aucun test ne dépend de leur verdict.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, Route

WEB = Path(__file__).resolve().parents[2] / "web"
GUIDE = "[data-role='setup-guide']"
STATE = f"{GUIDE} [data-role='setup-state']"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"le cockpit n'a pas répondu sur :{port}")


def _init(root: Path, name: str, backend: str = "local", profile: str = "lexical") -> None:
    root.mkdir(parents=True)
    env = {**os.environ, "GRIMOIRE_NO_COCKPIT": "1", "NO_COLOR": "1"}
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", name,
         "--backend", backend, "--memory-profile", profile],
        cwd=str(root), env=env, capture_output=True, check=False, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail(f"`grimoire init` n'a pas équipé {root}")


@pytest.fixture(scope="module")
def cockpit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("p36")
    blank = base / "projet-vierge"
    blank.mkdir()
    configured = base / "projet-lexical"
    _init(configured, "projet-lexical")
    failed = base / "projet-echec"
    _init(failed, "projet-echec")
    # Le journal que le wizard écrit après un passage en échec (`execute_setup_plan`).
    (failed / "_grimoire" / "setup-run.json").write_text(json.dumps({
        "executedAt": "2026-10-26T09:00:00+0000", "project": "projet-echec", "ok": False, "doctorOk": False,
        "steps": [
            {"step": "init", "status": "done", "detail": "project-context.yaml écrit"},
            {"step": "standard", "status": "failed", "detail": "profil governed introuvable"},
        ],
        "env": [], "extensionsInstalled": [], "extensionErrors": [],
    }), encoding="utf-8")

    home = tmp_path_factory.mktemp("p36-cockpit-home")
    env = {**os.environ, "GRIMOIRE_COCKPIT_HOME": str(home), "NO_COLOR": "1"}
    # Le projet de lancement doit être adopté : la garde de la suite l'interdit par défaut.
    env.pop("GRIMOIRE_NO_COCKPIT", None)
    for root in (configured, failed):
        subprocess.run([sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
                       env=env, capture_output=True, check=False, timeout=60)
    # PATH vide : `docker` introuvable, donc graphe et complet impossibles, partout.
    empty_bin = tmp_path_factory.mktemp("p36-path")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh",
         "--project-root", str(blank)],
        cwd=str(blank), env={**env, "PATH": str(empty_bin)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        registry = json.loads((home / "registry.json").read_text(encoding="utf-8"))
        slug = {Path(e["path"]).name: e["slug"] for e in registry}
        assert {"projet-vierge", "projet-lexical", "projet-echec"} <= set(slug), registry
        yield {
            "url": f"http://127.0.0.1:{port}",
            "blank": slug["projet-vierge"],
            "configured": slug["projet-lexical"],
            "failed": slug["projet-echec"],
            "home": str(home),
            "base": str(base),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _open(browser: Browser, url: str, *, scheme: str = "dark", width: int = 1440) -> tuple[Page, list[tuple[str, str]]]:
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.new_page()
    seen: list[tuple[str, str]] = []
    page.on("request", lambda req: seen.append((req.method, req.url)))
    page.goto(url, wait_until="domcontentloaded")
    return page, seen


def _api(seen: list[tuple[str, str]]) -> list[str]:
    return [url for _method, url in seen if "/api/" in url]


def _writes(seen: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(m, u) for m, u in seen if m not in ("GET", "HEAD", "OPTIONS")]


# ── Critère 2 : le guide sur un projet non configuré ────────────────────────


def test_le_cockpit_guide_un_projet_non_configure_sans_rien_ecrire(browser: Browser, cockpit: dict[str, str]) -> None:
    page, seen = _open(browser, f"{cockpit['url']}/workspace/index.html")
    try:
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        # À traiter, l'accueil, a cédé la place au guide.
        state = page.wait_for_selector(f"{STATE}[data-state='S0']", timeout=30_000)
        assert page.evaluate("window.GrimoireWorkspace.space") == "a-traiter"
        assert "/api/health · kit.scaffolded = false" in state.inner_text()

        # Détection : Docker est introuvable (PATH vide), et le guide le dit.
        docker = page.locator(f"{GUIDE} [data-check='docker']")
        assert docker.get_attribute("data-state") == "missing"
        assert "absent" in docker.inner_text()
        for check in ("python", "installer", "embedding", "egress", "services", "hosts", "projects"):
            assert page.locator(f"{GUIDE} [data-check='{check}']").count() == 1, check

        # Profil mémoire : quatre cartes, les impossibles grisées avec leur raison.
        cards = page.locator(f"{GUIDE} [data-role='setup-profiles'] [data-profile]")
        assert cards.count() == 4
        for impossible in ("graphe", "complet"):
            card = page.locator(f"{GUIDE} [data-profile='{impossible}']")
            assert card.get_attribute("aria-disabled") == "true"
            assert card.get_attribute("data-possible") == "false"
            assert "impossible ici" in card.inner_text()
            assert "demande Docker" in card.locator("[data-role='reason']").first.inner_text()
            card.click(force=True)
            assert card.get_attribute("aria-checked") == "false", "une carte impossible ne se choisit pas"
        for possible in ("lexical", "standard"):
            assert page.locator(f"{GUIDE} [data-profile='{possible}']").get_attribute("aria-disabled") is None

        # Choisir lexical change le plan et les commandes, sans rien envoyer.
        page.locator(f"{GUIDE} [data-profile='lexical']").click()
        assert page.locator(f"{GUIDE} [data-profile='lexical']").get_attribute("aria-checked") == "true"
        plan = page.locator(f"{GUIDE} [data-role='setup-plan']")
        assert "project-context.yaml" in plan.inner_text()
        assert "profil Lexical" in plan.inner_text()
        commands = page.locator(f"{GUIDE} [data-role='setup-commands'] [data-command]")
        listed = [commands.nth(i).get_attribute("data-command") for i in range(commands.count())]
        assert listed[0] == "grimoire init . -y --memory-profile lexical"
        assert "grimoire doctor ." in listed

        # « Copier » confirme sur place, et rien d'autre ne part.
        first = commands.first
        first.locator("[data-role='copy']").click()
        page.wait_for_function(
            "(el) => el.querySelector(\"[data-role='copy']\").dataset.copied", arg=first.element_handle(),
        )
        assert first.locator("[data-role='copy']").get_attribute("data-copied") == "ok"
        assert "Copié" in first.inner_text()
        assert page.evaluate("navigator.clipboard.readText()") == "grimoire init . -y --memory-profile lexical"
        assert "n’écrit rien" in page.locator(f"{GUIDE} [data-role='setup-readonly']").inner_text()

        # Aucune écriture : ni POST, ni PUT, pendant tout le parcours.
        assert _writes(seen) == [], _writes(seen)
        api = _api(seen)
        for route in ("/api/status", "/api/health", "/api/setup/run", "/api/memory/status", "/api/workspace/setup/guide"):
            assert any(route in url for url in api), (route, api)
        assert not [u for u in api if "/api/memory/status" in u and "probe" in u], "la mémoire est lue sans sonde"
        # Aucun bouton du guide n'installe.
        labels = page.locator(f"{GUIDE} button").all_inner_texts()
        assert not [t for t in labels if any(w in t.lower() for w in ("installer", "initialiser", "démarrer"))], labels
    finally:
        page.context.close()


# ── Critère 1 : l'état lu, avec sa source, sur des projets configurés ──────


def test_un_projet_configure_en_lexical_est_en_s2_et_garde_son_accueil(browser: Browser, cockpit: dict[str, str]) -> None:
    base = f"{cockpit['url']}/workspace/index.html?project={cockpit['configured']}"
    page, seen = _open(browser, base)
    try:
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        # Projet initialisé : À traiter reste l'accueil.
        page.wait_for_selector(".at-wrap", timeout=30_000)
        assert page.locator(GUIDE).count() == 0
        # Le guide reste joignable à tout moment.
        page.evaluate("() => window.GrimoireWorkspace.goto('a-traiter', { view: 'demarrer' })")
        state = page.wait_for_selector(f"{STATE}[data-state='S2']", timeout=30_000)
        text = state.inner_text()
        assert "/api/memory/status · configuredBackend = " in text
        assert "lexical seul" in text
        assert "#a-traiter?view=demarrer" in page.url
        commands = page.locator(f"{GUIDE} [data-role='setup-commands'] [data-command]")
        assert commands.first.get_attribute("data-command").startswith("grimoire memory up --profile ")
        assert _writes(seen) == []
    finally:
        page.context.close()


def test_un_passage_du_wizard_en_echec_est_en_s1(browser: Browser, cockpit: dict[str, str]) -> None:
    url = f"{cockpit['url']}/workspace/index.html?project={cockpit['failed']}#a-traiter?view=demarrer"
    page, seen = _open(browser, url)
    try:
        state = page.wait_for_selector(f"{STATE}[data-state='S1']", timeout=30_000)
        text = state.inner_text()
        assert "/api/setup/run · ok = false" in text
        assert "standard : profil governed introuvable" in text
        first = page.locator(f"{GUIDE} [data-role='setup-commands'] [data-command]").first
        assert first.get_attribute("data-command") == "grimoire doctor . --fix"
        assert _writes(seen) == []
    finally:
        page.context.close()


def test_le_classement_des_etats_ne_prend_jamais_une_lecture_ratee_pour_un_zero(browser: Browser, cockpit: dict[str, str]) -> None:
    """Le module partagé, sur des réponses construites : S0 à S3 et « inconnu »."""
    page, _seen = _open(browser, f"{cockpit['url']}/workspace/index.html")
    try:
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        result = page.evaluate("""async () => {
          const m = await import('./setup-state.js');
          const ok = (data) => ({ ok: true, data });
          const ko = (error) => ({ ok: false, error });
          const status = ok({ host: 'cockpit' });
          const init = ok({ kit: { scaffolded: true } });
          const noRun = ok({ available: false });
          const probed = (extra) => ok({ probed: true, probedAt: '2026-10-26T09:00:00+00:00', stale: false,
            state: 'ok', available: true, resolvedBackend: 'qdrant-local', configuredBackend: 'qdrant-local',
            retrieval: { in_service: 'hybrid' }, ...extra });
          const cases = {
            s0: { status, health: ok({ kit: { scaffolded: false } }), setupRun: ko('x'), memory: ko('x') },
            s1: { status, health: init, setupRun: ok({ available: true, ok: true, doctorOk: false }), memory: probed() },
            s2_down: { status, health: init, setupRun: noRun, memory: probed({ state: 'unavailable', available: false }) },
            s2_lexical: { status, health: init, setupRun: noRun, memory: probed({ retrieval: { in_service: 'lexical' } }) },
            s3: { status, health: init, setupRun: noRun, memory: probed() },
            health_down: { status, health: ko('HTTP 500'), setupRun: noRun, memory: probed() },
            status_down: { status: ko('API locale injoignable'), health: init, setupRun: noRun, memory: probed() },
            run_down: { status, health: init, setupRun: ko('HTTP 502'), memory: probed() },
            never_probed: { status, health: init, setupRun: noRun,
              memory: ok({ probed: false, state: 'uninitialized', configuredBackend: 'qdrant-local' }) },
          };
          return Object.fromEntries(Object.entries(cases).map(([k, v]) => {
            const s = m.classify(v);
            return [k, { id: s.id, source: s.source }];
          }));
        }""")
        assert result["s0"]["id"] == "S0"
        assert result["s1"] == {"id": "S1", "source": "/api/setup/run · doctorOk = false"}
        assert result["s2_down"]["id"] == "S2"
        assert result["s2_down"]["source"].startswith("/api/memory/status · state = unavailable")
        assert result["s2_lexical"]["id"] == "S2"
        assert result["s3"]["id"] == "S3"
        assert result["s3"]["source"].startswith("/api/memory/status · state = ok")
        assert result["health_down"] == {"id": "inconnu", "source": "/api/health : HTTP 500"}
        assert result["status_down"]["id"] == "inconnu"
        assert result["run_down"]["id"] == "inconnu", "un journal illisible ne vaut pas « aucun échec »"
        assert result["never_probed"]["id"] == "inconnu", "une mémoire jamais sondée ne vaut ni S2 ni S3"
        assert "jamais sondée" in result["never_probed"]["source"]
    finally:
        page.context.close()


# ── Critère 1 : la vitrine ──────────────────────────────────────────────────


def test_la_vitrine_servie_par_le_cockpit_dit_l_etat_et_sa_source(browser: Browser, cockpit: dict[str, str]) -> None:
    page, seen = _open(browser, f"{cockpit['url']}/index.html", scheme="light")
    try:
        banner = page.wait_for_selector("#etat-local[data-state='S0']:not([hidden])", timeout=30_000)
        text = banner.inner_text()
        assert "Ce dossier n'est pas encore un projet Grimoire." in text
        assert "lu dans /api/health · kit.scaffolded = false" in text
        link = page.locator("#etat-local a.btn")
        assert link.get_attribute("href") == "workspace/index.html#a-traiter?view=demarrer"
        api = _api(seen)
        for route in ("/api/status", "/api/health", "/api/setup/run", "/api/memory/status"):
            assert any(route in url for url in api), (route, api)
        assert not [u for u in api if "probe" in u]
        assert _writes(seen) == []
    finally:
        page.context.close()


def _serve_static(route: Route) -> None:
    """La vitrine publiée : web/ servi tel quel par un hôte qui n'est pas le cockpit."""
    rel = route.request.url.split("vitrine.example/", 1)[1].split("?", 1)[0].split("#", 1)[0] or "index.html"
    target = (WEB / rel).resolve()
    if WEB.resolve() not in target.parents or not target.is_file():
        route.fulfill(status=404, body="")
        return
    route.fulfill(path=str(target))


def _local_requests(seen: list[tuple[str, str]]) -> list[str]:
    return [u for _m, u in seen if any(h in u for h in ("127.0.0.1", "localhost", "[::1]")) or "/api/" in u]


def test_la_vitrine_publiee_ne_fait_aucune_requete_locale(browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.route("https://vitrine.example/**", _serve_static)
    page = context.new_page()
    seen: list[tuple[str, str]] = []
    page.on("request", lambda req: seen.append((req.method, req.url)))
    try:
        page.goto("https://vitrine.example/index.html", wait_until="load")
        page.wait_for_timeout(1500)
        assert page.locator("#etat-local").get_attribute("hidden") is not None
        assert _local_requests(seen) == [], _local_requests(seen)
        assert not [u for _m, u in seen if "setup-state.js" in u], "le module d'état ne se charge même pas"
    finally:
        context.close()


def test_la_vitrine_ouverte_depuis_le_disque_ne_fait_aucune_requete_locale(browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    seen: list[tuple[str, str]] = []
    page.on("request", lambda req: seen.append((req.method, req.url)))
    try:
        page.goto((WEB / "index.html").resolve().as_uri(), wait_until="load")
        page.wait_for_timeout(1500)
        assert page.locator("#etat-local").get_attribute("hidden") is not None
        assert _local_requests(seen) == [], _local_requests(seen)
    finally:
        context.close()


# ── Mise en page : 1024 px, rien ne déborde ────────────────────────────────


def test_a_1024_px_aucun_texte_du_guide_ni_des_barres_ne_deborde(browser: Browser, cockpit: dict[str, str]) -> None:
    page, _seen = _open(browser, f"{cockpit['url']}/workspace/index.html", scheme="light", width=1024)
    try:
        page.wait_for_selector(f"{STATE}[data-state='S0']", timeout=30_000)
        page.wait_for_selector(f"{GUIDE} [data-role='setup-commands']", timeout=30_000)
        overflow = page.evaluate("""() => {
          const bad = [];
          const canvas = document.getElementById('canvas').getBoundingClientRect();
          for (const el of document.querySelectorAll('[data-role="setup-guide"] *')) {
            if (!el.getClientRects().length || el.closest('svg')) continue;
            const r = el.getBoundingClientRect();
            if (r.right > canvas.right + 1) bad.push(`${el.tagName}.${el.className}: ${Math.round(r.right)} > ${Math.round(canvas.right)}`);
            if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible'
                && el.children.length === 0 && el.clientWidth > 0) bad.push(`${el.tagName}.${el.className} déborde`);
          }
          for (const id of ['appbar', 'statusbar']) {
            const bar = document.getElementById(id);
            for (const el of bar.querySelectorAll('*')) {
              if (!el.getClientRects().length || el.closest('svg')) continue;
              const r = el.getBoundingClientRect();
              if (r.right > window.innerWidth + 1) bad.push(`#${id} ${el.tagName}.${el.className}: ${Math.round(r.right)}`);
            }
          }
          return bad;
        }""")
        assert overflow == [], overflow
    finally:
        page.context.close()


# ── Critère 3 : captures de relecture ──────────────────────────────────────


def _workspace_shot(browser: Browser, url: str, wait: str, path: Path, scheme: str, scroll: str = "") -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate(f"() => document.querySelector(`#st-theme [data-theme-pref='{scheme}']`).click()")
        page.wait_for_selector(wait, timeout=30_000)
        if scroll:
            page.locator(scroll).scroll_into_view_if_needed()
            page.evaluate(f"() => document.querySelector(`{scroll}`).scrollIntoView({{ block: 'start' }})")
        page.wait_for_timeout(400)
        page.screenshot(path=str(path))
    finally:
        context.close()


def test_captures_de_relecture(browser: Browser, cockpit: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    url = cockpit["url"]
    for scheme, word in (("light", "clair"), ("dark", "sombre")):
        _workspace_shot(browser, f"{url}/workspace/index.html", f"{STATE}[data-state='S0']",
                        out / f"p36-guide-s0-{word}.png", scheme)
        _workspace_shot(browser, f"{url}/workspace/index.html", f"{GUIDE} [data-role='setup-plan']",
                        out / f"p36-guide-s0-profils-{word}.png", scheme, scroll=f"{GUIDE} [data-role='setup-profiles']")
        _workspace_shot(browser, f"{url}/workspace/index.html?project={cockpit['configured']}#a-traiter?view=demarrer",
                        f"{STATE}[data-state='S2']", out / f"p36-etat-s2-{word}.png", scheme)
        _workspace_shot(browser, f"{url}/workspace/index.html?project={cockpit['failed']}#a-traiter?view=demarrer",
                        f"{STATE}[data-state='S1']", out / f"p36-etat-s1-{word}.png", scheme)
        for name, page_url, wait in (
            ("vitrine-cockpit", f"{url}/index.html", "#etat-local:not([hidden])"),
            ("vitrine-publiee", "https://vitrine.example/index.html", "section.cmd5"),
        ):
            context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
            context.route("https://vitrine.example/**", _serve_static)
            page = context.new_page()
            try:
                page.goto(page_url, wait_until="load")
                page.wait_for_selector(wait, timeout=30_000)
                page.wait_for_timeout(400)
                page.screenshot(path=str(out / f"p36-{name}-{word}.png"))
            finally:
                context.close()
    assert len(list(out.glob("p36-*.png"))) >= 10
    _capture_s3(browser, cockpit, out)


def _capture_s3(browser: Browser, cockpit: dict[str, str], out: Path) -> None:
    """S3 demande une mémoire sondée qui fusionne : Qdrant embarqué et fastembed.

    La sonde part du test (``?probe=1``), jamais de la page : la page lit ensuite
    le dernier statut sondé, sans sonder. Sans fastembed sur la machine, le projet
    reste en S2 et la capture S3 n'est pas produite (elle n'est jamais simulée).
    """
    root = Path(cockpit["base"]) / "projet-pret"
    if not root.exists():
        _init(root, "projet-pret", backend="qdrant-local", profile="standard")
        env = {**os.environ, "GRIMOIRE_COCKPIT_HOME": cockpit["home"], "NO_COLOR": "1"}
        subprocess.run([sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
                       env=env, capture_output=True, check=False, timeout=60)
    registry = json.loads((Path(cockpit["home"]) / "registry.json").read_text(encoding="utf-8"))
    slug = next(e["slug"] for e in registry if Path(e["path"]).name == "projet-pret")
    probe_url = f"{cockpit['url']}/api/memory/status?probe=1&project={slug}"
    with urllib.request.urlopen(probe_url, timeout=120) as resp:  # noqa: S310 - cockpit local du test
        probed = json.loads(resp.read())
    if probed.get("state") != "ok":
        return
    for scheme, word in (("light", "clair"), ("dark", "sombre")):
        _workspace_shot(browser, f"{cockpit['url']}/workspace/index.html?project={slug}#a-traiter?view=demarrer",
                        f"{STATE}[data-state='S3'], {STATE}[data-state='S2']", out / f"p36-etat-s3-{word}.png", scheme)
