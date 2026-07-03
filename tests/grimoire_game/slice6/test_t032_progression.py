"""Executable tests for GAME-TKT-032 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import DEFAULT_XP_PER_LEVEL, DoubleCreditError, ProgressionEngine, ProgressState


class TestGameTkt032Progression(unittest.TestCase):
    """Executable mapping for XP and achievements verification IDs."""

    def setUp(self) -> None:
        self.engine = ProgressionEngine()

    def test_s6_t032_ut_01(self) -> None:
        """S6-T032-UT-01: Attribution XP par action eligibile."""
        before = self.engine.state
        after = self.engine.award_xp(action_id="action-ut-01", amount=35)

        self.assertEqual(after.total_xp, before.total_xp + 35)
        self.assertIn("action-ut-01", after.credited_actions)
        self.assertEqual(after.level, self.engine.level_for_xp(after.total_xp))

    def test_s6_t032_ut_02(self) -> None:
        """S6-T032-UT-02: Calcul niveau deterministic."""
        level_a = self.engine.level_for_xp(250)
        level_b = self.engine.level_for_xp(250)
        expected = 1 + (250 // DEFAULT_XP_PER_LEVEL)

        self.assertEqual(level_a, level_b)
        self.assertEqual(level_a, expected)
        self.assertGreaterEqual(self.engine.level_for_xp(400), level_a)

    def test_s6_t032_neg_01(self) -> None:
        """S6-T032-NEG-01: Protection contre credit XP en double."""
        first = self.engine.award_xp(action_id="dup-action", amount=20)
        with self.assertRaises(DoubleCreditError):
            self.engine.award_xp(action_id="dup-action", amount=20)

        self.assertEqual(self.engine.state.total_xp, first.total_xp)
        self.assertEqual(self.engine.state.level, first.level)

    def test_s6_t032_it_01(self) -> None:
        """S6-T032-IT-01: Persistence XP et achievements apres restart."""
        self.engine.award_xp(action_id="persist-1", amount=40)
        self.engine.award_xp(action_id="persist-2", amount=70)

        before = self.engine.state
        snapshot = self.engine.persistence_state()

        restarted = ProgressionEngine(state=ProgressState())
        restarted.load_persistence_state(snapshot)
        after = restarted.state

        self.assertEqual(after.total_xp, before.total_xp)
        self.assertEqual(after.level, before.level)
        self.assertEqual(after.credited_actions, before.credited_actions)
        self.assertEqual(after.level, restarted.level_for_xp(after.total_xp))


if __name__ == "__main__":
    unittest.main()
