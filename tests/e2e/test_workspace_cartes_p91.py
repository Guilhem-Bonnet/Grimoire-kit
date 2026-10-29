"""Travail › une ligne d'état par carte et le couloir « À reprendre » (P91, GAO-p91-cartes-001).

Avant : une carte tenue disait « session vivante · aucun signe de vie lu · durée inconnue »
et « modèle non rapporté · effort non rapporté · coût inconnu » — quatre lignes, trois
contradictions. Maintenant : UNE ligne (glyphe + mot + âge) lue du champ ``presence`` (P90),
et les tâches rendues par un bail perdu forment un groupe en tête de « À faire ».

Chaque test relit le DOM rendu par ``grimoire serve`` sur un projet réel.
"""

from __future__ import annotations

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
from playwright.sync_api import Browser, Page, expect

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""


APPEAR_MS = 10_000
EVIDENCE = Path(os.environ.get("GRIMOIRE_P91_SHOTS", "")) if os.environ.get("GRIMOIRE_P91_SHOTS") else None

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

REMOVED = (
    "aucun signe de vie lu",
    "durée inconnue",
    "modèle non rapporté",
    "effort non rapporté",
    "coût inconnu",
    "session vivante",
)
TITLES = {
    "working": "Exporter le journal du cockpit",
    "silent": "Relire la charte des cartes",
    "resume": "Reprendre la migration du ledger",
    "blocked": "Publier la version bêta",
    "none": "Documenter le couloir de reprise",
}
WORDS = {
    "working": "Au travail",
    "silent": "Silencieuse",
    "resume": "À reprendre",
    "blocked": "Bloquée",
    "none": "Sans agent",
}


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
def p91_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.lease import LeaseFile, lease_path, write_lease
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("cartes-p91") / "projet-cartes-p91"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-cartes-p91", "--backend", "local"],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P91 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    now = datetime.now(UTC)

    def held(title: str, renewed_ago_s: int, session: str) -> str:
        tid = service.add(title, ("fait",), owner="guilhem", actor="t", ready=True).task.id
        service.claim(tid, "kit-dev", host="host-claude-code-cli")
        service.attach_session(tid, session, session_host="claude-code-cli", actor="kit-dev")
        task = service.ledger.get_task(tid)
        assert task is not None and task.claim is not None
        write_lease(
            lease_path(service.ledger._root, tid),
            LeaseFile(
                tid, task.claim.lease_epoch, (now - timedelta(seconds=renewed_ago_s)).isoformat(), ttl_seconds=1200
            ),
        )
        return tid

    held(TITLES["working"], 40, "aaaaaaaa-0000-4000-8000-00000000000a")
    held(TITLES["silent"], 720, "bbbbbbbb-0000-4000-8000-00000000000b")
    held(TITLES["resume"], 3600, "cccccccc-0000-4000-8000-00000000000c")
    service.ledger.reap(fenced_hosts=frozenset({"host-claude-code-cli"}), now=datetime.now(UTC) + timedelta(seconds=5))
    blocked = service.add(TITLES["blocked"], ("fait",), owner="guilhem", actor="t", ready=True).task.id
    service.ledger.transition_task(blocked, TaskState.BLOCKED, reason="attend la CI")
    service.add(TITLES["none"], ("fait",), owner="guilhem", actor="t", ready=True)
    return root


@pytest.fixture(scope="module")
def p91_url(p91_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p91"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "grimoire",
            "serve",
            "--project-root",
            str(p91_project),
            "--port",
            str(port),
            "--no-open",
        ],
        cwd=str(p91_project),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def _open(browser: Browser, url: str, *, scheme: str = "light", width: int = 1440) -> tuple[object, Page]:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce", color_scheme=scheme
    )  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card", timeout=APPEAR_MS)
    return context, page


@pytest.fixture
def page(browser: Browser, p91_url: str) -> Iterator[Page]:
    context, opened = _open(browser, p91_url)
    try:
        yield opened  # type: ignore[misc]
    finally:
        context.close()  # type: ignore[attr-defined]


def _card(page: Page, state: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=TITLES[state]).first


