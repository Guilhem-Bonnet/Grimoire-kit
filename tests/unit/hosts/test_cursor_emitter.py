"""#1281 L1 : émetteur riche Cursor — hooks, sous-agents, compétences, rendu runtime.

Sources (lues le 2026-11-21 ; Cursor n'est pas installé sur le poste, aucune capture) :

- https://cursor.com/docs/hooks : ``{"version": 1, "hooks": {...}}``, champs d'entrée
  ``command``/``type``/``timeout``/``loop_limit``/``failClosed``/``matcher`` ; sortie
  ``permission``/``user_message``/``agent_message`` ; ``ask`` non appliqué sur
  ``preToolUse``, appliqué sur ``beforeShellExecution`` ; ``stop`` → ``followup_message`` ;
- https://cursor.com/docs/context/subagents : ``name``, ``description``, ``model``,
  ``readonly``, ``is_background`` ;
- https://cursor.com/docs/context/skills : ``name`` (minuscules, chiffres, tirets, égal au
  dossier) et ``description`` obligatoires.

Les charges ci-dessous sont des fixtures de protocole tirées de ces pages, pas des captures.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import cursor_wire
from grimoire.hosts.capabilities import (
    GovernanceFunction,
    ParityLevel,
    function_states,
    gaps_for,
    profile_for,
)
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.emitters import apply_plan, emitter_for
from grimoire.hosts.emitters.base import EmitPlan
from grimoire.hosts.emitters.cursor import CursorEmitter
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import ENV_OVERRIDE
from grimoire.hosts.matrix import ADVISORY, ENFORCED, build_matrix
from grimoire.hosts.runtime import parse_event, render

pytestmark = pytest.mark.covers("host:cursor", level="integration")

#: Événements documentés de ``hooks.json`` (tableau des événements de la doc).
DOCUMENTED_EVENTS = frozenset(
    {
        "sessionStart",
        "sessionEnd",
        "preToolUse",
        "postToolUse",
        "postToolUseFailure",
        "subagentStart",
        "subagentStop",
        "beforeShellExecution",
        "afterShellExecution",
        "beforeMCPExecution",
        "afterMCPExecution",
        "beforeReadFile",
        "afterFileEdit",
        "beforeTabFileRead",
        "afterTabFileEdit",
        "beforeSubmitPrompt",
        "afterAgentResponse",
        "afterAgentThought",
        "stop",
        "preCompact",
        "workspaceOpen",
    }
)
#: Champs documentés d'une entrée de hook.
ENTRY_FIELDS = frozenset({"command", "type", "prompt", "model", "timeout", "loop_limit", "failClosed", "matcher"})
#: Champs de sortie documentés par événement de permission.
PERMISSION_OUTPUT = {
    "preToolUse": {"permission", "user_message", "agent_message", "updated_input"},
    "beforeShellExecution": {"permission", "user_message", "agent_message"},
}
SUBAGENT_FIELDS = {"name", "description", "model", "readonly", "is_background"}

AGENT_DIR = Path("_grimoire/_config/custom/agents")
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
DESTRUCTIVE = "rm -rf /"
#: Écrit le profil sans valeur lisible : la politique demande confirmation (``ask``).
PROFILE_WRITE = 'echo "$X" > _grimoire/standard/standard-profile.yaml'


@pytest.fixture(autouse=True)
def _bare_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lanceur déterministe : le nom nu, quelle que soit l'installation du poste.
    monkeypatch.setenv(ENV_OVERRIDE, "grimoire-hook")
    # Racine du projet : seulement ce que chaque test pose (la session de test peut en exporter une).
    for var in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _agent(root: Path, name: str, *, reasoning: str, cost: str, tools: str = "") -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    extra = f"tools: [{tools}]\n" if tools else ""
    (root / AGENT_DIR / f"{name}.md").write_text(
        f'---\nname: "{name}"\ndescription: "{name} test"\n{extra}model_affinity:\n  reasoning: {reasoning}\n'
        f"  context_window: medium\n  cost: {cost}\n---\n\nTu es {name}.\n",
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["cursor"]\n',
        encoding="utf-8",
    )
    _agent(tmp_path, "architecte", reasoning="high", cost="high", tools="read, search, edit, execute")  # opus
    _agent(tmp_path, "scribe", reasoning="low", cost="medium", tools="read, search")  # haiku, lecture seule
    _agent(tmp_path, "moyen", reasoning="medium", cost="medium")  # inherit
    return tmp_path


def _plan(root: Path) -> EmitPlan:
    emitter = emitter_for(HostId.CURSOR)
    assert isinstance(emitter, CursorEmitter)
    return emitter.plan(build_surface(root), root)


def _hooks(plan: EmitPlan, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    (merge,) = [m for m in plan.merges if m.relpath == Path(".cursor/hooks.json")]
    return merge.merge(json.loads(json.dumps(existing or {})))


def _frontmatter(text: str) -> dict[str, str]:
    head = text.split("---", 2)[1]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.strip().splitlines())}


# ── .cursor/hooks.json ───────────────────────────────────────────────────────


def test_hooks_json_follows_the_documented_schema(governed: Path) -> None:
    data = _hooks(_plan(governed))
    assert data["version"] == 1
    assert set(data) == {"version", "hooks"}
    assert set(data["hooks"]) <= DOCUMENTED_EVENTS
    for event, entries in data["hooks"].items():
        assert isinstance(entries, list) and entries, event
        for entry in entries:
            assert set(entry) <= ENTRY_FIELDS, (event, entry)
            assert isinstance(entry["timeout"], int) and entry["timeout"] > 0
            assert re.search(r"grimoire-hook --host cursor --event [A-Za-z]+", entry["command"]), entry


def test_guard_is_wired_on_the_events_that_can_refuse(governed: Path) -> None:
    hooks = _hooks(_plan(governed))["hooks"]
    (shell,) = hooks["beforeShellExecution"]
    (pre,) = hooks["preToolUse"]
    for entry in (shell, pre):
        assert entry["command"].endswith("--event PreToolUse")
        assert entry["failClosed"] is True
        assert entry["timeout"] >= 30  # un dépassement vaut refus avec failClosed
    assert "matcher" not in shell  # toute commande shell passe par la garde
    assert set(pre["matcher"].split("|")) == {"Write", "Delete", "Read", "Grep"}
    assert "beforeSubmitPrompt" not in hooks  # aucun canal de contexte : déclaré dégradé


def test_stop_relaunch_is_bounded_by_the_documented_loop_limit(governed: Path) -> None:
    hooks = _hooks(_plan(governed))["hooks"]
    assert hooks["stop"][0]["loop_limit"] == cursor_wire.LOOP_LIMIT == 5
    assert hooks["subagentStop"][0]["loop_limit"] == 5


def test_project_venv_launcher_is_written_from_the_project_root(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.hosts.emitters import cursor as cursor_mod
    from grimoire.hosts.hook_launcher import HookLauncher

    venv = HookLauncher(
        posix=".venv/bin/grimoire-hook", windows=".venv\\Scripts\\grimoire-hook.exe", project_relative=True
    )
    monkeypatch.setattr(cursor_mod, "resolve_hook_launcher", lambda root: venv)
    plan = _plan(governed)
    hooks = _hooks(plan)["hooks"]
    assert hooks["preToolUse"][0]["command"].startswith("./.venv/bin/grimoire-hook --host cursor")
    assert any(d.surface == "hook command windows" for d in plan.degradations)


def test_unresolved_launcher_keeps_the_guard_fail_open_and_says_so(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.hosts.emitters import cursor as cursor_mod
    from grimoire.hosts.hook_launcher import UNRESOLVED

    monkeypatch.setattr(cursor_mod, "resolve_hook_launcher", lambda root: UNRESOLVED)
    plan = _plan(governed)
    assert "failClosed" not in _hooks(plan)["hooks"]["preToolUse"][0]
    assert any(d.surface == "hook failClosed" for d in plan.degradations)


def test_merge_keeps_unmanaged_hooks_and_replaces_only_kit_entries(governed: Path) -> None:
    user_pre = {"command": ".cursor/hooks/audit.sh", "matcher": "Shell"}
    user_other = {"command": "./format.sh"}
    stale = {"command": "grimoire-hook --host cursor --event PreToolUse --decision grimoire.old", "timeout": 3}
    gone = {"command": "grimoire-hook --host cursor --event SessionEnd"}
    existing = {
        "version": 1,
        "hooks": {"preToolUse": [user_pre, stale], "afterFileEdit": [user_other], "sessionEnd": [gone]},
    }
    data = _hooks(_plan(governed), existing)
    pre = data["hooks"]["preToolUse"]
    assert pre[0] == user_pre  # le hook du projet, intact et en tête
    assert stale not in pre and len(pre) == 2
    assert data["hooks"]["afterFileEdit"] == [user_other]
    assert "sessionEnd" not in data["hooks"]  # entrée du kit périmée retirée, rien d'autre


def test_sync_is_idempotent_and_preserves_a_foreign_hook_on_disk(governed: Path) -> None:
    hooks_file = governed / ".cursor/hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "./notify.sh"}]}}), encoding="utf-8")
    first = apply_plan(_plan(governed), governed)
    assert ".cursor/hooks.json" in first.written
    second = apply_plan(_plan(governed), governed)
    assert ".cursor/hooks.json" in second.unchanged
    stop = json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]["stop"]
    assert stop[0] == {"command": "./notify.sh"}
    assert any("--event Stop" in e["command"] for e in stop[1:])


def test_grimoire_up_merges_cursor_hooks_without_touching_a_foreign_one(governed: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    (governed / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        'hosts:\n  enabled: ["cursor"]\n',
        encoding="utf-8",
    )
    hooks_file = governed / ".cursor/hooks.json"
    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    foreign = {"command": ".cursor/hooks/audit.sh", "matcher": "Shell", "timeout": 5}
    hooks_file.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [foreign]}}), encoding="utf-8")
    result = CliRunner().invoke(app, ["up", str(governed)])
    assert result.exit_code == 0, result.output
    data = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert data["hooks"]["preToolUse"][0] == foreign
    assert any("--host cursor --event PreToolUse" in e["command"] for e in data["hooks"]["preToolUse"][1:])
    assert "beforeShellExecution" in data["hooks"]


# ── sous-agents et compétences ───────────────────────────────────────────────


def test_subagents_use_documented_fields_and_no_invented_model(project: Path) -> None:
    plan = _plan(project)
    files = {f.relpath.as_posix(): f.content for f in plan.files}
    for name in ("architecte", "scribe", "moyen"):
        fields = _frontmatter(files[f".cursor/agents/{name}.md"])
        assert set(fields) <= SUBAGENT_FIELDS, fields
        assert "model" not in fields
    assert _frontmatter(files[".cursor/agents/scribe.md"])["readonly"] == "true"
    assert "readonly" not in _frontmatter(files[".cursor/agents/architecte.md"])
    gap = next(d for d in plan.degradations if d.surface == "model affinity")
    assert "architecte" in gap.reason and "scribe" in gap.reason and "moyen" not in gap.reason


def test_model_is_written_only_for_a_tier_the_project_declares(project: Path) -> None:
    (project / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (project / POLICY).write_text("dispatch:\n  cursor_models:\n    haiku: composer-2\n", encoding="utf-8")
    plan = _plan(project)
    files = {f.relpath.as_posix(): f.content for f in plan.files}
    assert _frontmatter(files[".cursor/agents/scribe.md"])["model"] == "'composer-2'"
    assert "model" not in _frontmatter(files[".cursor/agents/architecte.md"])
    gap = next(d for d in plan.degradations if d.surface == "model affinity")
    assert "architecte" in gap.reason and "scribe" not in gap.reason


def test_skills_are_native_with_a_name_matching_their_folder(governed: Path) -> None:
    plan = _plan(governed)
    skills = [f for f in plan.files if f.relpath.parts[:2] == (".cursor", "skills")]
    assert skills
    for skill in skills:
        fields = _frontmatter(skill.content)
        name = fields["name"].strip("'")
        assert name == skill.relpath.parent.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)
        assert fields["description"]


def test_entrypoint_rule_is_kept_and_says_the_guard_is_unverified(governed: Path) -> None:
    files = {f.relpath.as_posix(): f.content for f in _plan(governed).files}
    rule = files[".cursor/rules/grimoire.mdc"]
    assert rule.startswith("---\ndescription:") and "alwaysApply: true" in rule
    assert "**Non vérifié**" in rule
    assert "grimoire-uncertainties" in rule


# ── capacités : advisory, jamais enforced ────────────────────────────────────


def test_cursor_guard_is_advisory_never_enforced(governed: Path) -> None:
    profile = profile_for(HostId.CURSOR)
    assert profile.subagents_native and profile.skills_native
    assert not profile.blocks(HookEvent.PRE_TOOL_USE)
    assert profile.documents_unverified_block(HookEvent.PRE_TOOL_USE)
    matrix = build_matrix(governed, (HostId.CURSOR,))
    assert matrix.ok, [c.to_dict() for c in matrix.failing]
    cells = {c.subject: c for c in matrix.for_host(HostId.CURSOR)}
    assert cells["hook:pre_tool_use"].status == ADVISORY
    assert all(c.status != ENFORCED for c in cells.values())
    states = function_states(HostId.CURSOR)
    assert states[GovernanceFunction.TOOL_GUARD].level is ParityLevel.DEGRADED
    assert "non vérifié" in states[GovernanceFunction.TOOL_GUARD].reason
    assert states[GovernanceFunction.PERSONAS].level is ParityLevel.RICH
    gap = next(g for g in gaps_for(profile) if g.surface == "blocking_hooks")
    assert "non vérifiée" in gap.fallback


# ── rendu runtime ────────────────────────────────────────────────────────────


def _common(root: Path) -> dict[str, Any]:
    return {
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "composer-2",
        "cursor_version": "2.0.0",
        "workspace_roots": [str(root)],
        "user_email": None,
        "transcript_path": None,
    }


def _shell(root: Path, command: str) -> dict[str, Any]:
    return {
        **_common(root),
        "hook_event_name": "beforeShellExecution",
        "command": command,
        "cwd": str(root),
        "sandbox": False,
    }


def _pre(root: Path, tool: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        **_common(root),
        "hook_event_name": "preToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "abc123",
        "cwd": str(root),
    }


def _entry(root: Path, payload: dict[str, Any], event: str | None = "PreToolUse") -> subprocess.CompletedProcess[str]:
    """Le vrai point d'entrée ``grimoire-hook``, lancé comme Cursor le lance (cwd = racine du projet)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRIMOIRE_", "CLAUDE_", "CURSOR_"))}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from grimoire.hosts.hook_entry import main; raise SystemExit(main())",
            "--host",
            "cursor",
            *(("--event", event) if event else ()),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=root,
        timeout=120,
    )


