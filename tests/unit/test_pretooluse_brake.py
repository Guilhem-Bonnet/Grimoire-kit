"""K3 (issue #768) — frein PreToolUse, fencing par hôte, fence git, Stop limité à la tâche de la session.

Constat sur la base (K2, ``aidlc/k2-lease-epoch``) : le ledger refuse l'effet d'un
titulaire périmé, mais aucun outil n'est freiné — ``check_holder`` « fichier seul »
n'a aucun appelant ; la récolte tient pour fencé l'hôte que l'opérateur nomme
(``--fenced-host``), pas celui que les fixtures de K0 prouvent ; aucun ``pre-push``
ne protège un hôte sans PreToolUse ; ``needs_verification`` ne peut pas être
annulée ; et ``Stop`` juge une tâche qui n'est pas celle de la session (claim sans
session, carte ``in_progress`` du board) tout en laissant passer une tâche que la
session tient sans l'avoir fermée ni garée.

Les refus du PreToolUse sont rejoués sur la charge utile réelle capturée par K0
(``tests/fixtures/hosts/claude-code/01-pretooluse-deny-json.json``, Claude Code 2.1.301).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.cmd_task import task_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.host_version import claude_version_from_transcript, detect_host_version
from grimoire.hosts.runtime import run_hook
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.prepush_fence import check_fence, install_fence
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.policies.session_state import load_session_state

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts" / "claude-code"
SESSION = "00000000-0000-4000-8000-000000000003"
ACCEPTATION = "le frein du bail refuse un titulaire périmé"
LEDGER = Path("_grimoire-runtime-output") / "ledger"


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _transcript(root: Path, version: str = "2.1.301") -> Path:
    path = root / "transcript.jsonl"
    path.write_text(
        '{"type":"user","version":"2.1.289"}\n' + json.dumps({"type": "assistant", "version": version}) + "\n",
        encoding="utf-8",
    )
    return path


def _claimed(root: Path, title: str = "Tâche", *, actor: str = "claude") -> str:
    service = TaskService(root)
    tid = service.add(title, (ACCEPTATION,), owner="guilhem", actor=actor, ready=True).task.id
    service.context(tid)
    service.claim(tid, actor, host="local")
    return tid


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _prompt(root: Path, session: str = SESSION) -> Any:
    payload = {
        "session_id": session,
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(root),
        "prompt": "go",
        "transcript_path": str(_transcript(root)),
    }
    return _hook(root, HookEvent.USER_PROMPT_SUBMIT, payload)[1]


def _real_pretooluse(root: Path, session: str = SESSION, **tool_input: Any) -> dict[str, Any]:
    """La charge utile PreToolUse capturée par K0 (``touch EXECUTED.marker``), recadrée sur le projet de test."""
    data = json.loads((FIXTURES / "01-pretooluse-deny-json.json").read_text(encoding="utf-8"))
    payload = dict(data["payloads"]["PreToolUse"])
    payload.update(cwd=str(root), session_id=session)
    if tool_input:
        payload["tool_input"] = {**payload["tool_input"], **tool_input}
    return payload


def _pre(root: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.PRE_TOOL_USE, payload)


def _verdict(rendered: dict[str, Any]) -> str:
    return str(rendered["hookSpecificOutput"]["permissionDecision"])


def _displace(root: Path, tid: str) -> None:
    """La session perd son bail : plus de battement, récolte (hôte fencé), un autre titulaire reprend."""
    service = TaskService(root)
    write_lease(
        lease_path(service.ledger_root, tid),
        LeaseFile(tid, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()),
    )
    [decision] = service.reap().decisions
    assert decision.action == "ready", decision
    service.claim(tid, "autre", host="local")
    assert service.require(tid).claim.lease_epoch == 3  # type: ignore[union-attr]


# ── PreToolUse : epoch périmé, rejoué sur la fixture réelle de K0 ────────────


def test_stale_epoch_pretooluse_denied(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    journal = load_session_state(governed, SESSION, now_iso="")
    assert (journal.task_id, journal.lease_epoch) == (tid, 1)

    control, _ = _pre(governed, _real_pretooluse(governed))
    assert _verdict(control) == "allow"

    _displace(governed, tid)
    rendered, decision = _pre(governed, _real_pretooluse(governed))

    fixture = json.loads((FIXTURES / "01-pretooluse-deny-json.json").read_text(encoding="utf-8"))
    observed = fixture["observed"]["hook_stdout"]["hookSpecificOutput"]
    out = rendered["hookSpecificOutput"]
    assert decision.outcome is Outcome.DENY
    assert out["hookEventName"] == observed["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == observed["permissionDecision"] == "deny"
    assert "epoch 1 périmé" in out["permissionDecisionReason"] and tid in out["permissionDecisionReason"]
    assert decision.detail["lease_brake"] == "stale_epoch"
    # Une lecture n'a aucun effet : elle passe, le titulaire périmé peut encore lire pourquoi il s'arrête.
    read, _ = _pre(governed, {**_real_pretooluse(governed), "tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert _verdict(read) == "allow"


def test_stale_epoch_cache_miss_decides_on_the_ledger(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    _displace(governed, tid)
    lease_path(TaskService(governed).ledger_root, tid).unlink()  # défaut de cache : le ledger tranche
    rendered, _ = _pre(governed, _real_pretooluse(governed))
    assert _verdict(rendered) == "deny"


def test_a_session_without_a_lease_is_never_braked(governed: Path) -> None:
    _claimed(governed)  # claim non rattaché : cette session n'a aucun bail noté
    rendered, _ = _pre(governed, _real_pretooluse(governed, session="une-autre-session"))
    assert _verdict(rendered) == "allow"


def test_displaced_session_does_not_adopt_the_new_claim(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    _displace(governed, tid)
    decision = _prompt(governed)  # le vieux titulaire parle encore : le claim d'aujourd'hui n'est pas le sien
    assert decision.detail.get("session_attached", False) is False
    task = TaskService(governed).require(tid)
    assert task.claim is not None and task.claim.session_id == ""
    assert load_session_state(governed, SESSION, now_iso="").lease_epoch == 1


# ── PreToolUse : annulation ──────────────────────────────────────────────────


def test_cancelled_task_pretooluse_names_the_cancellation(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    TaskService(governed).cancel(tid, "abandon", "guilhem", force=True)
    rendered, decision = _pre(governed, _real_pretooluse(governed))
    assert _verdict(rendered) == "deny"
    assert "annulée" in rendered["hookSpecificOutput"]["permissionDecisionReason"]
    assert decision.detail["lease_brake"] == "cancelled"


def test_cancel_from_needs_verification(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    ledger = TaskService(governed).ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="claude")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="claude")

    moved = TaskService(governed).cancel(tid, "preuve abandonnée", "guilhem", force=True)

    assert moved.task.status is TaskState.CANCELLED and moved.previous is TaskState.NEEDS_VERIFICATION
    barrier = read_lease(lease_path(TaskService(governed).ledger_root, tid))
    assert barrier is not None and not barrier.holder and barrier.origin == "cancel"
    assert TaskService(governed).ledger.check_holder(tid, 1).ok is False
    rendered, _ = _pre(governed, _real_pretooluse(governed))
    assert _verdict(rendered) == "deny"


# ── PreToolUse : --no-verify ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        ("git commit --no-verify -m x", True),
        ("git push --no-verify origin task/T", True),
        ("git -C /x push --no-verify", True),
        ("git commit -nm x", True),
        ("git -c core.hooksPath=/dev/null push origin x", True),
        ("git commit -m 'ne jamais passer --no-verify'", False),
        ("git push origin task/T", False),
        ("git status", False),
        ("grep -- --no-verify README.md", False),
    ],
)
def test_no_verify_refused_while_the_session_holds_a_lease(governed: Path, command: str, refused: bool) -> None:
    _claimed(governed)
    _prompt(governed)
    rendered, decision = _pre(governed, _real_pretooluse(governed, command=command))
    assert (_verdict(rendered) == "deny") is refused
    if refused:
        assert decision.detail["lease_brake"] == "no_verify"


def test_no_verify_is_not_the_brakes_business_without_a_lease(governed: Path) -> None:
    rendered, _ = _pre(governed, _real_pretooluse(governed, command="git commit --no-verify -m x"))
    assert _verdict(rendered) == "allow"


# ── PreToolUse : busy_until ──────────────────────────────────────────────────


def test_pretooluse_posts_busy_until_from_the_declared_timeout(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    path = lease_path(TaskService(governed).ledger_root, tid)
    stale = datetime.now(UTC) - timedelta(minutes=19)
    write_lease(path, LeaseFile(tid, 1, stale.isoformat()))

    rendered, _ = _pre(governed, _real_pretooluse(governed, command="make test", timeout=600_000))

    assert _verdict(rendered) == "allow"
    lease = read_lease(path)
    assert lease is not None and lease.busy_until
    busy = datetime.fromisoformat(lease.busy_until)
    assert timedelta(minutes=9) < busy - datetime.now(UTC) <= timedelta(minutes=10)
    # Le bail ne serait plus jugé perdu pendant l'outil, alors que le TTL seul l'aurait perdu à t+1 min.
    assert not lease.is_lost(datetime.now(UTC) + timedelta(minutes=8))


def test_pretooluse_leaves_the_lease_alone_when_the_call_fits_in_the_ttl(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    path = lease_path(TaskService(governed).ledger_root, tid)
    before = path.read_text(encoding="utf-8")
    _pre(governed, _real_pretooluse(governed))  # Bash sans timeout déclaré : 2 min, le TTL en laisse 20
    assert path.read_text(encoding="utf-8") == before


# ── Fencing par hôte : la table de K0, pas une déclaration ───────────────────


def _running_on(ledger: MissionLedger, title: str, host: str, version: str) -> str:
    mission = ledger.list_missions()[0] if ledger.list_missions() else ledger.create_mission("M", origin="test")
    task = ledger.create_task(mission.id, title, acceptance=("a",))
    ledger.transition_task(task.id, TaskState.READY)
    ledger.claim_task(task.id, "agent", host)
    ledger.transition_task(task.id, TaskState.RUNNING, actor_id="agent")
    ledger.attach_session(task.id, f"s-{title}", session_host=host, host_version=version)
    write_lease(
        lease_path(Path(ledger._root), task.id),
        LeaseFile(task.id, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()),
    )
    return task.id


def test_unfenced_host_asks_human(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    copilot = _running_on(service.ledger, "copilot", "host-github-copilot", "1.99.0")
    other_version = _running_on(service.ledger, "vieux", "host-claude-code-cli", "2.1.302")
    no_version = _running_on(service.ledger, "inconnu", "host-claude-code-cli", "")
    proven = _running_on(service.ledger, "prouve", "host-claude-code-cli", "2.1.301")

    report = service.reap()
    by_task = {d.task_id: d for d in report.decisions}

    for tid, session in ((copilot, "s-copilot"), (other_version, "s-vieux"), (no_version, "s-inconnu")):
        decision = by_task[tid]
        assert (decision.action, decision.cause) == ("defer", "resume_unfenced")
        assert session in decision.question and "sans frein" in decision.question and "reprendre ?" in decision.question
        assert service.require(tid).status is TaskState.RUNNING, "on ne relance pas de doublon"
    assert "version inconnue" in by_task[no_version].question and "2.1.302" in by_task[other_version].question
    assert (by_task[proven].action, by_task[proven].cause) == ("ready", "heartbeat")
    assert service.require(proven).status is TaskState.READY

    deferred = [e for e in service.ledger.list_events(copilot) if e.event_type == "task.reap_deferred"]
    assert deferred[-1].payload["question"]["kind"] == "resume_unfenced"
    assert "s-copilot" in deferred[-1].payload["question"]["text"]


def test_cli_reap_prints_the_question_and_no_longer_trusts_the_operator(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running_on(service.ledger, "copilot", "host-github-copilot", "1.99.0")
    runner = CliRunner()
    res = runner.invoke(task_app, ["reap", "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "différée (resume_unfenced)" in res.output and "reprendre ?" in res.output
    # Déclarer un hôte fencé n'est plus un geste d'opérateur : la table de K0 est la seule source.
    refused = runner.invoke(task_app, ["reap", "--fenced-host", "host-github-copilot", "--project-root", str(tmp_path)])
    assert refused.exit_code != 0
    assert service.require(tid).status is TaskState.RUNNING


def test_host_version_is_read_from_the_session_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = _transcript(tmp_path, "2.1.301")
    assert claude_version_from_transcript(transcript) == "2.1.301"  # la dernière ligne, pas la première
    assert claude_version_from_transcript(tmp_path / "absent.jsonl") == ""
    junk = tmp_path / "junk.jsonl"
    junk.write_text('{"version":"; rm -rf /"}\n', encoding="utf-8")
    assert claude_version_from_transcript(junk) == ""
    payload = {"transcript_path": str(transcript)}
    assert detect_host_version(HostId.CLAUDE_CODE_CLI, payload, {}) == "2.1.301"
    assert detect_host_version(HostId.GITHUB_COPILOT, payload, {}) == ""  # le journal de Claude Code ne parle que de lui
    assert detect_host_version(HostId.GITHUB_COPILOT, {}, {"GRIMOIRE_HOST_VERSION": "1.99.0"}) == "1.99.0"
    assert detect_host_version(HostId.GITHUB_COPILOT, {}, {"GRIMOIRE_HOST_VERSION": "pas une version"}) == ""


def test_the_hook_records_the_host_version_on_the_claim(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    claim = TaskService(governed).require(tid).claim
    assert claim is not None
    assert (claim.session_id, claim.session_host_version, claim.lease_epoch) == (SESSION, "2.1.301", 1)


# ── Fence git : pre-push d'un worktree de tâche ──────────────────────────────


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=60
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} : {done.stderr}")
    return done


@pytest.fixture
def worktree(tmp_path: Path) -> tuple[Path, MissionLedger, str]:
    repo, remote, wt = tmp_path / "repo", tmp_path / "remote.git", tmp_path / "wt"
    repo.mkdir()
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("M", origin="test")
    task = ledger.create_task(mission.id, "T", acceptance=("a",))
    ledger.transition_task(task.id, TaskState.READY)
    ledger.claim_task(task.id, "agent", "local")
    ledger.transition_task(task.id, TaskState.RUNNING, actor_id="agent")
    _git(repo, "worktree", "add", "-q", "-b", "task/T", str(wt))
    install_fence(wt, tmp_path / "ledger", task.id, 1, python=sys.executable)
    return wt, ledger, task.id


def _commit_and_push(wt: Path, name: str) -> subprocess.CompletedProcess[str]:
    (wt / name).write_text(name, encoding="utf-8")
    _git(wt, "add", name)
    _git(wt, "commit", "-q", "-m", name)
    return _git(wt, "push", "-q", "origin", "task/T", check=False)


def test_prepush_refuses_stale_epoch(worktree: tuple[Path, MissionLedger, str]) -> None:
    wt, ledger, tid = worktree
    assert _commit_and_push(wt, "un.txt").returncode == 0, "le titulaire pousse"

    ledger.transition_task(tid, TaskState.BLOCKED, actor_id="agent", reason="repris ailleurs")  # bail rendu : barrière
    refused = _commit_and_push(wt, "deux.txt")

    assert refused.returncode != 0
    assert "push refusé" in refused.stderr and "périmé" in refused.stderr
    remote = _git(wt, "ls-remote", "origin", "task/T").stdout
    assert remote.count("\n") == 1 and "refs/heads/task/T" in remote


def test_prepush_names_a_cancellation_and_a_new_holder(worktree: tuple[Path, MissionLedger, str]) -> None:
    wt, ledger, tid = worktree
    fence = wt.parent / "repo" / ".git" / "worktrees" / "wt" / "grimoire-fence" / "fence.json"
    assert check_fence(fence).ok
    ledger.transition_task(tid, TaskState.CANCELLED, actor_id="human")
    verdict = check_fence(fence)
    assert not verdict.ok and "annulée" in verdict.reason
    assert _commit_and_push(wt, "trois.txt").returncode != 0


def test_prepush_hands_over_to_the_repository_hook(worktree: tuple[Path, MissionLedger, str]) -> None:
    wt, _, _ = worktree
    hook = wt.parent / "repo" / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho depot-refuse >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    refused = _commit_and_push(wt, "quatre.txt")
    assert refused.returncode != 0 and "depot-refuse" in refused.stderr


def test_fence_is_fail_closed_on_garbage(tmp_path: Path) -> None:
    broken = tmp_path / "fence.json"
    assert not check_fence(broken).ok
    broken.write_text("{pas du json", encoding="utf-8")
    assert not check_fence(broken).ok
    broken.write_text(json.dumps({"$schema": "autre"}), encoding="utf-8")
    assert not check_fence(broken).ok
    broken.write_text(
        json.dumps({"$schema": "grimoire-task-fence/v1", "task_id": "T", "epoch": 1, "lease_file": str(tmp_path / "nope")}),
        encoding="utf-8",
    )
    assert not check_fence(broken).ok


def test_cli_fence_installs_the_hook_for_the_claim_epoch(governed: Path, tmp_path: Path) -> None:
    tid = _claimed(governed, actor="cli")
    repo = tmp_path / "wtrepo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "i")
    res = CliRunner().invoke(
        task_app, ["fence", tid, "--worktree", str(repo), "--project-root", str(governed), "--actor", "cli"]
    )
    assert res.exit_code == 0, res.output
    fence = json.loads((repo / ".git" / "grimoire-fence" / "fence.json").read_text(encoding="utf-8"))
    assert (fence["task_id"], fence["epoch"]) == (tid, 1)
    refused = CliRunner().invoke(
        task_app, ["fence", tid, "--worktree", str(repo), "--project-root", str(governed), "--actor", "autre"]
    )
    assert refused.exit_code == 1


# ── Stop : la tâche de la session, rien d'autre ──────────────────────────────


def _stop(root: Path, session: str = SESSION, *, active: bool = False) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": session, "hook_event_name": "Stop", "cwd": str(root), "stop_hook_active": active}
    return _hook(root, HookEvent.STOP, payload)


def _write_event(root: Path, session: str = SESSION, path: str = "src/app.py") -> None:
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / path), "old_string": "a", "new_string": "b"},
        "tool_response": {"filePath": str(root / path), "success": True},
    }
    _hook(root, HookEvent.POST_TOOL_USE, payload)


def test_stop_allowed_without_task_and_mutation(governed: Path) -> None:
    tid = _claimed(governed)  # claim d'un autre acteur, jamais rattaché à une session : gates rouges
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.ALLOW, decision.reason
    assert "decision" not in rendered
    assert decision.detail.get("task_id") != tid


def test_stop_ignores_other_session_task(governed: Path) -> None:
    tid = _claimed(governed)
    TaskService(governed).attach_session(tid, "session-d-un-autre", session_host="claude-code-cli", actor="claude")
    rendered, decision = _stop(governed, session="ma-session")
    assert decision.outcome is Outcome.ALLOW, decision.reason
    assert "decision" not in rendered
    # La session qui tient la tâche, elle, reste jugée dessus.
    _, mine = _stop(governed, session="session-d-un-autre")
    assert mine.detail.get("task_id") == tid


def test_stop_still_judges_a_hand_written_board_card(governed: Path) -> None:
    board = governed / "_grimoire/standard/task-board.yaml"
    board.write_text(
        "tasks:\n  - task_id: CARTE-1\n    status: in_progress\n    title: carte à la main\n", encoding="utf-8"
    )
    _, decision = _stop(governed)
    assert decision.detail.get("task_id") == "CARTE-1"  # le ledger ne la connaît pas : elle reste jugée


@pytest.mark.parametrize(("profile", "blocks"), [("governed", True), ("orchestrated", False)])
def test_unattached_work_signalled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, blocks: bool) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id=profile, task_id="bootstrap")
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _write_event(tmp_path)

    _, decision = _stop(tmp_path)

    assert (decision.outcome is Outcome.BLOCK) is blocks
    work = load_session_state(tmp_path, SESSION, now_iso="").unattached_work
    assert work["mutations"] >= 1 and any(f.startswith("src") for f in work["files"])
    assert decision.detail["unattached_work"]["mutations"] == work["mutations"]


def test_a_session_that_only_read_leaves_no_unattached_work(governed: Path) -> None:
    _stop(governed)
    assert load_session_state(governed, SESSION, now_iso="").unattached_work == {}


def _green_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grimoire.hosts.decisions.evidence_gate._gate_summary",
        lambda _root, task_id: (True, "", {"task_id": task_id, "state": "in_progress", "ok": True, "missing": []}),
    )


def test_stop_blocked_when_task_running_unparked(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _green_gates(monkeypatch)
    tid = _claimed(governed)
    _prompt(governed)

    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.BLOCK
    assert rendered["decision"] == "block"
    assert decision.detail["blocked_on"] == "task_unparked" and decision.detail["task_state"] in ("claimed", "running")
    for gesture in ("needs_verification", "grimoire task block", "grimoire task cancel"):
        assert gesture in decision.reason

    _, again = _stop(governed, active=True)  # le second Stop sort toujours
    assert again.outcome is Outcome.ALLOW

    TaskService(governed).transition(tid, TaskState.BLOCKED, "claude", "attend une réponse")  # garée avec une raison
    _, parked = _stop(governed)
    assert parked.outcome is Outcome.ALLOW and parked.detail.get("blocked_on") != "task_unparked"


def test_stop_only_informs_outside_blocking_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="orchestrated", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    _green_gates(monkeypatch)
    _claimed(tmp_path)
    _prompt(tmp_path)
    rendered, decision = _stop(tmp_path)
    assert decision.outcome is Outcome.ALLOW and "decision" not in rendered
    assert decision.detail["blocked_on"] == "task_unparked" and "needs_verification" in rendered["systemMessage"]
