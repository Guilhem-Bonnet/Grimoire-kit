"""Le routeur de LLM déclaratif (K9, issue #774) — la décision pure, sa politique, son historique.

Les propriétés sont vérifiées sur des tirages à graine fixe (``random.Random``), pas sur
``hypothesis`` : le test doit tourner partout où tourne le reste de la suite, et un tirage
déterministe donne un contre-exemple rejouable.
"""

from __future__ import annotations

import dataclasses
import inspect
import itertools
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.costs import Cost
from grimoire.missions.dispatch_history import MIN_OBSERVATIONS
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.router import (
    HISTORY_WINDOW,
    HistoryRow,
    ModelOption,
    ProviderView,
    RouteDecision,
    RouteHistory,
    RoutingTask,
    provider_view,
    replay_route_history,
    route,
)
from grimoire.missions.routing_policy import (
    DEFAULT_POLICY,
    POLICY_FILE,
    PolicyError,
    RoutingPolicy,
    Rule,
    load_policy,
    parse_policy,
)
from grimoire.providers.state import ProviderRuntimeState, Suspension, save_state

FORBIDDEN_WORDS = ("quota", "budget", "token", "remaining", "restant")


def _view(*options: ModelOption) -> ProviderView:
    return ProviderView(tuple(options))


def _opt(model: str, tier: str, *, provider: str = "p", order: int = 0, **kw: object) -> ModelOption:
    return ModelOption(provider=provider, model=model, tier=tier, order=order, **kw)  # type: ignore[arg-type]


FULL_VIEW = _view(
    _opt("haiku", "cheap"),
    _opt("sonnet", "mid"),
    _opt("opus", "strong"),
)


def _task(cls: str = "V0", **kw: object) -> RoutingTask:
    return RoutingTask(task_id="T-1", verifiability=cls, task_type="implementation", **kw)  # type: ignore[arg-type]


def _row(model: str, obs: int, ok: int, *, cls: str = "V0", cost: float | None = None) -> HistoryRow:
    return HistoryRow(
        "implementation", cls, model, obs, ok,
        cost_known_usd=0.0 if cost is None else cost * obs, cost_priced=0 if cost is None else obs,
        cost_unpriced=obs if cost is None else 0,
    )


# ── Déterminisme et indépendance à l'ordre des règles ──────────────────────────


def _random_task(rng: random.Random) -> RoutingTask:
    return RoutingTask(
        task_id="T-1",
        verifiability=rng.choice(["V0", "V1", "V2"]),
        task_type=rng.choice(["implementation", "security", "documentation"]),
        risk_tags=tuple(sorted(rng.sample(["security", "migration", "ledger", "ui"], rng.randint(0, 3)))),
        exclusive_files=rng.randint(0, 12),
        needs_local=rng.random() < 0.2,
    )


def test_decision_is_deterministic_and_independent_of_rule_order() -> None:
    rng = random.Random(774)
    rules = list(DEFAULT_POLICY.rules)
    for _ in range(200):
        task = _random_task(rng)
        shuffled = rules[:]
        rng.shuffle(shuffled)
        base = route(task, RouteHistory(), FULL_VIEW)
        again = route(task, RouteHistory(), FULL_VIEW)
        permuted = route(task, RouteHistory(), FULL_VIEW, policy=dataclasses.replace(DEFAULT_POLICY, rules=tuple(shuffled)))
        assert base == again
        assert base == permuted, (task, [r.id for r in shuffled])


def test_tier_is_the_maximum_of_matching_rules() -> None:
    view = _view(_opt("haiku", "cheap"), _opt("sonnet", "mid"), _opt("opus", "strong"))
    risky = route(_task("V0", risk_tags=("security",), exclusive_files=20), RouteHistory(), view)
    assert risky.tier == "strong"
    assert risky.rule_ids == ("r-risk", "r-size", "r-v0")
    big = route(_task("V0", exclusive_files=9), RouteHistory(), view)
    assert big.tier == "mid"


