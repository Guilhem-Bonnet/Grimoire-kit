"""Reçu de rappel visible et rejouable (P27) : l'inspecteur de tâche et Savoir › Rappel.

Le cockpit (lecture seule) sert un projet initialisé pour de vrai : trois
souvenirs sur le Qdrant partagé, une tâche dont le rappel a été lu par une
session (le reçu P13 est écrit par la mémoire elle-même), puis la mémoire a
bougé — l'épinglage 1.17.1 est remplacé, une nouvelle consigne est écrite.
Prouvé par navigateur :

- l'inspecteur de la tâche montre la lecture servie : requête, heure, entrées
  avec rang, score, portée, état d'aujourd'hui et fraîcheur ; une tâche sans
  lecture le dit, sans tableau vide ;
- « Rejouer maintenant » relance la requête et rend le différentiel (la
  remplacée et l'apparue y sont), sans écrire le moindre reçu ;
- Savoir › Rappel rend le même reçu en grand, se rejoue, et une requête libre
  se rejoue de la même façon (premier passage, puis comparaison).

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les captures 1440x900 (clair et sombre)
sont écrites ; sans la variable ``_capture`` ne fait rien.
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
from playwright.sync_api import Browser, Page

APPEAR_MS = 30_000
TASK_TITLE = "Mettre à jour le Qdrant partagé du poste"
QUIET_TITLE = "Relire la charte des icônes du cockpit"

_SEED = r"""
import json
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager
from grimoire.missions.service import TaskService

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
old = mgr.remember("decisions", "agent", "Épinglage du Qdrant partagé en 1.17.1.")
new = mgr.remember("decisions", "agent", "Qdrant partagé épinglé en 1.19.1, une mineure à la fois.")
snap = mgr.remember("failures", "agent", "Piège du Qdrant partagé : snapshots hors volume, perdus à la recréation.")
svc = TaskService(root)
task = svc.add("Mettre à jour le Qdrant partagé du poste", ("version montée",), actor="t", owner="guilhem", ready=True).task
quiet = svc.add("Relire la charte des icônes du cockpit", ("charte relue",), actor="t", owner="guilhem", ready=True).task
svc.recall(task.id)  # la lecture d'une session : le reçu de rappel est écrit par la mémoire
mgr.supersede(old.id, new.id)
fresh = mgr.remember("decisions", "agent", "Qdrant partagé : exporter les snapshots avant toute montée de version.")
print(json.dumps({"old": old.id, "new": new.id, "snap": snap.id, "fresh": fresh.id, "task": task.id, "quiet": quiet.id}))
"""

_LATER = r"""
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
print(mgr.remember("failures", "agent", "Snapshots : la restauration n'a jamais été testée sur le poste.").id)
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
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


