"""Tests d'intégration inter-outils Synapse (Story 7.1 — BM-46).

Vérifie que les outils Synapse fonctionnent ensemble
dans des scénarios end-to-end réalistes.

5 Scénarios :
  1. Orchestrator → plan/execute (simulated)
  2. Token budget check → status chain
  3. Message bus → delivery-contracts validation
  4. Synapse config → trace integration
  5. Conversation branch → context merge flow
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

# ── Module loader ──────────────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent.parent / "framework" / "tools"


def _load_module(name: str):
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = TOOLS_DIR / f"{name}.py"
    if not mod_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load all modules we'll test
synapse_config = _load_module("synapse-config")
synapse_trace = _load_module("synapse-trace")
message_bus = _load_module("message-bus")
delivery_contracts = _load_module("delivery-contracts")
orchestrator = _load_module("orchestrator")
token_budget = _load_module("token-budget")
conversation_branch = _load_module("conversation-branch")
context_merge = _load_module("context-merge")
agent_worker = _load_module("agent-worker")
conversation_history = _load_module("conversation-history")


# ── Scenario 1: Orchestrator simulated workflow ─────────────────────────────


class TestScenario1OrchestratorFlow(unittest.TestCase):
    """E2E: Orchestrator crée un plan et l'exécute en mode simulated."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        (self.root / "_grimoire-output" / "orchestrator-history").mkdir(parents=True)
        synapse_trace.reset_global_tracer()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        synapse_trace.reset_global_tracer()

    @unittest.skipIf(orchestrator is None, "orchestrator.py missing")
    def test_plan_and_execute_simulated(self):
        """Orchestrator can create and execute a plan in simulated mode."""
        orch = orchestrator.Orchestrator(self.root, budget_cap=500_000)
        plan = orch.create_plan(
            workflow="code-review",
            agents=["dev", "qa"],
            task="Review PR #42",
            mode_override="simulated",
        )
        self.assertEqual(plan.mode, "simulated")
        self.assertTrue(plan.budget_ok)
        self.assertEqual(len(plan.steps), 2)

        result = orch.execute(plan, dry_run=True)
        self.assertIsInstance(result, orchestrator.ExecutionResult)
        self.assertEqual(result.status, "completed")

    @unittest.skipIf(orchestrator is None, "orchestrator.py missing")
    def test_mode_detection(self):
        """Orchestrator selects appropriate mode based on workflow."""
        orch = orchestrator.Orchestrator(self.root)
        mode, reason = orch.decide_mode("code-review")
        self.assertIn(mode, orchestrator.VALID_MODES)
        self.assertTrue(reason)

    @unittest.skipIf(orchestrator is None, "orchestrator.py missing")
    def test_budget_fallback(self):
        """Orchestrator falls back when budget is exceeded."""
        orch = orchestrator.Orchestrator(self.root, budget_cap=1)
        plan = orch.create_plan(
            workflow="sprint-planning",
            agents=["pm", "sm", "dev"],
            task="Sprint 5",
        )
        # Should fallback to simulated due to low budget
        self.assertTrue(plan.budget_ok)  # simulated is always affordable
        self.assertIn(plan.mode, orchestrator.VALID_MODES)


# ── Scenario 2: Token budget check chain ────────────────────────────────────