def test_no_rule_routes_to_session() -> None:
    only_risk = RoutingPolicy(rules=(Rule("r-risk", {"risk_any": ["security"]}, min_tier="strong"),))
    decision = route(_task("V0"), RouteHistory(), FULL_VIEW, policy=only_risk)
    assert decision.outcome == "session"
    assert decision.model is None
    assert route(_task("V0"), RouteHistory(), FULL_VIEW, policy=RoutingPolicy()).outcome == "session"


def test_v2_and_session_rule_are_absorbing() -> None:
    assert route(_task("V2"), RouteHistory(), FULL_VIEW).outcome == "session"
    # Même sans règle r-v2 : V2 n'a jamais de route autonome.
    assert route(_task("V2"), RouteHistory(), FULL_VIEW, policy=RoutingPolicy(rules=(Rule("x", {}, min_tier="cheap"),))).outcome == "session"
    to_session = RoutingPolicy(rules=(Rule("s", {"type": "implementation"}, route="session"), *DEFAULT_POLICY.rules))
    assert route(_task("V0"), RouteHistory(), FULL_VIEW, policy=to_session).outcome == "session"


def test_never_below_the_class_floor() -> None:
    rng = random.Random(1)
    # Une politique qui ne dit rien du plancher, et un historique qui recommande cheap.
    lax = RoutingPolicy(rules=(Rule("r", {}, min_tier="cheap"),))
    for _ in range(100):
        task = _random_task(rng)
        if task.verifiability != "V1":
            continue
        history = RouteHistory(start_tiers={(task.task_type, "V1"): ("cheap", "re-sonde")})
        for policy in (DEFAULT_POLICY, lax):
            decision = route(task, history, FULL_VIEW, policy=policy)
            assert decision.tier in ("mid", "strong"), decision


def test_history_only_raises_the_tier() -> None:
    history = RouteHistory(start_tiers={("implementation", "V0"): ("mid", "escalade depuis cheap à 60 %")})
    assert route(_task("V0"), history, FULL_VIEW).tier == "mid"
    lowered = RouteHistory(start_tiers={("implementation", "V0"): ("cheap", "re-sonde")})
    risky = route(_task("V0", risk_tags=("security",)), lowered, FULL_VIEW)
    assert risky.tier == "strong"


# ── Politique : validation fail-closed ─────────────────────────────────────────


