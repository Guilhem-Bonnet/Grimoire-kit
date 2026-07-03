"""Executable tests for GAME-TKT-029 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import AgentFactory, AgentFactoryError, SensitiveMutationError


class TestGameTkt029AgentFactory(unittest.TestCase):
    """Executable mapping for Agent Factory verification IDs."""

    def setUp(self) -> None:
        self.factory = AgentFactory()

    def test_s6_t029_e2e_01(self) -> None:
        """S6-T029-E2E-01: Creation agent valide depuis UI."""
        created = self.factory.create_agent(
            agent_id="agent-ui-01",
            name="Nova",
            archetype="architect",
            xp=12,
            history=("boot",),
            sensitive_mode=False,
        )

        self.assertEqual(created.agent_id, "agent-ui-01")
        self.assertEqual(created.name, "Nova")
        self.assertEqual(created.archetype, "architect")
        self.assertEqual(created.xp, 12)
        self.assertEqual(created.history, ("boot",))
        self.assertFalse(created.sensitive_mode)

    def test_s6_t029_e2e_02(self) -> None:
        """S6-T029-E2E-02: Clonage sans heritage XP et historique."""
        source = self.factory.create_agent(
            agent_id="agent-source",
            name="Source",
            archetype="developer",
            xp=240,
            history=("session-a", "session-b"),
            sensitive_mode=True,
        )

        clone = self.factory.clone_agent(
            source=source,
            new_agent_id="agent-clone",
            new_name="Source Clone",
        )

        self.assertEqual(clone.agent_id, "agent-clone")
        self.assertEqual(clone.name, "Source Clone")
        self.assertEqual(clone.archetype, source.archetype)
        self.assertEqual(clone.xp, 0)
        self.assertEqual(clone.history, ())
        self.assertEqual(clone.sensitive_mode, source.sensitive_mode)

        self.assertEqual(source.xp, 240)
        self.assertEqual(source.history, ("session-a", "session-b"))

    def test_s6_t029_neg_01(self) -> None:
        """S6-T029-NEG-01: Creation invalide rejectee avec erreur actionnable."""
        with self.assertRaises(AgentFactoryError) as context:
            self.factory.create_agent(agent_id="", name="", archetype="")

        message = str(context.exception)
        self.assertIn("non-empty string", message)
        self.assertIn("agent_id", message)

    def test_s6_t029_it_01(self) -> None:
        """S6-T029-IT-01: Mutation sensible bloquee sans restart confirme."""
        agent = self.factory.create_agent(
            agent_id="agent-mutate",
            name="Mutator",
            archetype="ops",
            sensitive_mode=False,
        )

        with self.assertRaises(SensitiveMutationError) as context:
            self.factory.mutate_sensitive(agent=agent, sensitive_mode=True, restart_confirmed=False)
        self.assertIn("restart confirmation", str(context.exception))

        updated = self.factory.mutate_sensitive(agent=agent, sensitive_mode=True, restart_confirmed=True)
        self.assertTrue(updated.sensitive_mode)
        self.assertFalse(agent.sensitive_mode)


if __name__ == "__main__":
    unittest.main()
