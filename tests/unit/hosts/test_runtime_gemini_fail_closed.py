"""Gemini CLI : le hook échoue fermé sur toute entrée qu'il ne sait pas juger.

Un hook Gemini qui sort en 0 avec ``{}`` n'a rien décidé : la CLI laisse alors
passer l'action. Ces tests verrouillent les seules issues possibles quand la
charge utile ne permet pas de rendre un verdict — code 2 et raison sur stderr —
et les routes explicites d'un outil inconnu (chemin -> écriture, commande ->
shell), plutôt qu'un repli silencieux vers un événement ou une classe qui
n'était pas demandée.

Ce que ces tests ne couvrent pas : le comportement de la CLI Gemini elle-même
(elle n'est pas installée sur le poste) ; les fixtures de protocole restent la
source de vérité de l'entrée.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli import cmd_host
from grimoire.hosts import gemini_wire
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import main, run_hook
from grimoire.policies.schemas import ActionKind

GEMINI = HostId.GEMINI_CLI


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


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str], raw: str) -> tuple[int, str, str]:
    stdin, stdout, stderr = io.StringIO(raw), io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _before_tool(project: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": "00000000-0000-4000-8000-000000000102",
        "cwd": str(project),
        "hook_event_name": "BeforeTool",
        "tool_name": "read_file",
        "tool_input": {"file_path": str(project / "notes.md")},
    }
    payload.update(overrides)
    return payload


def _argv(project: Path, event: str = "BeforeTool") -> list[str]:
    return ["--host", "gemini", "--event", event, "--project-root", str(project)]


# ── 1. Entrée malformée, vide ou non-objet sur un événement bloquant ──────


@pytest.mark.parametrize("event", ["BeforeTool", "BeforeAgent"])
@pytest.mark.parametrize(
    "raw",
    ["", "   \n", "{pas du json", "[]", "42", '"une chaîne"'],
    ids=["vide", "blancs", "json-invalide", "liste", "nombre", "chaine"],
)
def test_malformed_input_on_a_blocking_event_exits_2(
    project: Path, monkeypatch: pytest.MonkeyPatch, event: str, raw: str
) -> None:
    code, out, err = _run_main(monkeypatch, _argv(project, event), raw)

    assert code == 2
    assert out == ""
    assert "refusée par défaut" in err


# ── 2. Événement inconnu ou absent : plus de repli sur SessionStart ───────


@pytest.mark.parametrize(
    ("argv", "payload"),
    [
        (["--host", "gemini", "--event", "BeforTool"], {"session_id": "S1"}),
        (["--host", "gemini"], {"session_id": "S1", "hook_event_name": "PasUnEvenement"}),
        (["--host", "gemini"], {"session_id": "S1"}),
    ],
    ids=["evenement-mal-orthographie", "evenement-inconnu-dans-la-charge", "evenement-absent"],
)
def test_unknown_or_absent_event_on_gemini_exits_2(
    project: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], payload: dict[str, Any]
) -> None:
    code, out, err = _run_main(monkeypatch, [*argv, "--project-root", str(project)], json.dumps(payload))

    assert code == 2
    assert out == ""
    assert "événement" in err.lower()


# ── 3. `tool_name` vide ou absent sur BeforeTool ──────────────────────────


@pytest.mark.parametrize("tool_name", ["", "   ", None], ids=["vide", "blancs", "absent"])
def test_empty_tool_name_on_before_tool_exits_2(
    project: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str | None
) -> None:
    payload = _before_tool(project)
    if tool_name is None:
        payload.pop("tool_name")
    else:
        payload["tool_name"] = tool_name

    code, out, err = _run_main(monkeypatch, _argv(project), json.dumps(payload))

    assert code == 2
    assert out == ""
    assert "tool_name" in err


# ── 4. Outil inconnu : chemin -> écriture, commande -> shell, rien -> 2 ──


@pytest.mark.parametrize("field", gemini_wire.PATH_FIELDS)
def test_unknown_tool_with_a_path_field_is_a_write_on_that_path(
    project: Path, field: str
) -> None:
    target = str(project / "notes.md")
    payload = _before_tool(project, tool_name="extension_inconnue", tool_input={field: target})

    _rendered, _decision, hook = run_hook(payload, host_id=GEMINI, project_root=project)

    assert hook.tool_name == gemini_wire.TOOL_CLASS_WRITE
    facts = classify_tool(hook.tool_name, hook.tool_input)
    assert facts.kind is ActionKind.FILE_WRITE
    assert target in facts.targets


def test_unknown_tool_with_a_command_is_a_shell_command(project: Path) -> None:
    payload = _before_tool(project, tool_name="extension_inconnue", tool_input={"command": "ls -la"})

    _rendered, _decision, hook = run_hook(payload, host_id=GEMINI, project_root=project)

    assert hook.tool_name == gemini_wire.TOOL_CLASS_EXECUTE
    facts = classify_tool(hook.tool_name, hook.tool_input)
    assert facts.kind is ActionKind.TOOL_USE
    assert facts.command == "ls -la"


@pytest.mark.parametrize("tool_input", [{}, {"description": "rien d'exploitable"}], ids=["vide", "sans-champ"])
def test_unknown_tool_without_any_usable_field_exits_2(
    project: Path, monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any]
) -> None:
    payload = _before_tool(project, tool_name="extension_inconnue", tool_input=tool_input)

    code, out, err = _run_main(monkeypatch, _argv(project), json.dumps(payload))

    assert code == 2
    assert out == ""
    assert "extension_inconnue" in err


def test_malformed_input_on_a_non_blocking_event_still_lets_the_hook_work(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'échec fermé ne s'étend pas aux événements indicatifs : rien à refuser."""
    code, out, err = _run_main(monkeypatch, _argv(project, "SessionStart"), "{pas du json")

    assert code == 0
    assert json.loads(out)  # le hook rend quand même son contexte de session
    assert "refusée" not in err


