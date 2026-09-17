"""Condensed root ``--help`` — five commands, the rest one flag away.

Extracted out of ``grimoire.cli.app`` (issue Grimoire-kit#552, phase 2 of the
2026-Q4 plan, métrique 2) so that grandfathered, size-ratcheted module stays
under its baseline (``scripts/code-ratchet-baseline.json`` — R2, `app.py` may
not grow past 2798 lines) rather than for any behavioural reason.

``grimoire --help`` used to list 49+ top-level names by walking every lazy
group/command to read its short help — which imports all of them (see
``_lazy.py``). Five commands cover the loop a first session actually needs;
everything else is one ``--all`` away, never renamed or removed.
"""

from __future__ import annotations

from typing import Any

from grimoire.cli._lazy import LazyTyperGroup

#: Order here is the order rendered in the condensed panel.
_FIRST_HOUR_COMMANDS: tuple[tuple[str, str], ...] = (
    ("init", "Créer ou enrôler un projet — détection de stack, agents déployés."),
    ("up", "Tout enchaîner en une commande — init, identité, standard, doctor."),
    ("doctor", "Diagnostiquer la santé du projet — config, structure, agents."),
    ("flow", "Lancer et suivre un flow gouverné — le reçu de preuve d'une tâche."),
    ("cockpit", "Ouvrir le tableau de bord — projets, activité, mises à jour."),
)


def _render_condensed_help(group: LazyTyperGroup, ctx: Any) -> None:
    """Print the first-hour ``--help`` — five commands, the rest one flag away.

    Deliberately avoids ``super().format_help()``: the full panel view walks
    every lazy group/command to read its short help, which imports all of
    them — the exact cost ``--help`` should not pay by default. This path
    never imports a single ``cmd_*`` module.
    """
    from rich.console import Console
    from rich.padding import Padding
    from rich.table import Table

    out = Console()
    out.print(Padding(group.get_usage(ctx), (1, 1, 0, 1)), style="bold")
    if group.help:
        out.print(Padding(group.help, (0, 1, 1, 1)))

    table = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for name, blurb in _FIRST_HOUR_COMMANDS:
        table.add_row(name, blurb)
    out.print(Padding("[bold]Commandes pour commencer[/bold]", (0, 1)))
    out.print(Padding(table, (0, 1, 1, 3)))

    total = len(group.list_commands(ctx))
    hidden = total - len(_FIRST_HOUR_COMMANDS)
    out.print(
        Padding(
            f"[dim]Autres commandes ({hidden})[/dim] — mémoire, standard, dispatch, "
            "hôtes, propositions, blueprints, tâches, outils…\n"
            "  [bold]grimoire --help --all[/bold]     Tout afficher "
            f"({total} commandes)\n"
            "  [bold]grimoire COMMAND --help[/bold]   Aide d'une commande précise",
            (0, 1, 1, 1),
        )
    )


class _RootHelpGroup(LazyTyperGroup):
    """Root command group — condensed ``--help`` unless ``--all`` was passed.

    Only the root ``app`` uses this class; every lazy sub-app (``flow``,
    ``memory``...) keeps Typer's normal ``TyperGroup``, so ``grimoire flow
    --help`` is untouched.

    Whether ``--all`` was requested is decided from the raw argument list in
    :meth:`parse_args`, not from ``ctx.params`` in :meth:`format_help`: click
    resolves eager options in the order they were *typed*, and ``--help``'s
    own eager callback (which triggers ``format_help``) can run before
    ``--all``'s if the user wrote ``--help --all`` rather than ``--all
    --help``. Reading the argument list directly is order-independent, and
    the flag is stashed on ``ctx`` (fresh per invocation) rather than on
    ``self`` (the module-level ``app`` singleton, reused across every
    ``CliRunner`` call in the test suite).
    """

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        ctx._condensed_help_show_all = "--all" in args
        return super().parse_args(ctx, args)

    def format_help(self, ctx: Any, formatter: Any) -> None:
        if getattr(ctx, "_condensed_help_show_all", False):
            super().format_help(ctx, formatter)
            return
        _render_condensed_help(self, ctx)
