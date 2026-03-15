#!/usr/bin/env python3
"""
Tests pour llm-router.py — Routeur LLM intelligent Grimoire (BM-40).

Fonctions testées :
  - TaskClassifier.classify()
  - TaskClassifier._classify_task_type()
  - LLMRouter.route()
  - LLMRouter._select_by_capability()
  - LLMRouter.get_stats()
  - LLMRouter.get_recommendations()
  - load_config()
  - build_router_from_config()
  - _print_classification()
  - _print_decision()
  - main()
"""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "llm-router.py"


def _import_mod():
    """Import le module llm-router via importlib."""
    mod_name = "llm_router"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "LLM_ROUTER_VERSION"))

    def test_version_format(self):
        parts = self.mod.LLM_ROUTER_VERSION.split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.isdigit())

    def test_complexity_levels(self):
        c = self.mod.Complexity
        self.assertEqual(c.TRIVIAL, "trivial")
        self.assertEqual(c.STANDARD, "standard")
        self.assertEqual(c.COMPLEX, "complex")
        self.assertEqual(c.EXPERT, "expert")

    def test_expert_indicators_not_empty(self):
        self.assertGreater(len(self.mod.EXPERT_INDICATORS), 3)

    def test_complex_indicators_not_empty(self):
        self.assertGreater(len(self.mod.COMPLEX_INDICATORS), 3)

    def test_task_type_keywords_has_coding(self):
        self.assertIn("coding", self.mod.TASK_TYPE_KEYWORDS)

    def test_task_type_keywords_has_reasoning(self):
        self.assertIn("reasoning", self.mod.TASK_TYPE_KEYWORDS)

    def test_default_models_not_empty(self):
        self.assertGreater(len(self.mod.DEFAULT_MODELS), 3)


# ── Dataclasses ──────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_model_spec_fields(self):
        field_names = {f.name for f in fields(self.mod.ModelSpec)}
        for expected in ["id", "provider", "api", "cost_per_1m_tokens", "max_tokens", "capabilities"]:
            self.assertIn(expected, field_names)

    def test_model_spec_cost_label_free(self):
        m = self.mod.ModelSpec(id="test", provider="test", cost_per_1m_tokens=0.0)
        self.assertIn("FREE", m.cost_label)

    def test_model_spec_cost_label_cheap(self):
        m = self.mod.ModelSpec(id="test", provider="test", cost_per_1m_tokens=0.5)
        self.assertIn("CHEAP", m.cost_label)

    def test_model_spec_cost_label_moderate(self):
        m = self.mod.ModelSpec(id="test", provider="test", cost_per_1m_tokens=3.0)
        self.assertIn("MODERATE", m.cost_label)

    def test_model_spec_cost_label_premium(self):
        m = self.mod.ModelSpec(id="test", provider="test", cost_per_1m_tokens=15.0)
        self.assertIn("PREMIUM", m.cost_label)

    def test_task_classification_fields(self):
        field_names = {f.name for f in fields(self.mod.TaskClassification)}
        for expected in ["complexity", "task_type", "confidence", "indicators_matched", "prompt_length"]:
            self.assertIn(expected, field_names)

    def test_routing_rule_fields(self):
        field_names = {f.name for f in fields(self.mod.RoutingRule)}
        for expected in ["match_agent", "match_task_type", "match_complexity", "model", "fallback"]:
            self.assertIn(expected, field_names)

    def test_routing_decision_fields(self):
        field_names = {f.name for f in fields(self.mod.RoutingDecision)}
        for expected in ["agent", "selected_model", "fallback_model", "classification", "rule_matched"]:
            self.assertIn(expected, field_names)

    def test_usage_stat_fields(self):
        field_names = {f.name for f in fields(self.mod.UsageStat)}
        for expected in ["model", "request_count", "total_tokens", "estimated_cost"]:
            self.assertIn(expected, field_names)

    def test_dataclass_serializable(self):
        tc = self.mod.TaskClassification(
            complexity="standard", task_type="coding", confidence=0.8,
        )
        d = asdict(tc)
        self.assertEqual(d["complexity"], "standard")
        self.assertIsInstance(json.dumps(d), str)


# ── TaskClassifier ───────────────────────────────────────────────────────────

