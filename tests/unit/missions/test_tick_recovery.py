"""K12 (épopée #705) — tick idempotent, plan = application, récolte qui garde le travail local, effets, minuteur.

Constat sur la base (``aidlc/k11-global-pause``) :

- l'aperçu du tick (``run_tick(apply=False)``) calcule l'admission sur le ledger d'*avant* ses propres gestes :
  une tâche ``waiting(paused)`` rendue par la levée de la pause est annoncée réveillée mais pas admise, alors
  que le tick appliqué l'admet (``orchestrate.py``, ``queue_plan`` appelé après des gestes à blanc) ;
- deux ticks simultanés récoltent et dispatchent tous les deux : aucun verrou de tick ;
- la récolte rend ``ready`` une tâche dont le worktree porte des commits jamais poussés
  (``ledger.py``, ``_reap_one``) : le repreneur part d'une remote qui ne les a pas ;
- un worktree de tâche peut être créé sous ``/tmp`` (``create_task_worktree(path=...)``) : un redémarrage
  le vide ;
- aucun effet externe n'est tracé ni réconcilié : un push interrompu se refait à l'aveugle ;
- aucun déclencheur sans session : pas de minuteur.

Les nouveaux modules sont importés dans les tests, pas en tête : sur la base, chaque test rougit seul.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_orchestrate import orchestrate_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.lease import LeaseFile, backoff_delay, lease_path, parse_instant, write_lease
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import CONTROL_HOME_ENV
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import create_task_worktree

S = TaskState
ACC = "le tick reprend sans rien perdre"
STANDARD = Path("_grimoire/standard")
#: La version de Claude Code capturée par K0 : hôte fencé (récolte automatique).
FENCED_VERSION = "2.1.301"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path_factory.mktemp("control-home")))
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION",
        "GRIMOIRE_UNATTENDED", "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def _commit(cwd: Path, name: str, content: str) -> str:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-q", "-m", f"ajoute {name}")
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "depot"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("_grimoire-runtime-output/\n_grimoire-output/\n", encoding="utf-8")
    _git(root, "add", "--", ".gitignore")
    _commit(root, "socle.txt", "socle\n")
    return root


def _ready(service: TaskService, title: str, **kw: Any) -> str:
    return service.add(title, (ACC,), actor="t", owner="amelia", ready=True, **kw).task.id


def _running(service: TaskService, title: str, *, version: str = FENCED_VERSION, session: str = "S1") -> str:
    tid = _ready(service, title)
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "agent-a")
    service.attach_session(tid, session, session_host="claude-code-cli", actor="agent-a", host_version=version)
    return tid


def _expire(service: TaskService, tid: str, *, ago: timedelta = timedelta(hours=1)) -> None:
    """Le dernier battement de cœur date de *ago* : le bail (TTL 20 min) est perdu."""
    task = service.require(tid)
    assert task.claim is not None
    stale = (datetime.now(UTC) - ago).isoformat()
    write_lease(lease_path(service.ledger_root, tid), LeaseFile(tid, task.claim.lease_epoch, stale))


def _events(service: TaskService) -> bytes:
    return (service.ledger_root / "events.jsonl").read_bytes()


_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?\+00:00")


def _comparable(report: Any, now: datetime) -> Any:
    """Le rapport sans ``mode`` ni ``dispatched`` ; les horodatages autres que *now* sont ceux des écritures.

    Un backoff se compte depuis le ``created_at`` de l'événement de récolte, posé à l'écriture : la copie
    et le vrai ledger l'écrivent à quelques millisecondes d'écart. Les décisions, elles, doivent être les mêmes.
    """
    data = report.to_dict()
    data.pop("mode", None)
    data.pop("dispatched", None)
    text = json.dumps(data, sort_keys=True, ensure_ascii=False).replace(now.isoformat(), "<now>")
    return json.loads(_STAMP.sub("<écrit>", text))


# ── CA : le plan est l'application ──────────────────────────────────────────


def test_plan_equals_apply_diff(projet: Path) -> None:
    service = TaskService(projet)
    parked = _running(service, "Garée", session="S1")
    service.wait(parked, "paused", "agent-a")  # pause déjà levée : le tick la rend ready
    lost = _running(service, "Perdue", session="S2")
    _expire(service, lost)
    fresh = _ready(service, "Fraîche")
    before, board = _events(service), (projet / "_grimoire/standard/task-board.yaml")
    board_before = board.read_bytes() if board.exists() else b""
    now = datetime.now(UTC)

    planned = run_tick(projet, apply=False, now=now)
    assert _events(TaskService(projet)) == before, "le plan n'écrit rien au ledger"
    assert (board.read_bytes() if board.exists() else b"") == board_before, "ni au board"
    assert not list((projet / "_grimoire-output" / "control").glob("tick-plan-*")), "la copie est retirée"

    applied = run_tick(projet, apply=True, now=now)
    # Le cas qui divergeait : la tâche rendue par la levée de pause est admise dans le plan comme à l'application.
    admitted = {a.task_id for a in planned.plan.admitted}  # type: ignore[union-attr]
    assert parked in admitted and fresh in admitted
    assert [(d.task_id, d.action) for d in planned.reaped] == [(lost, "ready")]
    assert _comparable(planned, now) == _comparable(applied, now)
    assert (planned.mode, applied.mode) == ("plan", "apply")


def test_tick_twice_same_state(projet: Path) -> None:
    service = TaskService(projet)
    parked = _running(service, "Garée", session="S1")
    service.wait(parked, "paused", "agent-a")
    lost = _running(service, "Perdue", session="S2")
    _expire(service, lost)
    run_tick(projet, apply=True)
    after_first = _events(TaskService(projet))
    states = {t.id: t.status for t in TaskService(projet).list_tasks()}

    second = run_tick(projet, apply=True)

    assert _events(TaskService(projet)) == after_first, "le second tick n'écrit rien"
    assert {t.id: t.status for t in TaskService(projet).list_tasks()} == states
    assert not any(d.written for d in second.reaped) and not any(w.written for w in second.woken)


def test_a_second_tick_skips_while_the_first_holds_the_lock(projet: Path) -> None:
    from grimoire.core.fsio import locked
    from grimoire.missions.orchestrate import TICK_LOCK_RELPATH

    service = TaskService(projet)
    lost = _running(service, "Perdue")
    _expire(service, lost)
    before = _events(service)
    with locked(projet / TICK_LOCK_RELPATH):
        report = run_tick(projet, apply=True)
    assert report.status == "busy" and "saute" in report.notes[0]
    assert _events(TaskService(projet)) == before and TaskService(projet).require(lost).status is S.RUNNING
    # Le verrou rendu (tick tué ou fini), le suivant reprend.
    assert run_tick(projet, apply=True).status == "ran"
    assert TaskService(projet).require(lost).status is S.READY
    res = runner.invoke(orchestrate_app, ["tick", "--apply", "--plan", "--project-root", str(projet)])
    assert res.exit_code == 2


def test_tick_without_session_reaps(projet: Path) -> None:
    service = TaskService(projet)
    lost = _running(service, "Perdue")
    _expire(service, lost)
    res = runner.invoke(orchestrate_app, ["tick", "--apply", "--project-root", str(projet), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [(d["task_id"], d["action"]) for d in payload["reaped"]] == [(lost, "ready")]
    task = TaskService(projet).require(lost)
    assert task.status is S.READY


def test_backoff_then_human_after_3(tmp_path: Path) -> None:
    class _Clock:
        now = datetime.now(UTC)

        def __call__(self) -> datetime:
            return self.now

    clock = _Clock()
    ledger = MissionLedger(tmp_path / "ledger", clock=clock)
    mission = ledger.create_mission("M", origin="test")
    tid = ledger.create_task(mission.id, "T", acceptance=("a",)).id
    ledger.transition_task(tid, S.READY)
    fenced = frozenset({"claude-code-cli"})
    for attempt in (1, 2, 3):
        ledger.claim_task(tid, f"agent-{attempt}", "claude-code-cli")
        ledger.transition_task(tid, S.RUNNING, actor_id=f"agent-{attempt}")
        clock.now += timedelta(minutes=30)
        [decision] = ledger.reap(fenced_hosts=fenced).decisions
        assert (decision.action, decision.attempts) == ("ready", attempt)
        lost_at = parse_instant(ledger.list_events(tid)[-1].created_at)
        until = ledger.backoff_until(tid)
        assert lost_at is not None and until == lost_at + backoff_delay(attempt), "réadmission différée, doublée"
    ledger.claim_task(tid, "agent-4", "claude-code-cli")
    ledger.transition_task(tid, S.RUNNING, actor_id="agent-4")
    clock.now += timedelta(minutes=30)
    [decision] = ledger.reap(fenced_hosts=fenced).decisions
    assert (decision.action, decision.cause) == ("defer", "retry_exhausted")
    task = ledger.get_task(tid)
    assert task is not None and task.waiting is not None and task.waiting.reason == "human"


# ── CA : le travail local n'est pas rendu à un repreneur ────────────────────


def _task_in_worktree(repo: Path) -> tuple[TaskService, str, Path]:
    service = TaskService(repo)
    tid = _running(service, "Dans son worktree")
    wt = create_task_worktree(service, tid, repo=repo)
    return service, tid, wt.path


def test_unpushed_commits_ask_human(repo: Path) -> None:
    service, tid, path = _task_in_worktree(repo)
    _commit(path, "travail.txt", "pas encore poussé\n")
    _expire(service, tid)

    [decision] = service.reap().decisions

    assert (decision.action, decision.cause) == ("defer", "resume_branch")
    task = TaskService(repo).require(tid)
    assert task.status is S.WAITING and task.waiting is not None and task.waiting.reason == "human"
    question = task.waiting.question
    assert question is not None and question.kind == "resume_branch" and question.origin == "tick"
    assert "1 commit(s) non poussé(s)" in question.text and f"task/{tid}" in question.text
    assert question.options == ("reprendre", "abandonner")


def test_pushed_branch_is_reaped_and_a_dirty_tree_asks(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    service, tid, path = _task_in_worktree(repo)
    _commit(path, "travail.txt", "poussé\n")
    _git(path, "push", "-q", "origin", f"task/{tid}")
    _expire(service, tid)
    [decision] = service.reap().decisions
    assert (decision.action, decision.cause) == ("ready", "heartbeat"), "tout est sur la remote : récolte normale"

    other, tid2, path2 = _task_in_worktree(repo)
    (path2 / "brouillon.txt").write_text("non commité\n", encoding="utf-8")
    _expire(other, tid2)
    [decision] = other.reap().decisions
    assert (decision.action, decision.cause) == ("defer", "resume_branch")
    question = TaskService(repo).require(tid2).waiting.question  # type: ignore[union-attr]
    assert question is not None and "arbre de travail modifié" in question.text


def test_worktree_never_under_tmp(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    service = TaskService(repo)
    tid = _running(service, "Sous tmp")
    with pytest.raises(GrimoireMissionError, match="dossier temporaire"):
        create_task_worktree(service, tid, repo=repo, path=system_tmp / "wt")
    assert not (system_tmp / "wt").exists()
    wt = create_task_worktree(service, tid, repo=repo)  # le défaut : sous le dossier git commun du projet
    assert not wt.path.is_relative_to(system_tmp)


# ── CA : un effet à moitié fait est constaté, pas refait ───────────────────


@pytest.fixture
def pushed(repo: Path, tmp_path: Path) -> tuple[TaskService, str, dict[str, str]]:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "checkout", "-q", "-b", "task/x")
    sha = _commit(repo, "x.txt", "x\n")
    service = TaskService(repo)
    tid = _running(service, "Pousse")
    return service, tid, {"repo": str(repo), "remote": "origin", "branch": "task/x", "sha": sha}


def test_intended_effect_reconciled_not_redone(pushed: tuple[TaskService, str, dict[str, str]]) -> None:
    from grimoire.missions import effects

    service, tid, target = pushed
    calls: list[str] = []

    def push() -> str:
        calls.append("push")
        _git(Path(target["repo"]), "push", "-q", "origin", target["branch"])
        return target["sha"]

    # Coupure entre l'effet et sa trace : l'intention est écrite, le push est parti, le constat manque.
    eid = effects.effect_id(tid, effects.GIT_PUSH, target)
    service.ledger.append_event(
        effects.EFFECT_INTENDED_EVENT, tid, "task", "agent-a",
        {"effect_id": eid, "task_id": tid, "kind": effects.GIT_PUSH, "target": target},
    )
    _git(Path(target["repo"]), "push", "-q", "origin", target["branch"])

    outcome = effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-b")

    assert outcome.status == "reconciled" and outcome.evidence == target["sha"]
    assert calls == [], "le push n'est pas refait"
    assert effects.pending_effects(service) == []
    again = effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-b")
    assert again.status == "already" and calls == []


def test_the_tick_observes_pending_effects_without_redoing_them(pushed: tuple[TaskService, str, dict[str, str]]) -> None:
    from grimoire.missions import effects

    service, tid, target = pushed
    absent_target = {**target, "branch": "task/jamais-poussee"}
    for tgt in (target, absent_target):
        service.ledger.append_event(
            effects.EFFECT_INTENDED_EVENT, tid, "task", "agent-a",
            {"effect_id": effects.effect_id(tid, effects.GIT_PUSH, tgt), "task_id": tid, "kind": effects.GIT_PUSH, "target": tgt},
        )
    _git(Path(target["repo"]), "push", "-q", "origin", target["branch"])

    report = run_tick(Path(target["repo"]), apply=True)

    statuses = {o.effect.target["branch"]: o.status for o in report.effects}
    assert statuses == {"task/x": "reconciled", "task/jamais-poussee": "absent"}
    assert any("non constaté" in line and "task/jamais-poussee" in line for line in report.attention)
    [left] = effects.pending_effects(TaskService(Path(target["repo"])))
    assert left.target["branch"] == "task/jamais-poussee"


def test_an_unverifiable_effect_is_not_redone(pushed: tuple[TaskService, str, dict[str, str]]) -> None:
    from grimoire.missions import effects

    service, tid, target = pushed
    calls: list[str] = []
    eid = effects.effect_id(tid, effects.GH_PR, {"repo": target["repo"], "branch": "task/x"})
    service.ledger.append_event(
        effects.EFFECT_INTENDED_EVENT, tid, "task", "agent-a",
        {"effect_id": eid, "task_id": tid, "kind": effects.GH_PR, "target": {"repo": target["repo"], "branch": "task/x"}},
    )
    outcome = effects.perform(
        service, tid, effects.GH_PR, {"repo": target["repo"], "branch": "task/x"}, lambda: calls.append("pr") or "",
        actor="agent-b", probe=lambda _e: (effects.UNKNOWN, "gh absent"),
    )
    assert outcome.status == "unknown" and calls == [], "invérifiable : rien n'est fait (fail-closed)"


# ── CA : le minuteur optionnel ──────────────────────────────────────────────


class _Systemctl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_install_timer_writes_a_oneshot_unit_and_its_timer(projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import timer

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    spy = _Systemctl()
    plan, done = timer.install_timer(projet, "15m", run=spy)

    assert plan.service_path.parent == tmp_path / "xdg" / "systemd" / "user"
    service_text = plan.service_path.read_text(encoding="utf-8")
    timer_text = plan.timer_path.read_text(encoding="utf-8")
    assert "Type=oneshot" in service_text and '"orchestrate" "tick" "--apply"' in service_text
    assert f'"{projet.resolve()}"' in service_text
    assert "OnUnitActiveSec=15min" in timer_text and f"Unit={plan.name}.service" in timer_text
    assert spy.calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", f"{plan.name}.timer"]]
    assert done[-1] == f"activé : {plan.name}.timer" and "enable-linger" in plan.notes[0]
    assert timer.timer_status(projet).startswith("minuteur : ")

    _plan, removed = timer.uninstall_timer(projet, run=spy)
    assert not plan.service_path.exists() and not plan.timer_path.exists()
    assert ["systemctl", "--user", "disable", "--now", f"{plan.name}.timer"] in spy.calls
    assert len(removed) == 2 and "absent" in timer.timer_status(projet)


def test_without_systemd_the_timer_is_a_crontab_line(projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import timer

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(timer.shutil, "which", lambda _name: None)
    plan, done = timer.install_timer(projet, "5m")
    assert not plan.service_path.exists(), "rien n'est écrit sans systemd"
    assert plan.cron.startswith("*/5 * * * * ") and "orchestrate tick --apply --project-root" in plan.cron
    assert plan.cron in done[0]
    assert timer.cron_line(projet, 120).startswith("0 */2 * * * ")
    with pytest.raises(ValueError):
        timer.cron_line(projet, 7 * 60)
    for bad in ("", "0m", "5s", "25h", "cinq"):
        with pytest.raises(ValueError):
            timer.parse_interval(bad)
    res = runner.invoke(orchestrate_app, ["install-timer", "--interval", "5s", "--project-root", str(projet)])
    assert res.exit_code == 2


def test_cli_install_timer_without_enabling(projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from grimoire.missions import timer

    if timer.shutil.which("systemctl") is None:
        pytest.skip("systemctl absent : la branche crontab est couverte ailleurs")
    res = runner.invoke(orchestrate_app, ["install-timer", "--no-enable", "--project-root", str(projet), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert Path(payload["timer_path"]).is_file() and "non activé" in payload["done"][-1]
    res = runner.invoke(orchestrate_app, ["uninstall-timer", "--project-root", str(projet)])
    assert res.exit_code == 0 and not Path(payload["timer_path"]).exists()


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
def test_generated_units_pass_systemd_analyze_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import timer

    projet = tmp_path / "un projet % $HOME"
    projet.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    plan, _ = timer.install_timer(projet, "5m", enable=False, run=_Systemctl(), python=sys.executable)
    done = subprocess.run(
        ["systemd-analyze", "verify", str(plan.service_path), str(plan.timer_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert f"WorkingDirectory={projet.resolve()}".replace("%", "%%") in plan.service
