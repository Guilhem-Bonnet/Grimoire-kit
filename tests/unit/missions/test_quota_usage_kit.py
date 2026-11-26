"""Les appels de modèle du kit lui-même (Atelier) entrent au quota : inscription idempotente, refus par les règles.

Le point d'entrée est réservé au code du kit : origine d'une liste fermée, jetons déclarés par la réponse, rien d'écrit sans
ledger. La consommation du kit s'ADDITIONNE sans jamais rendre « connue » une journée dont des sessions sont sans mesure.
Le refus lit ce que l'admission lit et échoue fermé.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.missions.admission import _consumed_today
from grimoire.missions.quota_usage import KIT_ORIGINS, kit_call_refusal, record_consumption, record_kit_call
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import QUOTA_CONSUMED_EVENT, consumption_today, evaluate, parse_rules

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
    return [dict(e.payload) for e in TaskService(service.home_root).ledger.list_events() if e.event_type == QUOTA_CONSUMED_EVENT]


def _call(projet: Path, call_id: str = "msg_1", **over: object) -> str:
    args: dict[str, object] = {
        "origin": "atelier", "call_id": call_id, "model": "claude-haiku-5-5", "input_tokens": 120, "output_tokens": 40,
        "chantier": "un",
    }
    args.update(over)
    return record_kit_call(projet, **args)  # type: ignore[arg-type]


def test_a_known_call_is_recorded_once_and_replaying_it_writes_nothing(projet: Path, service: TaskService) -> None:
    assert KIT_ORIGINS == ("atelier",)
    assert _call(projet) == "recorded" and _call(projet) == "duplicate"
    (event,) = _events(service)
    assert event["session"] == "kit:atelier:msg_1" and event["role"] == "atelier" and event["tokens"] == 160
    assert (event["input_tokens"], event["output_tokens"], event["chantier"]) == (120, 40, "un")
    assert event["status"] == "lower_bound" and event["task_id"] == "" and event["model"] == "claude-haiku-5-5"
    assert _call(projet, "msg_2") == "recorded" and len(_events(service)) == 2  # un autre appel est un autre événement


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
        {"origin": "agent"}, {"origin": ""}, {"call_id": ""}, {"call_id": "a b"}, {"call_id": "x" * 200}, {"call_id": "a..b"},
        {"call_id": "a/b"}, {"model": ""}, {"model": "a b"}, {"model": "m/n"}, {"model": "../../etc"}, {"model": "a..b"},
        {"model": "m" * 65}, {"input_tokens": -1}, {"output_tokens": True}, {"input_tokens": 1.5}, {"input_tokens": 10**9},
        {"output_tokens": "40"}, {"call_id": None},
    ],
)
def test_an_out_of_shape_call_writes_nothing(projet: Path, service: TaskService, over: dict[str, object]) -> None:
    assert _call(projet, **over) == "refused" and _events(service) == []


def test_without_a_ledger_nothing_is_written(tmp_path: Path) -> None:
    root = tmp_path / "sans"
    (root / STANDARD).mkdir(parents=True)
    assert _call(root) == "no_ledger" and not TaskService(root).has_ledger


def test_a_bad_chantier_slug_is_dropped_not_stored(projet: Path, service: TaskService) -> None:
    _call(projet, chantier="../../etc")
    assert _events(service)[0]["chantier"] == ""


def test_a_single_dot_in_the_model_is_accepted(projet: Path, service: TaskService) -> None:
    assert _call(projet, model="claude-haiku-4.5") == "recorded"


# ── Le kit s'additionne, il ne rend jamais « connue » une journée dont des sessions sont sans mesure ──


def test_a_kit_call_never_turns_an_unknown_day_into_a_known_one(projet: Path, service: TaskService) -> None:
    now = datetime.now(UTC)
    service.ledger.append_event("task.dispatched", "T-1", "task", "tick", {})  # un appel de session sans chiffre
    before = _consumed_today(service.ledger.list_events(), now)
    assert before.tokens is None and before.status == "unknown"
    assert _call(projet, "flip", input_tokens=10, output_tokens=5) == "recorded"
    after = _consumed_today(TaskService(projet).ledger.list_events(), now)
    assert after.status == "unknown" and after.tokens == 15  # le statut d'avant est conservé, le kit s'ajoute au connu


def test_a_kit_call_alone_is_known_and_all_measured_sessions_plus_kit_is_the_exact_sum(projet: Path, service: TaskService) -> None:
    now = datetime.now(UTC)
    _call(projet, "seul", input_tokens=10, output_tokens=5)
    alone = _consumed_today(TaskService(projet).ledger.list_events(), now)
    assert (alone.tokens, alone.status) == (15, "lower_bound")
    service.ledger.append_event(
        QUOTA_CONSUMED_EVENT, "x", "task", "tick", {"session": "s1", "task_id": "", "tokens": 100, "status": "lower_bound"}
    )
    mixed = _consumed_today(TaskService(projet).ledger.list_events(), now)
    assert (mixed.tokens, mixed.status, mixed.unknown_sessions) == (115, "lower_bound", 0)  # somme exacte, connue


def test_the_usage_rules_keep_a_day_with_unmeasured_sessions_incomplete_whatever_the_kit_adds(projet: Path, service: TaskService) -> None:
    rules, _ = parse_rules([{"id": "d", "scope": "day", "metric": "tokens", "threshold": 1000, "action": "warn"}])
    service.ledger.append_event("task.session_launched", "T-1", "task", "worker", {"session_id": "5a1c-aaaa-bbbb"})  # jamais mesurée
    now = datetime.now(UTC)
    (before,) = evaluate(rules, service.ledger.list_events(), now)
    assert not before.complete
    _call(projet, "k")
    (after,) = evaluate(rules, TaskService(projet).ledger.list_events(), now)
    assert after.value == 160 and not after.complete  # les jetons du kit s'ajoutent, l'incomplétude reste
    service.ledger.append_event(
        QUOTA_CONSUMED_EVENT, "T-1", "task", "tick", {"session": "5a1c-aaaa-bbbb", "task_id": "T-1", "tokens": 40, "status": "lower_bound"}
    )
    (measured,) = evaluate(rules, TaskService(projet).ledger.list_events(), now)
    assert measured.value == 200 and measured.complete  # toutes mesurées + kit : somme exacte, connue


def test_a_duplicate_kit_key_in_the_ledger_never_counts_twice(projet: Path, service: TaskService) -> None:
    payload = {"session": "kit:atelier:dup", "task_id": "", "tokens": 160, "status": "lower_bound"}
    for _ in range(2):  # un doublon qui aurait échappé à l'écriture
        service.ledger.append_event(QUOTA_CONSUMED_EVENT, "kit-atelier", "task", "tick", payload)
    events = service.ledger.list_events()
    now = datetime.now(UTC)
    assert _consumed_today(events, now).tokens == 160 and consumption_today(events, now)["tokens"] == 160


# ── Idempotence sous concurrence, clés réservées ───────────────────────────────


def test_eight_threads_recording_the_same_call_write_one_event(projet: Path, service: TaskService) -> None:
    barrier = threading.Barrier(8)
    outcomes: list[str] = []

    def go() -> None:
        barrier.wait()
        outcomes.append(_call(projet, "race"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("recorded") == 1 and outcomes.count("duplicate") == 7
    assert [e["session"] for e in _events(service)] == ["kit:atelier:race"]


def test_a_session_key_starting_with_the_kit_prefix_is_never_written_for_a_session(projet: Path, service: TaskService) -> None:
    service.ledger.append_event(
        "task.session_launched", "T-1", "task", "worker", {"session_id": "kit:atelier:msg_1", "cwd": str(projet)}
    )
    record_consumption(service, now=datetime.now(UTC))
    keys = [str(e["session"]) for e in _events(service)]
    assert keys and not any(k.startswith("kit:") for k in keys)  # inconnu, sous une clé d'événement, jamais « kit: »


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
