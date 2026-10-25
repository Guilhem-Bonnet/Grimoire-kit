"""Travail — la vue « Sessions » (P33), prouvée dans un navigateur.

Un cockpit dédié sert un projet réel (``grimoire init`` + standard gouverné) où
une tâche est réclamée par une session Claude Code dont le journal s'est tu il y
a trois heures, avec une consigne humaine pas encore livrée ; une seconde
session, sans claim, vient d'écrire son journal. Le harnais ouvre Travail →
Sessions et vérifie ce que l'artboard promet : les quatre tuiles, une ligne par
session (état, hôte, identifiant, projet et tâche tenue, signe), puis le panneau
de détail (claims, dernier signe, consignes en attente) avec « Copier » la
commande de reprise, confirmé sur place et vérifié dans le presse-papiers. En
portée « Tous les projets », « Envoyer une consigne » écrit dans le ledger du
projet, refus et succès rendus sur place.

Aucune assertion ne dépend de la position d'une ligne : chaque ligne se cible
par son ``data-session``.
"""

from __future__ import annotations

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

from .conftest import open_scope_all

SILENT = "1038a1c8-bdee-49a6-b5e5-52b2329bcabf"
FRESH = "7102fd4c-6416-4361-8a73-660d0a08b3cc"
TITLE = "Suppressions PowerShell (#761)"


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
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


