"""UI-3 du cockpit : un pilote vivant — actualisation sans rechargement, compteur de
décisions, vivacité, dernier signe de vie, durée et coût de chaque carte.

Constat sur la base : aucune actualisation automatique (ni sondage ni flux), les
décisions en attente n'étaient visibles que dans la vue qui les liste, et une carte
de tâche ne portait ni vivacité, ni durée, ni coût. Ce qui se prouve ici ne se prouve
pas par la lecture du code : il faut un vrai serveur, une vraie écriture du ledger
faite HORS du navigateur, et la page qui ne bouge pas.

Un serveur dédié déclare un intervalle d'une seconde (``GRIMOIRE_PULSE_SECONDS=1``)
pour que les attentes restent courtes ; le contrat vaut pour n'importe quelle valeur
déclarée (`interval_seconds`, lu par la coque).

Captures (preuve d'interface) : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les écrit.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

# Recopiés de `tests/e2e/conftest.py` plutôt qu'importés : sans `tests/__init__.py`, un
# `from tests.e2e.conftest import …` réexécuterait le conftest sous un second nom de module.


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists() if sys.platform == "linux" else True


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


INTERVAL_S = 1
#: Large marge : l'intervalle déclaré est de 1 s, mais le sondage, la relecture et le dessin ne sont pas instantanés.
APPEAR_MS = 8_000
PHONE = {"width": 390, "height": 844}


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


_BADGE_IS_API_COUNT = """async () => {
  const b = document.getElementById('decisions-count');
  const api = (await (await fetch('/api/workspace/attention?scope=portfolio')).json()).count;
  return (b.hidden ? 0 : Number(b.textContent)) === api;
}"""


def _badge_is_api_count(page: Page) -> None:
    """Le badge dit le `count` de l'API d'attention (réponses sans réponse humaine comprises)."""
    page.wait_for_function(_BADGE_IS_API_COUNT, timeout=APPEAR_MS)


def _badge(page: Page) -> int:
    """Le compteur de l'onglet « À traiter » : 0 quand il est caché."""
    return page.evaluate(
        "() => { const b = document.getElementById('decisions-count'); return b.hidden ? 0 : Number(b.textContent); }"
    )


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


@pytest.fixture(scope="module")
def live_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Un projet réel avec un Mission Ledger déjà ouvert (sans lui, l'espace affiche l'état « aucun ledger »)."""
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("live-pilot") / "projet-vivant"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-vivant", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    TaskService(root).add("Carte de départ du pilote vivant", ("critère",), actor="t", owner="winston", ready=True)
    return root


@pytest.fixture(scope="module")
def live_url(live_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-live"))
    env["GRIMOIRE_PULSE_SECONDS"] = str(INTERVAL_S)
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(live_project), "--port", str(port), "--no-open"],
        cwd=str(live_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def service(live_project: Path):  # type: ignore[no-untyped-def]
    from grimoire.missions.service import TaskService

    return TaskService(live_project)


def _open(
    browser: Browser, url: str, *, viewport: dict[str, int] | None = None, poll: bool = False
) -> tuple[Page, object]:
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 900}, reduced_motion="reduce")
    if poll:
        # Navigateur sans `EventSource` : la coque se replie sur le sondage (P10).
        context.add_init_script("delete window.EventSource;")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    return page, context


@pytest.fixture
def page(browser: Browser, live_url: str) -> Iterator[Page]:
    opened, context = _open(browser, live_url)
    try:
        yield opened
    finally:
        context.close()  # type: ignore[attr-defined]


@pytest.fixture
def poll_page(browser: Browser, live_url: str) -> Iterator[Page]:
    """La coque en sondage : le contrat du repli, quand le flux (P10) n'est pas disponible."""
    opened, context = _open(browser, live_url, poll=True)
    try:
        yield opened
    finally:
        context.close()  # type: ignore[attr-defined]