def _verdict(proc: subprocess.CompletedProcess[str], cursor_event: str) -> dict[str, Any]:
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert set(out) <= PERMISSION_OUTPUT[cursor_event], out
    return out


def test_shell_refusal_is_a_readable_flat_deny(governed: Path) -> None:
    out = _verdict(_entry(governed, _shell(governed, DESTRUCTIVE)), "beforeShellExecution")
    assert out["permission"] == "deny"
    assert "recursive/forced delete" in out["user_message"]
    assert out["agent_message"] == out["user_message"]


def test_shell_ask_is_kept_where_cursor_applies_it(governed: Path) -> None:
    out = _verdict(_entry(governed, _shell(governed, PROFILE_WRITE)), "beforeShellExecution")
    assert out["permission"] == "ask"
    assert "standard-profile.yaml" in out["user_message"]


def test_ask_on_pre_tool_use_becomes_a_motivated_refusal(governed: Path) -> None:
    payload = _pre(governed, "Shell", {"command": PROFILE_WRITE, "working_directory": str(governed)})
    out = _verdict(_entry(governed, payload), "preToolUse")
    assert out["permission"] == "deny"
    assert "n'applique pas `ask`" in out["user_message"]


def test_allow_is_a_bare_allow(governed: Path) -> None:
    assert _verdict(_entry(governed, _shell(governed, "ls")), "beforeShellExecution") == {"permission": "allow"}
    readme = _pre(governed, "Read", {"file_path": str(governed / "README.md")})
    assert _verdict(_entry(governed, readme), "preToolUse")["permission"] == "allow"


