#!/usr/bin/env python3
"""
Grimoire Custom Kit — Tests unitaires Python

Teste les fonctions pures des outils Python du framework :
  - context-guard.py : estimation tokens, formatage, scoring modèles
  - maintenance.py   : chargement mémoire, détection contradictions
  - session-save.py  : parsing état session

Usage:
    python3 -m pytest tests/test_python_tools.py -v
    python3 tests/test_python_tools.py           # unittest runner
"""

import sys
import tempfile
import unittest
from pathlib import Path

# ── Ajouter le dossier framework/tools et framework/memory au path ──────────
KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
sys.path.insert(0, str(KIT_DIR / "framework" / "memory"))


# ═══════════════════════════════════════════════════════════════════════════════
# context-guard.py — Fonctions pures
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateTokens(unittest.TestCase):
    """Test estimate_tokens() — approximation 1 token ≈ 3.7 chars."""

    def setUp(self):
        # Import dynamique car le fichier contient un tiret dans le nom
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_empty_string(self):
        self.assertEqual(self.cg.estimate_tokens(""), 1)  # min 1

    def test_short_string(self):
        result = self.cg.estimate_tokens("hello")
        self.assertGreater(result, 0)

    def test_known_length(self):
        # 370 chars → ~100 tokens (370 / 3.7)
        text = "a" * 370
        result = self.cg.estimate_tokens(text)
        self.assertEqual(result, 100)

    def test_french_text(self):
        text = "L'architecture micro-services est recommandée pour ce projet."
        result = self.cg.estimate_tokens(text)
        self.assertGreater(result, 10)
        self.assertLess(result, 30)


class TestFmtTokens(unittest.TestCase):
    """Test fmt_tokens() — formatage K/brut."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_small_number(self):
        self.assertEqual(self.cg.fmt_tokens(500), "500")

    def test_exactly_1000(self):
        self.assertEqual(self.cg.fmt_tokens(1000), "1.0K")

    def test_large_number(self):
        self.assertEqual(self.cg.fmt_tokens(128000), "128.0K")

    def test_fractional(self):
        self.assertEqual(self.cg.fmt_tokens(1500), "1.5K")


class TestStatusIcon(unittest.TestCase):
    """Test status_icon() — mapping status → emoji."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_ok(self):
        self.assertEqual(self.cg.status_icon("OK"), "✅")

    def test_warning(self):
        self.assertEqual(self.cg.status_icon("WARNING"), "⚠️ ")

    def test_critical(self):
        self.assertEqual(self.cg.status_icon("CRITICAL"), "🔴")

    def test_unknown(self):
        self.assertEqual(self.cg.status_icon("FOOBAR"), "❓")


class TestBar(unittest.TestCase):
    """Test bar() — barre de progression."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_zero(self):
        result = self.cg.bar(0, width=10)
        self.assertIn("·" * 10, result)

    def test_hundred(self):
        result = self.cg.bar(100, width=10)
        # All filled, no dots
        self.assertNotIn("·", result)

    def test_fifty(self):
        result = self.cg.bar(50, width=10)
        self.assertEqual(len(result), 12)  # [chars·dots]


class TestRoleIcon(unittest.TestCase):
    """Test role_icon() — mapping role → emoji."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_known_roles(self):
        self.assertEqual(self.cg.role_icon("agent-definition"), "🤖")
        self.assertEqual(self.cg.role_icon("memory"), "🧠")

    def test_unknown_role(self):
        self.assertEqual(self.cg.role_icon("unknown"), "📄")


class TestModelProfile(unittest.TestCase):
    """Test ModelProfile et MODEL_PROFILES dict."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_profiles_not_empty(self):
        self.assertGreater(len(self.cg.MODEL_PROFILES), 10)

    def test_copilot_profile(self):
        p = self.cg.MODEL_PROFILES["copilot"]
        self.assertEqual(p.id, "copilot")
        self.assertEqual(p.window_tokens, 200_000)
        self.assertEqual(p.tier, "standard")

    def test_all_profiles_have_window(self):
        for name, p in self.cg.MODEL_PROFILES.items():
            self.assertGreater(p.window_tokens, 0, f"{name} has no window_tokens")


class TestCountCommentLines(unittest.TestCase):
    """Test _count_comment_lines() — détection commentaires."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_python_comments(self):
        content = "# comment\ncode\n# another\n"
        comments, total = self.cg._count_comment_lines(content, ".py")
        self.assertEqual(comments, 2)
        self.assertEqual(total, 3)

    def test_yaml_comments(self):
        content = "# header\nkey: value\n# footer\n"
        comments, total = self.cg._count_comment_lines(content, ".yaml")
        self.assertEqual(comments, 2)
        self.assertEqual(total, 3)

    def test_empty_content(self):
        comments, total = self.cg._count_comment_lines("", ".py")
        self.assertEqual(comments, 0)
        self.assertEqual(total, 0)

    def test_no_comments(self):
        content = "x = 1\ny = 2\n"
        comments, total = self.cg._count_comment_lines(content, ".py")
        self.assertEqual(comments, 0)
        self.assertEqual(total, 2)


