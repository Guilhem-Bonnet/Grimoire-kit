"""Smoke tests for the legacy framework/tools/context-router.py entrypoint."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "context-router.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("context_router_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_project(project_root: Path) -> Path:
    custom_dir = project_root / "_grimoire" / "_config" / "custom"
    memory_dir = project_root / "_grimoire" / "_memory"
    (memory_dir / "agent-learnings").mkdir(parents=True, exist_ok=True)
    custom_dir.mkdir(parents=True, exist_ok=True)

    (project_root / "project-context.yaml").write_text("project:\n  name: smoke\n", encoding="utf-8")
    (custom_dir / "agent-base.md").write_text("# Agent Base\nRules here.\n", encoding="utf-8")
    (custom_dir / "analyst.md").write_text("name: analyst\n# Analyst\nAnalysis agent.\n", encoding="utf-8")
    (memory_dir / "shared-context.md").write_text("# Shared Context\n", encoding="utf-8")
    (memory_dir / "decisions-log.md").write_text("# Decisions\n- Use Python\n", encoding="utf-8")
    (memory_dir / "agent-learnings" / "analyst.md").write_text(
        "# Analyst Learnings\n- Prefer concise plans\n",
        encoding="utf-8",
    )
    return project_root


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.find_agent_files)
    assert callable(legacy_module.discover_context_files)
    assert callable(legacy_module.calculate_plan)
    assert callable(legacy_module.build_parser)


def test_calculate_plan_keeps_core_context(tmp_path: Path) -> None:
    legacy_module = _load_module()
    project_root = _make_project(tmp_path)
    plan = legacy_module.calculate_plan(project_root, "analyst", model="gpt-4o", task_query="decisions")

    assert plan.agent == "analyst"
    assert plan.model == "gpt-4o"
    assert plan.loaded_tokens >= 1
    assert any(entry.loaded and entry.path.endswith("agent-base.md") for entry in plan.entries)
    assert any(entry.path.endswith("shared-context.md") for entry in plan.entries)


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
    assert "budget" in output


def test_budget_command_reports_agent(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "budget"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Context Budget Report" in result.stdout
    assert "analyst" in result.stdout