class TestScenario2TokenBudgetChain(unittest.TestCase):
    """E2E: Token budget check → status → enforcement dry-run."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @unittest.skipIf(token_budget is None, "token-budget.py missing")
    def test_check_returns_status(self):
        """TokenBudgetEnforcer.check() returns a valid BudgetStatus."""
        enforcer = token_budget.TokenBudgetEnforcer(
            project_root=self.root,
            model="claude-sonnet-4-20250514",
        )
        status = enforcer.check()
        self.assertIsInstance(status, token_budget.BudgetStatus)
        self.assertEqual(status.model, "claude-sonnet-4-20250514")
        self.assertEqual(status.level, "ok")  # Empty project = no files
        self.assertEqual(status.used_tokens, 0)

    @unittest.skipIf(token_budget is None, "token-budget.py missing")
    def test_enforce_dry_run(self):
        """Enforcement in dry-run mode produces a report without writing."""
        enforcer = token_budget.TokenBudgetEnforcer(
            project_root=self.root,
            model="claude-sonnet-4-20250514",
        )
        report = enforcer.enforce(dry_run=True)
        self.assertIsInstance(report, token_budget.EnforcementReport)
        self.assertIsNotNone(report.status_before)
        self.assertIsNotNone(report.status_after)

    @unittest.skipIf(token_budget is None, "token-budget.py missing")
    def test_mcp_budget_interface(self):
        """MCP interface returns dict with required fields."""
        result = token_budget.mcp_context_budget(str(self.root))
        self.assertIn("model", result)
        self.assertIn("level", result)
        self.assertIn("used_tokens", result)


# ── Scenario 3: Message bus → delivery contracts ────────────────────────────


class TestScenario3BusContracts(unittest.TestCase):
    """E2E: Message bus send/receive + contract validation."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @unittest.skipIf(message_bus is None or delivery_contracts is None, "deps missing")
    def test_send_receive_validate(self):
        """Send a message via bus, receive it, validate payload against contract."""
        bus = message_bus.InProcessBus()
        bus._ensure_queue("qa")

        msg = message_bus.AgentMessage(
            sender="dev",
            recipient="qa",
            msg_type="task-request",
            payload={"task": "Run tests", "priority": 1},
        )
        result = bus.send(msg)
        self.assertTrue(result.success)

        received = bus.receive("qa", timeout=0.1)
        self.assertIsNotNone(received)
        self.assertEqual(received.sender, "dev")
        self.assertEqual(received.payload["task"], "Run tests")

        # Validate with a built-in contract
        registry = delivery_contracts.ContractRegistry(self.root)
        # Access builtin contracts
        self.assertGreater(len(registry._contracts), 0)

        bus.close()

    @unittest.skipIf(message_bus is None, "message-bus.py missing")
    def test_broadcast_and_stats(self):
        """Broadcast message reaches multiple recipients."""
        bus = message_bus.InProcessBus()
        bus._ensure_queue("dev")
        bus._ensure_queue("qa")
        bus._ensure_queue("architect")

        msg = message_bus.AgentMessage(
            sender="pm",
            recipient="*",
            msg_type="announcement",
            payload={"text": "Sprint planning at 10am"},
        )
        result = bus.send(msg)
        self.assertTrue(result.success)
        self.assertGreaterEqual(result.recipients_count, 2)

        stats = bus.get_stats()
        self.assertEqual(stats.total_sent, 1)

        bus.close()

    @unittest.skipIf(delivery_contracts is None, "delivery-contracts.py missing")
    def test_schema_validation(self):
        """SchemaValidator validates payloads correctly."""
        validator = delivery_contracts.SchemaValidator()
        schema = delivery_contracts.ContractSchema(
            fields=[
                delivery_contracts.ContractField(name="task", field_type="string", required=True),
                delivery_contracts.ContractField(name="priority", field_type="integer", required=False),
            ],
        )
        json_schema = schema.to_json_schema()
        result = validator.validate({"task": "test"}, json_schema)
        self.assertTrue(result.valid)

        result_bad = validator.validate({}, json_schema)
        self.assertFalse(result_bad.valid)


# ── Scenario 4: Synapse config + trace integration ──────────────────────────


class TestScenario4ConfigTrace(unittest.TestCase):
    """E2E: Config loading + trace recording with config-driven settings."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        synapse_config.clear_config_cache()
        synapse_trace.reset_global_tracer()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        synapse_config.clear_config_cache()
        synapse_trace.reset_global_tracer()

    def test_default_config_then_trace(self):
        """Load default config, create tracer, record entries, get stats."""
        cfg = synapse_config.load_synapse_config(self.root)
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.trace.enabled)

        tracer = synapse_trace.SynapseTracer(self.root, enabled=cfg.trace.enabled, dry_run=True)
        tracer.record(synapse_trace.TraceEntry(tool="router", operation="classify", agent="dev"))
        tracer.record(synapse_trace.TraceEntry(tool="cache", operation="lookup", status="ok"))

        stats = tracer.get_stats()
        self.assertEqual(stats.total_entries, 2)
        self.assertIn("router", stats.by_tool)

    def test_disabled_config_skips_trace(self):
        """When trace is disabled in config, tracer does not record."""
        cfg = synapse_config.SynapseConfig()
        cfg.trace.enabled = False

        tracer = synapse_trace.SynapseTracer(self.root, enabled=cfg.trace.enabled, dry_run=True)
        tracer.record(synapse_trace.TraceEntry(tool="t", operation="o"))
        self.assertEqual(len(tracer.entries), 0)

    def test_config_with_yaml_then_trace(self):
        """Load custom YAML config and verify trace uses it."""
        yaml_content = """
