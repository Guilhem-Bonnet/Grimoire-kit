"""Le badge « checkpoint destructif en attente » survit à une navigation
Flotte → Projet (restes #506/#510, validation finale de la boucle de mise à
jour, issue #511/#513).

Avant ce correctif, `markKitCheckpointPending()` (piloter.js) était un état
purement client, posé une fois après le clic « Confirmer la mise à jour » —
perdu à la première navigation qui redessine la fiche (Flotte, puis retour au
Projet). Ce harnais ne rejoue pas le flow complet (lent, touche `doctor`/les
hooks) : il fabrique directement, via `FlowEngine`, un run persistant dont le
blueprint porte l'id `project-upgrade` et le kernel dit « checkpointé au node
`destructive` » — exactement ce que `GET /api/workspace/flows/runs` (#513)
rend une fois `list_flow_runs` augmenté de `status`/`currentNode`
(`grimoire.tools.flow_runs`). Le test observe seulement ce que la fiche en
fait : le badge doit apparaître au premier rendu ET après un aller-retour par
la Flotte, jamais seulement juste après un clic.
"""

from __future__ import annotations

import os
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

#: Le petit programme qui fabrique l'état — deux nodes, `project-upgrade`
#: comme id de blueprint (jamais le vrai blueprint du registre, inutilement
#: lourd pour ce que ce test observe), avancé jusqu'au checkpoint sur le
#: second node, nommé `destructive` pour matcher exactement ce que la fiche
#: Piloter regarde (`currentNode === 'destructive'`).
_FABRICATE_CHECKPOINTED_RUN = """
import io
import json
import sys
from pathlib import Path

from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor

root = Path(sys.argv[1])
blueprint = {
    "blueprintVersion": 1,
    "id": "project-upgrade",
    "name": "Project upgrade (doublure de test)",
    "nodes": [
        {
            "id": "backup", "kind": "pattern", "ref": "ORC-01", "label": "Backup",
            "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
        },
        {
            "id": "destructive", "kind": "pattern", "ref": "QUA-04", "label": "Destructif",
            "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
        },
    ],
    "edges": [{"from": "backup.out", "to": "destructive.in", "contract": "c1"}],
}
bp_path = root / "fake-project-upgrade.blueprint.json"
bp_path.write_text(json.dumps(blueprint), encoding="utf-8")

engine = FlowEngine(
    kernel_root=root / "_grimoire-runtime-output" / "runtime",
    flows_root=root / "_grimoire-runtime-output" / "flows",
    project_root=root,
)
wfi, _ = engine.run(bp_path, executor=InteractiveNodeExecutor(stream=io.StringIO()))
engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
print(wfi.id)
"""


def _free_port() -> int:
    import socket

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


@pytest.fixture(scope="session")
def served_upgrade_checkpoint(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, str]]:
    """Un cockpit avec un projet dédié dont le dernier run `project-upgrade`

    est déjà checkpointé au node `destructive` — état fabriqué directement
    (voir :data:`_FABRICATE_CHECKPOINTED_RUN`), jamais rejoué via le vrai
    flow (lent, hors sujet ici)."""
    root = tmp_path_factory.mktemp("upgrade-checkpoint") / "projet-checkpoint-badge"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    init = subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-checkpoint-badge"],
        cwd=str(root), env=env, capture_output=True, text=True, check=False, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")

    fabricate = subprocess.run(
        [sys.executable, "-c", _FABRICATE_CHECKPOINTED_RUN, str(root)],
        cwd=str(root), env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    if fabricate.returncode != 0:
        pytest.skip(f"fabrication du run checkpointé impossible : {fabricate.stderr[-800:]}")

    port = _free_port()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-checkpoint-badge")
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    added = subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip(f"`grimoire cockpit add` n'a pas peuplé le registre : {added.stderr[-400:]}")
    import json

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slug = next((str(e.get("slug", "")) for e in registry if e.get("path") == str(root)), "")
    if not slug:
        pytest.skip("slug introuvable au registre du cockpit après `add`")

    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
def checkpoint_badge_workspace(browser: Browser, served_upgrade_checkpoint: tuple[str, str]) -> Iterator[Page]:
    served, _slug = served_upgrade_checkpoint
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


_BADGE_TEXT = "mis à niveau, checkpoint destructif en attente"


def test_the_checkpoint_badge_survives_a_fleet_then_project_navigation(
    checkpoint_badge_workspace: Page, served_upgrade_checkpoint: tuple[str, str],
) -> None:
    page = checkpoint_badge_workspace

    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    page.get_by_role("button", name="Flotte").click()
    page.wait_for_selector(".pl-table-wrap")

    row = page.locator(".pl-table tbody tr", has_text="projet-checkpoint-badge")
    row.wait_for(timeout=15_000)
    row.click()

    # Premier rendu de la fiche : le badge doit déjà être là, dérivé du
    # backend (`GET /api/workspace/flows/runs`) — jamais un état posé
    # seulement après un clic « Confirmer ».
    page.wait_for_selector(f"text={_BADGE_TEXT}", timeout=15_000)

    # Aller-retour Flotte → Projet, dans la même session navigateur : c'est
    # exactement le rechargement de fiche qui perdait le badge avant ce
    # correctif (`renderSheet` reconstruit tout l'inspecteur à chaque appel).
    page.get_by_role("button", name="Flotte").click()
    page.wait_for_selector(".pl-table-wrap")
    row = page.locator(".pl-table tbody tr", has_text="projet-checkpoint-badge")
    row.click()

    page.wait_for_selector(f"text={_BADGE_TEXT}", timeout=15_000)
