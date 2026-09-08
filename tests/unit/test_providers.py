"""Routage par palier de coût entre fournisseurs LLM (issue #310, lot 2).

Trois garanties tenues ensemble : un registre v1 antérieur au lot (sans
``currency``/``invocation``/``models``) continue de se lire et de vérifier
sans rien changer ; un vocabulaire de ``tier``/``currency`` hors liste est une
erreur, pas un silence ; et surtout, le critère d'arrêt du lot — un
fournisseur qui échoue est écarté du choix jusqu'à l'expiration de son
refroidissement, puis redevient éligible tout seul.
"""

from __future__ import annotations

import http.server
import json
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.missions.gates import _resolve_provider_policy
from grimoire.providers.audit import audit_providers, probe_provider
from grimoire.providers.registry import ProviderRegistryError, read_registry
from grimoire.providers.routing import candidates, choose
from grimoire.providers.state import load_state, record_failure, record_success, save_state

STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"

_V1_REGISTRY_NO_NEW_FIELDS = """\
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
    default_models: ["claude-sonnet-4.6"]
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""

_TIERED_REGISTRY = """\
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
    default_models: ["claude-sonnet-4.6"]
    currency: "quota"
    invocation: "claude -p {prompt} --model {model}"
    models:
      - id: "claude-haiku-4.5"
        tier: "cheap"
      - id: "claude-sonnet-4.6"
        tier: "mid"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: ["local"]
    audit:
      log_prompts: false
      log_metadata: true
  - id: "local"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat", "code"]
    default_models: ["qwen3-coder"]
    currency: "local"
    invocation: "ollama run {model}"
    models:
      - id: "qwen3-coder"
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: []
      retention_notes: "Local execution still requires explicit data classification."
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: ["local"]
  require_capability_match: true
  require_data_policy_match: true
