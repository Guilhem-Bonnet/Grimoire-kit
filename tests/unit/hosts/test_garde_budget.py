"""La garde avant outil échoue fermé quand son analyse dépasse son budget.

Un hook Claude Code qui dépasse son ``timeout`` ne bloque pas l'outil, sauf
``"onFailure": "block"`` sur son gestionnaire. Deux défenses, testées ici sur
des chaînes (rien n'est exécuté) :

- côté décision : une borne de taille (refus sans analyse) et un budget de
  temps nettement sous le délai émis (refus sans révocation) ;
- côté émetteur : ``onFailure: block`` sur la garde ``PreToolUse`` seule, et
  seulement quand le lanceur du hook est trouvé.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions import HookInput, Outcome, decide_tool_policy, tool_policy
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import UNRESOLVED
from grimoire.hosts.runtime import run_hook

pytestmark = pytest.mark.covers("host:claude-code-cli", level="integration")

#: L'enveloppe répétée : chaque mot est banal, la ligne entière ne l'est pas.
_ENVELOPE = "git -C /srv/depot "


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _hook(root: Path, command: str) -> HookInput:
    return HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=root,
        tool_name="Bash",
        tool_input={"command": command},
        session_id="session-budget",
    )


@pytest.fixture
def revocations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("grimoire.missions.mandate.revoke_every_mandate", spy)
    monkeypatch.setattr("grimoire.missions.mandate.revoke_session_mandates", spy)
    return calls


def test_a_giant_command_line_is_refused_in_bounded_time(governed: Path, revocations: list[tuple[Any, ...]]) -> None:
    command = _ENVELOPE * 20_000 + "status"
    started = time.perf_counter()
    rendered, decision, _ = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    elapsed = time.perf_counter() - started
    assert decision.outcome is Outcome.DENY
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert decision.detail["policy_budget"] == "oversized"
    # Sur main, l'analyse de cette ligne durait plus de 7 s : le délai du hook (10 s) était à portée.
    assert elapsed < 1.0, f"refus en {elapsed:.2f} s"
    assert not revocations


def test_too_many_words_are_refused_without_analysis(governed: Path) -> None:
    command = "echo" + " a" * (tool_policy.MAX_COMMAND_WORDS + 1)
    assert len(command) <= tool_policy.MAX_COMMAND_CHARS
    decision = decide_tool_policy(_hook(governed, command))
    assert decision.outcome is Outcome.DENY
    assert decision.detail["policy_budget"] == "oversized"


def test_an_ordinary_command_is_still_allowed(governed: Path) -> None:
    decision = decide_tool_policy(_hook(governed, "git -C /srv/depot log --oneline -5"))
    assert decision.outcome is Outcome.ALLOW
    assert "policy_budget" not in decision.detail


def test_a_long_but_real_sized_command_is_still_judged(governed: Path) -> None:
    """Sous les bornes (environ 3 fois la plus longue commande réelle mesurée), la ligne est analysée."""
    command = _ENVELOPE * 1_000 + "status"
    assert len(command) < tool_policy.MAX_COMMAND_CHARS
    decision = decide_tool_policy(_hook(governed, command))
    assert decision.outcome is Outcome.ALLOW


def test_an_exceeded_budget_refuses_without_revoking(
    governed: Path, monkeypatch: pytest.MonkeyPatch, revocations: list[tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(tool_policy, "POLICY_BUDGET_S", 0.01)
    # Sous les bornes de taille, mais assez longue pour que l'analyse réelle dépasse 10 ms.
    command = _ENVELOPE * 2_000 + "status"
    assert len(command) < tool_policy.MAX_COMMAND_CHARS
    started = time.perf_counter()
    decision = decide_tool_policy(_hook(governed, command))
    elapsed = time.perf_counter() - started
    assert decision.outcome is Outcome.DENY
    assert decision.detail["policy_budget"] == "exceeded"
    assert "budget" in decision.reason
    assert elapsed < 0.5
    assert not revocations


def test_an_error_inside_the_budgeted_analysis_still_reaches_the_caller(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le fil d'analyse ne doit pas avaler une panne : ``run_decision`` la change en ``ask``, jamais en ``allow``."""
    from grimoire.hosts.decisions import run_decision

    def boom(_hook: HookInput) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(tool_policy, "_evaluate_tool_policy", boom)
    decision = run_decision("grimoire.tool-policy", _hook(governed, "git status"))
    assert decision.outcome is Outcome.ASK
    assert "panne simulée" in decision.reason


def _claude_hooks(root: Path) -> tuple[dict[str, Any], Any]:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    plan = emitter.plan(build_surface(root), root)
    (merge,) = plan.merges
    return merge.merge({})["hooks"], plan


def test_the_claude_emitter_makes_only_the_pretooluse_guard_fail_closed(governed: Path) -> None:
    hooks, _ = _claude_hooks(governed)
    assert "PreToolUse" in hooks
    for event, entries in hooks.items():
        for entry in entries:
            for handler in entry["hooks"]:
                if "--decision grimoire.dispatch-model" in handler["command"]:
                    # Consultatif : imposer un modèle n'a rien à garder, sa panne laisse passer l'appel.
                    assert "onFailure" not in handler, json.dumps(handler)
                elif event == "PreToolUse":
                    assert handler["onFailure"] == "block", json.dumps(handler)
                else:
                    assert "onFailure" not in handler, f"{event} : {json.dumps(handler)}"


def test_an_unresolved_launcher_keeps_the_guard_open_and_says_so(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un ``grimoire-hook`` introuvable en échec fermé refuserait chaque appel d'outil gardé."""
    monkeypatch.setattr("grimoire.hosts.emitters.claude_code.resolve_hook_launcher", lambda _root: UNRESOLVED)
    hooks, plan = _claude_hooks(governed)
    handlers = [h for entry in hooks["PreToolUse"] for h in entry["hooks"]]
    assert handlers
    assert all("onFailure" not in h for h in handlers)
    assert "hook onFailure" in {d.surface for d in plan.degradations}
