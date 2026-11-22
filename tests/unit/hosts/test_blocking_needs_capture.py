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
    GovernanceFunction,
    ParityLevel,
    all_profiles,
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
    assert "introuvable" in unproven_blocking_claims(missing, FIXTURES)[0]
    # Une fixture qui existe mais ne prouve pas le refus (fin de session) ne suffit pas.
    unrelated = replace(claude, blocking_captures=("claude-code/02-session-end.json",))
    assert "pas une preuve vérifiée" in unproven_blocking_claims(unrelated, FIXTURES)[0]
    # Copilot a des fixtures pretooluse_deny, mais non vérifiées : elles ne prouvent rien.
    copilot = replace(
        profile_for(HostId.GITHUB_COPILOT),
        blocking_events=None,
        blocking_hooks=True,
        blocking_captures=("copilot/copilot-harness/pretooluse-single.json",),
    )
    assert "pas une preuve vérifiée" in unproven_blocking_claims(copilot, FIXTURES)[0]


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
    assert claude["surfaces"]["blocking_hooks"] is True and claude["unverified_blocking_events"] == []


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
