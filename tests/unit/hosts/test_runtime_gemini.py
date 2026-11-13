"""Le point d'entrée du kit parle le protocole de hooks de Gemini CLI.

Les charges utiles sont des **fixtures de protocole**, pas des captures : la
CLI Gemini n'est pas installée sur le poste de développement, donc chaque
événement documenté a sa fixture sous ``tests/fixtures/hosts/gemini/hooks/``
(voir :mod:`grimoire.hosts.gemini_wire` pour les points « non vérifiés »).

Ce que ces tests verrouillent :

- chaque nom d'événement Gemini atteint l'événement neutre que les décisions
  du kit répondent déjà ;
- une écriture dans la zone de contrôle est refusée **dans le format de
  l'hôte** (``decision``/``reason``), pas dans un format interne ;
- une erreur interne sur un événement bloquant sort en code 2 avec la raison
  sur stderr (échec fermé) ;
- un outil inconnu est classé comme commande shell, la classe la plus stricte.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts import gemini_wire, runtime
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import main, parse_event, run_hook

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/hosts/gemini/hooks"

#: Une fixture par événement documenté, et l'événement neutre qu'elle alimente.
EVENT_CASES = [
    ("session-start", "SessionStart", HookEvent.SESSION_START),
    ("before-tool", "BeforeTool", HookEvent.PRE_TOOL_USE),
    ("after-tool", "AfterTool", HookEvent.POST_TOOL_USE),
    ("before-agent", "BeforeAgent", HookEvent.USER_PROMPT_SUBMIT),
    ("after-agent", "AfterAgent", HookEvent.STOP),
    ("session-end", "SessionEnd", HookEvent.STOP),
    ("pre-compress", "PreCompress", HookEvent.PRE_COMPACT),
]


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucune racine ambiante ne doit primer sur la racine passée par le test."""
    for var in ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / "_grimoire").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    return root


def _payload(name: str, project: Path) -> dict[str, object]:
    raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    raw = raw.replace("<PROJECT>", str(project)).replace("<HOME>", str(project.parent / "home"))
    return json.loads(raw)


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str], payload: dict[str, object]) -> tuple[int, str, str]:
    stdin, stdout, stderr = io.StringIO(json.dumps(payload)), io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize(("name", "wire", "event"), EVENT_CASES)
def test_every_event_fixture_reaches_its_neutral_event(project: Path, name: str, wire: str, event: HookEvent) -> None:
    payload = _payload(name, project)
    assert payload["hook_event_name"] == wire
    assert parse_event(wire, host_id=HostId.GEMINI_CLI) is event

    _rendered, _decision, hook = run_hook(payload, host_id=HostId.GEMINI_CLI, project_root=project)
    assert hook.event is event
    assert hook.session_id == payload["session_id"]
    assert hook.project_root == project.resolve()


def test_a_control_zone_write_is_refused_in_the_host_format(project: Path) -> None:
    payload = _payload("before-tool", project)

    rendered, decision, hook = run_hook(payload, host_id=HostId.GEMINI_CLI, project_root=project)

    assert hook.tool_name == gemini_wire.TOOL_CLASS_WRITE
    assert decision.outcome is Outcome.DENY
    assert rendered == {"decision": "deny", "reason": decision.reason}
    assert "authority" in rendered["reason"].lower()
    assert "host-contracts.json" in rendered["reason"]


def test_main_emits_the_refusal_json_on_stdout(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload("before-tool", project)
    argv = ["--host", "gemini", "--event", "BeforeTool", "--project-root", str(project)]

    code, out, err = _run_main(monkeypatch, argv, payload)

    assert code == 0
    assert err == ""
    verdict = json.loads(out)
    assert verdict["decision"] == "deny"
    assert verdict["reason"]


def test_before_agent_refusal_uses_the_same_deny_format() -> None:
    hook = HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=Path())
    decision = Decision(outcome=Outcome.DENY, reason="message refusé")

    assert gemini_wire.render(decision, hook) == {"decision": "deny", "reason": "message refusé"}


def test_ask_is_refused_rather_than_silently_allowed() -> None:
    """Gemini ne sait pas interroger l'humain depuis un hook : ``ask`` refuse."""
    hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=Path())
    decision = Decision(outcome=Outcome.ASK, reason="à confirmer")

    assert gemini_wire.render(decision, hook) == {"decision": "deny", "reason": "à confirmer"}