@pytest.fixture
def phone(browser: Browser, live_url: str) -> Iterator[Page]:
    opened, context = _open(browser, live_url, viewport=PHONE)
    try:
        yield opened
    finally:
        context.close()  # type: ignore[attr-defined]


def _running(service, title: str) -> str:  # type: ignore[no-untyped-def]
    from grimoire.missions.schemas import TaskState

    tid = service.add(title, ("critère",), actor="t", owner="winston", ready=True).task.id
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "agent-a")
    return tid


def _ask(service, tid: str, text: str) -> str:  # type: ignore[no-untyped-def]
    service.ask(tid, text, "agent-a", kind="merge", options=("fusionner", "attendre"))
    return str(service.require(tid).waiting.question.id)


def _answer_all(service) -> None:  # type: ignore[no-untyped-def]
    from grimoire.missions.decisions import pending_decisions
    from grimoire.missions.schemas import ORIGIN_HUMAN

    for decision in pending_decisions(service.ledger):
        service.answer(decision.task_id, "attendre", "guilhem", origin=ORIGIN_HUMAN, question_id=decision.question.id)


def _journal(root: Path, session_id: str, task_id: str, *, started: datetime, updated: datetime) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"session-{session_id}.json").write_text(
        json.dumps({
            "schema_version": 1, "session_id": session_id, "started_at": started.isoformat(),
            "updated_at": updated.isoformat(), "task_id": task_id, "rules": {},
        }),
        encoding="utf-8",
    )


def _card(page: Page, title: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title).first


def _api_requests(page: Page) -> list[str]:
    seen: list[str] = []
    page.on("request", lambda request: seen.append(request.url) if "/api/" in request.url else None)
    return seen


_HIDE = """() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  document.dispatchEvent(new Event('visibilitychange'));
}"""
_SHOW = """() => {
  delete document.visibilityState;
  delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
}"""


# ── Actualisation sans rechargement ─────────────────────────────────────────


def test_un_changement_de_ledger_apparait_sans_rechargement_dans_l_intervalle_declare(
    page: Page, service
) -> None:  # type: ignore[no-untyped-def]
    """Une carte créée HORS du navigateur paraît, la page n'ayant ni rechargé ni navigué.

    Défaut d'origine : ni `setInterval` ni `EventSource` dans `web/workspace` — le
    ledger changeait, la page restait telle quelle jusqu'au rechargement à la main.
    """
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.evaluate("() => { window.__marqueur = 'jamais rechargee'; }")
    navigations = page.evaluate("() => performance.getEntriesByType('navigation').length")
    assert page.evaluate("() => window.GrimoireWorkspace.live.state.intervalMs") == INTERVAL_S * 1000, (
        "l'intervalle est celui que le serveur déclare, pas une constante de l'interface"
    )
    title = "Carte née pendant que la page restait ouverte"
    assert page.locator(".ex-card").filter(has_text=title).count() == 0

    service.add(title, ("critère",), actor="t", owner="winston", ready=True)

    _card(page, title).wait_for(timeout=APPEAR_MS)
    assert page.evaluate("() => window.__marqueur") == "jamais rechargee", "la page a été rechargée"
    assert page.evaluate("() => performance.getEntriesByType('navigation').length") == navigations


def test_sans_changement_le_sondage_ne_redessine_rien(poll_page: Page) -> None:
    """Le pouls tourne mais, ledger immobile, le DOM de l'espace n'est pas touché (« sans charge si rien ne change »)."""
    page = poll_page
    assert page.evaluate("() => window.GrimoireWorkspace.live.state.transport") == "poll"
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1500)  # laisser passer le sondage de référence
    requests = _api_requests(page)
    page.evaluate("() => { document.querySelector('.ex-card').dataset.sentinelle = 'intacte'; }")

    page.wait_for_timeout(3500)

    pulses = [u for u in requests if "/api/workspace/pulse" in u]
    others = [u for u in requests if "/api/workspace/" in u and "/pulse" not in u and "/glossary" not in u]
    assert len(pulses) >= 2, "le sondage tourne à l'intervalle déclaré"
    assert not others, f"rien n'a bougé, rien n'est relu : {others}"
    assert page.evaluate("() => document.querySelector('.ex-card').dataset.sentinelle") == "intacte"