MALFORMED = {
    "pas_une_table": ["a"],
    "sans_version": {"rules": []},
    "version_inconnue": {"version": 2},
    "mode_inconnu": {"version": 1, "mode": "turbo"},
    "on_invalid_inconnu": {"version": 1, "on_invalid": "ignore"},
    "cle_inconnue": {"version": 1, "budget": 10},
    "regle_sans_effet": {"version": 1, "rules": [{"id": "a", "when": {}}]},
    "regle_sans_id": {"version": 1, "rules": [{"when": {}, "min_tier": "mid"}]},
    "id_en_double": {"version": 1, "rules": [{"id": "a", "min_tier": "mid"}, {"id": "a", "min_tier": "mid"}]},
    "palier_inconnu": {"version": 1, "rules": [{"id": "a", "min_tier": "huge"}]},
    "condition_de_quota": {"version": 1, "rules": [{"id": "a", "when": {"quota_remaining_lt": 10}, "min_tier": "cheap"}]},
    "condition_de_budget": {"version": 1, "rules": [{"id": "a", "when": {"budget": "low"}, "min_tier": "cheap"}]},
    "classe_inconnue": {"version": 1, "rules": [{"id": "a", "when": {"class": "V9"}, "min_tier": "mid"}]},
    "route_inconnue": {"version": 1, "rules": [{"id": "a", "route": "opus"}]},
    "monnaie_inconnue": {"version": 1, "rules": [{"id": "a", "providers": {"currency": "gold"}}]},
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_policy_is_rejected(name: str) -> None:
    with pytest.raises(PolicyError):
        parse_policy(MALFORMED[name])


@pytest.mark.parametrize("raw", ["", "version: [", "- a\n- b\n", "version: 1\nrules: {a: 1}\n", "\x00"])
def test_malformed_policy_refuses_autonomous(tmp_path: Path, raw: str) -> None:
    """Une politique illisible n'est jamais remplacée par le défaut : aucune route autonome."""
    path = tmp_path / POLICY_FILE
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")
    load = load_policy(tmp_path)
    assert load.policy is None
    assert load.error
    assert load.source == "file"


def test_missing_policy_file_is_the_shipped_default_in_shadow(tmp_path: Path) -> None:
    load = load_policy(tmp_path)
    assert load.policy is DEFAULT_POLICY
    assert load.source == "default"
    assert DEFAULT_POLICY.mode == "shadow"
    assert parse_policy({"version": 1}).mode == "shadow"
    assert parse_policy(DEFAULT_POLICY.to_dict()) == DEFAULT_POLICY


# ── Aucune entrée de quota, de budget ni de tokens ─────────────────────────────


def _field_names(cls: type) -> set[str]:
    names: set[str] = set()
    for f in dataclasses.fields(cls):
        names.add(f.name)
    return names


def test_router_signature_has_no_quota_budget_or_token_input() -> None:
    params = list(inspect.signature(route).parameters)
    assert params[:3] == ["task", "history", "provider_state"]
    assert params == ["task", "history", "provider_state", "policy"]
    for cls in (RoutingTask, RouteHistory, HistoryRow, ProviderView, ModelOption, RoutingPolicy, Rule):
        for name in _field_names(cls):
            assert not any(word in name.lower() for word in FORBIDDEN_WORDS), (cls.__name__, name)
    for name in itertools.chain(params, ("when_keys",)):
        assert not any(word in name for word in FORBIDDEN_WORDS)


def _project_with_models(root: Path) -> None:
    std = root / "_grimoire" / "standard"
    std.mkdir(parents=True)
    (std / "llm-provider-registry.yaml").write_text(
        """\
$schema: "grimoire-llm-provider-registry/v1"
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-haiku-4-5"]
    currency: "quota"
    invocation: "echo {prompt} --model {model}"
    models:
      - {id: "claude-haiku-4-5", tier: "cheap"}
      - {id: "claude-sonnet-4-6", tier: "mid"}
      - {id: "claude-opus-4-7", tier: "strong"}
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
""",
        encoding="utf-8",
    )


def test_changing_quota_or_budget_never_changes_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Propriété : le niveau du forfait, le budget, les tokens restants ne déplacent jamais la route."""
    _project_with_models(tmp_path)
    rng = random.Random(42)
    task = _task("V1", risk_tags=("security",))
    baseline: dict[str, object] | None = None
    for _ in range(25):
        observation = {
            "used_percent": rng.uniform(0, 100),
            "remaining_tokens": rng.randint(0, 10_000_000),
            "budget_usd": rng.choice([0, 0.01, 5, 1e6]),
            "resets_at": "2026-12-31T00:00:00+00:00",
        }
        save_state(tmp_path, {"anthropic": ProviderRuntimeState(quota_observation=observation, probed_at="2026-10-17T00:00:00+00:00")})
        monkeypatch.setenv("GRIMOIRE_BUDGET_USD", str(rng.uniform(0, 100)))
        monkeypatch.setenv("GRIMOIRE_TOKENS_REMAINING", str(rng.randint(0, 10**7)))
        for autonomous in (False, True):
            decision = route(task, RouteHistory(), provider_view(tmp_path, autonomous=autonomous))
            if not autonomous:
                baseline = baseline or decision.to_dict()
                assert decision.to_dict() == baseline
            assert decision.model == "claude-opus-4-7"
    assert baseline is not None


def test_a_suspended_provider_is_unavailable_whatever_the_quota_level(tmp_path: Path) -> None:
    """Le refus du fournisseur compte (sa date de réouverture), le niveau de son forfait jamais."""
    _project_with_models(tmp_path)
    until = datetime.now(UTC) + timedelta(hours=3)
    save_state(tmp_path, {"anthropic": ProviderRuntimeState(suspension=Suspension(until, "resets_at"), quota_observation={"used_percent": 1})})
    decision = route(_task("V0"), RouteHistory(), provider_view(tmp_path))
    assert decision.outcome == "defer"
    assert decision.tier == "cheap"
    assert decision.defer_until == until.isoformat()
    assert {e["reason"] for e in decision.excluded} == {"provider_suspended"}


# ── Intra-palier : un modèle fermé est sauté, la réussite départage ────────────


def test_intra_tier_skips_suspended_model() -> None:
    view = _view(
        _opt("fast-a", "cheap", order=0, available=False, reason="model_suspended", until="2026-10-18T00:00:00+00:00"),
        _opt("fast-b", "cheap", order=0),
    )
    decision = route(_task("V0"), RouteHistory(), view)
    assert decision.model == "fast-b"
    assert decision.excluded == ({"provider": "p", "model": "fast-a", "reason": "model_suspended", "until": "2026-10-18T00:00:00+00:00"},)


def test_excluded_is_filled_with_a_reason_for_every_dropped_model() -> None:
    view = _view(
        _opt("a", "cheap", available=False, reason="provider_cooling_down"),
        _opt("b", "cheap", provider="q", available=False, reason="provider_unprobed"),
        _opt("c", "cheap", provider="r"),
        _opt("m", "mid"),
    )
    decision = route(_task("V0"), RouteHistory(), view)
    assert decision.model == "c"
    assert {(e["model"], e["reason"]) for e in decision.excluded} == {("a", "provider_cooling_down"), ("b", "provider_unprobed")}
    assert all(e["model"] != "m" for e in decision.excluded), "un autre palier n'est pas une exclusion"


def test_success_rate_breaks_ties_inside_a_tier_when_measured() -> None:
    view = _view(_opt("first", "cheap", order=0), _opt("second", "cheap", provider="q", order=1))
    assert route(_task("V0"), RouteHistory(), view).model == "first"
    history = RouteHistory((_row("first", 10, 4), _row("second", 10, 9)))
    decision = route(_task("V0"), history, view)
    assert decision.model == "second"
    assert decision.ranking == (("q", "second"), ("p", "first"))
    # Sous le seuil d'observations, la réussite ne départage pas : ordre déclaré.
    thin = RouteHistory((_row("first", MIN_OBSERVATIONS - 1, 0), _row("second", MIN_OBSERVATIONS - 1, MIN_OBSERVATIONS - 1)))
    assert route(_task("V0"), thin, view).model == "first"


def test_equal_success_falls_back_to_known_unit_cost_then_declared_order() -> None:
    view = _view(
        _opt("pricey", "cheap", order=0, unit_cost=30.0),
        _opt("unknown", "cheap", provider="q", order=1, unit_cost=None),
        _opt("cheapest", "cheap", provider="r", order=2, unit_cost=6.0),
    )
    assert route(_task("V0"), RouteHistory(), view).model == "cheapest"
    without_prices = _view(_opt("x", "cheap", order=0), _opt("y", "cheap", provider="q", order=1))
    assert route(_task("V0"), RouteHistory(), without_prices).model == "x"
    # La réussite prime sur le coût.
    history = RouteHistory((_row("pricey", 10, 10), _row("cheapest", 10, 5)))
    assert route(_task("V0"), history, view).model == "pricey"


def test_no_candidate_defers_and_never_drops_a_tier() -> None:
    until = "2026-10-20T10:00:00+00:00"
    view = _view(
        _opt("a", "mid", available=False, reason="provider_suspended", until=until),
        _opt("h", "cheap"),
    )
    decision = route(_task("V1"), RouteHistory(), view)
    assert decision.outcome == "defer"
    assert decision.tier == "mid"
    assert decision.model is None
    assert decision.defer_until == until
    unknown = route(_task("V1"), RouteHistory(), _view(_opt("a", "mid", available=False, reason="provider_unprobed")))
    assert unknown.outcome == "defer"
    assert unknown.defer_until is None


def test_local_need_restricts_to_the_local_currency() -> None:
    view = _view(_opt("hosted", "cheap", currency="api"), _opt("tiny", "cheap", provider="q", currency="local", order=1))
    decision = route(_task("V0", needs_local=True), RouteHistory(), view)
    assert decision.model == "tiny"
    assert any(e["model"] == "hosted" for e in decision.excluded)


def test_reasons_and_digest_are_filled() -> None:
    decision = route(_task("V1", risk_tags=("security",)), RouteHistory(), FULL_VIEW)
    assert decision.reasons
    assert any("r-risk" in r for r in decision.reasons)
    assert len(decision.inputs_digest) == 64
    other = route(_task("V1"), RouteHistory(), FULL_VIEW)
    assert other.inputs_digest != decision.inputs_digest
    assert isinstance(decision, RouteDecision)


# ── Historique : attribution par tentative ─────────────────────────────────────


def _dispatched(ledger: MissionLedger, task_id: str, attempt: int, model: str, verdict: str, *, cost: float | None = None) -> None:
    ledger.append_event(
        "task.dispatched", task_id, "task", "test",
        {
            "task_id": task_id, "attempt": attempt, "tier": "cheap", "provider": "p", "model": model, "verdict": verdict,
            "cost_usd": cost, "task_type": "implementation", "verifiability": "V0", "start_tier": "cheap",
        },
    )


def test_attribution_per_attempt(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    # Cascade : A rouge, B 429 (non attribuée), C unrunnable, D vert.
    for n, (model, verdict) in enumerate([("A", "red"), ("B", "rate_limit"), ("C", "unrunnable"), ("D", "green")], start=1):
        _dispatched(ledger, "T-1", n, model, verdict, cost=0.5 if model == "D" else None)
    rows = {r.model: r for r in replay_route_history(ledger).rows}
    assert set(rows) == {"A", "C", "D"}, "une tentative sans résultat propre n'est attribuée à personne"
    assert (rows["A"].observations, rows["A"].successes) == (1, 0)
    assert (rows["C"].observations, rows["C"].successes) == (1, 0), "unrunnable n'est jamais un succès"
    assert (rows["D"].observations, rows["D"].successes) == (1, 1)
    assert rows["D"].mean_cost == Cost.exact(0.5)
    assert rows["A"].mean_cost.status == "unknown", "un coût absent n'est jamais compté comme zéro"


def test_history_is_a_replayed_table_bounded_by_a_window(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    for n in range(HISTORY_WINDOW + 10):
        _dispatched(ledger, f"T-{n}", 1, "M", "red" if n < 10 else "green")
    row = replay_route_history(ledger).rows[0]
    assert row.observations == HISTORY_WINDOW
    assert row.successes == HISTORY_WINDOW, "les 10 premiers échecs sont sortis de la fenêtre"
    assert replay_route_history(MissionLedger(tmp_path / "ledger")).to_dict() == replay_route_history(ledger).to_dict()


def test_rate_is_not_measured_below_five_observations() -> None:
    assert _row("m", MIN_OBSERVATIONS - 1, 0).rate is None
    assert _row("m", MIN_OBSERVATIONS, 3).rate == 0.6


def test_provider_view_reports_a_suspended_model_with_its_reopening(tmp_path: Path) -> None:
    _project_with_models(tmp_path)
    until = datetime.now(UTC) + timedelta(hours=2)
    save_state(tmp_path, {"anthropic": ProviderRuntimeState(model_suspensions={"claude-haiku-4-5": Suspension(until, "resets_at")})})
    options = {o.model: o for o in provider_view(tmp_path).models}
    assert not options["claude-haiku-4-5"].available
    assert options["claude-haiku-4-5"].reason == "model_suspended"
    assert options["claude-haiku-4-5"].until == until.isoformat()
    assert options["claude-sonnet-4-6"].available
    assert options["claude-sonnet-4-6"].unit_cost == 18.0


def test_decision_serialises_to_strict_utf8() -> None:
    decision = route(_task("V1", risk_tags=("migration",)), RouteHistory(), FULL_VIEW)
    json.dumps(decision.to_dict(), ensure_ascii=False).encode("utf-8")