@pytest.mark.parametrize("state", list(TITLES))
def test_une_carte_de_chaque_etat_montre_une_seule_ligne_d_etat_sans_les_mentions_retirees(
    page: Page, state: str
) -> None:
    if state == "blocked":  # replié par défaut sous « En cours » (P72v2)
        page.locator("[data-role='blocked-count']").click()
    card = _card(page, state)
    expect(card).to_be_visible()
    text = card.inner_text()
    assert WORDS[state] in text, text
    for gone in REMOVED:
        assert gone not in text, f"{gone!r} ne doit plus être sur la carte : {text}"
    if state == "blocked":
        # « Bloquée » est portée par sa ligne d'attente : pas de ligne de présence en plus.
        assert card.locator("[data-role='presence']").count() == 0
        assert card.locator("[data-role='blocked']").count() == 1
    else:
        line = card.locator("[data-role='presence']")
        expect(line).to_have_count(1)
        assert line.get_attribute("data-state") == state
        assert line.locator("svg").count() >= 1, "la forme (glyphe) accompagne le mot"
    # Une seule mention d'état de vie : jamais « Au travail » et « Silencieuse » ensemble.
    assert sum(1 for w in WORDS.values() if w in text) == 1, text


def test_la_ligne_dit_l_age_et_la_tentative(page: Page) -> None:
    working = _card(page, "working").locator("[data-role='presence']").inner_text()
    assert re.search(r"Au travail\s*·\s*il y a \d+ s", working), working
    silent = _card(page, "silent").locator("[data-role='presence']").inner_text()
    assert re.search(r"Silencieuse\s*·\s*rien depuis \d+ min", silent), silent
    assert "reprise auto dans" in silent
    resume = _card(page, "resume").locator("[data-role='presence']").inner_text()
    assert "tentative 1 sur 3" in resume, resume


def test_une_tache_rendue_apres_un_bail_perdu_est_dans_le_groupe_a_reprendre_en_tete_de_a_faire(page: Page) -> None:
    todo = page.locator(".ex-col[data-group='todo']")
    group = todo.locator("[data-role='resume-group']")
    expect(group).to_have_count(1)
    assert "à reprendre · 1" in group.inner_text().lower()
    resumed = group.locator(".ex-card").filter(has_text=TITLES["resume"])
    expect(resumed).to_have_count(1)
    # En tête : le groupe précède toutes les autres cartes de la colonne.
    first = todo.locator(".ex-col-body > *").first
    assert first.get_attribute("data-role") == "resume-group"
    assert todo.locator("[data-role='resume-count']").inner_text() == "1 à reprendre"
    # Pas de 5e colonne.
    assert page.locator(".ex-col[data-group='resume']").count() == 0
    expect(resumed.locator("[data-role='resume-now']")).to_have_text("Reprendre maintenant")
    # Les tâches neuves restent hors du groupe.
    assert group.locator(".ex-card").filter(has_text=TITLES["none"]).count() == 0
    assert todo.locator(".ex-card").filter(has_text=TITLES["none"]).count() == 1


def test_le_filtre_des_cinq_etats_somme_les_cartes_ouvertes(page: Page) -> None:
    bar = page.locator("[data-role='presence-filter']")
    chips = bar.locator("button.chip")
    expect(chips).to_have_count(5)
    total = sum(int(chips.nth(i).locator(".mono").inner_text()) for i in range(5))
    assert total == 5
    if bar.locator("button[data-state='resume']").get_attribute("aria-pressed") == "true":  # pragma: no cover
        pytest.fail("le filtre doit démarrer vide")
    bar.locator("button[data-state='resume']").click()
    page.wait_for_function("() => document.querySelectorAll('.ex-card').length === 1")
    assert TITLES["resume"] in page.locator(".ex-card").first.inner_text()
    page.locator("[data-role='presence-filter'] button[data-state='resume']").click()
    page.wait_for_function("() => document.querySelectorAll('.ex-card').length === 5")


@pytest.mark.parametrize(("scheme", "width"), [("light", 1440), ("dark", 1440), ("light", 390)])
def test_captures_clair_sombre_et_390(browser: Browser, p91_url: str, scheme: str, width: int) -> None:
    context, shot = _open(browser, p91_url, scheme=scheme, width=width)
    try:
        if width <= 390:
            overflow = shot.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1, f"défilement horizontal de {overflow}px à {width}px"
        if EVIDENCE is not None:
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            shot.screenshot(path=str(EVIDENCE / f"cartes-p91-{scheme}-{width}.png"), full_page=True)
    finally:
        context.close()  # type: ignore[attr-defined]
