"""La garde commune du plafond de sessions par jour d'un rôle : compte du jour, refus journalisé une fois, attention.

Module autonome : le relecteur et le merger s'y branchent dans la partie réservée (``sessions_per_day``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from grimoire.missions.service import TaskService
from grimoire.missions.session_cap import SESSION_CAP_EVENT, cap_attention, cap_refusal, launched_today


def _service(tmp_path: Path) -> TaskService:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    return TaskService(tmp_path)


def test_the_guard_counts_today_refuses_at_the_limit_and_journals_once(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ledger = service.ledger
    now = datetime.now(UTC)
    assert cap_refusal(service, "reviewer", None, ("x.event",), entity_id="t", actor="a") == ""  # sans plafond : rien
    ledger.append_event("x.event", "t", "task", "a", {})
    assert launched_today(service, ("x.event",), now) == 1
    assert cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a") == ""
    ledger.append_event("x.event", "t", "task", "a", {})
    why = cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a")
    assert "2/2" in why and "minuit UTC" in why
    cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a")
    assert [e.payload for e in ledger.list_events() if e.event_type == SESSION_CAP_EVENT] == [
        {"role": "reviewer", "limit": 2, "count": 2, "day": now.date().isoformat()}
    ]
    ledger.append_event("y.event", "t", "task", "a", {"escalated": True})
    assert launched_today(service, ("y.event",), now, not_launch=lambda e: bool(e.payload.get("escalated"))) == 0
    assert len(cap_attention(service)) == 1 and "reviewer" in cap_attention(service)[0]
    assert cap_attention(service, now + timedelta(days=2)) == ()  # le lendemain, plus rien à dire
