"""K8 (#773) : suspension datée par fournisseur et par modèle, 529 distinct du quota.

Le refroidissement ne connaissait que ``rate_limit`` et ``timeout`` (5 min qui
doublent) : aucune date de réouverture, un 529 traité comme un forfait épuisé,
un fournisseur jamais sondé réputé disponible, et un événement ``allowed`` du
flux ``stream-json`` pris pour une limite à cause du seul mot ``rate_limit``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.dispatch import _run_provider_call
from grimoire.providers.quota import classify_failure, observe_window
from grimoire.providers.registry import read_registry
from grimoire.providers.routing import candidates, choose
from grimoire.providers.state import (
    ProviderRuntimeState,
    Suspension,
    available_models,
    clear_unknown_suspensions,
    load_state,
    record_failure,
    record_suspension,
    save_state,
)

T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)

_REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["opus"]
    currency: "quota"
    invocation: "claude -p {prompt} --model {model}"
    models:
      - id: "opus"
        tier: "mid"
      - id: "sonnet"
        tier: "mid"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: []
      retention_notes: ""
    fallback_order: ["local"]
    audit: {log_prompts: false, log_metadata: true}
  - id: "local"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat", "code"]
    default_models: ["qwen"]
    currency: "local"
    invocation: "ollama run {model}"
    models:
      - id: "qwen"
        tier: "mid"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: []
      retention_notes: ""
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
routing:
  default_provider: "anthropic"
  default_fallback_chain: ["local"]
  require_capability_match: true
  require_data_policy_match: true
"""


def _registry(root: Path) -> None:
    path = root / "_grimoire/standard/llm-provider-registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_REGISTRY, encoding="utf-8")


def _probed(root: Path, *ids: str) -> None:
    state = load_state(root)
    for pid in ids:
        state[pid] = ProviderRuntimeState(available=True, probed_at=T0.isoformat())
    save_state(root, state)


def _ids(root: Path, **kwargs: object) -> list[str]:
    return [p.id for p in candidates(root, "mid", now=T0 + timedelta(seconds=1), **kwargs)]  # type: ignore[arg-type]


def _event(status: str, resets: int | None, **extra: object) -> str:
    info: dict[str, object] = {"status": status, **extra}
    if resets is not None:
        info["resetsAt"] = resets
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info})


# ── Lecture des signaux ─────────────────────────────────────────────────


def test_resets_at_3pm_gives_suspended_until_15h_and_source_resets_at() -> None:
    signal = classify_failure("", "Claude usage limit reached. Your limit resets at 3pm", now=T0)

    assert signal is not None
    assert signal.kind == "quota"
    assert signal.until == datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    assert signal.source == "resets_at"


def test_resets_at_already_past_today_means_tomorrow() -> None:
    signal = classify_failure("", "quota exhausted, resets at 9am", now=T0)

    assert signal is not None
    assert signal.until == datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def test_retry_after_120_is_respected() -> None:
    signal = classify_failure("", "HTTP 429 Too Many Requests\nRetry-After: 120", now=T0)

    assert signal is not None
    assert signal.until == T0 + timedelta(seconds=120)
    assert signal.source == "retry_after"


def test_unreadable_quota_message_is_unknown_never_a_guessed_date() -> None:
    signal = classify_failure("", "quota exceeded, contact your admin", now=T0)

    assert signal is not None
    assert (signal.kind, signal.until, signal.source) == ("quota", None, "unknown")


def test_stream_event_rejected_without_date_is_unknown() -> None:
    signal = classify_failure(_event("rejected", None), "", now=T0)

    assert signal is not None
    assert (signal.kind, signal.until, signal.source) == ("quota", None, "unknown")


def test_stream_event_rejected_reads_epoch_seconds() -> None:
    resets = int((T0 + timedelta(hours=3)).timestamp())

    signal = classify_failure(_event("rejected", resets, rateLimitType="five_hour"), "", now=T0)

    assert signal is not None
    assert signal.until == T0 + timedelta(hours=3)
    assert signal.source == "resets_at"


def test_stream_event_with_a_past_date_is_unknown_not_reopened() -> None:
    signal = classify_failure(_event("rejected", int((T0 - timedelta(hours=1)).timestamp())), "", now=T0)

    assert signal is not None
    assert (signal.until, signal.source) == (None, "unknown")


