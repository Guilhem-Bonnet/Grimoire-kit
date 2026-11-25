"""GAO-test-de-bout-001 : un seul scénario chaîne une tâche de ``ready`` à ``closed``.

Chaque étape du tick est testée seule (``test_launcher.py``, ``test_reviewer.py``, ``test_merger.py``,
``test_janitor.py``) ; ce test ne fabrique aucun état d'étape pour la suivante. Il ne fait que les gestes
qu'un worker et un relecteur feraient (commit, push, PR, reçu READY, passage en revue), puis laisse le
tick enchaîner le lancement, la relecture READY, la fusion, l'acceptation, la clôture et le nettoyage,
sous mandat et avec ``merge`` et ``accept`` délégués à l'orchestrateur.

Le doublage est celui des lots 2 à 4 (``lot2_doubles.py``) : un faux ``claude``, un faux ``gh`` et un
vrai dépôt git. Le test affirme la suite ordonnée des événements du ledger et l'absence de toute question
posée à l'humain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git, make_repo

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions import mandate as md
from grimoire.missions.janitor import SWEPT_EVENT
from grimoire.missions.launcher import WORKER_HALTED_EVENT, launched_sessions
from grimoire.missions.mandate import ORIGIN_HUMAN, MandateRequest, MandateStore
from grimoire.missions.merger import (
    MERGE_ACCEPTED_EVENT,
    MERGE_CI_EVENT,
    MERGE_QUEUED_EVENT,
    MERGE_REQUESTED_EVENT,
)
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.questions import records_of
from grimoire.missions.review_receipt import write_receipt
from grimoire.missions.reviewer import REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT, WORKTREE_CREATED_EVENT

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
REPO = "acme/kit"
CRITERIA = ("la route rend 404 sur un id inconnu", "relu par un autre agent")
#: La CI verte du scénario ; ``codecov/patch`` est ignoré par défaut, comme sur le vrai dépôt.
GREEN = [{"name": "tests", "bucket": "pass"}, {"name": "codecov/patch", "bucket": "fail"}]
CHANGELOG_MAIN = (
    "# Changelog\n\n## [Unreleased]\n\n- feat: une entrée de main\n\n## [1.0.0]\n\n"
    + "".join(f"- livré {n}\n" for n in range(1, 9))
    + "- init\n"
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session héritée du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path) -> None:
    """Worker, relecteur et merger activés ; ``merge`` et ``accept`` délégués à l'orchestrateur."""
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        "      permission_mode: acceptEdits\n    reviewer:\n      enabled: true\n      wip: 3\n"
        "      permission_mode: acceptEdits\n    merger:\n      enabled: true\n"
        "escalation:\n  merge: orchestrator\n  accept: orchestrator\n"
        f"ci:\n  repository: {REPO}\n",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    """Le dépôt du projet : un CHANGELOG, un .gitignore des sorties du kit, et la politique complète."""
    root = make_repo(tmp_path)
    (root / ".gitignore").write_text("_grimoire-output/\n_grimoire-runtime-output/\n_grimoire/\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG_MAIN, encoding="utf-8")
    git(root, "add", "--", ".gitignore", "CHANGELOG.md")
    git(root, "commit", "-q", "-m", "gitignore et changelog")
    git(root, "push", "-q", "origin", "main")
    write_policy(root)
    return root


@pytest.fixture
def host() -> FakeHost:
    fake = FakeHost()
    fake.repo = REPO
    return fake


def grant(root: Path) -> md.Mandate:
    """Le mandat qui couvre les deux gestes délégués : ``merge`` puis ``accept``."""
    request = MandateRequest.build(("merge", "accept"), ("implementation",), 5, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def tick(projet: Path, host: FakeHost):  # type: ignore[no-untyped-def]
    return run_tick(projet, apply=True, exec_=host)


def reviewer_sid(host: FakeHost) -> str:
    """L'identifiant réel du relecteur lancé, tel que ``claude agents`` le rend (#966)."""
    return host.session_of("grimoire-review-")


def ready_task(projet: Path) -> str:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Chaîne complète", origin="test")
    task = service.ledger.create_task(mission.id, "route 404", acceptance=CRITERIA, owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def event_types(projet: Path, tid: str) -> list[str]:
    return [e.event_type for e in TaskService(projet).ledger.events_for(tid)]


def test_une_tache_va_de_ready_a_closed_sans_question_a_l_humain(projet: Path, host: FakeHost) -> None:
    grant(projet)
    # Ce que GitHub voit : la remote d'origine, jamais la configuration locale du worker.
    host.real = Path(git(projet, "remote", "get-url", "origin"))
    tid = ready_task(projet)

    # 1. Le tick lance le worker dans son worktree et pose sa session.
    first = tick(projet, host)
    assert [(o.task_id, o.status) for o in first.launches] == [(tid, "launched")], first.launches
    launched = launched_sessions(TaskService(projet))[tid]
    wt = Path(str(launched["cwd"]))

    # 2. Le worker travaille, pousse sa branche et ouvre sa PR, puis passe la tâche en revue.
    (wt / "src/app/route.py").parent.mkdir(parents=True, exist_ok=True)
    (wt / "src/app/route.py").write_text("404\n", encoding="utf-8")
    git(wt, "add", "-f", "--", "src/app/route.py")
    git(wt, "commit", "-q", "-m", f"travail de {tid}")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    host.prs[f"task/{tid}"] = "OPEN"
    host.checks[f"task/{tid}"] = list(GREEN)
    ledger = TaskService(projet).ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")

    # 3. Le tick lance le relecteur, distinct du worker.
    second = tick(projet, host)
    assert [(o.task_id, o.status) for o in second.reviews] == [(tid, "launched")], second.reviews
    assert reviewer_sid(host) != launched["session_id"]

    # 4. Le relecteur rend son pack et son reçu READY ; le tick suivant le juge.
    item = EvidenceItem.from_text(
        "check-01", EvidenceKind.TEST, "pytest -> exit 0", uri="check:pytest", summary="pytest -> exit 0"
    )
    EvidenceService(wt / EVIDENCE).create_pack(tid, EvidenceProfile.LIGHT, [item], acceptance=CRITERIA)
    write_receipt(wt, tid, "READY", reviewer_id="reviewer", reviewer_session=reviewer_sid(host))

    # 5. Le merger voit la CI verte une première fois : il attend la confirmation.
    third = tick(projet, host)
    assert [(o.action, o.status) for o in third.merges] == [("wait", "waiting")], third.merges

    # 6. Le tick suivant confirme la CI verte, fusionne, accepte et close.
    fourth = tick(projet, host)
    assert [(o.action, o.status) for o in fourth.merges] == [("accept", "done")], fourth.merges
    assert TaskService(projet).require(tid).status is TaskState.CLOSED
    gestures = MandateStore(projet).gestures_taken(tid)
    assert [g["gesture"] for g in gestures] == ["merge", "accept"]  # les deux gestes délégués
    assert {g["origin"] for g in gestures} == {"orchestrator"}  # pris par l'orchestrateur, pas par un humain

    # 7. Le tick suivant laisse le janitor nettoyer la tâche terminale.
    fifth = tick(projet, host)
    assert {o.action for o in fifth.cleanup if o.status == "done"} >= {
        "session",
        "worktree",
        "branch_local",
        "branch_remote",
    }, fifth.cleanup
    assert not wt.exists()
    stopped = {argv[2] for argv in host.of("claude", "stop")}
    assert launched["session"] in stopped  # la session du worker est arrêtée
    assert host.review_stops()  # celle du relecteur aussi

    # La suite ordonnée des événements du ledger, du lancement au nettoyage (les ``janitor.action``
    # répétés sont couverts par l'assertion ci-dessus, leur compte n'est pas le sujet de la chaîne).
    types = [kind for kind in event_types(projet, tid) if not kind.startswith("janitor.action")]
    assert types == [
        "task.created",
        "task.transitioned",  # ready
        WORKTREE_CREATED_EVENT,
        "task.transitioned",  # claimed au lancement
        "task.session_launched",
        "task.transitioned",  # running, geste du worker
        "task.transitioned",  # needs_verification, geste du worker
        WORKER_HALTED_EVENT,  # la session du worker rend sa place pendant la revue
        REVIEW_LAUNCHED_EVENT,
        REVIEW_JUDGED_EVENT,
        MERGE_QUEUED_EVENT,
        MERGE_CI_EVENT,
        MERGE_REQUESTED_EVENT,
        MERGED_EVENT,
        MERGE_ACCEPTED_EVENT,
        "task.transitioned",  # closed
        SWEPT_EVENT,
    ], types

    # Aucune question posée à l'humain : la chaîne est allée au bout seule.
    assert records_of(TaskService(projet).require(tid)) == []
