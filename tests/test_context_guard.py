"""Smoke tests for the legacy framework/tools/context-guard.py entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "context-guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("context_guard_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_project(project_root: Path) -> Path:
    (project_root / "project-context.yaml").write_text("project:\n  name: smoke\n", encoding="utf-8")
    (project_root / "_grimoire" / "_config" / "custom" / "agents").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (project_root / "_grimoire" / "_config" / "custom" / "agents" / "analyst.md").write_text(
        "# Analyst\n\n<activation>budget</activation>\n",
        encoding="utf-8",
    )
    (project_root / "_grimoire" / "_memory" / "shared-context.md").write_text(
        "# Shared Context\n",
        encoding="utf-8",
    )
    return project_root


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.resolve_agent_loads)
    assert callable(legacy_module.compute_budget)
    assert callable(legacy_module.find_agents)
    assert callable(legacy_module.main)


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    output = (result.stdout + result.stderr).lower()
    assert "context" in output
    assert "recommend-models" in output


def test_list_models_includes_default_model() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--list-models"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "copilot" in result.stdout.lower()


def test_cli_json_reports_agent_budget(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["model"]
    assert payload["agents"]
    assert payload["agents"][0]["id"] == "analyst"
    assert payload["agents"][0]["total_tokens"] >= 1