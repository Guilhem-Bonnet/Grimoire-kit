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


# ── issue #875 : les lectures courantes ne sont pas des écritures ──────────


@pytest.mark.parametrize(
    "command",
    [
        "cd x && grep -rn y .",
        "sed -n 1,20p f",
        "awk '{print $1}' f | sort | uniq -c",
        "git grep -n x origin/main -- src",
        "command -v gh",
        "git -C repo log --oneline",
        "git remote get-url origin",
        "git branch --show-current",
        "cat f | cut -d: -f1 | tr a-z A-Z | jq .",
        "for f in a b; do cat $f; done",
        "gh pr view 12",
    ],
)
def test_lectures_courantes_sont_read_only(command: str) -> None:
    assert is_read_only_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/a/b/ f",
        "sed --in-place s/a/b/ f",
        "sed -n 's/a/b/w out' f",
        "awk -i inplace '{print}' f",
        "awk '{print > \"out\"}' f",
        "cd x && rm f",
        "sort -o out f",
        "sort f --output=out",
        "uniq in out",
        "cd x && grep y . > out",
        "for f in a b; do rm $f; done",
        "git branch -d topic",
        "git remote add o url",
        "ls & rm f",
        "sed p f & rm f",
        "cat f |& rm f",
    ],
)
def test_formes_qui_ecrivent_restent_des_mutations(command: str) -> None:
    assert is_read_only_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "sed -n '1w out' f",
        "sed -n '$w out' f",
        "sed -n '1,2w out' f",
        "sed -n 'w/dev/stdout' f",
        "sed -e 1w -e out f",
        "sed --expression=w\\ out f",
        "sed 'wout' f",
        "sed '1e rm x' f",
        "sed 'e rm x'",
        "sed --in-pl s/a/b/ f",
        "sed -f script f",
        "awk 'BEGIN{ \"rm x\" | getline }'",
        "awk '{ \"rm f\" | getline }'",
        "awk -f p.awk",
        "gawk -f /dev/stdin",
        "awk '$1 > 3' f",
        "sort --compress-program=sh g",
        "git remote -v add x y",
        "git remote -v rm origin",
        "echo $(rm f)",
        "echo `rm f`",
        "cat $(rm f)",
        'echo "$(rm y)"',
        'for f in "$(rm y)"; do cat; done',
        'cd "$(rm y)"',
    ],
)
def test_sed_awk_sort_remote_substitutions_sont_des_mutations(command: str) -> None:
    """Liste blanche stricte : chacune de ces formes écrit ou exécute malgré son verbe de lecture."""
    assert is_read_only_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "sed -n 1,20p f",
        "sed -n '/foo/,/bar/p' f",
        "sed -E 's/a+/b/g' f",
        "sed -e 's/a/b/;s/c/d/gI' f",
        "sed -n '$p' f",
        "sed '/x/!d' f",
        "git remote",
        "git remote -v",
        "git remote --verbose",
        "git remote get-url --push origin",
        "echo '$(not run)'",
        "grep -n 'a`b' f",
    ],
)
def test_formes_de_la_liste_blanche_restent_des_lectures(command: str) -> None:
    assert is_read_only_command(command) is True


# ── Zone protégée ~/.grimoire/control/ (#886 d, #876 lot 1) ────────────────

_ZONE = "/home/u/.grimoire/control"
_JOURNAL = f"{_ZONE}/mandates.authority.jsonl"
_PROOF = f"{_ZONE}/host-contracts.json"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": "{}\n"}),
        ("Edit", {"file_path": _PROOF, "old_string": "false", "new_string": "true"}),
        ("NotebookEdit", {"notebook_path": f"{_ZONE}/x.ipynb", "new_source": "x"}),
        ("Write", {"file_path": "~/.grimoire/control/host-contracts.json", "content": "{}"}),
        ("Bash", {"command": f"echo '{{}}' >> {_PROOF}"}),
        ("Bash", {"command": "echo '{}' > $HOME/.grimoire/control/mandates.authority.jsonl"}),
        ("Bash", {"command": f"cp /tmp/forged.json {_PROOF}"}),
        ("Bash", {"command": f"mv /tmp/forged {_JOURNAL}"}),
        ("Bash", {"command": f"tee {_PROOF} < /tmp/forged"}),
        ("Bash", {"command": f"sed -i 's/false/true/' {_PROOF}"}),
        ("Bash", {"command": f"python -c \"open('{_PROOF}', 'w').write('{{}}')\""}),
        ("Bash", {"command": "cd ~/.grimoire && echo '{}' > control/host-contracts.json"}),
        ("Bash", {"command": "rm -rf ~/.grimoire/control"}),
    ],
)
def test_an_agent_write_in_the_control_zone_is_a_forgery_vector(tool: str, tool_input: dict[str, object]) -> None:
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    vector = forgery_vector(classify_tool(tool, tool_input))
    assert vector is not None and vector.governed_only is False
    assert vector.code in ("control-zone", "journal")


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": "{}\n"}),
        ("Edit", {"file_path": _PROOF, "old_string": "a", "new_string": "b"}),
        ("Bash", {"command": f"echo ok >> {_PROOF}"}),
    ],
)
def test_the_brake_denies_a_control_zone_write_in_any_profile(tmp_path, tool: str, tool_input: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import HookInput, Outcome, run_decision
    from grimoire.hosts.events import HookEvent

    decision = run_decision(
        "grimoire.tool-policy",
        HookInput(event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, tool_name=tool, tool_input=tool_input, session_id="S1"),
    )
    assert decision.outcome is Outcome.DENY
    assert decision.detail["authority_vector"] in ("control-zone", "journal")
    assert "Limite" in decision.reason  # la limite résiduelle est dite à chaque refus


def test_a_control_zone_refusal_revokes_the_mandates(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import HookInput, run_decision
    from grimoire.hosts.events import HookEvent
    from grimoire.missions.mandate import MandateRequest, MandateStore
    from grimoire.missions.schemas import ORIGIN_HUMAN

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    store = MandateStore(project)
    store.grant(MandateRequest.build(("accept",), ("implementation",), 1, "+1h"), origin=ORIGIN_HUMAN, via="test")
    assert store.state().active
    run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=project, tool_name="Write",
            tool_input={"file_path": _JOURNAL, "content": "{}"}, session_id="S1",
        ),
    )
    assert not store.state().active


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [("Read", {"file_path": f"{_ZONE}/cockpit-key"}), ("Bash", {"command": f"cat {_ZONE}/cockpit-key"})],
)
def test_the_cockpit_key_cannot_be_read_by_an_agent(tool: str, tool_input: dict[str, object]) -> None:
    assert classify_tool(tool, tool_input).secret_target.endswith("cockpit-key")


def test_reading_the_control_zone_proof_stays_allowed() -> None:
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    assert forgery_vector(classify_tool("Bash", {"command": f"cat {_PROOF}"})) is None
    assert forgery_vector(classify_tool("Read", {"file_path": _PROOF})) is None
