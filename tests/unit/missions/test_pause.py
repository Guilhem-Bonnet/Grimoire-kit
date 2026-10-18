"""K11 (épopée #705) — pause globale, par projet ou par tâche : ``drain`` et ``hold``.

Constat sur la base (``aidlc/k10-queue-admission``) : ``PAUSED`` n'est émis nulle part
(``runtime/schemas.py``, ``flows/engine.py``) ; ``admit(paused=...)`` n'a aucun appelant qui calcule la pause ;
aucun drapeau ne dit « stoppe tout » ; le tick n'existe qu'en pièces (``task reap``, ``task wake``) et rien ne
l'empêche de réclamer, de récolter ou de dispatcher pendant qu'on veut tout arrêter ; un agent garé ``waiting(paused)``
n'est jamais rendu (``until`` vaut ``unknown`` et ``wake_due`` n'attend que des échéances).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.cmd_orchestrate import orchestrate_app
from grimoire.cli.cmd_queue import queue_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions import admission
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import (
    CONTROL_HOME_ENV,
    DRAIN,
    HOLD,
    PAUSE_RELPATH,
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    PauseError,
    clear_pause,
    effective_mode,
    read_pause,
    write_pause,
)
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.service import TaskService

ACC = "la pause arrête l'admission sans rien casser"
STANDARD = Path("_grimoire/standard")
SESSION = "00000000-0000-4000-8000-0000000000a1"
runner = CliRunner()
S = TaskState


@pytest.fixture(autouse=True)
def _isolated_control_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le drapeau de la machine pointe chez le test, jamais sous le vrai ``$HOME``."""
    home = tmp_path_factory.mktemp("control-home")
    monkeypatch.setenv(CONTROL_HOME_ENV, str(home))
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION",
        "GRIMOIRE_UNATTENDED", "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def machine(_isolated_control_home: Path) -> Path:
    return _isolated_control_home


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def mission(service: TaskService) -> str:
    return service.ledger.create_mission(title="Pause", origin="test").id


def _pret(service: TaskService, mission: str, titre: str, **kw: Any) -> MissionTask:
    kw.setdefault("owner", "amelia")
    task = service.ledger.create_task(mission, titre, acceptance=(ACC,), **kw)
    service.ledger.transition_task(task.id, S.READY)
    return task


def _running(service: TaskService, task: MissionTask, actor: str, session: str = "") -> str:
    service.claim(task.id, actor, host="host-claude-code-cli")
    service.transition(task.id, S.RUNNING, actor)
    if session:
        service.attach_session(task.id, session, session_host="claude-code-cli", actor=actor)
    return task.id


def _flag(projet: Path, **payload: Any) -> Path:
    path = projet / PAUSE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "grimoire-pause/v1", **payload}), encoding="utf-8")
    return path


# ── CA : le drapeau ferme l'admission ───────────────────────────────────────


def test_pause_file_blocks_admission(projet: Path, service: TaskService, mission: str) -> None:
    a, b = _pret(service, mission, "A"), _pret(service, mission, "B")
    assert {d.task_id for d in service.queue_plan(routes=False).admitted} == {a.id, b.id}  # contrôle : sans drapeau

    _flag(projet, mode="drain")
    plan = service.queue_plan(routes=False)
    assert plan.admitted == ()
    assert {(d.task_id, d.reason) for d in plan.deferred} == {(a.id, "pause"), (b.id, "pause")}
    assert "drain projet" in plan.deferred[0].detail
    # Le claim lui-même est refusé, sous le verrou du ledger : l'admission n'est pas qu'un plan.
    with pytest.raises(GrimoireMissionError, match=r"Claim refusé.*pause demandée"):
        service.claim(a.id, "agent-a", host="local")
    assert service.require(a.id).status is S.READY


@pytest.mark.parametrize(
    "content",
    [
        "{pas du json",
        '["une", "liste"]',
        '{"mode": "stop"}',
        '{"mode": "hold", "tasks": "T-1"}',
        '{"mode": "drain", "tasks": [1, 2]}',
    ],
)
def test_unreadable_pause_is_drain(projet: Path, service: TaskService, mission: str, content: str) -> None:
    task = _pret(service, mission, "A")
    path = projet / PAUSE_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    state = read_pause(projet)
    assert state.mode_for(task.id) == DRAIN and state.attention()
    plan = service.queue_plan(routes=False)
    assert plan.admitted == () and plan.deferred[0].reason == "pause"
    assert "illisible" in plan.deferred[0].detail
    with pytest.raises(GrimoireMissionError, match="pause illisible"):
        service.claim(task.id, "agent-a", host="local")


def test_unreadable_pause_covers_a_directory_and_a_permission_error(projet: Path) -> None:
    path = projet / PAUSE_RELPATH
    path.mkdir(parents=True)  # un dossier à la place du fichier : ni lisible, ni absent
    assert read_pause(projet).mode_for("T-1") == DRAIN
    path.rmdir()
    path.write_text('{"mode": "hold"}', encoding="utf-8")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("lecture permise malgré chmod 0 (exécution en root)")
        state = read_pause(projet)
        assert state.mode_for("T-1") == DRAIN and "illisible" in state.attention()[0]
    finally:
        path.chmod(0o644)


def test_a_missing_flag_is_no_pause_and_an_empty_task_list_is_the_whole_project(projet: Path) -> None:
    assert not read_pause(projet).active
    _flag(projet, mode="drain", tasks=[])
    state = read_pause(projet)
    assert state.global_stop and state.mode_for("T-quelconque") == DRAIN


