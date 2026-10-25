"""Vue « Sessions » de Travail (P33) — vivacité réelle, une seule définition de « vivante ».

Trois choses verrouillées sans navigateur :

- ``liveness`` de chaque session nourrie par des fixtures de ``claude agents --json``
  (une par état : occupée, au repos, en attente, terminée, arrêtée) passées par un
  exécuteur factice — et le repli sur les journaux de session quand l'hôte ne
  répond pas (binaire absent, délai, sortie illisible) ;
- une seule fenêtre de vie partagée par la Flotte (``project_health``) et Travail
  (portefeuille, vue Sessions, ``task list --live``) : avant P33, 15 min contre 30 ;
- ce que la vue lit : projet, hôte, tâche tenue, dernier signe, consignes en
  attente, question en attente, activité des quinze dernières minutes, commande
  de reprise — et une lecture ratée nommée, jamais un zéro.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.missions import liveness as lv
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH, TaskService
from grimoire.tools import project_health as ph
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_portfolio as wp
from grimoire.tools import workspace_sessions as ws

NOW = datetime.now(UTC).replace(microsecond=0)

# ── Fixtures de `claude agents --json` (forme relevée sur la sortie réelle du 2026-10-25) ──

BUSY = {
    "pid": 11,
    "cwd": "",
    "kind": "interactive",
    "startedAt": 1792955730592,
    "sessionId": "s-busy",
    "name": "a",
    "status": "busy",
}
IDLE = {
    "pid": 12,
    "cwd": "",
    "kind": "interactive",
    "startedAt": 1792955730592,
    "sessionId": "s-idle",
    "name": "b",
    "status": "idle",
}
WAITING = {
    "id": "w",
    "cwd": "",
    "kind": "background",
    "startedAt": 1792955730592,
    "sessionId": "s-wait",
    "name": "c",
    "state": "waiting",
}
DONE = {
    "id": "d",
    "cwd": "",
    "kind": "background",
    "startedAt": 1792955730592,
    "sessionId": "s-done",
    "name": "d",
    "state": "done",
}
STOPPED = {
    "id": "x",
    "cwd": "",
    "kind": "background",
    "startedAt": 1792955730592,
    "sessionId": "s-stop",
    "name": "e",
    "state": "stopped",
}
FIXTURES = {"s-busy": BUSY, "s-idle": IDLE, "s-wait": WAITING, "s-done": DONE, "s-stop": STOPPED}


def _runner(stdout: str, code: int = 0) -> Callable[..., Any]:
    """L'exécuteur factice : ce que `subprocess.run` aurait rendu, sans lancer `claude`."""

    def run(argv: list[str], **_kwargs: Any) -> Any:
        assert argv[:3] == ["claude", "agents", "--json"]
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    return run


def _raising(exc: BaseException) -> Callable[..., Any]:
    def run(*_a: Any, **_k: Any) -> Any:
        raise exc

    return run


def _probe_from(run: Callable[..., Any]) -> Callable[[], Any]:
    return lambda: lv.probe_entries(run=run)


# ── Projet gouverné et sessions ──────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    reg.save_registry([{"slug": "proj", "name": "Proj", "path": str(root.resolve())}])
    return root


def _journal(root: Path, sid: str, *, age: timedelta | None, task_id: str = "", ended: bool = False) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"session_id": sid, "task_id": task_id}
    if age is not None:
        at = (NOW - age).isoformat()
        data.update(started_at=(NOW - age - timedelta(minutes=5)).isoformat(), updated_at=at)
        if ended:
            data["ended_at"] = at
    (runs / f"session-{sid}.json").write_text(json.dumps(data), encoding="utf-8")


def _held(root: Path, sid: str, title: str = "Tâche tenue") -> str:
    ledger = TaskService(root, DEFAULT_LEDGER_RELPATH).ledger
    mission = ledger.create_mission(title="M", origin="t", created_by="t")
    task = ledger.create_task(mission.id, title, acceptance=("critère",), owner="winston")
    ledger.transition_task(task.id, TaskState.READY, actor_id="t")
    ledger.claim_task(task.id, "claude", "host-claude-code-cli")
    ledger.attach_session(task.id, sid, session_host="host-claude-code-cli")
    return task.id


