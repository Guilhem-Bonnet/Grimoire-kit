"""#1281 : aucun hôte n'est déclaré bloquant (``enforced``) sans capture réelle.

Une capacité documentée mais non prouvée vaut ``advisory``. Ce test est transverse :
il parcourt tous les profils, et tout profil qui tient un événement pour bloquant
doit nommer ses captures (``HostProfile.blocking_captures``), présentes sous
``tests/fixtures/hosts/`` et rattachées à la preuve vérifiée ``pretooluse_deny`` de
son contrat. Ce que le kit **déclare** (matrice, ``host list``, ``gaps_for``) est
séparé de ce qu'il **écrit** (``writes_refusal`` : le refus reste rendu au runtime).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import (
    PROVING_KEYS,
    GovernanceFunction,
    ParityLevel,
    all_profiles,
    capture_shows_refusal,
    function_states,
    gaps_for,
    profile_for,
    recorded_contract,
    unproven_blocking_claims,
)
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import render

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts"

#: Hôtes dont un refus a été capturé sur l'hôte réel (fixtures versées).
PROVEN = {HostId.CLAUDE_CODE_CLI, HostId.OPENCODE}
#: Hôtes qui documentent un refus jamais observé : garde écrite, déclarée advisory.
DOCUMENTED_ONLY = {HostId.GITHUB_COPILOT, HostId.CODEX, HostId.CURSOR, HostId.GEMINI_CLI}


@pytest.mark.parametrize("profile", all_profiles(), ids=lambda p: p.host_id.value)
def test_no_profile_claims_a_refusal_without_a_capture(profile) -> None:
    assert unproven_blocking_claims(profile, FIXTURES) == ()


@pytest.mark.parametrize("profile", all_profiles(), ids=lambda p: p.host_id.value)
def test_a_proven_pre_tool_use_matches_the_recorded_contract(profile) -> None:
    proven = HookEvent.PRE_TOOL_USE in profile.proven_blocking_events()
    assert proven == recorded_contract(profile.host_id).evidence["pretooluse_deny"].verified


def test_only_captured_hosts_enforce() -> None:
    assert {p.host_id for p in all_profiles() if p.enforces_blocking} == PROVEN
    for host in DOCUMENTED_ONLY:
        profile = profile_for(host)
        assert not profile.blocking_hooks and not profile.blocking_captures, host
        assert profile.documents_unverified_block(HookEvent.PRE_TOOL_USE), host


def test_a_claim_without_capture_is_reported() -> None:
    lying = replace(profile_for(HostId.GEMINI_CLI), blocking_hooks=True, blocking_events=None)
    (problem,) = unproven_blocking_claims(lying, FIXTURES)
    assert "sans aucune capture" in problem and "pre_tool_use" in problem


def test_a_missing_or_unverified_capture_is_reported() -> None:
    claude = profile_for(HostId.CLAUDE_CODE_CLI)
    missing = replace(claude, blocking_captures=("claude-code/absent.json",))
    assert any("introuvable" in p for p in unproven_blocking_claims(missing, FIXTURES))
    # Une fixture qui existe mais ne prouve pas le refus (fin de session) ne suffit pas.
    unrelated = replace(claude, blocking_captures=("claude-code/02-session-end.json",))
    assert any("pas une preuve vérifiée" in p for p in unproven_blocking_claims(unrelated, FIXTURES))
    # Copilot a des fixtures pretooluse_deny, mais non vérifiées : elles ne prouvent rien.
    copilot = replace(
        profile_for(HostId.GITHUB_COPILOT),
        blocking_events=None,
        blocking_hooks=True,
        blocking_captures=("copilot/copilot-harness/pretooluse-single.json",),
    )
    assert any("pas une preuve vérifiée" in p for p in unproven_blocking_claims(copilot, FIXTURES))


@pytest.mark.parametrize("host", sorted(DOCUMENTED_ONLY, key=lambda h: h.value))
def test_gaps_say_advisory_never_permissions_through_a_hook(host: HostId) -> None:
    gaps = {g.surface: g for g in gaps_for(profile_for(host))}
    assert "non vérifiée" in gaps["blocking_hooks"].fallback
    assert HookEvent.PRE_TOOL_USE.value in gaps["blocking_hooks"].events
    assert "permissions" not in gaps


@pytest.mark.parametrize("host", sorted(DOCUMENTED_ONLY, key=lambda h: h.value))
def test_the_tool_guard_is_never_rich_without_capture(host: HostId) -> None:
    assert function_states(host)[GovernanceFunction.TOOL_GUARD].level is ParityLevel.DEGRADED


def test_copilot_write_trace_is_degraded() -> None:
    state = function_states(HostId.GITHUB_COPILOT)[GovernanceFunction.WRITE_TRACE]
    assert state.level is ParityLevel.DEGRADED and "non vérifié" in state.reason


def test_profile_dict_reports_proof_not_documentation() -> None:
    gemini = profile_for(HostId.GEMINI_CLI).to_dict()
    assert gemini["surfaces"]["blocking_hooks"] is False
    assert gemini["unverified_blocking_events"] == ["user_prompt_submit", "pre_tool_use"]
    claude = profile_for(HostId.CLAUDE_CODE_CLI).to_dict()
    assert claude["surfaces"]["blocking_hooks"] is True and claude["unverified_blocking_events"] == [
        "user_prompt_submit",
        "post_tool_use",
        "subagent_stop",
        "stop",
    ]


def test_host_list_marks_documented_hosts_as_advisory() -> None:
    from grimoire.cli.app import app

    result = CliRunner().invoke(app, ["host", "list"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    rows = {
        name: line
        for line in result.output.splitlines()
        for name in ("Claude Code", "Copilot", "Gemini", "opencode")
        if name in line
    }
    for name in ("Copilot", "Gemini"):
        assert "consultatif" in rows[name], rows[name]
    for name in ("Claude Code", "opencode"):
        assert "consultatif" not in rows[name], rows[name]


# ── le comportement reste : le refus documenté est toujours écrit ─────────────


def test_copilot_still_renders_its_refusal_and_its_closure_block() -> None:
    """Déclarer advisory ne retire pas le refus : le runtime l'écrit toujours."""
    pre = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=Path("/p"), tool_name="Bash")
    out = render(Decision(outcome=Outcome.DENY, reason="[Grimoire] non."), pre, HostId.GITHUB_COPILOT)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    stop = HookInput(event=HookEvent.STOP, project_root=Path("/p"))
    out = render(Decision(outcome=Outcome.BLOCK, reason="[Grimoire] gates rouges."), stop, HostId.GITHUB_COPILOT)
    assert out["decision"] == "block"