def test_task_scoped_pause(projet: Path, service: TaskService, mission: str) -> None:
    a, b = _pret(service, mission, "A"), _pret(service, mission, "B")
    _flag(projet, mode="drain", tasks=[a.id])

    plan = service.queue_plan(routes=False)
    assert [d.task_id for d in plan.admitted] == [b.id]
    assert [(d.task_id, d.reason) for d in plan.deferred] == [(a.id, "pause")]
    with pytest.raises(GrimoireMissionError, match="Claim refusé"):
        service.claim(a.id, "agent-a", host="local")
    service.claim(b.id, "agent-b", host="local")  # l'autre tâche suit son chemin
    assert service.require(b.id).status is S.CLAIMED
    state = read_pause(projet)
    assert not state.global_stop and state.mode_for(a.id) == DRAIN and state.mode_for(b.id) is None


def test_global_and_project_union_hold_wins(projet: Path, service: TaskService, mission: str, machine: Path) -> None:
    task = _pret(service, mission, "A")
    _flag(projet, mode="drain")
    (machine / "pause.json").write_text(json.dumps({"mode": "hold", "tasks": []}), encoding="utf-8")
    state = read_pause(projet)
    assert [f.scope for f in state.flags] == [SCOPE_PROJECT, SCOPE_GLOBAL]
    assert state.mode_for(task.id) == HOLD  # hold (machine) l'emporte sur drain (projet)

    _flag(projet, mode="hold")
    (machine / "pause.json").write_text(json.dumps({"mode": "drain"}), encoding="utf-8")
    assert read_pause(projet).mode_for(task.id) == HOLD  # et inversement

    _flag(projet, mode="drain", tasks=["autre"])
    (machine / "pause.json").write_text(json.dumps({"mode": "drain"}), encoding="utf-8")
    assert read_pause(projet).mode_for(task.id) == DRAIN  # l'union couvre ce que l'un des deux couvre

    # La machine arrête aussi un projet qui n'a aucun drapeau à lui.
    autre = projet.parent / "autre-projet"
    (autre / STANDARD).mkdir(parents=True)
    other = TaskService(autre)
    mid = other.ledger.create_mission(title="Autre", origin="test").id
    _pret(other, mid, "B")
    assert other.queue_plan(routes=False).admitted == ()
    (machine / "pause.json").unlink()
    assert len(other.queue_plan(routes=False).admitted) == 1


def test_write_merge_clear_and_refusals(projet: Path, machine: Path) -> None:
    flag = write_pause(projet, mode=DRAIN, tasks=["T-1"], actor="guilhem", reason="revue")
    assert (flag.mode, flag.tasks, flag.actor, flag.reason) == (DRAIN, ("T-1",), "guilhem", "revue")
    write_pause(projet, mode=DRAIN, tasks=["T-2", "T-1"])
    assert read_pause(projet).flags[0].tasks == ("T-1", "T-2")  # même mode : les tâches s'ajoutent
    write_pause(projet, mode=HOLD, tasks=["T-3"])
    assert read_pause(projet).flags[0].tasks == ("T-3",)  # autre mode : remplacé

    clear_pause(projet, tasks=["T-3"])
    assert not read_pause(projet).active  # la dernière tâche levée supprime le drapeau
    write_pause(projet, mode=DRAIN, tasks=["T-1", "T-2"])
    clear_pause(projet, tasks=["T-1"])
    assert read_pause(projet).flags[0].tasks == ("T-2",)
    assert clear_pause(projet) is not None and not (projet / PAUSE_RELPATH).exists()
    assert clear_pause(projet) is None

    write_pause(projet, mode=DRAIN)
    with pytest.raises(PauseError, match=r"déjà couvert|couvre déjà"):
        write_pause(projet, mode=DRAIN, tasks=["T-1"])
    with pytest.raises(PauseError, match="tout le périmètre"):
        clear_pause(projet, tasks=["T-1"])
    with pytest.raises(PauseError, match="mode inconnu"):
        write_pause(projet, mode="stop")
    write_pause(projet, mode=HOLD, scope=SCOPE_GLOBAL)
    assert json.loads((machine / "pause.json").read_text(encoding="utf-8"))["mode"] == HOLD
    (projet / PAUSE_RELPATH).write_text("{illisible", encoding="utf-8")
    assert clear_pause(projet) is not None  # un drapeau illisible se supprime : le seul moyen de le réparer


def test_claim_guard_resumption_rules(projet: Path, service: TaskService, mission: str) -> None:
    """Un agent qui attendait la CI finit sa tâche sous drain, pas sous hold ; une attente ``paused`` attend la fin de la pause."""
    ci = _running(service, _pret(service, mission, "CI"), "agent-a", "S1")
    service.wait(ci, "ci", "agent-a")
    parked = _running(service, _pret(service, mission, "Pause"), "agent-b", "S2")
    service.wait(parked, "paused", "agent-b")

    _flag(projet, mode="drain")
    service.resume(ci, "agent-a")  # drain : les agents en cours finissent
    assert service.require(ci).status is S.RUNNING
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*pause"):
        service.resume(parked, "agent-b")  # une attente `paused` n'est pas levée par un agent tant que la pause dure

    service.wait(ci, "ci", "agent-a")
    _flag(projet, mode="hold")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*pause"):
        service.resume(ci, "agent-a")  # hold : plus personne ne reprend


