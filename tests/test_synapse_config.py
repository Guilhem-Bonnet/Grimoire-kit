"""Tests for synapse-config.py — Configuration centralisée Synapse (Story 7.5)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

# ── Load module via importlib ────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent.parent / "framework" / "tools"


def _load_module(name: str):
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module("synapse-config")


class TestSynapseConfigDataClasses(unittest.TestCase):
    """Test default values and data class construction."""

    def test_default_config(self):
        cfg = mod.SynapseConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.version, mod.SYNAPSE_CONFIG_VERSION)
        self.assertIsInstance(cfg.trace, mod.TraceConfig)

    def test_trace_defaults(self):
        tc = mod.TraceConfig()
        self.assertTrue(tc.enabled)
        self.assertEqual(tc.output, "_grimoire-output/Grimoire_TRACE.md")
        self.assertTrue(tc.include_tokens)
        self.assertEqual(tc.max_entries, 10000)

    def test_llm_router_defaults(self):
        lr = mod.LLMRouterConfig()
        self.assertEqual(lr.default_model, "claude-sonnet-4-20250514")
        self.assertIsInstance(lr.fallback_chain, list)
        self.assertGreater(lr.budget_per_session, 0)

    def test_rag_defaults(self):
        r = mod.RAGConfig()
        self.assertEqual(r.chunk_size, 512)
        self.assertEqual(r.chunk_overlap, 50)
        self.assertGreater(r.top_k, 0)

    def test_token_budget_defaults(self):
        tb = mod.TokenBudgetConfig()
        self.assertLess(tb.warning_threshold, tb.auto_summarize_threshold)
        self.assertLess(tb.auto_summarize_threshold, tb.critical_threshold)
        self.assertEqual(tb.counter, "auto")

    def test_semantic_cache_defaults(self):
        sc = mod.SemanticCacheConfig()
        self.assertGreater(sc.similarity_threshold, 0)
        self.assertGreater(sc.ttl_hours, 0)

    def test_message_bus_defaults(self):
        mb = mod.MessageBusConfig()
        self.assertEqual(mb.backend, "in-process")
        self.assertIn("redis", mb.redis_url)

    def test_orchestrator_defaults(self):
        oc = mod.OrchestratorConfig()
        self.assertEqual(oc.default_mode, "auto")
        self.assertGreater(oc.budget_cap, 0)
        self.assertGreater(oc.max_concurrent, 0)

    def test_to_dict(self):
        cfg = mod.SynapseConfig()
        d = cfg.to_dict()
        self.assertIn("enabled", d)
        self.assertIn("trace", d)
        self.assertIn("llm_router", d)
        self.assertIn("rag", d)
        self.assertIn("token_budget", d)
        self.assertIn("semantic_cache", d)
        self.assertIn("message_bus", d)
        self.assertIn("orchestrator", d)


class TestSynapseConfigFromDict(unittest.TestCase):
    """Test from_dict parsing."""

    def test_empty_dict(self):
        cfg = mod.SynapseConfig.from_dict({})
        self.assertTrue(cfg.enabled)

    def test_partial_dict(self):
        cfg = mod.SynapseConfig.from_dict({"enabled": False})
        self.assertFalse(cfg.enabled)
        # Others should be defaults
        self.assertTrue(cfg.trace.enabled)

    def test_nested_override(self):
        cfg = mod.SynapseConfig.from_dict({
            "trace": {"enabled": False, "max_entries": 500},
        })
        self.assertFalse(cfg.trace.enabled)
        self.assertEqual(cfg.trace.max_entries, 500)
        # Other trace fields intact
        self.assertTrue(cfg.trace.include_tokens)

    def test_all_sections(self):
        data = {
            "enabled": True,
            "trace": {"enabled": True},
            "llm_router": {"default_model": "gpt-4o"},
            "rag": {"chunk_size": 1024},
            "token_budget": {"warning_threshold": 0.5},
            "semantic_cache": {"ttl_hours": 48},
            "message_bus": {"backend": "redis"},
            "orchestrator": {"budget_cap": 100000},
        }
        cfg = mod.SynapseConfig.from_dict(data)
        self.assertEqual(cfg.llm_router.default_model, "gpt-4o")
        self.assertEqual(cfg.rag.chunk_size, 1024)
        self.assertEqual(cfg.token_budget.warning_threshold, 0.5)
        self.assertEqual(cfg.semantic_cache.ttl_hours, 48)
        self.assertEqual(cfg.message_bus.backend, "redis")
        self.assertEqual(cfg.orchestrator.budget_cap, 100000)

    def test_non_dict_input(self):
        cfg = mod.SynapseConfig.from_dict("not a dict")
        self.assertTrue(cfg.enabled)  # Returns default

    def test_roundtrip(self):
        original = mod.SynapseConfig()
        d = original.to_dict()
        restored = mod.SynapseConfig.from_dict(d)
        self.assertEqual(original.to_dict(), restored.to_dict())


class TestLoadSynapseConfig(unittest.TestCase):
    """Test config loading from files."""

    def setUp(self):
        mod.clear_config_cache()

    def tearDown(self):
        mod.clear_config_cache()

    def test_no_config_file_returns_defaults(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = mod.load_synapse_config(td)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.trace.output, "_grimoire-output/Grimoire_TRACE.md")

    def test_config_from_yaml_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            yaml_content = """
