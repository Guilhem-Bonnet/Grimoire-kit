"""K12 (épopée #705) — banc de coupure : huit points où une panne peut tomber, chacun avec son résultat attendu.

Chaque scénario reproduit sur disque l'état qu'une coupure laisse à ce point (ligne tronquée, renommage
perdu, exception levée entre deux écritures), puis vérifie que le rejeu reste cohérent et que le tick
suivant termine le geste sans le doubler. Ce sont des coupures *simulées* : aucun processus n'est tué,
aucune alimentation coupée — un ``fsync`` réellement perdu par le noyau reste hors de portée d'un test.

Constat sur la base (``aidlc/k11-global-pause``), scénarios A et H : une ligne finale tronquée sans saut
de ligne collait l'événement suivant à elle (les deux perdus au rejeu, en silence) ; tronquée au milieu
d'un caractère multi-octet, elle rendait tout le journal illisible (``read_text`` strict) ; et le tick ne
disait rien d'une ligne ignorée.

Marqués ``crash_bench`` (``pytest -m crash_bench`` les isole) ; rapides, ils tournent aussi avec la suite.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import ledger as ledger_mod
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import CONTROL_HOME_ENV
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.crash_bench

S = TaskState
FENCED_VERSION = "2.1.301"


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path_factory.mktemp("control-home")))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / "_grimoire/standard").mkdir(parents=True)
    return root


def _running(service: TaskService, title: str, *, version: str = FENCED_VERSION, session: str = "S1") -> str:
    tid = service.add(title, ("critère",), actor="t", owner="amelia", ready=True).task.id
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "agent-a")
    service.attach_session(tid, session, session_host="claude-code-cli", actor="agent-a", host_version=version)
    return tid


def _expire(service: TaskService, tid: str) -> int:
    task = service.require(tid)
    assert task.claim is not None
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    write_lease(lease_path(service.ledger_root, tid), LeaseFile(tid, task.claim.lease_epoch, stale))
    return task.claim.lease_epoch


def _states(root: Path) -> dict[str, TaskState]:
    return {t.id: t.status for t in MissionLedger(root).list_tasks()}


def _events_path(service: TaskService) -> Path:
    return service.ledger_root / "events.jsonl"


# ── A : ajout au ledger, coupé avant fsync (ligne partielle) ──────────────────


@pytest.mark.parametrize("cut", ["moitié", "multi-octet"])
def test_scenario_a_append_cut_before_fsync_replays_the_prefix(projet: Path, cut: str) -> None:
    service = TaskService(projet)
    tid = service.add("Coupée", ("critère",), actor="t", owner="amelia", ready=True).task.id
    path = _events_path(service)
    before = path.read_bytes()
    expected = _states(service.ledger_root)
    service.comment(tid, "note accentuée : été, déjà", "guilhem")
    added = path.read_bytes()[len(before):]
    if cut == "moitié":
        torn = added[: len(added) // 2]
    else:  # coupé au milieu du « é » (deux octets en UTF-8)
        at = added.index("é".encode())
        torn = added[: at + 1]
    path.write_bytes(before + torn)

    ledger = MissionLedger(service.ledger_root)
    assert {t.id: t.status for t in ledger.list_tasks()} == expected, "rejeu = l'état d'avant l'ajout"
    # L'ajout suivant n'est pas collé à la ligne tronquée : il se relit.
    ledger.append_event("task.note", tid, "task", "t", {"task_id": tid, "n": 1})
    replayed = MissionLedger(service.ledger_root)
    assert replayed.list_events(tid)[-1].event_type == "task.note"
    assert replayed.unreadable_lines() == (before.count(b"\n") + 1,), "la ligne tronquée est rendue, pas tue"


# ── B : après fsync du fichier, avant fsync du dossier (renommage perdu) ──────


def test_scenario_b_rename_lost_before_directory_fsync_replays_the_old_file(projet: Path) -> None:
    service = TaskService(projet)
    tid = _running(service, "Renommage perdu")
    path = _events_path(service)
    old = path.read_bytes()
    expected = _states(service.ledger_root)
    service.note_progress(tid, "étape", "agent-a")
    path.write_bytes(old)  # le dossier n'a pas gardé le renommage : l'ancien fichier revient entier
    assert _states(service.ledger_root) == expected
    assert MissionLedger(service.ledger_root).unreadable_lines() == ()
    assert run_tick(projet, apply=True).status == "ran"


# ── C : fichier de bail écrit, coupé avant rename ─────────────────────────────


def test_scenario_c_lease_cut_before_rename_keeps_the_old_lease(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(projet)
    tid = _running(service, "Bail")
    path = lease_path(service.ledger_root, tid)
    old = read_lease(path)
    assert old is not None

    def _cut(self: Path, target: Any) -> Path:
        raise OSError("coupure avant rename")

    monkeypatch.setattr(Path, "replace", _cut)
    with pytest.raises(OSError, match="coupure"):
        write_lease(path, LeaseFile(tid, old.epoch + 1, datetime.now(UTC).isoformat(), holder=False, origin=ledger_mod.ORIGIN_REAP))
    monkeypatch.undo()
    assert read_lease(path) == old, "ancien bail intact"
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")], "pas de temporaire laissé"


# ── D : barrière écrite (rename fait), événement du ledger pas encore ─────────


def test_scenario_d_fence_written_but_no_event_refuses_the_holder_and_the_tick_finishes(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = TaskService(projet)
    tid = _running(service, "Barrière")
    epoch = _expire(service, tid)
    real = MissionLedger._transition_locked

    def _cut(self: MissionLedger, *args: Any, **kw: Any) -> Any:
        raise OSError("coupure entre la barrière et l'événement")

    monkeypatch.setattr(MissionLedger, "_transition_locked", _cut)
    with pytest.raises(OSError):
        run_tick(projet, apply=True)
    monkeypatch.setattr(MissionLedger, "_transition_locked", real)
    fence = read_lease(lease_path(service.ledger_root, tid))
    assert fence is not None and fence.epoch == epoch + 1 and not fence.holder

    ledger = MissionLedger(service.ledger_root)
    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.renew_lease(tid, epoch, actor_id="agent-a")  # titulaire refusé
    report = run_tick(projet, apply=True)
    assert [(d.task_id, d.action) for d in report.reaped] == [(tid, "ready")]
    events = [e for e in MissionLedger(service.ledger_root).list_events(tid) if ledger_mod.LEASE_LOST_KEY in e.payload]
    assert len(events) == 1 and events[0].payload["fence_epoch"] == epoch + 1
    assert run_tick(projet, apply=True).reaped == (), "sans double récolte"


# ── E : entre la différée (lease_lost) et la transition ──────────────────────


def test_scenario_e_deferral_written_but_no_transition_is_completed_once(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(projet)
    tid = _running(service, "Sans frein", version="0.0.0")  # version non capturée : hôte non fencé
    _expire(service, tid)
    real = MissionLedger._transition_locked

    def _cut(self: MissionLedger, *args: Any, **kw: Any) -> Any:
        raise OSError("coupure entre la différée et la transition")

    monkeypatch.setattr(MissionLedger, "_transition_locked", _cut)
    with pytest.raises(OSError):
        run_tick(projet, apply=True)
    monkeypatch.setattr(MissionLedger, "_transition_locked", real)
    assert MissionLedger(service.ledger_root).get_task(tid).status is S.RUNNING  # type: ignore[union-attr]

    report = run_tick(projet, apply=True)
    assert [(d.task_id, d.action, d.cause) for d in report.reaped] == [(tid, "defer", "resume_unfenced")]
    ledger = MissionLedger(service.ledger_root)
    assert ledger.get_task(tid).status is S.WAITING  # type: ignore[union-attr]
    assert len([e for e in ledger.list_events(tid) if e.event_type == ledger_mod.REAP_DEFERRED_EVENT]) == 1
    assert run_tick(projet, apply=True).reaped == ()


# ── F et G : effets externes ─────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


@pytest.fixture
def push_target(tmp_path: Path) -> tuple[TaskService, str, dict[str, str]]:
    repo = tmp_path / "depot"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "task/f")
    for key, value in (("user.name", "Test"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    (repo / ".gitignore").write_text("_grimoire-runtime-output/\n_grimoire-output/\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(repo, "commit", "-q", "-m", "socle")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    service = TaskService(repo)
    tid = _running(service, "Pousse")
    sha = _git(repo, "rev-parse", "HEAD")
    return service, tid, {"repo": str(repo), "remote": "origin", "branch": "task/f", "sha": sha}


def test_scenario_f_cut_between_intent_and_effect_checks_the_remote_then_acts_once(
    push_target: tuple[TaskService, str, dict[str, str]],
) -> None:
    from grimoire.missions import effects

    service, tid, target = push_target
    calls: list[int] = []

    def crash() -> str:
        raise OSError("coupure avant le push")

    def push() -> str:
        calls.append(1)
        _git(Path(target["repo"]), "push", "-q", "origin", target["branch"])
        return target["sha"]

    with pytest.raises(OSError):
        effects.perform(service, tid, effects.GIT_PUSH, target, crash, actor="agent-a")
    [pending] = effects.pending_effects(service)
    outcome = effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-a")
    assert outcome.status == "done" and calls == [1], "vérification distante (absent), puis une exécution"
    assert effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-a").status == "already"
    assert calls == [1] and effects.pending_effects(service) == []
    intents = [e for e in service.ledger.list_events(tid) if e.event_type == effects.EFFECT_INTENDED_EVENT]
    assert len(intents) == 1 and pending.effect_id == outcome.effect.effect_id


def test_scenario_g_cut_between_effect_and_its_trace_is_seen_not_redone(
    push_target: tuple[TaskService, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.missions import effects

    service, tid, target = push_target
    calls: list[int] = []

    def push() -> str:
        calls.append(1)
        _git(Path(target["repo"]), "push", "-q", "origin", target["branch"])
        return target["sha"]

    real = effects._observe

    def _cut(*args: Any, **kw: Any) -> None:
        raise OSError("coupure après le push, avant le constat")

    monkeypatch.setattr(effects, "_observe", _cut)
    with pytest.raises(OSError):
        effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-a")
    monkeypatch.setattr(effects, "_observe", real)
    assert calls == [1]

    report = run_tick(Path(target["repo"]), apply=True)  # le tick constate, sans refaire
    assert [o.status for o in report.effects] == ["reconciled"]
    assert effects.perform(service, tid, effects.GIT_PUSH, target, push, actor="agent-b").status == "already"
    assert calls == [1]


# ── H : ligne finale tronquée ─────────────────────────────────────────────────


def test_scenario_h_truncated_final_line_is_ignored_reported_and_replay_is_coherent(projet: Path) -> None:
    service = TaskService(projet)
    tid = _running(service, "Tronquée")
    _expire(service, tid)
    path = _events_path(service)
    expected = _states(service.ledger_root)
    path.write_bytes(path.read_bytes() + '{"id": "evt-x", "event_type": "task.transitioned", "payload": {"task_id": "é'.encode()[:-1])

    report = run_tick(projet, apply=True)

    assert any("ligne(s) illisible(s)" in line for line in report.attention), report.attention
    assert [(d.task_id, d.action) for d in report.reaped] == [(tid, "ready")], "le rejeu cohérent permet la récolte"
    ledger = MissionLedger(service.ledger_root)
    assert ledger.get_task(tid).status is S.READY  # type: ignore[union-attr]
    assert set(_states(service.ledger_root)) == set(expected)
    assert len(ledger.unreadable_lines()) == 1


def test_a_raw_line_separator_in_a_payload_stays_in_its_line(projet: Path) -> None:
    """``ensure_ascii=False`` écrit ``U+2028`` brut ; ``str.splitlines`` coupait la ligne et perdait l'événement."""
    service = TaskService(projet)
    tid = service.add("Séparateur", ("critère",), actor="t", owner="amelia", ready=True).task.id
    service.comment(tid, "avant\u2028après", "guilhem")
    ledger = MissionLedger(service.ledger_root)
    assert [d.text for d in ledger.get_task(tid).directives] == ["avant\u2028après"]  # type: ignore[union-attr]
    assert ledger.unreadable_lines() == ()