def test_the_pause_checkpoint_records_the_branch_and_head(projet: Path, service: TaskService, mission: str) -> None:
    import subprocess

    def git(*args: str) -> str:
        done = subprocess.run(  # git, dépôt jetable du test
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
            cwd=projet, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return done.stdout.strip()

    git("init", "-q", "-b", "task/k11")
    (projet / "a.txt").write_text("a", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "init")
    head = git("rev-parse", "HEAD")

    parked = _running(service, _pret(service, mission, "Garée"), "agent-a", "S1")
    service.wait(parked, "paused", "agent-a")
    hint = service.require(parked).waiting.resume_hint  # type: ignore[union-attr]
    assert hint is not None and (hint.branch, hint.observed_head, hint.session_id) == ("task/k11", head, "S1")

    other = _running(service, _pret(service, mission, "CI"), "agent-b", "S2")
    service.wait(other, "ci", "agent-b")  # les autres attentes ne lisent pas le dépôt (K12 le récoltera)
    other_hint = service.require(other).waiting.resume_hint  # type: ignore[union-attr]
    assert other_hint is not None and other_hint.observed_head == ""


def test_effective_mode_degrades_hold_without_a_brake() -> None:
    assert effective_mode(HOLD, fenced=True) == (HOLD, "")
    assert effective_mode(HOLD, fenced=False) == (DRAIN, "hold dégradé en drain (hôte sans frein)")
    assert effective_mode(DRAIN, fenced=False) == (DRAIN, "")
    assert effective_mode(None, fenced=False) == (None, "")


# ── CA : le tick sort sans charger le ledger ni dispatcher ───────────────────


def test_paused_tick_exits_without_loading_the_ledger_or_dispatching(
    projet: Path, service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pret(service, mission, "A")
    _flag(projet, mode="drain")
    spy = mock.Mock()
    factory = mock.Mock(side_effect=AssertionError("le service (donc le ledger) ne doit pas être ouvert sous pause"))

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("le ledger ne doit pas être chargé sous pause")

    monkeypatch.setattr(MissionLedger, "__init__", _boom)
    monkeypatch.setattr(MissionLedger, "_load", _boom)
    report = run_tick(projet, service_factory=factory, dispatcher=spy)

    assert report.status == "paused" and report.woken == () and report.plan is None and report.dispatched == ()
    factory.assert_not_called()
    spy.assert_not_called()
    assert "continuent de courir" in report.notes[0]
    # Même verdict par le chemin par défaut (sans fabrique injectée) et par la CLI : code 0, rien d'ouvert.
    assert run_tick(projet).status == "paused"
    res = runner.invoke(orchestrate_app, ["tick", "--apply", "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    assert "sort sans ouvrir le ledger" in res.output


def test_unreadable_pause_also_stops_the_tick(projet: Path) -> None:
    (projet / PAUSE_RELPATH).parent.mkdir(parents=True)
    (projet / PAUSE_RELPATH).write_text("{", encoding="utf-8")
    report = run_tick(projet, service_factory=mock.Mock(side_effect=AssertionError("ledger ouvert")))
    assert report.status == "paused" and "illisible" in report.attention[0]


def test_global_pause_stops_the_tick_of_every_project(projet: Path, machine: Path) -> None:
    (machine / "pause.json").write_text('{"mode": "drain"}', encoding="utf-8")
    assert run_tick(projet, service_factory=mock.Mock(side_effect=AssertionError("ledger ouvert"))).status == "paused"


def test_a_task_scoped_pause_leaves_the_tick_running(projet: Path, service: TaskService, mission: str) -> None:
    a, b = _pret(service, mission, "A"), _pret(service, mission, "B")
    _flag(projet, mode="hold", tasks=[a.id])
    spy = mock.Mock()
    report = run_tick(projet, dispatcher=spy)
    assert report.status == "ran"
    assert [x.task_id for x in report.plan.admitted] == [b.id]  # type: ignore[union-attr]
    spy.assert_called_once()
    assert [x.task_id for x in spy.call_args.args[0]] == [b.id]
    assert dict(report.plan.deferred[0].to_dict())["reason"] == "pause"  # type: ignore[union-attr]
    assert any(a.id in note for note in report.notes)


def test_a_free_tick_dispatches_only_when_applying(projet: Path, service: TaskService, mission: str) -> None:
    _pret(service, mission, "A")
    spy = mock.Mock()
    assert run_tick(projet, apply=False, dispatcher=spy).status == "ran"
    spy.assert_not_called()
    run_tick(projet, apply=True, dispatcher=spy)
    spy.assert_called_once()


def test_a_tick_without_a_ledger_has_nothing_to_do(projet: Path) -> None:
    assert run_tick(projet).status == "idle"


# ── CA : resume restitue l'ordre de la file ──────────────────────────────────


def test_resume_restores_priority_order(projet: Path, service: TaskService, mission: str) -> None:
    low = _pret(service, mission, "basse", priority="low")
    # Même priorité, créées dans l'ordre X1 puis X2 mais prêtes dans l'ordre inverse : X2 est la plus ancienne.
    ledger = service.ledger
    x1 = ledger.create_task(mission, "X1", acceptance=(ACC,), owner="amelia", priority="high")
    x2 = ledger.create_task(mission, "X2", acceptance=(ACC,), owner="amelia", priority="high")
    ledger.transition_task(x2.id, S.READY)
    ledger.transition_task(x1.id, S.READY)
    before = admission.ready_since(ledger)
    expected = [x2.id, x1.id, low.id]
    assert [e.task.id for e in admission.queue_order([service.require(t) for t in expected], before, now=datetime.now(UTC))] == expected

    for n, task in enumerate((x1, low, x2)):  # parquées dans un autre ordre encore
        _running(service, task, f"agent-{n}")
        service.wait(task.id, "paused", f"agent-{n}")
    write_pause(projet, mode=HOLD)
    assert {d.action for d in service.wake_due()} == {"keep"}  # la pause dure : personne n'est rendu
    assert all(service.require(t).status is S.WAITING for t in expected)

    clear_pause(projet)
    decisions = service.wake_due()
    assert [d.task_id for d in decisions if d.written] == [x1.id, low.id, x2.id] or len({d.cause for d in decisions}) == 1
    assert {d.cause for d in decisions} == {"pause levée"}
    woken = [e.entity_id for e in service.ledger.list_events() if e.event_type == "task.transitioned" and e.payload.get("woken")]
    assert woken == expected  # réveillées dans l'ordre de la file, pas dans celui du ledger
    assert admission.ready_since(service.ledger) == before  # et sans perdre leur ancienneté
    plan = service.queue_plan(routes=False)
    assert [t for t, _b, _e in plan.order] == expected
    assert [a.task_id for a in plan.admitted] == expected


def test_a_waiting_paused_task_with_a_date_waits_for_its_date(projet: Path, service: TaskService, mission: str) -> None:
    task = _running(service, _pret(service, mission, "Datée"), "agent-a")
    service.wait(task, "paused", "agent-a", until="2999-01-01T00:00:00+00:00")
    [decision] = service.wake_due()
    assert decision.action == "keep"  # pas de pause, mais l'échéance n'est pas venue : elle attend toujours
    [due] = service.wake_due(now=datetime(3000, 1, 1, tzinfo=UTC))
    assert due.action == "wake" and "échéance" in due.cause


def test_pause_wakes_are_applied_by_the_tick(projet: Path, service: TaskService, mission: str) -> None:
    task = _running(service, _pret(service, mission, "Garée"), "agent-a", "S1")
    service.wait(task, "paused", "agent-a")
    write_pause(projet, mode=HOLD)
    held = run_tick(projet)
    assert held.status == "paused" and TaskService(projet).require(task).status is S.WAITING
    clear_pause(projet)
    done = run_tick(projet)
    assert [(w.task_id, w.action, w.written) for w in done.woken] == [(task, "wake", True)]
    assert TaskService(projet).require(task).status is S.READY


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_pause_status_resume_roundtrip(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = ["--project-root", str(projet)]
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")

    posted = runner.invoke(orchestrate_app, ["pause", "--hold", "--task", "T-1", "--task", "T-2", "--reason", "revue", *root])
    assert posted.exit_code == 0, posted.output
    flag = json.loads((projet / PAUSE_RELPATH).read_text(encoding="utf-8"))
    assert (flag["mode"], flag["tasks"], flag["reason"]) == ("hold", ["T-1", "T-2"], "revue")

    status = runner.invoke(orchestrate_app, ["status", "--json", *root])
    assert status.exit_code == 0 and json.loads(status.output)["flags"][0]["mode"] == "hold"
    assert "hold projet, tâches T-1, T-2" in runner.invoke(orchestrate_app, ["status", *root]).output

    assert runner.invoke(orchestrate_app, ["pause", "--drain", "--hold", *root]).exit_code == 2
    assert runner.invoke(orchestrate_app, ["pause", "--global", "--task", "T-1", *root]).exit_code == 2

    lifted = runner.invoke(orchestrate_app, ["resume", "--task", "T-1", *root])
    assert lifted.exit_code == 0 and json.loads((projet / PAUSE_RELPATH).read_text(encoding="utf-8"))["tasks"] == ["T-2"]
    assert runner.invoke(orchestrate_app, ["resume", *root]).exit_code == 0
    assert not (projet / PAUSE_RELPATH).exists()
    assert "Pas de pause" in runner.invoke(orchestrate_app, ["status", *root]).output
    assert "Aucune pause" in runner.invoke(orchestrate_app, ["resume", *root]).output


def test_resume_and_global_pause_are_human_gestures(projet: Path, machine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = ["--project-root", str(projet)]
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "agent")
    assert runner.invoke(orchestrate_app, ["pause", *root]).exit_code == 0  # se garer est le sens sûr
    assert runner.invoke(orchestrate_app, ["pause", "--global", *root]).exit_code == 1
    assert not (machine / "pause.json").exists()
    assert runner.invoke(orchestrate_app, ["resume", *root]).exit_code == 1  # un agent ne lève pas la pause
    assert (projet / PAUSE_RELPATH).exists()
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")
    assert runner.invoke(orchestrate_app, ["pause", "--global", "--hold", *root]).exit_code == 0
    assert json.loads((machine / "pause.json").read_text(encoding="utf-8"))["mode"] == "hold"
    assert runner.invoke(orchestrate_app, ["resume", "--global", *root]).exit_code == 0
    assert not (machine / "pause.json").exists()


def test_queue_plan_shows_the_pause(projet: Path, service: TaskService, mission: str) -> None:
    _pret(service, mission, "A")
    _flag(projet, mode="hold")
    res = runner.invoke(queue_app, ["plan", "--no-routes", "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    assert "pause : hold projet" in res.output and "Defer(pause)" in res.output
    data = json.loads(runner.invoke(queue_app, ["plan", "--no-routes", "--json", "--project-root", str(projet)]).output)
    assert data["pause"]["flags"][0]["mode"] == "hold" and data["admitted"] == []


# ── Hooks : le frein de PreToolUse, la consigne du tour, la pause levée ──────


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def _transcript(root: Path, version: str = "2.1.301") -> Path:
    path = root / "transcript.jsonl"
    path.write_text(json.dumps({"type": "assistant", "version": version}) + "\n", encoding="utf-8")
    return path


def _hook(root: Path, event: HookEvent, payload: dict[str, Any], host: HostId = HostId.CLAUDE_CODE_CLI) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=host, event=event, project_root=root)
    return rendered, decision


def _prompt(root: Path, host: HostId = HostId.CLAUDE_CODE_CLI, version: str = "2.1.301") -> Any:
    payload = {
        "session_id": SESSION, "hook_event_name": "UserPromptSubmit", "cwd": str(root), "prompt": "go",
        "transcript_path": str(_transcript(root, version)),
    }
    return _hook(root, HookEvent.USER_PROMPT_SUBMIT, payload, host)[1]


def _pre(
    root: Path, tool: str = "Bash", host: HostId = HostId.CLAUDE_CODE_CLI, _version: str = "2.1.301", **tool_input: Any
) -> tuple[dict[str, Any], Any]:
    payload = {
        "session_id": SESSION, "hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": tool,
        "tool_input": tool_input, "transcript_path": str(_transcript(root, _version)), "tool_use_id": "toolu_k11",
    }
    return _hook(root, HookEvent.PRE_TOOL_USE, payload, host)


def _verdict(rendered: dict[str, Any]) -> str:
    return str(rendered["hookSpecificOutput"]["permissionDecision"])


def _reason(rendered: dict[str, Any]) -> str:
    return str(rendered["hookSpecificOutput"].get("permissionDecisionReason", ""))


def _stop(root: Path) -> Any:
    payload = {"session_id": SESSION, "hook_event_name": "Stop", "cwd": str(root), "stop_hook_active": False}
    return _hook(root, HookEvent.STOP, payload)[1]


def _claimed(root: Path, title: str = "Tâche", actor: str = "claude") -> str:
    service = TaskService(root)
    tid = service.add(title, (ACC,), owner="guilhem", actor=actor, ready=True).task.id
    service.context(tid)
    service.claim(tid, actor, host="local")
    return tid


TOUCH: dict[str, Any] = {"command": "touch EXECUTED.marker"}


def test_pretooluse_denies_then_allows_one_checkpoint(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    assert _verdict(_pre(governed, **TOUCH)[0]) == "allow"  # contrôle : sans pause, rien n'est refusé

    write_pause(governed, mode=HOLD)
    rendered, decision = _pre(governed, **TOUCH)
    assert _verdict(rendered) == "deny"
    assert "pause : dépose ton point de reprise" in _reason(rendered)
    assert f"grimoire task wait {tid} --reason paused" in _reason(rendered)
    assert decision.detail["pause_brake"] == "hold"

    # Les lectures restent permises.
    assert _verdict(_pre(governed, "Read", file_path="README.md")[0]) == "allow"
    assert _verdict(_pre(governed, command="grimoire orchestrate status")[0]) == "allow"
    # Un point de reprise pour une autre tâche, ou enchaîné à autre chose, n'est pas le point de reprise.
    for command in (
        "grimoire task wait T-autre --reason paused",
        f"grimoire task wait {tid} --reason ci",
        f"grimoire task wait {tid} --reason paused && touch x",
    ):
        assert _verdict(_pre(governed, command=command)[0]) == "deny", command

    # La seule écriture permise : le point de reprise de CETTE tâche.
    checkpoint = f"grimoire task wait {tid} --reason paused"
    assert _verdict(_pre(governed, command=checkpoint)[0]) == "allow"
    TaskService(governed).wait(tid, "paused", "claude")  # ce que la commande ferait
    task = TaskService(governed).require(tid)
    assert task.status is S.WAITING and task.waiting is not None and task.waiting.reason == "paused"
    assert task.waiting.resume_hint is not None and task.waiting.resume_hint.session_id == SESSION

    # Une seule : le point de reprise est déposé, tout outil mutant reste refusé.
    again, second = _pre(governed, command=checkpoint)
    assert _verdict(again) == "deny" and second.detail["pause_brake"] == "checkpoint_used"
    assert _verdict(_pre(governed, **TOUCH)[0]) == "deny"
    assert _verdict(_pre(governed, "Read", file_path="README.md")[0]) == "allow"
    # Le Stop est permis : la session garée n'est plus jugée sur sa tâche.
    assert _stop(governed).outcome is Outcome.ALLOW


def test_the_mcp_checkpoint_is_the_same_single_write(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    write_pause(governed, mode=HOLD)
    mcp = {"action": "wait", "to": "paused", "task_id": tid}
    assert _verdict(_pre(governed, "mcp__grimoire__task_update", **mcp)[0]) == "allow"
    assert _verdict(_pre(governed, "mcp__grimoire__task_update", **{**mcp, "to": "ci"})[0]) == "deny"
    assert _verdict(_pre(governed, "mcp__grimoire__task_update", **{**mcp, "task_id": "T-autre"})[0]) == "deny"
    assert _verdict(_pre(governed, "mcp__grimoire__task_update", action="close", task_id=tid)[0]) == "deny"
    assert _verdict(_pre(governed, "mcp__grimoire__task_claim", task_id="T-neuve")[0]) == "deny"
    assert _verdict(_pre(governed, "mcp__grimoire__task_show", task_id=tid)[0]) == "allow"  # une lecture reste permise


def test_drain_and_an_unreadable_flag_refuse_no_tool(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    write_pause(governed, mode=DRAIN)
    assert _verdict(_pre(governed, **TOUCH)[0]) == "allow"  # drain : les agents en cours finissent
    (governed / PAUSE_RELPATH).write_text("{", encoding="utf-8")
    assert _verdict(_pre(governed, **TOUCH)[0]) == "allow"  # illisible = drain, pas hold
    context = _prompt(governed).context
    assert "pause illisible" in context and "ne réclame aucune nouvelle tâche" in context
    assert read_pause(governed).mode_for(tid) == DRAIN


def test_a_task_scoped_hold_brakes_only_that_task(governed: Path) -> None:
    mine = _claimed(governed)
    _prompt(governed)
    write_pause(governed, mode=HOLD, tasks=["T-d-une-autre-tache"])
    assert _verdict(_pre(governed, **TOUCH)[0]) == "allow"
    write_pause(governed, mode=HOLD, tasks=[mine])
    assert _verdict(_pre(governed, **TOUCH)[0]) == "deny"


def test_a_session_holding_no_task_is_not_braked(governed: Path) -> None:
    _claimed(governed)  # claim non rattaché : cette session n'a aucun bail noté
    write_pause(governed, mode=HOLD)
    assert _verdict(_pre(governed, **TOUCH)[0]) == "allow"


def test_hold_degrades_to_drain_on_unfenced_host(governed: Path) -> None:
    tid = _claimed(governed)
    # Même hôte, version non prouvée : le refus d'outil n'est pas garanti, le frein se tait.
    _prompt(governed, version="9.9.9")
    write_pause(governed, mode=HOLD)
    rendered, decision = _pre(governed, _version="9.9.9", **TOUCH)
    assert _verdict(rendered) == "allow" and not decision.is_refusal
    context = _prompt(governed, version="9.9.9").context
    assert "gare-toi" in context and "hold dégradé en drain (hôte sans frein)" in context
    assert f"grimoire task wait {tid} --reason paused" in context
    # La même pause sur l'hôte prouvé (Claude Code 2.1.301) : refus et consigne de dépôt.
    fenced = _prompt(governed).context
    assert "le prochain outil mutant sera refusé" in fenced and "dégradé" not in fenced
    assert _verdict(_pre(governed, **TOUCH)[0]) == "deny"
    # Un hôte jamais prouvé (Copilot) : drain, quoi qu'on lui demande.
    assert _verdict(_pre(governed, host=HostId.GITHUB_COPILOT, **TOUCH)[0]) == "allow"

    # L'état le dit aussi à l'opérateur : la carte « hold dégradé en drain ».
    ledger = TaskService(governed).ledger
    assert ledger.get_task(tid) is not None
    TaskService(governed).attach_session(tid, SESSION, session_host="copilot", actor="claude", host_version="1.0")
    status = runner.invoke(orchestrate_app, ["status", "--project-root", str(governed)])
    assert f"{tid} : drain — hold dégradé en drain (hôte sans frein)" in status.output


def test_session_start_announces_the_pause(governed: Path) -> None:
    _claimed(governed)
    write_pause(governed, mode=DRAIN)
    payload = {"session_id": SESSION, "hook_event_name": "SessionStart", "cwd": str(governed), "source": "startup"}
    decision = _hook(governed, HookEvent.SESSION_START, payload)[1]
    assert "Pause drain" in decision.context and "drain projet" in decision.context


def test_open_session_sees_resume_and_reclaims(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed)  # la session se rattache à sa tâche
    write_pause(governed, mode=HOLD)
    TaskService(governed).wait(tid, "paused", "claude")  # son point de reprise
    held = run_tick(governed)
    assert held.status == "paused" and TaskService(governed).require(tid).status is S.WAITING
    assert "Pause levée" not in _prompt(governed).context

    clear_pause(governed)
    done = run_tick(governed)
    assert [(w.task_id, w.action) for w in done.woken] == [(tid, "wake")]
    assert TaskService(governed).require(tid).status is S.READY

    # La session est restée ouverte : à son tour suivant, elle apprend que la pause est levée.
    context = _prompt(governed).context
    assert "Pause levée" in context and tid in context and f"grimoire task claim {tid}" in context
    # Et la même session re-claime, à l'epoch suivant.
    service = TaskService(governed)
    service.claim(tid, "claude", host="local")
    task = service.require(tid)
    assert task.status is S.CLAIMED and task.claim is not None and task.claim.lease_epoch > 1  # barrière du réveil, puis claim
    assert "Pause levée" not in _prompt(governed).context  # annoncée tant que la tâche est ready, plus après


# ── Flows : PAUSED à la frontière de node ────────────────────────────────────


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1, "id": "trois-nodes", "name": "Trois nodes",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "label": "A",
             "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            {"id": "b", "kind": "extension-node", "ref": "demo/demo-node", "label": "B",
             "pins": [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}]},
            {"id": "c", "kind": "pattern", "ref": "QUA-04", "label": "C",
             "pins": [{"id": "in", "direction": "in", "contract": "c2"}]},
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


@pytest.fixture
def flow(tmp_path: Path) -> tuple[Any, Path, Callable[[], Any]]:
    from grimoire.flows.engine import FlowEngine

    engine = FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")
    return engine, _blueprint(tmp_path), lambda: engine


def _start(engine: Any, bp: Path) -> str:
    import io

    from grimoire.flows.executor import InteractiveNodeExecutor

    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return str(wfi.id)


OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}


def test_paused_emitted_at_node_boundary(flow: tuple[Any, Path, Any], machine: Path) -> None:
    from grimoire.runtime.schemas import RunEventType, WorkflowStatus

    engine, bp, _ = flow
    run_id = _start(engine, bp)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")

    outcome = engine.resume(run_id, output=OUT_A)
    # Le node `a` est accepté ; le suivant n'est ni présenté, ni exécuté : le run est posé à la frontière.
    assert outcome.ok and outcome.paused and not outcome.finished
    assert outcome.node_id == "b" and outcome.contract is None
    assert any("pause hold" in w for w in outcome.warnings)
    view = engine.status(run_id)
    assert view.status == WorkflowStatus.PAUSED.value
    assert (view.current_node, view.completed_nodes) == ("b", ("a",))
    events = [e.event_type for e in engine._kernel.get_run_events(run_id)]
    assert events.count(RunEventType.WORKFLOW_PAUSED) == 1 and RunEventType.STEP_STARTED in events
    assert [e.payload.get("step_id") for e in engine._kernel.get_run_events(run_id) if e.event_type is RunEventType.STEP_STARTED] == ["a"]
    assert engine.directive(run_id).kind == "blocked" and engine.directive(run_id).reason == "paused"
    assert engine.doctor(run_id).ok, engine.doctor(run_id).violations

    # Pendant la pause, rien ne s'écrit : la sortie du node suivant est refusée sans effet.
    from grimoire.core.exceptions import GrimoireRuntimeError

    with pytest.raises(GrimoireRuntimeError, match="pause hold en vigueur"):
        engine.resume(run_id, output=OUT_B)
    assert engine.status(run_id).status == WorkflowStatus.PAUSED.value

    # Levée : le run repart du node suivant, ouvert une fois, sans rejouer `a`.
    (machine / "pause.json").unlink()
    assert engine.directive(run_id).kind == "execute"
    resumed = engine.resume(run_id, output=OUT_B)
    assert resumed.ok and not resumed.paused and resumed.node_id == "c"
    started = [e.payload.get("step_id") for e in engine._kernel.get_run_events(run_id) if e.event_type is RunEventType.STEP_STARTED]
    assert started == ["a", "b"]
    assert engine.doctor(run_id).ok, engine.doctor(run_id).violations
    last = engine.resume(run_id, output={"pins": {}})
    assert last.finished and engine.status(run_id).status == WorkflowStatus.COMPLETED.value


def test_drain_and_task_scoped_pauses_do_not_pause_a_run_in_flight(flow: tuple[Any, Path, Any], machine: Path) -> None:
    engine, bp, _ = flow
    run_id = _start(engine, bp)
    (machine / "pause.json").write_text('{"mode": "drain"}', encoding="utf-8")
    assert not engine.resume(run_id, output=OUT_A).paused  # drain : les agents en cours finissent
    (machine / "pause.json").write_text('{"mode": "hold", "tasks": ["T-sans-rapport"]}', encoding="utf-8")
    assert not engine.resume(run_id, output=OUT_B).paused


def test_a_paused_run_can_be_aborted(flow: tuple[Any, Path, Any], machine: Path) -> None:
    from grimoire.runtime.schemas import WorkflowStatus

    engine, bp, _ = flow
    run_id = _start(engine, bp)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")
    assert engine.resume(run_id, output=OUT_A).paused
    assert engine.abort(run_id, reason="abandon").status is WorkflowStatus.ABORTED


def test_the_dispatch_driver_stops_on_a_paused_run(flow: tuple[Any, Path, Any], machine: Path) -> None:
    """``_drive`` enchaîne les nodes tant que la cascade est verte : un run posé à la frontière l'arrête sans assertion."""
    from grimoire.flows import dispatch_executor as de
    from grimoire.flows.schemas import NodeExecutionResult, ResumeOutcome

    paused = ResumeOutcome(ok=True, finished=False, node_id="b", paused=True, warnings=("pause hold en vigueur",))
    engine_stub = mock.Mock()
    engine_stub.resume.return_value = paused
    executor = mock.Mock()
    contract = mock.Mock(node_id="a")
    with mock.patch.object(de, "_outcome", side_effect=lambda *a, **k: (a, k)):
        _args, kwargs = de._drive(engine_stub, "WFI-1", executor, contract, NodeExecutionResult(pending=False, output={"pins": {}}))
    assert kwargs["status"] == "paused" and kwargs["node_id"] == "b" and kwargs["faults"] == ("pause hold en vigueur",)


def test_the_kernel_table_allows_pausing_after_a_checkpoint() -> None:
    from grimoire.runtime.kernel import _WF_TRANSITIONS
    from grimoire.runtime.schemas import WorkflowStatus as W

    assert W.PAUSED in _WF_TRANSITIONS[W.CHECKPOINTED] and W.PAUSED in _WF_TRANSITIONS[W.RUNNING]
    assert _WF_TRANSITIONS[W.PAUSED] == frozenset({W.RUNNING, W.ABORTED, W.REFUSED})


# ── Flows : le challenge d'un checkpoint et le contrat d'un run en pause (défauts du sceptique K11) ───────────────


def _gated_blueprint(root: Path, *, destructive: bool) -> Path:
    gate: dict[str, Any] = {
        "id": "gate", "kind": "checkpoint", "ref": "",
        "pins": [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}],
    }
    if destructive:
        gate["config"] = {"checkpoint": {"destructive": True}}
    path = root / f"gated-{int(destructive)}.blueprint.json"
    path.write_text(json.dumps({
        "blueprintVersion": 1, "id": "gated",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            gate,
            {"id": "after", "kind": "pattern", "ref": "QUA-04", "pins": [{"id": "in", "direction": "in", "contract": "c2"}]},
        ],
        "edges": [
            {"from": "a.out", "to": "gate.in", "contract": "c1"},
            {"from": "gate.out", "to": "after.in", "contract": "c2"},
        ],
    }), encoding="utf-8")
    return path


def _human_turn(root: Path) -> None:
    import time

    from grimoire.hosts.decisions import HookInput, run_decision

    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=root, prompt="ok", session_id="S1", host="host-claude-code-cli"),
    )
    time.sleep(0.02)


def _gated_engine(root: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from grimoire.flows.engine import FlowEngine

    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    home = root / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


@pytest.mark.parametrize("destructive", [True, False])
def test_after_a_lifted_hold_the_checkpoint_can_be_attested(
    tmp_path: Path, machine: Path, monkeypatch: pytest.MonkeyPatch, destructive: bool
) -> None:
    """K11-S1-01 : la frontière posait le run sans présenter le checkpoint suivant, donc sans challenge ;
    la session qui reprend lisait la directive, l'humain répondait, et la décision finissait ``no_challenge``."""
    import io

    from grimoire.flows.executor import InteractiveNodeExecutor

    engine = _gated_engine(tmp_path, monkeypatch)
    quiet = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(_gated_blueprint(tmp_path, destructive=destructive), executor=quiet)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")
    paused = engine.resume(wfi.id, output=OUT_A, executor=quiet)
    assert paused.paused and paused.node_id == "gate"
    (machine / "pause.json").unlink()

    # La session qui reprend lit la directive (le checkpoint), l'humain répond à son tour suivant, elle décide.
    assert engine.directive(wfi.id).kind == "checkpoint"
    _human_turn(tmp_path)
    outcome = engine.resume(wfi.id, output={**OUT_B, "checkpoint_decision": "approve"}, executor=quiet)
    assert outcome.ok, (outcome.faults, outcome.attestation)
    assert outcome.attestation is not None and outcome.attestation["status"] == "attested"
    assert not outcome.warnings


def test_a_session_skipping_the_directive_gets_a_precise_refusal_not_no_challenge(
    tmp_path: Path, machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans passer par la directive, le premier ``resume`` après la pause présente le checkpoint avant de le juger."""
    import io

    from grimoire.flows.executor import InteractiveNodeExecutor

    engine = _gated_engine(tmp_path, monkeypatch)
    quiet = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(_gated_blueprint(tmp_path, destructive=True), executor=quiet)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")
    assert engine.resume(wfi.id, output=OUT_A, executor=quiet).paused
    (machine / "pause.json").unlink()
    _human_turn(tmp_path)
    refused = engine.resume(wfi.id, output={**OUT_B, "checkpoint_decision": "approve"}, executor=quiet)
    assert not refused.ok and refused.attestation is not None
    assert refused.attestation["reason"] != "no_challenge"  # un challenge existe, c'est le tour humain qui manque
    _human_turn(tmp_path)
    assert engine.resume(wfi.id, output={**OUT_B, "checkpoint_decision": "approve"}, executor=quiet).ok


def test_a_run_paused_under_hold_serves_no_contract(
    tmp_path: Path, machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K11-S1-02 : ``status`` servait le contrat du node en pause, donc n'importe quel appelant pouvait l'exécuter."""
    import io

    from grimoire.flows.executor import InteractiveNodeExecutor

    engine = _gated_engine(tmp_path, monkeypatch)
    quiet = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(_gated_blueprint(tmp_path, destructive=False), executor=quiet)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")
    assert engine.resume(wfi.id, output=OUT_A, executor=quiet).paused
    held = engine.status(wfi.id, include_contract=True)
    assert held.status == "paused" and held.contract is None
    assert held.directive is not None and held.directive.reason == "paused"
    (machine / "pause.json").unlink()
    assert engine.status(wfi.id, include_contract=True).contract is not None  # levée : le contrat est servi


def test_dispatch_resume_under_hold_launches_no_agent(
    tmp_path: Path, machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K11-S1-02 : ``flow resume --executor dispatch`` lançait l'agent du node suivant puis se faisait refuser."""
    import io

    from grimoire.core.exceptions import GrimoireRuntimeError
    from grimoire.flows import dispatch_executor as de
    from grimoire.flows.executor import InteractiveNodeExecutor
    from grimoire.flows.schemas import NodeExecutionResult

    engine = _gated_engine(tmp_path, monkeypatch)
    quiet = InteractiveNodeExecutor(stream=io.StringIO())
    wfi, _ = engine.run(_gated_blueprint(tmp_path, destructive=False), executor=quiet)
    (machine / "pause.json").write_text('{"mode": "hold"}', encoding="utf-8")
    assert engine.resume(wfi.id, output=OUT_A, executor=quiet).paused

    spy = mock.Mock()
    spy.return_value.execute.return_value = NodeExecutionResult(pending=False, output={"pins": {}})
    spy.return_value.last_result = None
    monkeypatch.setattr(de, "DispatchExecutor", spy)
    with pytest.raises(GrimoireRuntimeError, match="pause"):
        de.resume_with_dispatch(engine, wfi.id, project_root=tmp_path)
    assert spy.return_value.execute.call_count == 0
    assert engine.status(wfi.id).status == "paused"  # et rien n'a été écrit
