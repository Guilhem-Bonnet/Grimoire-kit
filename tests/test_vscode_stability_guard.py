"""Tests for vscode-stability-guard.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Import module with hyphenated filename.
_TOOL_PATH = (
    Path(__file__).resolve().parent.parent / "framework" / "tools" / "vscode-stability-guard.py"
)
_spec = importlib.util.spec_from_file_location("vscode_stability_guard", _TOOL_PATH)
vscode_stability_guard = importlib.util.module_from_spec(_spec)
sys.modules["vscode_stability_guard"] = vscode_stability_guard
_spec.loader.exec_module(vscode_stability_guard)


class TestSettingsApply:
    def test_apply_creates_settings_file(self, tmp_path: Path):
        settings_path, created, updates = vscode_stability_guard.apply_stability_settings(tmp_path)

        assert created is True
        assert settings_path.exists()
        # STABILITY_SETTINGS + 1 for the dynamic "task.allowAutomaticTasks" setting
        assert len(updates) == len(vscode_stability_guard.STABILITY_SETTINGS) + 1

        content = settings_path.read_text(encoding="utf-8")
        assert '"terminal.integrated.enablePersistentSessions": false' in content
        assert '"terminal.integrated.persistentSessionReviveProcess": "never"' in content

    def test_apply_updates_existing_value_preserving_comments(self, tmp_path: Path):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir(parents=True)
        settings_path = vscode_dir / "settings.json"
        settings_path.write_text(
            """{
  // keep me
  \"terminal.integrated.scrollback\": 9999,
  \"editor.rulers\": [80]
}
""",
            encoding="utf-8",
        )

        _, created, updates = vscode_stability_guard.apply_stability_settings(tmp_path)

        assert created is False
        assert any(delta.key == "terminal.integrated.scrollback" for delta in updates)
        updated = settings_path.read_text(encoding="utf-8")
        assert "// keep me" in updated
        assert '"terminal.integrated.scrollback": 2000' in updated
        assert '"editor.rulers": [80]' in updated

    def test_apply_idempotent_second_run(self, tmp_path: Path):
        vscode_stability_guard.apply_stability_settings(tmp_path)
        _, _, updates = vscode_stability_guard.apply_stability_settings(tmp_path)
        assert updates == []


class TestSettingsCheck:
    def test_check_missing_when_file_absent(self, tmp_path: Path):
        _, missing = vscode_stability_guard.check_stability_settings(tmp_path)
        # STABILITY_SETTINGS + dynamic "task.allowAutomaticTasks"
        expected_keys = set(vscode_stability_guard.STABILITY_SETTINGS) | {"task.allowAutomaticTasks"}
        assert set(missing) == expected_keys

    def test_check_ok_after_apply(self, tmp_path: Path):
        vscode_stability_guard.apply_stability_settings(tmp_path)
        _, missing = vscode_stability_guard.check_stability_settings(tmp_path)
        assert missing == []


class TestRuntimeParsing:
    def test_parse_code_status_counts_processes(self):
        sample = """
    0  38912   pty-host
    0  41077     /usr/bin/zsh
    0  62069     /usr/bin/zsh -i
    0  661249    /usr/bin/zsh -f
    0  40035   extension-host [2]
    1  487404  extension-host [3]
"""
        snapshot = vscode_stability_guard.parse_code_status_text(sample)

        assert snapshot.pty_hosts == 1
        assert snapshot.zsh_total == 3
        assert snapshot.zsh_interactive == 1
        assert snapshot.zsh_safe == 1
        assert snapshot.extension_hosts == 2
