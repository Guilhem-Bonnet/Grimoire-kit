"""La commande de hook émise doit se lancer dans l'environnement de l'hôte (issue #760).

Sous Windows, un kit installé par ``uv pip install`` dans un venv laissait des
hooks ``grimoire-hook --host copilot …`` que VS Code ne trouvait pas : le venv
n'est dans le PATH que d'un shell où il est activé. Ces tests figent la
résolution (nom nu, chemin relatif au projet, chemin absolu), ce qu'en font les
deux émetteurs, et le contrôle de ``grimoire doctor``.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from grimoire.cli.doctor_hooks import check_hook_commands
from grimoire.hosts.emitters.base import Emitter
from grimoire.hosts.emitters.claude_code import _hook_entry
from grimoire.hosts.emitters.copilot import _hook_file
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import (
    BARE,
    ENV_OVERRIDE,
    HookLauncher,
    hook_program_resolves,
    path_without_active_venv,
    resolve_hook_launcher,
)
from grimoire.hosts.surface import HookSpec


def _make_script(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "grimoire-hook"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _venv(root: Path) -> tuple[Path, str]:
    """Un faux venv : ``bin/python`` et ``bin/grimoire-hook``."""
    _make_script(root / "bin")
    python = root / "bin" / "python"
    python.write_text("", encoding="utf-8")
    return root, str(python)


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)


def test_override_wins(tmp_path: Path) -> None:
    launcher = resolve_hook_launcher(tmp_path, environ={ENV_OVERRIDE: "C:/tools/grimoire-hook.exe", "PATH": ""})
    assert launcher == HookLauncher(posix="C:/tools/grimoire-hook.exe")


def test_global_install_keeps_the_bare_name(tmp_path: Path) -> None:
    global_bin = tmp_path / "global-bin"
    _make_script(global_bin)
    venv, python = _venv(tmp_path / "proj" / ".venv")
    launcher = resolve_hook_launcher(
        tmp_path / "proj",
        environ={"PATH": str(global_bin)},
        executable=python,
        prefix=str(venv),
        base_prefix="/usr",
    )
    assert launcher is BARE


def test_activated_venv_alone_is_not_global(tmp_path: Path) -> None:
    """Le défaut de #760 : le venv activé du shell masquait l'absence de nom global."""
    project = tmp_path / "proj"
    venv, python = _venv(project / ".venv")
    env = {"PATH": str(venv / "bin"), "VIRTUAL_ENV": str(venv)}
    assert path_without_active_venv(env, prefix=str(venv), base_prefix="/usr") == ""
    launcher = resolve_hook_launcher(project, environ=env, executable=python, prefix=str(venv), base_prefix="/usr")
    assert launcher.project_relative
    assert launcher.posix == ".venv/bin/grimoire-hook"
    assert launcher.windows == ".venv\\Scripts\\grimoire-hook.exe"
    assert not launcher.machine_specific


def test_venv_outside_the_project_gives_a_machine_specific_path(tmp_path: Path) -> None:
    venv, python = _venv(tmp_path / "elsewhere" / "venv")
    project = tmp_path / "proj"
    project.mkdir()
    launcher = resolve_hook_launcher(project, environ={"PATH": ""}, executable=python, prefix=str(venv), base_prefix="/usr")
    assert launcher.machine_specific
    assert launcher.posix == (venv / "bin" / "grimoire-hook").as_posix()
    assert Emitter.launcher_degradations(launcher)


def test_nothing_found_falls_back_to_the_bare_name(tmp_path: Path) -> None:
    launcher = resolve_hook_launcher(tmp_path, environ={"PATH": ""}, executable=str(tmp_path / "python"))
    assert launcher is BARE
    assert Emitter.launcher_degradations(launcher) == ()


_RELATIVE = HookLauncher(
    posix=".venv/bin/grimoire-hook", windows=".venv\\Scripts\\grimoire-hook.exe", project_relative=True
)


