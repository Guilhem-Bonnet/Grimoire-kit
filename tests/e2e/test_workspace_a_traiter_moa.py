"""« À traiter » et le parcours MOA/MOE (#1342), dans un vrai navigateur, sur un vrai cockpit.

Un projet avec gouvernance (MOA nommée, gate en ``warn``), une exigence non validée portée par une tâche prête et une
exigence validée dont la tâche est livrée : le cockpit dit qui doit agir, donne la commande exacte et ne propose aucun
geste qui valide ou recette (ces gestes exigent un terminal interactif humain).

Le projet se monte par l'API Python (le « oui » de la MOA est simulé à l'écran, comme dans les tests unitaires) ; le
journal d'autorité vit sous ``HOME``, que la suite détourne par test : le serveur hérite du même ``HOME``.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.missions.service import TaskService

from .conftest import _alive, _free_port, _wait_ready
from .test_workspace_a_traiter_p32 import _grimoire, _init

NAME = "Atelier Cadre"
EXIGENCES = """# Exigences

## Fonctionnelles

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 (Should) : export CSV des commandes
  - le fichier s'ouvre dans un tableur
"""
GOVERNANCE = "\ngovernance:\n  roles:\n    moa: Ada Lovelace\n  requirement_gate: warn\n"


@pytest.fixture
def served_moa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    import os

    for name in ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path / "cockpit-home")
    env["NO_COLOR"] = "1"

    home = tmp_path / NAME
    _init(home, NAME, env)
    context = home / "project-context.yaml"
    context.write_text(context.read_text(encoding="utf-8") + GOVERNANCE, encoding="utf-8")
    (home / CADRAGE_DIR).mkdir(parents=True, exist_ok=True)
    (home / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")

    rg.approve_requirement(home, "REQ-001", actor="ada")
    service = TaskService(home)
    delivered = service.add("Connexion par lien", ("le lien fonctionne",), requirement="REQ-001").task.id
    service.ledger.close_merged_externally(delivered, pr=12, merged_sha="a" * 40)
    service.add("Export CSV", ("le fichier s'ouvre",), requirement="REQ-002", ready=True)

    _grimoire(tmp_path, "cockpit", "add", str(home), env=env)
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--project-root", str(home),
         "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(home), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def test_a_traiter_dit_qui_valide_et_qui_recette(page: Page, served_moa: str) -> None:
    page.goto(f"{served_moa}/workspace/index.html#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_selector(".at-row[data-kind='requirement_recette']")

    recette = page.locator(".at-row[data-kind='requirement_recette']")
    expect(recette.locator(".at-chip")).to_have_text("À recetter par la MOA")
    expect(recette.locator(".at-reason")).to_contain_text("La MOA recette depuis son terminal")
    recette.click()
    detail = page.locator(".at-detail [data-role='moa-guide']")
    expect(detail).to_have_attribute("data-stage", "recette")
    expect(detail).to_contain_text("La MOA")
    expect(detail.locator("[data-role='moa-command']")).to_have_text("grimoire cadrage recette REQ-001")
    expect(detail.locator("[data-action='copy-command']")).to_have_count(1)

    unvalidated = page.locator(".at-row[data-kind='requirement_unvalidated']")
    expect(unvalidated.locator(".at-chip")).to_have_text("À valider par la MOA")
    unvalidated.click()
    detail = page.locator(".at-detail [data-role='moa-guide']")
    expect(detail).to_have_attribute("data-stage", "validation")
    expect(detail.locator("[data-role='moa-command']")).to_have_text("grimoire cadrage approve REQ-002")
    expect(page.locator(".at-detail")).to_contain_text("seule la MOA le fait")

    # Aucun bouton ne valide ni ne recette depuis le cockpit.
    labels = " ".join(page.locator(".at-detail button, .at-detail a.btn").all_inner_texts()).lower()
    assert "valider" not in labels and "recetter" not in labels
    assert not page.locator(".at-detail [data-action='approve'], .at-detail [data-action='recette']").count()