# ── 5. `grimoire host hook` : même échec fermé sur événement bloquant ────


def test_host_hook_exception_on_a_blocking_event_exits_2(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(cmd_host, "run_hook", _boom)
    runner = CliRunner()
    result = runner.invoke(
        cmd_host.host_app,
        ["hook", "--host", "gemini", "--event", "BeforeTool", "--project-root", str(project)],
        input=json.dumps(_before_tool(project)),
    )

    assert result.exit_code == 2
    assert "panne simulée" in result.output
    assert "refusée par défaut" in result.output


def test_host_hook_unknown_event_exits_2(project: Path) -> None:
    """Même sans exception : un événement inconnu n'a pas de verdict à rendre."""
    runner = CliRunner()
    payload = _before_tool(project)
    payload.pop("hook_event_name")
    result = runner.invoke(
        cmd_host.host_app,
        ["hook", "--host", "gemini", "--event", "BeforTool", "--project-root", str(project)],
        input=json.dumps(payload),
    )

    assert result.exit_code == 2
    assert "événement" in result.output.lower()


# ── Une entrée valide et autorisée sort toujours en 0 ─────────────────────


def test_a_valid_allowed_input_still_exits_0(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _before_tool(project, tool_name="read_file", tool_input={"file_path": str(project / "notes.md")})

    code, out, err = _run_main(monkeypatch, _argv(project), json.dumps(payload))

    assert code == 0
    assert err == ""
    assert json.loads(out) == {}


def test_advisory_events_without_a_tool_keep_their_neutral_route(project: Path) -> None:
    """Les événements documentés sans outil gardent leur route neutre."""
    for wire, event in (("SessionStart", HookEvent.SESSION_START), ("PreCompress", HookEvent.PRE_COMPACT)):
        payload = {"session_id": "S1", "cwd": str(project), "hook_event_name": wire}
        _rendered, _decision, hook = run_hook(payload, host_id=GEMINI, project_root=project)
        assert hook.event is event
