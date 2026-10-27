"""Piloter › fiche projet — panneau « Capacité » (P71, GAO-p71-capacit-001).

Un projet réellement initialisé, servi par `grimoire serve`, avec un ledger
seedé pour montrer les goulots : un worker tient le seul créneau WIP, deux
tâches prêtes sont différées « WIP atteint », la section ``pools.ci`` de
``capacity.yaml`` est illisible (le réservoir s'affiche « inconnue », en ton
neutre — une sonde muette n'est pas une panne), et la consommation du jour est
inconnue sous un quota fini. Prouvé par navigateur :

- la ligne « N workers actifs sur M » et le quota ;
- le bandeau des réservoirs : l'illisible porte « inconnue », les sains sont
  regroupés sur une ligne compacte ;
- la file, info reine : groupée par raison, chaque tâche différée porte la
  sienne en clair ; aucune session n'y est listée, seulement des tâches.
"""

from __future__ import annotations

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

HELD_TITLE = "P71 : tâche déjà en cours de travail"
FIRST_TITLE = "P71 : refonte du panneau de capacité"
SECOND_TITLE = "P71 : nettoyage des captures du cockpit"

#: `pools.ci.runners: 0` est invalide (entier strictement positif exigé) : la
#: section est illisible, la jauge CI doit dire « inconnue », jamais deviner.
CAPACITY = """\
version: 1
wip:
  limit_global: 1
pools:
  ci:
    runners: 0
  llm:
    claude:
      sessions: 2
quota:
  tokens_per_day: 1000
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
def p71_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("capacite-p71") / "projet-capacite-p71"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-capacite-p71", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet de P71 ne peut pas être monté")

    service = TaskService(root)
    mission = service.ledger.create_mission(title="P71 capacité", origin="test").id

    def pret(title: str, priority: str) -> str:
        task = service.ledger.create_task(mission, title, acceptance=("le panneau se lit",), owner="amelia", priority=priority)
        service.ledger.transition_task(task.id, TaskState.READY)
        return task.id

    held = pret(HELD_TITLE, "high")
    service.claim(held, "agent-a", host="host-claude-code-cli")
    pret(FIRST_TITLE, "high")
    pret(SECOND_TITLE, "low")
    # Un dispatch du jour sans chiffre de tokens : la consommation est inconnue.
    service.ledger.append_event("task.dispatched", held, "task", "test", {})
    # La capacité s'écrit APRÈS le claim : avec `limit_global: 1`, la garde de
    # claim aurait sinon refusé le seed lui-même.
    capacity_file = root / "_grimoire" / "standard" / "capacity.yaml"
    capacity_file.parent.mkdir(parents=True, exist_ok=True)
    capacity_file.write_text(CAPACITY, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def p71_url(p71_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p71"))
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(p71_project), "--port", str(port), "--no-open"],
        cwd=str(p71_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
def capacite(browser: Browser, p71_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{p71_url}/workspace/index.html#flotte", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'piloter'")
    page.wait_for_selector(".cp-panel .cp-file", timeout=60_000)
    try:
        yield page
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"


def test_le_panneau_montre_le_wip_et_le_quota(capacite: Page) -> None:
    head = capacite.locator(".cp-panel .cp-head")
    assert "1 worker actif sur 1" in head.locator(".cp-wip").inner_text()
    quota = head.locator(".cp-quota")
    text = quota.inner_text()
    # Consommation inconnue sous quota fini : « inconnue », jamais un zéro inventé.
    # (le plafond est localisé fr-FR : « 1 000 » avec une espace fine insécable)
    assert "inconnue" in text and "000 tokens" in text


def test_un_reservoir_illisible_dit_inconnue_en_ton_neutre_et_les_sains_sont_groupes(capacite: Page) -> None:
    ci = capacite.locator(".cp-res[data-reservoir='ci.runners']")
    assert ci.get_attribute("data-etat") == "inconnu"
    assert "inconnue" in ci.inner_text()
    assert "pools.ci.runners" in ci.inner_text()
    # Neutre : la couleur vient du token d'encre discrète, pas d'un état d'alerte.
    color = ci.evaluate("(el) => getComputedStyle(el).color")
    ink3 = capacite.locator(".cp-panel").evaluate(
        "(el) => { const probe = document.createElement('span'); probe.style.color = 'var(--ink3)';"
        " el.append(probe); const c = getComputedStyle(probe).color; probe.remove(); return c; }"
    )
    assert color == ink3, f"« inconnue » doit rester en ton neutre (ink3), pas {color}"
    sains = capacite.locator(".cp-panel .cp-sains")
    assert sains.count() == 1, "les réservoirs sains tiennent sur une seule ligne compacte"
    assert "Sessions claude" in sains.inner_text() and "0/2" in sains.inner_text()
    meter = sains.locator("[role='meter']").first
    assert meter.get_attribute("aria-valuenow") == "0"
    assert meter.get_attribute("aria-valuemax") == "2"


def test_la_file_est_groupee_par_raison_et_chaque_differee_porte_la_sienne(capacite: Page) -> None:
    group = capacite.locator(".cp-group[data-group='defer-wip']")
    assert group.locator("h4").inner_text() == "WIP atteint"
    rows = capacite.locator(".cp-row[data-verdict='defer']")
    assert rows.count() >= 2
    first = rows.filter(has_text="refonte du panneau de capacité").first
    assert "WIP global atteint (1/1)" in (first.get_attribute("title") or "")
    # L'ordre réel d'admission : la haute priorité avant la basse.
    texts = capacite.locator(".cp-row .cp-t").all_inner_texts()
    assert texts.index(FIRST_TITLE) < texts.index(SECOND_TITLE)
    # La file liste des tâches, jamais des sessions (la seule liste de
    # sessions du cockpit est Travail › Sessions).
    assert "agent-a" not in capacite.locator(".cp-panel").inner_text()
    assert capacite.locator(".cp-row", has_text=HELD_TITLE).count() == 0, "une tâche tenue n'est plus dans la file"
