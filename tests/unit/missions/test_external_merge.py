"""#1162 : une PR ``task/<id>`` fusionnée hors du merger solde sa tâche au tick suivant.

La file du merger ne voit que les tâches lancées par le tick et arrivées en revue. Une fusion faite à la main
(``gh pr merge``) laissait donc la tâche ouverte : elle repassait ``ready``/``claimed`` et un nouveau worker la
reprenait. Ici, le constat précède l'admission : la tâche passe ``closed``, sans ``review_launched`` ni nouveau
``session_launched``, et la session vivante est arrêtée par le janitor du même tick.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo

from grimoire.missions import mandate as md
from grimoire.missions.external_merge import EXTERNAL_MERGED_EVENT
from grimoire.missions.launcher import LAUNCHED_EVENT, launched_sessions
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.reviewer import REVIEW_LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import TASK_BRANCH_PREFIX, find_task_worktree

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session héritée du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        "      permission_mode: acceptEdits\n    reviewer:\n      enabled: true\n      max_cycles: 2\n"
        "      permission_mode: acceptEdits\n",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    return root


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def launch(projet: Path, host: FakeHost) -> str:
    """Une tâche créée ``ready`` puis lancée par le tick : worktree, claim et session de fond."""
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Fusion externe", origin="test")
    task = service.ledger.create_task(mission.id, "déjà livrée", acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    report = run_tick(projet, apply=True, exec_=host)
    assert [o.status for o in report.launches] == ["launched"], report.launches
    return task.id


def in_review(projet: Path, tid: str) -> None:
    ledger = TaskService(projet).ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")


def merged(host: FakeHost, tid: str, *, sha: str = SHA) -> None:
    branch = TASK_BRANCH_PREFIX + tid
    host.prs[branch] = "MERGED"
    host.merge_commits[branch] = sha


def kinds(projet: Path, tid: str) -> list[str]:
    return [e.event_type for e in TaskService(projet).ledger.events_for(tid)]


def status_of(projet: Path, tid: str) -> TaskState:
    task = TaskService(projet).ledger.get_task(tid)
    assert task is not None
    return task.status


def test_une_tache_en_revue_dont_la_pr_est_fusionnee_est_close_sans_relecture(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    in_review(projet, tid)
    merged(host, tid)
    before = kinds(projet, tid)

    report = run_tick(projet, apply=True, exec_=host)

    assert status_of(projet, tid) is TaskState.CLOSED
    assert [o.status for o in report.external_merges] == ["done"]
    after = kinds(projet, tid)
    assert after.count(LAUNCHED_EVENT) == before.count(LAUNCHED_EVENT)  # aucun worker relancé
    assert after.count(REVIEW_LAUNCHED_EVENT) == before.count(REVIEW_LAUNCHED_EVENT) == 0  # aucun relecteur
    assert tid not in report.dispatched


def test_le_motif_nomme_la_pr_et_le_sha_de_fusion(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    in_review(projet, tid)
    merged(host, tid)

    run_tick(projet, apply=True, exec_=host)

    events = TaskService(projet).ledger.events_for(tid)
    proof = [e for e in events if e.event_type == EXTERNAL_MERGED_EVENT]
    assert len(proof) == 1 and proof[0].payload["pr"] == 7 and proof[0].payload["merged_sha"] == SHA
    closed = [e for e in events if e.event_type == "task.transitioned" and e.payload.get("to_state") == "closed"]
    assert len(closed) == 1
    assert "PR #7" in closed[0].payload["reason"] and SHA[:12] in closed[0].payload["reason"]


def test_une_tache_claimed_dont_la_pr_est_fusionnee_est_close_et_sa_session_arretee(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    assert status_of(projet, tid) is TaskState.CLAIMED
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None and wt.path.is_dir()
    session = launched_sessions(TaskService(projet))[tid]["session"]
    merged(host, tid)
    before = kinds(projet, tid)

    report = run_tick(projet, apply=True, exec_=host)

    assert status_of(projet, tid) is TaskState.CLOSED
    assert [o.status for o in report.external_merges] == ["done"]
    assert kinds(projet, tid).count(LAUNCHED_EVENT) == before.count(LAUNCHED_EVENT)
    assert ["claude", "stop", session] in host.of("claude", "stop")  # le janitor arrête la session vivante
    assert tid not in report.dispatched


def test_une_pr_ouverte_ne_clot_rien(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    host.prs[TASK_BRANCH_PREFIX + tid] = "OPEN"

    report = run_tick(projet, apply=True, exec_=host)

    assert status_of(projet, tid) is TaskState.CLAIMED
    assert not report.external_merges
    assert EXTERNAL_MERGED_EVENT not in kinds(projet, tid)


def test_une_fusion_sans_sha_lisible_ne_clot_rien(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    host.prs[TASK_BRANCH_PREFIX + tid] = "MERGED"  # pas de mergeCommit : réponses partielles ou ambiguës

    report = run_tick(projet, apply=True, exec_=host)

    assert status_of(projet, tid) is TaskState.CLAIMED
    assert [o.status for o in report.external_merges] == ["failed"]
    assert any("fusion externe" in line for line in report.attention)
    assert EXTERNAL_MERGED_EVENT not in kinds(projet, tid)


def test_le_constat_ne_rejoue_rien_au_tick_suivant(projet: Path, host: FakeHost) -> None:
    tid = launch(projet, host)
    in_review(projet, tid)
    merged(host, tid)
    run_tick(projet, apply=True, exec_=host)

    report = run_tick(projet, apply=True, exec_=host)

    assert status_of(projet, tid) is TaskState.CLOSED
    assert not report.external_merges
    assert kinds(projet, tid).count(EXTERNAL_MERGED_EVENT) == 1
