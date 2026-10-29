"""I3 — l'ajout au ledger écrit en fin de fichier, sans réécriture, et n'en laisse rien s'il échoue (non chronométré)."""

from __future__ import annotations

import errno
import os
from typing import TYPE_CHECKING, Any

import pytest

import grimoire.core.fsio as fsio
import grimoire.missions.ledger as ledger_module
from grimoire.missions.ledger import MissionLedger

if TYPE_CHECKING:
    from pathlib import Path


def _no_rewrite(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("atomic_write appelé : l'ajout réécrit le journal au lieu d'ajouter en fin")


def test_append_keeps_inode_and_never_rewrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = MissionLedger(tmp_path / "l")
    ledger.create_mission("avant", origin="user")
    path = tmp_path / "l" / "events.jsonl"
    inode = path.stat().st_ino
    monkeypatch.setattr(fsio, "atomic_write", _no_rewrite)
    monkeypatch.setattr(ledger_module, "atomic_write", _no_rewrite, raising=False)

    ledger.append_event("note.added", "T-1", "task", "a", {"i": 1})

    assert path.stat().st_ino == inode


def test_failed_fsync_leaves_no_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = MissionLedger(tmp_path / "l")
    ledger.create_mission("avant", origin="user")
    path = tmp_path / "l" / "events.jsonl"
    size = path.stat().st_size

    def eio(_fd: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "fsync", eio)
    with pytest.raises(OSError):
        ledger.append_event("note.added", "T-1", "task", "a", {"i": 1})
    monkeypatch.undo()

    assert path.stat().st_size == size
    fresh = MissionLedger(tmp_path / "l")
    fresh.list_missions()
    assert all(e.event_type != "note.added" for e in fresh._events)
    assert fresh.unreadable_lines() == ()
