"""Fichiers de hook Copilot au format du harness « Copilot » (#1280, lots P1-1 et P1-2).

Référence : https://docs.github.com/en/copilot/reference/hooks-reference —
``{"version": 1, "hooks": {...}}``, entrée ``bash``/``powershell``/``command``,
``cwd``, ``timeoutSec``, ``matcher`` (liste littérale ou regex ancrée) sur
``preToolUse``/``postToolUse``. Le harness « Local » de VS Code
(https://code.visualstudio.com/docs/agents/reference/hooks-reference) lit
``bash``/``powershell`` comme repli de ``linux``/``osx``/``windows``, lit
``timeoutSec`` quand ``timeout`` manque, et ignore ``matcher``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_hooks import check_copilot_guard_timeout
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface, governance_hooks
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import apply_plan
from grimoire.hosts.emitters.claude_code import FAIL_CLOSED_TIMEOUT, _hook_entry
from grimoire.hosts.emitters.copilot import GUARD_TIMEOUT, _hook_file, matcher_for
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import BARE, HookLauncher
from grimoire.hosts.surface import Enforcement, HookSpec

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

#: Champs d'une entrée ``command`` documentés par la référence du harness Copilot.
_DOCUMENTED_ENTRY_FIELDS = frozenset(
    {"type", "command", "bash", "powershell", "exec", "args", "cwd", "env", "timeoutSec", "timeout", "matcher"}
)

#: Événements dont l'entrée accepte un ``matcher`` (référence, PascalCase).
_MATCHER_EVENTS = {"Notification", "PermissionRequest", "PostToolUse", "PreCompact", "PreToolUse", "SubagentStart"}

#: Chaque outil documenté qui écrit un fichier ou exécute une commande, sous ses
#: deux nommages : nom runtime Copilot et nom Claude que l'hôte rapporte en PascalCase.
_WRITE_OR_EXECUTE = (
    "bash",
    "powershell",
    "Bash",  # shell (Unix, Windows), tous deux rapportés `Bash`
    "create",
    "Write",  # création de fichier
    "edit",
    "str_replace_editor",
    "apply_patch",
    "Edit",  # modification de fichier
)

#: Outils qui ne lisent ni n'écrivent de fichier et n'exécutent rien : hors garde.
_NOT_GUARDED = (
    "grep",
    "rg",
    "Grep",
    "glob",
    "Glob",
    "web_fetch",
    "WebFetch",
    "web_search",
    "WebSearch",
    "ask_user",
    "AskUserQuestion",
    "update_todo",
    "TodoWrite",
    "task",
    "Agent",
    "Task",
)


def _guard() -> HookSpec:
    (guard,) = [h for h in governance_hooks(governed=True) if h.event is HookEvent.PRE_TOOL_USE]
    return guard


def _matches(matcher: str, tool: str) -> bool:
    """Les deux lectures que la référence donne d'un matcher : liste littérale, et regex ``^(?:…)$``."""
    literal = tool in matcher.split("|")
    regex = re.fullmatch(f"(?:{matcher})", tool) is not None
    assert literal == regex, f"{tool}: lecture littérale et regex divergent pour {matcher!r}"
    return literal


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _hook_files(project: Path) -> dict[str, dict]:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project), project)
    return {
        f.relpath.name: json.loads(f.content)
        for f in plan.files
        if f.relpath.as_posix().startswith(".github/hooks/grimoire-")
    }


# ── Schéma ───────────────────────────────────────────────────────────────────


