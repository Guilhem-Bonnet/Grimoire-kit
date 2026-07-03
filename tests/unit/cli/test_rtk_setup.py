"""Tests for grimoire.cli.rtk_setup — RTK install/hook helpers (public API only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.cli import rtk_setup


def test_find_rtk_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtk_setup.shutil, "which", lambda _name: "/usr/bin/rtk")
    assert rtk_setup.find_rtk() == "/usr/bin/rtk"


def test_find_rtk_falls_back_to_cargo_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rtk_setup.shutil, "which", lambda _name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    cargo_bin = tmp_path / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    (cargo_bin / "rtk").write_text("#!/bin/sh\n")
    assert rtk_setup.find_rtk() == str(cargo_bin / "rtk")


def test_find_rtk_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rtk_setup.shutil, "which", lambda _name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rtk_setup.find_rtk() is None


def test_activate_claude_hook_creates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, _msg = rtk_setup.activate_claude_hook("/opt/rtk")
    assert ok is True
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    pre = settings["hooks"]["PreToolUse"]
    assert any("/opt/rtk hook claude" in h["command"] for entry in pre for h in entry["hooks"])


def test_activate_claude_hook_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rtk_setup.activate_claude_hook("/opt/rtk")
    rtk_setup.activate_claude_hook("/opt/rtk")
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    pre = settings["hooks"]["PreToolUse"]
    commands = [h["command"] for entry in pre for h in entry["hooks"]]
    assert commands.count("/opt/rtk hook claude") == 1


def test_activate_claude_hook_preserves_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"model": "opus"}))
    rtk_setup.activate_claude_hook("/opt/rtk")
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"]


def test_setup_rtk_absent_no_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtk_setup, "find_rtk", lambda: None)
    result = rtk_setup.setup_rtk(allow_install=False)
    assert result.present is False
    assert result.installed_now is False
    assert result.hook_activated is False


def test_setup_rtk_present_activates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rtk_setup, "find_rtk", lambda: "/opt/rtk")
    monkeypatch.setenv("HOME", str(tmp_path))
    result = rtk_setup.setup_rtk(allow_install=False)
    assert result.present is True
    assert result.installed_now is False
    assert result.hook_activated is True
    assert result.rtk_path == "/opt/rtk"
    assert result.to_dict()["rtk_path"] == "/opt/rtk"
