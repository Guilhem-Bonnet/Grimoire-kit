"""RTK (Rust Token Killer) setup helpers for ``grimoire init``.

Installs the ``rtk`` CLI proxy — it compresses verbose command output (git,
pytest, ruff, build...) before it reaches an AI agent, cutting token usage by
60-90% — and wires the Claude Code PreToolUse hook. Used by the init wizard so
RTK ships with grimoire onboarding.

Install method is ``cargo install`` (compile from source) on purpose: no remote
``curl | sh`` piping. When cargo is absent the manual install command is
surfaced instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

RTK_REPO = "https://github.com/rtk-ai/rtk"
_CLAUDE_HOOK_COMMAND_SUFFIX = "hook claude"


@dataclass(frozen=True, slots=True)
class RtkSetupResult:
    """Outcome of an RTK setup attempt."""

    present: bool
    installed_now: bool
    hook_activated: bool
    message: str
    rtk_path: str

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for ``grimoire init --output json``."""
        return {
            "present": self.present,
            "installed_now": self.installed_now,
            "hook_activated": self.hook_activated,
            "message": self.message,
            "rtk_path": self.rtk_path,
        }


def find_rtk() -> str | None:
    """Return the rtk binary path if installed, else None."""
    found = shutil.which("rtk")
    if found:
        return found
    candidates = (Path.home() / ".cargo" / "bin" / "rtk", Path.home() / ".local" / "bin" / "rtk")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def install_rtk_via_cargo(*, timeout: float = 900.0) -> tuple[bool, str]:
    """Compile and install rtk from source via cargo. Returns (ok, message)."""
    cargo = shutil.which("cargo")
    if not cargo:
        return False, f"cargo introuvable — installe rtk manuellement: {RTK_REPO}"
    try:
        proc = subprocess.run(
            [cargo, "install", "--git", RTK_REPO],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "cargo introuvable au moment de l'execution."
    except subprocess.TimeoutExpired:
        return False, "cargo install rtk a depasse le delai (compilation longue)."
    if proc.returncode != 0:
        raw = (proc.stderr.strip() or proc.stdout.strip() or "erreur inconnue").splitlines()
        return False, f"cargo install rtk a echoue: {raw[-1] if raw else 'erreur inconnue'}"
    return True, "rtk installe via cargo."


def _hook_already_present(pre_tool_use: list[object]) -> bool:
    """True if an RTK Claude hook command is already wired."""
    for entry in pre_tool_use:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and _CLAUDE_HOOK_COMMAND_SUFFIX in str(hook.get("command", "")):
                return True
    return False


def activate_claude_hook(rtk_path: str) -> tuple[bool, str]:
    """Idempotently add the RTK PreToolUse Bash hook to ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    command = f"{rtk_path} {_CLAUDE_HOOK_COMMAND_SUFFIX}"

    settings: dict[str, object] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, f"{settings_path} illisible — hook RTK non active."
        if isinstance(loaded, dict):
            settings = loaded

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False, "Cle 'hooks' inattendue dans settings.json — hook RTK non active."
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        return False, "Cle 'hooks.PreToolUse' inattendue — hook RTK non active."

    if _hook_already_present(pre):
        return True, "Hook RTK deja actif (idempotent)."

    pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": command}]})
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"Ecriture de settings.json impossible: {exc}"
    return True, "Hook RTK active (redemarre Claude Code pour le charger)."


def setup_rtk(*, allow_install: bool = True, timeout: float = 900.0) -> RtkSetupResult:
    """Ensure rtk is installed and the Claude hook is active."""
    rtk_path = find_rtk()
    installed_now = False
    if rtk_path is None:
        if not allow_install:
            return RtkSetupResult(False, False, False, "rtk absent (installation non demandee).", "")
        ok, msg = install_rtk_via_cargo(timeout=timeout)
        if not ok:
            return RtkSetupResult(False, False, False, msg, "")
        installed_now = True
        rtk_path = find_rtk()
        if rtk_path is None:
            return RtkSetupResult(False, True, False, "rtk installe mais binaire introuvable sur le PATH.", "")

    hook_ok, hook_msg = activate_claude_hook(rtk_path)
    present_msg = "rtk installe" if installed_now else "rtk deja present"
    return RtkSetupResult(
        present=True,
        installed_now=installed_now,
        hook_activated=hook_ok,
        message=f"{present_msg}. {hook_msg}",
        rtk_path=rtk_path,
    )
