"""Issue #1235 — frein du bail : « epoch 3 périmé, bail à 2 » alors que le bail est à l'epoch 3.

Cause reproduite : dans un worktree de tâche (W5-02), le frein lisait le fichier de bail sous
``<worktree>/_grimoire-runtime-output/ledger`` alors que ``TaskService`` — claim, renouvellement,
rattachement — écrit au ledger du projet d'origine (marqueur du worktree). Le journal de la
session reçoit l'epoch du vrai claim (3) ; le frein le comparait à une copie du ledger restée
dans le worktree (suivie par git, ou copiée), figée à la barrière de récolte (2). Sans copie, le
frein ne trouvait rien et se taisait : une session périmée écrivait librement depuis un worktree.

Ces tests jouent les deux cas sur le chemin réel, plus les chemins couverts du projet d'origine
(re-claim après récolte, renouvellement, reprise après attente) et le message de refus, qui
nomme l'epoch de la session puis celui du bail, chacun avec sa source.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.decisions._shared import HookInput
from grimoire.hosts.decisions.lease_brake import held_task_id, lease_brake
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.hosts.events import HookEvent
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MARKER_NAME
from grimoire.policies.session_state import load_session_state, note_session_task, session_state_path

SESSION = "00000000-0000-4000-8000-000000001235"
ENV = {"CLAUDE_CODE_SESSION_ID": SESSION}
ACCEPTATION = "le frein du bail lit le ledger du projet d'origine"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "GRIMOIRE_HOST_VERSION",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home-dir"))
    root = tmp_path / "projet"
    root.mkdir()
    _governed(root)
    return root


def _governed(root: Path) -> None:
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )


def _worktree(home: Path, tid: str) -> Path:
    """Un worktree de tâche tel que ``create_worktree`` le laisse : ``.git`` fichier, marqueur dans son dossier git."""
    service = TaskService(home)
    wt = home.parent / "wt" / tid
    gitdir = home.parent / "gitdir" / tid
    wt.mkdir(parents=True)
    gitdir.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    marker = {
        "$schema": "grimoire-task-worktree/v1",
        "task_id": tid,
        "branch": f"task/{tid}",
        "base_sha": "0" * 40,
        "into": "main",
        "project_root": str(service.home_root),
        "ledger_root": str(service.ledger_root),
    }
    (gitdir / MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    _governed(wt)  # `_grimoire/` est suivi par git : le worktree porte le même profil
    shutil.rmtree(wt / "_grimoire-runtime-output")  # ignoré par git : un worktree neuf n'en a pas
    TaskService(wt).context(tid)  # le gate du claim lit le paquet de contexte du worktree
    return wt


def _ready(root: Path) -> str:
    service = TaskService(root)
    tid = service.add("Tâche", (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    return tid


def _reap(root: Path, tid: str) -> None:
    """Le bail de *tid* n'a plus battu depuis une heure : la récolte pose sa barrière (epoch suivant, sans titulaire)."""
    service = TaskService(root)
    epoch = service.require(tid).claim.lease_epoch  # type: ignore[union-attr]
    write_lease(
        lease_path(service.ledger_root, tid),
        LeaseFile(tid, epoch, (datetime.now(UTC) - timedelta(hours=1)).isoformat()),
    )
    [decision] = service.ledger.reap(is_fenced=lambda _view: True).decisions  # hôte tenu pour fencé : récolte franche
    assert decision.action == "ready", decision


def _edit(root: Path) -> HookInput:
    return HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=root,
        tool_name="Edit",
        tool_input={"file_path": str(root / "src.py"), "old_string": "a", "new_string": "b"},
        session_id=SESSION,
    )


def _brake(root: Path) -> object:
    hook = _edit(root)
    return lease_brake(hook, classify_tool(hook.tool_name, hook.tool_input))


def _claim_epoch(root: Path, tid: str) -> int:
    claim = TaskService(root).require(tid).claim
    assert claim is not None
    return claim.lease_epoch


def _reclaimed_at_epoch_3(home: Path, tid: str, cwd: Path) -> None:
    """claim (1) → récolte (barrière 2) → la même session réclame de nouveau (3), depuis *cwd*."""
    TaskService(home).claim(tid, "claude", host="local")
    _reap(home, tid)
    service = TaskService(cwd)
    service.claim(tid, "claude", host="local")
    assert service.attach_current_session(tid, actor="claude", env=ENV) == SESSION


# ── La cause : le worktree de tâche ─────────────────────────────────────────


