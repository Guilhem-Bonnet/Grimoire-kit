"""Issue #1259 — les décisions de hook lisent le ledger du projet d'origine dans un worktree de tâche.

Suite de #1235 (PR #1258, frein du bail). Dans un worktree de tâche (W5-02), ``TaskService`` écrit
au ledger que désigne le marqueur du worktree ; les décisions de hook lisaient
``<worktree>/_grimoire-runtime-output/ledger`` — une copie figée (suivie par git, ou copiée) ou
rien du tout. Effets : la tâche active d'un worker retombait sur ``bootstrap``, la garde de
clôture « tâche encore tenue » se taisait au ``Stop``, une tâche garée ne couvrait plus le
travail de sa session, le contournement humain était refusé, consignes et vue de soi restaient
muettes.

Chaque décision est jouée sur cinq états : copie figée, sans copie (les deux doivent lire le
ledger d'origine), marqueur illisible et ledger désigné absent (échec fermé, propre à chaque
décision), et le projet d'origine (témoin, inchangé).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import ActiveTask, resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.decisions import gate_override as go
from grimoire.hosts.decisions._shared import HookInput
from grimoire.hosts.decisions.enrolment import parked_task_of_session, stop_scope
from grimoire.hosts.decisions.evidence_gate import decide_evidence_gate
from grimoire.hosts.decisions.pause_brake import _task_status
from grimoire.hosts.decisions.self_view import self_view_context
from grimoire.hosts.decisions.steering import steering_context
from grimoire.hosts.events import HookEvent
from grimoire.missions.decisions import decisions_summary
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MARKER_NAME

SESSION = "00000000-0000-4000-8000-000000001259"
ENV = {"CLAUDE_CODE_SESSION_ID": SESSION}
HOST = "claude-code-cli"
ACCEPTATION = "les décisions de hook lisent le ledger du projet d'origine"

GOOD = ("copie_figee", "sans_copie")
BAD = ("marqueur_illisible", "ledger_absent")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "GRIMOIRE_HOST_VERSION",
        "GRIMOIRE_UNATTENDED",
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


def _marker(wt: Path) -> Path:
    return Path((wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip()) / MARKER_NAME


def _worktree(home: Path, tid: str, *, copy: bool) -> Path:
    """Un worktree de tâche : ``.git`` fichier, marqueur vers le ledger du projet, profil suivi par git."""
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
    _governed(wt)
    shutil.rmtree(wt / "_grimoire-runtime-output")  # ignoré par git : un worktree neuf n'en a pas
    if copy:  # suivi par git, ou copié : figé à l'état d'avant le claim
        shutil.copytree(service.ledger_root, wt / "_grimoire-runtime-output" / "ledger")
    TaskService(wt).context(tid)
    return wt


def _break(wt: Path, state: str, tmp: Path) -> None:
    if state == "marqueur_illisible":
        _marker(wt).write_text("{pas du json", encoding="utf-8")
    elif state == "ledger_absent":  # projet déplacé, `git worktree repair` : chemin absolu périmé
        raw = json.loads(_marker(wt).read_text(encoding="utf-8"))
        _marker(wt).write_text(json.dumps({**raw, "ledger_root": str(tmp / "ailleurs" / "ledger")}), encoding="utf-8")


def _task(home: Path) -> str:
    service = TaskService(home)
    tid = service.add("Tâche", (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    return tid


def _held_from(cwd: Path, tid: str) -> None:
    """La session réclame la tâche depuis *cwd* et s'y rattache (journal : tâche et epoch)."""
    service = TaskService(cwd)
    service.claim(tid, "claude", host="local")
    assert service.attach_current_session(tid, actor="claude", env=ENV) == SESSION


def _setup(home: Path, tmp: Path, state: str) -> tuple[Path, str]:
    """``(racine du hook, tâche)`` : la session tient la tâche, la racine est dans l'état *state*."""
    tid = _task(home)
    if state == "origine":
        _held_from(home, tid)
        return home, tid
    wt = _worktree(home, tid, copy=state == "copie_figee")
    _held_from(wt, tid)
    _break(wt, state, tmp)
    return wt, tid


def _green_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grimoire.hosts.decisions.evidence_gate._gate_summary",
        lambda _root, task_id: (True, "", {"task_id": task_id, "state": "in_progress", "ok": True, "missing": []}),
    )


def _stop(root: Path, *, active: bool = False) -> Any:
    return decide_evidence_gate(
        HookInput(event=HookEvent.STOP, project_root=root, session_id=SESSION, host=HOST, stop_active=active)
    )


# ── Résolution de la tâche active (standard_state) ───────────────────────────


