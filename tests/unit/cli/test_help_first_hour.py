"""`grimoire --help` shows five commands by default (issue Grimoire-kit#552).

Métrique 2 du plan 2026-Q4 : 49+ commandes de premier niveau pour une cible
``≤ 5``. Aucune commande n'est renommée ni retirée — ``grimoire --help --all``
donne toujours la liste complète, panneaux compris, inchangée.

Le test qui compte les commandes visibles par défaut est le garde-fou contre
la dérive : si un sixième nom rejoint ``_FIRST_HOUR_COMMANDS``, il échoue.
"""

from __future__ import annotations

from typer.testing import CliRunner

from grimoire.cli.app import _FIRST_HOUR_COMMANDS, app

runner = CliRunner()

# A sample of names that only ever appear behind `--all` — picking names from
# distinct panels (Data, Agents, Project) makes the "still hidden" assertion
# robust to any single panel being reworked later.
_SAMPLE_HIDDEN_COMMANDS = ("stigmergy", "providers", "policies", "features")


def test_first_hour_commands_are_exactly_five() -> None:
    """Drift guard: the condensed panel never grows past five entries."""
    assert len(_FIRST_HOUR_COMMANDS) <= 5
    names = [name for name, _ in _FIRST_HOUR_COMMANDS]
    assert names == ["init", "up", "doctor", "flow", "cockpit"]


def test_default_help_lists_the_five_first_hour_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name, _ in _FIRST_HOUR_COMMANDS:
        assert name in result.output, f"{name!r} missing from condensed --help"
    assert "Commandes pour commencer" in result.output
    assert "--all" in result.output
    for hidden in _SAMPLE_HIDDEN_COMMANDS:
        assert hidden not in result.output, f"{hidden!r} leaked into the condensed view"


def test_bare_invocation_also_shows_the_condensed_help() -> None:
    """`grimoire` alone (no_args_is_help) renders the same condensed panel.

    Pre-existing, unrelated to this change: a bare invocation exits 2 (no
    command given, after printing help) — unchanged before/after, so only
    the rendered content is asserted here, not the exit code.
    """
    result = runner.invoke(app, [])
    assert "Commandes pour commencer" in result.output
    for hidden in _SAMPLE_HIDDEN_COMMANDS:
        assert hidden not in result.output


def test_help_all_lists_every_command_regardless_of_flag_order() -> None:
    for args in (["--help", "--all"], ["--all", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, args
        for hidden in _SAMPLE_HIDDEN_COMMANDS:
            assert hidden in result.output, f"{hidden!r} missing from --all ({args})"
        # The original panel-grouped rendering is untouched.
        assert "Options" in result.output
        assert "Examples:" in result.output


def test_subcommand_help_is_not_condensed() -> None:
    """Only the root group is affected — `grimoire flow --help` stays full."""
    result = runner.invoke(app, ["flow", "--help"])
    assert result.exit_code == 0
    assert "Commandes pour commencer" not in result.output