def test_worktree_stale_ledger_copy_no_longer_refuses_the_current_epoch(home: Path) -> None:
    """Le scénario rapporté : bail à 3 au ledger du projet, copie du worktree figée à la barrière 2."""
    tid = _ready(home)
    TaskService(home).claim(tid, "claude", host="local")
    _reap(home, tid)
    wt = _worktree(home, tid)
    shutil.copytree(TaskService(home).ledger_root, wt / "_grimoire-runtime-output" / "ledger")  # copie figée à 2
    service = TaskService(wt)
    assert service.ledger_root == TaskService(home).ledger_root  # le service écrit au ledger du projet
    service.claim(tid, "claude", host="local")
    assert service.attach_current_session(tid, actor="claude", env=ENV) == SESSION

    assert _claim_epoch(home, tid) == 3
    assert read_lease(lease_path(TaskService(home).ledger_root, tid)).epoch == 3  # type: ignore[union-attr]
    journal = load_session_state(wt, SESSION, now_iso="")
    assert (journal.task_id, journal.lease_epoch) == (tid, 3)
    stale_copy = read_lease(lease_path(wt / "_grimoire-runtime-output" / "ledger", tid))
    assert stale_copy is not None and stale_copy.epoch == 2 and not stale_copy.holder

    assert _brake(wt) is None  # avant #1235 : « epoch 3 périmé : le bail de … est à l'epoch 2 »
    assert held_task_id(_edit(wt)) == tid

    service.renew(tid, "claude")  # renouvellement depuis le worktree : même ledger, même epoch
    assert _brake(wt) is None


def test_worktree_without_ledger_copy_still_brakes_a_displaced_session(home: Path) -> None:
    """Sans copie du ledger dans le worktree, le frein se taisait : une session périmée écrivait."""
    tid = _ready(home)
    wt = _worktree(home, tid)
    _reclaimed_at_epoch_3(home, tid, cwd=wt)
    assert _brake(wt) is None

    _reap(home, tid)  # barrière 4
    TaskService(home).claim(tid, "autre", host="local")  # un autre titulaire, epoch 5
    assert not (wt / "_grimoire-runtime-output" / "ledger" / "events.jsonl").exists()

    decision = _brake(wt)
    assert decision is not None and decision.outcome is Outcome.DENY  # type: ignore[attr-defined]
    reason = decision.reason  # type: ignore[attr-defined]
    assert decision.detail["lease_brake"] == "stale_epoch"  # type: ignore[attr-defined]
    assert reason.index("epoch 3") < reason.index("epoch 5")
    assert str(session_state_path(wt, SESSION)) in reason
    assert str(lease_path(TaskService(home).ledger_root, tid)) in reason
    assert held_task_id(_edit(wt)) == ""


def test_unreadable_worktree_marker_is_never_an_allow(home: Path) -> None:
    tid = _ready(home)
    wt = _worktree(home, tid)
    _reclaimed_at_epoch_3(home, tid, cwd=wt)
    gitdir = Path((wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
    (gitdir / MARKER_NAME).write_text("{pas du json", encoding="utf-8")

    decision = _brake(wt)
    assert decision is not None and decision.outcome is Outcome.ASK  # type: ignore[attr-defined]
    assert held_task_id(_edit(wt)) == ""


# ── Les chemins du projet d'origine, inchangés ─────────────────────────────


def test_reclaim_after_reap_then_renew_accepts_the_current_epoch(home: Path) -> None:
    tid = _ready(home)
    _reclaimed_at_epoch_3(home, tid, cwd=home)
    assert load_session_state(home, SESSION, now_iso="").lease_epoch == 3
    assert _brake(home) is None
    TaskService(home).renew(tid, "claude")
    assert read_lease(lease_path(TaskService(home).ledger_root, tid)).epoch == 3  # type: ignore[union-attr]
    assert _brake(home) is None


def test_resume_after_wait_keeps_the_epoch_and_the_write(home: Path) -> None:
    tid = _ready(home)
    _reclaimed_at_epoch_3(home, tid, cwd=home)
    service = TaskService(home)
    service.wait(tid, "ci", "claude")
    service.resume(tid, "claude")
    assert _claim_epoch(home, tid) == 3
    assert read_lease(lease_path(service.ledger_root, tid)).epoch == 3  # type: ignore[union-attr]
    assert _brake(home) is None


def test_stale_epoch_message_names_both_values_in_order_with_their_source(home: Path) -> None:
    tid = _ready(home)
    _reclaimed_at_epoch_3(home, tid, cwd=home)
    note_session_task(home, SESSION, tid, now_iso="", lease_epoch=2)  # journal resté sur un epoch ancien

    decision = _brake(home)
    assert decision is not None and decision.outcome is Outcome.DENY  # type: ignore[attr-defined]
    reason = decision.reason  # type: ignore[attr-defined]
    assert "epoch 2 périmé" in reason
    assert reason.index("epoch 2") < reason.index("epoch 3")
    assert "session" in reason[: reason.index("epoch 3")]
    assert str(session_state_path(home, SESSION)) in reason
    assert str(lease_path(TaskService(home).ledger_root, tid)) in reason


def test_stale_epoch_message_on_cache_miss_names_the_ledger(home: Path) -> None:
    tid = _ready(home)
    _reclaimed_at_epoch_3(home, tid, cwd=home)
    note_session_task(home, SESSION, tid, now_iso="", lease_epoch=1)
    lease_path(TaskService(home).ledger_root, tid).unlink()  # défaut de cache : le ledger tranche

    decision = _brake(home)
    assert decision is not None and decision.outcome is Outcome.DENY  # type: ignore[attr-defined]
    reason = decision.reason  # type: ignore[attr-defined]
    assert reason.index("epoch 1") < reason.index("epoch 3")
    assert str(session_state_path(home, SESSION)) in reason
    assert str(TaskService(home).ledger_root / "events.jsonl") in reason
