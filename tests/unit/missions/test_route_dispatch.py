"""Le routeur branché sur ``run_dispatch`` : ``task.routed``, mode shadow par défaut, mode actif (K9, #774)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import route_dispatch
from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.route_dispatch import ROUTED_EVENT, replay_event
from grimoire.missions.routing_policy import POLICY_FILE
from grimoire.missions.service import TaskService
from grimoire.providers.state import ProviderRuntimeState, Suspension, save_state

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")
V0 = "la suite de tests passe"
V1 = "revue humaine avant fusion"
V2 = "le code est propre"
CHECK = ("test -f marker.txt",)

#: Le fournisseur factice note le modèle qu'on lui a demandé et écrit ce que le check attend.
_SCRIPT = dedent(
    """\
    import sys
    from pathlib import Path
    model = sys.argv[sys.argv.index("--model") + 1]
    with open("calls.log", "a", encoding="utf-8") as fh:
        fh.write(model + "\\n")
    Path("marker.txt").write_text("done", encoding="utf-8")
    """
)


def _registry(root: Path, providers: dict[str, list[str]], *, tier: str = "cheap") -> None:
    script = root / "scripts" / "fake.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_SCRIPT, encoding="utf-8")
    blocks = []
    for pid, models in providers.items():
        listing = "\n".join(f'      - {{id: "{m}", tier: "{tier}"}}' for m in models)
        blocks.append(
            f'  - id: "{pid}"\n    enabled: true\n    provider_type: "hosted"\n'
            f'    allowed_capabilities: ["chat", "code"]\n    default_models: ["{models[0]}"]\n    currency: "api"\n'
            f'    invocation: "{Path(sys.executable).as_posix()} {script.as_posix()} {{prompt}} --model {{model}}"\n'
            f"    models:\n{listing}\n    fallback_order: []\n"
        )
    std = root / "_grimoire" / "standard"
    std.mkdir(parents=True, exist_ok=True)
    (std / "llm-provider-registry.yaml").write_text(
        '$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n' + "".join(blocks)
        + "routing:\n  default_provider: \"\"\n  default_fallback_chain: []\n",
        encoding="utf-8",
    )


def _registry_with_currency(root: Path, providers: dict[str, tuple[str, list[tuple[str, str]]]]) -> None:
    """Comme :func:`_registry`, avec une monnaie par fournisseur et un palier par modèle."""
    script = root / "scripts" / "fake.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_SCRIPT, encoding="utf-8")
    blocks = []
    for pid, (currency, models) in providers.items():
        listing = "\n".join(f'      - {{id: "{m}", tier: "{tier}"}}' for m, tier in models)
        blocks.append(
            f'  - id: "{pid}"\n    enabled: true\n    provider_type: "hosted"\n'
            f'    allowed_capabilities: ["chat", "code"]\n    default_models: ["{models[0][0]}"]\n    currency: "{currency}"\n'
            f'    invocation: "{Path(sys.executable).as_posix()} {script.as_posix()} {{prompt}} --model {{model}}"\n'
            f"    models:\n{listing}\n    fallback_order: []\n"
        )
    std = root / "_grimoire" / "standard"
    std.mkdir(parents=True, exist_ok=True)
    (std / "llm-provider-registry.yaml").write_text(
        '$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n' + "".join(blocks)
        + "routing:\n  default_provider: \"\"\n  default_fallback_chain: []\n",
        encoding="utf-8",
    )


def _policy(root: Path, text: str) -> None:
    path = root / POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _task(service: TaskService, criterion: str = V0) -> str:
    mission = service.ledger.create_mission("Démo routeur", origin="test")
    return service.ledger.create_task(mission.id, "Tâche", acceptance=(criterion,), owner="amelia").id


def _local_task(service: TaskService) -> str:
    mission = service.ledger.create_mission("Démo local", origin="test")
    return service.ledger.create_task(mission.id, "Tâche locale", acceptance=(V0,), owner="amelia", extra={"needs_local": True}).id


def _seed(service: TaskService, model: str, green: int, red: int, *, cost: float | None = None) -> None:
    """Un historique : *model* a réussi *green* fois et échoué *red* fois sur (implementation, V0)."""
    for n in range(green + red):
        service.ledger.append_event(
            "task.dispatched", f"OLD-{model}-{n}", "task", "test",
            {
                "task_id": f"OLD-{model}-{n}", "attempt": 1, "tier": "cheap", "provider": "p", "model": model,
                "verdict": "green" if n < green else "red", "cost_usd": cost, "task_type": "implementation",
                "verifiability": "V0", "start_tier": "cheap",
            },
        )


def _calls(root: Path) -> list[str]:
    log = root / "calls.log"
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def _routed(service: TaskService, task_id: str) -> list[dict[str, object]]:
    return [e.payload for e in service.ledger.list_events(task_id) if e.event_type == ROUTED_EVENT]


# ── Chaque dispatch laisse un task.routed avec ses raisons ─────────────────────


def test_every_dispatch_has_routed_event_with_reasons(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m1"]})
    service = TaskService(tmp_path, LEDGER)
    cases = {
        "green": (V0, CHECK),
        "v2_refused": (V2, ("true",)),
        "no_check": (V0, ()),
        "red": (V0, ("test -f absent.txt",)),
    }
    for name, (criterion, checks) in cases.items():
        tid = _task(service, criterion)
        report = run_dispatch(service, tid, checks=checks)
        events = _routed(service, tid)
        assert len(events) == 1, (name, report.refusal)
        payload = events[0]
        assert payload["reasons"], name
        assert payload["mode"] == "shadow"
        assert payload["task_id"] == tid
        dispatched = [e for e in service.ledger.list_events(tid) if e.event_type == "task.dispatched"]
        order = [e.event_type for e in service.ledger.list_events(tid) if e.event_type in (ROUTED_EVENT, "task.dispatched")]
        assert order[0] == ROUTED_EVENT, "la décision précède la cascade"
        assert len(order) == 1 + len(dispatched)
    assert _routed(service, tid)[0]["outcome"] == "route"


def test_dry_run_traces_nothing(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m1"]})
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK, dry_run=True)
    assert _routed(service, tid) == []


def test_v2_decision_is_session(tmp_path: Path) -> None:
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service, V2)
    run_dispatch(service, tid, checks=("true",))
    assert _routed(service, tid)[0]["outcome"] == "session"


# ── Shadow par défaut : l'ancien chemin gouverne, le routeur est tracé ─────────


def test_shadow_traces_the_route_but_dispatch_follows_the_old_path(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "first", green=2, red=8)
    _seed(service, "second", green=9, red=1)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert report.exit_code == 0
    assert _calls(tmp_path) == ["first"], "shadow : le premier modèle du palier, comme avant"
    decision = _routed(service, tid)[0]["decision"]
    assert isinstance(decision, dict)
    assert decision["model"] == "second", "le routeur, lui, aurait pris le mieux classé"
    assert _routed(service, tid)[0]["mode"] == "shadow"


def test_active_mode_picks_the_model_with_the_best_first_try_success(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n")
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "first", green=2, red=8)
    _seed(service, "second", green=9, red=1)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert report.exit_code == 0
    assert _calls(tmp_path) == ["second"]
    assert report.attempts[0].model == "second"
    assert _routed(service, tid)[0]["mode"] == "active"


def test_active_mode_orders_providers_by_rank(tmp_path: Path) -> None:
    _registry(tmp_path, {"slow": ["a"], "good": ["b"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n")
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "a", green=1, red=9)
    _seed(service, "b", green=10, red=0)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK)
    assert [a.provider for a in report.attempts] == ["good"]


def test_active_mode_skips_a_suspended_model_inside_the_tier(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n")
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "first", green=10, red=0)
    from datetime import UTC, datetime, timedelta

    save_state(tmp_path, {"p": ProviderRuntimeState(model_suspensions={"first": Suspension(datetime.now(UTC) + timedelta(hours=2), "resets_at")})})
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)
    assert _calls(tmp_path) == ["second"]
    decision = _routed(service, tid)[0]["decision"]
    assert isinstance(decision, dict)
    assert [e["reason"] for e in decision["excluded"]] == ["model_suspended"]


def test_active_mode_raises_the_start_tier_for_a_risky_task(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m"]}, tier="strong")
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: strong}\n")
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK)
    assert report.start_tier == "strong"
    assert report.start_tier_reason is not None
    assert report.start_tier_reason.startswith("routeur")


def test_explicit_start_tier_still_wins_over_the_router(tmp_path: Path) -> None:
    _registry_with_currency(tmp_path, {"p": ("api", [("m", "mid"), ("big", "strong")])})  # le routeur dit `route`, au palier strong
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: strong}\n")
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK, start_tier="mid")
    assert report.start_tier == "mid"


# ── Mode actif : la décision `session` / `defer` n'est pas contournée ──────────


_SESSION_RULE = "version: 1\nmode: active\nrules:\n  - {id: s, when: {type: implementation}, route: session}\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n"


def test_active_session_rule_runs_no_cascade(tmp_path: Path) -> None:
    """`route: session` l'emporte sur tout : la décision `session` ne retombe pas sur l'ancien chemin."""
    _registry(tmp_path, {"p": ["m1"]})
    _policy(tmp_path, _SESSION_RULE)
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert _routed(service, tid)[0]["outcome"] == "session"
    assert _calls(tmp_path) == [], "active + session : aucun appel autonome"
    assert report.attempts == ()
    assert report.refusal == "routed_to_session"
    assert report.exit_code == 2
    assert report.refusal_message
    assert not [e for e in service.ledger.list_events(tid) if e.event_type == "task.dispatched"]


def test_active_without_a_matching_rule_runs_no_cascade(tmp_path: Path) -> None:
    """Aucune règle qui correspond donne `session` : en mode actif, rien ne part."""
    _registry(tmp_path, {"p": ["m1"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v1, when: {class: V1}, min_tier: mid}\n")
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert _routed(service, tid)[0]["outcome"] == "session"
    assert _calls(tmp_path) == []
    assert report.refusal == "routed_to_session"


def test_shadow_keeps_the_old_path_even_when_the_router_would_say_session(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m1"]})
    _policy(tmp_path, _SESSION_RULE.replace("mode: active", "mode: shadow"))
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK)
    assert _routed(service, tid)[0]["outcome"] == "session"
    assert _calls(tmp_path) == ["m1"]
    assert report.refusal is None


def test_active_defer_runs_no_cascade(tmp_path: Path) -> None:
    """Aucun modèle disponible au palier retenu : `defer`, jamais d'appel hors décision."""
    from datetime import UTC, datetime, timedelta

    _registry(tmp_path, {"p": ["m1"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n")
    save_state(tmp_path, {"p": ProviderRuntimeState(model_suspensions={"m1": Suspension(datetime.now(UTC) + timedelta(hours=2), "resets_at")})})
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert _routed(service, tid)[0]["outcome"] == "defer"
    assert _calls(tmp_path) == []
    assert report.refusal == "routed_defer"
    assert report.exit_code == 2


def test_routing_refusals_are_reported_by_the_cli(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m1"]})
    _policy(tmp_path, _SESSION_RULE)
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    result = runner.invoke(app, ["task", "dispatch", tid, "--check", CHECK[0], "--project-root", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "session" in result.output
    assert _calls(tmp_path) == []


# ── Mode actif : la contrainte de monnaie vaut pour l'appel, pas seulement pour la trace ──

_LOCAL_RULES = "version: 1\nmode: active\nrules:\n  - {id: r-local, when: {needs_local: true}, providers: {currency: local}}\n  - {id: r-v0, when: {class: V0}, min_tier: cheap}\n"


def test_active_needs_local_calls_only_the_local_provider(tmp_path: Path) -> None:
    _registry_with_currency(tmp_path, {"cloud": ("api", [("cloudm", "cheap")]), "loc": ("local", [("localm", "cheap")])})
    _policy(tmp_path, _LOCAL_RULES)
    service = TaskService(tmp_path, LEDGER)
    tid = _local_task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    decision = _routed(service, tid)[0]["decision"]
    assert isinstance(decision, dict)
    assert (decision["provider"], decision["model"]) == ("loc", "localm")
    assert _calls(tmp_path) == ["localm"], "l'appel suit la décision tracée"
    assert [a.provider for a in report.attempts] == ["loc"]


def test_active_needs_local_never_escalates_to_a_cloud_provider(tmp_path: Path) -> None:
    """Le palier suivant n'admet pas non plus un fournisseur hors de la monnaie requise."""
    _registry_with_currency(
        tmp_path,
        {"cloud": ("api", [("cloudm", "cheap"), ("cloudmid", "mid")]), "loc": ("local", [("localm", "cheap")])},
    )
    _policy(tmp_path, _LOCAL_RULES)
    service = TaskService(tmp_path, LEDGER)
    tid = _local_task(service)

    report = run_dispatch(service, tid, checks=("test -f absent.txt",))  # rouge : la cascade escalade

    assert _calls(tmp_path) == ["localm"]
    assert {a.provider for a in report.attempts} == {"loc"}


def test_active_needs_local_without_a_local_provider_defers_instead_of_calling_the_cloud(tmp_path: Path) -> None:
    _registry_with_currency(tmp_path, {"cloud": ("api", [("cloudm", "cheap")])})
    _policy(tmp_path, _LOCAL_RULES)
    service = TaskService(tmp_path, LEDGER)
    tid = _local_task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert _routed(service, tid)[0]["outcome"] == "defer"
    assert _calls(tmp_path) == []
    assert report.refusal == "routed_defer"


# ── Politique invalide / routeur en panne : jamais au prix du dispatch ─────────


def test_invalid_policy_refuses_the_cascade_and_is_traced_as_a_refusal(tmp_path: Path) -> None:
    """`on_invalid: refuse` : un fichier de politique qui ne se valide pas ne laisse aucune route autonome."""
    _registry(tmp_path, {"p": ["first", "second"]})
    _policy(tmp_path, "version: 1\nmode: active\nrules:\n  - {id: r, when: {quota_left: 1}, min_tier: strong}\n")
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "second", green=10, red=0)
    tid = _task(service)

    report = run_dispatch(service, tid, checks=CHECK)

    assert report.refusal == "routing_policy_invalid"
    assert report.exit_code == 2
    assert report.attempts == ()
    assert _calls(tmp_path) == [], "la politique illisible ne laisse aucun appel autonome"
    assert report.start_tier_reason is not None
    assert "quota_left" in report.start_tier_reason or "politique" in report.start_tier_reason
    payload = _routed(service, tid)[0]
    assert payload["outcome"] == "refuse"
    assert "politique illisible" in str(payload["reasons"])
    assert payload["policy_error"]


def test_unparsable_policy_file_refuses_too(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m"]})
    _policy(tmp_path, "version: [unclosed\n")
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK)
    assert (report.refusal, report.exit_code, _calls(tmp_path)) == ("routing_policy_invalid", 2, [])


def test_invalid_policy_dry_run_traces_nothing_but_says_it_would_refuse(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["m"]})
    _policy(tmp_path, "version: 9\n")
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    report = run_dispatch(service, tid, checks=CHECK, dry_run=True)
    assert report.refusal == "routing_policy_invalid"
    assert report.exit_code == 0, "--dry-run ne sort jamais en erreur"
    assert _routed(service, tid) == []


def test_a_router_crash_never_breaks_the_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _registry(tmp_path, {"p": ["m"]})
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("registre cassé")

    monkeypatch.setattr(route_dispatch, "provider_view", boom)
    report = run_dispatch(service, tid, checks=CHECK)

    assert report.exit_code == 0
    payload = _routed(service, tid)[0]
    assert payload["outcome"] == "refuse"
    assert "registre cassé" in str(payload["reasons"])


# ── Rejeu : route explain ──────────────────────────────────────────────────────


def test_routed_event_replays_identically_and_carries_a_digest(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "second", green=9, red=1)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)
    payload = _routed(service, tid)[0]
    replayed, same, digest = replay_event(payload)
    assert replayed is not None
    assert same
    assert digest == payload["decision"]["inputs_digest"]  # type: ignore[index]
    json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_replay_survives_the_ledger_round_trip_with_prices_and_costs(tmp_path: Path) -> None:
    """Les flottants (prix catalogue, coûts observés) ne changent pas l'empreinte en passant par le disque."""
    _registry(tmp_path, {"p": ["claude-haiku-4-5", "claude-haiku-3-5"]})
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "claude-haiku-4-5", green=5, red=0, cost=0.0123)
    _seed(service, "claude-haiku-3-5", green=5, red=0, cost=1.0)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)
    reloaded = MissionLedger(tmp_path / LEDGER)
    payload = next(e.payload for e in reloaded.list_events(tid) if e.event_type == ROUTED_EVENT)
    _, same, _ = replay_event(payload)
    assert same
    assert payload["decision"]["model"] == "claude-haiku-3-5", "à réussite égale, le prix catalogue le plus bas"  # type: ignore[index]


def test_replay_is_identical_with_a_cost_that_the_serialisation_must_not_round(tmp_path: Path) -> None:
    """Un coût moyen à plus de six décimales : les entrées tracées reproduisent exactement la décision."""
    _registry(tmp_path, {"p": ["claude-haiku-4-5"]})
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "claude-haiku-4-5", green=5, red=0, cost=0.01234567)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)
    reloaded = MissionLedger(tmp_path / LEDGER)
    payload = next(e.payload for e in reloaded.list_events(tid) if e.event_type == ROUTED_EVENT)
    replayed, same, _ = replay_event(payload)
    assert replayed is not None
    assert replayed.to_dict() == payload["decision"]
    assert same
    result = runner.invoke(app, ["route", "explain", tid, "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["replay_identical"] is True


def test_route_explain_replays_and_detects_tampering(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)

    ok = runner.invoke(app, ["route", "explain", tid, "--project-root", str(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output
    data = json.loads(ok.stdout)
    assert data["replay_identical"] is True
    assert data["decision"]["model"] == "first"

    events = tmp_path / LEDGER / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    tampered = []
    for line in lines:
        record = json.loads(line)
        if record["event_type"] == ROUTED_EVENT:
            record["payload"]["inputs"]["providers"]["models"][0]["model"] = "forged"
        tampered.append(json.dumps(record, ensure_ascii=False))
    events.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    bad = runner.invoke(app, ["route", "explain", tid, "--project-root", str(tmp_path), "--json"])
    assert bad.exit_code == 1
    assert json.loads(bad.stdout)["replay_identical"] is False


def test_route_explain_without_event_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["route", "explain", "GAO-nope", "--project-root", str(tmp_path)])
    assert result.exit_code == 2


# ── Rapport ────────────────────────────────────────────────────────────────────


def test_route_report_counts_and_says_not_measured_under_five(tmp_path: Path) -> None:
    _registry(tmp_path, {"p": ["first", "second"]})
    service = TaskService(tmp_path, LEDGER)
    _seed(service, "second", green=9, red=1)
    _seed(service, "thin", green=1, red=1)
    tid = _task(service)
    run_dispatch(service, tid, checks=CHECK)  # shadow : route -> second, dispatch -> first

    result = runner.invoke(app, ["route", "report", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    by_model = {m["model"]: m for m in report["models"]}
    assert by_model["second"]["pass1_label"] == "90%"
    assert by_model["thin"]["pass1_label"] == "non mesuré"
    assert by_model["thin"]["pass1"] is None
    assert report["routed"]["by_mode"] == {"shadow": 1}
    shadow = report["shadow_comparison"]
    assert (shadow["compared"], shadow["agree"]) == (1, 0)
    assert shadow["disagreements"][0]["routed"] == ["cheap", "second"]
    assert shadow["disagreements"][0]["dispatched"] == ["cheap", "first"]


# ── Évaluation hors ligne (D16) ────────────────────────────────────────────────


def _eval_set(path: Path, *, n: int = 20, cost: bool = True) -> Path:
    """V0 sans risque : le routeur et la base prennent haiku ; les V0 à risque : le routeur prend opus."""
    tasks = []
    for i in range(n):
        risky = i < 5
        results = {
            "haiku": {"passed": True, "cost_usd": 0.01 if cost else None},
            "opus": {"passed": True, "cost_usd": 0.05 if cost else None},
        }
        tasks.append({"id": f"E-{i}", "class": "V0", "type": "implementation", "risk": ["security"] if risky else [], "results": results})
    path.write_text(json.dumps({"version": 1, "tasks": tasks}), encoding="utf-8")
    return path


def _eval_project(root: Path) -> None:
    std = root / "_grimoire" / "standard"
    std.mkdir(parents=True)
    (std / "llm-provider-registry.yaml").write_text(
        'providers:\n  - id: "a"\n    enabled: true\n    provider_type: "hosted"\n    currency: "api"\n    models:\n'
        '      - {id: "haiku", tier: "cheap"}\n      - {id: "sonnet", tier: "mid"}\n      - {id: "opus", tier: "strong"}\n',
        encoding="utf-8",
    )


def test_route_eval_is_not_measured_when_the_cost_is_unknown(tmp_path: Path) -> None:
    _eval_project(tmp_path)
    result = runner.invoke(app, ["route", "eval", "--set", str(_eval_set(tmp_path / "set.json", cost=False)), "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 3, result.output
    out = json.loads(result.stdout)
    assert out["verdict"] == "non mesuré"
    assert out["activable"] is False
    assert any("coût inconnu" in r for r in out["reasons"])


def test_route_eval_is_not_measured_under_twenty_tasks(tmp_path: Path) -> None:
    _eval_project(tmp_path)
    result = runner.invoke(app, ["route", "eval", "--set", str(_eval_set(tmp_path / "set.json", n=12)), "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["verdict"] == "non mesuré"


def test_route_eval_threshold_is_base_minus_one_and_known_cost(tmp_path: Path) -> None:
    _eval_project(tmp_path)
    # Le routeur envoie les 5 tâches à risque sur opus (0,05) contre haiku (0,01) pour la base :
    # coût du routeur > coût de la base, donc le seuil n'est pas tenu même à pass^1 égal.
    pricey = runner.invoke(app, ["route", "eval", "--set", str(_eval_set(tmp_path / "a.json")), "--project-root", str(tmp_path), "--json"])
    assert pricey.exit_code == 1
    assert json.loads(pricey.stdout)["verdict"] == "fail"
    # Sans risque, le routeur et la base choisissent le même modèle : égalité, le seuil est tenu.
    flat = tmp_path / "flat.json"
    flat.write_text(
        json.dumps({"version": 1, "tasks": [
            {"id": f"F-{i}", "class": "V0", "type": "implementation", "results": {"haiku": {"passed": i != 0, "cost_usd": 0.01}}}
            for i in range(20)
        ]}),
        encoding="utf-8",
    )
    ok = runner.invoke(app, ["route", "eval", "--set", str(flat), "--project-root", str(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["verdict"] == "pass"
    assert json.loads(ok.stdout)["activable"] is True


def test_route_eval_refuses_an_invalid_policy(tmp_path: Path) -> None:
    _eval_project(tmp_path)
    _policy(tmp_path, "version: 9\n")
    result = runner.invoke(app, ["route", "eval", "--set", str(_eval_set(tmp_path / "s.json")), "--project-root", str(tmp_path)])
    assert result.exit_code == 2


def test_ledger_is_not_otherwise_touched_by_routing(tmp_path: Path) -> None:
    """Le routeur n'écrit que `task.routed` : aucune transition, aucun autre événement de son fait."""
    _registry(tmp_path, {"p": ["m"]})
    service = TaskService(tmp_path, LEDGER)
    tid = _task(service)
    before = {e.event_type for e in service.ledger.list_events(tid)}
    run_dispatch(service, tid, checks=CHECK)
    after = {e.event_type for e in MissionLedger(tmp_path / LEDGER).list_events(tid)}
    assert after - before == {ROUTED_EVENT, "task.dispatched"}
