"""Le suivi des sessions est tournant, sincère et sans bruit : jamais « complet » à tort.

Au-delà du budget de lecture d'un pas, les sessions qui ont changé sans pouvoir être relues rendent la consommation du
jour ``partial`` ; un journal immobile ne coûte aucun budget ; un état inconnu ne s'écrit qu'une fois par changement
réel ; la part consommée avant minuit UTC se compte pour la veille.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.missions.admission import QUOTA_CONSUMED_EVENT, Admit, Defer, admit, derive_view, load_capacity
from grimoire.missions.quota_usage import (
    NO_ID_REASON,
    OVERSIZE_REASON,
    PENDING_REASON,
    STATE_RELPATH,
    record_consumption,
    tracking,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    from grimoire.missions import usage_fold
    from grimoire.tools import workspace_work as ww

    ww._TRANSCRIPT_CACHE.clear()
    usage_fold._missing_cache.clear()


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


def _sid(n: int) -> str:
    return f"5a1c{n:04d}-0000-0000-0000-{n:012d}"


def _task(service: TaskService, titre: str = "t") -> str:
    mission = next(iter(service.ledger.list_missions()), None)
    mission_id = mission.id if mission else service.ledger.create_mission(title="Suivi", origin="test").id
    task = service.ledger.create_task(mission_id, titre, acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _line(n: int, stamp: datetime | None = None, pad: int = 0) -> str:
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    message = {"id": f"msg-{n}", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "x" * pad}], "usage": usage}
    return json.dumps({"type": "assistant", "timestamp": (stamp or datetime.now(UTC)).isoformat(), "message": message})


def _journal(tmp_path: Path, session: str, calls: int, *, pad: int = 0) -> Path:
    path = tmp_path / "claude" / "projects" / "wt" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_line(i, pad=pad) for i in range(calls)) + "\n", encoding="utf-8")
    return path


def _launch(service: TaskService, tid: str, session: str) -> None:
    service.ledger.append_event("task.session_launched", tid, "task", "worker", {"session": session[:8], "session_id": session, "model": "sonnet"})


def _events(service: TaskService) -> list[dict[str, object]]:
    return [dict(e.payload) for e in service.ledger.list_events() if e.event_type == QUOTA_CONSUMED_EVENT]


def _use(service: TaskService):  # type: ignore[no-untyped-def]
    capacity = load_capacity(service.home_root)
    return derive_view(service.ledger, capacity, now=datetime.now(UTC)).tokens


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _decision(service: TaskService, tid: str, quota: str):  # type: ignore[no-untyped-def]
    (service.home_root / STANDARD / "capacity.yaml").write_text(f"version: 1\nquota:\n{quota}", encoding="utf-8")
    capacity = load_capacity(service.home_root)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    return admit(service.require(tid), None, capacity, view, autonomous=True)


def test_sessions_beyond_the_read_budget_are_followed_not_starved(service: TaskService, tmp_path: Path) -> None:
    """22 sessions, budget de 20 : les deux dernières croissent de 1 500 à 75 000 ; le total devient exact en peu de pas."""
    for n in range(22):
        _launch(service, _task(service, f"t{n}"), _sid(n))
        _journal(tmp_path, _sid(n), 1)
    first = record_consumption(service, now=_later())
    assert sorted({o.status for o in first}) == ["recorded", "skipped"]
    assert _use(service).status == "partial" and _use(service).unknown_sessions == 2  # jamais « complète » entre-temps
    record_consumption(service, now=_later())
    assert _use(service).tokens == 22 * 1500 and _use(service).status == "lower_bound"
    _journal(tmp_path, _sid(20), 50)
    _journal(tmp_path, _sid(21), 50)
    record_consumption(service, now=_later(), max_sessions=1)  # un seul journal relu : l'autre attend
    assert _use(service).status == "partial"
    record_consumption(service, now=_later(), max_sessions=1)
    assert _use(service).status == "lower_bound" and _use(service).tokens == 20 * 1500 + 2 * 75000


def test_the_decision_follows_the_real_consumption_and_the_unknown_setting(service: TaskService, tmp_path: Path) -> None:
    tid = _task(service)
    for n in range(4):
        _launch(service, _task(service, f"t{n}"), _sid(n))
        _journal(tmp_path, _sid(n), 1)
    record_consumption(service, now=_later(), max_sessions=3)  # une session reste en attente : partielle
    assert isinstance(_decision(service, tid, "  tokens_per_day: 50000\n"), Admit) is False
    refused = _decision(service, tid, "  tokens_per_day: 50000\n")
    assert isinstance(refused, Defer) and refused.reason == "unknown"
    assert isinstance(_decision(service, tid, "  tokens_per_day: 50000\n  on_unknown: count_known\n"), Admit)
    record_consumption(service, now=_later(), max_sessions=3)
    for n in range(4):
        _journal(tmp_path, _sid(n), 50)  # 75 000 chacune
    record_consumption(service, now=_later(), max_sessions=20)
    over = _decision(service, tid, "  tokens_per_day: 50000\n")
    assert isinstance(over, Defer) and over.reason == "quota"


def test_an_unchanged_journal_costs_no_budget_and_the_oldest_read_goes_first(service: TaskService, tmp_path: Path) -> None:
    for n in range(3):
        _launch(service, _task(service, f"t{n}"), _sid(n))
        _journal(tmp_path, _sid(n), 1)
    for _ in range(3):
        record_consumption(service, now=_later(), max_sessions=1)
    assert tracking(service.ledger.list_events())["known"] == 3
    quiet = record_consumption(service, now=_later(), max_sessions=1)
    assert {o.status for o in quiet} == {"unchanged"}  # rien à relire : le budget d'un pas ne sert plus
    _journal(tmp_path, _sid(0), 2)
    _journal(tmp_path, _sid(1), 2)
    _journal(tmp_path, _sid(2), 2)
    order = []
    for _ in range(3):
        order += [o.session for o in record_consumption(service, now=_later(), max_sessions=1) if o.status == "recorded"]
    assert sorted(order) == sorted(_sid(n)[:12] for n in range(3))  # chacune à son tour, aucune affamée


def test_a_lost_state_cache_rereads_without_counting_twice(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 2)
    record_consumption(service, now=_later())
    (service.home_root / STATE_RELPATH).unlink()
    again = record_consumption(service, now=_later())
    assert [o.status for o in again] == ["unchanged"] and len(_events(service)) == 1
    (service.home_root / STATE_RELPATH).write_text("pas du json", encoding="utf-8")
    record_consumption(service, now=_later())
    assert _use(service).tokens == 3000


def test_a_counted_journal_that_becomes_too_big_turns_partial_with_its_reason(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 1, pad=100_000)
    record_consumption(service, now=_later(), max_bytes=MIB)
    assert _use(service).status == "lower_bound"
    _journal(tmp_path, _sid(1), 25, pad=100_000)  # ~2,4 Mo : au-dessus du plafond de lecture
    out = record_consumption(service, now=_later(), max_bytes=MIB)
    assert [o.status for o in out] == ["unknown"]
    assert _use(service).status == "partial" and _use(service).tokens == 1500  # le dernier total reste la borne basse
    assert [str(e["reason"]) for e in _events(service) if e["status"] == "unknown"] == [OVERSIZE_REASON]


def test_an_unknown_state_is_written_once_per_real_change(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 15, pad=100_000)
    record_consumption(service, now=_later(), max_bytes=MIB)
    _journal(tmp_path, _sid(1), 25, pad=100_000)  # grossit encore, toujours trop gros
    for _ in range(5):
        record_consumption(service, now=_later(), max_bytes=MIB)
    assert len([e for e in _events(service) if e["status"] == "unknown"]) == 1
    _journal(tmp_path, _sid(1), 2)  # redevient lisible : un état connu remplace l'inconnu
    record_consumption(service, now=_later(), max_bytes=MIB)
    assert _use(service).status == "lower_bound" and _use(service).tokens == 3000


def test_a_launch_without_an_identity_is_unknown_not_zero(service: TaskService) -> None:
    tid = _task(service)
    service.ledger.append_event("task.session_launched", tid, "task", "worker", {"session": "abcd", "model": "sonnet"})
    for _ in range(3):
        record_consumption(service, now=_later())
    events = _events(service)
    assert len(events) == 1 and events[0]["status"] == "unknown" and events[0]["reason"] == NO_ID_REASON
    assert tracking(service.ledger.list_events())["unknown"] == 1 and _use(service).status == "unknown"


def test_the_part_consumed_before_midnight_counts_for_the_previous_day(service: TaskService, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _launch(service, _task(service), _sid(1))
    path = tmp_path / "claude" / "projects" / "wt" / f"{_sid(1)}.jsonl"
    path.parent.mkdir(parents=True)
    lines = [_line(0, midnight - timedelta(hours=2)), _line(1, midnight - timedelta(minutes=5)), _line(2, now)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record_consumption(service, now=now + timedelta(hours=1))
    parts = _events(service)
    assert [(p.get("day"), p["tokens"]) for p in parts] == [((midnight - timedelta(days=1)).date().isoformat(), 3000), (None, 1500)]
    assert parts[-1]["session_total_tokens"] == 4500
    assert _use(service).tokens == 1500  # seule la part d'aujourd'hui compte pour aujourd'hui


def test_an_ongoing_unknown_weighs_on_the_next_day_but_an_old_plain_one_does_not(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 15, pad=100_000)
    record_consumption(service, now=_later(), max_bytes=MIB)  # trop gros : inconnu « en cours »
    capacity = load_capacity(service.home_root)
    tomorrow = datetime.now(UTC) + timedelta(hours=30)
    assert derive_view(service.ledger, capacity, now=tomorrow).tokens.status == "unknown"
    assert derive_view(service.ledger, capacity, now=datetime.now(UTC) + timedelta(days=4)).tokens.status == "lower_bound"
    _launch(service, _task(service), _sid(2))
    record_consumption(service, now=_later() + timedelta(hours=1))  # sans journal : inconnu simple, écrit aujourd'hui
    assert derive_view(service.ledger, capacity, now=tomorrow).tokens.unknown_sessions == 1


def test_pending_reasons_are_stable_strings() -> None:
    assert "Mo" not in OVERSIZE_REASON and not any(c.isdigit() for c in PENDING_REASON + OVERSIZE_REASON + NO_ID_REASON)


def test_queue_rules_shows_the_tracking(projet: Path, service: TaskService, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    _launch(service, _task(service), _sid(1))
    result = CliRunner().invoke(app, ["queue", "rules", "--json", "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["tracking"] == {"sessions": 1, "known": 0, "unknown": 0, "pending_reread": 0, "never_measured": 1}
    text = CliRunner().invoke(app, ["queue", "rules", "--project-root", str(projet)])
    assert "Sessions suivies : 1" in text.output
