"""Le board d'Exécuter suit le graphe réel du ledger (P07, revue UX M1).

Trois défauts, prouvés sur un projet dédié où les trois états existent pour
de vrai (posés par la CLI, même `TaskService` que l'interface) :

- la suite d'une carte en cours était « → Bloquée » (colonne suivante de
  `LIFECYCLE`) ; le graphe du ledger dit `running → needs_verification`, et
  bloquer est un geste à part qui demande une raison ;
- la colonne « Fait » regroupait `archived`, donc les tâches annulées ;
- une carte bloquée ne disait ni le mot ni la raison du blocage.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

_GATES_YAML = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack"]
profile_strictness:
  governed: hard_fail
"""

_BLOCK_REASON = "attend la clé d'API du fournisseur"


def _free_port() -> int:
    import socket

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
    msg = f"`grimoire serve` n'a pas répondu sur :{port}"
    raise TimeoutError(msg)


def _grimoire(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=180,
    )


def _add(root: Path, title: str) -> str:
    added = _grimoire(root, "task", "add", title, "-a", "un critère observable", "--owner", "winston")
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    return match.group(1)


def _steps(root: Path, task_id: str, *steps: list[str]) -> None:
    for step in steps:
        outcome = _grimoire(root, *step)
        if outcome.returncode != 0:
            pytest.skip(f"préparation de {task_id} interrompue à {step} : {outcome.stderr[-400:]}")


@pytest.fixture(scope="session")
def served_board_moves(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Un projet dédié : une tâche en cours, une bloquée avec sa raison, une annulée."""
    root = tmp_path_factory.mktemp("board-moves") / "projet-board"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    _grimoire(root, "init", ".", "-y", "--name", "projet-board")
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    _grimoire(root, "standard", "init", "--profile", "governed")
    # Le ledger d'init (tâche `bootstrap`) est retiré : le board ne porte que
    # les trois tâches posées ci-dessous (même raison que `served_review_gate`).
    for name in ("events.jsonl", ".events.jsonl.lock"):
        (root / "_grimoire-runtime-output" / "ledger" / name).unlink(missing_ok=True)
    gates_path = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates_path.parent.mkdir(parents=True, exist_ok=True)
    gates_path.write_text(_GATES_YAML, encoding="utf-8")

    running = _add(root, "Tâche en cours")
    _steps(root, running, ["task", "move", running, "--to", "ready"], ["task", "claim", running],
           ["task", "move", running, "--to", "running"])
    blocked = _add(root, "Tâche bloquée")
    _steps(root, blocked, ["task", "move", blocked, "--to", "ready"],
           ["task", "block", blocked, "--reason", _BLOCK_REASON])
    cancelled = _add(root, "Tâche annulée")
    _steps(root, cancelled, ["task", "cancel", cancelled, "--reason", "doublon"])

    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-board-moves"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
def board_page(browser: Browser, served_board_moves: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served_board_moves}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('executer')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    # `:visible` : depuis P72v2, la carte bloquée vit dans une section repliée et
    # peut être le premier `.ex-card` du DOM — `wait_for_selector` ne regarde que lui.
    page.wait_for_selector(".ex-card:visible")
    try:
        yield page
    finally:
        context.close()


def _card(page: Page, title: str):
    return page.locator(".ex-card", has_text=title).first


def test_une_carte_en_cours_propose_revue_et_bloquer_jamais_bloquee_comme_suite(board_page: Page) -> None:
    card = _card(board_page, "Tâche en cours")
    moves = card.locator("[data-role='next-moves']").inner_text()
    assert "→ Revue" in moves
    assert "Bloquer…" in moves
    assert "→ Bloquée" not in board_page.locator(".ex-board").inner_text()

    # « Bloquer… » ouvre la tâche, le curseur dans le champ de raison.
    card.get_by_role("button", name="Bloquer…").click()
    reason = board_page.locator("#inspector-body [data-role='block-reason']")
    reason.wait_for(timeout=15_000)
    board_page.wait_for_function("() => document.activeElement?.dataset?.role === 'block-reason'")
    assert "→ Bloquée" not in board_page.locator("#inspector-body").inner_text()


def test_les_taches_annulees_ne_sont_plus_dans_fait(board_page: Page) -> None:
    done = board_page.locator(".ex-col[data-group='done']")
    assert "Tâche annulée" not in done.inner_text()
    cancelled = board_page.locator(".ex-col[data-group='cancelled']")
    assert cancelled.locator(".ex-col-body").is_hidden(), "la colonne des annulées est repliée par défaut"
    cancelled.get_by_role("button", name="Afficher").click()
    board_page.locator(".ex-col[data-group='cancelled'] .ex-card", has_text="Tâche annulée").wait_for()
    # Quatre colonnes de travail, plus celle des annulées.
    assert board_page.locator(".ex-col:not(.ex-col-folded)").count() == 4


def test_une_carte_bloquee_affiche_le_mot_et_la_raison(board_page: Page) -> None:
    # Depuis P72v2, la bloquée vit dans la section repliée : on la déplie d'abord.
    board_page.locator("[data-role='blocked-count']").click()
    line = _card(board_page, "Tâche bloquée").locator("[data-role='blocked']")
    line.wait_for()
    text = line.inner_text()
    assert "Bloquée" in text
    assert _BLOCK_REASON in text
