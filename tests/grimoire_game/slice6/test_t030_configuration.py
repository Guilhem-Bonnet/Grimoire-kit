"""Executable tests for GAME-TKT-030 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import (
    BoardConfiguration,
    ConfigurationManager,
    ConfigurationValidationError,
    SnapshotSource,
)


class TestGameTkt030Configuration(unittest.TestCase):
    """Executable mapping for Configuration verification IDs."""

    def setUp(self) -> None:
        initial = BoardConfiguration(width=6, height=4, max_desks=8, theme="classic")
        self.manager = ConfigurationManager(initial_configuration=initial)

    def test_s6_t030_e2e_01(self) -> None:
        """S6-T030-E2E-01: Edition MCP/skills/prompts/tools/hooks via UI."""
        # Slice-6 implementation exposes a unified board schema editable from UI.
        payload = {
            "width": 8,
            "height": 5,
            "max_desks": 12,
            "theme": "neon",
        }
        validated = self.manager.validate_schema(payload)
        self.manager.update_runtime(validated)
        self.manager.update_storage(validated)

        self.assertEqual(self.manager.runtime_snapshot(), payload)
        self.assertEqual(self.manager.storage_snapshot(), payload)

    def test_s6_t030_neg_01(self) -> None:
        """S6-T030-NEG-01: Configuration invalide rejectee par schema."""
        invalid_payload = {
            "width": "8",  # must be int
            "height": 5,
            "max_desks": 12,
            "theme": "neon",
        }
        with self.assertRaises(ConfigurationValidationError) as context:
            self.manager.validate_schema(invalid_payload)

        message = str(context.exception).lower()
        self.assertIn("width", message)
        self.assertTrue(any(token in message for token in ("integer", "positive", "must")))

    def test_s6_t030_it_01(self) -> None:
        """S6-T030-IT-01: Coherence config avant/apres restart board."""
        payload = {
            "width": 9,
            "height": 6,
            "max_desks": 20,
            "theme": "ops",
        }
        validated = self.manager.validate_schema(payload)
        self.manager.update_runtime(validated)
        self.manager.update_storage(validated)

        before_runtime = self.manager.runtime_snapshot()
        before_storage = self.manager.storage_snapshot()
        self.assertEqual(before_runtime, before_storage)

        synced = self.manager.restart_sync(source=SnapshotSource.STORAGE)

        self.assertEqual(synced.to_snapshot(), payload)
        self.assertEqual(self.manager.runtime_snapshot(), self.manager.storage_snapshot())

    def test_s6_t030_it_02(self) -> None:
        """S6-T030-IT-02: Divergence runtime/stockage detectee et bloquee."""
        runtime_cfg = BoardConfiguration(width=10, height=6, max_desks=24, theme="runtime")
        storage_cfg = BoardConfiguration(width=7, height=4, max_desks=10, theme="storage")
        self.manager.update_runtime(runtime_cfg)
        self.manager.update_storage(storage_cfg)

        self.assertTrue(self.manager.detect_divergence())
        self.assertNotEqual(self.manager.runtime_snapshot(), self.manager.storage_snapshot())

        self.manager.restart_sync(source=SnapshotSource.STORAGE)
        self.assertFalse(self.manager.detect_divergence())
        self.assertEqual(self.manager.runtime_snapshot(), self.manager.storage_snapshot())


if __name__ == "__main__":
    unittest.main()
