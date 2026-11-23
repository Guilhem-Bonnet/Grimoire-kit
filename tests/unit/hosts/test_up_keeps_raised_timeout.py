"""``grimoire up`` garde le délai de garde relevé à la main (#1277, point 3).

Une garde en échec fermé refuse sur dépassement : un délai relevé à la main
pour une machine lente ne doit pas redescendre à chaque ``up``. Le délai écrit
est ``max(existant, émis)`` ; un délai abaissé remonte au délai émis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import apply_plan
from grimoire.hosts.emitters.claude_code import FAIL_CLOSED_TIMEOUT
from grimoire.hosts.emitters.copilot import GUARD_TIMEOUT

pytestmark = pytest.mark.covers("host:claude-code-cli", level="integration")


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _up(root: Path, host: HostId) -> None:
    emitter = emitter_for(host)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(root), root), root)


def _settings(root: Path) -> Path:
    return root / ".claude" / "settings.json"


def _claude_handlers(root: Path) -> list[dict[str, Any]]:
    data = json.loads(_settings(root).read_text(encoding="utf-8"))
    return [h for entry in data["hooks"]["PreToolUse"] for h in entry["hooks"]]


def _guards(root: Path) -> list[dict[str, Any]]:
    return [h for h in _claude_handlers(root) if h.get("onFailure") == "block"]


def _set_claude_guard_timeout(root: Path, timeout: Any) -> None:
    path = _settings(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        for handler in entry["hooks"]:
            if handler.get("onFailure") == "block":
                handler["timeout"] = timeout
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_up_keeps_a_claude_guard_timeout_raised_by_hand(governed: Path) -> None:
    _up(governed, HostId.CLAUDE_CODE_CLI)
    assert _guards(governed)
    _set_claude_guard_timeout(governed, 60)
    _up(governed, HostId.CLAUDE_CODE_CLI)
    guards = _guards(governed)
    assert guards
    assert all(h["timeout"] == 60 for h in guards), json.dumps(guards)
    # Le conseil de modèle, consultatif, garde son délai court.
    advisory = [h for h in _claude_handlers(governed) if "onFailure" not in h]
    assert advisory
    assert all(h["timeout"] < FAIL_CLOSED_TIMEOUT for h in advisory), json.dumps(advisory)


def test_up_raises_a_claude_guard_timeout_lowered_by_hand(governed: Path) -> None:
    _up(governed, HostId.CLAUDE_CODE_CLI)
    _set_claude_guard_timeout(governed, 5)
    _up(governed, HostId.CLAUDE_CODE_CLI)
    assert all(h["timeout"] == FAIL_CLOSED_TIMEOUT for h in _guards(governed))


def test_up_ignores_a_boolean_claude_guard_timeout(governed: Path) -> None:
    _up(governed, HostId.CLAUDE_CODE_CLI)
    _set_claude_guard_timeout(governed, True)
    _up(governed, HostId.CLAUDE_CODE_CLI)
    assert all(h["timeout"] == FAIL_CLOSED_TIMEOUT for h in _guards(governed))


def test_up_keeps_the_raised_timeout_when_the_launcher_path_changes(governed: Path) -> None:
    """Le lanceur (nom nu ou chemin du venv) n'identifie pas le hook : seule la queue de la commande compte."""
    _up(governed, HostId.CLAUDE_CODE_CLI)
    path = _settings(governed)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        for handler in entry["hooks"]:
            if handler.get("onFailure") == "block":
                handler["timeout"] = 90
                tail = handler["command"][handler["command"].index(" --host ") :]
                handler["command"] = f'"/ancien/venv/bin/grimoire-hook"{tail}'
    path.write_text(json.dumps(data), encoding="utf-8")
    _up(governed, HostId.CLAUDE_CODE_CLI)
    guards = _guards(governed)
    assert guards
    assert all(h["timeout"] == 90 for h in guards), json.dumps(guards)
    assert not any("/ancien/venv" in h["command"] for h in _claude_handlers(governed))


def _copilot_guard_file(root: Path) -> Path:
    return root / ".github" / "hooks" / "grimoire-pre-tool-use.json"


def _set_copilot_timeouts(root: Path, timeout: int) -> None:
    path = _copilot_guard_file(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        entry["timeoutSec"] = timeout
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _copilot_entries(root: Path) -> list[dict[str, Any]]:
    data = json.loads(_copilot_guard_file(root).read_text(encoding="utf-8"))
    (entries,) = data["hooks"].values()
    return entries


def test_up_keeps_a_copilot_guard_timeout_raised_by_hand(governed: Path) -> None:
    _up(governed, HostId.GITHUB_COPILOT)
    _set_copilot_timeouts(governed, 60)
    _up(governed, HostId.GITHUB_COPILOT)
    entries = _copilot_entries(governed)
    guards = [e for e in entries if "--decision" not in e["command"]]
    advisory = [e for e in entries if "--decision" in e["command"]]
    assert guards
    assert advisory
    assert all(e["timeoutSec"] == 60 for e in guards), json.dumps(guards)
    assert all(e["timeoutSec"] < GUARD_TIMEOUT for e in advisory), json.dumps(advisory)


def test_up_raises_a_copilot_guard_timeout_lowered_by_hand(governed: Path) -> None:
    _up(governed, HostId.GITHUB_COPILOT)
    _set_copilot_timeouts(governed, 5)
    _up(governed, HostId.GITHUB_COPILOT)
    guards = [e for e in _copilot_entries(governed) if "--decision" not in e["command"]]
    assert all(e["timeoutSec"] == GUARD_TIMEOUT for e in guards), json.dumps(guards)