def test_secret_read_is_refused_on_pre_tool_use(governed: Path) -> None:
    out = _verdict(_entry(governed, _pre(governed, "Read", {"file_path": str(governed / ".env")})), "preToolUse")
    assert out["permission"] == "deny"


def test_a_write_without_a_readable_path_fails_closed(governed: Path) -> None:
    out = _verdict(_entry(governed, _pre(governed, "Write", {})), "preToolUse")
    assert out["permission"] == "deny"


def test_import_failure_still_refuses_the_guard_in_cursor_shape(capsys: pytest.CaptureFixture[str]) -> None:
    from grimoire.hosts import hook_entry

    code = hook_entry.import_failure(["--host", "cursor", "--event", "PreToolUse"], "{}", "ImportError: x")
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["permission"] == "deny" and "garde avant outil en panne" in out["user_message"]
    assert set(out) <= PERMISSION_OUTPUT["beforeShellExecution"]


def _stop_hook(root: Path, **raw: Any) -> HookInput:
    payload = {**_common(root), "hook_event_name": "stop", **raw}
    return HookInput(event=HookEvent.STOP, project_root=root, raw=payload)


def test_stop_block_becomes_a_followup_within_the_loop_limit(tmp_path: Path) -> None:
    block = Decision(outcome=Outcome.BLOCK, reason="gates rouges")
    assert render(block, _stop_hook(tmp_path, status="completed", loop_count=0), HostId.CURSOR) == {
        "followup_message": "gates rouges"
    }
    assert render(block, _stop_hook(tmp_path, status="completed", loop_count=5), HostId.CURSOR) == {}
    assert render(block, _stop_hook(tmp_path, status="aborted", loop_count=0), HostId.CURSOR) == {}
    assert render(Decision(context="info"), _stop_hook(tmp_path, status="completed"), HostId.CURSOR) == {}


