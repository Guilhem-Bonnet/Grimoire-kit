"""Contract tests for GAME-TKT-034 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import (
    INVESTIGATION_PHASE_SEQUENCE,
    InvestigationCriticalBlockingError,
    InvestigationPhase,
    InvestigationRootCauseError,
    InvestigationWorkflow,
)


class TestGameTkt034Investigation(unittest.TestCase):
    """Contract mapping for Investigation Lab verification IDs."""

    def test_s6_t034_e2e_01(self) -> None:
        """S6-T034-E2E-01: Enchainement strict des 4 phases debug."""
        workflow = InvestigationWorkflow()

        self.assertEqual(workflow.state.phase, InvestigationPhase.DETECTION)

        workflow.advance_phase()  # detection -> root_cause
        self.assertEqual(workflow.state.phase, InvestigationPhase.ROOT_CAUSE)

        workflow.set_root_cause("Null state reused across retries")
        workflow.advance_phase()  # root_cause -> fix_proposed
        self.assertEqual(workflow.state.phase, InvestigationPhase.FIX_PROPOSED)

        workflow.set_fix_proposal("Reset retry cache before replay")
        workflow.advance_phase()  # fix_proposed -> verification
        self.assertEqual(workflow.state.phase, InvestigationPhase.VERIFICATION)
        self.assertEqual(workflow.state.phase_history, INVESTIGATION_PHASE_SEQUENCE)

    def test_s6_t034_neg_01(self) -> None:
        """S6-T034-NEG-01: Blocage FIX_PROPOSED sans root cause identifiee."""
        workflow = InvestigationWorkflow()
        workflow.advance_phase()  # detection -> root_cause

        with self.assertRaises(InvestigationRootCauseError):
            workflow.advance_phase()

        self.assertEqual(workflow.state.phase, InvestigationPhase.ROOT_CAUSE)

    def test_s6_t034_e2e_02(self) -> None:
        """S6-T034-E2E-02: Blocage progression avec critical non resolu."""
        workflow = InvestigationWorkflow()
        workflow.advance_phase()  # detection -> root_cause
        workflow.set_root_cause("Race condition in phase transition")
        workflow.advance_phase()  # root_cause -> fix_proposed
        workflow.set_fix_proposal("Serialize transition checkpoints")
        workflow.report_critical_issue()

        with self.assertRaises(InvestigationCriticalBlockingError):
            workflow.advance_phase()  # fix_proposed -> verification is blocked

        workflow.resolve_critical_issue()
        workflow.advance_phase()
        self.assertEqual(workflow.state.phase, InvestigationPhase.VERIFICATION)

    def test_s6_t034_it_01(self) -> None:
        """S6-T034-IT-01: Escalade architecture apres trois fix_failed."""
        workflow = InvestigationWorkflow()

        state = workflow.mark_fix_failed()
        self.assertEqual(state.fix_failed_count, 1)
        self.assertFalse(state.escalated)

        state = workflow.mark_fix_failed()
        self.assertEqual(state.fix_failed_count, 2)
        self.assertFalse(state.escalated)

        state = workflow.mark_fix_failed()
        self.assertEqual(state.fix_failed_count, 3)
        self.assertTrue(state.escalated)
