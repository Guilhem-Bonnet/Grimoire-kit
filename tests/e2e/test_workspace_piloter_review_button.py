"""Le bouton « Revoir dans l'IDE » de Piloter (issue #520, lot 2).

`grimoire upgrade-flow run` s'arrête volontairement, non décidé, au
checkpoint `destructive` ou sur une proposition V1 — rien depuis le cockpit
n'aidait jusqu'ici à ouvrir la revue de ces décisions ailleurs que par la CLI
à la main (le lot 1, PR précédente, a livré le skill/prompt
`upgrade-review` et `grimoire upgrade-flow review`). Ce bouton ne fait que
préparer et exposer le texte que le prompt `/grimoire-upgrade-review`
attend — il n'écrit jamais rien, et n'est présent dans la page que quand il y
a un travail réel à revoir.

Trois scénarios, trois fixtures dédiées (comme
``test_workspace_piloter_upgrade_checkpoint_badge.py``) : un projet neuf sans
rien en attente (le bouton doit être absent), un projet avec une seule
proposition en attente et aucun checkpoint (le bouton doit apparaître), et un
projet dont le dernier run `project-upgrade` est checkpointé au node
`destructive` (même fabrication directe via ``FlowEngine`` que le test du
badge — rejouer le vrai flow serait lent et hors sujet ici).
"""

from __future__ import annotations

import json
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

_WRITE_PENDING_PROPOSAL = """
import sys
from pathlib import Path

from grimoire.proposals import create_manual_proposal

root = Path(sys.argv[1])
create_manual_proposal(
    root,
    slug="override-migration-agent-x",
    specialty="override en derive : agent-x",
    artifact_type="override-migration",
    category="override-drift",
)
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


def _init_project(root: Path, name: str) -> subprocess.CompletedProcess[str]:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", name],
        cwd=str(root), env=env, capture_output=True, text=True, check=False, timeout=180,
    )


def _serve(root: Path, tmp_path_factory: pytest.TempPathFactory, label: str) -> Iterator[tuple[str, str]]:
    """Enregistre *root* au cockpit et le sert — même patron que le test du badge."""
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    port = _free_port()
    cockpit_home = tmp_path_factory.mktemp(f"cockpit-home-{label}")
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    added = subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip(f"`grimoire cockpit add` n'a pas peuplé le registre : {added.stderr[-400:]}")
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


@pytest.fixture(scope="session")
def served_idle_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Un projet neuf, sans run et sans proposition — le bouton doit être absent."""
    root = tmp_path_factory.mktemp("review-btn-idle") / "projet-review-idle"
    init = _init_project(root, "projet-review-idle")
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    yield from _serve(root, tmp_path_factory, "idle")


