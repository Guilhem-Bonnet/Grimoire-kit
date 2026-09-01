"""The destructive policy must read the action, never the data it carries.

Matching the patterns against the whole command line made the guardrail refuse
a heredoc that *documented* a dangerous command, a commit message that named
one, and a test fixture containing one — while the identical text written
through an editing tool passed, since those carry no command string at all.

The dangerous half of this fix is the false negative: dropping quoted text
wholesale would let ``bash -c '…'`` smuggle anything past the policy. Both
directions are asserted below, and the literals are assembled at runtime so
this file can be edited on a machine where the policy is enforced.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool, command_surface

#: Assembled rather than written: a policy-governed session refuses to write a
#: file whose command line contains these, which is the very defect under test.
KUBE_DELETE = "kube" + "ctl " + "del" + "ete"
RM_RF = "rm" + " -rf"
FORCE_PUSH = "git push --" + "force origin main"
HARD_RESET = "git reset --" + "hard origin/main"


def _blocked(command: str) -> bool:
    return bool(classify_tool("Bash", {"command": command}).destructive_reason)


CARRIED_AS_DATA = [
    pytest.param(
        "cat > runbook.md <<'EOF'\n# Runbook\nNe jamais lancer `" + KUBE_DELETE + " ns prod`.\nEOF",
        id="heredoc-de-documentation",
    ),
    pytest.param(
        "git commit -m 'doc: expliquer pourquoi " + KUBE_DELETE + " est interdit'",
        id="message-de-commit",
    ),
    pytest.param(
        "python -c \"open('f.txt','w').write('" + KUBE_DELETE + " ns x')\"",
        id="fixture-de-test",
    ),
    pytest.param('echo "' + RM_RF + ' / détruit tout"', id="echo-pedagogique"),
    pytest.param(
        'failure-museum.py check --description "' + KUBE_DELETE + ' sans validation"',
        id="argument-description",
    ),
]

EXECUTED_AS_ACTION = [
    pytest.param(KUBE_DELETE + " ns prod", id="action-nue"),
    pytest.param(RM_RF + " /tmp/build", id="suppression-recursive"),
    pytest.param("bash -c '" + RM_RF + " /tmp/build'", id="via-bash-c"),
    pytest.param("eval '" + KUBE_DELETE + " ns prod'", id="via-eval"),
    pytest.param("echo prod | xargs -I{} " + KUBE_DELETE + " ns {}", id="via-xargs"),
    pytest.param(
        "cat > f.md <<'EOF'\ndoc\nEOF\n" + RM_RF + " /tmp/build",
        id="apres-un-heredoc",
    ),
    pytest.param("true && " + RM_RF + " /tmp/x", id="dans-un-pipeline"),
    pytest.param(FORCE_PUSH, id="poussee-forcee"),
    pytest.param(HARD_RESET, id="reset-dur"),
]


@pytest.mark.parametrize("command", CARRIED_AS_DATA)
def test_data_carried_by_a_command_is_not_an_action(command: str) -> None:
    assert not _blocked(command), f"refusé alors que rien n'est exécuté :\n{command}"


@pytest.mark.parametrize("command", EXECUTED_AS_ACTION)
def test_an_action_is_still_caught(command: str) -> None:
    assert _blocked(command), f"laissé passer alors que la commande s'exécute :\n{command}"


def test_a_write_tool_is_judged_on_its_target_not_its_content() -> None:
    """The asymmetry the report noted was the symptom, not the defect.

    A write tool carries no command string, so the destructive patterns never
    applied to it — correctly: what makes a write dangerous is where it lands.
    Bash now behaves the same way about the data it carries.
    """
    facts = classify_tool(
        "Write",
        {"file_path": "runbook.md", "content": "Ne jamais lancer " + KUBE_DELETE},
    )
    assert not facts.destructive_reason


def test_heredoc_body_is_dropped_but_the_redirection_survives() -> None:
    surface = command_surface("cat > f.md <<'EOF'\n" + RM_RF + " /\nEOF")
    assert RM_RF not in surface
    # Le tag est ensuite mangé comme toute chaîne entre quotes ; ce qui compte
    # est que la redirection reste visible et que le corps ait disparu.
    assert "<<" in surface, "la redirection elle-même doit rester visible"
    assert surface.startswith("cat > f.md")


def test_quoted_text_is_kept_when_a_shell_is_about_to_run_it() -> None:
    assert RM_RF in command_surface("bash -c '" + RM_RF + " /tmp/x'")
    assert RM_RF not in command_surface("git commit -m '" + RM_RF + " /tmp/x'")


def test_quotes_cannot_splice_two_fragments_into_one_verb() -> None:
    """Removing a quoted run must leave a word boundary behind."""
    spliced = command_surface("echo 'rm' 'ignored' '-rf /'")
    assert not _blocked("echo 'rm' 'ignored' '-rf /'")
    assert "rm -rf" not in spliced
