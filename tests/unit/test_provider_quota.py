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
    record_suspension(tmp_path, "anthropic", until=None, source="unknown")

    assert [p.id for p in candidates(tmp_path, "mid", now=T0 + timedelta(days=30))] == ["local"]
    assert clear_unknown_suspensions(tmp_path) == ("anthropic",)
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
    record_suspension(tmp_path, "p", until=None, source="unknown")
    unknown = load_state(tmp_path)["p"]
    assert unknown.blocked_row(now=T0) == (None, False)
    dated = ProviderRuntimeState(cooldown_until=T0 + timedelta(minutes=5))
    assert dated.blocked_row(now=T0)[0] == (T0 + timedelta(minutes=5)).timestamp()


# ── Appel réel d'un fournisseur ────────────────────────────────────────


def _call(tmp_path: Path, body: str) -> tuple[str | None, object]:
    script = tmp_path / "p.py"
    script.write_text(dedent(body), encoding="utf-8")
    registry = _REGISTRY.replace(
        "claude -p {prompt} --model {model}", f"{sys.executable} {script} {{prompt}} {{model}}"
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


@pytest.fixture(autouse=True)
def _no_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)
    assert os.environ.get("GRIMOIRE_UNATTENDED") is None