@pytest.fixture(scope="session")
def served_pending_proposal_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Un projet avec une seule proposition en attente, aucun checkpoint — le bouton doit apparaître."""
    root = tmp_path_factory.mktemp("review-btn-proposal") / "projet-review-proposal"
    init = _init_project(root, "projet-review-proposal")
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    write = subprocess.run(
        [sys.executable, "-c", _WRITE_PENDING_PROPOSAL, str(root)],
        cwd=str(root), env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    if write.returncode != 0:
        pytest.skip(f"écriture de la proposition impossible : {write.stderr[-800:]}")
    yield from _serve(root, tmp_path_factory, "proposal")


@pytest.fixture(scope="session")
def served_checkpoint_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Un projet dont le dernier run `project-upgrade` est checkpointé au node `destructive`."""
    root = tmp_path_factory.mktemp("review-btn-checkpoint") / "projet-review-checkpoint"
    init = _init_project(root, "projet-review-checkpoint")
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    fabricate = subprocess.run(
        [sys.executable, "-c", _FABRICATE_CHECKPOINTED_RUN, str(root)],
        cwd=str(root), env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    if fabricate.returncode != 0:
        pytest.skip(f"fabrication du run checkpointé impossible : {fabricate.stderr[-800:]}")
    run_id = fabricate.stdout.strip().splitlines()[-1] if fabricate.stdout.strip() else ""
    yield from _served_with_run_id(root, tmp_path_factory, run_id)


def _served_with_run_id(
    root: Path, tmp_path_factory: pytest.TempPathFactory, run_id: str
) -> Iterator[tuple[str, str, str]]:
    for served, slug in _serve(root, tmp_path_factory, "checkpoint"):
        yield served, slug, run_id


def _open_project(page: Page, served: str, project_name: str) -> None:
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    page.get_by_role("button", name="Flotte").click()
    page.wait_for_selector(".pl-table-wrap")
    row = page.locator(".pl-table tbody tr", has_text=project_name)
    row.wait_for(timeout=15_000)
    row.click()
    page.wait_for_selector("h2:has-text('" + project_name + "')", timeout=15_000)


def test_the_review_button_is_absent_with_nothing_pending(
    browser: Browser, served_idle_project: tuple[str, str],
) -> None:
    served, _slug = served_idle_project
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        _open_project(page, served, "projet-review-idle")
        page.wait_for_selector(".pl-actions")
        assert page.locator("button", has_text="Revoir dans l'IDE").count() == 0
    finally:
        context.close()


def test_the_review_button_appears_with_a_pending_proposal(
    browser: Browser, served_pending_proposal_project: tuple[str, str],
) -> None:
    served, _slug = served_pending_proposal_project
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    try:
        _open_project(page, served, "projet-review-proposal")
        btn = page.locator("button", has_text="Revoir dans l'IDE")
        btn.wait_for(timeout=15_000)
        btn.click()

        page.wait_for_selector("text=Copié dans le presse-papiers")
        clipboard_text = page.evaluate("() => navigator.clipboard.readText()")
        assert clipboard_text.startswith("/grimoire-upgrade-review")
        assert "override-migration-agent-x" in clipboard_text

        preview_code = page.locator(".pl-review-preview code").first
        assert "override-migration-agent-x" in preview_code.inner_text()
    finally:
        context.close()


def test_the_review_button_appears_with_a_pending_checkpoint_and_names_the_run(
    browser: Browser, served_checkpoint_project: tuple[str, str, str],
) -> None:
    served, _slug, run_id = served_checkpoint_project
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    try:
        _open_project(page, served, "projet-review-checkpoint")
        btn = page.locator("button", has_text="Revoir dans l'IDE")
        btn.wait_for(timeout=15_000)
        btn.click()

        page.wait_for_selector("text=Copié dans le presse-papiers")
        clipboard_text = page.evaluate("() => navigator.clipboard.readText()")
        assert clipboard_text.startswith("/grimoire-upgrade-review")
        if run_id:
            assert run_id in clipboard_text

        assert page.locator("text=Aucun IDE ouvrable déclaré").count() == 1
        assert page.locator("code", has_text="grimoire upgrade-flow review").count() >= 1
    finally:
        context.close()


def test_the_review_button_falls_back_to_manual_copy_when_the_clipboard_is_unavailable(
    browser: Browser, served_pending_proposal_project: tuple[str, str],
) -> None:
    """`reviewBtn` (``piloter.js``) catche l'échec de `navigator.clipboard.
    writeText` (permission refusée, contexte non sécurisé, API absente) et
    affiche le texte à copier à la main plutôt que de rester silencieux —
    seul le scénario « copié » (permission accordée) était couvert jusqu'ici.
    Un contexte SANS permission clipboard-write, plus une redéfinition de
    `navigator.clipboard.writeText` en promesse rejetée, rend ce refus
    déterministe (le seul refus de permission ne l'est pas de façon fiable
    en Chromium headless)."""
    served, _slug = served_pending_proposal_project
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', { value: "
        "{ writeText: () => Promise.reject(new Error('denied')) }, configurable: true });"
    )
    try:
        _open_project(page, served, "projet-review-proposal")
        btn = page.locator("button", has_text="Revoir dans l'IDE")
        btn.wait_for(timeout=15_000)
        btn.click()

        page.wait_for_selector("text=Presse-papiers indisponible")
        assert page.locator("text=Copié dans le presse-papiers").count() == 0
        preview_code = page.locator(".pl-review-preview code").first
        assert preview_code.inner_text().startswith("/grimoire-upgrade-review")
        assert "override-migration-agent-x" in preview_code.inner_text()
    finally:
        context.close()