"""


def _write_registry(root: Path, content: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(content, encoding="utf-8")


# ── Compatibilité v1 ──────────────────────────────────────────────────────


def test_v1_registry_without_new_fields_reads_with_empty_optionals(tmp_path: Path) -> None:
    _write_registry(tmp_path, _V1_REGISTRY_NO_NEW_FIELDS)

    providers = read_registry(tmp_path)

    assert len(providers) == 1
    anthropic = providers[0]
    assert anthropic.currency is None
    assert anthropic.invocation is None
    assert anthropic.models == ()


def test_v1_registry_without_new_fields_still_verifies_and_gates(tmp_path: Path) -> None:
    _write_registry(tmp_path, _V1_REGISTRY_NO_NEW_FIELDS)
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: controlled\n", encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert not any(check.id.startswith("providers.currency") for check in result.checks)
    assert not any(check.id.startswith("providers.model_tier") for check in result.checks)
    assert _resolve_provider_policy(tmp_path, task=None, name="provider_policy") is None


def test_choose_finds_nothing_without_tiered_models(tmp_path: Path) -> None:
    _write_registry(tmp_path, _V1_REGISTRY_NO_NEW_FIELDS)

    assert choose(tmp_path, "cheap") is None


# ── Vocabulaire fermé ─────────────────────────────────────────────────────


def test_verify_rejects_unknown_currency(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY.replace('currency: "quota"', 'currency: "crypto"'))
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: controlled\n", encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert any(check.id == "providers.currency_invalid" for check in result.checks)
    assert not result.ok


def test_verify_rejects_unknown_tier(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY.replace('tier: "cheap"', 'tier: "ultra"', 1))
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: controlled\n", encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert any(check.id == "providers.model_tier_invalid" for check in result.checks)
    assert not result.ok


def test_verify_passes_with_valid_tiers_and_currency(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: controlled\n", encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert not any(
        check.id in {"providers.currency_invalid", "providers.model_tier_invalid"} for check in result.checks
    )


def test_read_registry_raises_on_corrupt_yaml(tmp_path: Path) -> None:
    _write_registry(tmp_path, "providers: [this is not: valid: yaml")

    with pytest.raises(ProviderRegistryError):
        read_registry(tmp_path)


def test_read_registry_empty_without_file(tmp_path: Path) -> None:
    assert read_registry(tmp_path) == ()


# ── choose() : ordre et refroidissement ──────────────────────────────────


def test_choose_returns_first_enabled_provider_with_tier(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)

    chosen = choose(tmp_path, "cheap")

    assert chosen is not None
    assert chosen.id == "anthropic"


def test_choose_skips_provider_without_tier_model(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)

    # "mid" n'existe que chez anthropic — local n'a que "cheap".
    chosen = choose(tmp_path, "mid")

    assert chosen is not None
    assert chosen.id == "anthropic"


def test_choose_skips_disabled_provider(tmp_path: Path) -> None:
    _write_registry(
        tmp_path, _TIERED_REGISTRY.replace('id: "anthropic"\n    enabled: true', 'id: "anthropic"\n    enabled: false')
    )

    chosen = choose(tmp_path, "cheap")

    assert chosen is not None
    assert chosen.id == "local"


# ── candidates() : la liste complète, pour une cascade (issue #323) ──────


def test_candidates_rend_tous_les_eligibles_dans_l_ordre_du_registre(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)

    found = candidates(tmp_path, "cheap")

    assert [p.id for p in found] == ["anthropic", "local"]


def test_candidates_est_le_sur_ensemble_dont_choose_rend_le_premier(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)

    found = candidates(tmp_path, "cheap")
    picked = choose(tmp_path, "cheap")

    assert picked is not None
    assert found[0].id == picked.id


def test_candidates_exclut_un_fournisseur_en_refroidissement(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    record_failure(tmp_path, "anthropic", "rate_limit", now=t0)

    found = candidates(tmp_path, "cheap", now=t0 + timedelta(seconds=1))

    assert [p.id for p in found] == ["local"]


# ── Le critère d'arrêt : refroidissement puis reprise automatique ────────


def test_record_failure_removes_provider_from_choice_until_cooldown_expires(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    assert choose(tmp_path, "cheap", now=t0).id == "anthropic"

    record_failure(tmp_path, "anthropic", "rate_limit", now=t0)

    # Anthropic est en refroidissement : le choix bascule sur l'autre fournisseur.
    fallback = choose(tmp_path, "cheap", now=t0 + timedelta(seconds=1))
    assert fallback is not None
    assert fallback.id == "local"

    # Le refroidissement (5 min) a expiré : anthropic redevient éligible sans
    # action supplémentaire — c'est le temps qui rouvre la porte, pas un appel.
    recovered = choose(tmp_path, "cheap", now=t0 + timedelta(minutes=6))
    assert recovered is not None
    assert recovered.id == "anthropic"


def test_record_failure_doubles_cooldown_on_recidivism_capped_at_one_hour(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    first = record_failure(tmp_path, "anthropic", "rate_limit", now=t0)
    assert first.cooldown_until == t0 + timedelta(minutes=5)

    second = record_failure(tmp_path, "anthropic", "rate_limit", now=t0 + timedelta(minutes=6))
    assert second.cooldown_until == t0 + timedelta(minutes=6) + timedelta(minutes=10)

    # Beaucoup de récidives : le refroidissement plafonne à une heure, il ne
    # diverge pas indéfiniment.
    state = load_state(tmp_path)
    state["anthropic"].failure_count = 10
    save_state(tmp_path, state)
    capped = record_failure(tmp_path, "anthropic", "rate_limit", now=t0 + timedelta(hours=1))
    assert capped.cooldown_until == t0 + timedelta(hours=1) + timedelta(hours=1)


def test_record_success_clears_cooldown(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    record_failure(tmp_path, "anthropic", "rate_limit", now=t0)

    record_success(tmp_path, "anthropic")

    assert choose(tmp_path, "cheap", now=t0 + timedelta(seconds=1)).id == "anthropic"
    state = load_state(tmp_path)
    assert state["anthropic"].failure_count == 0
    assert state["anthropic"].cooldown_until is None


def test_state_survives_reload_from_disk(tmp_path: Path) -> None:
    _write_registry(tmp_path, _TIERED_REGISTRY)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    record_failure(tmp_path, "anthropic", "timeout", now=t0)

    reloaded = load_state(tmp_path)
    assert reloaded["anthropic"].last_failure == "timeout"
    assert reloaded["anthropic"].failure_count == 1
    assert reloaded["anthropic"].cooldown_until == t0 + timedelta(minutes=5)


# ── grimoire providers audit (issue #330) : sonder sans dépenser ─────────

_ANTHROPIC_ONLY_REGISTRY = """\
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
    default_models: ["claude-sonnet-4.6"]
    currency: "quota"
    invocation: "claude -p {prompt} --model {model}"
    models:
      - id: "claude-haiku-4.5"
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""