def _by_id(view: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in view["sessions"]}


# ── 1. La sonde : une fixture par état, exécuteur factice ────────────────────


class TestProbeFixtures:
    def test_each_state_of_claude_agents_maps_to_its_probe_status(self) -> None:
        stdout = json.dumps(
            [BUSY, IDLE, WAITING, DONE, STOPPED, {"sessionId": "s-odd", "state": "teleported"}, {"x": 1}]
        )
        got = lv.probe_entries(run=_runner(stdout))
        assert got is not None
        assert {sid: e["status"] for sid, e in got.items()} == {
            "s-busy": "busy",
            "s-idle": "idle",
            "s-wait": "waiting",
            "s-done": "ended",
            "s-stop": "ended",
        }, "un état inconnu ne prouve rien : écarté"
        assert got["s-busy"]["started_at"].startswith("2026-10-25"), "startedAt (ms) devient une date ISO"
        assert lv.probe_claude_agents(run=_runner(stdout)) == {sid: e["status"] for sid, e in got.items()}

    def test_waiting_for_marks_a_session_waiting_whatever_its_status(self) -> None:
        entry = {**IDLE, "waitingFor": "permission"}
        got = lv.probe_entries(run=_runner(json.dumps([entry])))
        assert got == {"s-idle": {**got["s-idle"], "status": "waiting"}}

    @pytest.mark.parametrize(
        "run",
        [
            _raising(FileNotFoundError("claude")),
            _raising(subprocess.TimeoutExpired(["claude"], 3)),
            _runner("pas du json"),
            _runner("{}"),
            _runner("[]", code=1),
        ],
        ids=["binaire-absent", "delai", "illisible", "pas-une-liste", "code-non-nul"],
    )
    def test_a_mute_host_is_none_not_an_empty_fleet(self, run: Callable[..., Any]) -> None:
        assert lv.probe_entries(run=run) is None

    def test_liveness_reads_the_two_new_probe_statuses(self) -> None:
        ended = lv.liveness(now=NOW, journal_at=NOW, probe_status="ended")
        assert ended.state == lv.TERMINE and "arrêtée" in ended.reason
        waiting = lv.liveness(now=NOW, probe_status="waiting")
        assert waiting.state == lv.VIVANT and "attend" in waiting.reason


# ── 2. liveness(carte) dans la vue, par état, puis le repli sur les journaux ──


class TestLivenessPerState:
    def test_one_session_per_probe_state(self, project: Path) -> None:
        for sid in FIXTURES:
            _journal(project, sid, age=timedelta(hours=3))  # journal vieux : seule la sonde peut trancher
        held = _held(project, "s-idle")
        view = ws.sessions_view(project, probe=_probe_from(_runner(json.dumps(list(FIXTURES.values())))), now=NOW)
        rows = _by_id(view)
        assert rows["s-busy"]["state"] == ws.ACTIVE
        assert rows["s-busy"]["liveness"]["state"] == lv.VIVANT
        assert rows["s-idle"]["state"] == ws.SILENT and rows["s-idle"]["liveness"]["state"] == lv.INACTIF
        assert rows["s-wait"]["state"] == ws.WAITING
        assert rows["s-done"]["state"] == ws.ENDED and rows["s-stop"]["state"] == ws.ENDED
        assert view["probe"] == "ok"
        assert view["summary"] == {
            "active": 1,
            "waiting": 1,
            "silent": 1,
            "silent_with_claims": 1,
            "ended_24h": 2,
        }
        assert rows["s-idle"]["tasks"][0]["id"] == held

    @pytest.mark.parametrize(
        "run",
        [_raising(FileNotFoundError("claude")), _raising(subprocess.TimeoutExpired(["claude"], 3)), _runner("?")],
        ids=["binaire-absent", "delai", "illisible"],
    )
    def test_falls_back_to_session_journals_when_the_host_does_not_answer(
        self, project: Path, run: Callable[..., Any]
    ) -> None:
        _journal(project, "s-fresh", age=timedelta(minutes=2))
        _journal(project, "s-old", age=timedelta(hours=2))
        _journal(project, "s-gone", age=timedelta(hours=1), ended=True)
        _held(project, "s-old")
        view = ws.sessions_view(project, probe=_probe_from(run), now=NOW)
        rows = _by_id(view)
        assert view["probe"] == "muette"
        assert rows["s-fresh"]["state"] == ws.ACTIVE and "journal" in rows["s-fresh"]["liveness"]["reason"]
        assert rows["s-old"]["state"] == ws.SILENT and rows["s-old"]["liveness"]["state"] == lv.INCONNU
        assert rows["s-gone"]["state"] == ws.ENDED

    def test_without_probe_the_view_says_so(self, project: Path) -> None:
        _journal(project, "s1", age=timedelta(minutes=1))
        view = ws.sessions_view(project, now=NOW)
        assert view["probe"] == "absent" and _by_id(view)["s1"]["state"] == ws.ACTIVE

    def test_view_state_projection(self) -> None:
        assert ws.view_state(lv.Liveness(lv.TERMINE), waiting=True) == ws.ENDED
        assert ws.view_state(lv.Liveness(lv.INCONNU), waiting=True) == ws.WAITING
        assert ws.view_state(lv.Liveness(lv.VIVANT), waiting=False) == ws.ACTIVE
        assert ws.view_state(lv.Liveness(lv.INACTIF), waiting=False) == ws.SILENT