def test_un_onglet_cache_ne_fait_aucune_requete_puis_reprend(poll_page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Onglet masqué ⇒ zéro requête ; revenu visible ⇒ un sondage part tout de suite et rattrape ce qui a changé."""
    page = poll_page
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1500)
    page.evaluate(_HIDE)
    page.wait_for_timeout(300)  # une requête déjà en vol peut se terminer
    requests = _api_requests(page)
    title = "Carte née pendant que l'onglet était masqué"

    service.add(title, ("critère",), actor="t", owner="winston", ready=True)
    page.wait_for_timeout(int(INTERVAL_S * 1000 * 3.5))

    assert requests == [], f"requêtes émises onglet masqué : {requests}"
    assert page.locator(".ex-card").filter(has_text=title).count() == 0
    assert "suspendue" in page.locator("#st-live-text").inner_text()

    page.evaluate(_SHOW)
    _card(page, title).wait_for(timeout=APPEAR_MS)
    assert any("/api/workspace/pulse" in u for u in requests), "le sondage reprend dès le retour"


def test_une_rafale_de_changements_est_regroupee_en_rechargements_espaces(poll_page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """P79 : huit workers font bouger l'empreinte plusieurs fois par seconde. Une rafale ne relance pas un rechargement
    par pouls : au plus un toutes les 3 s (``MIN_RELOAD_MS``), jamais deux en même temps, et le dernier changement
    s'affiche quand même."""
    page = poll_page
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1500)
    started: list[float] = []
    urls: list[str] = []
    page.on("request", lambda r: (started.append(time.monotonic()), urls.append(r.url.split("?")[0]))
            if "/api/workspace/" in r.url and "/pulse" not in r.url and "/glossary" not in r.url else None)
    titles = [f"Rafale de changements {i}" for i in range(6)]
    for title in titles:
        service.add(title, ("critère",), actor="t", owner="winston", ready=True)
        page.wait_for_timeout(500)

    _card(page, titles[-1]).wait_for(timeout=APPEAR_MS)

    # Un rechargement peut porter plusieurs requêtes (tâches, sessions…) au même instant : on compte les vagues.
    waves = [s for i, s in enumerate(started) if i == 0 or s - started[i - 1] > 0.5]
    assert waves, "la rafale a bien fait recharger l'espace"
    gaps = [b - a for a, b in itertools.pairwise(waves)]
    assert all(gap >= 2.5 for gap in gaps), f"rechargements trop rapprochés : {gaps} {urls}"
    assert len(waves) <= 3, f"une rafale de 3 s en six écritures donne au plus 3 vagues : {len(waves)}"


def test_quinze_secondes_d_onglet_masque_sans_requete_puis_le_changement_est_recharge(poll_page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """P79, critère 3 : masqué 15 s pendant qu'une écriture arrive, l'onglet ne fait aucune requête ; revenu visible,
    le changement retenu est rechargé aussitôt (son empreinte est déjà connue, aucun pouls ne la redira)."""
    page = poll_page
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1500)
    page.evaluate(_HIDE)
    page.wait_for_timeout(300)  # une requête déjà en vol peut se terminer
    requests = _api_requests(page)
    title = "Carte écrite pendant quinze secondes d'onglet masqué"

    service.add(title, ("critère",), actor="t", owner="winston", ready=True)
    page.wait_for_timeout(15_000)

    assert requests == [], f"requêtes émises onglet masqué : {requests}"
    page.evaluate(_SHOW)
    _card(page, title).wait_for(timeout=APPEAR_MS)


