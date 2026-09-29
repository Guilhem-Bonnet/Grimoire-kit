"""Tests for grimoire.policies.security — OWASP threat matrix and pack trust gates."""

from __future__ import annotations

import pytest

from grimoire.policies.security import (
    GRIMOIRE_THREAT_MATRIX,
    PackTrustTier,
    SecurityGate,
    SecurityRefusalResult,
    ThreatCategory,
    ThreatEntry,
    ThreatMatrix,
    evaluate_pack_trust,
)


class TestThreatEntry:
    def test_to_dict_roundtrips_fields(self) -> None:
        entry = ThreatEntry(
            id="THR-X",
            category=ThreatCategory.PROMPT_INJECTION,
            description="desc",
            grimoire_mitigation="mitigation",
            implemented=True,
            negative_test_id="test_x",
            mitigation_ref="pkg.mod:Thing",
        )
        assert entry.to_dict() == {
            "id": "THR-X",
            "category": "LLM01-prompt-injection",
            "description": "desc",
            "grimoire_mitigation": "mitigation",
            "implemented": True,
            "negative_test_id": "test_x",
            "mitigation_ref": "pkg.mod:Thing",
        }

    def test_defaults(self) -> None:
        entry = ThreatEntry(
            id="THR-Y",
            category=ThreatCategory.SUPPLY_CHAIN,
            description="d",
            grimoire_mitigation="m",
        )
        assert entry.implemented is False
        assert entry.negative_test_id == ""
        assert entry.mitigation_ref == ""


class TestThreatMatrix:
    def _matrix(self) -> ThreatMatrix:
        return ThreatMatrix(
            entries=[
                ThreatEntry("A", ThreatCategory.PROMPT_INJECTION, "d", "m", implemented=True),
                ThreatEntry("B", ThreatCategory.PROMPT_INJECTION, "d", "m", implemented=False),
                ThreatEntry("C", ThreatCategory.EXCESSIVE_AGENCY, "d", "m", implemented=True),
            ]
        )

    def test_by_category_filters(self) -> None:
        m = self._matrix()
        injection = m.by_category(ThreatCategory.PROMPT_INJECTION)
        assert {e.id for e in injection} == {"A", "B"}
        assert [e.id for e in m.by_category(ThreatCategory.EXCESSIVE_AGENCY)] == ["C"]

    def test_implemented_and_not_implemented_partition(self) -> None:
        m = self._matrix()
        assert {e.id for e in m.implemented()} == {"A", "C"}
        assert {e.id for e in m.not_implemented()} == {"B"}

    def test_coverage_pct(self) -> None:
        m = self._matrix()
        assert m.coverage_pct() == pytest.approx(2 / 3 * 100.0)

    def test_coverage_pct_empty_matrix_is_zero(self) -> None:
        assert ThreatMatrix().coverage_pct() == 0.0

    def test_to_dict_summary(self) -> None:
        d = self._matrix().to_dict()
        assert d["total"] == 3
        assert d["implemented"] == 2
        assert d["coverage_pct"] == 66.7
        assert len(d["entries"]) == 3


class TestGrimoireThreatMatrix:
    def test_canonical_matrix_has_two_honestly_unimplemented_entries(self) -> None:
        # THR-008 and THR-009 named a hook gateway and a terminal guard that
        # never shipped in src/grimoire (Forge-only tooling, or nothing at
        # all) — see tests/security/test_threat_matrix_honesty.py, which is
        # what now keeps this number honest instead of a hand assertion.
        assert len(GRIMOIRE_THREAT_MATRIX.entries) == 10
        assert {e.id for e in GRIMOIRE_THREAT_MATRIX.not_implemented()} == {"THR-008", "THR-009"}
        assert GRIMOIRE_THREAT_MATRIX.coverage_pct() == 80.0

    def test_every_entry_has_a_well_formed_id(self) -> None:
        for entry in GRIMOIRE_THREAT_MATRIX.entries:
            assert entry.id.startswith("THR-")

    def test_implemented_entries_declare_a_negative_test_and_a_mitigation_ref(self) -> None:
        # The real existence/import checks live in
        # tests/security/test_threat_matrix_honesty.py; this only checks the
        # fields are populated (shape), not that they resolve (substance).
        for entry in GRIMOIRE_THREAT_MATRIX.implemented():
            assert entry.negative_test_id.startswith("test_")
            assert entry.mitigation_ref, f"{entry.id}: implemented=True with no mitigation_ref"

    def test_not_implemented_entries_declare_no_test_id(self) -> None:
        for entry in GRIMOIRE_THREAT_MATRIX.not_implemented():
            assert entry.negative_test_id == ""


