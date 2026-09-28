"""Tests for :mod:`grimoire.hosts.decisions.tool_facts` — the read/write split.

Regression for issue #643: two shapes of command wrote or deleted while being
classified read-only, so they never consumed ``per_session.max_writes`` and
left no mutation trace in the evidence pack.

1. ``find`` is a blanket read-only leading command, but ``-delete``/``-exec``
   (and siblings) turn it into a mutation or a destructive act; only
   ``-exec``/``rm`` was ever caught, via the destructive-pattern scan, not the
   read-only classifier itself.
2. :func:`_strip_heredoc_bodies` erased a heredoc's body starting right after
   the opening ``<<TAG``, on the same line — which also erases any
   redirection (``> file``, ``>> file``) that followed ``<<TAG`` on that same
   opening line, before :data:`_WRITE_REDIRECTION_RE` ever sees it.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool, command_surface
from grimoire.hosts.decisions.tool_facts import is_read_only_command

# ── find: -delete, -exec ────────────────────────────────────────────────────


def test_find_delete_is_not_read_only() -> None:
    assert is_read_only_command('find . -name "*.pyc" -delete') is False


def test_find_exec_rm_is_destructive() -> None:
    """Non-regression: already caught today, via the ``rm`` destructive pattern."""
    facts = classify_tool("Bash", {"command": "find . -exec rm -rf {} +"})
    assert facts.destructive_reason


def test_find_without_a_mutating_primary_stays_read_only() -> None:
    assert is_read_only_command('find . -name "*.py"') is True


# ── heredoc: redirection on the opening line must survive body-stripping ───


@pytest.mark.parametrize("redirect", [">", ">>"])
def test_heredoc_with_redirection_is_not_read_only(redirect: str) -> None:
    command = f"cat <<EOF {redirect} /etc/motd\nhello\nEOF"
    assert is_read_only_command(command) is False


@pytest.mark.parametrize("redirect", [">", ">>"])
def test_heredoc_body_strip_keeps_the_opening_line_redirection(redirect: str) -> None:
    command = f"cat <<EOF {redirect} /etc/motd\nhello\nEOF"
    surface = command_surface(command)
    assert f"{redirect} /etc/motd" in surface
    assert "hello" not in surface


def test_heredoc_without_redirection_documenting_a_dangerous_command_stays_read_only() -> None:
    """The case :func:`command_surface` exists to protect: data, not an action."""
    command = "cat <<EOF\nrm -rf /\nEOF"
    assert is_read_only_command(command) is True
    facts = classify_tool("Bash", {"command": command})
    assert not facts.destructive_reason


# ── non-regression: the lot-B commands issue #643 says must not move ───────


def test_plain_read_commands_stay_read_only() -> None:
    assert is_read_only_command("cat notes.md") is True
    assert is_read_only_command('grep -rn "kubectl delete" docs/') is True


def test_commit_message_quoting_a_dangerous_shape_is_not_destructive() -> None:
    facts = classify_tool("Bash", {"command": 'git commit -m "fix rm -rf bug"'})
    assert not facts.destructive_reason


def test_pipeline_through_tee_is_not_read_only() -> None:
    assert is_read_only_command("cat f | tee out") is False
