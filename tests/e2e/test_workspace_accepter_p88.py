"""Travail › « Accepter… » dans l'inspecteur d'une tâche en Revue (P88).

Le geste humain de #865 (`grimoire task accept --reason`) avait sa CLI mais pas son bouton. Ici le motif est
obligatoire (Confirmer reste désactivé sans lui), le geste part par la vraie route (`POST tasks/<id>/accept`,
canal humain du cockpit) et l'effet se relit au ledger et au dépôt de preuves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, expect

from grimoire.evidence.service import EvidenceService
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

MOTIF = "PR relue ; la preuve suffit, dérogation sur le critère 3"


def _en_revue(root: Path, titre: str) -> str:
    """Une tâche à preuve déclarative, passée en revue — la mise en place est celle de test_task_accept."""
    registry = root / "_grimoire" / "standard" / "llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    service = TaskService(root)
    tid = service.add(titre, ("la PR est relue",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude")
    service.transition(tid, TaskState.RUNNING, "claude")
    (root / "preuve.md").write_text("# PR relue\n", encoding="utf-8")
    trace = root / "_grimoire-output" / "decisions" / tid / "decision-trace.yaml"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("decisions: []\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "task", "verify", tid, "--project-root", str(root), "--evidence", "preuve.md"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude")
    return tid


def _ouvrir(page: Page, titre: str) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('executer')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card")
    page.locator(".ex-card").filter(has_text=titre).first.click()


def test_accepter_avec_un_motif_ferme_la_tache_et_l_inscrit(workspace: Page, real_project: Path) -> None:
    titre = "Accepter depuis le cockpit"
    tid = _en_revue(real_project, titre)
    _ouvrir(workspace, titre)
    inspector = workspace.locator("#inspector-body")
    inspector.get_by_text(tid, exact=True).wait_for()

    inspector.locator("[data-role='accept-open']").click()
    motif = inspector.locator("[data-role='accept-reason']")
    confirmer = inspector.locator("[data-role='accept-confirm']")
    expect(confirmer).to_be_disabled()  # sans motif, rien à confirmer
    motif.fill("   ")
    expect(confirmer).to_be_disabled()
    motif.fill(MOTIF)
    expect(confirmer).to_be_enabled()
    with workspace.expect_response(lambda r: f"/tasks/{tid}/accept" in r.url) as reponse:
        confirmer.click()
    assert reponse.value.status == 200
    assert TaskService(real_project).require(tid).status is TaskState.CLOSED
    verdicts = EvidenceService(real_project / "_grimoire-runtime-output" / "evidence").list_verdicts(tid)
    assert verdicts[-1].checks[0].reason == MOTIF
    # La carte passe en Acceptée (colonne terminale).
    expect(workspace.locator(".ex-col[data-group='done'] .ex-card").filter(has_text=titre)).to_have_count(1)


def test_le_bouton_n_apparait_que_dans_la_colonne_revue(workspace: Page, real_project: Path) -> None:
    titre = "Pas encore en revue"
    service = TaskService(real_project)
    service.add(titre, ("critère",), owner="guilhem", actor="claude", ready=True)
    _ouvrir(workspace, titre)
    inspector = workspace.locator("#inspector-body")
    inspector.get_by_text(titre).first.wait_for()
    expect(inspector.locator("[data-role='accept-open']")).to_have_count(0)
    expect(inspector.locator("[data-role='accept-block']")).to_have_count(0)
