"""``grimoire memory shared`` — mémoire transverse entre projets.

Enregistré sur ``memory_app`` à l'import, comme les autres extensions du
module mémoire : ``cmd_memory_lexical`` importe celui-ci pour son effet de
bord, ce qui évite de faire grossir ``cmd_memory`` (ratchet R2).

La logique vit dans :mod:`grimoire.memory.shared` ; ce module n'est que la
surface CLI.
"""

from __future__ import annotations

import json

import typer

from grimoire.cli.cmd_memory import (
    _get_fmt,
    _load_config_context,
    _load_manager_context,
    console,
    memory_app,
)
from grimoire.core.config import GrimoireConfig
from grimoire.memory import shared as sh
from grimoire.memory.manager import MemoryManager

__all__ = ["shared_app"]

shared_app = typer.Typer(help="Mémoire transverse : ce qui reste vrai d'un projet à l'autre.")
memory_app.add_typer(shared_app, name="shared")

_domain_opt = typer.Option(..., "--domain", "-d", help="Domaine du motif (ex. alembic, fastapi).")
_force_opt = typer.Option(False, "--force", help="Passer outre la garde — le contournement est enregistré.")
_limit_opt = typer.Option(5, "--limit", "-n", help="Résultats par portée.")


def _require_shared(cfg: GrimoireConfig) -> MemoryManager:
    """Store transverse, ou sortie nette avec la clé à déclarer."""
    manager = sh.open_shared(cfg)
    if manager is None:
        console.print(
            "[yellow]Mémoire transverse désactivée.[/yellow] Déclarez-la pour l'activer :\n"
            '  [cyan]memory:\n    shared_collection: "GrimoireShared"[/cyan]'
        )
        raise typer.Exit(1)
    return manager


@shared_app.command("promote")
def shared_promote(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Le motif à promouvoir."),
    domain: str = _domain_opt,
    force: bool = _force_opt,
) -> None:
    """Promouvoir un motif réutilisable vers la mémoire transverse.

    Refusé si le texte reste ancré dans son projet — un fait dont la vérité
    dépend d'un HEAD git ne devient pas vrai ailleurs.

    [dim]Examples:[/dim]
      [cyan]grimoire memory shared promote "les migrations Alembic cassent quand deux heads coexistent" -d alembic[/cyan]
    """
    cfg, _ = _load_config_context()
    manager = _require_shared(cfg)
    fmt = _get_fmt(ctx)

    try:
        entry = sh.promote(
            manager, text, domain=domain, project_name=cfg.project.name, force=force
        )
    except sh.SharedMemoryError as exc:
        if fmt == "json":
            verdict = sh.check_promotable(text, project_name=cfg.project.name, domain=domain)
            typer.echo(json.dumps({"promoted": False, **verdict.to_dict()}, indent=2))
        else:
            console.print(f"[red]{exc}[/red]")
            console.print(
                "\n[dim]Un motif reste vrai quand on efface le nom du projet. "
                "Si celui-ci en est un, relancez avec --force.[/dim]"
            )
        raise typer.Exit(1) from None

    if fmt == "json":
        typer.echo(json.dumps({"promoted": True, "entry": entry.to_dict()}, indent=2, default=str))
        return
    console.print(f"[green]Promu[/green] dans [bold]domain-{entry.metadata['domain']}[/bold]")
    console.print(f"  id      : {entry.id}")
    console.print(f"  appris  : {', '.join(entry.metadata['learned_in'])}")
    if entry.metadata.get("promotion_forced"):
        console.print("  [yellow]forcé — la garde avait relevé :[/yellow]")
        for warning in entry.metadata.get("promotion_warnings", []):
            console.print(f"    - {warning}")


@shared_app.command("confirm")
def shared_confirm(
    ctx: typer.Context,
    entry_id: str = typer.Argument(..., help="Identifiant du motif transverse."),
) -> None:
    """Confirmer qu'un motif tient aussi dans ce projet.

    C'est le seul mécanisme qui restaure la confiance : sans recontact, un
    motif finit servi comme hypothèse.
    """
    cfg, _ = _load_config_context()
    manager = _require_shared(cfg)

    entry = sh.confirm(manager, entry_id, project_name=cfg.project.name)
    if entry is None:
        console.print(f"[red]Motif introuvable :[/red] {entry_id}")
        raise typer.Exit(1)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(entry.to_dict(), indent=2, default=str))
        return
    console.print(f"[green]Confirmé[/green] — vérifié dans : {', '.join(entry.metadata['confirmed_in'])}")


@shared_app.command("recall")
def shared_recall(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Ce que l'on cherche."),
    limit: int = _limit_opt,
) -> None:
    """Chercher dans le projet puis dans le transverse, sans mélanger.

    Les deux portées restent séparées : un motif appris ailleurs est servi
    avec sa provenance et sa fraîcheur, jamais avec l'assurance d'un fait
    vérifié ici.
    """
    manager, cfg, _ = _load_manager_context()
    result = sh.layered_recall(manager, sh.open_shared(cfg), query, limit=limit)
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2, default=str))
        return

    console.print(f"[bold]Projet[/bold] — {cfg.project.name}")
    if not result.project:
        console.print("  [dim]aucun résultat local[/dim]")
    for item in result.project:
        console.print(f"  · {item.entry.text}")

    console.print("\n[bold]Transverse[/bold]")
    if not result.shared:
        console.print("  [dim]aucun motif transverse[/dim]")
    style = {
        sh.FRESHNESS_CURRENT: "green",
        sh.FRESHNESS_AGING: "yellow",
        sh.FRESHNESS_HYPOTHESIS: "red",
    }
    for item in result.shared:
        colour = style.get(item.freshness, "white")
        console.print(f"  · {item.entry.text}")
        origin = ", ".join(item.learned_in) or "origine inconnue"
        console.print(
            f"    [{colour}]{item.freshness}[/{colour}] · appris dans {origin}"
            + (f" · {item.caveat}" if item.caveat else "")
        )