class TestSecurityGate:
    def test_to_dict_with_restricted_tools(self) -> None:
        gate = SecurityGate(
            tier=PackTrustTier.UNTRUSTED,
            requires_doctor=True,
            requires_digest=True,
            requires_signed=False,
            max_mutation_class="READ_ONLY",
            allowed_tools=("read", "search"),
        )
        d = gate.to_dict()
        assert d["tier"] == "untrusted"
        assert d["allowed_tools"] == ["read", "search"]

    def test_to_dict_unrestricted_tools_is_none(self) -> None:
        gate = SecurityGate(
            tier=PackTrustTier.INTERNAL,
            requires_doctor=False,
            requires_digest=False,
            requires_signed=False,
            max_mutation_class="DESTRUCTIVE",
            allowed_tools=None,
        )
        assert gate.to_dict()["allowed_tools"] is None


class TestSecurityRefusalResult:
    def test_to_dict(self) -> None:
        res = SecurityRefusalResult(
            allowed=False, tier=PackTrustTier.COMMUNITY, violations=["v1"]
        )
        assert res.to_dict() == {
            "allowed": False,
            "tier": "community",
            "violations": ["v1"],
        }


class TestEvaluatePackTrust:
    def test_internal_tier_allows_destructive(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.INTERNAL,
            has_doctor_passed=False,
            has_digest=False,
            has_signature=False,
            requested_mutation_class="DESTRUCTIVE",
        )
        assert res.allowed is True
        assert res.violations == []

    def test_verified_tier_happy_path(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.VERIFIED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=True,
            requested_mutation_class="MUTATION_CONTROLLED",
            requested_tools=["read", "write"],
        )
        assert res.allowed is True

    def test_untrusted_blocks_destructive_and_missing_proofs(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.UNTRUSTED,
            has_doctor_passed=False,
            has_digest=False,
            has_signature=False,
            requested_mutation_class="DESTRUCTIVE",
            requested_tools=["write"],
        )
        assert res.allowed is False
        joined = " ".join(res.violations)
        assert "doctor check required" in joined
        assert "content digest required" in joined
        assert "mutation_class=DESTRUCTIVE" in joined
        assert "tools not allowed" in joined

    def test_verified_requires_signature(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.VERIFIED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="READ_ONLY",
        )
        assert res.allowed is False
        assert any("signed pack required" in v for v in res.violations)

    def test_community_doctor_required(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.COMMUNITY,
            has_doctor_passed=False,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="MUTATION_CONTROLLED",
        )
        assert res.allowed is False
        assert any("doctor check required" in v for v in res.violations)

    def test_untrusted_allowed_tools_subset_passes(self) -> None:
        res = evaluate_pack_trust(
            tier=PackTrustTier.UNTRUSTED,
            has_doctor_passed=True,
            has_digest=True,
            has_signature=False,
            requested_mutation_class="READ_ONLY",
            requested_tools=["read", "search"],
        )
        assert res.allowed is True

    def test_invalid_mutation_class_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown mutation_class"):
            evaluate_pack_trust(
                tier=PackTrustTier.INTERNAL,
                has_doctor_passed=True,
                has_digest=True,
                has_signature=True,
                requested_mutation_class="NUCLEAR",
            )
