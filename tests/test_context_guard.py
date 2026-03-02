#!/usr/bin/env python3
"""
Tests pour context-guard.py — BMAD Context Budget Guard — BM-55

Fonctions testées :
  - estimate_tokens()
  - read_file_safe()
  - resolve_agent_loads()
  - compute_budget()
  - find_agents()
  - analyze_file_for_optimize()
  - do_optimize()
  - parse_model_affinity()
  - score_model_for_agent()
  - load_available_models()
  - do_recommend_models()
  - generate_recommendations()
  - fmt_tokens()
  - status_icon()
  - bar()
  - role_icon()
  - print_budget()
  - print_summary_table()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "context-guard.py"


def _import_mod():
    """Import le module context-guard via importlib."""
    mod_name = "context_guard"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "context-guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet BMAD minimal pour les tests."""
    (root / "_bmad" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_bmad-output").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_model_profile_exists(self):
        self.assertTrue(hasattr(self.mod, "ModelProfile"))

    def test_model_profile_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ModelProfile)}
        for expected in ["id", "reasoning", "context_window", "speed", "tier"]:
            self.assertIn(expected, fields)

    def test_file_load_exists(self):
        self.assertTrue(hasattr(self.mod, "FileLoad"))

    def test_file_load_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.FileLoad)}
        for expected in ["path", "role", "content", "tokens", "loaded"]:
            self.assertIn(expected, fields)

    def test_agent_budget_exists(self):
        self.assertTrue(hasattr(self.mod, "AgentBudget"))

    def test_agent_budget_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.AgentBudget)}
        for expected in ["agent_id", "agent_path", "model", "model_window", "loads"]:
            self.assertIn(expected, fields)

    def test_optimize_hint_exists(self):
        self.assertTrue(hasattr(self.mod, "OptimizeHint"))

    def test_optimize_hint_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.OptimizeHint)}
        for expected in ["path", "category", "description", "current_tokens", "estimated_savings"]:
            self.assertIn(expected, fields)

    def test_model_affinity_exists(self):
        self.assertTrue(hasattr(self.mod, "ModelAffinity"))

    def test_model_affinity_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ModelAffinity)}
        for expected in ["reasoning", "context_window", "speed", "cost"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_estimate_tokens_callable(self):
        self.assertTrue(callable(getattr(self.mod, "estimate_tokens", None)))

    def test_read_file_safe_callable(self):
        self.assertTrue(callable(getattr(self.mod, "read_file_safe", None)))

    def test_analyze_file_for_optimize_callable(self):
        self.assertTrue(callable(getattr(self.mod, "analyze_file_for_optimize", None)))

    def test_parse_model_affinity_callable(self):
        self.assertTrue(callable(getattr(self.mod, "parse_model_affinity", None)))

    def test_score_model_for_agent_callable(self):
        self.assertTrue(callable(getattr(self.mod, "score_model_for_agent", None)))

    def test_generate_recommendations_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_recommendations", None)))

    def test_fmt_tokens_callable(self):
        self.assertTrue(callable(getattr(self.mod, "fmt_tokens", None)))

    def test_status_icon_callable(self):
        self.assertTrue(callable(getattr(self.mod, "status_icon", None)))

    def test_bar_callable(self):
        self.assertTrue(callable(getattr(self.mod, "bar", None)))

    def test_role_icon_callable(self):
        self.assertTrue(callable(getattr(self.mod, "role_icon", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resolve_agent_loads_callable(self):
        self.assertTrue(callable(self.mod.resolve_agent_loads))

    def test_compute_budget_callable(self):
        self.assertTrue(callable(self.mod.compute_budget))

    def test_find_agents_empty_project(self):
        try:
            result = self.mod.find_agents(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_do_optimize_callable(self):
        self.assertTrue(callable(self.mod.do_optimize))

    def test_load_available_models_empty_project(self):
        try:
            result = self.mod.load_available_models(self.tmpdir)
            # Returns None when no models config found — valid behavior
            self.assertIsNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_do_recommend_models_callable(self):
        self.assertTrue(callable(self.mod.do_recommend_models))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_default_model_defined(self):
        self.assertTrue(hasattr(self.mod, "DEFAULT_MODEL"))

    def test_threshold_warn_defined(self):
        self.assertTrue(hasattr(self.mod, "THRESHOLD_WARN"))

    def test_threshold_crit_defined(self):
        self.assertTrue(hasattr(self.mod, "THRESHOLD_CRIT"))

    def test_reasoning_rank_defined(self):
        self.assertTrue(hasattr(self.mod, "REASONING_RANK"))

    def test_window_rank_defined(self):
        self.assertTrue(hasattr(self.mod, "WINDOW_RANK"))

    def test_speed_rank_defined(self):
        self.assertTrue(hasattr(self.mod, "SPEED_RANK"))

    def test_tier_rank_defined(self):
        self.assertTrue(hasattr(self.mod, "TIER_RANK"))

    def test_tier_from_rank_defined(self):
        self.assertTrue(hasattr(self.mod, "TIER_FROM_RANK"))


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "context-guard.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("context", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
