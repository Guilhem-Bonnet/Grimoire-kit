"""Affichage texte de ``grimoire standard score`` : ce que valent le score et le seuil, et où lire le détail."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from grimoire.core.agentic_standard import StandardScoreResult

__all__ = ["print_score"]

#: Combien de dimensions faibles nommer avant de renvoyer au détail.
_LOWEST_SHOWN = 3


def print_score(console: Console, result: StandardScoreResult) -> None:
    """Le score sur 100 avec son seuil en clair, les dimensions les plus basses, puis le tableau complet."""
    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    console.print(
        f"{status} compliance score {result.score}/100, threshold {result.threshold} "
        f"for profile '{result.profile}' (below it the check fails) — written to {result.output_path}"
    )
    lowest = sorted(
        ((name, values["percentage"]) for name, values in result.dimensions.items() if values["percentage"] < 100),
        key=lambda item: item[1],
    )[:_LOWEST_SHOWN]
    if lowest:
        names = ", ".join(f"{name} {pct}%" for name, pct in lowest)
        console.print(f"lowest dimensions: {names} — details: `grimoire standard audit`")
    console.print(f"rules: {result.rules.get('rules_enforced')} enforced, {result.rules.get('rules_advisory')} advisory")
    table = Table(title="Compliance dimensions")
    table.add_column("Dimension")
    table.add_column("Score")
    table.add_column("Weight")
    table.add_column("Issues")
    for dimension, values in result.dimensions.items():
        table.add_row(
            dimension,
            f"{values['percentage']}%",
            str(values["weight"]),
            f"{values['errors']} error(s), {values['warnings']} warning(s)",
        )
    console.print(table)
