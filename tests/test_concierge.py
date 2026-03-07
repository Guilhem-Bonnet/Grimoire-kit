"""Tests for concierge.py — Triage & agent routing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

import concierge as C

# ── Triage Logic ─────────────────────────────────────────────────────────────


class TestTriage:
    def test_code_query_routes_to_dev(self):
        r = C.triage("Corrige le bug dans le module auth")
        assert r.suggested_agent == "dev"
        assert r.confidence >= 0.3

    def test_architecture_query_routes_to_architect(self):
        r = C.triage("Refonte de l'architecture API et migration base de données")
        assert r.suggested_agent == "architect"
        assert r.confidence >= 0.3

    def test_doc_query_routes_to_tech_writer(self):
        r = C.triage("Rédiger la documentation du README avec un diagramme mermaid")
        assert r.suggested_agent == "tech-writer"
        assert r.confidence >= 0.3

    def test_ux_query_routes_to_ux(self):
        r = C.triage("Concevoir le parcours utilisateur et l'interface du dashboard")
        assert r.suggested_agent == "ux-designer"

    def test_test_query_routes_to_qa_or_dev(self):
        r = C.triage("Écrire des tests e2e pour le pipeline CI de couverture")
        assert r.suggested_agent in ("qa", "dev")

    def test_quick_prototype_routes_to_barry(self):
        r = C.triage("Prototype rapide POC minimal")
        assert r.suggested_agent == "quick-flow-solo-dev"

    def test_ambiguous_query_low_confidence(self):
        r = C.triage("améliorer le truc")
        assert r.confidence <= 0.5

    def test_empty_like_query(self):
        r = C.triage("xyz zzz nop")
        assert r.confidence <= 0.3

    def test_alternatives_populated(self):
        r = C.triage("Implémenter l'API et la documenter")
        assert len(r.alternatives) >= 1

    def test_classification_complex(self):
        r = C.triage("Migration système et refonte de l'architecture multi-services")
        assert r.classification == "complex"

    def test_classification_simple(self):
        r = C.triage("Créer une fonction rapide pour parser le CSV")
        assert r.classification == "simple"

    def test_result_has_all_fields(self):
        r = C.triage("Analyse du marché concurrent")
        assert r.query
        assert r.classification in ("simple", "complex", "ambiguous")
        assert r.suggested_agent
        assert r.agent_name
        assert isinstance(r.confidence, float)
        assert r.reasoning


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_mcp_triage_ok(self):
        result = C.mcp_concierge_triage(str(ROOT), query="corriger un bug")
        assert result["status"] == "ok"
        assert result["suggested_agent"] == "dev"

    def test_mcp_triage_empty_query(self):
        result = C.mcp_concierge_triage(str(ROOT))
        assert result["status"] == "error"

    def test_mcp_agents(self):
        result = C.mcp_concierge_agents(str(ROOT))
        assert result["status"] == "ok"
        assert len(result["agents"]) > 5


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_triage_cli(self, capsys):
        ret = C.main(["--project-root", str(ROOT), "triage", "--query", "écrire du code"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Concierge" in out

    def test_triage_cli_json(self, capsys):
        ret = C.main(["--project-root", str(ROOT), "--json", "triage", "--query", "test"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "suggested_agent" in data

    def test_agents_cli(self, capsys):
        ret = C.main(["--project-root", str(ROOT), "agents"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "dev" in out

    def test_check_risk_cli(self, capsys):
        ret = C.main(["--project-root", str(ROOT), "check-risk", "--query", "déployer en prod"])
        # Either 0 (no risk) or 1 (risks found) — both valid
        assert ret in (0, 1)
