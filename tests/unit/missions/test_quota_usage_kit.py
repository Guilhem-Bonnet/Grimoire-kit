"""Les appels de modèle du kit lui-même (Atelier) entrent au quota : inscription idempotente, refus par les règles.

Le point d'entrée est réservé au code du kit : origine d'une liste fermée, jetons déclarés par la réponse, rien d'écrit sans
ledger. Le refus lit ce que l'admission lit et échoue fermé.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.missions.admission import _consumed_today
from grimoire.missions.quota_usage import KIT_ORIGINS, kit_call_refusal, record_kit_call
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import QUOTA_CONSUMED_EVENT, consumption_today

STANDARD = Path("_grimoire/standard")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    svc = TaskService(projet)
    svc.ledger.create_mission(title="Kit", origin="test")
    return svc


def _events(service: TaskService) -> list[dict[str, object]]:
    return [dict(e.payload) for e in service.ledger.list_events() if e.event_type == QUOTA_CONSUMED_EVENT]


def _call(projet: Path, call_id: str = "msg_1", **over: object) -> bool:
    args: dict[str, object] = {
        "origin": "atelier", "call_id": call_id, "model": "claude-haiku-5-5", "input_tokens": 120, "output_tokens": 40,
        "chantier": "un",
    }
    args.update(over)
    return record_kit_call(projet, **args)  # type: ignore[arg-type]


def test_a_known_call_is_recorded_once_and_replaying_it_writes_nothing(projet: Path, service: TaskService) -> None:
    assert KIT_ORIGINS == ("atelier",)
    assert _call(projet) is True and _call(projet) is False
    (event,) = _events(service)
    assert event["session"] == "kit:atelier:msg_1" and event["role"] == "atelier" and event["tokens"] == 160
    assert (event["input_tokens"], event["output_tokens"], event["chantier"]) == (120, 40, "un")
    assert event["status"] == "lower_bound" and event["task_id"] == "" and event["model"] == "claude-haiku-5-5"
    assert _call(projet, "msg_2") is True and len(_events(service)) == 2  # un autre appel est un autre événement


def test_the_consumption_enters_the_day_total_the_rules_and_the_sheet_without_double_count(projet: Path, service: TaskService) -> None:
    _call(projet, "a")
    _call(projet, "b", input_tokens=10, output_tokens=5)
    events = service.ledger.list_events()
    now = datetime.now(UTC)
    assert _consumed_today(events, now).tokens == 175
    today = consumption_today(events, now)
    assert today["tokens"] == 175 and today["tokens_complete"] is True and today["sessions"] == 0  # un appel n'est pas une session


def test_the_cost_is_known_for_a_priced_model_and_never_zero_for_an_unknown_one(projet: Path, service: TaskService) -> None:
    _call(projet, "priced")
    _call(projet, "unpriced", model="un-modele-que-le-kit-ne-tarife-pas")
    priced, unpriced = _events(service)
    assert isinstance(priced["cost_usd"], float) and priced["cost_status"] == "exact"
    assert unpriced["cost_usd"] is None and unpriced["cost_status"] == "unknown" and unpriced["tokens"] == 160


@pytest.mark.parametrize(
    "over",
    [
        {"origin": "agent"}, {"origin": ""}, {"call_id": ""}, {"call_id": "a b"}, {"call_id": "x" * 200}, {"model": ""},
        {"model": "a b"}, {"input_tokens": -1}, {"output_tokens": True}, {"input_tokens": 1.5}, {"input_tokens": 10**9},
        {"output_tokens": "40"}, {"call_id": None},
    ],
)
def test_an_out_of_shape_call_writes_nothing(projet: Path, service: TaskService, over: dict[str, object]) -> None:
    assert _call(projet, **over) is False and _events(service) == []


def test_without_a_ledger_nothing_is_written(tmp_path: Path) -> None:
    root = tmp_path / "sans"
    (root / STANDARD).mkdir(parents=True)
    assert _call(root) is False and not TaskService(root).has_ledger


def test_a_bad_chantier_slug_is_dropped_not_stored(projet: Path, service: TaskService) -> None:
    _call(projet, chantier="../../etc")
    assert _events(service)[0]["chantier"] == ""


# ── Le refus ───────────────────────────────────────────────────────────────────


def _capacity(projet: Path, body: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text(body, encoding="utf-8")


def _rule(action: str, scope: str = "day", threshold: int = 100, enabled: str = "true") -> str:
    return (
        "version: 1\nusage_rules:\n"
        f"  - {{id: gros, scope: {scope}, metric: tokens, threshold: {threshold}, action: {action}, enabled: {enabled}}}\n"
    )


def test_a_crossed_hold_or_pause_rule_refuses_the_gesture_and_the_kit_call_feeds_it(projet: Path, service: TaskService) -> None:
    assert kit_call_refusal(projet) == ""  # aucun fichier : rien ne retient
    _capacity(projet, _rule("hold"))
    assert kit_call_refusal(projet) == ""  # sous le seuil
    _call(projet)  # 160 jetons de l'Atelier franchissent la règle : c'est la même consommation
    for action in ("hold", "pause"):
        _capacity(projet, _rule(action))
        refusal = kit_call_refusal(projet)
        assert "règle d'usage gros" in refusal and "enabled: false" in refusal and "refusé" in refusal


def test_warn_a_task_scope_a_disabled_rule_and_a_higher_threshold_do_not_refuse(projet: Path, service: TaskService) -> None:
    _call(projet)
    for body in (_rule("warn"), _rule("hold", scope="task"), _rule("hold", enabled="false"), _rule("hold", threshold=1000)):
        _capacity(projet, body)
        assert kit_call_refusal(projet) == ""


def test_an_unreadable_capacity_file_or_ledger_closes_the_gesture(
    projet: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capacity(projet, "version: 1\nusage_rules: [pas\n")
    assert "illisible" in kit_call_refusal(projet) and "fermé" in kit_call_refusal(projet)
    _capacity(projet, _rule("hold"))

    def boom(*_a: object, **_k: object) -> list[object]:
        raise OSError("ledger illisible")

    monkeypatch.setattr(TaskService, "has_ledger", property(boom))
    refusal = kit_call_refusal(projet)
    assert "illisibles" in refusal and "fermé" in refusal
