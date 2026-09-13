"""Le wizard exécute (issue #171) — parcours complet dans un vrai navigateur.

Critère d'acceptation de l'issue : « depuis un dossier vierge, wizard web ->
projet réellement initialisé avec needs choisis, sans copier-coller ;
`grimoire doctor` vert derrière ». `tests/unit/tools/test_project_setup.py`
couvre déjà `execute_setup_plan()` en isolation ; ce module prouve que le
bouton « Initialiser le projet » de l'espace Piloter (`web/workspace/spaces/
piloter.js`) déclenche bien la même exécution, jusqu'au rendu du rapport.

`grimoire serve` (pas `cockpit serve`) : c'est le seul hôte où l'écriture est
ouverte sans navigation cockpit (`host.readOnly == False` d'office sur son
propre projet) — voir `web/workspace/api.js::boot()`.

Volontairement SANS `GRIMOIRE_NO_COCKPIT` : ce test vérifie justement que le
wizard enrôle le projet (comme `grimoire up` le ferait), donc l'enrôlement
doit pouvoir se produire — mais `GRIMOIRE_COCKPIT_HOME` reste détourné vers un
répertoire jetable, donc la machine réelle n'est jamais touchée.
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
    msg = f"`grimoire serve` n'a pas répondu sur :{port}"
    raise TimeoutError(msg)


@pytest.fixture
def served_blank(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """`grimoire serve` sur un dossier jetable, JAMAIS initialisé au préalable.

    C'est tout l'enjeu de #171 : contrairement à `served` (conftest.py), le
    projet servi ici n'a ni ``project-context.yaml`` ni ``_grimoire/`` avant
    que le test ne clique « Initialiser le projet ».
    """
    root = tmp_path_factory.mktemp("wizard-executes") / "projet-vierge"
    root.mkdir(parents=True)
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-wizard"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "serve",
            "--project-root", str(root), "--port", str(port), "--no-open",
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
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def blank_workspace(browser: Browser, served_blank: tuple[str, Path]) -> Iterator[Page]:
    served, _root = served_blank
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def test_wizard_initializes_a_blank_project_end_to_end(
    blank_workspace: Page, served_blank: tuple[str, Path],
) -> None:
    _served, root = served_blank
    page = blank_workspace

    # Piloter est l'espace par défaut ; sur `grimoire serve` (pas cockpit),
    # le niveau est directement « projet », jamais « flotte ».
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    page.wait_for_selector(".pl-sheet")

    run_btn = page.get_by_role("button", name="Initialiser le projet")
    run_btn.wait_for(timeout=15_000)

    name_input = page.locator(".pl-insp-block input[type='text']").first
    name_input.fill("projet-vierge-e2e")

    run_btn.click()

    # Le rapport d'exécution remplace le bouton une fois le plan appliqué.
    page.wait_for_selector("text=Projet initialisé.", timeout=60_000)
    page.wait_for_selector("text=doctor conforme")

    # Vérité sur disque, pas seulement dans le DOM : le plan a réellement
    # exécuté `grimoire up`, pas juste écrit une commande à copier-coller.
    assert (root / "project-context.yaml").is_file()
    assert (root / "_grimoire" / "standard").is_dir()
    run_report = json.loads((root / "_grimoire" / "setup-run.json").read_text(encoding="utf-8"))
    assert run_report["ok"] is True
    assert run_report["doctorOk"] is True
    plan = json.loads((root / "_grimoire" / "setup-plan.json").read_text(encoding="utf-8"))
    assert plan["executed"] is True
