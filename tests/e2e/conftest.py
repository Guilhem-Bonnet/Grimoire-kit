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
import re
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


#: Gates minimales pour amener une tâche jusqu'à la porte « review » sans
#: dépendre du template `governed` du standard (qui exige un context bundle et
#: un fournisseur activé dès `ready -> in_progress` — voir `served_cockpit`) :
#: seule la porte qui nous intéresse est déclarée, les autres passent libres.
#: Même forme que `tests/unit/cli/test_cmd_task_write.py::GATES`.
_REVIEW_GATE_YAML = """\
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
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate"]
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture(scope="session")
def served_review_gate(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """`grimoire serve` sur un projet dédié, une tâche déjà amenée en
    ``running`` (``in_progress`` côté board) — la porte suivante, ``review``,
    exige un evidence pack qu'aucune commande n'a produit ici.

    Un projet à part, pas ``real_project`` : les gates du template `governed`
    y bloquent déjà `ready -> in_progress` faute de context bundle et de
    fournisseur (voir le commentaire de
    `test_executer_un_move_reussi_deplace_la_carte_puis_un_claim_est_refuse`),
    donc aucune tâche n'y atteint jamais `running`. Le critère d'acceptation
    de #140 nomme `review` précisément : ce harnais amène la tâche jusqu'à
    cette porte par CLI (même mécanique que `TaskService`, aucun raccourci qui
    contournerait le gate), pour que seul le dernier geste — cliquer
    « Réaliser » vers Revue — soit observé dans le navigateur.
    """
    root = tmp_path_factory.mktemp("review-gate") / "projet-review-gate"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-review-gate"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    gates_path = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates_path.parent.mkdir(parents=True, exist_ok=True)
    gates_path.write_text(_REVIEW_GATE_YAML, encoding="utf-8")

    added = subprocess.run(
        [
            sys.executable, "-m", "grimoire", "task", "add", "Faire une revue sans preuve",
            "-a", "un critère observable", "--owner", "winston",
        ],
        cwd=str(root), capture_output=True, text=True, check=False, timeout=180,
    )
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    task_id = match.group(1)
    for step in (
        ["task", "move", task_id, "--to", "ready"],
        ["task", "claim", task_id],
        ["task", "move", task_id, "--to", "running"],
    ):
        outcome = subprocess.run(
            [sys.executable, "-m", "grimoire", *step],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=180,
        )
        if outcome.returncode != 0:
            pytest.skip(f"préparation de la tâche interrompue à {step} : {outcome.stderr[-400:]}")

    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-review"))
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
        yield f"http://127.0.0.1:{port}", task_id
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def review_gate_workspace(browser: Browser, served_review_gate: tuple[str, str]) -> Iterator[Page]:
    """La coque, chargée sur le projet dédié de :func:`served_review_gate`."""
    served, _task_id = served_review_gate
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


@pytest.fixture(scope="session")
def served_timeline(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Un projet dédié (#139) : une tâche avec un dispatch d'agent et une
    transition refusée déjà journalisés au TraceLedger avant que le
    navigateur n'ouvre quoi que ce soit — même mécanique que
    :func:`served_review_gate`, pour que le harnais observe seulement, sans
    dépendre de l'ordre des tests sur un projet partagé.
    """
    root = tmp_path_factory.mktemp("timeline") / "projet-timeline"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-timeline"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )

    added = subprocess.run(
        [
            sys.executable, "-m", "grimoire", "task", "add", "Tracer la timeline",
            "-a", "un critère observable", "--owner", "winston",
        ],
        cwd=str(root), capture_output=True, text=True, check=False, timeout=180,
    )
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    task_id = match.group(1)

    subprocess.run(
        [sys.executable, "-m", "grimoire", "task", "move", task_id, "--to", "ready"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    # Refus attendu du profil `governed` (pas de context bundle, aucun
    # fournisseur activé au registre) : c'est la transition refusée que le
    # test observe, la même mécanique que
    # `test_executer_un_move_reussi_deplace_la_carte_puis_un_claim_est_refuse`.
    subprocess.run(
        [sys.executable, "-m", "grimoire", "task", "claim", task_id],
        cwd=str(root), capture_output=True, text=True, check=False, timeout=180,
    )
    # Dispatch d'agent (#366/#389) : écrit par `hosts.decisions.activation` à
    # l'activation d'une session, hors du chemin CLI d'une tâche — reproduit
    # ici directement, comme le ferait un hook `SessionStart`.
    subprocess.run(
        [
            sys.executable, "-c",
            "import sys\n"
            "from pathlib import Path\n"
            "from grimoire.hosts.decisions.record import _record_agent_dispatch\n"
            "_record_agent_dispatch(Path(sys.argv[1]), 'grimoire-master', sys.argv[2])\n",
            str(root), task_id,
        ],
        cwd=str(root), capture_output=True, text=True, check=False, timeout=60,
    )

    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-timeline"))
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
        yield f"http://127.0.0.1:{port}", task_id
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def timeline_workspace(browser: Browser, served_timeline: tuple[str, str]) -> Iterator[Page]:
    """La coque, chargée sur le projet dédié de :func:`served_timeline`."""
    served, _task_id = served_timeline
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


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


@pytest.fixture(scope="session")
def served_cockpit_multi(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, str, str]]:
    """Un cockpit qui sert DEUX projets réels, chacun sa tâche reconnaissable.

    Issue #140, critère d'acceptation : « le board change quand on change de
    projet ». `served_cockpit` ne prouve qu'une route répond 200 pour le
    projet demandé (lot 4) ; aucun harnais n'avait encore deux boards
    distincts à comparer.

    Deux projets **dédiés** — pas ``real_project``/``second_project`` : ces
    fixtures de session sont partagées avec `project_with_task` (ailleurs
    dans ce lot), qui prend la première tâche du ledger par position. Y
    ajouter une tâche ici déciderait silencieusement laquelle est « la
    première » pour tous les autres tests de la suite qui la lisent par
    contenu (« Vérifier la vue de travail »).
    """
    import json

    projects: dict[Path, str] = {
        tmp_path_factory.mktemp("board-switch-a") / "projet-board-a": "Tâche du projet A — visible seulement ici (#140)",
        tmp_path_factory.mktemp("board-switch-b") / "projet-board-b": "Tâche du projet B — visible seulement ici (#140)",
    }
    for root, title in projects.items():
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", root.name],
            cwd=str(root), check=False, capture_output=True, timeout=180,
        )
        if not (root / "_grimoire" / "kit").is_dir():
            pytest.skip("`grimoire init` indisponible ici")
        subprocess.run(
            [
                sys.executable, "-m", "grimoire", "task", "add", title,
                "-a", "distincte du board de l'autre projet", "--owner", "winston",
            ],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=180,
        )

    project_a, project_b = projects.keys()
    port = _free_port()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-multi")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    # Comme `served_cockpit` : le cwd de ce harnais est le dépôt du kit
    # lui-même, un projet Grimoire qu'on ne veut pas voir adopté à la place
    # des deux projets qu'on enrôle explicitement ci-dessous.
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    for root in (project_a, project_b):
        subprocess.run(
            [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
            env=env, capture_output=True, text=True, check=False, timeout=60,
        )
    registry_path = cockpit_home / "registry.json"
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
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
