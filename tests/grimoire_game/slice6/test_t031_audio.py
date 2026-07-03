"""Executable tests for GAME-TKT-031 (Slice 6)."""

from __future__ import annotations

import unittest

from grimoire.core.game_slice6 import AudioChannel, AudioEvent, AudioSettings, AudioSystem


class TestGameTkt031Audio(unittest.TestCase):
    """Executable mapping for Audio verification IDs."""

    def setUp(self) -> None:
        self.audio = AudioSystem()

    def test_s6_t031_it_01(self) -> None:
        """S6-T031-IT-01: Declenchement SFX sur evenements cibles."""
        first = self.audio.trigger_event(AudioEvent(name="task_done", channel=AudioChannel.EFFECTS))
        second = self.audio.trigger_event("task_done", channel=AudioChannel.EFFECTS)

        self.assertTrue(first)
        self.assertFalse(second)

        drained = self.audio.drain_events()
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0].name, "task_done")
        self.assertEqual(drained[0].channel, AudioChannel.EFFECTS)

    def test_s6_t031_e2e_01(self) -> None:
        """S6-T031-E2E-01: Toggles audio independants."""
        self.audio.set_effects_enabled(False)
        self.audio.set_music_enabled(True)
        self.audio.set_voice_enabled(False)

        settings = self.audio.settings
        self.assertFalse(settings.effects_enabled)
        self.assertTrue(settings.music_enabled)
        self.assertFalse(settings.voice_enabled)

        self.assertFalse(self.audio.trigger_event("button_click", channel=AudioChannel.EFFECTS))
        self.assertTrue(self.audio.trigger_event("room_theme", channel=AudioChannel.MUSIC))
        self.assertFalse(self.audio.trigger_event("narration", channel=AudioChannel.VOICE))

    def test_s6_t031_e2e_02(self) -> None:
        """S6-T031-E2E-02: Mode mute total."""
        self.audio.set_music_enabled(True)
        self.audio.set_effects_enabled(True)
        self.audio.set_voice_enabled(True)
        self.audio.set_master_mute(True)

        self.assertFalse(self.audio.trigger_event("music_loop", channel=AudioChannel.MUSIC))
        self.assertFalse(self.audio.trigger_event("error_bip", channel=AudioChannel.EFFECTS))
        self.assertFalse(self.audio.trigger_event("voice_line", channel=AudioChannel.VOICE))
        self.assertEqual(self.audio.drain_events(), ())

    def test_s6_t031_it_02(self) -> None:
        """S6-T031-IT-02: Persistence des reglages audio apres restart."""
        self.audio.set_master_mute(False)
        self.audio.set_music_enabled(False)
        self.audio.set_effects_enabled(True)
        self.audio.set_voice_enabled(True)

        self.audio.trigger_event("task_done", channel=AudioChannel.EFFECTS)
        self.audio.trigger_event("voice_nudge", channel=AudioChannel.VOICE)

        snapshot = self.audio.persistence_state()

        restarted = AudioSystem(settings=AudioSettings())
        restarted.load_persistence_state(snapshot)

        self.assertEqual(restarted.settings.to_snapshot(), self.audio.settings.to_snapshot())

        restored_events = restarted.drain_events()
        self.assertEqual(len(restored_events), 2)
        self.assertEqual(restored_events[0].name, "task_done")
        self.assertEqual(restored_events[0].channel, AudioChannel.EFFECTS)
        self.assertEqual(restored_events[1].name, "voice_nudge")
        self.assertEqual(restored_events[1].channel, AudioChannel.VOICE)


if __name__ == "__main__":
    unittest.main()
