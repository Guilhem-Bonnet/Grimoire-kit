"""``sync_host_surfaces(only=…)`` : une demande explicite prime sur la détection (#957)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts import sync
from grimoire.hosts.sync import sync_host_surfaces


@pytest.fixture
def sans_hote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un worktree dont le contexte projet n'est pas suivi : aucun hôte déclaré ni détecté."""
    monkeypatch.setattr(sync, "enabled_host_ids", lambda _root: frozenset())
    return tmp_path


def test_only_pose_les_hooks_meme_sans_hote_detecte(sans_hote: Path) -> None:
    outcome = sync_host_surfaces(sans_hote, only=frozenset({HostId.CLAUDE_CODE_CLI}))

    assert not outcome.error
    settings = json.loads((sans_hote / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["PreToolUse"]


def test_sans_only_la_detection_reste_seule_juge(sans_hote: Path) -> None:
    sync_host_surfaces(sans_hote)

    assert not (sans_hote / ".claude" / "settings.json").exists()
