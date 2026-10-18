"""W5-03 : un test protégé modifié par la tâche donne le verdict ``tampered``, même si le check est vert.

Avant : ``merge_task`` fusionnait dès que le check sortait 0. Un agent qui édite le test
qui échoue (au lieu du code) fait passer le check : le merge est vert et vide de sens.
La comparaison se fait à la base épinglée de W5-02, pas à HEAD (un commit dans le
worktree rendrait ``git diff HEAD`` vide).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import (
    MERGE_REFUSED_EVENT,
    MERGED_EVENT,
    TEST_CHANGES_POLICY_EVENT,
    create_task_worktree,
    find_task_worktree,
    merge_task,
)

OK = f'"{sys.executable}" -c "import sys; sys.exit(0)"'


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


def _commit(cwd: Path, name: str, content: str, message: str) -> str:
    target = cwd / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("_grimoire-runtime-output/\n", encoding="utf-8")
    _git(root, "add", "--", ".gitignore")
    _commit(root, "src/app.py", "x = 1\n", "socle")
    _commit(root, "tests/test_app.py", "assert False\n", "test qui échoue")
    _commit(root, "golden/attendu.json", "{}\n", "golden")
    return root


@pytest.fixture
def service(repo: Path) -> TaskService:
    return TaskService(repo)


def _tache(service: TaskService) -> str:
    ledger = service.ledger
    missions = ledger.list_missions()
    mission = missions[0].id if missions else ledger.create_mission(title="Plan", origin="test").id
    task = ledger.create_task(mission, "Enfant", acceptance=("ok",), owner="amelia")
    ledger.transition_task(task.id, TaskState.READY)
    ledger.claim_task(task.id, "amelia", "local")
    ledger.transition_task(task.id, TaskState.RUNNING)
    return task.id


def test_un_test_modifie_puis_un_check_vert_donnent_tampered(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "tests/test_app.py", "assert True\n", "le test est assoupli")
    avant = _git(repo, "rev-parse", "main")

    issue = merge_task(service, tid, repo=repo, check=OK)
    assert issue.status == "tampered"
    assert not issue.ok
    assert issue.tampered == ("tests/test_app.py",)
    assert issue.check["exit"] == 0  # le check était vert : c'est le diff qui refuse
    assert _git(repo, "rev-parse", "main") == avant
    assert wt.path.is_dir()
    assert any(e.event_type == MERGE_REFUSED_EVENT for e in service.ledger.events_for(tid))


def test_le_test_est_compare_a_la_base_epinglee_pas_a_head(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "tests/test_app.py", "assert True\n", "modifié")
    _commit(wt.path, "src/app.py", "x = 2\n", "puis du code : le dernier commit ne montre plus le test")

    assert merge_task(service, tid, repo=repo, check=OK).status == "tampered"


def test_un_test_supprime_est_tampered_un_test_ajoute_non(repo: Path, service: TaskService) -> None:
    a = _tache(service)
    wa = create_task_worktree(service, a, repo=repo)
    _git(wa.path, "rm", "-q", "--", "tests/test_app.py")
    _git(wa.path, "commit", "-q", "-m", "supprime le test")
    assert merge_task(service, a, repo=repo, check=OK).tampered == ("tests/test_app.py",)

    b = _tache(service)
    wb = create_task_worktree(service, b, repo=repo)
    _commit(wb.path, "tests/test_nouveau.py", "assert True\n", "nouveau test")
    # Un test ajouté n'affaiblit aucune garantie existante.
    assert merge_task(service, b, repo=repo, check=OK).status == "merged"


def test_du_code_seul_se_fusionne(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "src/app.py", "x = 2\n", "code")

    issue = merge_task(service, tid, repo=repo, check=OK)
    assert issue.status == "merged", issue.reason
    assert issue.tampered == ()


def test_allow_test_changes_fusionne_et_journalise_la_politique(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "tests/test_app.py", "assert True\n", "changement voulu")

    issue = merge_task(service, tid, repo=repo, check=OK, allow_test_changes=True, actor="guilhem")
    assert issue.status == "merged", issue.reason
    events = service.ledger.events_for(tid)
    politique = [e for e in events if e.event_type == TEST_CHANGES_POLICY_EVENT]
    assert len(politique) == 1
    assert politique[0].payload["allow_test_changes"] is True
    assert politique[0].payload["paths"] == ["tests/test_app.py"]
    assert politique[0].actor_id == "guilhem"
    fusion = next(e for e in events if e.event_type == MERGED_EVENT)
    assert fusion.payload["allow_test_changes"] is True
    assert fusion.payload["tests_changed"] == ["tests/test_app.py"]


def test_la_politique_par_defaut_est_journalisee_dans_le_refus(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "tests/test_app.py", "assert True\n", "modifié")
    merge_task(service, tid, repo=repo, check=OK)

    refus = next(e for e in service.ledger.events_for(tid) if e.event_type == MERGE_REFUSED_EVENT)
    assert refus.payload["status"] == "tampered"
    assert refus.payload["tampered"] == ["tests/test_app.py"]
    assert refus.payload["allow_test_changes"] is False


def test_un_motif_protege_supplementaire(repo: Path, service: TaskService) -> None:
    sans = _tache(service)
    ws = create_task_worktree(service, sans, repo=repo)
    _commit(ws.path, "golden/attendu.json", '{"a": 1}\n', "golden modifié")
    assert merge_task(service, sans, repo=repo, check=OK).status == "merged"  # non protégé par défaut

    avec = _tache(service)
    wa = create_task_worktree(service, avec, repo=repo)
    _commit(wa.path, "golden/attendu.json", '{"a": 2}\n', "golden modifié encore")
    issue = merge_task(service, avec, repo=repo, check=OK, protect=("golden/*",))
    assert issue.status == "tampered"
    assert issue.tampered == ("golden/attendu.json",)


@pytest.mark.parametrize("motif", ["../autre/*", "tests/../../x", "/etc/hosts", "C:\\x", "a\\..\\b", ""])
def test_un_chemin_hors_projet_est_rejete(repo: Path, service: TaskService, motif: str) -> None:
    tid = _tache(service)
    create_task_worktree(service, tid, repo=repo)
    with pytest.raises(GrimoireMissionError, match="hors du projet"):
        merge_task(service, tid, repo=repo, check=OK, protect=(motif,))


def test_cli_allow_test_changes(repo: Path, service: TaskService) -> None:
    tid = _tache(service)
    assert CliRunner().invoke(task_app, ["worktree", tid, "--project-root", str(repo)]).exit_code == 0
    wt = find_task_worktree(TaskService(repo), tid)
    assert wt is not None
    _commit(wt.path, "tests/test_app.py", "assert True\n", "modifié")
    base = ["merge", tid, "--check", OK, "--project-root", str(repo)]

    refus = CliRunner().invoke(task_app, base)
    assert refus.exit_code == 1, refus.output
    assert "tampered" in refus.output

    permis = CliRunner().invoke(task_app, [*base, "--allow-test-changes"])
    assert permis.exit_code == 0, permis.output