def test_cursor_payload_is_normalised_to_kit_terms(tmp_path: Path) -> None:
    from grimoire.hosts.runtime import normalize_input

    assert parse_event("beforeShellExecution", host_id=HostId.CURSOR) is HookEvent.PRE_TOOL_USE
    assert parse_event("beforeSubmitPrompt", host_id=HostId.CURSOR) is HookEvent.USER_PROMPT_SUBMIT
    shell = normalize_input(_shell(tmp_path, "ls"), host_id=HostId.CURSOR)
    assert (shell.tool_name, shell.tool_input["command"], shell.session_id) == ("Bash", "ls", "conv-1")
    delete = normalize_input(_pre(tmp_path, "Delete", {"path": "src/a.py"}), host_id=HostId.CURSOR)
    assert (delete.tool_name, delete.tool_input["file_path"]) == ("Write", "src/a.py")
    again = normalize_input(
        {**_common(tmp_path), "hook_event_name": "stop", "status": "completed", "loop_count": 1},
        host_id=HostId.CURSOR,
    )
    # `loop_count` seul ne fait pas un Stop continué : il faut une relance émise par le kit.
    assert again.stop_active is False


def test_session_start_context_uses_the_documented_field(tmp_path: Path) -> None:
    hook = HookInput(event=HookEvent.SESSION_START, project_root=tmp_path, raw={})
    assert render(Decision(context="directive"), hook, HostId.CURSOR) == {"additional_context": "directive"}


