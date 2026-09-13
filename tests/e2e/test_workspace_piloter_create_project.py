"""« Nouveau projet » depuis le portefeuille — parcours complet (issue #172).

Volet restant de #172 (le point 1, API réelle de lecture, est livré depuis
#143) : un bouton qui ouvre le wizard de #171 sur un chemin choisi,
enregistre le projet à la fin, et le sélecteur de projets (le tableau
Flotte) le montre sans redémarrer le cockpit.

`tests/unit/cli/test_cmd_cockpit_create_project.py` couvre déjà
``POST /api/projects/create`` en isolation (scaffold, refus nommés) ; ce
module prouve que le bouton de l'espace Piloter (niveau Flotte) déclenche la
même exécution, jusqu'à la navigation sur la fiche du projet créé et sa
réapparition dans le tableau.

Un cockpit sans AUCUN projet enregistré ne répond même pas à ``/api/status``
(aucune racine à servir) : le harnais enregistre un premier projet jetable
pour que le cockpit ait un état — ce n'est pas la cible du test, seulement sa
condition de démarrage.
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


@pytest.fixture
def served_fleet(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """Un cockpit avec un premier projet déjà enregistré, prêt pour la création.

    ``HOME`` est détourné vers un dossier jetable pour toute la durée du
    process serveur : c'est ce qui rend ``resolve_within_allowed`` capable
    d'accepter le nouveau chemin (sous ``$HOME``) sans toucher la machine
    réelle, et c'est aussi ce qui laisse ``_select_cwd_project`` enrôler
    silencieusement le cwd du process sans effet — le cwd choisi ici
    (``fake_home``) ne porte aucun marqueur Grimoire.
    """
    root = tmp_path_factory.mktemp("cockpit-create") / "fake-home"
    root.mkdir(parents=True)
    existing = root / "projet-existant"
    port = _free_port()
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    env_init = dict(env)
    env_init["GRIMOIRE_NO_COCKPIT"] = "1"
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", str(existing), "-y", "--backend", "local"],
        env=env_init, capture_output=True, text=True, timeout=180, encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(existing)],
        env=env_init, capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "cockpit", "serve",
            "--port", str(port), "--no-open", "--no-refresh",
        ],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def fleet_workspace(browser: Browser, served_fleet: tuple[str, Path]) -> Iterator[Page]:
    served, _root = served_fleet
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def test_creating_a_project_from_the_fleet_makes_it_appear(
    fleet_workspace: Page, served_fleet: tuple[str, Path],
) -> None:
    _served, root = served_fleet
    page = fleet_workspace
    target = root / "nouveau-jetable"

    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    # Lancement bare (sans --project-root) sur un registre déjà peuplé : la
    # fiche du projet existant s'ouvre par défaut, la Flotte est un onglet.
    page.get_by_role("button", name="Flotte").click()

    new_btn = page.get_by_role("button", name="+ Nouveau projet")
    new_btn.wait_for(timeout=15_000)
    new_btn.click()

    path_input = page.locator("input[placeholder='/chemin/absolu/du/nouveau-projet']")
    path_input.wait_for(timeout=10_000)
    path_input.fill(str(target))
    page.get_by_role("button", name="Créer le projet").click()

    # Navigation directe sur la fiche du projet créé — la même mécanique que
    # cliquer une ligne du tableau (`onSelect`), pas une route dédiée.
    page.wait_for_function(
        "() => document.querySelector('.pl-sheet h2')?.textContent.includes('nouveau-jetable')",
        timeout=30_000,
    )

    # Vérité sur disque : le plan a réellement exécuté `grimoire up`.
    assert (target / "project-context.yaml").is_file()

    # Le sélecteur de projets (le tableau Flotte) le montre, sans redémarrer
    # le cockpit : retour au niveau Flotte, dans la MÊME session navigateur.
    page.get_by_role("button", name="Flotte").click()
    page.wait_for_selector(".pl-table-wrap")
    table_text = page.locator(".pl-table-wrap").inner_text()
    assert "nouveau-jetable" in table_text


def test_creating_a_project_on_an_existing_path_is_refused(
    fleet_workspace: Page, served_fleet: tuple[str, Path],
) -> None:
    """Refus nommé : un chemin qui EST DÉJÀ un projet n'est jamais écrasé."""
    _served, root = served_fleet
    page = fleet_workspace
    existing = root / "projet-existant"

    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    page.get_by_role("button", name="Flotte").click()
    page.get_by_role("button", name="+ Nouveau projet").click()

    path_input = page.locator("input[placeholder='/chemin/absolu/du/nouveau-projet']")
    path_input.wait_for(timeout=10_000)
    path_input.fill(str(existing))
    page.get_by_role("button", name="Créer le projet").click()

    page.wait_for_selector("text=refusé")