def test_allowed_stream_event_is_not_a_failure() -> None:
    """Le nom de l'événement contient ``rate_limit`` : il ne doit pas suffire."""
    resets = int((T0 + timedelta(hours=3)).timestamp())

    assert classify_failure(_event("allowed", resets) + '\n{"type":"result"}', "", now=T0) is None


def test_529_is_cooldown_not_suspension(tmp_path: Path) -> None:
    signal = classify_failure("", "API Error: 529 overloaded_error", now=T0)
    assert signal is not None
    assert signal.kind == "overloaded"

    _registry(tmp_path)
    _probed(tmp_path, "anthropic", "local")
    entry = record_failure(tmp_path, "anthropic", signal.kind, now=T0)

    assert entry.suspension is None
    assert entry.cooldown_until == T0 + timedelta(minutes=2)
    assert entry.last_failure == "overloaded"
    assert _ids(tmp_path) == ["local"]
    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(minutes=3))] == ["anthropic", "local"]


def test_exit_0_text_with_quota_or_limit_words_is_not_a_failure() -> None:
    """K8-S1-01 : sur un exit 0, stdout est la réponse du modèle, pas un diagnostic."""
    for answer in ("the quota module is ready", "retry limit reached in the loop I refactored", "too many requests"):
        assert classify_failure(answer, "", now=T0, returncode=0) is None


def test_exit_0_with_a_rejected_stream_event_is_still_a_refusal() -> None:
    signal = classify_failure(_event("rejected", None), "", now=T0, returncode=0)

    assert signal is not None
    assert (signal.kind, signal.source) == ("quota", "unknown")


def test_nonzero_exit_still_reads_the_words() -> None:
    signal = classify_failure("", "usage limit reached", now=T0, returncode=1)

    assert signal is not None
    assert signal.kind == "quota"


def test_529_inside_a_number_is_not_an_overload() -> None:
    """K8-S1-02 : ``duration_ms`` 15290 contient la sous-chaîne 529."""
    assert classify_failure('{"duration_ms":15290}', "", now=T0, returncode=1) is None
    assert classify_failure("", "took 14290 ms", now=T0, returncode=1) is None


def test_529_as_a_status_is_still_an_overload() -> None:
    for text in ("API Error: 529 overloaded_error", '{"type":"error","status":529}', "HTTP 529", "overloaded"):
        signal = classify_failure("", text, now=T0, returncode=1)
        assert signal is not None, text
        assert signal.kind == "overloaded", text


def test_bare_429_stays_a_plain_cooldown() -> None:
    signal = classify_failure("429 rate limit exceeded", "", now=T0)

    assert signal is not None
    assert (signal.kind, signal.until) == ("rate_limit", None)


# ── État et routage ───────────────────────────────────────────────────


def test_suspension_excludes_until_the_date_then_reopens(tmp_path: Path) -> None:
    _registry(tmp_path)
    _probed(tmp_path, "anthropic", "local")
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(hours=5), source="resets_at")

    assert _ids(tmp_path) == ["local"]
    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(hours=5, seconds=1))] == ["anthropic", "local"]
    reloaded = load_state(tmp_path)["anthropic"]
    assert reloaded.suspension is not None
    assert reloaded.suspension.source == "resets_at"
    assert reloaded.cooldown_until is None


def test_unknown_suspension_stays_excluded_until_probe(tmp_path: Path) -> None:
    _registry(tmp_path)
    _probed(tmp_path, "anthropic", "local")
    record_suspension(tmp_path, "anthropic", until=None, source="unknown", now=T0)

    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(hours=2))] == ["local"]
    assert clear_unknown_suspensions(tmp_path) == ("anthropic",)
    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(hours=2))] == ["anthropic", "local"]


def test_unknown_suspension_is_bounded_not_indefinite(tmp_path: Path) -> None:
    """K8-S1-01 : une date illisible ferme quelques heures, pas jusqu'à une sonde jamais lancée."""
    _registry(tmp_path)
    _probed(tmp_path, "anthropic", "local")
    record_suspension(tmp_path, "anthropic", until=None, source="unknown", now=T0)

    suspension = load_state(tmp_path)["anthropic"].suspension
    assert suspension is not None
    assert suspension.source == "unknown"
    assert suspension.until is not None
    assert timedelta(hours=1) <= suspension.until - T0 <= timedelta(hours=12)
    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(hours=1))] == ["local"]
    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(days=30))] == ["anthropic", "local"]