_COUNT_INFLIGHT = """() => {
  const real = window.fetch;
  window.__inflight = {};
  window.__maxInflight = {};
  window.fetch = async (...args) => {
    const raw = args[0] && args[0].url ? args[0].url : String(args[0]);
    const route = new URL(raw, location.href).pathname;
    window.__inflight[route] = (window.__inflight[route] || 0) + 1;
    window.__maxInflight[route] = Math.max(window.__maxInflight[route] || 0, window.__inflight[route]);
    try { return await real(...args); } finally { window.__inflight[route] -= 1; }
  };
}"""


def test_jamais_deux_requetes_en_vol_vers_la_meme_route(poll_page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """P79, critère 4 : pendant une rafale d'écritures, aucune route n'a deux requêtes en vol depuis le même onglet."""
    page = poll_page
    _goto(page, "executer")
    page.wait_for_selector(".ex-card")
    page.wait_for_timeout(1500)
    page.evaluate(_COUNT_INFLIGHT)
    titles = [f"Rafale sans chevauchement {i}" for i in range(6)]
    for title in titles:
        service.add(title, ("critère",), actor="t", owner="winston", ready=True)
        page.wait_for_timeout(400)

    _card(page, titles[-1]).wait_for(timeout=APPEAR_MS)
    page.wait_for_timeout(1000)
    peaks = page.evaluate("() => window.__maxInflight")
    assert peaks, "la rafale a fait relire des routes"
    assert all(n <= 1 for n in peaks.values()), f"requêtes simultanées vers une même route : {peaks}"


def test_l_horodatage_dit_depuis_quand_les_donnees_sont_a_jour(poll_page: Page) -> None:
    """« mis à jour il y a Xs » avance seul, entre deux sondages, et nomme l'intervalle déclaré."""
    page = poll_page
    text = page.locator("#st-live-text")
    page.wait_for_function("() => /mis à jour il y a \\d+ s/.test(document.getElementById('st-live-text').textContent)")
    assert "Sondage toutes les 1 s" in page.locator("#st-live").get_attribute("title")  # type: ignore[operator]
    assert re.fullmatch(r"mis à jour il y a \d+ s", text.inner_text())


def test_une_reponse_en_cours_de_saisie_n_est_pas_ecrasee_par_l_actualisation(page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Défaut empêché : un rafraîchissement qui vide la zone de réponse pendant que Guilhem écrit."""
    _answer_all(service)
    tid = _running(service, "Carte dont la réponse est en cours de saisie")
    _ask(service, tid, "Fusionner la PR en cours de saisie ?")
    _goto(page, "executer")
    page.locator("#view-seg button", has_text="Décisions").click()
    card = page.locator(".ex-decision").filter(has_text="en cours de saisie")
    # P68 : une question à réponses proposées replie la zone libre derrière « Autre réponse… ».
    card.locator("[data-role='answer-other']").click()
    area = card.locator("textarea")
    area.wait_for(timeout=APPEAR_MS)
    area.fill("ma réponse à moitié écrite")
    title = "Carte arrivée pendant la saisie"

    service.add(title, ("critère",), actor="t", owner="winston", ready=True)
    page.wait_for_timeout(int(INTERVAL_S * 1000 * 3.5))

    assert area.input_value() == "ma réponse à moitié écrite", "la saisie a été perdue"
    page.locator("#brand").click()  # sort du champ : l'actualisation différée repart
    page.locator("#view-seg button", has_text="Tableau").click()
    _card(page, title).wait_for(timeout=APPEAR_MS)
    _answer_all(service)


# ── Compteur de décisions ───────────────────────────────────────────────────


def test_le_compteur_de_decisions_est_exact_visible_partout_et_cliquable(page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Le nombre de la barre du haut est celui de la vue Décisions, sans rechargement, depuis n'importe quel espace.

    Défaut d'origine : les décisions en attente (K6) n'étaient visibles que dans la vue
    Décisions d'Exécuter — qui n'ouvre l'œil sur rien tant qu'on n'y est pas.
    """
    _answer_all(service)
    _goto(page, "memoire")  # un espace qui n'a rien à voir avec les décisions
    pill = page.locator("#decisions-count")  # le compteur est un badge de l'onglet « À traiter » (P63)
    tab = page.locator("#tab-space-a-traiter")
    page.wait_for_function("() => document.getElementById('decisions-count') !== null")
    base = _badge(page)  # le badge compte tout ce qui attend dans À traiter, pas les seules questions

    first = _running(service, "Première carte qui attend un humain")
    second = _running(service, "Seconde carte qui attend un humain")
    _ask(service, first, "Fusionner la première PR ?")
    qid = _ask(service, second, "Fusionner la seconde PR ?")

    page.wait_for_function(f"() => Number(document.getElementById('decisions-count').textContent) >= {base + 2}", timeout=APPEAR_MS)
    _badge_is_api_count(page)
    assert pill.is_visible()
    assert tab.get_attribute("aria-label").startswith("À traiter — ")
    assert tab.locator("#decisions-count").count() == 1, "le compteur est porté par l'onglet"
    assert page.locator("#decisions-pill").count() == 0, "plus de pastille séparée"
    _capture(page, "decisions-compteur-memoire")

    tab.click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_selector(".at-row[data-kind='question']")
    assert page.locator(".at-row[data-kind='question']").count() == 2, "l'espace dit les mêmes décisions que le compteur"
    page.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'decisions' })")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-decision")
    assert page.locator(".ex-decision").count() == 2, "le compteur et la vue disent la même chose"
    assert page.locator("#view-seg [aria-pressed='true']").filter(has_text="Décisions").count() == 1
    _capture(page, "decisions-vue")

    from grimoire.missions.schemas import ORIGIN_HUMAN

    service.answer(second, "attendre", "guilhem", origin=ORIGIN_HUMAN, question_id=qid)
    _badge_is_api_count(page)
    page.wait_for_function("() => document.querySelectorAll('.ex-decision').length === 1", timeout=APPEAR_MS)
    assert tab.get_attribute("aria-label").startswith("À traiter — ")

    _answer_all(service)
    _badge_is_api_count(page)


