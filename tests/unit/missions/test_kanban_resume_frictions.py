"""KAN-FRICTIONS — cinq frictions du kanban agentique signalées le 2026-10-19 (Forge, kit 3.63.0).

1. une session qui gare sa tâche (``task wait --reason paused``) ne peut plus la reprendre :
   passé le TTL du bail, le frein du bail refuse toute commande Bash de la session, y compris
   ``grimoire task resume <id> --epoch N`` (« Renouvellement refusé : tâche waiting, aucun bail ») ;
2. le serveur MCP répond « 'waiting' is not a valid TaskState » ;
3. un claim dont le bail a expiré n'est pas re-réclamable (« déjà claimed … jusqu'à <date passée> ») ;
4. ``task move --to needs_verification`` exige un pack que ``task verify`` ne produit pas sans commande ;
5. le WIP global compte les claims expirés.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.evidence.schemas import VerdictResult
from grimoire.evidence.service import EvidenceService
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.schemas import MissionTask, TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.policies.session_state import load_session_state

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "claude-code"
SESSION = "00000000-0000-4000-8000-0000000000f1"
ACC = "la reprise se fait depuis la session"
STANDARD = Path("_grimoire/standard")
runner = CliRunner()


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / STANDARD / "llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _transcript(root: Path) -> Path:
    path = root / "transcript.jsonl"
    path.write_text('{"type":"assistant","version":"2.1.301"}\n', encoding="utf-8")
    return path


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _prompt(root: Path) -> None:
    payload = {
        "session_id": SESSION, "hook_event_name": "UserPromptSubmit", "cwd": str(root), "prompt": "go",
        "transcript_path": str(_transcript(root)),
    }
    _hook(root, HookEvent.USER_PROMPT_SUBMIT, payload)


def _bash(root: Path, command: str, tool: str = "Bash", tool_input: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.loads((FIXTURES / "01-pretooluse-deny-json.json").read_text(encoding="utf-8"))
    payload = dict(data["payloads"]["PreToolUse"])
    payload.update(cwd=str(root), session_id=SESSION, tool_name=tool)
    payload["tool_input"] = tool_input if tool_input is not None else {**payload["tool_input"], "command": command}
    return payload


def _verdict(root: Path, payload: dict[str, Any]) -> tuple[str, str]:
    rendered, _ = _hook(root, HookEvent.PRE_TOOL_USE, payload)
    out = rendered["hookSpecificOutput"]
    return str(out["permissionDecision"]), str(out.get("permissionDecisionReason", ""))


def _parked_for_long(root: Path) -> str:
    """La séquence du signalement : claim depuis la session, ``task wait --reason paused``, bail plus vieux que le TTL."""
    service = TaskService(root)
    tid = service.add("Refonte", (ACC,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    _prompt(root)
    assert load_session_state(root, SESSION, now_iso="").lease_epoch == 1
    service.wait(tid, "paused", "claude")
    path = lease_path(service.ledger_root, tid)
    cached = read_lease(path)
    assert cached is not None and cached.waiting
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    write_lease(path, LeaseFile(tid, cached.epoch, old, cached.ttl_seconds, "", waiting=True))
    return tid


# ── 1. reprise d'une tâche garée ─────────────────────────────────────────────


def test_parked_session_can_resume_its_own_task(governed: Path) -> None:
    tid = _parked_for_long(governed)
    for command in (
        f"grimoire task resume {tid} --epoch 1",
        f"grimoire task resume {tid}",
        f"grimoire task show {tid}",
        f"grimoire task attach {tid}",
        f"grimoire task renew {tid} --epoch 1",
        f"/opt/venv/bin/grimoire task resume {tid} --epoch=1 --actor claude",
    ):
        decision, reason = _verdict(governed, _bash(governed, command))
        assert decision == "allow", (command, reason)


@pytest.mark.parametrize(
    "command",
    [
        "touch EXECUTED.marker",
        "grimoire task resume GAO-autre-001 --epoch 1",
        "grimoire task close {tid}",
        "grimoire task resume {tid} --epoch 1 && touch EXECUTED.marker",
        "grimoire task resume {tid} --project-root /ailleurs",
        "grimoire --project-root /ailleurs task resume {tid}",
    ],
)
def test_parked_session_is_no_longer_frozen_by_its_parked_task(governed: Path, command: str) -> None:
    """WAIT-SCOPE : avant, tout autre outil que la reprise de T était refusé (« reprends-la d'abord »).

    Une attente porte sur sa tâche : la session est rendue à l'état « sans tâche » (aucune écriture au nom de T,
    cf. ``test_wait_scope``), le frein du bail ne la gèle plus.
    """
    tid = _parked_for_long(governed)
    decision, reason = _verdict(governed, _bash(governed, command.format(tid=tid)))
    assert decision == "allow", reason
    assert "garée" not in reason


def test_a_fresh_parked_lease_still_lets_the_session_work(governed: Path) -> None:
    """Dans le TTL, rien ne change : rien n'est à renouveler, l'outil passe comme avant."""
    service = TaskService(governed)
    tid = service.add("Refonte", (ACC,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    _prompt(governed)
    service.wait(tid, "paused", "claude")
    assert _verdict(governed, _bash(governed, "make test"))[0] == "allow"


def test_double_quoted_substitution_does_not_ride_the_grimoire_task_exemption(governed: Path) -> None:
    """Le shell substitue ``"$(…)"`` : l'exemption « commande grimoire task simple » ne la couvre plus."""
    service = TaskService(governed)
    tid = service.add("Annulée", (ACC,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    _prompt(governed)
    service.cancel(tid, "abandon", "guilhem", force=True)
    assert _verdict(governed, _bash(governed, f"grimoire task show {tid}"))[0] == "allow"
    decision, _ = _verdict(governed, _bash(governed, 'grimoire task show "$(touch EXECUTED.marker)"'))
    assert decision == "deny"


def test_parked_session_can_resume_through_mcp(governed: Path) -> None:
    """L'équivalent MCP passait déjà sur main : un appel MCP ne déclare aucun temps d'outil, le frein ne renouvelle rien."""
    tid = _parked_for_long(governed)
    resume = _bash(governed, "", "mcp__grimoire__task_update", {"task_id": tid, "action": "resume", "actor": "claude"})
    show = _bash(governed, "", "mcp__grimoire__task_show", {"task_id": tid})
    assert _verdict(governed, resume)[0] == "allow"
    assert _verdict(governed, show)[0] == "allow"


def test_resume_refreshes_the_heartbeat_so_the_task_is_not_reaped_at_once(governed: Path) -> None:
    tid = _parked_for_long(governed)
    service = TaskService(governed)
    service.resume(tid, "claude", epoch=1)
    task = service.require(tid)
    assert task.status is TaskState.RUNNING
    view = service.ledger.lease_view(task)
    assert view is not None and view.status == "valid", view
    assert not service.reap(apply=False).decisions


def test_e2e_parked_resume_through_the_real_hook_entry_point(governed: Path) -> None:
    """Le vrai point d'entrée (``grimoire-hook --host claude --event PreToolUse``), dans un processus à part."""
    tid = _parked_for_long(governed)

    def hook(command: str) -> dict[str, Any]:
        done = subprocess.run(
            [sys.executable, "-c", "import sys; from grimoire.hosts.runtime import main; sys.exit(main())",
             "--host", "claude", "--event", "PreToolUse"],
            input=json.dumps(_bash(governed, command)), capture_output=True, text=True, encoding="utf-8",
            cwd=governed, timeout=120, check=False,
        )
        assert done.returncode == 0, done.stderr
        return dict(json.loads(done.stdout)["hookSpecificOutput"])

    allowed = hook(f"grimoire task resume {tid} --epoch 1")
    assert allowed["permissionDecision"] == "allow", allowed
    free = hook("touch EXECUTED.marker")  # WAIT-SCOPE : la session garée n'est plus gelée
    assert free["permissionDecision"] == "allow", free


# ── 2. MCP : l'état waiting et les états inconnus ────────────────────────────


def _text(result: Any) -> str:
    """Le corps d'une réponse d'outil MCP : la chaîne, ou le texte d'un ``CallToolResult`` d'erreur."""
    if isinstance(result, str):
        return result
    return "".join(str(getattr(part, "text", "")) for part in result.content)


def test_mcp_knows_the_waiting_state(governed: Path) -> None:
    from grimoire.mcp.server import task_claim, task_show, task_update

    service = TaskService(governed)
    tid = service.add("Refonte", (ACC,), owner="guilhem", actor="mcp-agent", ready=True).task.id
    service.context(tid)
    root = str(governed)
    assert json.loads(_text(task_claim(tid, project_path=root)))["status"] == "claimed"
    parked = json.loads(_text(task_update(tid, "wait", to="paused", project_path=root)))
    assert parked["status"] == "waiting", parked
    assert json.loads(_text(task_show(tid, project_path=root)))["status"] == "waiting"
    again = _text(task_claim(tid, project_path=root))
    assert "not a valid TaskState" not in again and "waiting" in again, again
    resumed = json.loads(_text(task_update(tid, "resume", project_path=root)))
    assert resumed["status"] == "running", resumed


def test_mcp_survives_a_state_written_by_a_newer_version(governed: Path) -> None:
    from grimoire.mcp.server import task_show, task_update

    service = TaskService(governed)
    tid = service.add("Future", (ACC,), owner="guilhem", actor="mcp-agent", ready=True).task.id
    service.ledger.append_event(
        "task.transitioned", tid, "task", "futur",
        {"task_id": tid, "from_state": "ready", "to_state": "frozen", "reason": "", "actor_id": "futur"},
    )
    root = str(governed)
    shown = _text(task_show(tid, project_path=root))
    assert "not a valid TaskState" not in shown and "frozen" in shown, shown
    moved = _text(task_update(tid, "move", to="frozen", project_path=root))
    assert "not a valid TaskState" not in moved and "frozen" in moved, moved
    other = service.add("Présente", (ACC,), owner="guilhem", actor="mcp-agent", ready=True).task.id
    sentinel = _text(task_update(other, "move", to="unknown", project_path=root))
    assert "unknown state" in sentinel and "waiting" in sentinel, sentinel


# ── 3. claim expiré re-réclamable ────────────────────────────────────────────


def _stale_claim(service: TaskService, tid: str, actor: str) -> None:
    """Un claim dont le plafond dur ``expires_at`` est passé (le cas du signalement)."""
    past = datetime.now(UTC) - timedelta(hours=8)
    service.ledger.transition_task(tid, TaskState.CLAIMED, actor_id=actor, claim=TaskClaim.new(actor, "local", now=past))


def _ready(service: TaskService, title: str, owner: str = "guilhem") -> MissionTask:
    task = service.add(title, (ACC,), owner=owner, actor="claude", ready=True).task
    service.context(task.id)
    return task


def test_same_actor_reclaims_an_expired_claim(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Expiré").id
    _stale_claim(service, tid, "claude")
    moved = service.claim(tid, "claude")
    task = moved.task
    assert task.status is TaskState.CLAIMED
    assert task.claim is not None and task.claim.lease_epoch == 3 and not task.claim.is_expired()
    events = [e for e in service.ledger.list_events(tid) if e.event_type == "task.transitioned"]
    reclaim = events[-2].payload
    assert (reclaim["from_state"], reclaim["to_state"]) == ("claimed", "ready")
    assert reclaim["reclaimed"] == {"epoch": 1, "reason": "expires_at", "by": "claude", "holder": "claude"}
    assert reclaim["fence_epoch"] == 2
    assert "lease_lost" not in reclaim  # une reprise n'est pas une tentative comptée par la récolte


def test_another_actor_reclaims_an_expired_claim_of_a_fenced_host(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.hosts.capabilities as caps

    service = TaskService(governed)
    tid = _ready(service, "Expiré").id
    _stale_claim(service, tid, "claude")
    monkeypatch.setattr(caps, "claim_host_is_fenced", lambda host, version: True)
    assert service.claim(tid, "codex").task.claim.actor_id == "codex"  # type: ignore[union-attr]


def test_another_actor_cannot_take_an_expired_claim_of_an_unfenced_host(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Expiré").id
    _stale_claim(service, tid, "claude")
    with pytest.raises(GrimoireMissionError, match="sans frein vérifié"):
        service.claim(tid, "codex")
    assert service.require(tid).status is TaskState.CLAIMED


def test_a_live_claim_is_still_refused(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Vivant").id
    service.claim(tid, "claude")
    with pytest.raises(GrimoireMissionError, match="déjà claimed par claude"):
        service.claim(tid, "claude")


def test_a_heartbeat_lost_claim_is_reclaimable_and_the_old_holder_is_fenced(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Silencieux").id
    service.claim(tid, "claude")
    path = lease_path(service.ledger_root, tid)
    write_lease(path, LeaseFile(tid, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    service.claim(tid, "claude")
    assert service.ledger.check_holder(tid, 1).ok is False
    assert service.ledger.check_holder(tid, 3).ok is True


# ── 4. revue d'une tâche sans commande déclarée ──────────────────────────────


def test_verify_without_checks_records_a_declarative_pack(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Preuve fichier").id
    proof = governed / "docs" / "capture.md"
    proof.parent.mkdir(parents=True)
    proof.write_bytes(b"# capture\n")
    root = str(governed)
    result = runner.invoke(
        app, ["-o", "json", "task", "verify", tid, "--project-root", root, "--evidence", "docs/capture.md"]
    )
    assert result.exit_code == 4, result.output
    payload = json.loads(result.stdout)
    assert payload["declarative"] is True and payload["verdict"]["verdict"] == "skipped"
    assert payload["verdict"]["created_by"] == "declarative"
    evidence = EvidenceService(governed / "_grimoire-runtime-output" / "evidence")
    [pack] = evidence.list_packs(tid)
    [item] = pack.items
    assert item.uri == "file:docs/capture.md"
    assert item.digest == f"sha256-{sha256(b'# capture' + bytes([10])).hexdigest()}"
    assert "déclarative" in item.summary
    verdict = evidence.get_latest_verdict(tid)
    assert verdict is not None and verdict.verdict is VerdictResult.SKIPPED
    assert not verdict.decision.close_task


@pytest.mark.parametrize("bad", ["absent.md", "../dehors.md", "/etc/hostname", "docs"])
def test_declarative_evidence_refuses_what_is_not_a_project_file(governed: Path, bad: str) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Preuve fichier").id
    (governed / "docs").mkdir()
    result = runner.invoke(app, ["task", "verify", tid, "--project-root", str(governed), "--evidence", bad])
    assert result.exit_code == 2, result.output
    assert not EvidenceService(governed / "_grimoire-runtime-output" / "evidence").list_packs(tid)


def test_verify_without_checks_nor_evidence_still_refuses(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Rien").id
    result = runner.invoke(app, ["task", "verify", tid, "--project-root", str(governed)])
    assert result.exit_code == 2
    assert "--evidence" in result.output


def test_declarative_pack_opens_review_but_never_closes(governed: Path) -> None:
    service = TaskService(governed)
    tid = _ready(service, "Revue").id
    service.context(tid)
    service.claim(tid, "claude")
    service.transition(tid, TaskState.RUNNING, "claude")
    (governed / "capture.png").write_bytes(b"png")
    trace = governed / "_grimoire-output" / "decisions" / tid / "decision-trace.yaml"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("decisions: []\n", encoding="utf-8")
    result = runner.invoke(app, ["task", "verify", tid, "--project-root", str(governed), "--evidence", "capture.png"])
    assert result.exit_code == 4, result.output
    assert service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude").task.status is TaskState.NEEDS_VERIFICATION
    with pytest.raises(GrimoireMissionError, match="skipped"):
        service.transition(tid, TaskState.CLOSED, "claude")


# ── 5. le WIP ne compte que les claims vivants ───────────────────────────────


def _wip(root: Path, total: int) -> None:
    (root / STANDARD / "capacity.yaml").write_text(f"version: 1\nwip:\n  limit_global: {total}\n", encoding="utf-8")


def test_expired_claims_do_not_count_against_the_global_wip(governed: Path) -> None:
    service = TaskService(governed)
    _wip(governed, 2)
    stale = [_ready(service, f"vieux {i}", owner=f"r{i}").id for i in range(2)]
    for tid in stale:
        _stale_claim(service, tid, "claude")
    fresh = _ready(service, "neuf", owner="r9").id
    assert service.claim(fresh, "claude").task.status is TaskState.CLAIMED


def test_a_heartbeat_lost_claim_does_not_count_either_but_a_live_one_does(governed: Path) -> None:
    service = TaskService(governed)
    _wip(governed, 1)
    first = _ready(service, "premier", owner="a").id
    service.claim(first, "claude")
    second = _ready(service, "second", owner="b").id
    with pytest.raises(GrimoireMissionError, match=r"WIP global atteint \(1/1\)"):
        service.claim(second, "claude")
    write_lease(lease_path(service.ledger_root, first), LeaseFile(first, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    assert service.claim(second, "claude").task.status is TaskState.CLAIMED


def test_admission_view_counts_only_live_claims(governed: Path) -> None:
    from grimoire.missions.admission import derive_view, load_capacity

    service = TaskService(governed)
    tid = _ready(service, "vieux").id
    _stale_claim(service, tid, "claude")
    view = derive_view(service.ledger, load_capacity(governed), now=datetime.now(UTC))
    assert view.held == ()
