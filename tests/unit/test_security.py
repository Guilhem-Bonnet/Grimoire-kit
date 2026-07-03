"""M3 — Security threat matrix and pack trust tier refusal tests."""

from __future__ import annotations

from grimoire.policies.security import (
    GRIMOIRE_THREAT_MATRIX,
    TRUST_TIER_GATES,
    PackTrustTier,
    ThreatCategory,
    ThreatMatrix,
    evaluate_pack_trust,
)

# ── Threat matrix ─────────────────────────────────────────────────────────────

class TestThreatMatrix:
    def test_all_threats_have_mitigations(self) -> None:
        for entry in GRIMOIRE_THREAT_MATRIX.entries:
            assert entry.grimoire_mitigation, f"{entry.id} missing mitigation"

    def test_coverage_pct_at_least_80(self) -> None:
        pct = GRIMOIRE_THREAT_MATRIX.coverage_pct()
        assert pct >= 80.0, f"Threat coverage {pct:.1f}% < 80%"

    def test_all_canonical_threats_implemented(self) -> None:
        not_done = GRIMOIRE_THREAT_MATRIX.not_implemented()
        assert not_done == [], f"Unimplemented threats: {[e.id for e in not_done]}"

    def test_prompt_injection_covered(self) -> None:
        entries = GRIMOIRE_THREAT_MATRIX.by_category(ThreatCategory.PROMPT_INJECTION)
        assert len(entries) >= 1

    def test_excessive_agency_covered(self) -> None:
        entries = GRIMOIRE_THREAT_MATRIX.by_category(ThreatCategory.EXCESSIVE_AGENCY)
        assert len(entries) >= 1

    def test_supply_chain_covered(self) -> None:
        entries = GRIMOIRE_THREAT_MATRIX.by_category(ThreatCategory.SUPPLY_CHAIN)
        assert len(entries) >= 1

    def test_to_dict(self) -> None:
        d = GRIMOIRE_THREAT_MATRIX.to_dict()
        assert "total" in d
        assert "implemented" in d
        assert "coverage_pct" in d
        assert d["total"] >= 10

    def test_each_entry_has_negative_test_id(self) -> None:
        for entry in GRIMOIRE_THREAT_MATRIX.entries:
            assert entry.negative_test_id, f"{entry.id} missing negative_test_id"

    def test_empty_matrix_coverage(self) -> None:
        m = ThreatMatrix()
        assert m.coverage_pct() == 0.0


# ── Pack trust tier gates ──────────────────────────────────────────────────────

class TestPackTrustTierGates:
    def test_all_tiers_have_gate(self) -> None:
        for tier in PackTrustTier:
            assert tier in TRUST_TIER_GATES

    def test_untrusted_requires_doctor(self) -> None:
        gate = TRUST_TIER_GATES[PackTrustTier.UNTRUSTED]
        assert gate.requires_doctor is True

    def test_untrusted_read_only(self) -> None:
        gate = TRUST_TIER_GATES[PackTrustTier.UNTRUSTED]
        assert gate.max_mutation_class == "READ_ONLY"

    def test_untrusted_restricted_tools(self) -> None:
        gate = TRUST_TIER_GATES[PackTrustTier.UNTRUSTED]
        assert gate.allowed_tools is not None
        assert "read" in gate.allowed_tools

    def test_verified_requires_signature(self) -> None:
        gate = TRUST_TIER_GATES[PackTrustTier.VERIFIED]
        assert gate.requires_signed is True

    def test_internal_unrestricted(self) -> None:
        gate = TRUST_TIER_GATES[PackTrustTier.INTERNAL]
        assert gate.allowed_tools is None
        assert gate.max_mutation_class == "DESTRUCTIVE"


# ── Refusal tests (negative tests / red-team cases) ───────────────────────────

class TestSecurityRefusals:
    # THR-005: Untrusted pack + destructive mutation
    def test_untrusted_pack_blocks_destructive_mutation(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.UNTRUSTED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="DESTRUCTIVE",
        )
        assert result.allowed is False
        assert any("mutation_class" in v for v in result.violations)

    # THR-006: Community pack without doctor
    def test_pack_trust_requires_doctor_for_community_tier(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.COMMUNITY,
            has_doctor_passed=False,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert result.allowed is False
        assert any("doctor" in v for v in result.violations)

    def test_community_pack_without_digest_blocked(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.COMMUNITY,
            has_doctor_passed=True,
            has_digest=False,
            has_signature=False,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert result.allowed is False
        assert any("digest" in v for v in result.violations)

    def test_verified_pack_without_signature_blocked(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.VERIFIED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert result.allowed is False
        assert any("signed" in v for v in result.violations)

    def test_untrusted_tool_not_in_allowlist_blocked(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.UNTRUSTED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="READ_ONLY",
            requested_tools=["execute", "write_file"],
        )
        assert result.allowed is False
        assert any("tools" in v for v in result.violations)

    def test_untrusted_allowed_tools_pass(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.UNTRUSTED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="READ_ONLY",
            requested_tools=["read", "search"],
        )
        assert result.allowed is True
        assert result.violations == []

    def test_verified_pack_all_checks_pass(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.VERIFIED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=True,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert result.allowed is True

    def test_internal_pack_no_restrictions(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.INTERNAL,
            has_doctor_passed=False,
            has_digest=False,
            has_signature=False,
            requested_mutation_class="DESTRUCTIVE",
            requested_tools=["rm", "overwrite"],
        )
        assert result.allowed is True

    def test_multiple_violations_accumulated(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.VERIFIED,
            has_doctor_passed=False,
            has_digest=False,
            has_signature=False,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert result.allowed is False
        assert len(result.violations) >= 3  # doctor + digest + signature

    def test_result_to_dict(self) -> None:
        result = evaluate_pack_trust(
            tier=PackTrustTier.COMMUNITY,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="READ_ONLY",
        )
        d = result.to_dict()
        assert "allowed" in d
        assert "tier" in d
        assert "violations" in d