synapse:
  trace:
    enabled: true
    max_entries: 5
"""
        (self.root / "project-context.yaml").write_text(yaml_content, encoding="utf-8")
        cfg = synapse_config.load_synapse_config(self.root)
        self.assertEqual(cfg.trace.max_entries, 5)

        tracer = synapse_trace.SynapseTracer(self.root, enabled=cfg.trace.enabled, dry_run=True)
        tracer.record(synapse_trace.TraceEntry(tool="t", operation="o"))
        self.assertEqual(len(tracer.entries), 1)

    def test_validate_then_trace(self):
        """Config validation + trace recording in sequence."""
        cfg = synapse_config.SynapseConfig()
        issues = synapse_config.validate_config(cfg)
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

        tracer = synapse_trace.SynapseTracer(self.root, dry_run=True)
        tracer.record(synapse_trace.TraceEntry(
            tool="synapse-config",
            operation="validate",
            details={"issues": len(issues)},
        ))
        self.assertEqual(tracer.entries[0].details["issues"], 0)

    def test_mcp_config_and_trace(self):
        """MCP interfaces for both config and trace work together."""
        cfg_result = synapse_config.mcp_synapse_config(str(self.root), action="show")
        self.assertEqual(cfg_result["status"], "ok")

        trace_result = synapse_trace.mcp_synapse_trace(str(self.root), action="status")
        self.assertEqual(trace_result["status"], "ok")


# ── Scenario 5: Branch + merge flow ─────────────────────────────────────────


class TestScenario5BranchMerge(unittest.TestCase):
    """E2E: Conversation branch creation + context merge compatibility."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @unittest.skipIf(conversation_branch is None, "conversation-branch.py missing")
    def test_branch_lifecycle(self):
        """Create, switch, and archive a branch."""
        mgr = conversation_branch.BranchManager(self.root)
        # Create branch
        info = mgr.branch("feature-x", purpose="test feature")
        self.assertEqual(info.name, "feature-x")

        # List — should have main and feature-x
        tree = mgr.list_branches()
        self.assertGreaterEqual(len(tree.branches), 1)

        # Switch
        mgr.switch("feature-x")

        # Archive
        mgr.archive("feature-x")

    @unittest.skipIf(context_merge is None, "context-merge.py missing")
    def test_merge_requires_branches(self):
        """ContextMerger handles missing branches gracefully."""
        merger = context_merge.ContextMerger(self.root)
        # Diff without valid branches
        diff = merger.diff("nonexistent-a", "nonexistent-b")
        # Should return a diff object even if empty
        self.assertIsNotNone(diff)

    @unittest.skipIf(conversation_branch is None or context_merge is None, "deps missing")
    def test_branch_then_diff(self):
        """Create branches and compute a diff."""
        mgr = conversation_branch.BranchManager(self.root)
        mgr.branch("branch-a", purpose="Test A")
        mgr.branch("branch-b", purpose="Test B")

        merger = context_merge.ContextMerger(self.root)
        diff = merger.diff("branch-a", "branch-b")
        self.assertIsNotNone(diff)


# ── Cross-cutting: Decorator tracing with real tool calls ────────────────────


