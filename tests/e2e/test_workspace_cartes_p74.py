"""Travail › cartes lisibles par un humain (P74, GAO-p74-cartes-k-001).

Ce que la capture d'avant montrait, critère par critère :

- une carte de la colonne « Fait » disait « session vivante » — le serveur pose
  ``task.session`` (claim, ou journal qui nomme la tâche) sans condition d'état,
  et la carte l'affichait tel quel ;
- « consigne non lue » : du jargon de ledger sur un tableau lu par un humain ;
- l'UUID de session (même tronqué à 8 caractères) sur la carte ;
- « R52 — », « [P47] », « P68 — » en tête des titres affichés ;
- un owner nu (« guilhem ») sans dire ce qu'il est (qui a ouvert, pas qui
  travaille), ni s'il est humain ou agent ;
- « depuis N h » sans dire de quoi ; aucun type de tâche lisible ; aucun
  repère de balayage par porteur.

Chaque test relit le DOM rendu par ``grimoire serve`` sur un projet réel.
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

DONE_TITLE = "P68 — Décisions lisibles : une carte par type"
RUNNING_TITLE = "[P47] Cartes lisibles pour un humain"
DOC_TITLE = "Documenter la charte des cartes du kanban"
DESIGN_TITLE = "Maquette de l'écran Travail : densité Douce"
DONE_SESSION = "77bf861f-aaaa-bbbb-cccc-000000000002"
RUNNING_SESSION = "41c89d23-db0e-4d2a-8888-000000000001"


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
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


@pytest.fixture(scope="module")
def p74_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import ORIGIN_HUMAN, TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("cartes-p74") / "projet-cartes-p74"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-cartes-p74", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P74 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    now = datetime.now(UTC)
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)

    def journal(sid: str, task_id: str, minutes: int) -> None:
        (runs / f"session-{sid}.json").write_text(
            json.dumps({
                "schema_version": 1, "session_id": sid,
                "started_at": (now - timedelta(minutes=minutes + 30)).isoformat(),
                "updated_at": (now - timedelta(minutes=minutes)).isoformat(),
                "task_id": task_id, "host": "host-claude-code-cli", "rules": {},
            }),
            encoding="utf-8",
        )

    def run_states(tid: str, *states: TaskState) -> None:
        # Mise en place seulement (comme test_workspace_decisions_p68) : les états
        # sont posés par le ledger, l'objet du test est la carte rendue.
        for state in states:
            claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
            service.ledger.transition_task(tid, state, actor_id="kit-dev", claim=claim)

    # À faire : un doc (heuristique sur le titre) et un design, deux porteurs.
    service.add(DOC_TITLE, ("la doc existe",), owner="kit-dev", actor="t", ready=True)
    service.add(DESIGN_TITLE, ("maquette validée",), owner="guilhem", actor="t", ready=True)

    # En cours : session vivante, consigne humaine en attente, préfixe [P47].
    running = service.add(RUNNING_TITLE, ("cartes relues",), owner="guilhem", actor="t").task.id
    run_states(running, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING)
    service.attach_session(running, RUNNING_SESSION, session_host="claude-code-cli", actor="kit-dev")
    journal(RUNNING_SESSION, running, 2)
    service.comment(running, "Garde la densité de la maquette Douce.", "guilhem", kind="directive", origin=ORIGIN_HUMAN)

    # Fait : tâche CLOSE dont la session garde un journal frais — le cas qui
    # affichait « session vivante » en colonne « Fait » avant P74.
    done = service.add(DONE_TITLE, ("vue livrée",), owner="claude", actor="t").task.id
    run_states(done, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION)
    service.attach_session(done, DONE_SESSION, session_host="claude-code-cli", actor="kit-dev")
    journal(DONE_SESSION, done, 1)
    service.ledger.transition_task(done, TaskState.CLOSED, actor_id="kit-dev")
    return root


@pytest.fixture(scope="module")
def p74_url(p74_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p74"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(p74_project), "--port", str(port), "--no-open"],
        cwd=str(p74_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


@pytest.fixture
def page(browser: Browser, p74_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p74_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    opened.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    opened.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    opened.wait_for_selector(".ex-card", timeout=APPEAR_MS)
    try:
        yield opened
    finally:
        context.close()


def _card(page: Page, title_fragment: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title_fragment).first


def test_une_tache_close_n_affiche_plus_de_vivacite_mais_l_inspecteur_dit_sa_session(page: Page) -> None:
    """Cause prouvée : `task.session` est posé par le serveur pour toute carte
    (claim, ou journal récent qui nomme la tâche), état compris « closed » —
    la carte l'affichait sans condition. Ici le journal de la session est
    frais (< 30 min, donc « vivante ») et la tâche est close : plus rien."""
    done = _card(page, "Décisions lisibles")
    done_text = done.inner_text()
    # « modèle de session » (badge de route) n'est pas une vivacité : on vise
    # le mot d'état et les deux lignes qui le portaient.
    assert "vivante" not in done_text, done_text
    assert done.locator("[data-role='live-facts']").count() == 0
    assert done.locator(".ex-session").count() == 0
    assert done.locator("[data-role='card-session']").count() == 0
    # La colonne est bien « Fait » (closed → accepted).
    assert done.evaluate("(n) => n.closest('.ex-col').dataset.group") == "done"
    # L'inspecteur, lui, parle encore de la session.
    done.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='session-block']").wait_for(timeout=APPEAR_MS)
    assert DONE_SESSION[:8] in inspector.inner_text()


def test_le_badge_de_consigne_parle_francais_et_s_explique(page: Page) -> None:
    card = _card(page, "Cartes lisibles")
    badge = card.locator("[data-role='directive-badge']")
    expect(badge).to_have_text("message en attente de la session (1)")
    assert "consigne non lue" not in card.inner_text()
    assert "pas encore livrée à la session" in (badge.get_attribute("title") or "")
    # L'inspecteur reformule aussi le statut de la consigne.
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    directive = inspector.locator(".ex-directive.unread").first
    directive.wait_for(timeout=APPEAR_MS)
    assert "en attente de la session" in directive.inner_text()
    assert "non lue" not in directive.inner_text()


def test_la_carte_ne_montre_plus_d_uuid_mais_hote_persona_et_palier(page: Page) -> None:
    card = _card(page, "Cartes lisibles")
    text = card.inner_text()
    assert RUNNING_SESSION[:8] not in text, "l'identifiant brut ne dit rien à un humain"
    line = card.locator("[data-role='card-session']")
    expect(line).to_have_count(1)
    assert "Claude Code" in line.inner_text(), "l'hôte, lui, parle"
    assert "kit-dev" in line.inner_text(), "la persona du claim parle"
    # L'inspecteur garde l'identifiant complet et la commande de reprise.
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='session-block']").wait_for(timeout=APPEAR_MS)
    assert RUNNING_SESSION in inspector.inner_text()


def test_les_prefixes_internes_sortent_des_titres_mais_restent_en_metadonnee(page: Page) -> None:
    card = _card(page, "Cartes lisibles")
    expect(card.locator(".ex-card-title")).to_have_text("Cartes lisibles pour un humain")
    assert "[P47]" in (card.locator(".ex-card-title").get_attribute("title") or ""), "le titre entier reste au survol"
    done = _card(page, "Décisions lisibles")
    expect(done.locator(".ex-card-title")).to_have_text("Décisions lisibles : une carte par type")
    # Inspecteur : titre nettoyé, identifiant interne en métadonnée discrète.
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.locator(".ex-insp-head h3").wait_for(timeout=APPEAR_MS)
    assert inspector.locator(".ex-insp-head h3").inner_text() == "Cartes lisibles pour un humain"
    expect(inspector.locator("[data-role='title-ref']")).to_have_text("P47")


def test_l_owner_dit_qui_a_ouvert_et_distingue_humain_et_agent(page: Page) -> None:
    human = _card(page, DESIGN_TITLE).locator("[data-role='owner']")
    assert human.inner_text() == "ouverte par guilhem"
    assert human.get_attribute("data-owner-kind") == "humain"
    assert "qui a ouvert la tâche, pas qui travaille dessus" in (human.get_attribute("title") or "")
    assert "icons.svg#user" in (human.locator("use").get_attribute("href") or "")
    agent = _card(page, DOC_TITLE).locator("[data-role='owner']")
    assert agent.inner_text() == "ouverte par kit-dev"
    assert agent.get_attribute("data-owner-kind") == "agent"
    assert "icons.svg#agent" in (agent.locator("use").get_attribute("href") or "")


def test_les_deux_temps_sont_nommes(page: Page) -> None:
    card = _card(page, "Cartes lisibles")
    age = card.locator("[data-role='card-age']")
    expect(age).to_have_count(1)
    assert age.inner_text().startswith("dans En cours depuis "), age.inner_text()
    title = age.get_attribute("title") or ""
    assert "dernière transition" in title and "âge total" in title
    # L'inspecteur écrit les deux, nommés.
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    ages = inspector.locator("[data-role='task-ages']")
    ages.wait_for(timeout=APPEAR_MS)
    assert "dans En cours depuis" in ages.inner_text() and "ouverte depuis" in ages.inner_text()


def test_le_type_est_un_badge_couleur_avec_le_mot_ecrit(page: Page) -> None:
    doc = _card(page, DOC_TITLE).locator("[data-role='type-badge']")
    expect(doc).to_have_text("doc")
    assert "type déduit du contenu" in (doc.get_attribute("title") or "")
    design = _card(page, DESIGN_TITLE).locator("[data-role='type-badge']")
    expect(design).to_have_text("design")
    code = _card(page, "Cartes lisibles").locator("[data-role='type-badge']")
    expect(code).to_have_text("code")
    # La pastille prend sa couleur d'un token (var(--type-…)), jamais d'un littéral.
    assert "var(--type-doc)" in (doc.locator(".ex-type-sw").get_attribute("style") or "")
    # Et la couleur n'est jamais seule : le mot est écrit dans le badge.
    assert doc.inner_text().strip() == "doc"


def test_une_pastille_stable_par_porteur_aide_a_balayer_le_tableau(page: Page) -> None:
    by_guilhem = [_card(page, DESIGN_TITLE), _card(page, "Cartes lisibles")]
    by_agent = _card(page, DOC_TITLE)
    tones = [c.evaluate("(n) => n.style.getPropertyValue('--owner-tone')") for c in by_guilhem]
    agent_tone = by_agent.evaluate("(n) => n.style.getPropertyValue('--owner-tone')")
    assert tones[0] and tones[0].startswith("var(--owner-"), tones
    assert tones[0] == tones[1], "le même porteur garde la même teinte"
    assert agent_tone and agent_tone != tones[0], "deux porteurs, deux teintes"
    # Le liseré est bien rendu (la couleur vient du token, pas d'un littéral).
    left = by_guilhem[0].evaluate("(n) => getComputedStyle(n).borderLeftWidth")
    assert left == "3px"
