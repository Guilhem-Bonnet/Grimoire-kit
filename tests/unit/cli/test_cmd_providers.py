"""`grimoire providers status` / `cooldown` (issue #310, lot 2).

La CLI n'est qu'une vue sur ``grimoire.providers`` — ces tests vérifient
qu'elle affiche quelque chose de lisible en texte, qu'elle rend un JSON
valide et fidèle sous ``--json``, et que ``cooldown`` retire vraiment le
fournisseur du prochain choix.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()
STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"

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


def _project(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / REGISTRY_PATH).write_text(_REGISTRY, encoding="utf-8")
    return tmp_path


def test_status_text_output_lists_providers_and_next_choice(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = runner.invoke(app, ["providers", "status", "--project-root", str(root)])

    assert result.exit_code == 0, result.stdout
    assert "anthropic" in result.stdout
    assert "local" in result.stdout
    assert "Prochain choix" in result.stdout


def test_status_json_output_is_valid_and_matches_registry(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = runner.invoke(app, ["providers", "status", "--project-root", str(root), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    ids = {p["id"] for p in payload["providers"]}
    assert ids == {"anthropic", "local"}
    assert payload["next_choice"]["cheap"] == "anthropic"


def test_cooldown_removes_provider_from_next_choice(tmp_path: Path) -> None:
    root = _project(tmp_path)

    cooldown_result = runner.invoke(
        app, ["providers", "cooldown", "anthropic", "--project-root", str(root), "--reason", "rate_limit"]
    )
    assert cooldown_result.exit_code == 0, cooldown_result.stdout
    assert "refroidi" in cooldown_result.stdout

    status_result = runner.invoke(app, ["providers", "status", "--project-root", str(root), "--json"])
    payload = json.loads(status_result.stdout)

    assert payload["next_choice"]["cheap"] == "local"
    anthropic = next(p for p in payload["providers"] if p["id"] == "anthropic")
    assert anthropic["cooling_down"] is True


def test_status_on_project_without_registry_does_not_crash(tmp_path: Path) -> None:
    result = runner.invoke(app, ["providers", "status", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout


# ── `grimoire providers audit` (issue #330) ───────────────────────────────


def test_audit_json_marks_missing_executable_unavailable(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # `anthropic` (claude) reste absent du PATH ; `ollama` (local) y est —
    # seul anthropic doit basculer `available: false`.
    fake_ollama = bin_dir / "ollama"
    fake_ollama.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_ollama.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    result = runner.invoke(app, ["providers", "audit", "--project-root", str(root), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    anthropic = next(p for p in payload["providers"] if p["id"] == "anthropic")
    local = next(p for p in payload["providers"] if p["id"] == "local")
    assert anthropic["available"] is False
    assert local["available"] is True

    status_result = runner.invoke(app, ["providers", "status", "--project-root", str(root), "--json"])
    status_payload = json.loads(status_result.stdout)
    anthropic_status = next(p for p in status_payload["providers"] if p["id"] == "anthropic")
    assert anthropic_status["available"] is False
    assert anthropic_status["probed_at"] is not None
    # anthropic est désormais indisponible : `local` prend sa place.
    assert status_payload["next_choice"]["cheap"] == "local"


def test_audit_on_project_without_registry_does_not_crash(tmp_path: Path) -> None:
    result = runner.invoke(app, ["providers", "audit", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
