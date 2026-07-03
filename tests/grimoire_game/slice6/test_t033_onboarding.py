"""Contract tests for GAME-TKT-033 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import OnboardingFlow, OnboardingFlowError, OnboardingState


class TestGameTkt033Onboarding(unittest.TestCase):
    """Contract mapping for onboarding verification IDs."""

    def test_s6_t033_e2e_01(self) -> None:
        """S6-T033-E2E-01: Lancement automatique au premier demarrage."""
        first_run_flow = OnboardingFlow()

        self.assertTrue(first_run_flow.state.started)
        self.assertFalse(first_run_flow.state.completed)
        self.assertEqual(first_run_flow.current_step(), "welcome")

        subsequent_run_flow = OnboardingFlow(
            state=OnboardingState(started=False, completed=True, skipped_permanently=False)
        )
        self.assertFalse(subsequent_run_flow.state.started)
        self.assertIsNone(subsequent_run_flow.current_step())

    def test_s6_t033_e2e_02(self) -> None:
        """S6-T033-E2E-02: Skip definitif sans relance automatique."""
        flow = OnboardingFlow()
        skipped_state = flow.skip_permanently()

        self.assertTrue(skipped_state.skipped_permanently)
        self.assertTrue(skipped_state.completed)
        self.assertFalse(skipped_state.started)
        self.assertIsNone(flow.current_step())

        restarted_flow = OnboardingFlow()
        restarted_flow.load_persistence_state(flow.persistence_state())
        self.assertTrue(restarted_flow.state.skipped_permanently)
        self.assertIsNone(restarted_flow.current_step())

    def test_s6_t033_e2e_03(self) -> None:
        """S6-T033-E2E-03: Reprise de l'etape onboarding apres interruption."""
        interrupted_flow = OnboardingFlow()
        interrupted_flow.advance()  # Move to "controls".
        interrupted_flow.advance()  # Move to "first-investigation".

        snapshot = interrupted_flow.persistence_state()
        resumed_flow = OnboardingFlow()
        resumed_flow.load_persistence_state(snapshot)
        resumed_state = resumed_flow.resume_step()

        self.assertEqual(resumed_state.current_step_index, 2)
        self.assertTrue(resumed_state.started)
        self.assertEqual(resumed_flow.current_step(), "first-investigation")

    def test_s6_t033_neg_01(self) -> None:
        """S6-T033-NEG-01: Detection relance non voulue ou perte d'etat."""
        skipped_flow = OnboardingFlow()
        skipped_flow.skip_permanently()

        fresh_run = OnboardingFlow()
        fresh_run.load_persistence_state(skipped_flow.persistence_state())
        self.assertIsNone(fresh_run.current_step())

        corrupted_snapshot = {
            "steps": ["welcome", "controls"],
            "current_step_index": 5,
            "started": True,
            "completed": False,
            "skipped_permanently": False,
        }
        with self.assertRaises(OnboardingFlowError):
            fresh_run.load_persistence_state(corrupted_snapshot)