@pytest.mark.parametrize("state", [*GOOD, "origine"])
def test_active_task_is_the_session_claim(home: Path, tmp_path: Path, state: str) -> None:
    root, tid = _setup(home, tmp_path, state)
    active = resolve_active_task(root, session_id=SESSION, env={})
    assert (active.task_id, active.source) == (tid, "session_claim")


@pytest.mark.parametrize("state", BAD)
def test_active_task_says_unreadable_when_the_designated_ledger_is_unverifiable(
    home: Path, tmp_path: Path, state: str
) -> None:
    root, _tid = _setup(home, tmp_path, state)
    active = resolve_active_task(root, session_id=SESSION, env={})
    assert (active.task_id, active.source) == ("bootstrap", "unreadable")
    assert any("projet d'origine" in item for item in active.unreadable), active.unreadable


# ── Garde de clôture : « tâche encore tenue » au Stop (evidence_gate) ────────


@pytest.mark.parametrize("state", [*GOOD, "origine"])
def test_stop_blocks_a_held_unparked_task(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _green_gates(monkeypatch)
    root, tid = _setup(home, tmp_path, state)
    decision = _stop(root)
    assert decision.outcome is Outcome.BLOCK, decision.reason
    assert decision.detail["blocked_on"] == "task_unparked" and decision.detail["task_id"] == tid
    assert _stop(root, active=True).outcome is Outcome.ALLOW  # le second Stop sort toujours


@pytest.mark.parametrize("state", BAD)
def test_stop_blocks_once_when_a_held_task_cannot_be_verified(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _green_gates(monkeypatch)
    root, _tid = _setup(home, tmp_path, state)
    decision = _stop(root)
    assert decision.outcome is Outcome.BLOCK, decision.reason
    assert decision.detail["task_source"] == "unreadable"
    assert "projet d'origine" in decision.reason
    assert _stop(root, active=True).outcome is Outcome.ALLOW


@pytest.mark.parametrize("state", BAD)
def test_stop_with_a_named_task_blocks_when_the_ledger_cannot_be_verified(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _green_gates(monkeypatch)
    root, tid = _setup(home, tmp_path, state)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)  # la tâche nommée : la résolution ne lit pas le ledger
    decision = _stop(root)
    assert decision.outcome is Outcome.BLOCK, decision.reason
    assert decision.detail["blocked_on"] == "ledger_unverifiable" and decision.detail["task_id"] == tid


# ── Tâche garée qui couvre le travail de sa session (enrolment) ─────────────


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_parked_task_of_session(home: Path, tmp_path: Path, state: str) -> None:
    tid = _task(home)
    root = home if state == "origine" else _worktree(home, tid, copy=state == "copie_figee")
    _held_from(root, tid)
    TaskService(root).wait(tid, "ci", "claude")
    _break(root, state, tmp_path)
    parked = parked_task_of_session(HookInput(event=HookEvent.STOP, project_root=root, session_id=SESSION))
    if state in BAD:
        assert parked is None  # invérifiable : aucune exemption, le travail de la session reste jugé
    else:
        assert parked is not None and parked.id == tid


# ── Stop limité à la tâche de la session (enrolment.stop_scope) ─────────────


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_stop_scope_drops_a_claim_held_by_another_session(home: Path, tmp_path: Path, state: str) -> None:
    tid = _task(home)
    root = home if state == "origine" else _worktree(home, tid, copy=state == "copie_figee")
    TaskService(home).claim(tid, "autre", host="local")
    TaskService(home).attach_session(tid, "session-d-un-autre", session_host=HOST, actor="autre")
    _break(root, state, tmp_path)
    active = ActiveTask(tid, "ledger_claim")
    scoped = stop_scope(HookInput(event=HookEvent.STOP, project_root=root, session_id=SESSION), active)
    if state in BAD:
        assert scoped == active  # invérifiable : la tâche reste jugée, jamais retirée du Stop
    else:
        assert (scoped.task_id, scoped.source) == ("bootstrap", "bootstrap")


# ── Contournement humain d'un refus de clôture (gate_override) ──────────────


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_override_request(home: Path, tmp_path: Path, state: str) -> None:
    root, tid = _setup(home, tmp_path, state)
    hook = HookInput(
        event=HookEvent.USER_PROMPT_SUBMIT,
        project_root=root,
        prompt=f"Override Gate {tid}: livraison urgente",
        session_id=SESSION,
        host=HOST,
    )
    _context, detail = go.record_override_request(hook)
    expected = "refused" if state in BAD else "granted"  # invérifiable : aucun contournement accordé
    assert detail["override"]["status"] == expected


# ── Contexte informatif : vue de soi, consignes, décisions en attente ───────


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_self_view(home: Path, tmp_path: Path, state: str) -> None:
    root, tid = _setup(home, tmp_path, state)
    line = self_view_context(root, tid, SESSION)
    assert (line == "") is (state in BAD)
    if state not in BAD:
        assert tid in line


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_steering_delivers_human_directives(home: Path, tmp_path: Path, state: str) -> None:
    tid = _task(home)
    root = home if state == "origine" else _worktree(home, tid, copy=state == "copie_figee")
    _held_from(root, tid)
    TaskService(home).comment(tid, "passe par la branche de secours", "guilhem", kind="directive", origin="human")
    _break(root, state, tmp_path)
    text, detail = steering_context(root, tid, SESSION)
    if state in BAD:
        assert "passe par la branche de secours" not in text
        assert "projet d'origine" in text and detail.get("ledger_unverifiable")
        assert TaskService(home).require(tid).pending_directives  # rien n'a été marqué livré
    else:
        assert "passe par la branche de secours" in text
        assert not TaskService(home).require(tid).pending_directives  # marquée livrée au ledger d'origine


@pytest.mark.parametrize("state", [*GOOD, "origine", *BAD])
def test_decisions_summary_counts_pending_questions(home: Path, tmp_path: Path, state: str) -> None:
    tid = _task(home)
    root = home if state == "origine" else _worktree(home, tid, copy=state == "copie_figee")
    _held_from(root, tid)
    TaskService(root).ask(tid, "Quelle base ?", "claude", options=("main", "release"))
    _break(root, state, tmp_path)
    assert decisions_summary(root)["count"] == (0 if state in BAD else 1)


# ── Frein de pause : état de la tâche au point de reprise (pause_brake) ─────


def test_pause_task_status_reads_the_marker_ledger_root(home: Path, tmp_path: Path) -> None:
    """Ledger configuré hors du chemin par défaut : seul le marqueur dit où il est."""
    custom = TaskService(home, ledger_root=Path("ailleurs/ledger"))
    tid = custom.add("Tâche", (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    custom.context(tid)
    wt = home.parent / "wt-custom"
    gitdir = home.parent / "gitdir-custom"
    wt.mkdir()
    gitdir.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    marker = {
        "$schema": "grimoire-task-worktree/v1",
        "task_id": tid,
        "branch": f"task/{tid}",
        "base_sha": "0" * 40,
        "into": "main",
        "project_root": str(custom.home_root),
        "ledger_root": str(custom.ledger_root),
    }
    (gitdir / MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    custom.claim(tid, "claude", host="local")
    assert _task_status(wt, tid) == "claimed"


@pytest.mark.parametrize("state", [*GOOD, "origine"])
def test_pause_task_status(home: Path, tmp_path: Path, state: str) -> None:
    root, tid = _setup(home, tmp_path, state)
    assert _task_status(root, tid) == "claimed"


@pytest.mark.parametrize("state", BAD)
def test_pause_task_status_raises_when_unverifiable(home: Path, tmp_path: Path, state: str) -> None:
    """Le frein de pause rattrape l'exception en ``ask`` : jamais un faux « point de reprise déjà déposé »."""
    root, tid = _setup(home, tmp_path, state)
    with pytest.raises(Exception, match=r"projet d'origine|marqueur"):
        _task_status(root, tid)


# ── Le board fait foi au projet d'origine (projection de TaskService.project_board) ──


@pytest.mark.parametrize("state", [*GOOD, "origine"])
def test_stop_judges_the_real_gates_not_a_stale_board_copy(home: Path, tmp_path: Path, state: str) -> None:
    """Sans patch des gates : la tâche est sur le board du projet d'origine, ses vrais manques sont nommés."""
    root, tid = _setup(home, tmp_path, state)
    decision = _stop(root)
    assert "is not on the board" not in decision.reason
    assert decision.detail.get("task_id") == tid and decision.detail.get("state") == "in_progress"


@pytest.mark.parametrize("state", GOOD)
def test_a_stale_board_card_in_the_worktree_names_no_task(home: Path, tmp_path: Path, state: str) -> None:
    tid = _task(home)
    wt = _worktree(home, tid, copy=state == "copie_figee")
    board = wt / "_grimoire/standard/task-board.yaml"
    board.write_text(
        "tasks:\n  - task_id: CARTE-PERIMEE\n    status: in_progress\n    title: copie figée\n", encoding="utf-8"
    )
    active = resolve_active_task(wt, session_id=SESSION, env={})
    assert active.task_id != "CARTE-PERIMEE"
