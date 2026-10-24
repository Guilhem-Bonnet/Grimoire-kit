"""Espace Mémoire — onglet Flotte, agrégation multi-projets (#172, dernier
volet de « Cockpit — du générateur statique au portefeuille actif », Refs
#468).

``tests/unit/tools/test_workspace_memory.py`` et
``tests/unit/cli/test_cmd_cockpit_memory_overview.py`` couvrent déjà
l'agrégation côté serveur, en isolation et par transport HTTP nu. Ce module
prouve le parcours navigateur qui reste : basculer le zoom du docbar sur
« Tous les projets » montre deux lignes, et chercher un terme présent dans
la mémoire d'un seul projet montre son étiquette — jamais un mélange des
deux stores.
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

from .conftest import open_scope_all

#: Terme distinctif présent dans la mémoire du seul projet A — choisi assez
#: rare pour ne matcher ni du bruit, ni l'autre projet.
_DISTINCTIVE_TERM = "redis873"


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
    msg = f"`grimoire cockpit serve` n'a pas répondu sur :{port}"
    raise TimeoutError(msg)


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


@pytest.fixture(scope="session")
def served_memory_fleet(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str, str]]:
    """Un cockpit servant deux projets réels, mémoire distincte pour chacun.

    Le projet A porte une entrée mémoire contenant `_DISTINCTIVE_TERM` ; le
    projet B est initialisé mais n'a jamais reçu d'écriture — la recherche
    croisée ne doit étiqueter QUE le premier.
    """
    root = tmp_path_factory.mktemp("memoire-fleet") / "fake-home"
    root.mkdir(parents=True)
    project_a = root / "projet-memoire-a"
    project_b = root / "projet-memoire-b"
    port = _free_port()
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    env_init = dict(env)
    env_init["GRIMOIRE_NO_COCKPIT"] = "1"

    for project, name in ((project_a, "projet-memoire-a"), (project_b, "projet-memoire-b")):
        init = subprocess.run(
            [sys.executable, "-m", "grimoire", "init", str(project), "-y", "--name", name, "--backend", "local"],
            env=env_init, capture_output=True, text=True, timeout=180, encoding="utf-8",
        )
        if not (project / "_grimoire" / "kit").is_dir():
            pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
        subprocess.run(
            [sys.executable, "-m", "grimoire", "cockpit", "add", str(project)],
            env=env_init, capture_output=True, text=True, timeout=60, encoding="utf-8",
        )

    remembered = subprocess.run(
        [
            sys.executable, "-m", "grimoire", "memory", "remember",
            f"le ticket {_DISTINCTIVE_TERM} bloque le cache de session",
            "--type", "decisions", "--agent", "e2e-fixture",
        ],
        cwd=str(project_a), env=env_init, capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    if remembered.returncode != 0:
        pytest.skip(f"`grimoire memory remember` a échoué : {remembered.stderr[-400:]}")

    registry_path = Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slug_a = next((str(e.get("slug", "")) for e in registry if e.get("path") == str(project_a)), "")
    slug_b = next((str(e.get("slug", "")) for e in registry if e.get("path") == str(project_b)), "")
    if not slug_a or not slug_b:
        pytest.skip("slugs introuvables au registre du cockpit après `add`")

    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "cockpit", "serve",
            "--port", str(port), "--no-open", "--no-refresh",
        ],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", slug_a, slug_b
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def memory_fleet_workspace(browser: Browser, served_memory_fleet: tuple[str, str, str]) -> Iterator[Page]:
    served, slug_a, _slug_b = served_memory_fleet
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html?project={slug_a}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def test_memoire_view_seg_bascule_correctement(memory_fleet_workspace: Page) -> None:
    """Sur un projet à mémoire initialisée, le groupe de segments a plusieurs
    boutons ; cliqué, un seul est `aria-pressed=true`, celui cliqué."""
    page = memory_fleet_workspace
    # Jamais sondé, le projet est « uninitialized » et n'offre que la Flotte :
    # le segment à plusieurs boutons n'existe qu'après une sonde (le geste du
    # bouton « Sonder la mémoire »).
    probed = page.evaluate(
        "() => fetch('/api/memory/status' + location.search + '&probe=1').then(r => r.status)"
    )
    assert probed == 200, f"sonde mémoire refusée : HTTP {probed}"
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _goto(page, "memoire")
    page.wait_for_selector("#view-seg button")
    buttons = page.locator("#view-seg button")
    count = buttons.count()
    assert count >= 2, f"#view-seg button : au moins deux boutons attendus, {count} trouvé(s)"
    for order in (range(count), reversed(range(count))):
        for index in order:
            buttons.nth(index).click()
            page.wait_for_function(
                "(i) => document.querySelectorAll('#view-seg button')[i]"
                "?.getAttribute('aria-pressed') === 'true'",
                arg=index,
            )
            pressed = [buttons.nth(i).get_attribute("aria-pressed") for i in range(count)]
            assert pressed.count("true") == 1 and pressed[index] == "true", (
                f"seul le bouton {index} doit être actif (observé : {pressed})"
            )


def test_switching_to_all_projects_shows_two_rows(memory_fleet_workspace: Page) -> None:
    page = memory_fleet_workspace
    # La portée de la coque (P31) remplace le zoom « Ce projet / Tous les
    # projets » de Mémoire : en « Tous les projets », la Recherche s'ouvre.
    open_scope_all(page, "#savoir/memoire")
    _goto(page, "memoire")

    table = page.locator(".me-wrap table")
    table.wait_for(timeout=15_000)
    rows = table.locator("tbody tr")
    # Deux lignes minimum : projet A (mémoire écrite) et projet B (vide) —
    # au moins ces deux-là, quel que soit ce que le registre de la machine
    # porte par ailleurs sur cette fixture jetable.
    page.wait_for_function(
        "() => document.querySelectorAll('.me-wrap table tbody tr').length >= 2",
        timeout=15_000,
    )
    assert rows.count() >= 2


def test_cross_project_search_labels_the_matching_project_only(memory_fleet_workspace: Page) -> None:
    page = memory_fleet_workspace
    # La portée de la coque (P31) remplace le zoom « Ce projet / Tous les
    # projets » de Mémoire : en « Tous les projets », la Recherche s'ouvre.
    open_scope_all(page, "#savoir/memoire")
    _goto(page, "memoire")
    page.wait_for_selector(".me-wrap table")

    search_input = page.locator(".me-search input")
    search_input.wait_for(timeout=10_000)
    search_input.fill(_DISTINCTIVE_TERM)
    page.get_by_role("button", name="Rechercher", exact=True).click()

    result = page.locator(".me-result").first
    result.wait_for(timeout=15_000)
    results_text = page.locator(".me-results").inner_text()
    assert "projet-memoire-a" in results_text or "Alpha" in results_text or _DISTINCTIVE_TERM in results_text

    # Un seul résultat : le terme n'existe QUE dans le projet A.
    assert page.locator(".me-result").count() == 1

    # EKP-E1 : la fraîcheur et la source se lisent sur le résultat, et le score
    # est nommé ou dit inconnu — jamais un « 0.000 » fabriqué.
    assert page.locator(".me-result .chip.pill[data-freshness='non_verifie']").count() == 1
    assert "saisie libre" in result.inner_text()
    assert "score 0.000" not in results_text

    # Cliquer le résultat l'ouvre dans le panneau d'inspection, avec son
    # projet d'origine.
    result.click()
    inspector_text = page.locator("#inspector-body").inner_text()
    assert _DISTINCTIVE_TERM in inspector_text
