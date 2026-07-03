"""Smoke tests for the legacy framework/tools/harmony-check.py entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "harmony-check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("harmony_check_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_project(project_root: Path) -> Path:
    (project_root / "project-context.yaml").write_text("project:\n  name: smoke\n", encoding="utf-8")
    (project_root / "_grimoire" / "core" / "agents").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "core" / "workflows").mkdir(parents=True, exist_ok=True)
    (project_root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "core" / "agents" / "analyst.md").write_text("# Analyst\n", encoding="utf-8")
    (project_root / "_grimoire" / "core" / "workflows" / "plan.md").write_text(
        "# Plan\nAgent: analyst\n",
        encoding="utf-8",
    )
    (project_root / "framework" / "tools" / "sample-tool.py").write_text("# tool\n", encoding="utf-8")
    (project_root / "tests" / "test_sample.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    (project_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    return project_root


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.build_parser)
    assert callable(legacy_module.full_analysis)
    assert callable(legacy_module.format_report)
    assert callable(legacy_module.main)


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "harmony" in (result.stdout + result.stderr).lower()


def test_scan_json_reports_counts(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--json", "scan"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["agents"] >= 1
    assert payload["workflows"] >= 1
    assert payload["tools"] >= 1


def test_check_json_reports_score(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--json", "check"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert 0 <= payload["score"] <= 100
    assert payload["grade"]
    assert "dissonances" in payload