def test_probe_does_not_lift_a_dated_suspension(tmp_path: Path) -> None:
    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(hours=5), source="retry_after")

    assert clear_unknown_suspensions(tmp_path) == ()
    assert load_state(tmp_path)["anthropic"].suspension is not None


def test_intra_tier_skips_a_suspended_model(tmp_path: Path) -> None:
    _registry(tmp_path)
    _probed(tmp_path, "anthropic", "local")
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(days=20), source="resets_at", model="opus")
    provider = next(p for p in read_registry(tmp_path) if p.id == "anthropic")
    now = T0 + timedelta(seconds=1)

    assert [m.id for m in available_models(provider, "mid", load_state(tmp_path), now=now)] == ["sonnet"]
    assert _ids(tmp_path) == ["anthropic", "local"]  # le fournisseur reste, sans le modèle fermé

    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(days=20), source="resets_at", model="sonnet")
    assert _ids(tmp_path) == ["local"]


def test_unprobed_provider_is_excluded_in_autonomous_mode_only(tmp_path: Path) -> None:
    _registry(tmp_path)
    _probed(tmp_path, "local")

    assert _ids(tmp_path) == ["anthropic", "local"]
    assert _ids(tmp_path, autonomous=True) == ["local"]
    chosen = choose(tmp_path, "mid", now=T0, autonomous=True)
    assert chosen is not None
    assert chosen.id == "local"


def test_audit_and_success_keep_the_suspension_fields(tmp_path: Path) -> None:
    from grimoire.providers.audit import audit_providers
    from grimoire.providers.state import record_success

    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(days=1), source="resets_at", model="opus")
    audit_providers(tmp_path, now=T0)
    record_success(tmp_path, "anthropic", "sonnet")

    entry = load_state(tmp_path)["anthropic"]
    assert "opus" in entry.model_suspensions
    record_success(tmp_path, "anthropic", "opus")
    assert load_state(tmp_path)["anthropic"].model_suspensions == {}


def test_backend_rust_rows_match_the_python_filter(tmp_path: Path) -> None:
    """La frontière PyO3 ne voit que (id, ts, bool) : une suspension s'y projette."""
    entry = ProviderRuntimeState(suspension=None)
    assert entry.blocked_row(now=T0) == (None, True)
    record_suspension(tmp_path, "p", until=None, source="unknown", now=T0)
    unknown = load_state(tmp_path)["p"]
    assert unknown.suspension is not None
    assert unknown.suspension.until is not None
    assert unknown.blocked_row(now=T0) == (unknown.suspension.until.timestamp(), True)
    dated = ProviderRuntimeState(cooldown_until=T0 + timedelta(minutes=5))
    assert dated.blocked_row(now=T0)[0] == (T0 + timedelta(minutes=5)).timestamp()


# ── Appel réel d'un fournisseur ────────────────────────────────────────


def _call(tmp_path: Path, body: str) -> tuple[str | None, object]:
    script = tmp_path / "p.py"
    script.write_text(dedent(body), encoding="utf-8")
    registry = _REGISTRY.replace(
        "claude -p {prompt} --model {model}", f"{Path(sys.executable).as_posix()} {script.as_posix()} {{prompt}} {{model}}"
    )
    path = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(registry, encoding="utf-8")
    provider = next(p for p in read_registry(tmp_path) if p.id == "anthropic")
    code, _out, _err, _dur, kind, signal = _run_provider_call(tmp_path, provider, "opus", "go", call_timeout=30)
    assert code is not None
    return kind, signal


def test_successful_call_with_an_allowed_rate_limit_event_is_not_a_failure(tmp_path: Path) -> None:
    resets = int((datetime.now(UTC) + timedelta(hours=3)).timestamp())
    kind, signal = _call(tmp_path, f'print({_event("allowed", resets)!r})\nprint(\'{{"type":"result"}}\')\n')

    assert kind is None
    assert signal is None


def test_call_stating_529_is_overloaded_not_rate_limit(tmp_path: Path) -> None:
    kind, _ = _call(tmp_path, "import sys\nprint('API Error: 529 overloaded_error', file=sys.stderr)\nsys.exit(1)\n")

    assert kind == "overloaded"


def test_successful_call_mentioning_quota_or_limit_is_not_a_failure(tmp_path: Path) -> None:
    """K8-S1-01 : la réponse du modèle parle de quota, l'appel est un succès."""
    for answer in ("the quota check passes", "retry limit reached is now handled"):
        kind, signal = _call(tmp_path, f"print({answer!r})\n")

        assert kind is None, answer
        assert signal is None, answer