# ── 3. Une seule définition de « vivante » (Flotte = Travail) ────────────────


class TestOneDefinitionOfAlive:
    def test_fleet_and_work_share_one_window(self) -> None:
        minutes = lv.DEFAULT_ACTIVE_MINUTES
        assert timedelta(minutes=minutes) == lv.DEFAULT_ACTIVE_WINDOW
        assert minutes == ph.ACTIVE_WINDOW_MINUTES, "Flotte : projet actif"
        assert minutes == wp.DEFAULT_LIVE_MINUTES, "Travail : cartes du portefeuille"
        assert ws.DEFAULT_ACTIVE_WINDOW is lv.DEFAULT_ACTIVE_WINDOW, "vue Sessions"

    def test_cli_default_is_the_shared_window(self) -> None:
        import inspect

        from grimoire.cli import cmd_task

        default = inspect.signature(cmd_task.task_list).parameters["live_minutes"].default
        assert default == lv.DEFAULT_ACTIVE_MINUTES

    @pytest.mark.parametrize("offset,alive", [(-1, True), (1, False)])
    def test_the_same_sign_is_alive_in_both_spaces_or_in_neither(self, project: Path, offset: int, alive: bool) -> None:
        """Un signe juste en deçà (resp. au-delà) de la fenêtre : vivant (resp. non) en Flotte ET en Travail."""
        age = timedelta(minutes=lv.DEFAULT_ACTIVE_MINUTES + offset)
        log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"ts": (NOW - age).isoformat(), "action": "x"}) + "\n", encoding="utf-8")
        _journal(project, "s-edge", age=age)
        fleet = ph.activity(project, now=NOW)["active"]
        work = _by_id(ws.sessions_view(project, now=NOW))["s-edge"]["liveness"]["state"] == lv.VIVANT
        assert fleet is alive and work is alive

    def test_recent_sign_refuses_absent_and_future(self) -> None:
        assert lv.recent_sign(None, NOW) is False
        assert lv.recent_sign(NOW + timedelta(minutes=1), NOW) is False
        assert lv.recent_sign(NOW - timedelta(minutes=1), NOW) is True


# ── 4. Ce que la vue lit ──────────────────────────────────────────────────────


