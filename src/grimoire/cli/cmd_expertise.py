"""CLI ``grimoire expertise`` — catalogue d'expertises optionnelles (issue #616).

Trois commandes sur un seul mécanisme (:mod:`grimoire.core.expertises`) :
``list`` affiche le catalogue (et, avec ``--detected``, seulement ce que la
détection recommande pour ce projet) ; ``add``/``remove`` attachent ou
détachent une expertise, en écrivant l'override projet qui survit à
``grimoire up`` — jamais dans ``_grimoire/kit/``. La projection sur les hôtes
reste la responsabilité de ``grimoire host sync``, déjà capable de lire ces
overrides ; cette commande ne fait que les écrire et les valider.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireAgentError, GrimoireRegistryError

if TYPE_CHECKING:
    from grimoire.core.expertises import Expertise

expertise_app = typer.Typer(help="Catalogue d'expertises optionnelles : langages, ingénierie, cloud.")
console = Console()

_PROJECT_ROOT_OPTION = typer.Option(Path.cwd(), "--project-root", help="Racine du projet.", show_default=False)
_DETECTED_OPTION = typer.Option(False, "--detected", help="N'afficher que les expertises recommandées par détection.")
_IDS_ARGUMENT = typer.Argument(..., help="Un ou plusieurs id d'expertise (voir `grimoire expertise list`).")


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text") or "text")


@expertise_app.command("list")
def expertise_list(
    ctx: typer.Context,
    project_root: Path = _PROJECT_ROOT_OPTION,
    detected: bool = _DETECTED_OPTION,
) -> None:
    """Lister le catalogue d'expertises, ou seulement celles détectées.

    [dim]Examples:[/dim]
      [cyan]grimoire expertise list[/cyan]
      [cyan]grimoire expertise list --detected --project-root .[/cyan]
    """
    from grimoire.core.expertises import detect_expertises, load_catalog

    root = project_root.resolve()
    try:
        catalog = load_catalog()
    except GrimoireRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if detected:
        recommendations = detect_expertises(root, catalog=catalog)
        if _get_fmt(ctx) == "json":
            typer.echo(json.dumps([asdict(r) for r in recommendations], indent=2, ensure_ascii=False))
            return
        if not recommendations:
            console.print("[dim]Aucune expertise détectée pour ce projet.[/dim]")
            return
        for rec in recommendations:
            console.print(f"[bold]{rec.id}[/bold] ({rec.family}) — {rec.name}")
            console.print(f"  [dim]{rec.reason}[/dim] · confiance {rec.confidence:.0%}")
        return

    if _get_fmt(ctx) == "json":
        typer.echo(
            json.dumps(
                [
                    {
                        "id": e.id,
                        "name": e.name,
                        "family": e.family,
                        "summary": e.summary,
                        "porteur": e.porteur,
                        "porteur_fallback": e.porteur_fallback,
                    }
                    for e in catalog
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    current_family = None
    for entry in catalog:
        if entry.family != current_family:
            current_family = entry.family
            console.print(f"\n[bold underline]{current_family}[/bold underline]")
        console.print(f"  [bold]{entry.id}[/bold] — {entry.name} ({entry.porteur})")
        console.print(f"    [dim]{entry.summary}[/dim]")


def _resolve_ids(catalog: tuple[Expertise, ...], ids: list[str]) -> tuple[list[Expertise], list[str]]:
    """``(entries, unknown_ids)`` — jamais d'échec partiel silencieux : un id
    inconnu est nommé, pas juste omis du résultat."""
    by_id = {e.id: e for e in catalog}
    entries: list[Expertise] = []
    unknown: list[str] = []
    for expertise_id in ids:
        if expertise_id in by_id:
            entries.append(by_id[expertise_id])
        else:
            unknown.append(expertise_id)
    return entries, unknown


@expertise_app.command("add")
def expertise_add(
    ctx: typer.Context,
    ids: list[str] = _IDS_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Attacher une ou plusieurs expertises au projet (override, `extends: kit`).

    N'écrit jamais sous ``_grimoire/kit/`` : l'attachement survit à
    ``grimoire up``. Lancez ``grimoire host sync`` ensuite pour le projeter
    sur les hôtes.

    [dim]Examples:[/dim]
      [cyan]grimoire expertise add rust[/cyan]
      [cyan]grimoire expertise add rust aws --project-root .[/cyan]
    """
    from grimoire.core.expertises import attach_expertise, load_catalog

    root = project_root.resolve()
    catalog = load_catalog()
    entries, unknown = _resolve_ids(catalog, ids)
    if unknown:
        console.print(f"[red]Id(s) inconnu(s) :[/red] {', '.join(unknown)} — voir `grimoire expertise list`.")
        raise typer.Exit(1)

    results = []
    for entry in entries:
        try:
            result = attach_expertise(root, entry)
        except GrimoireAgentError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        results.append(result)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
        return

    for result in results:
        verb = "déjà attaché" if result.already_attached else "attaché"
        console.print(f"[green]{verb}[/green] {result.expertise_id} → {result.porteur} ({result.agent_override_ref})")
    console.print("[dim]Lancez `grimoire host sync` pour projeter sur les hôtes.[/dim]")


@expertise_app.command("remove")
def expertise_remove(
    ctx: typer.Context,
    ids: list[str] = _IDS_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Détacher une ou plusieurs expertises du projet.

    Supprime aussi le fichier de skill de l'override — le garder sans agent
    qui le référence le rendrait transversal (chargé à chaque tour), pire
    que l'état avant attachement.

    [dim]Examples:[/dim]
      [cyan]grimoire expertise remove rust[/cyan]
    """
    from grimoire.core.expertises import detach_expertise, load_catalog

    root = project_root.resolve()
    catalog = load_catalog()
    entries, unknown = _resolve_ids(catalog, ids)
    if unknown:
        console.print(f"[red]Id(s) inconnu(s) :[/red] {', '.join(unknown)} — voir `grimoire expertise list`.")
        raise typer.Exit(1)

    results = []
    for entry in entries:
        try:
            result = detach_expertise(root, entry)
        except GrimoireAgentError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        results.append(result)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
        return

    for result in results:
        if result.was_attached:
            console.print(f"[green]détaché[/green] {result.expertise_id} de {result.porteur}")
        else:
            console.print(f"[dim]{result.expertise_id} n'était pas attaché à {result.porteur}[/dim]")
    console.print("[dim]Lancez `grimoire host sync` pour projeter sur les hôtes.[/dim]")