# ── non-régression des autres hôtes ──────────────────────────────────────────


@pytest.mark.parametrize("host_id", [HostId.CLAUDE_CODE_CLI, HostId.GITHUB_COPILOT, HostId.GEMINI_CLI])
def test_other_hosts_keep_their_dialect(tmp_path: Path, host_id: HostId) -> None:
    hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, tool_name="Bash", raw={})
    out = render(Decision(outcome=Outcome.DENY, reason="non"), hook, host_id)
    assert "permission" not in out and "user_message" not in out
    assert parse_event("beforeShellExecution", host_id=host_id) is None


def test_abstention_keeps_a_permission_on_pre_tool_use(tmp_path: Path) -> None:
    pre = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, raw={"hook_event_name": "preToolUse"})
    assert render(Decision(abstain=True), pre, HostId.CURSOR) == {"permission": "allow"}
    assert render(Decision(abstain=True), _stop_hook(tmp_path), HostId.CURSOR) == {}


# ── relecture adversariale de #1301 ──────────────────────────────────────────


def _fake_evidence_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """La garde de clôture réduite à sa règle d'entrée : un premier ``Stop`` aux gates rouges refuse,
    un ``Stop`` continué passe (``stop_hook_already_active``)."""
    from grimoire.hosts import runtime

    def decide(decision_id: str, hook: HookInput) -> Decision:
        if hook.event is not HookEvent.STOP:
            return Decision()
        if hook.stop_active:
            return Decision(detail={"skipped": "stop_hook_already_active"})
        return Decision(outcome=Outcome.BLOCK, reason="gates rouges", detail={"ok": False})

    monkeypatch.setattr(runtime, "run_decision", decide)


