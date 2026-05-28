from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import (
    STANDARD_PROFILE_FILE,
    list_profiles,
    setup_standard_profile,
    verify_standard_profile,
)


def test_profiles_include_orchestrated() -> None:
    profiles = {profile.id for profile in list_profiles()}
    assert {"starter", "controlled", "orchestrated", "governed", "production"} <= profiles


def test_setup_generates_orchestrated_artifacts(tmp_path: Path) -> None:
    result = setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")

    assert result.profile == "orchestrated"
    assert (tmp_path / STANDARD_PROFILE_FILE).is_file()
    assert (tmp_path / "_grimoire/standard/mission-brief.md").is_file()
    assert (tmp_path / "_grimoire/standard/knowledge-source-registry.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/llm-provider-registry.yaml").is_file()
    assert (tmp_path / "_grimoire-output/evidence/bootstrap/task-envelope.md").is_file()
    assert (tmp_path / "_grimoire-output/evidence/bootstrap/evidence-pack.md").is_file()

    mission = (tmp_path / "_grimoire/standard/mission-brief.md").read_text(encoding="utf-8")
    assert "- Project: Demo" in mission
    assert "- Selected profile: `orchestrated`" in mission


def test_verify_detects_missing_artifacts(tmp_path: Path) -> None:
    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert not result.ok
    assert STANDARD_PROFILE_FILE in result.missing


def test_verify_passes_after_setup(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled")

    result = verify_standard_profile(tmp_path)

    assert result.ok
    assert result.profile == "controlled"
    assert result.warning_count > 0
    assert any(check.id == "providers.none_enabled" for check in result.checks)


def test_orchestrated_verify_reports_knowledge_placeholder(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated")

    result = verify_standard_profile(tmp_path)

    assert result.ok
    assert any(check.id == "knowledge.no_real_source" for check in result.checks)


def test_verify_fails_on_manifest_profile_mismatch(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter")
    manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("profile: starter", "profile: production"), encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="starter")

    assert not result.ok
    assert any(check.id == "manifest.profile_mismatch" for check in result.checks)


def test_cli_standard_init_and_verify_json(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "init",
        str(tmp_path),
        "--profile",
        "starter",
    ])
    assert init_result.exit_code == 0
    init_data = json.loads(init_result.output)
    assert init_data["ok"] is True
    assert init_data["profile"] == "starter"

    verify_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "verify",
        str(tmp_path),
    ])
    assert verify_result.exit_code == 0
    verify_data = json.loads(verify_result.output)
    assert verify_data["ok"] is True
    assert verify_data["profile"] == "starter"
    assert "checks" in verify_data


def test_cli_standard_audit_markdown(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["standard", "init", str(tmp_path), "--profile", "orchestrated"])

    result = runner.invoke(app, [
        "standard",
        "audit",
        str(tmp_path),
        "--profile",
        "orchestrated",
        "--markdown",
    ])

    assert result.exit_code == 0
    assert "# Agentic Standard Audit" in result.output
    assert "knowledge.no_real_source" in result.output