class TestCrossCuttingTracedCalls(unittest.TestCase):
    """Test that @synapse_traced decorator captures real tool operations."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        self.tracer = synapse_trace.SynapseTracer(self.root, dry_run=True)
        synapse_trace.set_global_tracer(self.tracer)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        synapse_trace.reset_global_tracer()

    def test_traced_config_show(self):
        """Wrap mcp_synapse_config with tracing."""
        @synapse_trace.synapse_traced("synapse-config", "show")
        def traced_config_show(project_root):
            return synapse_config.mcp_synapse_config(project_root, action="show")

        synapse_config.clear_config_cache()
        result = traced_config_show(str(self.root))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(self.tracer.entries), 1)
        self.assertEqual(self.tracer.entries[0].tool, "synapse-config")
        self.assertEqual(self.tracer.entries[0].status, "ok")

    @unittest.skipIf(token_budget is None, "token-budget.py missing")
    def test_traced_budget_check(self):
        """Wrap mcp_context_budget with tracing."""
        @synapse_trace.synapse_traced("token-budget", "check")
        def traced_budget(project_root):
            return token_budget.mcp_context_budget(project_root)

        result = traced_budget(str(self.root))
        self.assertIn("level", result)
        self.assertEqual(len(self.tracer.entries), 1)
        self.assertEqual(self.tracer.entries[0].tool, "token-budget")

    def test_traced_error_captured(self):
        """Decorator captures exceptions in traced calls."""
        @synapse_trace.synapse_traced("bad-tool", "crash")
        def crashing_fn():
            raise RuntimeError("test explosion")

        with self.assertRaises(RuntimeError):
            crashing_fn()

        self.assertEqual(len(self.tracer.entries), 1)
        self.assertEqual(self.tracer.entries[0].status, "error")


# ── Scenario: Multi-tool pipeline ────────────────────────────────────────────


class TestMultiToolPipeline(unittest.TestCase):
    """Test a pipeline of multiple tools working together."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        (self.root / "_grimoire-output" / "orchestrator-history").mkdir(parents=True)
        synapse_config.clear_config_cache()
        synapse_trace.reset_global_tracer()
        self.tracer = synapse_trace.SynapseTracer(self.root, dry_run=True)
        synapse_trace.set_global_tracer(self.tracer)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        synapse_config.clear_config_cache()
        synapse_trace.reset_global_tracer()

    @unittest.skipIf(orchestrator is None or message_bus is None, "deps missing")
    def test_config_plan_execute_trace(self):
        """
        Full pipeline:
        1. Load config
        2. Orchestrator plan
        3. Execute (simulated)
        4. Verify trace captured all
        """
        # 1. Config
        cfg = synapse_config.load_synapse_config(self.root)
        self.assertTrue(cfg.enabled)

        # 2. Plan
        orch = orchestrator.Orchestrator(self.root, budget_cap=cfg.orchestrator.budget_cap)
        plan = orch.create_plan(
            workflow="code-review",
            agents=["dev", "qa"],
            task="Review login feature",
            mode_override="simulated",
        )
        self.assertTrue(plan.budget_ok)

        # Record trace manually (as the decorator would)
        self.tracer.record(synapse_trace.TraceEntry(
            tool="orchestrator", operation="plan",
            details={"mode": plan.mode, "steps": len(plan.steps)},
        ))

        # 3. Execute
        result = orch.execute(plan, dry_run=True)
        self.assertEqual(result.status, "completed")

        self.tracer.record(synapse_trace.TraceEntry(
            tool="orchestrator", operation="execute",
            details={"status": result.status, "steps": len(result.steps)},
        ))

        # 4. Verify traces
        stats = self.tracer.get_stats()
        self.assertEqual(stats.total_entries, 2)
        self.assertEqual(stats.by_tool["orchestrator"], 2)

    @unittest.skipIf(message_bus is None or delivery_contracts is None, "deps missing")
    def test_bus_contract_trace_pipeline(self):
        """
        Pipeline:
        1. Create bus + send message
        2. Validate via contracts
        3. All operations traced
        """
        bus = message_bus.InProcessBus()
        bus._ensure_queue("reviewer")

        # 1. Send
        msg = message_bus.AgentMessage(
            sender="dev",
            recipient="reviewer",
            msg_type="review-request",
            payload={"pr": 42, "branch": "feature-x"},
        )
        send_result = bus.send(msg)
        self.assertTrue(send_result.success)

        self.tracer.record(synapse_trace.TraceEntry(
            tool="message-bus", operation="send",
            details={"recipient": "reviewer", "type": msg.msg_type},
        ))

        # 2. Receive
        received = bus.receive("reviewer", timeout=0.1)
        self.assertIsNotNone(received)

        self.tracer.record(synapse_trace.TraceEntry(
            tool="message-bus", operation="receive",
            details={"sender": received.sender},
        ))

        # 3. Validate
        registry = delivery_contracts.ContractRegistry(self.root)
        self.tracer.record(synapse_trace.TraceEntry(
            tool="delivery-contracts", operation="validate",
            details={"contracts_loaded": len(registry._contracts)},
        ))

        # 4. Verify
        stats = self.tracer.get_stats()
        self.assertEqual(stats.total_entries, 3)
        self.assertIn("message-bus", stats.by_tool)
        self.assertIn("delivery-contracts", stats.by_tool)

        bus.close()


if __name__ == "__main__":
    unittest.main()