_LOCAL_ONLY_REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "local"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat", "code"]
    default_models: ["qwen3-coder"]
    currency: "local"
    invocation: "ollama run {model}"
    models:
      - id: "qwen3-coder"
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: []
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "local"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""


def _write_fake_executable(bin_dir: Path, name: str, *, marker: Path, version_line: str) -> Path:
    """Un faux CLI : répond à ``--version``, pose *marker* si appelé avec ``-p``.

    ``-p`` est le premier argument de l'invocation réelle du registre
    (``claude -p {prompt} --model {model}``) — si l'audit l'appelait jamais
    en entier, ce marqueur apparaîtrait. Il ne doit jamais apparaître.
    """
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-p" ]; then touch "{marker}"; fi\n'
        f'if [ "$1" = "--version" ]; then echo "{version_line}"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


class _FakeOllamaTagsHandler(http.server.BaseHTTPRequestHandler):
    """Sert ``GET /api/tags`` comme le ferait un vrai serveur Ollama local."""

    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"models": [{"name": "qwen3-coder:latest"}, {"name": "llama3:8b"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def fake_ollama_server() -> object:
    """Un serveur HTTP local factice sur un port libre, répondant à ``/api/tags``."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllamaTagsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_audit_missing_executable_marks_unavailable_and_choose_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(tmp_path, _ANTHROPIC_ONLY_REGISTRY)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    results = audit_providers(tmp_path)

    assert len(results) == 1
    assert results[0].provider_id == "anthropic"
    assert results[0].available is False
    assert "claude" in results[0].probe_note

    state = load_state(tmp_path)
    assert state["anthropic"].available is False
    assert state["anthropic"].probed_at is not None

    # Seul fournisseur du registre, jugé indisponible : plus rien à choisir.
    assert choose(tmp_path, "cheap") is None
    assert candidates(tmp_path, "cheap") == ()


def test_audit_present_executable_marks_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_registry(tmp_path, _ANTHROPIC_ONLY_REGISTRY)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked-with-prompt.marker"
    _write_fake_executable(bin_dir, "claude", marker=marker, version_line="claude-fake 9.9.9")
    monkeypatch.setenv("PATH", str(bin_dir))

    results = audit_providers(tmp_path)

    assert results[0].available is True
    assert "claude-fake 9.9.9" in results[0].probe_note

    chosen = choose(tmp_path, "cheap")
    assert chosen is not None
    assert chosen.id == "anthropic"

    # Le critère d'arrêt de l'issue #330 : aucune sous-commande de l'audit
    # n'a jamais appelé l'invocation complète (`-p {prompt} --model {model}`).
    assert not marker.exists()


def test_audit_never_executes_the_full_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Même test que ci-dessus, isolé : c'est le critère d'arrêt de l'issue."""
    _write_registry(tmp_path, _ANTHROPIC_ONLY_REGISTRY)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked-with-prompt.marker"
    _write_fake_executable(bin_dir, "claude", marker=marker, version_line="claude-fake 1.0.0")
    monkeypatch.setenv("PATH", str(bin_dir))

    probe_provider(read_registry(tmp_path)[0])

    assert not marker.exists()


def test_audit_lists_ollama_models_via_fake_http_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama_server: http.server.HTTPServer
) -> None:
    _write_registry(tmp_path, _LOCAL_ONLY_REGISTRY)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked-with-prompt.marker"
    _write_fake_executable(bin_dir, "ollama", marker=marker, version_line="ollama version fake")
    monkeypatch.setenv("PATH", str(bin_dir))
    port = fake_ollama_server.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")

    results = audit_providers(tmp_path)

    assert results[0].provider_id == "local"
    assert results[0].available is True
    assert set(results[0].models_seen) == {"qwen3-coder:latest", "llama3:8b"}

    state = load_state(tmp_path)
    assert set(state["local"].models_seen) == {"qwen3-coder:latest", "llama3:8b"}
    assert not marker.exists()


def test_audit_never_activates_or_disables_a_provider_in_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'audit écrit l'état runtime, jamais le registre — `enabled` n'est pas touché."""
    _write_registry(tmp_path, _ANTHROPIC_ONLY_REGISTRY)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    registry_before = (tmp_path / REGISTRY_PATH).read_text(encoding="utf-8")

    audit_providers(tmp_path)

    registry_after = (tmp_path / REGISTRY_PATH).read_text(encoding="utf-8")
    assert registry_after == registry_before
    assert read_registry(tmp_path)[0].enabled is True
