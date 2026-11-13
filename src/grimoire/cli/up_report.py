"""Rapport texte de ``grimoire up`` : étapes, projet, parité de l'hôte détecté.

Séparé de :mod:`grimoire.cli.cmd_up`, sous cliquet de taille : le rapport n'a
pas à faire grossir la commande.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:
    from grimoire.cli.cmd_up import _UpState
    from grimoire.cli.env_checks import EnvCheck


_STATUS_STYLE = {
    "done": "[green]done[/green]",
    "skipped": "[yellow]skipped[/yellow]",
    "failed": "[red]failed[/red]",
    "planned": "[cyan]planned[/cyan]",
}


def render_report(
    state: _UpState,
    checks: list[EnvCheck],
    *,
    project_name: str,
    dry_run: bool,
    console: Console,
    host_parity: str = "",
) -> None:
    """Text report: step table, project summary, next actions only on failure."""
    table = Table(title="grimoire up — summary", show_header=True, padding=(0, 2))
    table.add_column("Step", style="bold")
    table.add_column("Status")
    table.add_column("Detail")
    for step in state.steps:
        table.add_row(step.step, _STATUS_STYLE.get(step.status, step.status), step.detail)
    console.print(table)
    console.print(f"\n[bold]Project:[/bold] {project_name}")
    if host_parity:
        console.print(f"[bold]Governance (detected host):[/bold] {escape(host_parity)}")

    if dry_run:
        console.print("[dim]dry-run: nothing was written.[/dim]")

    failed_steps = [s for s in state.steps if s.status == "failed"]
    if failed_steps:
        console.print("\n[bold]Next actions:[/bold]")
        for step in failed_steps:
            console.print(f"  [red]FAIL[/red] {step.step}: {step.detail}")
        for chk in checks:
            if not chk.passed and chk.remedy:
                console.print(f"       remedy: [cyan]{chk.remedy}[/cyan]")
        console.print("  Run [cyan]grimoire doctor .[/cyan] for the full diagnosis.")
