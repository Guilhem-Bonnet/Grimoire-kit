"""Smoke tests for the legacy framework/tools/preflight-check.py entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "preflight-check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("preflight_check_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_project(project_root: Path) -> Path:
    (project_root / "project-context.yaml").write_text("project:\n  name: smoke\n", encoding="utf-8")
    (project_root / "_grimoire" / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "_memory" / "shared-context.md").write_text(
        "# Shared Context\n",
        encoding="utf-8",
    )
    return project_root


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.build_parser)
    assert callable(legacy_module.run_all_checks)
    assert callable(legacy_module.format_report)


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "pre-flight" in (result.stdout + result.stderr).lower()


def test_minimal_project_avoids_structure_blockers(tmp_path: Path) -> None:
    legacy_module = _load_module()
    project_root = _make_project(tmp_path)
    report = legacy_module.run_all_checks(project_root)
    assert not any(check.name == "structure" and check.is_blocker for check in report.checks)


def test_cli_json_reports_missing_structure(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"]
    assert any(check["name"] == "structure" for check in payload["blockers"])