def test_every_emitted_hook_file_follows_the_copilot_schema(governed: Path) -> None:
    files = _hook_files(governed)
    assert len(files) >= 6
    for name, payload in files.items():
        assert set(payload) == {"version", "hooks"}, name
        assert payload["version"] == 1, name
        ((event, entries),) = payload["hooks"].items()
        (entry,) = entries
        assert set(entry) <= _DOCUMENTED_ENTRY_FIELDS, (
            f"{name}: champ non documenté {set(entry) - _DOCUMENTED_ENTRY_FIELDS}"
        )
        assert entry["type"] == "command"
        assert entry["bash"] and entry["powershell"] and entry["command"] == entry["bash"]
        # Un seul des deux alias : les harness Copilot et Local leur donnent des priorités opposées.
        assert "timeout" not in entry and isinstance(entry["timeoutSec"], int), name
        assert "windows" not in entry and "linux" not in entry and "osx" not in entry, name
        if "matcher" in entry:
            assert event in _MATCHER_EVENTS, f"{name}: matcher sur un événement qui n'en prend pas"


def test_guard_carries_matcher_and_raised_timeout(governed: Path) -> None:
    entry = _hook_files(governed)["grimoire-pre-tool-use.json"]["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == matcher_for(_guard())
    assert entry["timeoutSec"] == GUARD_TIMEOUT == FAIL_CLOSED_TIMEOUT == 30


def test_events_without_tool_carry_no_matcher(governed: Path) -> None:
    for name, payload in _hook_files(governed).items():
        ((event, (entry,)),) = payload["hooks"].items()
        if event not in {"PreToolUse", "PostToolUse"}:
            assert "matcher" not in entry, name


def test_advisory_hooks_keep_their_timeout() -> None:
    spec = HookSpec(event=HookEvent.PRE_TOOL_USE, decision="x", enforcement=Enforcement.ADVISORY, timeout=3)
    entry = json.loads(_hook_file(spec, BARE).content)["hooks"]["PreToolUse"][0]
    assert entry["timeoutSec"] == 3


def test_quoted_absolute_launcher_uses_the_call_operator_under_powershell() -> None:
    launcher = HookLauncher(posix="C:/Program Files/kit/grimoire-hook.exe", machine_specific=True)
    entry = json.loads(_hook_file(_guard(), launcher).content)["hooks"]["PreToolUse"][0]
    assert entry["powershell"].startswith('& "C:/Program Files/kit/grimoire-hook.exe" --host copilot')
    assert entry["bash"].startswith('"C:/Program Files/kit/grimoire-hook.exe" --host copilot')


def test_no_matcher_degradation_and_timeout_gap_declared(governed: Path) -> None:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(governed), governed)
    surfaces = {g.surface for g in plan.degradations}
    assert "hook matchers" not in surfaces
    assert "hook timeout" in surfaces


# ── Matcher de la garde ──────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", _WRITE_OR_EXECUTE)
def test_guard_matcher_covers_every_write_or_execute_tool(tool: str) -> None:
    assert _matches(matcher_for(_guard()), tool)


@pytest.mark.parametrize("tool", ["view", "Read"])
def test_guard_matcher_covers_file_reads_for_secret_refusal(tool: str) -> None:
    """Sans table de permissions, le refus de lecture d'un secret n'existe que dans la garde."""
    assert _matches(matcher_for(_guard()), tool)


@pytest.mark.parametrize("tool", _NOT_GUARDED)
def test_guard_matcher_skips_tools_that_neither_write_nor_execute(tool: str) -> None:
    assert not _matches(matcher_for(_guard()), tool)


def test_guard_matcher_is_a_plain_literal_list() -> None:
    """Une liste littérale se compare au nom runtime *et* au nom Claude ; aucun méta-caractère regex."""
    assert re.fullmatch(r"[A-Za-z_]+(\|[A-Za-z_]+)*", matcher_for(_guard()))


def test_post_tool_use_matcher_covers_delegation() -> None:
    (trace,) = [h for h in governance_hooks(governed=True) if h.event is HookEvent.POST_TOOL_USE]
    matcher = matcher_for(trace)
    for tool in ("task", "Agent", "Task", "create", "Write", "bash", "powershell", "Edit"):
        assert _matches(matcher, tool)
    assert not _matches(matcher, "view")