def test_copilot_hook_file_carries_a_windows_variant_and_cwd() -> None:
    payload = json.loads(_hook_file(HookSpec(event=HookEvent.PRE_TOOL_USE, decision="grimoire.tool-policy", timeout=10), _RELATIVE).content)
    (entry,) = payload["hooks"]["PreToolUse"]
    assert entry["command"] == "./.venv/bin/grimoire-hook --host copilot --event PreToolUse"
    assert entry["windows"] == ".\\.venv\\Scripts\\grimoire-hook.exe --host copilot --event PreToolUse"
    assert entry["cwd"] == "."


def test_copilot_hook_file_unchanged_for_a_global_install() -> None:
    payload = json.loads(_hook_file(HookSpec(event=HookEvent.PRE_TOOL_USE, decision="grimoire.tool-policy", timeout=10), BARE).content)
    (entry,) = payload["hooks"]["PreToolUse"]
    assert entry == {"type": "command", "command": "grimoire-hook --host copilot --event PreToolUse", "timeout": 10}


def test_claude_hook_entry_starts_from_the_project_dir() -> None:
    (spec,) = _hook_entry(HookSpec(event=HookEvent.PRE_TOOL_USE, decision="grimoire.tool-policy", timeout=10), _RELATIVE)["hooks"]
    tail = ".venv/Scripts/grimoire-hook.exe" if os.name == "nt" else ".venv/bin/grimoire-hook"
    assert spec["command"] == f'"$CLAUDE_PROJECT_DIR"/{tail} --host claude --event PreToolUse'


def test_path_with_a_space_is_quoted() -> None:
    launcher = HookLauncher(posix="/opt/my tools/grimoire-hook", machine_specific=True)
    payload = json.loads(_hook_file(HookSpec(event=HookEvent.STOP, decision="grimoire.tool-policy", timeout=10), launcher).content)
    assert payload["hooks"]["Stop"][0]["command"].startswith('"/opt/my tools/grimoire-hook" --host')


def test_program_resolution(tmp_path: Path) -> None:
    _make_script(tmp_path / ".venv" / "bin")
    assert hook_program_resolves(".venv/bin/grimoire-hook", tmp_path)
    assert hook_program_resolves("./.venv/bin/grimoire-hook", tmp_path)
    assert hook_program_resolves('"$CLAUDE_PROJECT_DIR"/.venv/bin/grimoire-hook', tmp_path)
    assert not hook_program_resolves("grimoire-hook", tmp_path, environ={"PATH": ""})
    assert not hook_program_resolves(".venv/bin/absent", tmp_path)
    assert not hook_program_resolves("../outside/grimoire-hook", tmp_path / ".venv")


def _write_copilot_hook(project: Path, command: str) -> None:
    hooks = project / ".github" / "hooks"
    hooks.mkdir(parents=True)
    payload = {"hooks": {"PreToolUse": [{"type": "command", "command": command, "timeout": 10}]}}
    (hooks / "grimoire-pre-tool-use.json").write_text(json.dumps(payload), encoding="utf-8")


def test_doctor_flags_a_bare_name_the_host_cannot_find(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    _write_copilot_hook(tmp_path, "grimoire-hook --host copilot --event PreToolUse")
    (check,) = check_hook_commands(tmp_path)
    assert check.name == "hook_commands"
    assert check.level == "fail"
    assert ".github/hooks/grimoire-pre-tool-use.json: grimoire-hook" in check.detail
    assert "uv tool install" in check.remedy


def test_doctor_accepts_a_project_venv_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    _make_script(tmp_path / ".venv" / "bin")
    _write_copilot_hook(tmp_path, "./.venv/bin/grimoire-hook --host copilot --event PreToolUse")
    if os.name == "nt":  # pragma: no cover - la variante windows est lue sous Windows
        pytest.skip("le fichier de test n'a pas de variante windows")
    (check,) = check_hook_commands(tmp_path)
    assert check.passed, check.detail


def test_doctor_silent_without_generated_hooks(tmp_path: Path) -> None:
    assert check_hook_commands(tmp_path) == []
