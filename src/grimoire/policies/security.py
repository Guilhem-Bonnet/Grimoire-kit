"""M3 — OWASP Agentic security gates, threat matrix, and pack trust tiers.

Translates OWASP Top 10 for LLM Agents and supply chain risks into
PolicyEngine-compatible refusal rules and trust tier contracts.

Threat categories (OWASP Agentic, adapted):
  LLM01 - Prompt injection (direct and indirect)
  LLM02 - Insecure output handling
  LLM03 - Training data poisoning (supply chain)
  LLM04 - Model denial of service
  LLM06 - Sensitive information disclosure
  LLM07 - Insecure plugin/skill design
  LLM08 - Excessive agency
  LLM09 - Overreliance
  LLM10 - Model theft / exfiltration

Pack trust tiers:
  UNTRUSTED  - external, unverified origin
  COMMUNITY  - community-published, no full audit
  VERIFIED   - audited, signed, doctor-passed
  INTERNAL   - first-party Grimoire packs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

__all__ = [
    "GRIMOIRE_THREAT_MATRIX",
    "PackTrustTier",
    "SecurityGate",
    "SecurityRefusalResult",
    "ThreatCategory",
    "ThreatEntry",
    "ThreatMatrix",
    "evaluate_pack_trust",
]


class ThreatCategory(StrEnum):
    PROMPT_INJECTION = "LLM01-prompt-injection"
    INSECURE_OUTPUT = "LLM02-insecure-output"
    SUPPLY_CHAIN = "LLM03-supply-chain"
    MODEL_DOS = "LLM04-model-dos"
    SENSITIVE_DISCLOSURE = "LLM06-sensitive-disclosure"
    INSECURE_PLUGIN = "LLM07-insecure-plugin"
    EXCESSIVE_AGENCY = "LLM08-excessive-agency"
    OVERRELIANCE = "LLM09-overreliance"
    MODEL_EXFILTRATION = "LLM10-model-exfiltration"


class PackTrustTier(StrEnum):
    UNTRUSTED = "untrusted"
    COMMUNITY = "community"
    VERIFIED = "verified"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ThreatEntry:
    id: str
    category: ThreatCategory
    description: str
    grimoire_mitigation: str
    implemented: bool = False
    negative_test_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "description": self.description,
            "grimoire_mitigation": self.grimoire_mitigation,
            "implemented": self.implemented,
            "negative_test_id": self.negative_test_id,
        }


@dataclass
class ThreatMatrix:
    entries: list[ThreatEntry] = field(default_factory=list)

    def by_category(self, cat: ThreatCategory) -> list[ThreatEntry]:
        return [e for e in self.entries if e.category == cat]

    def implemented(self) -> list[ThreatEntry]:
        return [e for e in self.entries if e.implemented]

    def not_implemented(self) -> list[ThreatEntry]:
        return [e for e in self.entries if not e.implemented]

    def coverage_pct(self) -> float:
        if not self.entries:
            return 0.0
        return 100.0 * len(self.implemented()) / len(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "total": len(self.entries),
            "implemented": len(self.implemented()),
            "coverage_pct": round(self.coverage_pct(), 1),
        }


# ── Pack trust tier contracts ──────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SecurityGate:
    """Contract for a given trust tier."""
    tier: PackTrustTier
    requires_doctor: bool
    requires_digest: bool
    requires_signed: bool
    max_mutation_class: str  # READ_ONLY | MUTATION_CONTROLLED | DESTRUCTIVE
    allowed_tools: tuple[str, ...] | None  # None = unrestricted

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "requires_doctor": self.requires_doctor,
            "requires_digest": self.requires_digest,
            "requires_signed": self.requires_signed,
            "max_mutation_class": self.max_mutation_class,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools is not None else None,
        }


TRUST_TIER_GATES: MappingProxyType[PackTrustTier, SecurityGate] = MappingProxyType({
    PackTrustTier.UNTRUSTED: SecurityGate(
        tier=PackTrustTier.UNTRUSTED,
        requires_doctor=True,
        requires_digest=True,
        requires_signed=False,
        max_mutation_class="READ_ONLY",
        allowed_tools=("read", "search"),
    ),
    PackTrustTier.COMMUNITY: SecurityGate(
        tier=PackTrustTier.COMMUNITY,
        requires_doctor=True,
        requires_digest=True,
        requires_signed=False,
        max_mutation_class="MUTATION_CONTROLLED",
        allowed_tools=None,
    ),
    PackTrustTier.VERIFIED: SecurityGate(
        tier=PackTrustTier.VERIFIED,
        requires_doctor=True,
        requires_digest=True,
        requires_signed=True,
        max_mutation_class="MUTATION_CONTROLLED",
        allowed_tools=None,
    ),
    PackTrustTier.INTERNAL: SecurityGate(
        tier=PackTrustTier.INTERNAL,
        requires_doctor=False,
        requires_digest=False,
        requires_signed=False,
        max_mutation_class="DESTRUCTIVE",
        allowed_tools=None,
    ),
})


@dataclass
class SecurityRefusalResult:
    allowed: bool
    tier: PackTrustTier
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "tier": self.tier.value,
            "violations": self.violations,
        }


def evaluate_pack_trust(
    *,
    tier: PackTrustTier,
    has_doctor_passed: bool,
    has_digest: bool,
    has_signature: bool,
    requested_mutation_class: str,
    requested_tools: list[str] | None = None,
) -> SecurityRefusalResult:
    """Evaluate whether a pack activation is allowed given its trust tier.

    Returns SecurityRefusalResult with violations list.
    """
    gate = TRUST_TIER_GATES[tier]
    violations: list[str] = []

    if gate.requires_doctor and not has_doctor_passed:
        violations.append(f"tier={tier.value}: pack doctor check required but not passed")
    if gate.requires_digest and not has_digest:
        violations.append(f"tier={tier.value}: content digest required but missing")
    if gate.requires_signed and not has_signature:
        violations.append(f"tier={tier.value}: signed pack required but not signed")

    # Mutation class ordering: READ_ONLY < MUTATION_CONTROLLED < DESTRUCTIVE
    _mutation_order = {"READ_ONLY": 0, "MUTATION_CONTROLLED": 1, "DESTRUCTIVE": 2}
    if requested_mutation_class not in _mutation_order:
        raise ValueError(f"Unknown mutation_class: {requested_mutation_class!r}. Valid: {list(_mutation_order)}")
    max_allowed = _mutation_order.get(gate.max_mutation_class, 0)
    requested = _mutation_order[requested_mutation_class]
    if requested > max_allowed:
        violations.append(
            f"tier={tier.value}: mutation_class={requested_mutation_class} exceeds "
            f"max_allowed={gate.max_mutation_class}"
        )

    if gate.allowed_tools is not None and requested_tools:
        blocked = [t for t in requested_tools if t not in gate.allowed_tools]
        if blocked:
            violations.append(
                f"tier={tier.value}: tools not allowed: {blocked}"
            )

    return SecurityRefusalResult(
        allowed=len(violations) == 0,
        tier=tier,
        violations=violations,
    )


# ── Canonical threat matrix for Grimoire Agent OS ────────────────────────────

GRIMOIRE_THREAT_MATRIX = ThreatMatrix(entries=[
    ThreatEntry(
        id="THR-001",
        category=ThreatCategory.PROMPT_INJECTION,
        description="Malicious content in tool outputs rewrites agent instructions",
        grimoire_mitigation="hook grimoire-control-surface-guard blocks directive injection patterns in PreToolUse",
        implemented=True,
        negative_test_id="test_control_surface_guard_blocks_injection",
    ),
    ThreatEntry(
        id="THR-002",
        category=ThreatCategory.PROMPT_INJECTION,
        description="Indirect injection via memory recall retrieves adversarial content",
        grimoire_mitigation="MemoryManager.recall() governed — promotion requires human approval, digest-only storage",
        implemented=True,
        negative_test_id="test_memory_guard_blocks_unapproved_recall",
    ),
    ThreatEntry(
        id="THR-003",
        category=ThreatCategory.EXCESSIVE_AGENCY,
        description="Agent closes task without evidence (unverified completion)",
        grimoire_mitigation="PolicyEngine rule task_close_requires_verification; NEEDS_VERIFICATION guardrail",
        implemented=True,
        negative_test_id="test_policy_blocks_task_close_without_evidence",
    ),
    ThreatEntry(
        id="THR-004",
        category=ThreatCategory.EXCESSIVE_AGENCY,
        description="External A2A task marked completed bypasses verification",
        grimoire_mitigation="A2AAdapter: completed→NEEDS_VERIFICATION (never CLOSED); guardrail documented",
        implemented=True,
        negative_test_id="test_a2a_completed_maps_to_needs_verification",
    ),
    ThreatEntry(
        id="THR-005",
        category=ThreatCategory.SUPPLY_CHAIN,
        description="Untrusted pack activates with destructive mutation class",
        grimoire_mitigation="TRUST_TIER_GATES: UNTRUSTED tier restricted to READ_ONLY + allowed_tools=(read, search)",
        implemented=True,
        negative_test_id="test_untrusted_pack_blocks_destructive_mutation",
    ),
    ThreatEntry(
        id="THR-006",
        category=ThreatCategory.SUPPLY_CHAIN,
        description="Pack installed without doctor check or content digest",
        grimoire_mitigation="evaluate_pack_trust() enforces requires_doctor + requires_digest per tier",
        implemented=True,
        negative_test_id="test_pack_trust_requires_doctor_for_community_tier",
    ),
    ThreatEntry(
        id="THR-007",
        category=ThreatCategory.SENSITIVE_DISCLOSURE,
        description="Secrets stored in evidence artifacts or A2A message content",
        grimoire_mitigation="A2AAdapter.normalize_trace: digest-only input storage; EvidenceItem: no secret fields",
        implemented=True,
        negative_test_id="test_a2a_normalize_hashes_input",
    ),
    ThreatEntry(
        id="THR-008",
        category=ThreatCategory.INSECURE_PLUGIN,
        description="Skill/hook activates without safety gate clearance",
        grimoire_mitigation="Hook gateway requires hook-safety-registry.json entry; new hooks start in shadow mode",
        implemented=True,
        negative_test_id="test_hook_gateway_blocks_unregistered_hook",
    ),
    ThreatEntry(
        id="THR-009",
        category=ThreatCategory.INSECURE_OUTPUT,
        description="Terminal command injection via unvalidated tool arguments",
        grimoire_mitigation="grimoire-terminal-guard (PreToolUse): warns on unbalanced quotes, dangerous patterns",
        implemented=True,
        negative_test_id="test_terminal_guard_warns_injection_pattern",
    ),
    ThreatEntry(
        id="THR-010",
        category=ThreatCategory.MODEL_EXFILTRATION,
        description="CrewAI/A2A trace contains sensitive reasoning or tool outputs",
        grimoire_mitigation="normalize_crewai_trace() and normalize_trace() strip/hash sensitive fields",
        implemented=True,
        negative_test_id="test_crewai_normalize_hashes_thoughts",
    ),
])