# ── grimoire up : remplacement ───────────────────────────────────────────────


def test_sync_replaces_a_legacy_local_format_file_and_spares_a_foreign_hook(governed: Path) -> None:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(governed), governed)
    hooks = governed / ".github" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    legacy = {
        "hooks": {
            "PreToolUse": [
                {
                    "type": "command",
                    "command": "./.venv/bin/grimoire-hook --host copilot --event PreToolUse",
                    "windows": ".\\.venv\\Scripts\\grimoire-hook.exe --host copilot --event PreToolUse",
                    "cwd": ".",
                    "timeout": 10,
                }
            ]
        }
    }
    (hooks / "grimoire-pre-tool-use.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    foreign = '{"hooks": {"PreToolUse": [{"type": "command", "command": "./maison.sh", "timeout": 5}]}}\n'
    (hooks / "maison.json").write_text(foreign, encoding="utf-8")

    apply_plan(plan, governed)

    rewritten = json.loads((hooks / "grimoire-pre-tool-use.json").read_text(encoding="utf-8"))
    assert rewritten["version"] == 1
    assert "windows" not in rewritten["hooks"]["PreToolUse"][0]
    assert (hooks / "maison.json").read_text(encoding="utf-8") == foreign


# ── doctor ───────────────────────────────────────────────────────────────────


def _write_guard(project: Path, entry: dict) -> None:
    hooks = project / ".github" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "hooks": {"PreToolUse": [entry]}}
    (hooks / "grimoire-pre-tool-use.json").write_text(json.dumps(payload), encoding="utf-8")


_CMD = "grimoire-hook --host copilot --event PreToolUse"


def test_doctor_warns_on_a_short_copilot_guard_timeout(tmp_path: Path) -> None:
    _write_guard(tmp_path, {"type": "command", "command": _CMD, "timeout": 10})
    (check,) = check_copilot_guard_timeout(tmp_path)
    assert check.name == "hook_timeout_copilot"
    assert check.level == "warn" and not check.passed
    assert "10 s" in check.detail and "times out" in check.detail
    assert "grimoire up" in check.remedy


def test_doctor_notes_fail_open_timeout_on_a_compliant_guard(tmp_path: Path) -> None:
    _write_guard(tmp_path, {"type": "command", "bash": _CMD, "powershell": _CMD, "timeoutSec": 30})
    (check,) = check_copilot_guard_timeout(tmp_path)
    assert check.passed and check.level == "info"
    assert "fails open" in check.detail


def test_doctor_reads_timeout_sec_before_its_alias(tmp_path: Path) -> None:
    _write_guard(tmp_path, {"type": "command", "command": _CMD, "timeoutSec": 30, "timeout": 5})
    (check,) = check_copilot_guard_timeout(tmp_path)
    assert check.passed


def test_doctor_on_freshly_emitted_hooks_is_not_a_warning(governed: Path) -> None:
    apply_plan(emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(governed), governed), governed)
    (check,) = check_copilot_guard_timeout(governed)
    assert check.level == "info"


def test_doctor_ignores_foreign_guards_and_empty_projects(tmp_path: Path) -> None:
    assert check_copilot_guard_timeout(tmp_path) == []
    _write_guard(tmp_path, {"type": "command", "command": "./maison.sh", "timeout": 1})
    assert check_copilot_guard_timeout(tmp_path) == []


# ── Non-régression Claude Code ───────────────────────────────────────────────


def test_claude_guard_entry_is_unchanged() -> None:
    entry = _hook_entry(_guard(), BARE)
    assert entry["matcher"] == "Bash|Edit|Write|MultiEdit|NotebookEdit"
    (handler,) = entry["hooks"]
    assert handler["onFailure"] == "block"
    assert handler["timeout"] == FAIL_CLOSED_TIMEOUT
    assert "timeoutSec" not in handler and "version" not in entry