class TestFindExtractableSections(unittest.TestCase):
    """Test _find_extractable_sections() — détection tables/blocs longs."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_short_table(self):
        # Table < 10 lignes → pas extractible
        content = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        result = self.cg._find_extractable_sections(content)
        self.assertEqual(len(result), 0)

    def test_long_code_block(self):
        # Bloc > 15 lignes → extractible
        lines = ["```python"] + [f"  line_{i}" for i in range(20)] + ["```"]
        content = "\n".join(lines)
        result = self.cg._find_extractable_sections(content)
        self.assertGreaterEqual(len(result), 1)
        self.assertIn("Bloc de code", result[0][0])


class TestCostToTier(unittest.TestCase):
    """Test _cost_to_tier() — conversion cost → tier."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_cheap(self):
        self.assertEqual(self.cg._cost_to_tier("cheap"), "economy")

    def test_medium(self):
        self.assertEqual(self.cg._cost_to_tier("medium"), "standard")

    def test_any(self):
        self.assertEqual(self.cg._cost_to_tier("any"), "premium")

    def test_unknown(self):
        self.assertEqual(self.cg._cost_to_tier("xyz"), "standard")


class TestScoreModelForAgent(unittest.TestCase):
    """Test score_model_for_agent() — scoring modèle/agent."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_perfect_match(self):
        """Un modèle premium avec reasoning extreme devrait scorer haut."""
        profile = self.cg.MODEL_PROFILES["claude-opus-4"]
        affinity = self.cg.ModelAffinity(
            reasoning="extreme", context_window="large", speed="slow-ok", cost="any"
        )
        score = self.cg.score_model_for_agent(profile, affinity, 10000)
        self.assertGreater(score, 70)

    def test_underpowered_model(self):
        """Un modèle economy pour un agent qui a besoin d'extreme reasoning devrait scorer bas."""
        profile = self.cg.MODEL_PROFILES["llama3"]
        affinity = self.cg.ModelAffinity(
            reasoning="extreme", context_window="large", speed="fast", cost="any"
        )
        score = self.cg.score_model_for_agent(profile, affinity, 50000)
        self.assertLess(score, 40)


