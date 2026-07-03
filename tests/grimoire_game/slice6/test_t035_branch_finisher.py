"""Contract tests for GAME-TKT-035 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import (
    BranchFinisher,
    BranchFinishOption,
    CriticalSecurityBlockingError,
    DiscardReason,
    DiscardReasonRequiredError,
    SecurityAudit,
    SecurityFinding,
    SecuritySeverity,
)


class TestGameTkt035BranchFinisher(unittest.TestCase):
    """Contract mapping for Branch Finisher and Security Audit IDs."""

    def test_s6_t035_e2e_01(self) -> None:
        """S6-T035-E2E-01: Cycle complet options merge, pr, keep, discard."""
        finisher = BranchFinisher()

        clean_audit = SecurityAudit()
        merge_decision = finisher.finalize(option=BranchFinishOption.MERGE, audit=clean_audit)
        pr_decision = finisher.finalize(option=BranchFinishOption.PR, audit=clean_audit)

        review_only_audit = SecurityAudit(
            findings=[
                SecurityFinding(
                    finding_id="SEC-LOW-01",
                    title="Missing optional CSP directive",
                    severity=SecuritySeverity.LOW,
                    resolved=False,
                )
            ]
        )
        keep_decision = finisher.finalize(option=BranchFinishOption.KEEP, audit=review_only_audit)

        discard_decision = finisher.finalize(
            option=BranchFinishOption.DISCARD,
            audit=clean_audit,
            discard_reason=DiscardReason.OBSOLETE,
        )

        self.assertEqual(merge_decision.option, BranchFinishOption.MERGE)
        self.assertTrue(merge_decision.ship_allowed)
        self.assertEqual(pr_decision.option, BranchFinishOption.PR)
        self.assertTrue(pr_decision.ship_allowed)

        self.assertEqual(keep_decision.option, BranchFinishOption.KEEP)
        self.assertFalse(keep_decision.ship_allowed)
        self.assertEqual(len(keep_decision.generated_security_tickets), 1)

        self.assertEqual(discard_decision.option, BranchFinishOption.DISCARD)
        self.assertFalse(discard_decision.ship_allowed)
        self.assertEqual(discard_decision.discard_reason, DiscardReason.OBSOLETE)

    def test_s6_t035_neg_01(self) -> None:
        """S6-T035-NEG-01: Rejet action destructive sans typed discard."""
        finisher = BranchFinisher()
        audit = SecurityAudit()

        with self.assertRaises(DiscardReasonRequiredError):
            finisher.finalize(option=BranchFinishOption.DISCARD, audit=audit)

        decision = finisher.finalize(
            option=BranchFinishOption.DISCARD,
            audit=audit,
            discard_reason=DiscardReason.ABANDONED_EXPERIMENT,
        )
        self.assertEqual(decision.discard_reason, DiscardReason.ABANDONED_EXPERIMENT)

    def test_s6_t035_sec_01(self) -> None:
        """S6-T035-SEC-01: Blocage ship en presence d'un finding critical."""
        finisher = BranchFinisher()
        audit = SecurityAudit(
            findings=[
                SecurityFinding(
                    finding_id="SEC-CRIT-01",
                    title="RCE through unsanitized plugin chain",
                    severity=SecuritySeverity.CRITICAL,
                    resolved=False,
                )
            ]
        )

        self.assertTrue(audit.has_critical_blocker())
        with self.assertRaises(CriticalSecurityBlockingError):
            finisher.finalize(option=BranchFinishOption.MERGE, audit=audit)

    def test_s6_t035_it_01(self) -> None:
        """S6-T035-IT-01: Generation automatique de tickets securite."""
        finisher = BranchFinisher()
        audit = SecurityAudit(
            findings=[
                SecurityFinding(
                    finding_id="SEC-001",
                    title="Missing rate limit in endpoint",
                    severity=SecuritySeverity.HIGH,
                    resolved=False,
                ),
                SecurityFinding(
                    finding_id="SEC-002",
                    title="Potential info leak in logs",
                    severity=SecuritySeverity.MEDIUM,
                    resolved=False,
                ),
            ]
        )

        decision = finisher.finalize(option=BranchFinishOption.KEEP, audit=audit)

        self.assertEqual(len(decision.generated_security_tickets), 2)
        self.assertEqual(decision.generated_security_tickets[0].ticket_id, "SEC-0001")
        self.assertEqual(decision.generated_security_tickets[1].ticket_id, "SEC-0002")
        self.assertEqual(decision.generated_security_tickets[0].finding_id, "SEC-001")
        self.assertEqual(decision.generated_security_tickets[1].finding_id, "SEC-002")