# ── Vivacité, dernier signe, durée, coût ────────────────────────────────────


def test_une_carte_dit_sa_vivacite_son_dernier_signe_sa_duree_et_son_cout(
    page: Page, service, live_project: Path
) -> None:  # type: ignore[no-untyped-def]
    """Vivacité K5, dernier signe de vie, durée (premier au dernier signe) et coût par carte, en portée projet."""
    title = "Carte portée par une session vivante"
    tid = _running(service, title)
    now = datetime.now(UTC)
    _journal(live_project, "S-vivante-ui3", tid, started=now - timedelta(minutes=12), updated=now - timedelta(minutes=2))
    service.attach_session(tid, "S-vivante-ui3", session_host="claude-code-cli", actor="agent-a")
    _goto(page, "executer")

    card = _card(page, title)
    card.wait_for(timeout=APPEAR_MS)
    facts = card.locator("[data-role='presence']")
    facts.wait_for()
    text = facts.inner_text()
    # P91 : la carte ne porte plus qu'UNE ligne d'état ; signe et durée vivent dans l'inspecteur.
    assert "Au travail" in text, text
    assert card.locator("[data-role='live-facts']").count() == 0 and "aucun signe" not in card.inner_text()
    assert "durée inconnue" not in card.inner_text()
    # (durée : du premier au dernier signe de vie, lue ci-dessous dans l'inspecteur : 12 − 2 min)
    _capture(page, "carte-vivacite")

    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='last-sign']").wait_for()
    inside = inspector.inner_text()
    assert "durée : 10 min" in inside
    assert "coût de la session : inconnu (non mesuré par le kit)" in inside