def _cursor_stop(root: Path, loop_count: int, generation: str) -> dict[str, Any]:
    return {
        **_common(root),
        "generation_id": generation,
        "hook_event_name": "stop",
        "status": "completed",
        "loop_count": loop_count,
    }


def test_stop_with_loop_count_but_no_kit_followup_still_refuses(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``loop_count`` compte les relances de la conversation, pas celles du kit : un autre hook ou une
    relance ancienne ne doit pas éteindre la garde de clôture."""
    from grimoire.hosts.runtime import run_hook

    _fake_evidence_gate(monkeypatch)
    out, _decision, hook = run_hook(_cursor_stop(governed, 1, "gen-9"), host_id=HostId.CURSOR)
    assert hook.stop_active is False
    assert out == {"followup_message": "gates rouges"}


def test_a_stop_after_the_kits_own_followup_is_continued(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts.runtime import run_hook

    _fake_evidence_gate(monkeypatch)
    first, _, _ = run_hook(_cursor_stop(governed, 0, "gen-1"), host_id=HostId.CURSOR)
    assert first == {"followup_message": "gates rouges"}
    assert cursor_wire.followup_state_path(governed, "conv-1").is_file()
    # Le même tour rejoué (même generation_id) n'est pas la suite de la relance.
    replay, _, replay_hook = run_hook(_cursor_stop(governed, 1, "gen-1"), host_id=HostId.CURSOR)
    assert replay_hook.stop_active is False and "followup_message" in replay
    # Le tour qui suit la relance du kit (loop_count + 1, nouvelle génération) est continué.
    run_hook(_cursor_stop(governed, 0, "gen-1"), host_id=HostId.CURSOR)
    after, _, after_hook = run_hook(_cursor_stop(governed, 1, "gen-2"), host_id=HostId.CURSOR)
    assert after_hook.stop_active is True and after == {}
    # Sans relance émise, l'état est effacé : le stop suivant est un premier stop.
    assert not cursor_wire.followup_state_path(governed, "conv-1").exists()


def test_cursor_project_dir_wins_over_a_subfolder_cwd(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts.runtime import locate_project_root, normalize_input

    sub = governed / "vendor" / "lib"
    (sub / "_grimoire").mkdir(parents=True)
    payload = _pre(sub, "Read", {"file_path": str(sub / "README.md")})
    payload["workspace_roots"] = []
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(governed))
    assert normalize_input(payload, host_id=HostId.CURSOR).project_root == governed.resolve()
    assert locate_project_root(payload, host_id=HostId.CURSOR) == (governed.resolve(), True)
    monkeypatch.delenv("CURSOR_PROJECT_DIR")
    payload["workspace_roots"] = [str(governed)]
    assert normalize_input(payload, host_id=HostId.CURSOR).project_root == governed.resolve()


def test_unknown_cursor_event_fails_closed(governed: Path) -> None:
    from grimoire.hosts.gemini_wire import UnjudgeableInputError
    from grimoire.hosts.runtime import normalize_input

    payload = {**_common(governed), "tool_name": "Write", "tool_input": {"file_path": "a.py"}}
    with pytest.raises(UnjudgeableInputError):
        normalize_input(payload, host_id=HostId.CURSOR)
    proc = _entry(governed, payload, event=None)
    assert proc.returncode == 2, proc.stdout
    out = json.loads(proc.stdout)
    assert out["permission"] == "deny" and "événement inconnu" in out["user_message"]
    assert set(out) <= PERMISSION_OUTPUT["beforeShellExecution"]


def test_shell_payload_without_event_name_is_still_judged_as_shell(governed: Path) -> None:
    payload = {k: v for k, v in _shell(governed, DESTRUCTIVE).items() if k != "hook_event_name"}
    out = _verdict(_entry(governed, payload, event=None), "beforeShellExecution")
    assert out["permission"] == "deny"


def test_merge_keeps_a_user_wrapper_whose_name_contains_grimoire_hook(governed: Path) -> None:
    wrapper = {"command": "./my-grimoire-hook-wrapper.sh --host cursor"}
    lookalike = {"command": "grimoire-hook --host claude --event PreToolUse"}
    data = _hooks(_plan(governed), {"version": 1, "hooks": {"preToolUse": [wrapper, lookalike]}})
    assert data["hooks"]["preToolUse"][:2] == [wrapper, lookalike]
    quoted = {"command": '"/opt/my tools/grimoire-hook" --host cursor --event Stop', "timeout": 9}
    data = _hooks(_plan(governed), {"version": 1, "hooks": {"stop": [quoted]}})
    assert quoted not in data["hooks"]["stop"]  # commande exacte du kit : remplacée


@pytest.mark.parametrize(
    "hooks",
    [["not", "an", "object"], {"preToolUse": {"command": "./audit.sh"}}],
    ids=["hooks-not-object", "event-not-list"],
)
def test_malformed_hooks_json_is_preserved_not_overwritten(governed: Path, hooks: Any) -> None:
    hooks_file = governed / ".cursor/hooks.json"
    hooks_file.parent.mkdir(parents=True)
    original = json.dumps({"version": 1, "hooks": hooks})
    hooks_file.write_text(original, encoding="utf-8")
    result = apply_plan(_plan(governed), governed)
    assert hooks_file.read_text(encoding="utf-8") == original
    assert any(label.startswith(".cursor/hooks.json") for label in result.skipped)


def test_other_emitters_merge_behaviour_is_unchanged(governed: Path) -> None:
    """Seule une fusion qui lève ``MergeRefusedError`` est préservée : Claude Code réécrit toujours un
    `hooks` qui n'est pas un objet, comme avant."""
    settings = governed / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": []}), encoding="utf-8")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".claude/settings.json" in result.written
    assert isinstance(json.loads(settings.read_text(encoding="utf-8"))["hooks"], dict)


def test_abstention_choice_is_declared(governed: Path) -> None:
    assert any(d.surface == "hook abstention" for d in _plan(governed).degradations)