def test_an_undocumented_event_gets_context_not_a_refusal() -> None:
    copilot = profile_for(HostId.GITHUB_COPILOT)
    assert not copilot.writes_refusal(HookEvent.SESSION_START)
    assert profile_for(HostId.CLAUDE_CODE_CLI).writes_refusal(HookEvent.STOP)


def test_host_status_separates_proven_and_advisory_blocking_hooks(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude", "gemini"]\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    by_host = {}
    for host in ("claude", "gemini"):
        result = runner.invoke(app, ["-o", "json", "host", "status", "--host", host, "--project-root", str(tmp_path)])
        by_host[host] = json.loads(result.output)[0]
    if not by_host["claude"]["blocking_hooks"]:
        pytest.skip("le projet minimal ne déclare aucun hook bloquant")
    assert by_host["claude"]["advisory_blocking_hooks"] == []
    assert by_host["gemini"]["blocking_hooks"] == []
    assert HookEvent.PRE_TOOL_USE.value in by_host["gemini"]["advisory_blocking_hooks"]
    text = runner.invoke(app, ["host", "status", "--host", "gemini", "--project-root", str(tmp_path)]).output
    assert "hooks consultatifs" in text


# ── relecture de #1304 : une capture prouve un refus, et un seul événement ────


@pytest.mark.parametrize(
    ("host", "allow_only"),
    [
        (HostId.CLAUDE_CODE_CLI, "claude-code/01-pretooluse-control-allow.json"),
        (HostId.OPENCODE, "opencode/01-pretooluse-control-allow.json"),
    ],
)
def test_an_allow_capture_alone_does_not_prove_a_refusal(host: HostId, allow_only: str) -> None:
    profile = replace(profile_for(host), blocking_captures=(allow_only,))
    problems = unproven_blocking_claims(profile, FIXTURES)
    assert any("ne montre un refus honoré" in p for p in problems), problems


def test_capture_shows_refusal_reads_the_verdict() -> None:
    def load(rel: str) -> dict:
        return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))

    assert capture_shows_refusal(load("claude-code/01-pretooluse-deny-exit2.json"))
    assert capture_shows_refusal(load("claude-code/01-pretooluse-deny-json.json"))
    assert capture_shows_refusal(load("opencode/01-pretooluse-deny-throw.json"))
    assert not capture_shows_refusal(load("claude-code/01-pretooluse-control-allow.json"))
    assert not capture_shows_refusal(load("opencode/01-pretooluse-control-allow.json"))
    assert not capture_shows_refusal({})
    # Un verdict de refus sans action arrêtée (ou l'inverse) ne suffit pas.
    assert not capture_shows_refusal({"observed": {"hook_exit_code": 2, "tool_executed": True}})
    assert not capture_shows_refusal({"observed": {"tool_executed": False, "hook_exit_code": 1}})


