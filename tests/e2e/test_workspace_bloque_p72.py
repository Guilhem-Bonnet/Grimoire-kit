"""Travail › bloqué visible sans bruit (P72v2, GAO-p72v2-bloqu-001).

Décision de concertation : pas de 5e colonne sur le tableau compact — sous la
colonne « En cours », un compteur discret de ton warn (« N bloquée(s) ») et une
section « Bloqué » repliée par défaut, chaque carte y portant sa raison de
blocage en clair. Le board 8 colonnes garde sa colonne « Bloquée » inchangée.

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

RUNNING_TITLE = "Carte en cours, jamais bloquée"
BLOCKED_ARCH_TITLE = "Refonte du schéma des portes"
BLOCKED_DEP_TITLE = "Brancher la vue sur la nouvelle API"
ARCH_REASON = "attend la décision d'architecture de Guilhem"
DEP_REASON = "dépendance ouverte vers la maquette du design"


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
def p72_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("bloque-p72") / "projet-bloque-p72"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-bloque-p72", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P72 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    now = datetime.now(UTC)
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)

    # En cours : une session vivante, jamais bloquée.
    running = service.add(RUNNING_TITLE, ("la vue tourne",), owner="guilhem", actor="t").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(running, state, actor_id="kit-dev", claim=claim)
    sid = "41c89d23-db0e-4d2a-9999-000000000072"
    service.attach_session(running, sid, session_host="claude-code-cli", actor="kit-dev")
    (runs / f"session-{sid}.json").write_text(
        json.dumps({
            "schema_version": 1, "session_id": sid,
            "started_at": (now - timedelta(minutes=40)).isoformat(),
            "updated_at": (now - timedelta(minutes=2)).isoformat(),
            "task_id": running, "host": "host-claude-code-cli", "rules": {},
        }),
        encoding="utf-8",
    )

    # Bloquées : la raison vit dans l'événement de transition (``--reason``),
    # relue par ``block_reasons`` — c'est elle que la carte doit écrire.
    arch = service.add(BLOCKED_ARCH_TITLE, ("schéma validé",), owner="guilhem", actor="t", ready=True).task.id
    service.ledger.transition_task(arch, TaskState.CLAIMED, actor_id="kit-dev", claim=TaskClaim("kit-dev", "host-claude-code-cli"))
    service.ledger.transition_task(arch, TaskState.BLOCKED, actor_id="kit-dev", reason=ARCH_REASON)
    dep = service.add(BLOCKED_DEP_TITLE, ("vue branchée",), owner="kit-dev", actor="t", ready=True).task.id
    service.ledger.transition_task(dep, TaskState.BLOCKED, actor_id="kit-dev", reason=DEP_REASON)
    return root


@pytest.fixture(scope="module")
def p72_url(p72_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p72"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(p72_project), "--port", str(port), "--no-open"],
        cwd=str(p72_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
def page(browser: Browser, p72_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p72_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    opened.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    opened.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    # La section « Bloqué » (repliée) précède le flux dans le DOM : viser une carte visible.
    opened.wait_for_selector(".ex-col-body .ex-card", timeout=APPEAR_MS)
    try:
        yield opened
    finally:
        context.close()


def _counter(page: Page):  # type: ignore[no-untyped-def]
    return page.locator(".ex-col[data-group='doing'] [data-role='blocked-count']")


def _section(page: Page):  # type: ignore[no-untyped-def]
    return page.locator(".ex-col[data-group='doing'] [data-role='blocked-section']")


def test_le_compteur_dit_le_bon_nombre_sous_en_cours_et_porte_le_ton_warn(page: Page) -> None:
    counter = _counter(page)
    expect(counter).to_have_count(1)
    expect(counter).to_be_visible()
    assert "2 bloquées" in counter.inner_text()
    # Sous charge, la feuille de style arrive après le premier rendu, et un re-rendu peut
    # remplacer le nœud : lire le ton et le token dans le même tour, sur un nœud attaché.
    tones = page.wait_for_function(
        "() => { const n = document.querySelector(\"[data-role='blocked-count']\");"
        " if (!n || !n.isConnected) return null;"
        " const cs = getComputedStyle(n); const root = getComputedStyle(document.documentElement);"
        " const t = [cs.color, root.getPropertyValue('--warn').trim(), cs.borderTopColor];"
        " return t[0] && t[1] && t[2] ? t : null; }"
    ).json_value()
    assert tones[0] and tones[1], tones
    # La couleur calculée du compteur est celle du token --warn résolu.
    resolved = page.evaluate(
        "(v) => { const d = document.createElement('div'); d.style.color = v; document.body.append(d);"
        " const c = getComputedStyle(d).color; d.remove(); return c; }",
        tones[1],
    )
    assert tones[0] == resolved, (tones, resolved)
    # Et les cartes en cours ne sont pas polluées : la bloquée a quitté le flux.
    doing = page.locator(".ex-col[data-group='doing'] .ex-col-body .ex-card")
    assert doing.count() == 1 and RUNNING_TITLE in doing.first.inner_text()


def test_la_section_bloque_est_repliee_par_defaut_et_se_deplie_au_clic(page: Page) -> None:
    counter, section = _counter(page), _section(page)
    expect(section).to_have_count(1)
    expect(section).to_be_hidden()
    assert counter.get_attribute("aria-expanded") == "false"
    assert counter.get_attribute("aria-controls") == section.get_attribute("id")
    counter.click()
    counter, section = _counter(page), _section(page)  # le board est re-rendu au clic
    expect(section).to_be_visible()
    assert counter.get_attribute("aria-expanded") == "true"
    assert "Bloqué" in section.locator(".ex-blocked-title").inner_text()
    # Chaque carte de la section écrit sa raison, en clair — jamais un code.
    cards = section.locator(".ex-card")
    expect(cards).to_have_count(2)
    arch = cards.filter(has_text=BLOCKED_ARCH_TITLE).first
    line = arch.locator("[data-role='blocked']")
    expect(line).to_be_visible()
    assert ARCH_REASON in line.inner_text()
    dep = cards.filter(has_text=BLOCKED_DEP_TITLE).first
    assert DEP_REASON in dep.locator("[data-role='blocked']").inner_text()
    # Replier au second clic : aria-expanded et visibilité suivent.
    _counter(page).click()
    expect(_section(page)).to_be_hidden()
    assert _counter(page).get_attribute("aria-expanded") == "false"


def test_le_board_8_colonnes_garde_sa_colonne_bloquee_sans_compteur(page: Page) -> None:
    page.get_by_role("button", name="Huit colonnes").click()
    col = page.locator(".ex-col[data-group='blocked']")
    expect(col).to_have_count(1)
    cards = col.locator(".ex-card")
    expect(cards).to_have_count(2)
    assert page.locator("[data-role='blocked-count']").count() == 0
    assert page.locator("[data-role='blocked-section']").count() == 0


def test_sans_tache_bloquee_ni_compteur_ni_section(page: Page, p72_project: Path) -> None:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(p72_project)
    blocked = [t.id for t in service.ledger.list_tasks() if t.status is TaskState.BLOCKED]
    assert len(blocked) == 2
    try:
        for tid in blocked:
            service.ledger.transition_task(tid, TaskState.READY, actor_id="kit-dev")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
        page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
        page.wait_for_selector(".ex-col-body .ex-card", timeout=APPEAR_MS)
        assert page.locator("[data-role='blocked-count']").count() == 0
        assert page.locator("[data-role='blocked-section']").count() == 0
    finally:
        # L'état du module est restauré (même raison relue : le dernier passage
        # en ``blocked`` de chaque tâche garde son motif d'origine).
        for tid, reason in zip(blocked, (ARCH_REASON, DEP_REASON), strict=True):
            service.ledger.transition_task(tid, TaskState.BLOCKED, actor_id="kit-dev", reason=reason)
