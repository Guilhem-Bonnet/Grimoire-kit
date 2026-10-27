"""« Depuis ta dernière visite » — ``GET /api/workspace/attention/since`` (P53).

Critères de la tâche : à l'ouverture, un résumé liste ce qui a été livré,
refusé ou laissé en attente depuis la dernière visite enregistrée, chaque
ligne liée à sa tâche ; sans visite précédente, aucun résumé. La visite est
gardée par le navigateur : la route lit, elle ne retient rien.

Le terrain piégé de cette brique est le fuseau : un instant local du
navigateur et un instant UTC du journal se comparent en UTC conscient. Les
tests le prouvent avec des instants fixes, jamais en comparant deux appels.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.missions.review_receipt import write_receipt
from grimoire.missions.schemas import LedgerEvent, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_since as ws
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

VISIT = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
ROUTE = f"{PREFIX}attention/since"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _task(tid: str, status: TaskState, *, title: str = "", waiting: Any = None) -> SimpleNamespace:
    return SimpleNamespace(id=tid, title=title or f"Tâche {tid}", status=status, waiting=waiting)


def _move(tid: str, to: str, at: datetime, reason: str = "") -> LedgerEvent:
    return LedgerEvent(
        id=f"evt-{tid}-{to}-{at.timestamp()}", event_type="task.transitioned", entity_id=tid, entity_kind="task",
        actor_id="t", created_at=at.isoformat(), payload={"to_state": to, "reason": reason},
    )


def _refusal(tid: str, at: datetime, *, kind: str = "task.transition:needs_verification->closed",
             recipe: str = "grimoire.task-gate") -> SimpleNamespace:
    verdict = SimpleNamespace(verdict_id="review.receipt_missing", action_kind=kind)
    return SimpleNamespace(recipe_id=recipe, task_id=tid, started_at=at.isoformat(), policy_verdicts=[verdict])


def _ids(lines: list[dict[str, Any]]) -> list[str]:
    return [line["task_id"] for line in lines]


# ── summarize : fonction pure ───────────────────────────────────────────────


class TestSummarize:
    def test_livre_refuse_et_en_attente_depuis_la_visite(self) -> None:
        tasks = [
            _task("A", TaskState.CLOSED),
            _task("B", TaskState.NEEDS_VERIFICATION),
            _task("C", TaskState.BLOCKED),
        ]
        after = VISIT + timedelta(hours=1)
        events = [_move("A", "closed", after), _move("C", "blocked", after, "la CI est rouge")]
        traces = [_refusal("B", after), _refusal("B", after + timedelta(minutes=5))]

        lines = ws.summarize(tasks, events, traces, VISIT)

        assert _ids(lines[ws.DELIVERED]) == ["A"]
        assert _ids(lines[ws.REFUSED]) == ["B"]
        assert lines[ws.REFUSED][0]["count"] == 2
        assert lines[ws.REFUSED][0]["reason"] == "clôture refusée — manque : review.receipt_missing"
        assert lines[ws.REFUSED][0]["at"] == (after + timedelta(minutes=5)).isoformat()
        assert _ids(lines[ws.WAITING]) == ["C"]
        assert lines[ws.WAITING][0]["reason"] == "la CI est rouge"
        assert lines[ws.WAITING][0]["word"] == "bloquée"

    def test_rien_d_avant_la_visite(self) -> None:
        before = VISIT - timedelta(minutes=1)
        tasks = [_task("A", TaskState.CLOSED), _task("B", TaskState.RUNNING), _task("C", TaskState.WAITING)]
        events = [_move("A", "closed", before), _move("C", "waiting", before)]

        lines = ws.summarize(tasks, events, [_refusal("B", before)], VISIT)

        assert lines == {ws.DELIVERED: [], ws.REFUSED: [], ws.WAITING: []}

    def test_une_tache_sortie_de_l_attente_n_y_est_plus(self) -> None:
        after = VISIT + timedelta(minutes=10)
        tasks = [_task("C", TaskState.RUNNING)]

        lines = ws.summarize(tasks, [_move("C", "blocked", after)], [], VISIT)

        assert lines[ws.WAITING] == []

    def test_le_dernier_passage_en_attente_compte(self) -> None:
        """Bloquée avant la visite, débloquée puis rebloquée après : elle est nouvelle."""
        tasks = [_task("C", TaskState.BLOCKED)]
        events = [_move("C", "blocked", VISIT - timedelta(hours=2), "vieux"),
                  _move("C", "blocked", VISIT + timedelta(hours=2), "neuf")]

        lines = ws.summarize(tasks, events, [], VISIT)

        assert [line["reason"] for line in lines[ws.WAITING]] == ["neuf"]

    def test_une_question_posee_dit_sa_question(self) -> None:
        waiting = SimpleNamespace(question=SimpleNamespace(text="CSV ou JSON ?"))
        tasks = [_task("Q", TaskState.WAITING, waiting=waiting), _task("W", TaskState.WAITING)]
        after = VISIT + timedelta(minutes=3)
        events = [_move("Q", "waiting", after), _move("W", "waiting", after, "quota")]

        lines = {line["task_id"]: line for line in ws.summarize(tasks, events, [], VISIT)[ws.WAITING]}

        assert (lines["Q"]["reason"], lines["Q"]["word"]) == ("CSV ou JSON ?", "question posée")
        assert (lines["W"]["reason"], lines["W"]["word"]) == ("quota", "en attente")

    def test_refus_vers_un_autre_etat_et_recettes_etrangeres(self) -> None:
        after = VISIT + timedelta(minutes=1)
        tasks = [_task("B", TaskState.RUNNING), _task("D", TaskState.RUNNING), _task("E", TaskState.RUNNING)]
        traces = [
            _refusal("B", after, kind="task.transition:running->needs_verification"),
            _refusal("D", after, kind=""),
            _refusal("E", after, recipe="grimoire.evidence-gate"),
            _refusal("inconnue", after),
        ]

        lines = {line["task_id"]: line for line in ws.summarize(tasks, [], traces, VISIT)[ws.REFUSED]}

        assert set(lines) == {"B", "D"}
        assert lines["B"]["reason"].startswith("passage « en revue » refusé")
        assert lines["D"]["reason"].startswith("transition refusée")

    def test_le_fuseau_ne_decale_pas_la_visite(self) -> None:
        """La même visite écrite en +02:00 : un événement 30 min après en UTC est après elle."""
        paris = VISIT.astimezone(timezone(timedelta(hours=2)))
        tasks = [_task("A", TaskState.CLOSED)]
        events = [_move("A", "closed", VISIT + timedelta(minutes=30))]

        assert _ids(ws.summarize(tasks, events, [], paris)[ws.DELIVERED]) == ["A"]
        assert ws.summarize(tasks, events, [], paris + timedelta(hours=1))[ws.DELIVERED] == []

    def test_les_plus_recentes_d_abord_et_horodatage_illisible_ignore(self) -> None:
        tasks = [_task("A", TaskState.CLOSED), _task("B", TaskState.CLOSED), _task("C", TaskState.CLOSED)]
        bad = LedgerEvent(id="x", event_type="task.transitioned", entity_id="C", entity_kind="task",
                          actor_id="t", created_at="pas une date", payload={"to_state": "closed"})
        events = [_move("A", "closed", VISIT + timedelta(minutes=1)),
                  _move("B", "closed", VISIT + timedelta(minutes=9)), bad]

        assert _ids(ws.summarize(tasks, events, [], VISIT)[ws.DELIVERED]) == ["B", "A"]


# ── since_view : sur un vrai ledger ─────────────────────────────────────────


def _project(root: Path) -> dict[str, str]:
    """Une tâche livrée, une refusée en revue, une bloquée, une question posée."""
    service = TaskService(root)

    def started(title: str) -> str:
        tid = service.add(title, ("critère",), actor="t", owner="guilhem", ready=True).task.id
        service.claim(tid, "agent-a", host="host-claude-code-cli")
        service.transition(tid, TaskState.RUNNING, "agent-a")
        return tid

    ids = {"delivered": started("Livrer l'export"), "refused": started("Relire le module"),
           "blocked": started("Brancher la CI"), "question": started("Choisir le format")}
    service.transition(ids["delivered"], TaskState.NEEDS_VERIFICATION, "agent-a")
    write_receipt(root, ids["delivered"], "READY", reviewer_id="relecteur")
    service.transition(ids["delivered"], TaskState.CLOSED, "agent-a")
    service.transition(ids["refused"], TaskState.NEEDS_VERIFICATION, "agent-a")
    # Le vrai gate refuse la clôture sans reçu de revue, et journalise son refus.
    with pytest.raises(TaskRefusedError):
        service.transition(ids["refused"], TaskState.CLOSED, "agent-a")
    service.transition(ids["blocked"], TaskState.BLOCKED, "agent-a", reason="la CI est rouge")
    service.ask(ids["question"], "CSV ou JSON ?", "agent-a", kind="choice", options=("CSV", "JSON"))
    return ids


def _by_kind(view: dict[str, Any]) -> dict[str, list[str]]:
    return {section["id"]: [i["task_id"] for i in section["items"]] for section in view["sections"]}


class TestSinceView:
    def test_la_route_est_une_lecture(self) -> None:
        assert ROUTE in GET_ROUTES

    def test_sans_visite_aucun_resume(self, tmp_path: Path) -> None:
        root = tmp_path / "projet"
        root.mkdir()
        _project(root)

        view = workspace_get(root, ROUTE, {})

        assert view["visit"] is False
        assert view["count"] == 0
        assert all(section["items"] == [] for section in view["sections"])

    def test_resume_depuis_la_visite_chaque_ligne_liee_a_sa_tache(self, tmp_path: Path) -> None:
        root = tmp_path / "projet"
        root.mkdir()
        ids = _project(root)

        view = workspace_get(root, ROUTE, {"since": ["2020-01-01T00:00:00Z"]})

        assert view["visit"] is True
        assert view["since"] == "2020-01-01T00:00:00+00:00"
        kinds = _by_kind(view)
        assert kinds[ws.DELIVERED] == [ids["delivered"]]
        assert kinds[ws.REFUSED] == [ids["refused"]]
        assert set(kinds[ws.WAITING]) == {ids["blocked"], ids["question"]}
        assert view["count"] == 4
        line = view["sections"][0]["items"][0]
        assert line["title"] == "Livrer l'export"
        assert line["project"]["slug"]
        assert view["projects"][0]["state"] == "ok"

    def test_une_visite_posterieure_ne_resume_rien(self, tmp_path: Path) -> None:
        root = tmp_path / "projet"
        root.mkdir()
        _project(root)

        view = ws.since_view(root, since="2999-01-01T00:00:00+02:00")

        assert view["visit"] is True
        assert view["count"] == 0

    def test_un_horodatage_illisible_est_un_refus(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="since illisible"):
            ws.since_view(tmp_path, since="hier soir")

    def test_projet_sans_ledger(self, tmp_path: Path) -> None:
        view = ws.since_view(tmp_path, since="2020-01-01T00:00:00Z")

        assert view["projects"][0]["state"] == "no_ledger"
        assert view["count"] == 0

    def test_au_dela_de_la_limite_seul_le_compte(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "projet"
        root.mkdir()
        _project(root)
        monkeypatch.setattr(ws, "LIMIT", 1)

        view = ws.since_view(root, since="2020-01-01T00:00:00Z")
        waiting = next(s for s in view["sections"] if s["id"] == ws.WAITING)

        assert (waiting["count"], waiting["more"], len(waiting["items"])) == (2, 1, 1)

    def test_portefeuille_et_projets_illisibles(self, tmp_path: Path) -> None:
        a, broken = tmp_path / "projet-a", tmp_path / "projet-d"
        a.mkdir()
        ids = _project(a)
        ledger = broken / "_grimoire-runtime-output" / "ledger"
        ledger.mkdir(parents=True)
        (ledger / "events.jsonl").write_text('{"event_type": "task.created", "payload": {}}\n', encoding="utf-8")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
            {"slug": "projet-v", "name": "Vide", "path": ""},
            {"slug": "projet-d", "name": "Delta", "path": str(broken)},
        ])

        view = workspace_get(tmp_path, ROUTE, {"since": ["2020-01-01T00:00:00Z"], "scope": ["portfolio"]})

        assert view["scope"] == "portfolio"
        states = {row["slug"]: row["state"] for row in view["projects"]}
        assert states == {"projet-a": "ok", "projet-c": "unreadable", "projet-v": "unreadable",
                          "projet-d": "unreadable"}
        delivered = view["sections"][0]["items"]
        assert [(i["task_id"], i["project"]["name"]) for i in delivered] == [(ids["delivered"], "Alpha")]

        one = workspace_get(tmp_path, ROUTE, {"since": ["2020-01-01T00:00:00Z"], "scope": ["portfolio"],
                                              "slug": ["projet-a"]})
        assert [row["slug"] for row in one["projects"]] == ["projet-a"]
