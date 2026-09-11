"""CLI ``grimoire proposals`` — le déclencheur de propositions d'artefact (issue #395).

Même moteur que le cockpit (:mod:`grimoire.proposals`) : cette commande est
la voie pour les hôtes sans interface — ``list`` lit les propositions
(rafraîchies depuis le journal des non-choix avant d'être rendues),
``accept`` écrit l'artefact réel via le chemin fixé par #367, ``reject``
marque la proposition refusée. Aucune option ``--auto`` : accepter reste
toujours un geste explicite.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.proposals import accept_proposal, list_proposals, reject_proposal
from grimoire.tools._common import find_project_root

proposals_app = typer.Typer(
    help="Propositions d'artefact issues des non-choix répétés (issue #395).",
    no_args_is_help=True,
)
console = Console()


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text") or "text")


def _root() -> Path:
    try:
        return find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a Grimoire project — cannot locate kit root.[/red]")
        raise typer.Exit(1) from None


@proposals_app.command("list")
def proposals_list(ctx: typer.Context) -> None:
    """Lister les propositions — rafraîchies depuis le journal des non-choix.

    [dim]Examples:[/dim]
      [cyan]grimoire proposals list[/cyan]
    """
    root = _root()
    proposals = list_proposals(root)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"proposals": [p.to_dict() for p in proposals]}, indent=2, ensure_ascii=False))
        return

    if not proposals:
        console.print("[dim]Aucune proposition (aucune spécialité manquante n'a atteint le seuil).[/dim]")
        return

    table = Table(title="Propositions d'artefact")
    for column in ("Slug", "Spécialité", "Type", "Statut", "Occurrences", "Dernier non-choix"):
        table.add_column(column)
    for proposal in proposals:
        table.add_row(
            proposal.slug,
            proposal.specialty,
            proposal.artifact_type,
            proposal.status,
            str(proposal.count),
            proposal.last_seen or "—",
        )
    console.print(table)


@proposals_app.command("accept")
def proposals_accept(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug de la proposition (voir `grimoire proposals list`)."),
) -> None:
    """Accepter une proposition : écrit l'artefact réel, jamais sans ce geste explicite."""
    root = _root()
    result = accept_proposal(root, slug)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise typer.Exit(1)
        return

    if not result.get("ok"):
        console.print(f"[red]{result.get('error', 'échec inconnu')}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Créé:[/green] {result.get('path', slug)}")


@proposals_app.command("reject")
def proposals_reject(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug de la proposition (voir `grimoire proposals list`)."),
) -> None:
    """Refuser une proposition — reproposée seulement si son compte double depuis ce refus."""
    root = _root()
    result = reject_proposal(root, slug)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise typer.Exit(1)
        return

    if not result.get("ok"):
        console.print(f"[red]{result.get('error', 'échec inconnu')}[/red]")
        raise typer.Exit(1)
    console.print(f"[yellow]Refusée:[/yellow] {slug}")