class TestTaskClassifier(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.classifier = self.mod.TaskClassifier()

    def test_classify_expert_prompt(self):
        result = self.classifier.classify("Design a distributed system with consensus protocol")
        self.assertIn(result.complexity, ["expert", "complex"])

    def test_classify_trivial_prompt(self):
        result = self.classifier.classify("Format this readme")
        self.assertEqual(result.complexity, "trivial")

    def test_classify_coding_prompt(self):
        result = self.classifier.classify("Implement a new endpoint for user registration")
        self.assertIn(result.task_type, ["coding", "general"])

    def test_classify_reasoning_prompt(self):
        result = self.classifier.classify("Why should we use CQRS? Analyze the trade-offs")
        self.assertEqual(result.task_type, "reasoning")

    def test_classify_formatting_prompt(self):
        result = self.classifier.classify("Format this markdown table nicely")
        self.assertEqual(result.task_type, "formatting")

    def test_classify_summarization_prompt(self):
        result = self.classifier.classify("Summarize the key points from this document")
        self.assertEqual(result.task_type, "summarization")

    def test_classify_empty_prompt(self):
        result = self.classifier.classify("")
        # Empty prompt : boost trivial (<100 chars) makes trivial win
        self.assertIn(result.complexity, ["trivial", "standard"])
        self.assertLessEqual(result.confidence, 1.0)

    def test_classify_agent_boost_architect(self):
        # Architect boost should push towards complex
        result = self.classifier.classify("Do this", agent_id="architect")
        self.assertIn(result.complexity, ["complex", "standard"])

    def test_classify_agent_boost_tech_writer(self):
        result = self.classifier.classify("Do this", agent_id="tech-writer")
        self.assertIn(result.complexity, ["trivial", "standard"])

    def test_classify_prompt_length_effect(self):
        short = self.classifier.classify("fix it")
        self.assertLessEqual(short.prompt_length, 10)

    def test_classify_returns_task_classification(self):
        result = self.classifier.classify("test prompt")
        self.assertIsInstance(result, self.mod.TaskClassification)

    def test_classify_confidence_range(self):
        result = self.classifier.classify("Design a distributed system migration strategy")
        self.assertGreaterEqual(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_classify_indicators_matched(self):
        result = self.classifier.classify("Refactor the authentication module design")
        self.assertGreater(len(result.indicators_matched), 0)

    def test_task_type_embedding(self):
        result = self.classifier.classify("Create embeddings and vector similarity search index")
        self.assertEqual(result.task_type, "embedding")

    def test_custom_keywords(self):
        classifier = self.mod.TaskClassifier(custom_expert=["terraform state"])
        result = classifier.classify("Manage the terraform state migration")
        self.assertIn(result.complexity, ["expert", "complex"])


# ── LLMRouter ────────────────────────────────────────────────────────────────

class TestLLMRouter(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.stats_file = self.tmpdir / "stats.jsonl"
        self.router = self.mod.LLMRouter(stats_file=self.stats_file)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_route_returns_decision(self):
        decision = self.router.route("Implement a login page", agent_id="dev")
        self.assertIsInstance(decision, self.mod.RoutingDecision)

    def test_route_contains_model(self):
        decision = self.router.route("Design auth system", agent_id="architect")
        self.assertIn(decision.selected_model, [m.id for m in self.mod.DEFAULT_MODELS])

    def test_route_contains_fallback(self):
        decision = self.router.route("Fix typo", agent_id="dev")
        self.assertIn(decision.fallback_model, [m.id for m in self.mod.DEFAULT_MODELS])

    def test_route_expert_prompt_uses_premium(self):
        decision = self.router.route(
            "Design a distributed consensus protocol with formal verification",
            agent_id="architect",
        )
        # Expert/complex prompts should pick expensive models
        model = next(m for m in self.mod.DEFAULT_MODELS if m.id == decision.selected_model)
        self.assertGreater(model.cost_per_1m_tokens, 1.0)

    def test_route_trivial_prompt_uses_cheap(self):
        decision = self.router.route("Format this readme", agent_id="tech-writer")
        model = next(m for m in self.mod.DEFAULT_MODELS if m.id == decision.selected_model)
        self.assertLess(model.cost_per_1m_tokens, 5.0)

    def test_route_with_agent_rule(self):
        rule = self.mod.RoutingRule(match_agent="architect", model="claude-opus", fallback="claude-sonnet")
        router = self.mod.LLMRouter(rules=[rule], stats_file=self.stats_file)
        decision = router.route("anything", agent_id="architect")
        self.assertEqual(decision.selected_model, "claude-opus")
        self.assertEqual(decision.rule_matched, "agent:architect")

    def test_route_with_task_type_rule(self):
        rule = self.mod.RoutingRule(match_task_type="formatting", model="claude-haiku")
        router = self.mod.LLMRouter(rules=[rule], stats_file=self.stats_file)
        decision = router.route("Format this markdown table nicely", agent_id="dev")
        self.assertEqual(decision.selected_model, "claude-haiku")
        self.assertIn("task_type:", decision.rule_matched)

    def test_route_with_complexity_rule(self):
        rule = self.mod.RoutingRule(match_complexity="expert", model="claude-opus")
        router = self.mod.LLMRouter(rules=[rule], stats_file=self.stats_file)
        decision = router.route(
            "Formal verification of distributed consensus protocol",
            agent_id="architect",
        )
        if decision.classification.complexity == "expert":
            self.assertEqual(decision.selected_model, "claude-opus")

    def test_route_logs_stats(self):
        self.router.route("Test prompt", agent_id="dev")
        self.assertTrue(self.stats_file.exists())
        content = self.stats_file.read_text()
        entry = json.loads(content.strip())
        self.assertIn("model", entry)
        self.assertIn("agent", entry)
        self.assertIn("timestamp", entry)

    def test_get_stats_empty(self):
        router = self.mod.LLMRouter()
        stats = router.get_stats()
        self.assertEqual(stats, [])

    def test_get_stats_after_routing(self):
        self.router.route("First request", agent_id="dev")
        self.router.route("Second request", agent_id="architect")
        stats = self.router.get_stats()
        self.assertGreater(len(stats), 0)
        total = sum(s.request_count for s in stats)
        self.assertEqual(total, 2)

    def test_get_recommendations_empty(self):
        recs = self.router.get_recommendations()
        self.assertGreater(len(recs), 0)

    def test_prompt_summary_truncation(self):
        long_prompt = "x " * 200
        decision = self.router.route(long_prompt, agent_id="dev")
        self.assertLessEqual(len(decision.prompt_summary), 130)

    def test_invalid_model_falls_back(self):
        rule = self.mod.RoutingRule(match_agent="dev", model="nonexistent-model")
        router = self.mod.LLMRouter(rules=[rule], default_model="claude-sonnet")
        decision = router.route("test", agent_id="dev")
        self.assertEqual(decision.selected_model, "claude-sonnet")

    def test_estimated_cost_non_negative(self):
        decision = self.router.route("some prompt", agent_id="dev")
        self.assertGreaterEqual(decision.estimated_cost, 0.0)

    def test_default_model_configurable(self):
        router = self.mod.LLMRouter(default_model="gpt-4o")
        self.assertEqual(router.default_model, "gpt-4o")


# ── Config Loading ───────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_missing_file(self):
        config = self.mod.load_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_build_router_from_config_default(self):
        router = self.mod.build_router_from_config(self.tmpdir)
        self.assertIsInstance(router, self.mod.LLMRouter)
        # Should use default models
        self.assertGreater(len(router.models), 3)

    def test_build_router_from_config_with_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available")

        config = {
            "llm_router": {
                "default_model": "gpt-4o",
                "models": [
                    {"id": "test-model", "provider": "test", "api": "direct",
                     "cost_per_1m_tokens": 1.0, "max_tokens": 100000,
                     "capabilities": ["coding"]},
                ],
                "rules": [
                    {"match": {"agent": "dev"}, "model": "test-model", "fallback": "gpt-4o"},
                ],
            },
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config), encoding="utf-8",
        )
        router = self.mod.build_router_from_config(self.tmpdir)
        self.assertEqual(router.default_model, "gpt-4o")
        self.assertIn("test-model", router.models)
        self.assertEqual(len(router.rules), 1)


# ── CLI Integration ──────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("route", r.stdout.lower())
        self.assertIn("classify", r.stdout.lower())
        self.assertIn("models", r.stdout.lower())

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("llm-router", r.stdout)

    def test_no_args(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1, 2))

    def test_classify_json(self):
        r = self._run("classify", "--prompt", "Fix this typo in readme", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("complexity", data)
        self.assertIn("task_type", data)

    def test_classify_human(self):
        r = self._run("classify", "--prompt", "Design auth system")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Complexity", r.stdout)

    def test_route_json(self):
        r = self._run(
            "--project-root", ".",
            "route", "--agent", "dev",
            "--prompt", "Implement login endpoint",
            "--json",
        )
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("selected_model", data)
        self.assertIn("agent", data)

    def test_models(self):
        r = self._run("models")
        self.assertEqual(r.returncode, 0)
        self.assertIn("claude", r.stdout.lower())

    def test_stats_empty(self):
        r = self._run("--project-root", "/tmp", "stats")
        self.assertEqual(r.returncode, 0)

    def test_config_default(self):
        r = self._run("--project-root", "/tmp", "config")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
