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


# ── `grimoire providers history` (issue #312, lot 4) ──────────────────────


def _fabrique_couple_escalade(ledger_path: Path, task_type: str, verifiability: str, taux: float, n: int) -> None:
    """*n* dispatchs fabriqués pour un couple, une part *taux* escaladant depuis cheap."""
    from grimoire.missions.ledger import MissionLedger

    ledger = MissionLedger(ledger_path)
    escalades = round(taux * n)
    for i in range(n):
        tiers = ("cheap", "mid") if i < escalades else ("cheap",)
        task_id = f"GAO-{task_type}-{i:03d}"
        for attempt, tier in enumerate(tiers, start=1):
            ledger.append_event(
                "task.dispatched",
                task_id,
                "task",
                "test",
                {
                    "task_id": task_id,
                    "attempt": attempt,
                    "tier": tier,
                    "provider": f"{tier}-provider",
                    "model": f"{tier}-model",
                    "verdict": "green" if attempt == len(tiers) else "red",
                    "checks": [],
                    "cost_usd": None,
                    "task_type": task_type,
                    "verifiability": verifiability,
                    "start_tier": tiers[0],
                    "start_tier_reason": "test fabriqué",
                },
            )


def test_history_sans_ledger_ne_plante_pas(tmp_path: Path) -> None:
    result = runner.invoke(app, ["providers", "history", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout


def test_history_json_est_valide_et_recommande_mid(tmp_path: Path) -> None:
    ledger_path = tmp_path / "_grimoire-runtime-output" / "ledger"
    _fabrique_couple_escalade(ledger_path, "implementation", "V0", 0.6, 5)

    result = runner.invoke(app, ["providers", "history", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert len(payload["couples"]) == 1
    couple = payload["couples"][0]
    assert couple["task_type"] == "implementation"
    assert couple["verifiability"] == "V0"
    assert couple["observations"] == 5
    assert couple["recommended_start_tier"] == "mid"
    assert couple["by_start_tier"]["cheap"]["escalations"] == 3


def test_history_texte_liste_le_couple_et_le_depart_recommande(tmp_path: Path) -> None:
    ledger_path = tmp_path / "_grimoire-runtime-output" / "ledger"
    _fabrique_couple_escalade(ledger_path, "documentation", "V0", 0.0, 5)

    result = runner.invoke(app, ["providers", "history", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "documentation" in result.stdout
    assert "V0" in result.stdout