def _journal(root: Path, session_id: str, *, updated_at: datetime, task_id: str = "") -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"session-{session_id}.json").write_text(
        json.dumps({
            "schema_version": 1, "session_id": session_id, "task_id": task_id, "rules": {},
            "started_at": (updated_at - timedelta(minutes=20)).isoformat(), "updated_at": updated_at.isoformat(),
        }),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def served_sessions(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import ORIGIN_HUMAN
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("sessions-p33") / "projet-sessions"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", root.name, "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    added = subprocess.run(
        [sys.executable, "-m", "grimoire", "task", "add", TITLE, "-a", "un critère observable", "--owner", "winston"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=180,
    )
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    task_id = match.group(1)
    ledger = MissionLedger(root / "_grimoire-runtime-output" / "ledger")
    if ledger.get_task(task_id).status.value != "ready":  # type: ignore[union-attr]
        subprocess.run(
            [sys.executable, "-m", "grimoire", "task", "move", task_id, "--to", "ready"],
            cwd=str(root), check=False, capture_output=True, timeout=180,
        )
    ledger.claim_task(task_id, "claude", "host-claude-code-cli")
    ledger.attach_session(task_id, SILENT, session_host="host-claude-code-cli")
    TaskService(root).comment(task_id, "reprends par la suppression des alias", "guilhem", "directive", origin=ORIGIN_HUMAN)
    now = datetime.now(UTC)
    _journal(root, SILENT, updated_at=now - timedelta(hours=3), task_id=task_id)
    _journal(root, FRESH, updated_at=now - timedelta(minutes=1))

    port = _free_port()
    home = tmp_path_factory.mktemp("cockpit-home-sessions")
    env = {**os.environ, "GRIMOIRE_COCKPIT_HOME": str(home), "GRIMOIRE_NO_COCKPIT": "1", "NO_COLOR": "1"}
    subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60,
    )
    registry_path = home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slug = next((str(e.get("slug", "")) for e in registry if e.get("path") == str(root)), "")
    if not slug:
        pytest.skip("slug introuvable au registre du cockpit après `add`")
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "root": root, "slug": slug, "task": task_id}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def sessions_page(browser: Browser, served_sessions: dict[str, object]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.goto(
        f"{served_sessions['url']}/workspace/index.html?project={served_sessions['slug']}#travail?view=sessions",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _open_sessions(page: Page) -> None:
    page.wait_for_selector("[data-role='sessions'] tr[data-session]", timeout=30_000)


def test_la_vue_sessions_liste_projet_hote_tache_signe_et_consignes_avec_copier_la_reprise(
    sessions_page: Page, served_sessions: dict[str, object]
) -> None:
    page = sessions_page
    _open_sessions(page)

    # Le segment de vues porte « Sessions » et son compte ; il est pressé.
    seg = page.locator("#view-seg button[data-value='sessions']")
    assert seg.get_attribute("aria-pressed") == "true"
    assert re.fullmatch(r"Sessions\s*\d+", seg.inner_text().strip()), seg.inner_text()

    # Les quatre tuiles de l'artboard, avec des chiffres lus.
    tiles = page.locator("[data-role='sessions'] .kpi-item")
    labels = [tiles.nth(i).locator(".kpi-lbl").inner_text().strip() for i in range(tiles.count())]
    assert labels == ["Actives", "Attendent ta réponse", "Silencieuses avec claim", "Terminées · 24 h"]
    assert int(tiles.nth(2).locator(".kpi-val").inner_text()) >= 1, "la session muette tient un claim"

    silent = page.locator(f"tr[data-session='{SILENT}']")
    text = silent.inner_text()
    assert "silencieuse" in text and "Claude Code" in text and SILENT[:8] in text
    assert TITLE in text and "projet-sessions" in text, "tâche tenue et projet"
    assert "il y a 3 h" in text, "le dernier signe, en temps relatif"
    assert "active" in page.locator(f"tr[data-session='{FRESH}']").inner_text()

    silent.dispatch_event("click")
    inspector = page.locator("#inspector-body [data-role='session-inspector']")
    inspector.wait_for()
    assert silent.get_attribute("aria-current") == "true", "la ligne choisie est marquée"
    body = inspector.inner_text()
    assert "Claude Code · projet-sessions" in body
    assert served_sessions["task"] in body and TITLE in body, "Claims"
    assert "il y a 3 h · journal" in inspector.locator("[data-role='last-sign']").inner_text()
    assert inspector.locator("[data-role='directives-pending']").inner_text() == "1 en attente"
    assert "sans signe de vie depuis 3 h" in body
    for absent in ("Relancer la session", "Libérer les claims"):
        assert inspector.get_by_text(absent).count() == 0, f"{absent} : aucune route, jamais dessiné"

    command = inspector.locator("[data-role='resume-command']")
    assert command.inner_text().endswith(f"claude --resume {SILENT}")
    copy = inspector.locator("[data-role='copy-resume']")
    copy.click()
    inspector.get_by_text("Commande copiée").wait_for()
    assert "Copié" in copy.inner_text()
    assert page.evaluate("() => navigator.clipboard.readText()") == f"claude --resume {SILENT}"
    assert f"session={SILENT}" in page.url, "la sélection entre dans l'URL"


def test_envoyer_une_consigne_depuis_la_vue_sessions_ecrit_dans_le_ledger(
    sessions_page: Page, served_sessions: dict[str, object]
) -> None:
    from grimoire.missions.service import TaskService

    page = sessions_page
    open_scope_all(page, f"#travail?view=sessions&session={SILENT}")
    inspector = page.locator("#inspector-body [data-role='session-inspector']")
    inspector.wait_for(timeout=30_000)
    inspector.locator("[data-role='send-directive']").click()
    inspector.locator("[data-role='directive-submit']").click()
    inspector.get_by_text("Une consigne vide").wait_for()
    inspector.locator("[data-role='directive-text']").fill("termine les alias puis rends la main")
    inspector.locator("[data-role='directive-submit']").click()
    inspector.get_by_text(f"Consigne posée sur {served_sessions['task']}").wait_for()

    task = TaskService(served_sessions["root"]).require(served_sessions["task"])  # type: ignore[arg-type]
    assert any(d.text == "termine les alias puis rends la main" and d.kind == "directive" for d in task.directives)
    page.locator(f"tr[data-session='{SILENT}']").wait_for()
    page.wait_for_function(
        "() => document.querySelector(\"[data-role='directives-pending']\")?.textContent === '2 en attente'",
        timeout=15_000,
    )