def test_a_refusal_without_wording_still_carries_a_reason() -> None:
    hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=Path())
    rendered = gemini_wire.render(Decision(outcome=Outcome.DENY), hook)

    assert rendered["decision"] == "deny"
    assert rendered["reason"] == gemini_wire.DEFAULT_DENY_REASON


def test_session_start_context_uses_additional_context() -> None:
    hook = HookInput(event=HookEvent.SESSION_START, project_root=Path())
    decision = Decision(context="Persona d'entrée : concierge")

    assert gemini_wire.render(decision, hook) == {"additionalContext": "Persona d'entrée : concierge"}


def test_before_agent_context_is_injected_too() -> None:
    """La décision de prompt porte le rappel de tâche : il ne doit pas être perdu."""
    hook = HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=Path())
    decision = Decision(context="Tâche courante : GAO-gemini-cli-l-001")

    assert gemini_wire.render(decision, hook) == {"additionalContext": "Tâche courante : GAO-gemini-cli-l-001"}


def test_a_stop_refusal_is_not_claimed_as_enforced() -> None:
    """``AfterAgent`` peut-il bloquer ? Non vérifié : aucun verdict n'est émis."""
    hook = HookInput(event=HookEvent.STOP, project_root=Path())
    decision = Decision(outcome=Outcome.BLOCK, reason="gates rouges")

    assert gemini_wire.render(decision, hook) == {}


@pytest.mark.parametrize(
    ("tool", "klass"),
    [
        ("read_file", gemini_wire.TOOL_CLASS_READ),
        ("read_many_files", gemini_wire.TOOL_CLASS_READ),
        ("list_directory", gemini_wire.TOOL_CLASS_READ),
        ("grep_search", gemini_wire.TOOL_CLASS_READ),
        ("write_file", gemini_wire.TOOL_CLASS_WRITE),
        ("replace", gemini_wire.TOOL_CLASS_WRITE),
        ("run_shell_command", gemini_wire.TOOL_CLASS_EXECUTE),
    ],
)
def test_tool_names_map_to_the_kit_classes(tool: str, klass: str) -> None:
    assert gemini_wire.canonical_tool_name(tool) == klass


def test_unknown_tool_is_the_strictest_class(project: Path) -> None:
    assert gemini_wire.canonical_tool_name("definitely_unknown") == gemini_wire.UNKNOWN_TOOL_CLASS
    assert gemini_wire.canonical_tool_name("") == ""

    payload = _payload("before-tool", project)
    payload["tool_name"] = "definitely_unknown"
    payload["tool_input"] = {"command": "cat .env"}

    _rendered, decision, hook = run_hook(payload, host_id=HostId.GEMINI_CLI, project_root=project)

    assert hook.tool_name == gemini_wire.TOOL_CLASS_EXECUTE
    assert decision.outcome is Outcome.DENY


@pytest.mark.parametrize("name", ["before-tool", "before-agent"])
def test_internal_error_on_a_blocking_event_exits_2(project: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    payload = _payload(name, project)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(runtime, "run_hook", _boom)
    argv = ["--host", "gemini", "--event", payload["hook_event_name"]]

    code, out, err = _run_main(monkeypatch, argv, payload)

    assert code == 2
    assert out == ""
    assert "panne simulée" in err
    assert "refusée par défaut" in err


def test_missing_event_option_still_fails_closed_from_the_payload(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même sans ``--event``, la charge utile nomme l'événement bloquant."""
    payload = _payload("before-tool", project)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(runtime, "run_hook", _boom)

    code, out, err = _run_main(monkeypatch, ["--host", "gemini"], payload)

    assert code == 2
    assert out == ""
    assert "BeforeTool" in err


def test_internal_error_on_an_advisory_event_stays_out_of_the_way(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload("session-start", project)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(runtime, "run_hook", _boom)
    argv = ["--host", "gemini", "--event", "SessionStart"]

    code, out, err = _run_main(monkeypatch, argv, payload)

    assert code == 0
    assert json.loads(out) == {}
    assert "panne simulée" in err
    assert "laissée passer" in err


def test_internal_error_keeps_the_existing_behaviour_for_other_hosts(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code, Copilot et opencode ne changent pas : l'erreur remonte."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(runtime, "run_hook", _boom)

    with pytest.raises(RuntimeError, match="panne simulée"):
        _run_main(monkeypatch, ["--host", "claude", "--event", "PreToolUse"], {"cwd": str(project)})
