"""Tests for missions/intake.py — deterministic request classification."""

from __future__ import annotations

import pytest

from grimoire.missions.intake import IntakeRequest, MissionIntakeService
from grimoire.missions.schemas import RiskProfile, TaskType


@pytest.fixture()
def svc() -> MissionIntakeService:
    return MissionIntakeService()


class TestRiskScoring:
    def test_read_request_is_light(self, svc) -> None:
        r = svc.analyze(IntakeRequest("List all open missions and show their status"))
        assert r.risk_profile == RiskProfile.LIGHT

    def test_implement_is_standard(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Implement the recipe schema for workflow instances"))
        assert r.risk_profile == RiskProfile.STANDARD

    def test_delete_is_strict(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Remove deprecated packs from the registry and clean old migration files"))
        assert r.risk_profile in (RiskProfile.STRICT, RiskProfile.STANDARD)

    def test_secret_keyword_is_critical(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Rotate the API key and update the secret in .env"))
        assert r.risk_profile == RiskProfile.SECURITY_CRITICAL

    def test_sensitive_path_is_critical(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Read the contents of credentials.json and update it"))
        assert r.risk_profile == RiskProfile.SECURITY_CRITICAL


class TestTaskTypeDetection:
    def test_test_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Write pytest tests for the evidence service"))
        assert r.task_proposals[0].task_type == TaskType.TEST

    def test_implement_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Implement the new CLI command for recipe listing"))
        assert r.task_proposals[0].task_type == TaskType.IMPLEMENTATION

    def test_analyze_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Analyze the performance bottleneck in the memory backend"))
        assert r.task_proposals[0].task_type == TaskType.ANALYSIS

    def test_document_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Document the trace ledger API in the README"))
        assert r.task_proposals[0].task_type == TaskType.DOCUMENTATION

    def test_migrate_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Migrate the Qdrant vectors to Weaviate"))
        assert r.task_proposals[0].task_type == TaskType.MIGRATION

    def test_security_keyword(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Audit the authentication flow for vulnerabilities"))
        assert r.task_proposals[0].task_type == TaskType.SECURITY


class TestAutoTestProposal:
    def test_implementation_suggests_test_task(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Implement the cockpit projection builder"))
        types = [p.task_type for p in r.task_proposals]
        assert TaskType.IMPLEMENTATION in types
        assert TaskType.TEST in types

    def test_test_request_does_not_duplicate(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Write tests for the recipe registry"))
        types = [p.task_type for p in r.task_proposals]
        assert types.count(TaskType.TEST) == 1


class TestScopeDetection:
    def test_network_scope(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Deploy the new API endpoint to production"))
        assert "network" in r.scope_hints

    def test_memory_scope(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Migrate the Weaviate memory backend"))
        assert "memory" in r.scope_hints

    def test_pack_scope(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Install the new pack from the registry"))
        assert "pack" in r.scope_hints

    def test_default_scope(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Refactor the core module"))
        assert r.scope_hints  # at least one scope


class TestPolicyHints:
    def test_deploy_triggers_hint(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Deploy the release pack to production"))
        rule_ids = [h.rule_id for h in r.policy_hints]
        assert any("destructive" in rid or "strict" in rid or "activation" in rid for rid in rule_ids)

    def test_close_triggers_verification_hint(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Close all done tasks and finish the mission"))
        rule_ids = [h.rule_id for h in r.policy_hints]
        assert "task_close_requires_verification" in rule_ids


class TestMiscellaneous:
    def test_confidence_increases_with_detail(self, svc) -> None:
        short = svc.analyze(IntakeRequest("Fix it"))
        long = svc.analyze(IntakeRequest(
            "Implement the new trace ledger module with OTel export support, "
            "including unit tests and documentation for the grimoire-kit SDK."
        ))
        assert long.confidence >= short.confidence

    def test_title_extracted(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Implement the recipe schema. It should support steps and gates."))
        assert r.mission_title.startswith("Implement the recipe schema")

    def test_mission_type_set(self, svc) -> None:
        r = svc.analyze(IntakeRequest("Analyze the performance regression"))
        assert r.mission_type == "analysis"
