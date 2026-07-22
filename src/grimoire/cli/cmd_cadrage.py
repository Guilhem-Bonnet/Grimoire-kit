"""``grimoire cadrage`` — comprendre avant de construire (B4)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.cadrage import PHASES, check, scaffold, status

console = Console()
cadrage_app = typer.Typer(help="Cadrage produit : brief → brainstorm → compréhension → exigences → cahier des charges.")

_path_arg = typer.Argument(Path(), help="Racine du projet.")
_force_opt = typer.Option(False, "--force", "-f", help="Réécrire les gabarits existants.")
_name_opt = typer.Option("", "--name", help="Nom du projet (défaut : nom du dossier).")


@cadrage_app.command("init")
def cadrage_init(
    path: Path = _path_arg, name: str = _name_opt, force: bool = _force_opt
) -> None:
    """Pose les cinq phases du cadrage sous _grimoire/cadrage/."""
    root = path.resolve()
    written = scaffold(root, project_name=name or root.name, force=force)
    if written:
        console.print(f"[green]cadrage initialisé[/green] — {len(written)} gabarit(s) :")
        for p in written:
            console.print(f"  {p.relative_to(root)}")
    else:
        console.print("[yellow]déjà initialisé[/yellow] — rien à écrire (--force pour réécrire).")
    console.print(
        "\n[dim]Le chemin : brief → brainstorm → compréhension → exigences → "
        "cahier des charges. Remplissez avec vos agents, puis :[/dim] "
        "[cyan]grimoire cadrage check[/cyan]"
    )


@cadrage_app.command("status")
def cadrage_status(ctx: typer.Context, path: Path = _path_arg) -> None:
    """Progression du cadrage, phase par phase."""
    root = path.resolve()
    report = status(root)
    if not report["initialized"]:
        console.print(
            "[yellow]cadrage non initialisé[/yellow] — lancez "
            "[cyan]grimoire cadrage init[/cyan]"
        )
        raise typer.Exit(1)
    table = Table(title=f"Cadrage — {report['progress']} phases remplies")
    table.add_column("Phase")
    table.add_column("État")
    table.add_column("Sections à compléter")
    for r in report["phases"]:
        missing = [s for s, st in r["sections"].items() if st != "rempli"]
        style = {"rempli": "green", "partiel": "yellow"}.get(r["state"], "red")
        table.add_row(
            r["phase"] + (" [dim](gate)[/dim]" if r["gate"] else ""),
            f"[{style}]{r['state']}[/{style}]",
            ", ".join(missing) if missing else "—",
        )
    console.print(table)


@cadrage_app.command("check")
def cadrage_check(path: Path = _path_arg) -> None:
    """Gate de complétude : exigences + cahier des charges doivent être remplis."""
    root = path.resolve()
    errors, warnings = check(root)
    for w in warnings:
        console.print(f"[yellow]avertissement[/yellow] : {w}")
    for e in errors:
        console.print(f"[red]erreur[/red] : {e}")
    if errors:
        console.print(
            f"\n[red]cadrage incomplet[/red] — {len(errors)} phase(s) gate "
            "à compléter avant de construire."
        )
        raise typer.Exit(1)
    console.print(
        f"[green]cadrage complet[/green] — les {len(PHASES)} phases tiennent "
        "la route. Construisez."
        if not warnings
        else "[green]gate du cadrage passé[/green] — exigences et cahier des "
        "charges complets (phases amont partielles : voir avertissements)."
    )