def test_un_cout_jamais_mesure_s_affiche_inconnu_et_jamais_zero(page: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Les trois états du coût jusque dans la carte : inconnu, borne basse « ≥ », exact — et jamais « 0 »."""
    silent = _running(service, "Carte sans aucun dispatch")
    partial = _running(service, "Carte dont un appel manque de prix")
    exact = _running(service, "Carte dont tout est chiffré")
    ledger = service.ledger
    ledger.append_event("task.dispatched", partial, "task", "agent-a", {"task_id": partial, "cost_usd": 0.4, "verdict": "green"})
    ledger.append_event("task.dispatched", partial, "task", "agent-a", {"task_id": partial, "cost_usd": None, "verdict": "green"})
    ledger.append_event("task.dispatched", exact, "task", "agent-a", {"task_id": exact, "cost_usd": 0.25, "verdict": "green"})
    _goto(page, "executer")
    _card(page, "Carte sans aucun dispatch").wait_for(timeout=APPEAR_MS)

    # P91 : un coût inconnu ne s'écrit plus sur la carte (la liste et l'inspecteur le disent).
    assert _card(page, "Carte sans aucun dispatch").locator("[data-role='cost']").count() == 0
    low = _card(page, "Carte dont un appel manque de prix").locator("[data-role='cost']").inner_text()
    assert low.startswith("coût ≥ 0.4000 USD") and "1 non chiffré" in low, low
    assert _card(page, "Carte dont tout est chiffré").locator("[data-role='cost']").inner_text() == "coût 0.2500 USD (dispatch)"
    assert "0.0000" not in page.locator("#canvas").inner_text(), "un coût inconnu ne s'écrit jamais 0"
    assert silent and partial and exact
    _capture(page, "cartes-cout")

    page.locator("#view-seg button", has_text="Liste").click()
    page.wait_for_selector(".ex-list")
    row = page.locator("tr[data-task]").filter(has_text="Carte sans aucun dispatch")
    assert "inconnu" in row.inner_text()


# ── Petit écran ─────────────────────────────────────────────────────────────


def test_a_390_px_le_compteur_et_l_horodatage_ne_font_rien_deborder(phone: Page, service) -> None:  # type: ignore[no-untyped-def]
    """Le compteur ajoute un contrôle à une barre déjà serrée : à 390 px elle ne déborde pas, le compteur reste cliquable."""
    _answer_all(service)
    phone.wait_for_function("() => document.getElementById('decisions-count') !== null")
    tid = _running(service, "Carte à décider sur petit écran")
    _ask(service, tid, "Fusionner depuis le téléphone ?")
    phone.wait_for_function("() => !document.getElementById('decisions-count').hidden", timeout=APPEAR_MS)
    _badge_is_api_count(phone)

    measured = phone.evaluate(
        """() => {
          const iw = window.innerWidth;
          const out = [];
          for (const el of document.querySelectorAll('#appbar *, #statusbar *')) {
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            if (!box.width || style.display === 'none') continue;
            let scrolls = false;
            for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
              const ox = getComputedStyle(n).overflowX;
              if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') scrolls = true;
            }
            if (box.right > iw + 1 && !scrolls) out.push((el.id || el.className.baseVal || el.className || el.tagName) + ':' + Math.round(box.right));
          }
          const pill = document.getElementById('decisions-count').getBoundingClientRect();
          return { out, pillRight: pill.right, pillWidth: pill.width, iw };
        }"""
    )
    assert not measured["out"], measured
    assert measured["pillWidth"] > 0 and measured["pillRight"] <= measured["iw"], measured
    _capture(phone, "mobile-compteur")
    phone.locator("#tab-space-a-traiter").click()
    phone.wait_for_selector(".at-row[data-kind='question']")
    phone.locator(".at-row[data-kind='question'] .at-act").first.click()
    phone.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    phone.wait_for_selector(".ex-decision")
    _capture(phone, "mobile-decisions")
    _answer_all(service)


# ── Correctifs de revue sceptique (UI3-S1-01, UI3-S1-02) ────────────────────


def _serve(root: Path, home: Path) -> tuple[subprocess.Popen[bytes], str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(home)
    env["GRIMOIRE_PULSE_SECONDS"] = str(INTERVAL_S)
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _wait_ready(port, time.monotonic() + 60)
    return process, f"http://127.0.0.1:{port}"


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def test_le_premier_ledger_ouvert_hors_du_navigateur_apparait_sans_rechargement(
    browser: Browser, tmp_path: Path
) -> None:
    """UI3-S1-01 : projet SANS Mission Ledger au montage ; la première carte, créée ailleurs, doit paraître seule.

    Défaut d'origine : `board` était lu une fois au montage et `draw()` sortait sur « aucun ledger »
    avant tout appel réseau — le pouls tournait pour rien, il fallait recharger la page.
    """
    from grimoire.missions.service import TaskService

    root = tmp_path / "sans-ledger"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "sans-ledger", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    process, url = _serve(root, tmp_path / "cockpit-home")
    try:
        opened, context = _open(browser, url)
        try:
            _goto(opened, "executer")
            opened.wait_for_function("() => document.querySelector('.ex-wrap, #space-root, body').innerText.includes('Mission Ledger')")
            assert opened.locator(".ex-card").count() == 0
            opened.evaluate("() => { window.__marqueur = 'jamais rechargee'; }")
            title = "Première carte née hors du navigateur"

            TaskService(root).add(title, ("critère",), actor="t", owner="winston", ready=True)

            _card(opened, title).wait_for(timeout=APPEAR_MS)
            assert opened.evaluate("() => window.__marqueur") == "jamais rechargee", "la page a été rechargée"
        finally:
            context.close()  # type: ignore[attr-defined]
    finally:
        _stop(process)


def test_une_raison_d_annulation_saisie_survit_au_battement_d_une_session(
    page: Page, service, live_project: Path
) -> None:  # type: ignore[no-untyped-def]
    """UI3-S1-02 : le champ `cancel-reason` est un `<input>` sans attribut `type` ; sa saisie doit protéger l'inspecteur.

    Défaut d'origine : `editing()` ne cherchait que `input[type="text"]` — un input sans attribut n'y
    correspond pas — et la signature de la carte change à chaque battement (`updated_at` de la session) :
    l'inspecteur était reconstruit, la raison saisie perdue et le focus rendu au corps de la page.
    """
    title = "Carte dont la raison d'annulation est en cours de saisie"
    tid = _running(service, title)
    beat_at = datetime.now(UTC)
    _journal(live_project, "S-saisie-ui3", tid, started=beat_at - timedelta(minutes=5), updated=beat_at)
    service.attach_session(tid, "S-saisie-ui3", session_host="claude-code-cli", actor="agent-a")
    _goto(page, "executer")
    _card(page, title).wait_for(timeout=APPEAR_MS)
    _card(page, title).dispatch_event("click")
    # « Annuler » ne vit que dans le menu « ⋯ » de l'inspecteur (P11).
    page.locator("#inspector-body [data-role='more']").click(timeout=APPEAR_MS)
    reason = page.locator("#inspector-body [data-role='cancel-reason']")
    reason.wait_for(timeout=APPEAR_MS)
    page.wait_for_timeout(int(INTERVAL_S * 1000 * 1.5))
    reason.fill("raison saisie par l'humain")
    page.locator("#inspector-body [data-role='cancel-submit']").focus()

    page.wait_for_timeout(1200)
    now = datetime.now(UTC)
    _journal(live_project, "S-saisie-ui3", tid, started=now - timedelta(minutes=5), updated=now)
    page.wait_for_timeout(int(INTERVAL_S * 1000 * 4.5))

    assert page.locator("#inspector-body [data-role='cancel-reason']").input_value() == "raison saisie par l'humain", (
        "la raison saisie a été perdue au battement de la session"
    )
    assert page.evaluate("() => document.activeElement.dataset.role") == "cancel-submit", "le focus a été volé"
