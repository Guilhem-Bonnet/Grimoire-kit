"""Travail et Flotte : rien ne déborde, aucune colonne n'est rognée (issue #883).

Constat d'origine (2026-10-23, 1440 px, explorateur et inspecteur ouverts) : une carte
du board dont le titre est long déborde de sa colonne, la troisième colonne est rognée
à droite, la Flotte porte une valeur coupée. Chaque test mesure les boîtes rendues par
``grimoire serve`` sur un projet réel dont les titres de tâches sont très longs, à
1440, 1280 et 1024 px.

Ce qui est mesuré, jamais supposé :

- aucune carte ne dépasse la boîte de sa colonne ;
- aucune colonne n'est rognée : sa boîte tient dans la zone du board, ou le board
  la fait défiler (``scrollWidth`` > ``clientWidth``) sans qu'elle soit coupée par un
  ancêtre non défilant ;
- la page ne défile pas à l'horizontale.
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
from playwright.sync_api import Browser, BrowserContext, Page, Route

APPEAR_MS = 10_000
WIDTHS = (1440, 1280, 1024)

LONG = (
    "Ledger : aucune commande ne crée de pack de preuve pour une tâche dont le titre est "
    "particulièrement long et sans aucun point de rupture évident"
)
UNBROKEN = "Extraordinairement" + "longuementsansespace" * 6

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""


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
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("colonnes-883") / "projet-colonnes-883"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-colonnes-883", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    for title in (LONG, UNBROKEN, LONG + " bis"):
        service.add(title, (title,), owner="kit-dev-avec-un-nom-long", actor="t", ready=True)
    path = (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION)
    for stop in range(1, len(path) + 1):
        for title in (LONG, UNBROKEN):
            tid = service.add(f"{title} {stop}", (title,), owner="claude", actor="t").task.id
            for state in path[:stop]:
                claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
                service.ledger.transition_task(tid, state, actor_id="kit-dev", claim=claim)
    for title in (LONG, UNBROKEN):
        tid = service.add(f"{title} clos", (title,), owner="claude", actor="t").task.id
        for state in (*path, TaskState.CLOSED):
            claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
            service.ledger.transition_task(tid, state, actor_id="kit-dev", claim=claim)
    return root


@pytest.fixture(scope="module")
def url(project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-883"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(project), "--port", str(port), "--no-open"],
        cwd=str(project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


def _checkpoint_pending(route: Route) -> None:
    """Un run `project-upgrade` arrêté au checkpoint destructif, posé dans la réponse réelle de la fiche."""
    response = route.fetch()
    payload = response.json()
    payload["upgradeRuns"] = {
        "total": 1,
        "runs": [{"runId": "WFI-project-upgrade-883", "status": "checkpointed", "currentNode": "destructive"}],
    }
    route.fulfill(response=response, body=json.dumps(payload))


def _open(
    browser: Browser, base: str, width: int, space: str, module: str, ready: str, *, checkpoint: bool = False,
) -> tuple[BrowserContext, Page]:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    if checkpoint:
        page.route("**/api/workspace/sheet*", _checkpoint_pending)
    page.goto(f"{base}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(m) => window.GrimoireWorkspace.module === m", arg=module)
    page.wait_for_selector(ready, timeout=APPEAR_MS)
    return context, page


#: Mesure commune : la page ne défile pas à l'horizontale, et aucun élément rendu ne
#: dépasse à droite de la fenêtre sans qu'un ancêtre défilant le contienne.
PAGE_SCROLL = "() => document.scrollingElement.scrollWidth - window.innerWidth"

CLIPPED = """(selector) => {
  // Une boîte est rognée quand un ancêtre qui coupe (overflow hidden ou clip) la
  // laisse dépasser : seul un ancêtre défilant (auto, scroll) est un abri légitime.
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX === 'visible') continue;
      // Le premier ancêtre qui coupe décide : un ancêtre défilant garde la boîte
      // atteignable (le dernier test vérifie le bout du défilement).
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') break;
      const pr = p.getBoundingClientRect();
      if (r.right > pr.right + 1 || r.left < pr.left - 1) {
        out.push({ el: el.className || el.tagName, by: p.className || p.tagName, over: Math.round(r.right - pr.right) });
      }
      break;
    }
  }
  return out;
}"""

CARDS_OUT_OF_COLUMN = """() => {
  const out = [];
  for (const col of document.querySelectorAll('.ex-col')) {
    const c = col.getBoundingClientRect();
    for (const card of col.querySelectorAll('.ex-card')) {
      const r = card.getBoundingClientRect();
      if (r.right > c.right + 1 || r.left < c.left - 1) out.push({ id: card.dataset.id || '', over: Math.round(r.right - c.right) });
      // Le contenu de la carte ne dépasse pas la carte.
      for (const child of card.querySelectorAll('*')) {
        const k = child.getBoundingClientRect();
        if (k.width && k.right > r.right + 1) out.push({ id: card.dataset.id || '', child: child.className || child.tagName, over: Math.round(k.right - r.right) });
      }
    }
  }
  return out;
}"""

COLUMNS_VS_BOARD = """() => {
  const board = document.querySelector('.ex-board');
  const b = board.getBoundingClientRect();
  return {
    board: { left: Math.round(b.left), right: Math.round(b.right), scroll: board.scrollWidth, client: board.clientWidth },
    cols: [...board.querySelectorAll('.ex-col')].map((c) => {
      const r = c.getBoundingClientRect();
      return { group: c.dataset.group, left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) };
    }),
  };
}"""


@pytest.mark.parametrize("width", WIDTHS)
def test_board_sans_debordement(browser: Browser, url: str, width: int) -> None:
    context, page = _open(browser, url, width, "travail", "executer", ".ex-card")
    try:
        assert page.evaluate(PAGE_SCROLL) <= 0, "la page défile à l'horizontale"
        assert page.evaluate(CARDS_OUT_OF_COLUMN) == [], "une carte dépasse sa colonne"
        clipped = page.evaluate(CLIPPED, ".ex-board, .ex-card, .ex-col")
        assert clipped == [], f"largeur {width} : boîtes coupées par un ancêtre {clipped}"
        geo = page.evaluate(COLUMNS_VS_BOARD)
        board = geo["board"]
        for col in geo["cols"]:
            # Rognée = dépasse le bord droit du board alors que le board ne défile pas.
            if board["scroll"] <= board["client"] + 1:
                assert col["right"] <= board["right"] + 1, ("colonne rognée", col, board)
            assert col["width"] >= 200, ("colonne écrasée", col)
        # Sans défilement du board, toutes les colonnes tiennent ; avec défilement, la
        # dernière est atteignable et entière une fois le board au bout.
        page.evaluate("() => { const b = document.querySelector('.ex-board'); b.scrollLeft = b.scrollWidth; }")
        last = page.evaluate(
            """() => { const b = document.querySelector('.ex-board'); const cols = b.querySelectorAll('.ex-col');
              const l = cols[cols.length - 1].getBoundingClientRect(); const r = b.getBoundingClientRect();
              return { right: l.right, boardRight: r.right }; }"""
        )
        assert last["right"] <= last["boardRight"] + 1, ("dernière colonne rognée au bout du défilement", last)
    finally:
        context.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_flotte_sans_debordement(browser: Browser, url: str, width: int) -> None:
    # Fiche du projet servi, avec un checkpoint destructif en attente : la valeur la
    # plus longue que porte une tuile KPI (« checkpoint en attente »).
    context, page = _open(browser, url, width, "flotte", "piloter", ".pl-sheet .kpi-item", checkpoint=True)
    try:
        try:
            page.wait_for_function(
                "() => [...document.querySelectorAll('.kpi-val')].some((v) => v.textContent === 'checkpoint en attente')",
                timeout=8_000,
            )
        except Exception:
            raise AssertionError(page.evaluate("() => document.querySelector('.pl-sheet').innerText.slice(0, 600)")) from None
        assert page.evaluate(PAGE_SCROLL) <= 0, "la page défile à l'horizontale"
        clipped = page.evaluate(CLIPPED, ".kpi-item, .pl-card, .pl-section, .pl-sheet")
        assert clipped == [], f"largeur {width} : boîtes coupées {clipped}"
        tiles = page.evaluate(
            """() => [...document.querySelectorAll('.kpi-item')].map((k) => {
                const r = k.getBoundingClientRect(); const p = k.parentElement.getBoundingClientRect();
                const v = k.querySelector('.kpi-val');
                return { out: Math.round(Math.max(r.right - p.right, p.left - r.left)),
                         cut: v.scrollWidth - v.clientWidth, text: v.textContent };
            })"""
        )
        for tile in tiles:
            assert tile["out"] <= 1, ("tuile hors de sa grille", tile)
            assert tile["cut"] <= 0, (f"largeur {width} : valeur coupée par la tuile", tile)
    finally:
        context.close()


def test_captures(browser: Browser, url: str) -> None:
    """Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, écrit le board (1440) et la fiche (1024) ; sinon ne fait rien."""
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES", "").strip()
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    context, page = _open(browser, url, 1440, "travail", "executer", ".ex-card")
    try:
        page.screenshot(path=str(out / "board-1440.png"))
    finally:
        context.close()
    context, page = _open(browser, url, 1024, "flotte", "piloter", ".pl-sheet .kpi-item", checkpoint=True)
    try:
        page.wait_for_function(
            "() => [...document.querySelectorAll('.kpi-val')].some((v) => v.textContent === 'checkpoint en attente')"
        )
        page.screenshot(path=str(out / "fiche-kpi-1024.png"))
    finally:
        context.close()