class TestWhatTheViewReads:
    def test_project_host_task_last_sign_directives_and_resume(self, project: Path) -> None:
        sid = "1038a1c8-bdee-49a6-b5e5-52b2329bcabf"
        tid = _held(project, sid, "Suppressions PowerShell")
        service = TaskService(project, DEFAULT_LEDGER_RELPATH)
        service.comment(tid, "reprends par la suppression", "guilhem", "directive", origin=ORIGIN_HUMAN)
        _journal(project, sid, age=timedelta(hours=5))
        session = _by_id(ws.sessions_view(project, now=NOW))[sid]
        assert session["project"] == {"slug": "proj", "name": "Proj", "current": True}
        assert session["host"] == "host-claude-code-cli"
        assert [t["id"] for t in session["tasks"]] == [tid] and session["tasks"][0][
            "title"
        ] == "Suppressions PowerShell"
        assert session["claims"] == 1 and session["state"] == ws.SILENT
        assert session["last_sign"] == {"at": (NOW - timedelta(hours=5)).isoformat(), "source": "journal"}
        assert session["directives_pending"] == 1
        assert session["persona"] == "claude"
        assert session["resume_command"] == f"claude --resume {sid}"

    def test_a_question_on_a_held_task_is_waiting_for_you(self, project: Path) -> None:
        tid = _held(project, "s-ask")
        TaskService(project, DEFAULT_LEDGER_RELPATH).ask(tid, "Fusionner la PR ?", "claude", kind="merge")
        _journal(project, "s-ask", age=timedelta(minutes=1))
        session = _by_id(ws.sessions_view(project, now=NOW))["s-ask"]
        assert session["state"] == ws.WAITING
        assert session["question"]["task"] == tid and session["question"]["text"] == "Fusionner la PR ?"

    def test_a_journal_naming_a_claimed_task_links_it_without_session_id(self, project: Path) -> None:
        ledger = TaskService(project, DEFAULT_LEDGER_RELPATH).ledger
        mission = ledger.create_mission(title="M", origin="t", created_by="t")
        task = ledger.create_task(mission.id, "Sans session", acceptance=("c",), owner="w")
        ledger.transition_task(task.id, TaskState.READY, actor_id="t")
        ledger.claim_task(task.id, "claude", "local")
        _journal(project, "s-journal", age=timedelta(minutes=1), task_id=task.id)
        assert _by_id(ws.sessions_view(project, now=NOW))["s-journal"]["tasks"][0]["id"] == task.id

    def test_old_silent_sessions_without_claim_are_dropped(self, project: Path) -> None:
        _journal(project, "s-yesterday", age=timedelta(hours=30))
        _journal(project, "s-claim", age=timedelta(hours=30))
        _held(project, "s-claim")
        rows = _by_id(ws.sessions_view(project, now=NOW))
        assert "s-yesterday" not in rows, "silencieuse depuis plus de 24 h, sans claim : n'occupe plus personne"
        assert "s-claim" in rows, "un claim tenu reste visible quel que soit son âge"

    def test_unknown_host_has_no_resume_command(self, project: Path) -> None:
        runs = project / "_grimoire-output" / ".runs"
        runs.mkdir(parents=True)
        (runs / "session-s-cx.json").write_text(
            json.dumps({"session_id": "s-cx", "updated_at": NOW.isoformat(), "host": "host-codex"}), encoding="utf-8"
        )
        session = _by_id(ws.sessions_view(project, now=NOW))["s-cx"]
        assert session["host"] == "host-codex" and session["resume_command"] is None


class TestActivityFromTraces:
    @staticmethod
    def _trace(root: Path, lines: list[Any]) -> Path:
        path = root / ws.TRACES_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines), encoding="utf-8")
        return path

    @staticmethod
    def _record(
        sid: str, *ages_s: int, tool: str = "Bash", agent: str = "concierge", model: str = ""
    ) -> dict[str, Any]:
        return {
            "run_id": sid,
            "agent": {"agent_id": agent, "host_id": "host-claude-code-cli", "model": model},
            "tool_calls": [{"tool": tool, "timestamp": (NOW - timedelta(seconds=a)).isoformat()} for a in ages_s],
        }

    def test_tool_calls_fill_seven_slices_of_fifteen_minutes(self, project: Path) -> None:
        self._trace(
            project,
            [
                self._record("s1", 20 * 60),  # hors fenêtre : ne compte pas
                self._record("s1", 14 * 60, 13 * 60, model="opus"),
                "pas du json",
                {"run_id": ""},
                self._record("s1", 30, 10, tool="Edit"),
            ],
        )
        _journal(project, "s1", age=timedelta(minutes=30))
        session = _by_id(ws.sessions_view(project, now=NOW))["s1"]
        assert session["activity"] == {"window_minutes": 15, "buckets": [2, 0, 0, 0, 0, 0, 2], "calls": 4}
        assert session["last_sign"]["source"] == "outil Edit", "le dernier appel tracé est plus récent que le journal"
        assert session["state"] == ws.ACTIVE, "un appel d'outil récent est un signe de vie"
        assert session["persona"] == "concierge" and session["model"] == "opus"

    def test_no_trace_ledger_is_unread_not_zero(self, project: Path) -> None:
        _journal(project, "s1", age=timedelta(minutes=1))
        assert _by_id(ws.sessions_view(project, now=NOW))["s1"]["activity"] is None

    def test_only_the_tail_of_a_big_ledger_is_read(self, project: Path) -> None:
        filler = [self._record("s-old", 60) for _ in range(200)]
        self._trace(project, [*filler, self._record("s-tail", 5)])
        got = ws.recent_tool_calls(project, now=NOW, tail_bytes=600)
        assert got is not None and "s-tail" in got and sum(got["s-tail"].buckets) == 1
        old = got.get("s-old")
        assert old is None or sum(old.buckets) < 200  # la tête n'est pas relue


