"""Claude Code activation hook — install semantics and fail-safety.

The SessionStart activation mechanism was validated 40/40 by the
2026-07-09 evals campaign; these tests pin the product integration:
idempotent install, non-destructive merge into existing settings, and
byte-for-byte preservation of malformed settings files.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.core.claude_activation import (
    ACTIVATION_CONTEXT_RELPATH,
    HOOK_COMMAND,
    SETTINGS_RELPATH,
    activation_context_text,
    default_activation_directive,
    install_claude_activation,
)


def _settings(root: Path) -> dict[str, object]:
    return json.loads((root / SETTINGS_RELPATH).read_text(encoding="utf-8"))


def _session_start_commands(root: Path) -> list[str]:
    data = _settings(root)
    hooks = data["hooks"]
    assert isinstance(hooks, dict)
    commands: list[str] = []
    for entry in hooks["SessionStart"]:
        for hook in entry["hooks"]:
            commands.append(hook["command"])
    return commands


def test_install_on_pristine_project(tmp_path: Path) -> None:
    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    assert ACTIVATION_CONTEXT_RELPATH in result.written
    assert SETTINGS_RELPATH in result.written
    assert _session_start_commands(tmp_path) == [HOOK_COMMAND]
    directive = (tmp_path / ACTIVATION_CONTEXT_RELPATH).read_text(encoding="utf-8")
    assert "[Grimoire Standard — activation]" in directive
    assert "gate check --task-id bootstrap --strict" in directive


def test_install_is_idempotent(tmp_path: Path) -> None:
    install_claude_activation(tmp_path)
    before = (tmp_path / SETTINGS_RELPATH).read_text(encoding="utf-8")
    result = install_claude_activation(tmp_path)
    assert result.status == "already-installed"
    assert result.written == []
    assert (tmp_path / SETTINGS_RELPATH).read_text(encoding="utf-8") == before
    assert _session_start_commands(tmp_path) == [HOOK_COMMAND]


def test_merge_preserves_existing_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    existing = {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "echo post"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}],
        },
    }
    settings_path.write_text(json.dumps(existing), encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    data = _settings(tmp_path)
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    hooks = data["hooks"]
    assert isinstance(hooks, dict)
    assert hooks["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert _session_start_commands(tmp_path) == ["echo hello", HOOK_COMMAND]


def test_malformed_settings_left_untouched(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    malformed = '{"hooks": [broken'
    settings_path.write_text(malformed, encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "skipped-invalid-settings"
    assert settings_path.read_text(encoding="utf-8") == malformed
    assert result.message


def test_unexpected_shapes_left_untouched(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    for payload in ('["list-root"]', '{"hooks": "oops"}', '{"hooks": {"SessionStart": "oops"}}'):
        settings_path.write_text(payload, encoding="utf-8")
        result = install_claude_activation(tmp_path)
        assert result.status == "skipped-invalid-settings"
        assert settings_path.read_text(encoding="utf-8") == payload


def test_custom_context_file_is_never_overwritten(tmp_path: Path) -> None:
    context_path = tmp_path / ACTIVATION_CONTEXT_RELPATH
    context_path.parent.mkdir(parents=True)
    context_path.write_text("directive maison\n", encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    assert ACTIVATION_CONTEXT_RELPATH not in result.written
    assert context_path.read_text(encoding="utf-8") == "directive maison\n"
    assert activation_context_text(tmp_path) == "directive maison\n"


def test_directive_follows_task_id(tmp_path: Path) -> None:
    install_claude_activation(tmp_path, task_id="sprint-7")
    directive = (tmp_path / ACTIVATION_CONTEXT_RELPATH).read_text(encoding="utf-8")
    assert "evidence/sprint-7/task-envelope.md" in directive
    assert "gate check --task-id sprint-7 --strict" in directive
    assert activation_context_text(tmp_path, task_id="ignored") == directive


def test_default_directive_matches_preregistered_mechanism() -> None:
    directive = default_activation_directive()
    for anchor in (
        "task-envelope.md",
        "evidence-pack.md",
        "grimoire standard gate check --task-id bootstrap --strict",
        "grimoire standard verify .",
    ):
        assert anchor in directive