def test_successful_call_whose_output_holds_529_is_not_overloaded(tmp_path: Path) -> None:
    """K8-S1-02 : ``"duration_ms":15290`` dans le flux d'un appel sorti en 0."""
    kind, signal = _call(tmp_path, 'print(\'{"type":"result","duration_ms":15290}\')\n')

    assert kind is None
    assert signal is None


def test_failed_call_with_529_inside_a_number_is_a_plain_error(tmp_path: Path) -> None:
    kind, _ = _call(tmp_path, "import sys\nprint('{\"duration_ms\":15290}')\nsys.exit(1)\n")

    assert kind == "error"


def test_observe_window_is_a_positive_lower_bound_or_nothing() -> None:
    zero = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0}}},
        }
    )
    some = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.18, "resetsAt": int((T0 + timedelta(hours=1)).timestamp())},
                    "seven_day": {"utilization": 0.08},
                },
            },
        }
    )

    assert observe_window(zero, now=T0) is None
    found = observe_window(some, now=T0)
    assert found is not None
    assert (found.window, found.utilization) == ("five_hour", 0.18)


# ── Commandes ──────────────────────────────────────────────────────────


def test_providers_quota_is_lower_bound_or_unknown_never_zero(tmp_path: Path) -> None:
    from grimoire.providers.quota import QuotaObservation
    from grimoire.providers.state import record_quota_observation

    _registry(tmp_path)
    record_quota_observation(tmp_path, "anthropic", QuotaObservation("five_hour", 0.18, None, T0))

    result = CliRunner().invoke(app, ["providers", "quota", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    rows = {row["id"]: row for row in json.loads(result.output)["providers"]}
    assert rows["anthropic"]["status"] == "lower_bound"
    assert rows["anthropic"]["used_fraction"] == 0.18
    assert rows["local"] == {"id": "local", "status": "unknown"}


def test_providers_probe_reopens_an_unknown_suspension(tmp_path: Path) -> None:
    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=None, source="unknown")

    result = CliRunner().invoke(app, ["providers", "probe", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["reopened"] == ["anthropic"]
    assert load_state(tmp_path)["anthropic"].suspension is None


# ── Entrées illisibles : « inconnu », jamais une date devinée ──────────────


def test_stream_resets_at_in_milliseconds_is_read_as_the_same_instant() -> None:
    seconds = int((T0 + timedelta(hours=2)).timestamp())

    signal = classify_failure(_event("rejected", seconds * 1000), "", now=T0)

    assert signal is not None
    assert (signal.until, signal.source) == (T0 + timedelta(hours=2), "resets_at")


@pytest.mark.parametrize("resets", ["soon", True, None, 10**18, -(10**18)])
def test_stream_resets_at_unreadable_is_unknown(resets: object) -> None:
    info: dict[str, object] = {"status": "rejected"}
    if resets is not None:
        info["resetsAt"] = resets
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": info})

    signal = classify_failure(line, "", now=T0)

    assert signal is not None
    assert (signal.kind, signal.until, signal.source) == ("quota", None, "unknown")


def test_malformed_stream_line_is_kept_as_text_not_an_event() -> None:
    """Un JSON cassé qui cite l'événement n'est pas un événement : le texte reste lu."""
    broken = '{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"'

    assert classify_failure(broken, "", now=T0, returncode=0) is None
    signal = classify_failure(broken + "\nusage limit reached", "", now=T0, returncode=1)
    assert signal is not None
    assert (signal.kind, signal.source) == ("quota", "unknown")


def test_event_without_info_is_skipped_and_the_next_rejected_one_counts() -> None:
    lines = "\n".join(
        [
            json.dumps({"type": "rate_limit_event", "rate_limit_info": "oops"}),
            _event("allowed", None),
            _event("rejected", None, rateLimitType="seven_day_opus"),
        ]
    )

    signal = classify_failure(lines, "", now=T0)

    assert signal is not None
    assert (signal.kind, signal.model_hint) == ("quota", "opus")


def test_rate_limit_type_without_model_gives_no_hint() -> None:
    for kind in ("five_hour", "seven_day_", 42):
        signal = classify_failure(_event("rejected", None, rateLimitType=kind), "", now=T0)
        assert signal is not None
        assert signal.model_hint is None


def test_resets_in_relative_duration() -> None:
    cases = {"resets in 30 minutes": 1800, "resets in 2 hours": 7200, "resets in 1 day": 86400}
    for message, seconds in cases.items():
        signal = classify_failure("", f"usage limit reached, {message}", now=T0)
        assert signal is not None
        assert (signal.until, signal.source) == (T0 + timedelta(seconds=seconds), "resets_at")


@pytest.mark.parametrize(
    "message", ["resets 30", "resets at 13pm", "resets at 0am", "resets at 25:00", "resets at 9:75"]
)
def test_resets_at_unreadable_hour_is_unknown(message: str) -> None:
    signal = classify_failure("", f"usage limit reached, {message}", now=T0)

    assert signal is not None
    assert (signal.until, signal.source) == (None, "unknown")


def test_retry_after_http_date_is_read_and_a_past_or_naive_one_is_not() -> None:
    future = classify_failure("", "429 Too Many Requests\nRetry-After: Fri, 16 Oct 2026 12:00:00 GMT", now=T0)
    past = classify_failure("", "429 Too Many Requests\nRetry-After: Fri, 16 Oct 2026 08:00:00 GMT", now=T0)
    naive = classify_failure("", "429 Too Many Requests\nRetry-After: Fri, 16 Oct 2026 12:00:00 -0000", now=T0)
    garbage = classify_failure("", "429 Too Many Requests\nRetry-After: next tuesday maybe", now=T0)

    assert future is not None
    assert (future.until, future.source) == (datetime(2026, 10, 16, 12, 0, tzinfo=UTC), "retry_after")
    for signal in (past, naive, garbage):
        assert signal is not None
        assert (signal.kind, signal.until) == ("rate_limit", None)


def test_retry_after_is_capped_to_the_maximum_suspension() -> None:
    signal = classify_failure("", "429 Too Many Requests\nRetry-After: 99999999999", now=T0)

    assert signal is not None
    assert signal.until == T0 + timedelta(days=366)


def test_observe_window_skips_unreadable_windows_and_keeps_the_most_used() -> None:
    line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "bad_shape": "oops",
                    "bool": {"utilization": True},
                    "over": {"utilization": 1.5},
                    "text": {"utilization": "0.4"},
                    "low": {"utilization": 0.1},
                    "high": {"utilization": 0.6},
                }
            },
        }
    )

    found = observe_window(line, now=T0)

    assert found is not None
    assert (found.window, found.utilization) == ("high", 0.6)
    assert (
        observe_window(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": []}}), now=T0)
        is None
    )


# ── État et routage : suspension par modèle contre par fournisseur ──────


def test_model_suspension_projects_on_the_rust_row_without_closing_the_provider(tmp_path: Path) -> None:
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(hours=1), source="resets_at", model="opus", now=T0)
    entry = load_state(tmp_path)["anthropic"]

    assert entry.suspension is None
    assert entry.is_model_suspended("opus", now=T0)
    assert not entry.is_model_suspended("sonnet", now=T0)
    assert entry.blocked_row(now=T0) == (None, True)


