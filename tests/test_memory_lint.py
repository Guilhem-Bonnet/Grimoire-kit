"""Smoke tests for the legacy framework/tools/memory-lint.py entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "memory-lint.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("memory_lint_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_duplicate_memory(project_root: Path) -> None:
    memory_dir = project_root / "_grimoire" / "_memory"
    learnings_dir = memory_dir / "agent-learnings"
    learnings_dir.mkdir(parents=True, exist_ok=True)
    (learnings_dir / "dev.md").write_text(
        "# Learnings\n- [2026-01-01] adopted ruamel.yaml for project configuration\n",
        encoding="utf-8",
    )
    (memory_dir / "decisions-log.md").write_text(
        "# Decisions\n- [2026-01-02] adopted ruamel.yaml for project configuration\n",
        encoding="utf-8",
    )


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.collect_memory_files)
    assert callable(legacy_module.lint_memory)
    assert callable(legacy_module.report_to_dict)


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "memory lint" in result.stdout.lower()


def test_lint_memory_on_empty_project_is_clean(tmp_path: Path) -> None:
    legacy_module = _load_module()
    report = legacy_module.lint_memory(tmp_path)
    assert report.files_scanned == 0
    assert report.entries_scanned == 0
    assert report.issues == []


def test_cli_json_reports_duplicate_issue(tmp_path: Path) -> None:
    _write_duplicate_memory(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["total"] >= 1
    assert any(issue["category"] == "duplicate" for issue in payload["issues"])
