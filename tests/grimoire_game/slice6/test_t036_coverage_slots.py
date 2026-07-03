"""Contract tests for GAME-TKT-036 (Slice 6)."""

from __future__ import annotations

import unittest
from pathlib import Path

from grimoire.core.game_slice6 import (
    DeskDirectoryMap,
    DeskDirectoryMapError,
    GridPosition,
    MapConstraintError,
    MapEditor,
    MapReadOnlyError,
    RoomStatus,
    RoomTransitionError,
    WorktreeRoomLifecycle,
)


class TestGameTkt036CoverageSlots(unittest.TestCase):
    """Contract mapping for remaining CdC slot coverage IDs."""

    def test_s6_t036_e2e_01(self) -> None:
        """S6-T036-E2E-01: Editeur map et contraintes de grille."""
        editor = MapEditor(width=3, height=3)

        editor.place_desk(desk_id="desk-a", position=GridPosition(x=0, y=0))
        editor.move_desk(desk_id="desk-a", position=GridPosition(x=1, y=0))
        self.assertEqual(editor.snapshot()["desk-a"], (1, 0))

        self.assertTrue(editor.undo())
        self.assertEqual(editor.snapshot()["desk-a"], (0, 0))

        self.assertTrue(editor.redo())
        self.assertEqual(editor.snapshot()["desk-a"], (1, 0))

        with self.assertRaises(MapConstraintError):
            editor.place_desk(desk_id="desk-b", position=GridPosition(x=3, y=0))

    def test_s6_t036_sec_01(self) -> None:
        """S6-T036-SEC-01: Verification negative read-only spectateur."""
        editor = MapEditor(width=2, height=2, read_only=True)

        with self.assertRaises(MapReadOnlyError):
            editor.place_desk(desk_id="desk-spectator", position=GridPosition(x=0, y=0))

        editor.set_read_only(False)
        editor.place_desk(desk_id="desk-spectator", position=GridPosition(x=0, y=0))
        editor.set_read_only(True)

        with self.assertRaises(MapReadOnlyError):
            editor.move_desk(desk_id="desk-spectator", position=GridPosition(x=1, y=0))

    def test_s6_t036_it_01(self) -> None:
        """S6-T036-IT-01: Mapping desk->directory persistant et non ambigu."""
        map_store = DeskDirectoryMap()
        map_store.bind(desk_id="desk-a", directory=Path("/tmp/grimoire-desk-a"))
        map_store.bind(desk_id="desk-b", directory=Path("/tmp/grimoire-desk-b"))

        snapshot = map_store.snapshot()
        restored = DeskDirectoryMap(entries=snapshot)

        self.assertEqual(
            restored.directory_for_desk("desk-a"),
            Path("/tmp/grimoire-desk-a").expanduser().resolve(strict=False),
        )
        self.assertEqual(restored.desk_for_directory(Path("/tmp/grimoire-desk-b")), "desk-b")

        with self.assertRaises(DeskDirectoryMapError):
            restored.bind(desk_id="desk-c", directory=Path("/tmp/grimoire-desk-b"))

        with self.assertRaises(DeskDirectoryMapError):
            restored.bind(desk_id="desk-a", directory=Path("/tmp/grimoire-desk-a-v2"))

    def test_s6_t036_e2e_02(self) -> None:
        """S6-T036-E2E-02: Lifecycle complet des worktree rooms."""
        lifecycle = WorktreeRoomLifecycle()

        created = lifecycle.create_room(room_id="feature-auth", directory=Path("/tmp/worktree-auth"))
        self.assertEqual(created.status, RoomStatus.CREATED)

        active = lifecycle.activate_room(room_id="feature-auth")
        self.assertEqual(active.status, RoomStatus.ACTIVE)

        archived = lifecycle.archive_room(room_id="feature-auth")
        self.assertEqual(archived.status, RoomStatus.ARCHIVED)

        closed = lifecycle.close_room(room_id="feature-auth")
        self.assertEqual(closed.status, RoomStatus.CLOSED)

        with self.assertRaises(RoomTransitionError):
            lifecycle.activate_room(room_id="feature-auth")