class TestPortfolio:
    def test_unreadable_project_is_named_and_probe_sessions_go_to_the_deepest_project(
        self, project: Path, tmp_path: Path
    ) -> None:
        nested = project / "inner"
        nested.mkdir()
        reg.save_registry(
            [
                {"slug": "proj", "name": "Proj", "path": str(project.resolve())},
                {"slug": "inner", "name": "Inner", "path": str(nested.resolve())},
                {"slug": "gone", "name": "Gone", "path": str(tmp_path / "nope")},
                {"slug": "empty", "name": "Empty", "path": ""},
            ]
        )
        entries = [{**BUSY, "cwd": str(nested / "src")}, {**IDLE, "cwd": "/ailleurs"}]
        view = ws.sessions_view(project, portfolio=True, probe=_probe_from(_runner(json.dumps(entries))), now=NOW)
        rows = _by_id(view)
        assert rows["s-busy"]["project"]["slug"] == "inner", "le projet le plus profond qui contient le cwd"
        assert rows["s-busy"]["host"] == "host-claude-code-cli" and rows["s-busy"]["resume_command"]
        assert "s-idle" not in rows, "hors de tout projet du registre"
        states = {p["slug"]: (p["state"], p["reason"]) for p in view["projects"]}
        assert states["gone"][0] == "unreadable" and "introuvable" in states["gone"][1]
        assert states["empty"] == ("unreadable", "chemin absent du registre")
        assert states["proj"] == ("ok", None)
        assert next(p for p in view["projects"] if p["slug"] == "inner")["count"] == 1

    def test_a_broken_ledger_names_the_project(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_root: Path) -> Any:
            raise RuntimeError("ledger abîmé")

        monkeypatch.setattr(ws, "sessions_index", boom)
        view = ws.sessions_view(project, portfolio=True, now=NOW)
        assert view["projects"][0]["state"] == "unreadable" and "ledger abîmé" in view["projects"][0]["reason"]
        assert view["sessions"] == []

    def test_a_session_in_two_projects_is_listed_once(self, project: Path, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        reg.save_registry(
            [
                {"slug": "proj", "name": "Proj", "path": str(project.resolve())},
                {"slug": "other", "name": "Other", "path": str(other.resolve())},
            ]
        )
        _journal(project, "s-twice", age=timedelta(hours=2))
        _journal(other, "s-twice", age=timedelta(minutes=3))
        view = ws.sessions_view(project, portfolio=True, now=NOW)
        assert [s["project"]["slug"] for s in view["sessions"] if s["id"] == "s-twice"] == ["other"]


def test_the_route_serves_the_view(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools import workspace_routes as routes

    monkeypatch.setattr(routes, "probe_entries", lambda: {"s-r": {"status": "busy", "cwd": str(project)}})
    monkeypatch.setattr(routes, "_probe_memo", None)
    _journal(project, "s-r", age=timedelta(hours=3))
    payload = routes.GET_ROUTES[f"{routes.PREFIX}sessions"](project, {"scope": ["portfolio"]})
    assert payload["scope"] == "portfolio" and _by_id(payload)["s-r"]["state"] == ws.ACTIVE
    assert routes._host_probe() == {"s-r": "busy"}, "la sonde des cartes lit la même mémoire"