@pytest.mark.parametrize("content", ["pas du json", "[1, 2]"])
def test_an_unreadable_capture_proves_nothing(tmp_path: Path, content: str) -> None:
    rel = "claude-code/01-pretooluse-deny-exit2.json"
    (tmp_path / "claude-code").mkdir()
    (tmp_path / rel).write_text(content, encoding="utf-8")
    profile = replace(profile_for(HostId.CLAUDE_CODE_CLI), blocking_captures=(rel,))
    assert any("ne montre un refus honoré" in p for p in unproven_blocking_claims(profile, tmp_path))


def test_a_pre_tool_use_capture_proves_pre_tool_use_only() -> None:
    claude = profile_for(HostId.CLAUDE_CODE_CLI)
    assert claude.proven_blocking_events() == (HookEvent.PRE_TOOL_USE,)
    for event in (HookEvent.STOP, HookEvent.SUBAGENT_STOP, HookEvent.USER_PROMPT_SUBMIT):
        assert claude.documents_unverified_block(event), event
    assert set(PROVING_KEYS) == {HookEvent.PRE_TOOL_USE}
    # Revenir à « tout bloque » est signalé : Stop n'a aucune clé de contrat qui le prouve.
    lying = replace(claude, blocking_events=None)
    problems = unproven_blocking_claims(lying, FIXTURES)
    assert any("stop tenu pour bloquant sans clé de contrat" in p for p in problems), problems


def test_claude_code_stop_is_advisory_in_the_matrix(tmp_path: Path) -> None:
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.matrix import ADVISORY, ENFORCED, build_matrix
    from grimoire.hosts.surface import Enforcement, HookSpec

    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n',
        encoding="utf-8",
    )
    surface = build_surface(tmp_path)
    extra = (HookSpec(HookEvent.STOP, "grimoire.evidence-gate", Enforcement.BLOCKING),)
    hooks = (*(h for h in surface.hooks if h.event is not HookEvent.STOP), *extra)
    matrix = build_matrix(tmp_path, (HostId.CLAUDE_CODE_CLI,), surface=replace(surface, hooks=hooks))
    cells = {c.subject: c for c in matrix.for_host(HostId.CLAUDE_CODE_CLI)}
    assert cells["hook:pre_tool_use"].status == ENFORCED
    assert cells["hook:stop"].status == ADVISORY and "non vérifié" in cells["hook:stop"].reason


_DENY = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "raison"}


