"""Savoir › Santé et carte (P25) : la santé de la mémoire, sa file d'hygiène, son alerte dans À traiter.

Le cockpit sert un projet initialisé pour de vrai dont le store reproduit
celui de la Forge : 25 fois « python is the best language », utilisateur
alice, et un seul vrai souvenir. Prouvé par navigateur :

- le segmenté de Savoir porte « Santé et carte », « Recherche », « Rappel », « Journal »
  (pas de vue qui ne marche pas encore) et l'entrée de Savoir reste la
  Recherche ;
- la carte « Santé de la mémoire » rend chaque critère avec sa mesure, et le
  projet sort en alerte ;
- la file d'hygiène : chaque geste ouvre un aperçu (ce qui sera touché), puis
  donne la commande — l'export avant la purge — avec un Copier qui confirme
  sur place ;
- À traiter montre l'alerte mémoire et son action ouvre la Santé sur elle.
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

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

_SEED = r"""
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
for _ in range(25):
    mgr.store("python is the best language", user_id="alice")
mgr.store("Qdrant partagé épinglé en 1.19.1, une mineure à la fois.", metadata={"type": "decisions"}, actor="Guilhem")
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


@pytest.fixture(scope="session")
def served_sante(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    root = tmp_path_factory.mktemp("sante-p25") / "fake-home"
    root.mkdir(parents=True)
    forge = root / "grimoire-forge"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    init = _run([sys.executable, "-m", "grimoire", "init", str(forge), "-y", "--name", "Grimoire-Forge",
                 "--backend", "local"], root, env_init)
    if not (forge / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    _run([sys.executable, "-m", "grimoire", "cockpit", "add", str(forge)], root, env_init)
    seeded = _run([sys.executable, "-c", _SEED], forge, env_init)
    assert seeded.returncode == 0, seeded.stderr[-800:]
    registry = json.loads((Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json").read_text(encoding="utf-8"))
    slug = next((str(e.get("slug", "")) for e in registry if Path(str(e.get("path", ""))).name == "grimoire-forge"), "")
    if not slug:
        pytest.skip("slug introuvable au registre du cockpit après `add`")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "forge": slug}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def sante(browser: Browser, served_sante: dict[str, str]) -> Iterator[tuple[Page, dict[str, str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce",
                                  color_scheme="light")
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=served_sante["base"])
    page = context.new_page()
    dialogs: list[str] = []
    errors: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield page, served_sante
    finally:
        context.close()
    assert dialogs == [], f"aucune boîte de dialogue native attendue : {dialogs}"
    assert errors == [], f"erreurs de page : {errors}"


def _open_sante(page: Page, srv: dict[str, str], suffix: str = "") -> None:
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['forge']}#savoir/sante{suffix}",
              wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'sante'")
    page.wait_for_selector(".sm-health .sm-crit", timeout=60_000)


def _hygiene_row(page: Page, kind: str):
    return page.locator(f".sm-hyg[data-kind='{kind}']")


def test_le_segmente_de_savoir_et_l_entree_par_la_recherche(sante: tuple[Page, dict[str, str]]) -> None:
    page, srv = sante
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['forge']}#savoir", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")

    tabs = [t.strip() for t in page.locator("#section-tabs .tab").all_inner_texts()]
    assert tabs == ["Santé et carte", "Recherche", "Rappel", "Carte 3D", "Journal"]

    page.locator("#section-tabs [data-section='sante']").click()
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'sante'")
    page.wait_for_selector(".sm-health .sm-crit", timeout=60_000)
    assert page.url.endswith("#savoir/sante")


def test_la_sante_rend_chaque_critere_et_le_projet_sort_en_alerte(sante: tuple[Page, dict[str, str]]) -> None:
    page, srv = sante
    _open_sante(page, srv)

    head = page.locator(".sm-health .sm-cardhead .badge")
    assert head.inner_text().strip().endswith("en échec")
    assert page.locator(".sm-wrap").get_attribute("data-alert") == "true"
    distinct = page.locator(".sm-crit[data-criterion='distinct']")
    assert distinct.get_attribute("data-state") == "fail"
    assert distinct.locator(".sm-value").inner_text().strip() == "2"
    assert "« python is the best language » ×25" in distinct.locator(".sm-detail").inner_text()
    users = page.locator(".sm-crit[data-criterion='users']")
    assert users.get_attribute("data-state") == "fail"
    assert "alice (25)" in users.inner_text()
    states = set(page.locator(".sm-crit").evaluate_all("rows => rows.map(r => r.dataset.state)"))
    assert states <= {"fail", "ok", "unknown"}
    for value in page.locator(".sm-crit .sm-value").all_inner_texts():
        assert value.strip() and "[n]" not in value, "chaque critère porte sa mesure, jamais un trou"
    assert "grimoire-forge › local · 26 souvenirs, 0 projection" in page.locator(".space-head").inner_text()


def test_la_carte_par_hall_rend_la_mesure_et_ouvre_la_recherche_filtree(sante: tuple[Page, dict[str, str]]) -> None:
    """P73 : surface = nombre d'entrées classées, un clic descend dans la Recherche filtrée."""
    page, srv = sante
    _open_sante(page, srv)

    # 25 « python is the best language » (faits non typés) + 1 décision = un seul hall.
    tiles = page.locator(".sm-tmap .sm-tile")
    assert tiles.count() == 1
    tile = tiles.first
    assert tile.get_attribute("data-hall") == "faits"
    assert tile.locator(".sm-tile-n").inner_text() == "26"
    assert "25 faits non typés" in tile.locator(".sm-tile-sub").inner_text()
    assert "1 décision" in tile.locator(".sm-tile-sub").inner_text()
    assert page.locator(".sm-map .sm-slot").count() == 0, "la carte est servie : plus de place vide"

    # La légende « Lire la carte » vit dans le panneau de droite et ne promet
    # que ce que la carte dessine (la forme ; fraîcheur et liens sont nommés à venir).
    legend = page.locator("#inspector-body .sm-legend")
    assert legend.get_attribute("aria-label") == "Lire la carte"
    assert legend.locator(".sm-leg").count() == 4
    assert "Faits et décisions" in legend.inner_text()
    assert "ne les dessine pas encore" in legend.locator(".sm-leg-note").inner_text()

    tile.click()
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")
    page.wait_for_selector(".kn-main .kn-summary:not(:empty)", timeout=30_000)
    boxes = page.locator(".kn-filters fieldset").first.locator(".kn-chk")
    states = {
        boxes.nth(i).locator(".kn-lbl").inner_text().strip(): boxes.nth(i).locator("input").is_checked()
        for i in range(boxes.count())
    }
    assert states.get("Décision") is True and states.get("Fait non typé") is True
    assert states.get("Incident, piège") is False, "le hall cliqué filtre la Recherche sur ses seuls types"


def test_la_carte_dit_sa_place_vide_plutot_que_dessiner(sante: tuple[Page, dict[str, str]]) -> None:
    """P73, états vides : un store vide ou non lu se dit en clair, rien n'est simulé."""
    page, srv = sante
    _open_sante(page, srv)
    report = page.evaluate(
        "(u) => fetch(u).then(r => r.json())",
        f"{srv['base']}/api/workspace/memory/quality?slug={srv['forge']}",
    )

    empty = dict(report)
    empty["measures"] = {**report["measures"], "types": {}}
    page.route("**/api/workspace/memory/quality*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(empty)))
    page.get_by_role("button", name="Mesurer à nouveau").click()
    page.wait_for_selector(".sm-map .sm-slot", timeout=30_000)
    assert "store est vide" in page.locator(".sm-map .sm-slot").inner_text()

    unread = dict(report)
    unread["storeReason"] = "aucun store encore créé"
    unread["measures"] = {**report["measures"], "types": None}
    page.unroute("**/api/workspace/memory/quality*")
    page.route("**/api/workspace/memory/quality*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(unread)))
    page.get_by_role("button", name="Mesurer à nouveau").click()
    page.wait_for_function(
        "() => (document.querySelector('.sm-map .sm-slot')?.textContent || '').includes('Carte non mesurable')",
        timeout=30_000,
    )
    assert "aucun store encore créé" in page.locator(".sm-map .sm-slot").inner_text()
    page.unroute("**/api/workspace/memory/quality*")


def test_le_geste_passe_par_un_apercu_puis_la_commande_a_copier(sante: tuple[Page, dict[str, str]]) -> None:
    page, srv = sante
    _open_sante(page, srv)

    row = _hygiene_row(page, "memory_quality")
    assert row.locator(".sm-hyg-title").inner_text() == "24 copies et des entrées de test dans le store"
    button = row.locator("button[aria-expanded]")
    assert button.inner_text().strip() == "Purger (aperçu)…"
    preview = row.locator(".sm-preview")
    assert preview.is_hidden()

    button.click()
    assert button.get_attribute("aria-expanded") == "true"
    assert preview.is_visible()
    assert "« python is the best language » · ×25 · utilisateur alice" in preview.locator(".sm-touched").inner_text()
    command = preview.locator("code").inner_text()
    assert command.startswith("grimoire memory export --file ")
    assert command.index("export") < command.index("gc"), "l'export précède toute suppression"

    preview.get_by_role("button", name="Copier").click()
    status = preview.locator(".action-status")
    page.wait_for_function("(el) => el.textContent.length > 0", arg=status.element_handle())
    assert status.inner_text() == "Commande copiée : colle-la dans un terminal du projet."
    assert page.evaluate("() => navigator.clipboard.readText()") == command


def test_a_traiter_montre_l_alerte_memoire_et_ouvre_la_sante(sante: tuple[Page, dict[str, str]]) -> None:
    page, srv = sante
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['forge']}#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    row = page.locator(".at-row[data-kind='memory_quality']")
    row.wait_for(timeout=60_000)
    assert "python is the best language" in row.inner_text()
    assert row.locator(".at-title").inner_text() == "24 copies et des entrées de test dans le store"
    action = row.locator("a.at-act")
    assert action.inner_text().strip() == "Voir la santé"
    assert "#sante?item=memory_quality" in action.get_attribute("href")

    row.click()
    assert "grimoire memory export --file" in page.locator(".at-detail .at-formula").inner_text()

    action.click()
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'sante'")
    page.wait_for_selector(".sm-hyg[data-kind='memory_quality'][data-open='true'] .sm-preview:not([hidden])",
                           timeout=60_000)