@pytest.fixture(scope="module")
def served_rappel(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    root = tmp_path_factory.mktemp("rappel-p27") / "fake-home"
    root.mkdir(parents=True)
    forge = root / "qdrant-poste"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    subprocess.run(["git", "init", "-q", str(forge)], check=False, capture_output=True)
    init = _run([sys.executable, "-m", "grimoire", "init", str(forge), "-y", "--name", "qdrant-poste",
                 "--backend", "local"], root, env_init)
    if not (forge / "_grimoire" / "kit").is_dir():
        pytest.fail(f"`grimoire init` indisponible : {init.stderr[-400:]}")
    _run([sys.executable, "-m", "grimoire", "cockpit", "add", str(forge)], root, env_init)
    seeded = _run([sys.executable, "-c", _SEED], forge, env_init)
    assert seeded.returncode == 0, seeded.stderr[-800:]
    ids = json.loads(seeded.stdout.strip().splitlines()[-1])
    registry = json.loads((Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json").read_text(encoding="utf-8"))
    slug = next((str(e.get("slug", "")) for e in registry if Path(str(e.get("path", ""))).name == "qdrant-poste"), "")
    assert slug, "slug introuvable au registre du cockpit après `add`"

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "slug": slug, "root": str(forge), "env": json.dumps(env_init), **ids}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def rappel(browser: Browser, served_rappel: dict[str, str]) -> Iterator[tuple[Page, dict[str, str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield page, served_rappel
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"


#: Contraste calculé sur le rendu, même méthode que test_workspace_responsive_a11y.py, borné à *selector*.
_CONTRAST_JS = """
(selector) => {
  const parse = (value) => {
    const n = value.match(/[\\d.]+/g).map(Number);
    return [n[0] / 255, n[1] / 255, n[2] / 255, n.length > 3 ? n[3] : 1];
  };
  const lum = ([r, g, b]) => {
    const f = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const backdrop = (el) => {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0.9) return bg;
    }
    return parse(getComputedStyle(document.body).backgroundColor);
  };
  const out = [];
  for (const scope of document.querySelectorAll(selector)) {
    for (const el of scope.querySelectorAll('*')) {
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const text = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim())
        .map((n) => n.textContent.trim()).join('');
      if (!text) continue;
      const a = lum(parse(style.color)), b = lum(backdrop(el));
      out.push({ text: text.slice(0, 40), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) });
    }
  }
  return out;
}
"""


def _low_contrast(page: Page, selector: str) -> list[dict]:  # type: ignore[type-arg]
    page.evaluate("() => document.fonts.ready")
    return [m for m in page.evaluate(_CONTRAST_JS, selector) if m["ratio"] < 4.5]


def _reads(srv: dict[str, str]) -> int:
    path = Path(srv["root"]) / "_grimoire-output" / "memory-reads.jsonl"
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _set_theme(page: Page, theme: str) -> None:
    page.evaluate("(t) => document.querySelector(`[data-theme-pref=\"${t}\"]`).click()", theme)
    page.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)


def _open_task(page: Page, srv: dict[str, str], title: str):  # type: ignore[no-untyped-def]
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['slug']}#travail", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.locator(".ex-card").filter(has_text=title).first.click()
    inspector = page.locator("#inspector-body")
    inspector.locator("h3", has_text=title).wait_for(timeout=APPEAR_MS)
    block = inspector.locator("[data-role='recall-receipt']")
    block.wait_for(timeout=APPEAR_MS)
    return inspector, block


def _api(page: Page, srv: dict[str, str], path: str) -> dict:  # type: ignore[type-arg]
    response = page.request.get(f"{srv['base']}{path}")
    assert response.ok, path
    return response.json()


# ── Critère 1 : l'inspecteur montre la lecture servie ──────────────────────


def test_l_inspecteur_montre_la_lecture_memoire_servie(rappel: tuple[Page, dict[str, str]]) -> None:
    page, srv = rappel
    _inspector, block = _open_task(page, srv, TASK_TITLE)
    receipt = _api(page, srv, f"/api/workspace/tasks/{srv['task']}/receipts?project={srv['slug']}")["receipts"][0]

    meta = block.locator("[data-role='receipt-meta']")
    assert f"« {receipt['query']} »" in meta.inner_text()
    assert receipt["query"].startswith(TASK_TITLE)
    assert meta.locator("time").get_attribute("datetime") == receipt["ts"]
    assert "rappel de tâche" in meta.inner_text()

    items = block.locator("[data-role='receipt-served'] .rc-item")
    assert items.count() == len(receipt["served"]) >= 2
    for index, served in enumerate(receipt["served"]):
        item = block.locator(f".rc-item[data-id='{served['id']}']")
        assert item.locator(".rc-rank").inner_text() == str(index + 1)
        facts = item.locator(".rc-facts").inner_text()
        assert facts.startswith("score ") and "portée projet" in facts
        assert item.locator("[data-freshness]").count() == 1
    # L'état est celui d'aujourd'hui : l'épinglage servi alors a été remplacé depuis.
    old = block.locator(f".rc-item[data-id='{srv['old']}']")
    assert old.get_attribute("data-state") == "superseded"
    assert old.locator(".badge[data-state='superseded']").inner_text() == "remplacée"
    live = block.locator(f".rc-item[data-id='{srv['new']}']")
    assert live.locator(".badge[data-state='live']").inner_text() == "actuelle"

    # L'ordre de P11 tient : gates, transition, critères, preuves, session, puis la mémoire servie.
    assert page.evaluate(
        """() => {
            const root = document.querySelector('#inspector-body');
            const order = ['[data-role="gates"]', '[data-role="acceptance-block"]', '[data-role="evidence-block"]',
                           '[data-role="session-block"]', '[data-role="recall-receipt"]'].map((s) => root.querySelector(s));
            return order.every((node, i) => i === 0 || (order[i - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING));
        }"""
    )
    assert _low_contrast(page, "[data-role='recall-receipt']") == []
    block.scroll_into_view_if_needed()
    _capture(page, "p27-inspecteur-recu-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, "[data-role='recall-receipt']") == []
    block.scroll_into_view_if_needed()
    _capture(page, "p27-inspecteur-recu-sombre")


def test_une_tache_sans_lecture_le_dit(rappel: tuple[Page, dict[str, str]]) -> None:
    page, srv = rappel
    _inspector, block = _open_task(page, srv, QUIET_TITLE)
    assert block.get_attribute("data-empty") == "true"
    assert block.locator("p").inner_text() == "aucune lecture mémoire enregistrée pour cette tâche"
    assert block.locator(".rc-item").count() == 0
    assert block.locator("[data-role='replay']").count() == 0


# ── Critère 2 : rejouer rend le différentiel, sans écrire ──────────────────


def test_rejouer_maintenant_rend_le_differentiel_sans_ecrire(rappel: tuple[Page, dict[str, str]]) -> None:
    page, srv = rappel
    _inspector, block = _open_task(page, srv, TASK_TITLE)
    before = _reads(srv)
    block.locator("[data-role='replay']").click()
    result = block.locator("[data-role='replay-result']")
    result.locator("li").first.wait_for(timeout=APPEAR_MS)

    replaced = result.locator(f"li[data-kind='replaced'][data-id='{srv['old']}']")
    assert "remplacée" in replaced.inner_text() and "était rang" in replaced.inner_text()
    appeared = result.locator(f"li[data-kind='appeared'][data-id='{srv['fresh']}']")
    assert "apparue" in appeared.inner_text() and "exporter les snapshots" in appeared.inner_text()
    counts = result.locator("[data-role='replay-counts']")
    assert counts.locator(".badge[data-kind='replaced']").count() == 1
    assert counts.locator(".badge[data-kind='appeared']").count() == 1
    assert _reads(srv) == before, "rejouer depuis le cockpit n'écrit aucun reçu"
    result.scroll_into_view_if_needed()
    _capture(page, "p27-inspecteur-rejeu-clair")
    _set_theme(page, "dark")
    result.scroll_into_view_if_needed()
    _capture(page, "p27-inspecteur-rejeu-sombre")


# ── Savoir › Rappel : le reçu en grand, une requête libre rejouée ──────────


def _open_rappel(page: Page, srv: dict[str, str], suffix: str = "") -> None:
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['slug']}#savoir/rappel{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'rappel'")
    page.wait_for_selector("[data-role='receipt-head']", timeout=APPEAR_MS)


def test_savoir_rappel_rend_le_recu_et_le_rejoue(rappel: tuple[Page, dict[str, str]]) -> None:
    page, srv = rappel
    _open_rappel(page, srv)
    tabs = [t.strip() for t in page.locator("#section-tabs .tab").all_inner_texts()]
    assert "Rappel" in tabs and tabs.index("Rappel") == tabs.index("Recherche") + 1

    head = page.locator("[data-role='receipt-head']")
    assert f"tâche « {TASK_TITLE} »" in head.inner_text()
    assert "Ce que l’agent a reçu avant de décider" in head.inner_text()
    row = page.locator(f"[data-role='served-table'] .rp-row[data-id='{srv['old']}']")
    assert row.get_attribute("data-state") == "superseded"
    assert "remplacée" in row.inner_text()
    reading = page.locator("#inspector-body [data-role='receipt-reading']")
    assert "Une entrée remplacée depuis a été servie en rang" in reading.inner_text()
    _capture(page, "p27-savoir-rappel-clair")

    before = _reads(srv)
    card = page.locator("[data-role='replay-card']")
    card.get_by_role("button", name="Rejouer et comparer").click()
    card.locator(f"[data-role='replay-result'] li[data-kind='replaced'][data-id='{srv['old']}']").wait_for(timeout=APPEAR_MS)
    assert card.locator(f"li[data-kind='appeared'][data-id='{srv['fresh']}']").count() == 1
    assert _reads(srv) == before
    zone = "[data-role='served-table'], [data-role='replay-card'], [data-role='receipt-reading']"
    assert _low_contrast(page, zone) == []
    _capture(page, "p27-savoir-rappel-rejeu-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, zone) == []
    _capture(page, "p27-savoir-rappel-rejeu-sombre")


def test_une_requete_libre_se_rejoue_de_la_meme_facon(rappel: tuple[Page, dict[str, str]]) -> None:
    page, srv = rappel
    _open_rappel(page, srv)
    before = _reads(srv)
    page.locator("[data-role='free-query']").fill("snapshots Qdrant")
    page.get_by_role("button", name="Lancer").click()
    head = page.locator("[data-role='receipt-head']")
    head.filter(has_text="Ce que la mémoire servirait maintenant").wait_for(timeout=APPEAR_MS)
    assert "« snapshots Qdrant »" in head.inner_text()
    assert page.locator(f"[data-role='served-table'] .rp-row[data-id='{srv['snap']}']").count() == 1

    later = subprocess.run([sys.executable, "-c", _LATER], cwd=srv["root"], env=json.loads(srv["env"]),
                           capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert later.returncode == 0, later.stderr[-800:]
    added = later.stdout.strip().splitlines()[-1]

    card = page.locator("[data-role='replay-card']")
    card.get_by_role("button", name="Rejouer et comparer").click()
    card.locator(f"[data-role='replay-result'] li[data-kind='appeared'][data-id='{added}']").wait_for(timeout=APPEAR_MS)
    assert _reads(srv) == before, "une requête libre du cockpit n'écrit aucun reçu"