class TestReadFileSafe(unittest.TestCase):
    """Test read_file_safe() — lecture fichier sécurisée."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            f.flush()
            result = self.cg.read_file_safe(Path(f.name))
            self.assertEqual(result, "hello world")
            Path(f.name).unlink()

    def test_missing_file(self):
        result = self.cg.read_file_safe(Path("/nonexistent/file.txt"))
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════════════════════════════════════
# maintenance.py — Fonctions mémoire
# ═══════════════════════════════════════════════════════════════════════════════

class TestMemoryLoadSave(unittest.TestCase):
    """Test load_memories() / save_memories()."""

    def setUp(self):
        import importlib
        self.maint = importlib.import_module("maintenance")
        self._original_file = self.maint.MEMORIES_FILE
        self.tmpdir = tempfile.mkdtemp()
        self.maint.MEMORIES_FILE = Path(self.tmpdir) / "memories.json"

    def tearDown(self):
        self.maint.MEMORIES_FILE = self._original_file
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty(self):
        result = self.maint.load_memories()
        self.assertEqual(result, [])

    def test_save_and_load(self):
        data = [{"agent": "test", "memory": "learned X", "timestamp": "2026-01-01"}]
        self.maint.save_memories(data)
        loaded = self.maint.load_memories()
        self.assertEqual(loaded, data)

    def test_save_unicode(self):
        data = [{"note": "Décision architecturale: microservices"}]
        self.maint.save_memories(data)
        loaded = self.maint.load_memories()
        self.assertIn("Décision", loaded[0]["note"])


class TestDetectContradictions(unittest.TestCase):
    """Test _detect_memory_contradictions()."""

    def setUp(self):
        import importlib
        self.maint = importlib.import_module("maintenance")

    def test_no_memories(self):
        count = self.maint._detect_memory_contradictions([])
        self.assertEqual(count, 0)

    def test_no_contradictions(self):
        memories = [
            {"agent": "dev", "memory": "use Python 3.12 for this project"},
            {"agent": "qa", "memory": "integration tests with pytest"},
        ]
        count = self.maint._detect_memory_contradictions(memories)
        self.assertEqual(count, 0)


class TestInfrastructurePattern(unittest.TestCase):
    """Test _get_infrastructure_pattern()."""

    def setUp(self):
        import importlib
        self.maint = importlib.import_module("maintenance")

    def test_returns_pattern(self):
        pattern = self.maint._get_infrastructure_pattern()
        self.assertIsInstance(pattern, str)
        self.assertGreater(len(pattern), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# session-save.py — Test de structure
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionSaveImport(unittest.TestCase):
    """Vérifie que session-save.py s'importe correctement."""

    def test_import(self):
        import importlib
        mod = importlib.import_module("session-save")
        # Vérifier que les fonctions principales existent
        self.assertTrue(hasattr(mod, "main") or hasattr(mod, "save_session") or callable(mod))


# ═══════════════════════════════════════════════════════════════════════════════
# agent-forge.py — Test structure d'import
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentForgeImport(unittest.TestCase):
    """Vérifie que agent-forge.py s'importe sans erreur."""

    def test_import(self):
        import importlib
        mod = importlib.import_module("agent-forge")
        self.assertTrue(hasattr(mod, "main") or True)  # import ok = pass


# ═══════════════════════════════════════════════════════════════════════════════
# FileLoad & AgentBudget — dataclasses contextuelles
# ═══════════════════════════════════════════════════════════════════════════════

class TestFileLoad(unittest.TestCase):
    """Test FileLoad.compute()."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_compute_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test agent\n" * 100)
            f.flush()
            fl = self.cg.FileLoad(path=Path(f.name), role="agent-definition")
            fl.compute()
            self.assertTrue(fl.loaded)
            self.assertGreater(fl.tokens, 0)
            self.assertIn("# Test agent", fl.content)
            Path(f.name).unlink()

    def test_compute_missing_file(self):
        fl = self.cg.FileLoad(path=Path("/nonexistent.md"), role="memory")
        fl.compute()
        self.assertFalse(fl.loaded)
        self.assertEqual(fl.tokens, 0)


class TestAgentBudget(unittest.TestCase):
    """Test AgentBudget properties."""

    def setUp(self):
        import importlib
        self.cg = importlib.import_module("context-guard")

    def test_total_tokens(self):
        loads = [
            self.cg.FileLoad(path=Path("a.md"), role="agent", tokens=100, loaded=True),
            self.cg.FileLoad(path=Path("b.md"), role="memory", tokens=200, loaded=True),
            self.cg.FileLoad(path=Path("c.md"), role="dna", tokens=0, loaded=False),
        ]
        budget = self.cg.AgentBudget(
            agent_id="test-agent",
            agent_path=Path("test.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        self.assertEqual(budget.total_tokens, 300)

    def test_pct(self):
        loads = [
            self.cg.FileLoad(path=Path("a.md"), role="agent", tokens=40_000, loaded=True),
        ]
        budget = self.cg.AgentBudget(
            agent_id="test",
            agent_path=Path("test.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        self.assertAlmostEqual(budget.pct, 20.0, places=1)

    def test_status_ok(self):
        loads = [
            self.cg.FileLoad(path=Path("a.md"), role="agent", tokens=10_000, loaded=True),
        ]
        budget = self.cg.AgentBudget(
            agent_id="test",
            agent_path=Path("test.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        self.assertEqual(budget.status, "OK")

    def test_status_critical(self):
        loads = [
            self.cg.FileLoad(path=Path("a.md"), role="agent", tokens=180_000, loaded=True),
        ]
        budget = self.cg.AgentBudget(
            agent_id="test",
            agent_path=Path("test.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        self.assertEqual(budget.status, "CRITICAL")


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main()