def test_provider_suspension_wins_over_a_shorter_cooldown_in_the_row(tmp_path: Path) -> None:
    until = T0 + timedelta(hours=3)
    entry = ProviderRuntimeState(cooldown_until=T0 + timedelta(minutes=5))
    record_suspension(tmp_path, "p", until=until, source="resets_at", now=T0)
    suspended = load_state(tmp_path)["p"]

    assert replace(suspended, cooldown_until=entry.cooldown_until).blocked_row(now=T0) == (until.timestamp(), True)
    legacy = ProviderRuntimeState(suspension=Suspension(until=None, source="unknown"))
    assert legacy.blocked_row(now=T0) == (None, False)
    assert replace(legacy, cooldown_until=T0 + timedelta(minutes=5)).blocked_row(now=T0) == (
        (T0 + timedelta(minutes=5)).timestamp(),
        False,
    )


def test_rust_backend_receives_suspensions_and_unprobed_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.providers import routing

    seen: dict[str, object] = {}

    class FakeCore:
        @staticmethod
        def candidate_provider_ids_py(providers: list, chain: list, cooldowns: list, now_ts: float) -> list[str]:
            seen.update(providers=providers, chain=chain, cooldowns=cooldowns)
            return ["local", "ghost"]

    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(hours=1), source="resets_at", now=T0)
    monkeypatch.setattr(routing, "_use_rust_backend", lambda: True)
    monkeypatch.setattr(routing, "_rust_core", FakeCore)

    result = candidates(tmp_path, "mid", now=T0 + timedelta(seconds=1), autonomous=True)

    assert [p.id for p in result] == ["local"]
    rows = {pid: (ts, ok) for pid, ts, ok in seen["cooldowns"]}  # type: ignore[attr-defined]
    assert rows["anthropic"] == ((T0 + timedelta(hours=1)).timestamp(), False)
    assert rows["local"] == (None, False)


