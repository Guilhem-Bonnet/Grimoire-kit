"""Le suivi des sessions est tournant, sincère et sans bruit : jamais « complet » à tort.

Au-delà du budget de lecture d'un pas, les sessions qui ont changé sans pouvoir être relues rendent la consommation du
jour ``partial`` ; un journal immobile ne coûte aucun budget ; un état inconnu ne s'écrit qu'une fois par changement
réel ; la part consommée avant minuit UTC se compte pour la veille.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.missions.quota_usage import (
    NO_ID_REASON,
    OVERSIZE_REASON,
    PENDING_REASON,
    SETTLED_REASON,
    STATE_RELPATH,
    record_consumption,
    tracking,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import QUOTA_CONSUMED_EVENT, consumption_today

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


class _Use(SimpleNamespace):
    tokens: int
    status: str
    unknown_sessions: int


def _use(service: TaskService, now: datetime | None = None) -> _Use:
    """La consommation du jour comme les règles la lisent : ``partial`` quand une partie des sessions est inconnue."""
    events = service.ledger.list_events()
    today = consumption_today(events, now or datetime.now(UTC))
    tokens = int(today["tokens"])
    status = "lower_bound" if today["tokens_complete"] else ("partial" if tokens else "unknown")
    return _Use(tokens=tokens, status=status, unknown_sessions=tracking(events)["unknown"])


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


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
    tomorrow = datetime.now(UTC) + timedelta(hours=30)
    far = datetime.now(UTC) + timedelta(days=4)
    assert _use(service, tomorrow).status == "unknown" and _use(service, far).status == "unknown"  # sans limite de durée
    _launch(service, _task(service), _sid(2))
    record_consumption(service, now=_later() + timedelta(hours=1))  # sans journal : inconnu simple, écrit aujourd'hui
    assert tracking(service.ledger.list_events())["unknown"] == 2
    # L'inconnu simple ne pèse que le jour où il est écrit : plus loin, seul l'inconnu en cours pèse encore.
    assert _use(service, far).status == "unknown"
    events = service.ledger.list_events()
    assert [e.payload.get("ongoing") for e in events if str(e.payload.get("reason", "")).startswith("journal de session")] == [False]


def test_a_big_journal_that_keeps_growing_stays_partial_and_stops_weighing_only_once_still(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 15, pad=100_000)
    record_consumption(service, now=_later(), max_bytes=MIB)
    later = datetime.now(UTC) + timedelta(hours=49)
    _journal(tmp_path, _sid(1), 40, pad=100_000)  # grossit encore à 49 h
    record_consumption(service, now=later, max_bytes=MIB)
    assert _use(service, later).status == "unknown"
    assert len([e for e in _events(service) if e["status"] == "unknown"]) == 1  # aucun événement de plus
    still = datetime.now(UTC) + timedelta(hours=100)  # immobile depuis la fenêtre
    for _ in range(3):
        record_consumption(service, now=still, max_bytes=MIB)
    settled = [e for e in _events(service) if e["status"] == "unknown"]
    assert [bool(e["ongoing"]) for e in settled] == [True, False] and settled[-1]["reason"] == SETTLED_REASON
    assert _use(service, still + timedelta(days=2)).status == "lower_bound"


def test_a_cache_entry_never_redirects_the_journal(service: TaskService, projet: Path, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    real = _journal(tmp_path, _sid(1), 2)
    record_consumption(service, now=_later())
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_bytes(real.read_bytes())
    cache = projet / STATE_RELPATH
    state = json.loads(cache.read_text(encoding="utf-8"))
    info = decoy.stat()
    state[_sid(1)].update(path=str(decoy), size=info.st_size, mtime_ns=info.st_mtime_ns)
    cache.write_text(json.dumps(state), encoding="utf-8")
    _journal(tmp_path, _sid(1), 200)
    for _ in range(3):
        record_consumption(service, now=_later())
    assert _use(service).tokens == 300_000
    assert "path" not in json.loads(cache.read_text(encoding="utf-8"))[_sid(1)]


def test_a_journal_outside_the_host_folder_is_not_read(service: TaskService, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    outside = tmp_path / "ailleurs.jsonl"
    outside.write_text(_line(0) + "\n", encoding="utf-8")
    link = tmp_path / "claude" / "projects" / "wt" / f"{_sid(1)}.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    record_consumption(service, now=_later())
    assert [e["status"] for e in _events(service)] == ["unknown"]  # introuvable : jamais lu hors du dossier de l'hôte


def test_a_forged_cache_cannot_make_an_unmeasured_session_look_read(service: TaskService, projet: Path, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    path = _journal(tmp_path, _sid(1), 3)
    info = path.stat()
    cache = projet / STATE_RELPATH
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({_sid(1): {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "read_at": "x", "total": 0}}), encoding="utf-8")
    record_consumption(service, now=_later())
    assert _use(service).tokens == 4500


def test_an_unsafe_cache_file_is_ignored_and_never_blocks_the_tick(service: TaskService, projet: Path, tmp_path: Path) -> None:
    import os
    import signal

    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 2)
    cache = projet / STATE_RELPATH
    cache.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(cache)  # un tube : l'ouvrir en lecture bloquerait
    signal.alarm(10)
    try:
        record_consumption(service, now=_later())
    finally:
        signal.alarm(0)
    assert _use(service).tokens == 3000
    cache.unlink()
    victim = tmp_path / "victime.json"
    victim.write_text("{}", encoding="utf-8")
    cache.symlink_to(victim)  # un lien n'est pas suivi
    _journal(tmp_path, _sid(1), 4)
    record_consumption(service, now=_later())
    assert victim.read_text(encoding="utf-8") == "{}" and _use(service).tokens == 6000
    cache.unlink()
    cache.write_text(" " * (4 * MIB + 10), encoding="utf-8")  # trop gros : ignoré
    assert load_state_ignored(projet)


def load_state_ignored(projet: Path) -> bool:
    from grimoire.missions.quota_usage import _load_state

    return _load_state(projet) == {}


def test_the_time_budget_defers_the_rest_and_says_partial(service: TaskService, tmp_path: Path) -> None:
    for n in range(3):
        _launch(service, _task(service, f"t{n}"), _sid(n))
        _journal(tmp_path, _sid(n), 1)
    out = record_consumption(service, now=_later(), time_budget=-1.0)  # la première lecture a toujours lieu
    assert sorted(o.status for o in out) == ["recorded", "skipped", "skipped"]
    assert _use(service).status == "partial" and _use(service).unknown_sessions == 2
    record_consumption(service, now=_later(), time_budget=-1.0)
    record_consumption(service, now=_later(), time_budget=-1.0)
    assert _use(service).status == "lower_bound" and _use(service).tokens == 4500


def test_sessions_out_of_the_tracking_leave_the_cache(service: TaskService, projet: Path, tmp_path: Path) -> None:
    _launch(service, _task(service), _sid(1))
    _journal(tmp_path, _sid(1), 1)
    record_consumption(service, now=_later())
    assert _sid(1) in json.loads((projet / STATE_RELPATH).read_text(encoding="utf-8"))
    record_consumption(service, now=datetime.now(UTC) + timedelta(days=40))
    assert json.loads((projet / STATE_RELPATH).read_text(encoding="utf-8")) == {}


def test_a_journal_never_found_is_no_longer_searched_after_a_while(
    service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import grimoire.missions.quota_usage as quota

    _launch(service, _task(service), _sid(1))
    record_consumption(service, now=datetime.now(UTC) + timedelta(hours=25))
    assert [e["status"] for e in _events(service)] == ["unknown"]

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("journal recherché alors qu'il y a renoncé")

    monkeypatch.setattr(quota, "_locate", boom)
    out = record_consumption(service, now=datetime.now(UTC) + timedelta(hours=26))
    assert [o.status for o in out] == ["unchanged"] and tracking(service.ledger.list_events())["unknown"] == 1


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
