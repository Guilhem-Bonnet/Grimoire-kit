"""Every command the generated surfaces name must exist in the CLI.

Skills and slash commands are instructions an agent follows literally. A
`grimoire` invocation that no longer resolves does not fail loudly — the agent
reports the error as a project problem, or worse, improvises around it. That
failure mode is invisible in review and expensive in session, so it is pinned
here instead: the generated content is parsed and every command path is walked
against the real Typer tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest
import typer.main

from grimoire.cli.app import app
from grimoire.data import framework_path

#: Options that take a value; their argument is not a command segment.
_VALUE_OPTIONS = {
    "-o",
    "--output",
    "-p",
    "--profile",
    "-t",
    "--type",
    "-a",
    "--agent",
    "--task-id",
    "--host",
    "--event",
    "--project-root",
    "--needs",
    "--pattern",
    "--format",
}

#: `grimoire …` occurrences inside inline code or fenced blocks.
_INVOCATION_RE = re.compile(r"`(grimoire [^`\n]+)`|^\s*(grimoire .+)$", re.MULTILINE)


def _sources() -> list[Path]:
    """Every file this layer publishes as an instruction to an agent.

    The kit's Copilot workflow prompts are included: they are collected as
    commands and rendered on every host, so a dead command reference in one of
    them fails exactly the same way.
    """
    root = framework_path()
    return sorted((root / "hosts").rglob("*.md")) + sorted((root / "copilot" / "prompts").glob("*.md"))


def _invocations(text: str) -> list[str]:
    found: list[str] = []
    for match in _INVOCATION_RE.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        raw = raw.strip().rstrip("\\")
        if raw:
            found.append(raw)
    return found


def _command_path(invocation: str) -> list[str]:
    """Command segments of an invocation, options and arguments removed."""
    tokens = invocation.split()[1:]  # drop the executable
    segments: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in _VALUE_OPTIONS and "=" not in token:
                skip_next = True
            continue
        segments.append(token)
    return segments


def _is_group(command: object) -> bool:
    """Group-ness by capability, not by class.

    Recent Typer vendors its own click fork, so ``TyperGroup`` is not a
    subclass of ``click.Group``; an isinstance check silently answers "leaf"
    for every group and the guard stops guarding. Asking for the method that
    matters holds across both layouts.
    """
    return callable(getattr(command, "get_command", None)) and callable(
        getattr(command, "list_commands", None)
    )


def _walk(segments: list[str]) -> tuple[bool, str]:
    """Walk *segments* down the CLI tree.

    A group only accepts its own subcommands, so an unmatched segment there is
    a broken reference at any depth. Once a leaf command is reached, whatever
    follows is its arguments and the walk stops.
    """
    command = typer.main.get_command(app)
    ctx = click.Context(command)
    for segment in segments:
        if not _is_group(command):
            return True, ""  # remaining tokens are this command's arguments
        child = command.get_command(ctx, segment)  # type: ignore[attr-defined]
        if child is None:
            return False, f"sous-commande inconnue de `{command.name}` : {segment}"
        command = child
        ctx = click.Context(command, parent=ctx)
    return True, ""


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_generated_invocations_resolve(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    failures: list[str] = []
    for invocation in _invocations(text):
        segments = _command_path(invocation)
        if not segments:
            continue
        ok, reason = _walk(segments)
        if not ok:
            failures.append(f"{invocation} — {reason}")
    assert not failures, f"{source.name} référence des commandes inexistantes :\n" + "\n".join(failures)


def test_the_guard_actually_catches_a_bad_command() -> None:
    """A guard that cannot fail proves nothing."""
    ok, reason = _walk(_command_path("grimoire agent list"))
    assert not ok
    assert "agent" in reason
    assert not _walk(_command_path("grimoire standard gatecheck"))[0]


def test_the_guard_accepts_a_real_command() -> None:
    assert _walk(_command_path("grimoire -o json standard gate check --strict"))[0]
    assert _walk(_command_path('grimoire memory search "sujet"'))[0]


def test_the_walk_actually_descends_into_groups() -> None:
    """Guards against a group being mistaken for a leaf, which disarms the check."""
    assert _is_group(typer.main.get_command(app))
    assert not _walk(_command_path("grimoire standard nawak"))[0]
    assert not _walk(_command_path("grimoire memory nawak"))[0]
