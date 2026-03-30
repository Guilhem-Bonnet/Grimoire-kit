"""Tests for `grimoire workflows list` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_prompt(dir_path: Path, slug: str) -> None:
    file_path = dir_path / f"{slug}.prompt.md"
    file_path.write_text(f"# {slug}\n", encoding="utf-8")


def test_workflows_list_reads_project_prompts(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-status")
    _write_prompt(prompts, "grimoire-health-check")

    result = runner.invoke(app, ["workflows", "list", str(project)])

    assert result.exit_code == 0
    assert "/grimoire-status" in result.output
    assert "/grimoire-health-check" in result.output


def test_workflows_list_json_output(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-pre-push")

    result = runner.invoke(app, ["-o", "json", "workflows", "list", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] >= 1
    assert any(
        wf["command"] == "/grimoire-pre-push" and wf["source"] == "project"
        for wf in data["workflows"]
    )


def test_workflows_list_falls_back_to_framework(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fake-framework"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-session-bootstrap")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    project = tmp_path / "empty-project"
    project.mkdir()

    result = runner.invoke(app, ["workflows", "list", str(project)])

    assert result.exit_code == 0
    assert "/grimoire-session-bootstrap" in result.output
    assert "framework" in result.output


def test_workflows_list_prefers_project_over_framework_duplicate(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    _write_prompt(project_prompts, "grimoire-status")

    fake_fw = tmp_path / "fake-fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "list", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["workflows"][0]["source"] == "project"


def test_workflows_doctor_passes_when_project_matches_framework(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")
    _write_prompt(fw_prompts, "grimoire-health-check")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    _write_prompt(project_prompts, "grimoire-status")
    _write_prompt(project_prompts, "grimoire-health-check")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "doctor", str(project)])

    assert result.exit_code == 0
    assert "Workflow audit passed" in result.output


def test_workflows_doctor_fails_on_missing_or_modified(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")
    _write_prompt(fw_prompts, "grimoire-health-check")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    # Missing grimoire-health-check, and modified grimoire-status
    (project_prompts / "grimoire-status.prompt.md").write_text("# custom\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "doctor", str(project)])

    assert result.exit_code == 1
    assert "Missing workflows" in result.output
    assert "Modified workflows" in result.output


def test_workflows_doctor_json_output(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    _write_prompt(project_prompts, "grimoire-status")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "doctor", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["counts"]["missing"] == 0
    assert data["counts"]["modified"] == 0


def test_workflows_doctor_strict_fails_on_extra(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    _write_prompt(project_prompts, "grimoire-status")
    _write_prompt(project_prompts, "custom-flow")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "doctor", str(project), "--strict"])

    assert result.exit_code == 1
    assert "Extra workflows" in result.output


def test_workflows_sync_copies_missing_prompts(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")
    _write_prompt(fw_prompts, "grimoire-health-check")

    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "sync", str(project)])

    assert result.exit_code == 0
    assert (project / ".github" / "prompts" / "grimoire-status.prompt.md").is_file()
    assert (project / ".github" / "prompts" / "grimoire-health-check.prompt.md").is_file()


def test_workflows_sync_dry_run_does_not_write(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "sync", str(project), "--dry-run"])

    assert result.exit_code == 0
    assert "copy grimoire-status.prompt.md" in result.output
    assert not (project / ".github" / "prompts" / "grimoire-status.prompt.md").exists()


def test_workflows_sync_overwrite_updates_modified_prompt(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\n", encoding="utf-8")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# project\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "sync", str(project), "--overwrite"])

    assert result.exit_code == 0
    assert (project_prompts / "grimoire-status.prompt.md").read_text(encoding="utf-8") == "# framework\n"


def test_workflows_sync_json_reports_skipped_modified(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\n", encoding="utf-8")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# local\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "sync", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert {item["action"] for item in data["actions"]} == {"skip-modified"}


def test_workflows_diff_shows_modified_workflow(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\nbase\n", encoding="utf-8")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# framework\ncustom\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "diff", str(project)])

    assert result.exit_code == 0
    assert "grimoire-status.prompt.md" in result.output
    assert "framework/grimoire-status.prompt.md" in result.output
    assert "project/grimoire-status.prompt.md" in result.output


def test_workflows_diff_json_output_for_specific_workflow(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\nbase\n", encoding="utf-8")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# framework\nlocal\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "diff", str(project), "grimoire-status"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["diffs"][0]["slug"] == "grimoire-status"
    assert any(line.startswith("---") for line in data["diffs"][0]["diff"])


def test_workflows_diff_reports_no_differences(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# same\n", encoding="utf-8")

    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# same\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "diff", str(project)])

    assert result.exit_code == 0
    assert "No workflow differences found" in result.output


def test_workflows_show_prefers_project_version(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# project version\n", encoding="utf-8")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework version\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "show", "grimoire-status", str(project)])

    assert result.exit_code == 0
    assert "Source: project" in result.output
    assert "# project version" in result.output


def test_workflows_show_falls_back_to_framework(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-session-bootstrap.prompt.md").write_text("# framework bootstrap\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "show", "grimoire-session-bootstrap", str(project)])

    assert result.exit_code == 0
    assert "Source: framework" in result.output
    assert "# framework bootstrap" in result.output


def test_workflows_show_json_output(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-pre-push.prompt.md").write_text("# pre push\n", encoding="utf-8")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "show", "grimoire-pre-push", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["slug"] == "grimoire-pre-push"
    assert data["source"] == "project"
    assert data["content"] == "# pre push\n"


def test_workflows_install_copies_single_framework_workflow(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework status\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "install", "grimoire-status", str(project)])

    assert result.exit_code == 0
    assert (project / ".github" / "prompts" / "grimoire-status.prompt.md").read_text(encoding="utf-8") == "# framework status\n"


def test_workflows_install_dry_run_does_not_write(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework status\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "install", "grimoire-status", str(project), "--dry-run"])

    assert result.exit_code == 0
    assert "install grimoire-status.prompt.md" in result.output
    assert not (project / ".github" / "prompts" / "grimoire-status.prompt.md").exists()


def test_workflows_install_skips_existing_without_overwrite(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# local\n", encoding="utf-8")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "install", "grimoire-status", str(project)])

    assert result.exit_code == 0
    assert "skip existing" in result.output
    assert (project_prompts / "grimoire-status.prompt.md").read_text(encoding="utf-8") == "# local\n"


def test_workflows_install_json_overwrite(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project_prompts = project / ".github" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "grimoire-status.prompt.md").write_text("# local\n", encoding="utf-8")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    (fw_prompts / "grimoire-status.prompt.md").write_text("# framework\n", encoding="utf-8")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "install", "grimoire-status", str(project), "--overwrite"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["action"] == "overwrite"
    assert data["workflow"] == "grimoire-status"
    assert (project_prompts / "grimoire-status.prompt.md").read_text(encoding="utf-8") == "# framework\n"


def test_workflows_prune_removes_extra_project_workflows(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-status")
    _write_prompt(prompts, "custom-only")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "prune", str(project)])

    assert result.exit_code == 0
    assert (prompts / "grimoire-status.prompt.md").is_file()
    assert not (prompts / "custom-only.prompt.md").exists()


def test_workflows_prune_dry_run_keeps_files(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-status")
    _write_prompt(prompts, "custom-only")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)
    _write_prompt(fw_prompts, "grimoire-status")

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "prune", str(project), "--dry-run"])

    assert result.exit_code == 0
    assert "delete custom-only.prompt.md" in result.output
    assert (prompts / "custom-only.prompt.md").is_file()


def test_workflows_prune_json_output(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "custom-only")

    fake_fw = tmp_path / "fw"
    fw_prompts = fake_fw / "copilot" / "prompts"
    fw_prompts.mkdir(parents=True)

    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "prune", str(project), "--dry-run"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["actions"][0]["action"] == "delete"
    assert data["actions"][0]["file"] == "custom-only.prompt.md"


def test_workflows_search_matches_slug(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-status")
    _write_prompt(prompts, "grimoire-health-check")

    fake_fw = tmp_path / "fw"
    (fake_fw / "copilot" / "prompts").mkdir(parents=True)
    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "search", "status", str(project)])

    assert result.exit_code == 0
    assert "/grimoire-status" in result.output


def test_workflows_search_matches_content_when_enabled(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "grimoire-status.prompt.md").write_text("# title\nincident response workflow\n", encoding="utf-8")

    fake_fw = tmp_path / "fw"
    (fake_fw / "copilot" / "prompts").mkdir(parents=True)
    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["workflows", "search", "incident", str(project), "--content"])

    assert result.exit_code == 0
    assert "/grimoire-status" in result.output


def test_workflows_search_json_output(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    prompts = project / ".github" / "prompts"
    prompts.mkdir(parents=True)
    _write_prompt(prompts, "grimoire-status")

    fake_fw = tmp_path / "fw"
    (fake_fw / "copilot" / "prompts").mkdir(parents=True)
    monkeypatch.setattr("grimoire.cli.app.framework_path", lambda: fake_fw)

    result = runner.invoke(app, ["-o", "json", "workflows", "search", "status", str(project)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["results"][0]["slug"] == "grimoire-status"
