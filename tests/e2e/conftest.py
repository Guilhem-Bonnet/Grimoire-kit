"""Harnais Playwright de la vue de travail — un vrai serveur, un vrai navigateur.

Ce qui se mesure ici ne se mesure pas ailleurs : la taille de police *rendue*,
le contraste *calculé sur le DOM*, et les mécaniques au clavier et à la souris
que la spécification exige (§6.3, §6.4).

Ces tests ne sont **pas** dans ``tests/unit/`` : la CI y mesure la couverture et
y exige que tout passe partout, or Playwright et son Chromium ne sont pas des
dépendances du kit. Ici, leur absence est un ``skip`` explicite, jamais un
faux vert.

Trois règles d'hygiène, apprises à la dure :

- port haut tiré au sort par le noyau, jamais 4173 : deux sessions parallèles ne
  doivent pas se marcher dessus ;
- ``GRIMOIRE_COCKPIT_HOME`` détourné vers un répertoire jetable, pour qu'un test
  n'enrôle pas le poste de la personne qui le lance ;
- le processus est tué **et** son extinction vérifiée : un serveur survivant à
  une session est un port occupé et un dossier verrouillé pour la suivante.
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

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]

#: Le vrai répertoire personnel, capturé à l'import de CE fichier — et non
#: réimporté depuis ``tests/conftest.py``. Sans ``tests/__init__.py``, pytest
#: charge ce dernier comme un module nu ``conftest`` ; un `from tests.conftest
#: import REAL_HOME` ici force Python à le résoudre en plus via le chemin
#: pointé `tests.conftest` (paquet à espace de noms), donc à en réexécuter le
#: code une seconde fois — **après** que `_isolate_user_state` a déjà détourné
#: `HOME`. Le `REAL_HOME` importé vaut alors le faux `HOME`, Playwright
#: cherche Chromium sous un répertoire jetable qui n'existe plus à la fin du
#: test précédent, et le harnais entier se skippe en silence. Le capturer ici,
#: dans le seul module que pytest charge pour ce fichier, ferme le trou.
REAL_HOME = Path.home()


def _free_port() -> int:
    """Un port haut libre, choisi par le noyau — pas par une constante optimiste."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return port


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists() if sys.platform == "linux" else True


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


@pytest.fixture(scope="session")
def served(real_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """`grimoire serve` sur un port haut, cockpit détourné, tué à la fin."""
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "serve",
            "--project-root", str(real_project),
            "--port", str(port),
            "--no-open",
        ],
        cwd=str(real_project),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        # Vérification, pas espoir : /proc dit si le processus est parti.
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture(scope="session")
def served_cockpit(
    real_project: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, str]]:
    """`grimoire cockpit serve` sur un port haut, avec le projet réel enrôlé.

    Lot 4 : Piloter a un niveau Flotte, et « écritures désactivées » (spec §5,
    « une interface partagée … prouve, par un test, que chaque route honore la
    cible ») n'a jamais été couvert côté cockpit avant ce lot. `--no-refresh`
    évite `gen-site-data.py` : la vue de travail lit `/api/health` et
    `/api/workspace/…`, jamais `data/projects.json`, donc rien n'a besoin
    d'être généré pour ce harnais.

    Dépend de `real_project` (portée session), pas de `project_with_task`
    (portée fonction) : une fixture session ne peut pas requérir une fixture
    plus étroite. La tâche est donc ouverte ici, idempotente comme le fait
    `project_with_task`.
    """
    from grimoire.missions.service import TaskService

    if not TaskService(real_project).has_ledger:
        subprocess.run(
            [
                sys.executable, "-m", "grimoire", "task", "add",
                "Vérifier la vue de travail (cockpit)",
                "-a", "Les six espaces s'ouvrent", "-a", "Aucune couleur hors tokens",
                "--owner", "winston",
            ],
            cwd=str(real_project), capture_output=True, text=True, check=False, timeout=180,
        )
    project_root = real_project
    port = _free_port()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-flotte")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    # Le cockpit adopte le projet du répertoire courant (#351). Ici le
    # répertoire courant est le dépôt du kit lui-même, qui est un projet
    # Grimoire : sans cette variable, il prendrait la place du projet que ce
    # harnais enrôle et veut afficher. L'adoption a ses propres tests.
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    added = subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(project_root)],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip(f"`grimoire cockpit add` n'a pas peuplé le registre : {added.stderr[-400:]}")
    import json

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slug = next((str(e.get("slug", "")) for e in registry if e.get("path") == str(project_root)), "")
    if not slug:
        pytest.skip("slug introuvable au registre du cockpit après `add`")

    process = subprocess.Popen(
        [
            sys.executable, "-m", "grimoire", "cockpit", "serve",
            "--port", str(port), "--no-open", "--no-refresh",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", slug
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def cockpit_workspace(browser: Browser, served_cockpit: tuple[str, str]) -> Iterator[Page]:
    """La coque, chargée sur le cockpit et ciblée sur le projet enrôlé."""
    served, slug = served_cockpit
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html?project={slug}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium, cherché là où il est réellement installé.

    ``tests/conftest.py`` détourne ``HOME`` pour qu'aucun test ne touche l'état
    réel du poste — et Playwright range ses navigateurs sous ``HOME``. Sans
    cette ligne, le harnais se skippe tout seul en annonçant « Chromium
    absent » alors qu'il est là : un faux vert de plus, exactement le mode de
    panne que ce dépôt traque.
    """
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(REAL_HOME / ".cache" / "ms-playwright")
    )
    with sync_playwright() as playwright:
        # `else` plutôt qu'un `yield` à la suite du `except` : `pytest.skip`
        # lève, mais rien dans sa signature ne le dit, et une analyse statique
        # lit donc `instance` comme possiblement non initialisée. La forme
        # ci-dessous rend l'affectation certaine pour un lecteur comme pour un
        # analyseur.
        try:
            instance = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover — navigateur non installé
            pytest.skip(f"Chromium absent : {exc} — `playwright install chromium`")
        else:
            try:
                yield instance
            finally:
                instance.close()


@pytest.fixture
def workspace(browser: Browser, served: str) -> Iterator[Page]:
    """La coque, chargée et prête. `data-ready` dit que l'amorçage est fini.

    ``reduced_motion="reduce"`` n'est pas une commodité : la coque déclare des
    transitions de 120 ms sur ``color``, et ``getComputedStyle`` pendant une
    transition rend la valeur **interpolée**. Mesurer le contraste juste après
    un changement de thème donnait alors des encres sombres sur des surfaces
    claires — un échec fabriqué par le harnais. La feuille honore déjà
    ``prefers-reduced-motion`` ; le test s'en sert pour mesurer un état stable,
    et couvre au passage le chemin d'accessibilité.
    """
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()