def test_clear_unknown_suspensions_leaves_dated_ones_and_other_providers(tmp_path: Path) -> None:
    record_suspension(tmp_path, "a", until=None, source="unknown", now=T0)
    record_suspension(tmp_path, "a", until=None, source="unknown", model="opus", now=T0)
    record_suspension(tmp_path, "a", until=T0 + timedelta(hours=1), source="resets_at", model="sonnet", now=T0)
    record_suspension(tmp_path, "b", until=None, source="unknown", now=T0)

    cleared = clear_unknown_suspensions(tmp_path, "a")

    assert sorted(cleared) == ["a", "a/opus"]
    state = load_state(tmp_path)
    assert state["a"].suspension is None
    assert set(state["a"].model_suspensions) == {"sonnet"}
    assert state["b"].suspension is not None
    assert clear_unknown_suspensions(tmp_path, "a") == ()


# ── Commandes : sorties texte et erreurs ───────────────────────────────


def _invoke(*args: str) -> object:
    return CliRunner().invoke(app, list(args), env={"COLUMNS": "220"})


def test_providers_status_text_shows_dated_and_unknown_suspensions(tmp_path: Path) -> None:
    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=T0 + timedelta(days=30), source="resets_at")
    record_suspension(tmp_path, "local", until=None, source="unknown")
    state = load_state(tmp_path)
    state["local"] = replace(state["local"], suspension=Suspension(until=None, source="unknown"))
    save_state(tmp_path, state)

    result = _invoke("providers", "status", "--project-root", str(tmp_path))

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "suspendu jusqu'à" in result.output  # type: ignore[attr-defined]
    assert "(resets_at)" in result.output  # type: ignore[attr-defined]


def test_state_label_for_a_suspension_without_date() -> None:
    from grimoire.cli.cmd_providers import _state_label

    entry = ProviderRuntimeState(suspension=Suspension(until=None, source="unknown"))

    assert "date inconnue" in _state_label("x", {"x": entry}, now=T0)


def test_providers_quota_text_table_is_unknown_without_reading(tmp_path: Path) -> None:
    from grimoire.providers.quota import QuotaObservation
    from grimoire.providers.state import record_quota_observation

    _registry(tmp_path)
    record_quota_observation(tmp_path, "anthropic", QuotaObservation("five_hour", 0.18, None, T0))

    result = _invoke("providers", "quota", "--project-root", str(tmp_path))

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "18%" in result.output  # type: ignore[attr-defined]
    assert "unknown" in result.output  # type: ignore[attr-defined]
    assert "0%" not in result.output.replace("18%", "")  # type: ignore[attr-defined]


def test_providers_quota_without_enabled_provider_says_so(tmp_path: Path) -> None:
    _registry(tmp_path)
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(_REGISTRY.replace("enabled: true", "enabled: false"), encoding="utf-8")

    result = _invoke("providers", "quota", "--project-root", str(tmp_path))

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "Aucun fournisseur activé" in result.output  # type: ignore[attr-defined]


def test_providers_quota_and_probe_fail_cleanly_on_a_broken_registry(tmp_path: Path) -> None:
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("providers: [unclosed", encoding="utf-8")

    for command in ("quota", "probe"):
        result = _invoke("providers", command, "--project-root", str(tmp_path))
        assert result.exit_code == 1, result.output  # type: ignore[attr-defined]


def test_providers_probe_text_reports_what_it_reopened(tmp_path: Path) -> None:
    _registry(tmp_path)
    record_suspension(tmp_path, "anthropic", until=None, source="unknown")

    first = _invoke("providers", "probe", "--project-root", str(tmp_path))
    second = _invoke("providers", "probe", "--project-root", str(tmp_path))

    assert "rouverts (date illisible) : anthropic" in first.output  # type: ignore[attr-defined]
    assert "rouverts (date illisible) : aucun" in second.output  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _no_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)
    assert os.environ.get("GRIMOIRE_UNATTENDED") is None
