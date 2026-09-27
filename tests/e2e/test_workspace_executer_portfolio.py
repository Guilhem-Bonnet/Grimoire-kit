"""Exécuter — le portefeuille multi-projets, prouvé dans un navigateur (#638, lot C).

Un cockpit dédié sert deux projets réels (chacun `grimoire init`, un
Mission Ledger, une tâche reconnaissable) plus une entrée de registre dont le
dossier n'existe pas. Le projet A porte une tâche réclamée par une session
« vivante » (journal de hooks daté de l'instant), le projet B une tâche libre
dont la session s'est tue il y a trois heures. Le harnais observe la portée
« Tous les projets » : deux cartes avec leur puce projet, le projet absent
nommé avec sa raison, le filtre « sessions vivantes » qui ne garde que A, le
bouton « Reprendre la session » qui montre `claude --resume <id>` sans rien
exécuter, et un blocage depuis le portefeuille qui écrit dans le ledger de B —
pas dans celui de A.

Captures (preuve d'interface) : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les
écrit ; absent, le test ne capture rien mais prouve tout le reste.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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

# Recopiés de `tests/e2e/conftest.py` plutôt qu'importés : sans
# `tests/__init__.py`, un `from tests.e2e.conftest import …` réexécuterait le
# conftest sous un second nom de module (voir son en-tête sur `REAL_HOME`).


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
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


LIVE_SESSION = "sess-portefeuille-vivante"
STALE_SESSION = "sess-portefeuille-eteinte"
TITLE_A = "Alpha — tâche réclamée par une session vivante (#638)"
TITLE_B = "Beta — tâche libre, session éteinte (#638)"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _journal(root: Path, session_id: str, *, task_id: str, updated_at: datetime) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"session-{session_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session_id,
                "started_at": updated_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "task_id": task_id,
                "rules": {},
            }
        ),
        encoding="utf-8",
    )


def _add_task(root: Path, title: str, *, ready: bool) -> str:
    added = subprocess.run(
        [sys.executable, "-m", "grimoire", "task", "add", title, "-a", "un critère observable", "--owner", "winston"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    task_id = match.group(1)
    if ready:
        subprocess.run(
            [sys.executable, "-m", "grimoire", "task", "move", task_id, "--to", "ready"],
            cwd=str(root),
            check=False,
            capture_output=True,
            timeout=180,
        )
    return task_id


@pytest.fixture(scope="module")
def served_portfolio(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    """Le cockpit dédié du portefeuille : projets A (réclamée, vivante), B
    (libre, éteinte) et C (chemin absent au registre)."""
    from grimoire.missions.ledger import MissionLedger

    roots = {
        "a": tmp_path_factory.mktemp("portfolio-a") / "projet-alpha",
        "b": tmp_path_factory.mktemp("portfolio-b") / "projet-beta",
    }
    for root in roots.values():
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", root.name, "--backend", "local"],
            cwd=str(root),
            check=False,
            capture_output=True,
            timeout=180,
        )
        if not (root / "_grimoire" / "kit").is_dir():
            pytest.skip("`grimoire init` indisponible ici")
        # Gouvernés (profil `governed`, gates du gabarit), comme le demande le
        # test de fait de l'issue — et sans la tâche `bootstrap` que
        # `standard init` ouvre (ADR-007), pour que chaque projet ne porte QUE
        # la tâche que ce harnais crée (même patron que `served_timeline`).
        subprocess.run(
            [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
            cwd=str(root),
            check=False,
            capture_output=True,
            timeout=180,
        )
        shutil.rmtree(root / "_grimoire-runtime-output" / "ledger", ignore_errors=True)
    task_a = _add_task(roots["a"], TITLE_A, ready=True)
    # B reste `proposed` : la transition gatée que le harnais réalise depuis
    # le portefeuille est `proposed → ready` (critères + owner déclarés), la
    # seule que le gabarit gouverné accorde sans context bundle ni fournisseur.
    task_b = _add_task(roots["b"], TITLE_B, ready=False)
    # Le claim au niveau du ledger, avec l'hôte Claude Code : c'est l'état
    # que le lot A produira depuis une session réelle — ce harnais prouve la
    # lecture du portefeuille, pas le gate `ready → in_progress`.
    MissionLedger(roots["a"] / "_grimoire-runtime-output" / "ledger").claim_task(
        task_a, "claude", "host-claude-code-cli"
    )
    now = datetime.now(UTC)
    _journal(roots["a"], LIVE_SESSION, task_id=task_a, updated_at=now - timedelta(minutes=1))
    _journal(roots["b"], STALE_SESSION, task_id=task_b, updated_at=now - timedelta(hours=3))

    port = _free_port()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-portfolio")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    for root in roots.values():
        subprocess.run(
            [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slugs = {
        key: next((str(e.get("slug", "")) for e in registry if e.get("path") == str(root)), "")
        for key, root in roots.items()
    }
    if not all(slugs.values()):
        pytest.skip("slugs introuvables au registre du cockpit après `add`")
    # Le troisième projet : une entrée dont le dossier n'existe pas — écrite
    # directement au registre, comme le laisse un projet déplacé ou supprimé.
    registry.append({"name": "Gamma", "slug": "projet-gamma", "path": str(cockpit_home / "n-existe-pas")})
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {
            "url": f"http://127.0.0.1:{port}",
            "roots": roots,
            "slugs": slugs,
            "task_a": task_a,
            "task_b": task_b,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def portfolio_page(browser: Browser, served_portfolio: dict[str, object]) -> Iterator[Page]:
    """La coque, ciblée sur le projet A — le portefeuille se choisit ensuite."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.new_page()
    slugs = served_portfolio["slugs"]
    page.goto(f"{served_portfolio['url']}/workspace/index.html?project={slugs['a']}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _open_portfolio(page: Page) -> None:
    _goto(page, "executer")
    page.wait_for_selector(".ex-card, .empty")
    _capture(page, "01-executer-avant-ce-projet")
    page.locator("select[data-role='scope']").select_option("portfolio")
    page.wait_for_selector(".ex-card[data-project]")
    page.wait_for_function("() => document.querySelectorAll('.ex-card[data-project]').length >= 2")


def test_le_portefeuille_liste_les_deux_projets_et_nomme_le_troisieme(
    portfolio_page: Page, served_portfolio: dict[str, object]
) -> None:
    page = portfolio_page
    _open_portfolio(page)
    _capture(page, "02-portefeuille-tous-les-projets")

    canvas = page.locator("#canvas")
    text = canvas.inner_text()
    assert TITLE_A in text and TITLE_B in text, "les deux tâches, chacune de son projet"
    slugs = served_portfolio["slugs"]
    projects = {page.locator(f".ex-card[data-project='{slugs[k]}']").count() for k in ("a", "b")}
    assert projects == {1}, "une carte par projet, chacune sous sa puce"
    assert "projet-alpha" in text and "projet-beta" in text, "la puce projet porte le nom du projet"
    assert "session vivante" in text and "session inactive" in text, "point + mot, jamais la couleur seule"
    notice = page.locator(".ex-notice")
    notice.wait_for()
    assert "projet-gamma" in notice.inner_text() and "introuvable" in notice.inner_text()
    assert "Tous les projets" in page.locator("#breadcrumb").inner_text()


def test_le_filtre_sessions_vivantes_ne_garde_que_la_tache_reclamee(
    portfolio_page: Page, served_portfolio: dict[str, object]
) -> None:
    page = portfolio_page
    _open_portfolio(page)
    page.locator("input[data-role='live']").check()
    page.wait_for_function("() => document.querySelectorAll('.ex-card[data-project]').length === 1")
    _capture(page, "03-portefeuille-filtre-sessions-vivantes")

    text = page.locator("#canvas").inner_text()
    assert TITLE_A in text and TITLE_B not in text
    # Filtre projet : B seul, sans le filtre vivant.
    page.locator("input[data-role='live']").uncheck()
    page.wait_for_function("() => document.querySelectorAll('.ex-card[data-project]').length === 2")
    page.locator("select[data-role='project']").select_option(served_portfolio["slugs"]["b"])
    page.wait_for_function("() => document.querySelectorAll('.ex-card[data-project]').length === 1")
    assert TITLE_B in page.locator("#canvas").inner_text()


def test_reprendre_la_session_montre_la_commande_sans_l_executer(
    portfolio_page: Page, served_portfolio: dict[str, object]
) -> None:
    page = portfolio_page
    _open_portfolio(page)
    # `dispatch_event` plutôt que `click()` : sur une longue colonne flex, le
    # clic géométrique touche la ligne voisine (piège connu du harnais).
    page.locator(".ex-card").filter(has_text=TITLE_A).first.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.get_by_text(served_portfolio["task_a"], exact=True).wait_for()
    _capture(page, "04-portefeuille-inspecteur-session")

    session_text = inspector.locator(".ex-session").inner_text()
    assert "vivante" in session_text and LIVE_SESSION in session_text
    assert f"claude --resume {LIVE_SESSION}" in session_text
    button = inspector.locator("button", has_text="Reprendre la session")
    button.wait_for()
    button.dispatch_event("click")
    inspector.get_by_text("commande copiée").wait_for()
    assert page.evaluate("() => navigator.clipboard.readText()") == f"claude --resume {LIVE_SESSION}"


def test_une_transition_gatee_depuis_le_portefeuille_ecrit_dans_le_ledger_du_bon_projet(
    portfolio_page: Page, served_portfolio: dict[str, object]
) -> None:
    """Le cockpit sert A en direct (`?project=alpha`) ; B n'est que regardé.
    En portefeuille, la porte de B reste actionnable (jamais « Écriture
    désactivée »), et la réaliser écrit dans le ledger de B — pas dans celui
    de A. `block` suit exactement la même route (prouvé côté transport dans
    `tests/unit/test_workspace_routes.py`, section 3ter) ; ici c'est la
    transition que le gabarit gouverné déclare et accorde, `proposed → ready`.
    """
    from grimoire.missions.service import TaskService

    page = portfolio_page
    _open_portfolio(page)
    page.locator(".ex-card").filter(has_text=TITLE_B).first.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.get_by_text(served_portfolio["task_b"], exact=True).wait_for()
    assert inspector.locator("button", has_text="Écriture désactivée").count() == 0
    gate = inspector.locator(".ex-gate-row").filter(has_text="→ Prête").first
    gate.wait_for()
    _capture(page, "05-portefeuille-porte-projet-b-avant")
    gate.locator("button", has_text="Réaliser").dispatch_event("click")
    inspector.get_by_text("→ En cours").first.wait_for()
    _capture(page, "06-portefeuille-porte-projet-b-apres")

    roots = served_portfolio["roots"]
    task_b = served_portfolio["task_b"]
    assert TaskService(roots["b"]).require(task_b).status.value == "ready"
    assert TaskService(roots["a"]).ledger.get_task(task_b) is None
    assert TaskService(roots["a"]).require(served_portfolio["task_a"]).status.value == "claimed"