def test_claude_code_guard_rendering_is_byte_identical() -> None:
    """Annoncer Stop advisory ne change pas un octet de ce que Claude Code reçoit."""
    root = Path("/p")
    pre = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name="Bash")
    assert render(Decision(outcome=Outcome.DENY, reason="raison"), pre, HostId.CLAUDE_CODE_CLI) == {
        "hookSpecificOutput": _DENY,
        "permissionDecision": "deny",
        "permissionDecisionReason": "raison",
    }
    for event in (HookEvent.STOP, HookEvent.SUBAGENT_STOP):
        hook = HookInput(event=event, project_root=root)
        for context in ("", "contexte"):
            decision = Decision(outcome=Outcome.BLOCK, reason="raison", context=context)
            assert render(decision, hook, HostId.CLAUDE_CODE_CLI) == {"decision": "block", "reason": "raison"}


@pytest.mark.parametrize(
    ("host", "flat"),
    [(HostId.CLAUDE_CODE_CLI, True), (HostId.GITHUB_COPILOT, True), (HostId.OPENCODE, False)],
)
def test_the_pre_tool_use_refusal_is_always_written(host: HostId, flat: bool) -> None:
    """Le refus PreToolUse ne dépend pas de ce qui est prouvé : il est écrit pour ces trois hôtes."""
    assert profile_for(host).writes_refusal(HookEvent.PRE_TOOL_USE)
    pre = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=Path("/p"), tool_name="Bash")
    out = render(Decision(outcome=Outcome.DENY, reason="raison"), pre, host)
    assert out["hookSpecificOutput"] == _DENY
    assert (out.get("permissionDecision") == "deny") is flat


def test_copilot_refusal_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io
    import sys

    from grimoire.hosts import runtime

    monkeypatch.setattr(runtime, "_decide", lambda *_a: Decision(outcome=Outcome.DENY, reason="[Grimoire] non."))
    payload = {"hookEventName": "preToolUse", "toolName": "bash", "toolArgs": '{"command": "ls"}', "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = runtime.main(["--host", "copilot", "--event", "PreToolUse", "--project-root", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.out)["permissionDecision"] == "deny"
    assert "[Grimoire] non." in captured.err


# ── L7 : une capture de la sonde ne compte que si elle montre un refus ────────


@pytest.mark.parametrize(
    "stdout",
    [
        {"permission": "deny"},  # Cursor
        {"permissionDecision": "deny"},  # Copilot
        {"decision": "deny"},  # Gemini, Antigravity
    ],
)
def test_flat_deny_verdicts_count_only_when_the_action_did_not_run(stdout: dict) -> None:
    observed = {"tool_executed": False, "hook_called": True, "hook_exit_code": 0, "hook_stdout": stdout}
    assert capture_shows_refusal({"observed": observed})
    assert not capture_shows_refusal({"observed": {**observed, "tool_executed": True}})


def test_a_probe_capture_whose_hook_was_never_called_proves_nothing() -> None:
    """Le modèle n'a rien tenté, ou l'hôte n'a pas chargé le hook : aucun refus n'a été observé."""
    observed = {"tool_executed": False, "hook_called": False, "hook_exit_code": 2}
    assert not capture_shows_refusal({"observed": observed})


@pytest.mark.parametrize("verdict", ["executed", "inconclusive", "error"])
def test_a_promoted_probe_capture_without_refusal_is_reported(tmp_path: Path, verdict: str) -> None:
    rel = "claude-code/01-pretooluse-deny-probe-1.0.0.json"
    (tmp_path / "claude-code").mkdir()
    observed = {"tool_executed": verdict == "executed", "hook_called": verdict == "executed"}
    (tmp_path / rel).write_text(json.dumps({"verdict": verdict, "observed": observed}), encoding="utf-8")
    profile = replace(profile_for(HostId.CLAUDE_CODE_CLI), blocking_captures=(rel,))
    problems = unproven_blocking_claims(profile, tmp_path)
    assert any("ne montre un refus honoré" in p for p in problems), problems