synapse:
  enabled: true
  trace:
    enabled: false
  orchestrator:
    budget_cap: 200000
"""
            (Path(td) / "project-context.yaml").write_text(yaml_content, encoding="utf-8")
            cfg = mod.load_synapse_config(td)
            self.assertFalse(cfg.trace.enabled)
            self.assertEqual(cfg.orchestrator.budget_cap, 200000)

    def test_cache_works(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg1 = mod.load_synapse_config(td)
            cfg2 = mod.load_synapse_config(td)
            self.assertIs(cfg1, cfg2)

    def test_clear_cache(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg1 = mod.load_synapse_config(td)
            mod.clear_config_cache()
            cfg2 = mod.load_synapse_config(td)
            self.assertIsNot(cfg1, cfg2)

    def test_grimoire_yaml_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            yaml_content = """
synapse:
  message_bus:
    backend: redis
"""
            (Path(td) / "grimoire.yaml").write_text(yaml_content, encoding="utf-8")
            cfg = mod.load_synapse_config(td)
            self.assertEqual(cfg.message_bus.backend, "redis")


class TestValidation(unittest.TestCase):
    """Test config validation."""

    def test_valid_default_config(self):
        cfg = mod.SynapseConfig()
        issues = mod.validate_config(cfg)
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

    def test_invalid_trace_max_entries(self):
        cfg = mod.SynapseConfig()
        cfg.trace.max_entries = 0
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("max_entries", error_fields)

    def test_invalid_threshold_order(self):
        cfg = mod.SynapseConfig()
        cfg.token_budget.warning_threshold = 0.9
        cfg.token_budget.auto_summarize_threshold = 0.5
        issues = mod.validate_config(cfg)
        warnings = [i for i in issues if i.level == "warning"]
        self.assertGreater(len(warnings), 0)

    def test_invalid_chunk_overlap(self):
        cfg = mod.SynapseConfig()
        cfg.rag.chunk_overlap = 600
        cfg.rag.chunk_size = 512
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("chunk_overlap", error_fields)

    def test_invalid_backend(self):
        cfg = mod.SynapseConfig()
        cfg.message_bus.backend = "kafka"
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("backend", error_fields)

    def test_invalid_mode(self):
        cfg = mod.SynapseConfig()
        cfg.orchestrator.default_mode = "turbo"
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("default_mode", error_fields)

    def test_invalid_counter(self):
        cfg = mod.SynapseConfig()
        cfg.token_budget.counter = "custom"
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("counter", error_fields)

    def test_invalid_similarity_threshold(self):
        cfg = mod.SynapseConfig()
        cfg.semantic_cache.similarity_threshold = 1.5
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("similarity_threshold", error_fields)

    def test_zero_budget(self):
        cfg = mod.SynapseConfig()
        cfg.orchestrator.budget_cap = -1
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("budget_cap", error_fields)

    def test_empty_model(self):
        cfg = mod.SynapseConfig()
        cfg.llm_router.default_model = ""
        issues = mod.validate_config(cfg)
        error_fields = [i.field for i in issues if i.level == "error"]
        self.assertIn("default_model", error_fields)


class TestTemplateGeneration(unittest.TestCase):
    """Test template and MCP generation."""

    def test_generate_template_contains_sections(self):
        template = mod.generate_template()
        self.assertIn("synapse:", template)
        self.assertIn("trace:", template)
        self.assertIn("llm_router:", template)
        self.assertIn("rag:", template)
        self.assertIn("token_budget:", template)
        self.assertIn("semantic_cache:", template)
        self.assertIn("message_bus:", template)
        self.assertIn("orchestrator:", template)

    def test_generate_mcp_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mcp = mod.generate_mcp_config(Path(td))
            self.assertIn("mcpServers", mcp)
            self.assertIn("grimoire-synapse", mcp)


class TestMCPInterface(unittest.TestCase):
    """Test MCP tool function."""

    def setUp(self):
        mod.clear_config_cache()

    def tearDown(self):
        mod.clear_config_cache()

    def test_mcp_show(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = mod.mcp_synapse_config(td, action="show")
            self.assertEqual(result["status"], "ok")
            self.assertIn("config", result)
            self.assertIn("enabled", result["config"])

    def test_mcp_validate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = mod.mcp_synapse_config(td, action="validate")
            self.assertEqual(result["status"], "valid")
            self.assertEqual(len(result["errors"]), 0)

    def test_mcp_generate(self):
        result = mod.mcp_synapse_config(".", action="generate")
        self.assertEqual(result["status"], "ok")
        self.assertIn("template", result)

    def test_mcp_generate_mcp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = mod.mcp_synapse_config(td, action="generate-mcp")
            self.assertEqual(result["status"], "ok")

    def test_mcp_unknown_action(self):
        result = mod.mcp_synapse_config(".", action="foo")
        self.assertEqual(result["status"], "error")


class TestCLI(unittest.TestCase):
    """Test CLI commands."""

    def test_no_command_shows_help(self):
        r = mod.main([])
        self.assertIn(r, (0, 1))

    def test_show_command(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod.clear_config_cache()
            r = mod.main(["--project-root", td, "show"])
            self.assertEqual(r, 0)

    def test_validate_default_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod.clear_config_cache()
            r = mod.main(["--project-root", td, "validate"])
            self.assertEqual(r, 0)

    def test_generate_stdout(self):
        r = mod.main(["generate"])
        self.assertEqual(r, 0)

    def test_generate_to_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "out.yaml"
            r = mod.main(["generate", "--output", str(output)])
            self.assertEqual(r, 0)
            self.assertTrue(output.exists())
            self.assertIn("synapse:", output.read_text())


class TestCoerceValue(unittest.TestCase):
    """Test the YAML value coercion helper."""

    def test_bool_true(self):
        self.assertTrue(mod._coerce_value("true"))
        self.assertTrue(mod._coerce_value("True"))

    def test_bool_false(self):
        self.assertFalse(mod._coerce_value("false"))

    def test_null(self):
        self.assertIsNone(mod._coerce_value("null"))

    def test_int(self):
        self.assertEqual(mod._coerce_value("42"), 42)

    def test_float(self):
        self.assertEqual(mod._coerce_value("0.9"), 0.9)

    def test_quoted_string(self):
        self.assertEqual(mod._coerce_value('"hello"'), "hello")

    def test_plain_string(self):
        self.assertEqual(mod._coerce_value("hello"), "hello")


if __name__ == "__main__":
    unittest.main()
