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
    MCP_ENV_OVERRIDE,
    HookLauncher,
    expand_project_dir,
    hook_program_resolves,
    mcp_command,
    path_without_active_venv,
    resolve_hook_launcher,
)
from grimoire.hosts.surface import HookSpec

#: Sous Windows, ``shutil.which`` ne retient qu'un fichier dont l'extension est
#: dans ``PATHEXT`` : le point d'entrée réel y est ``grimoire-hook.exe``.
_EXE = ".exe" if os.name == "nt" else ""


def _make_script(directory: Path, name: str = "grimoire-hook") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"{name}{_EXE}"
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
    monkeypatch.delenv(MCP_ENV_OVERRIDE, raising=False)


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
    assert launcher.posix == (venv / "bin" / f"grimoire-hook{_EXE}").as_posix()
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
    assert hook_program_resolves(f".venv/bin/grimoire-hook{_EXE}", tmp_path)
    assert hook_program_resolves(f"./.venv/bin/grimoire-hook{_EXE}", tmp_path)
    assert hook_program_resolves(f'"$CLAUDE_PROJECT_DIR"/.venv/bin/grimoire-hook{_EXE}', tmp_path)
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


# --- grimoire-mcp et le plugin opencode : même défaut, même résolution ---


def test_mcp_command_from_a_project_venv(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    venv, python = _venv(project / ".venv")
    _make_script(venv / "bin", "grimoire-mcp")
    env = {"PATH": str(venv / "bin"), "VIRTUAL_ENV": str(venv)}
    command = mcp_command(project, environ=env, executable=python, prefix=str(venv), base_prefix="/usr")
    expected = ".venv/Scripts/grimoire-mcp.exe" if os.name == "nt" else ".venv/bin/grimoire-mcp"
    assert command == "${CLAUDE_PROJECT_DIR:-.}/" + expected
    assert expand_project_dir(command, project) == str(project) + "/" + expected


def test_mcp_command_keeps_the_bare_name_for_a_global_install(tmp_path: Path) -> None:
    global_bin = tmp_path / "global-bin"
    _make_script(global_bin, "grimoire-mcp")
    assert mcp_command(tmp_path, environ={"PATH": str(global_bin)}) == "grimoire-mcp"
    assert mcp_command(tmp_path, environ={MCP_ENV_OVERRIDE: "/opt/grimoire-mcp", "PATH": ""}) == "/opt/grimoire-mcp"


def test_scaffold_writes_the_resolved_mcp_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.core.scaffold import ProjectScaffolder, ScaffoldPlan

    monkeypatch.setenv(MCP_ENV_OVERRIDE, "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp")
    scaffolder = ProjectScaffolder.__new__(ProjectScaffolder)
    scaffolder._target = tmp_path
    plan = ScaffoldPlan()
    scaffolder._plan_mcp_config(plan)
    (render,) = plan.templates
    assert json.loads(render.content)["mcpServers"]["grimoire"]["command"] == "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp"


def test_opencode_plugin_runs_the_resolved_hook() -> None:
    from grimoire.hosts.emitters.opencode import _guard_plugin

    hook = HookSpec(event=HookEvent.PRE_TOOL_USE, decision="policy")
    content = _guard_plugin(hook, _RELATIVE).content
    assert 'spawnSync(hookProgram(directory), [' in content
    assert '"posix": ".venv/bin/grimoire-hook"' in content
    assert 'spawnSync("grimoire-hook"' not in content
    assert '"windows": null' in _guard_plugin(hook).content


def test_opencode_mcp_command_is_absolute(tmp_path: Path) -> None:
    from grimoire.hosts.emitters.opencode import _config_merge
    from grimoire.hosts.surface import McpServerSpec

    surface = type("S", (), {"mcp_servers": (McpServerSpec(name="grimoire", command="${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp"),)})()
    data = _config_merge(surface, tmp_path).merge({})  # type: ignore[arg-type]
    assert data["mcp"]["grimoire"]["command"] == [str(tmp_path) + "/.venv/bin/grimoire-mcp"]


def test_doctor_mcp_check_ignores_the_active_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli.cmd_up import check_mcp_json

    venv = tmp_path / "venv"
    _make_script(venv / "bin", "grimoire-mcp")
    monkeypatch.setenv("PATH", str(venv / "bin"))
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"grimoire": {"command": "grimoire-mcp"}}}), encoding="utf-8")
    (check,) = check_mcp_json(tmp_path)
    assert not check.passed
    project_cmd = "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp" + _EXE
    _make_script(tmp_path / ".venv" / "bin", "grimoire-mcp")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"grimoire": {"command": project_cmd}}}), encoding="utf-8")
    (check,) = check_mcp_json(tmp_path)
    assert check.passed, check.detail


def test_doctor_ignores_a_custom_hook_that_only_names_grimoire_hook(tmp_path: Path) -> None:
    """Constat Forge (3.64.0) : ``grimoire-hook-gateway.sh --hook-id …`` était pris pour un hook du kit."""
    hooks = tmp_path / ".github" / "hooks"
    hooks.mkdir(parents=True)
    gateway = ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id grimoire-pre-compact --event PreCompact"
    (hooks / "forge-pre-compact.json").write_text(
        json.dumps({"version": 1, "hooks": {"PreCompact": [{"type": "command", "command": gateway}]}}),
        encoding="utf-8",
    )
    assert check_hook_commands(tmp_path) == []
