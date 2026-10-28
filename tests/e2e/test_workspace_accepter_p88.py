"""Travail › « Accepter… » dans l'inspecteur d'une tâche en Revue (P88).

Le geste humain de #865 (`grimoire task accept --reason`) avait sa CLI mais pas son bouton. Ici le motif est
obligatoire (Confirmer reste désactivé sans lui), le geste part par la vraie route (`POST tasks/<id>/accept`,
canal humain du cockpit) et l'effet se relit au ledger et au dépôt de preuves.
"""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService

from .conftest import _serve_empty_project


@pytest.fixture(scope="module")
def projet_accepter(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """Un projet DÉDIÉ : accepter ferme une tâche, et la clôture entre dans la mémoire du projet, d'où le rappel
    des autres tâches l'affiche. Dans `real_project`, partagé par toute la suite, elle fausserait leurs tests."""
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "workspace-accepter", roots):
        yield url, roots[0]


@pytest.fixture
def workspace(browser: Browser, projet_accepter: tuple[str, Path]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="dark")
    page = context.new_page()
    page.goto(f"{projet_accepter[0]}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


@pytest.fixture
def real_project(projet_accepter: tuple[str, Path]) -> Path:
    return projet_accepter[1]


MOTIF = "PR relue ; la preuve suffit, dérogation sur le critère 3"


def _en_revue(root: Path, titre: str) -> str:
    """Une tâche à preuve déclarative, passée en revue — la mise en place est celle de test_task_accept."""
    # Mise en place par le ledger (comme test_workspace_decisions_p68) : le projet e2e est partagé par toute la
    # session, activer un fournisseur dans son registre changerait le comportement des autres tests.
    service = TaskService(root)
    tid = service.add(titre, ("la PR est relue",), owner="guilhem", actor="claude", ready=True).task.id
    for state in (TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("claude", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="claude", claim=claim)
    # Le pack déclaratif est rangé directement : `task verify` sort en code 4 (verdict `skipped`) et laisse une
    # trace de mémoire que le rappel des autres tâches du projet partagé reprendrait.
    (root / "preuve.md").write_text("# PR relue\n", encoding="utf-8")
    item = EvidenceItem("doc-01", EvidenceKind.DOC, "file:preuve.md", f"sha256-{sha256(b'preuve').hexdigest()}", "PR relue")
    EvidenceService(root / "_grimoire-runtime-output" / "evidence").create_pack(
        tid, EvidenceProfile.STANDARD, [item], acceptance=("la PR est relue",),
    )
    service.ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="claude")
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
