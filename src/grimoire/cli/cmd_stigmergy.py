"""CLI ``grimoire stigmergy`` — coordination stigmergique (phéromones).

Feature **expérimentale** (R&D, hors contrat SemVer — voir docs/rnd.md).

Coordination indirecte : les agents déposent des signaux typés qui
s'évaporent (décroissance par demi-vie, calculée à la lecture), d'autres
agents les captent et adaptent leur comportement. Le tableau est un fichier
local (``_grimoire-output/pheromone-board.json``) ; rien ne s'exécute.

Cette commande enveloppe le module ``grimoire.tools.stigmergy``. Le script
autonome ``framework/tools/stigmergy.py`` (embarqué dans les projets, sans le
paquet) partage le même format de board.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from grimoire.tools import stigmergy as stig

console = Console()
stigmergy_app = typer.Typer(
    help="Coordination stigmergique par phéromones (expérimental).",
    no_args_is_help=True,
)

_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet (défaut : dossier courant).")
_TYPE_OPTION = typer.Option("--type", "-t", help="Type de signal.")
_LOCATION_OPTION = typer.Option("--location", "-l", help="Zone concernée (ex. src/auth).")
_TEXT_OPTION = typer.Option("--text", help="Description du signal.")
_AGENT_OPTION = typer.Option("--agent", help="Agent émetteur.")
_TAGS_OPTION = typer.Option("--tags", help="Étiquettes séparées par des virgules.")
_INTENSITY_OPTION = typer.Option("--intensity", help="Intensité initiale (0..1).")
_ID_OPTION = typer.Option("--id", help="Identifiant de phéromone (PH-xxxxxxxx).")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON brute.")
_DRYRUN_OPTION = typer.Option("--dry-run", help="Aperçu sans modifier le board.")


def _root(project_root: Path | None) -> Path:
    return (project_root or Path.cwd()).resolve()


def _bar(intensity: float, width: int = 10) -> str:
    filled = max(0, min(width, round(intensity * width)))
    return "#" * filled + "-" * (width - filled)


@stigmergy_app.command("emit")
def emit(
    signal_type: Annotated[str, _TYPE_OPTION] = "NEED",
    location: Annotated[str, _LOCATION_OPTION] = "",
    text: Annotated[str, _TEXT_OPTION] = "",
    agent: Annotated[str, _AGENT_OPTION] = "",
    tags: Annotated[str, _TAGS_OPTION] = "",
    intensity: Annotated[float, _INTENSITY_OPTION] = stig.DEFAULT_INTENSITY,
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Déposer une phéromone typée sur le tableau."""
    ptype = signal_type.upper()
    if ptype not in stig.VALID_TYPES:
        console.print(f"[red]✗[/red] Type inconnu : {signal_type}. Attendus : {', '.join(sorted(stig.VALID_TYPES))}")
        raise typer.Exit(2)
    root = _root(project_root)
    board = stig.load_board(root)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    p = stig.emit_pheromone(board, ptype=ptype, location=location, text=text,
                            emitter=agent, tags=tag_list, intensity=intensity)
    stig.save_board(root, board)
    console.print(f"[green]●[/green] Phéromone émise : [b]{p.pheromone_id}[/b]")
    console.print(f"  {p.pheromone_type} @ [cyan]{p.location or '—'}[/cyan] · intensité {int(p.intensity * 100)}%")
    if p.text:
        console.print(f"  {p.text}")


@stigmergy_app.command("sense")
def sense(
    signal_type: Annotated[str, _TYPE_OPTION] = "",
    location: Annotated[str, _LOCATION_OPTION] = "",
    as_json: Annotated[bool, _JSON_OPTION] = False,
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Détecter les phéromones actives (au-dessus du seuil de détection)."""
    root = _root(project_root)
    board = stig.load_board(root)
    active = stig.sense_pheromones(board, ptype=(signal_type.upper() or None),
                                   location=(location or None))
    if as_json:
        import json
        console.print_json(json.dumps([
            {**p.to_dict(), "current_intensity": round(inten, 4)} for p, inten in active
        ]))
        return
    if not active:
        console.print("[dim]Aucun signal actif.[/dim]")
        return
    console.print(f"[b]{len(active)}[/b] signal(aux) actif(s)\n")
    for p, inten in active:
        console.print(f"[yellow]{p.pheromone_type}[/yellow] [dim]{p.pheromone_id}[/dim] "
                      f"@ [cyan]{p.location or '—'}[/cyan]  {_bar(inten)} {int(inten * 100)}%")
        if p.text:
            console.print(f"   {p.text} [dim]· {p.emitter or '?'}[/dim]")


@stigmergy_app.command("amplify")
def amplify(
    pheromone_id: Annotated[str, _ID_OPTION] = "",
    agent: Annotated[str, _AGENT_OPTION] = "",
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Renforcer un signal existant (comme une piste de fourmis)."""
    root = _root(project_root)
    board = stig.load_board(root)
    p = stig.amplify_pheromone(board, pheromone_id, agent)
    if p is None:
        console.print(f"[red]✗[/red] Phéromone introuvable : {pheromone_id}")
        raise typer.Exit(1)
    stig.save_board(root, board)
    console.print(f"[green]▲[/green] {p.pheromone_id} renforcée ({p.reinforcements} renfort·s)")


@stigmergy_app.command("resolve")
def resolve(
    pheromone_id: Annotated[str, _ID_OPTION] = "",
    agent: Annotated[str, _AGENT_OPTION] = "",
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Marquer un signal comme résolu."""
    root = _root(project_root)
    board = stig.load_board(root)
    p = stig.resolve_pheromone(board, pheromone_id, agent)
    if p is None:
        console.print(f"[red]✗[/red] Phéromone introuvable : {pheromone_id}")
        raise typer.Exit(1)
    stig.save_board(root, board)
    console.print(f"[green]✓[/green] {p.pheromone_id} résolue par {agent or '?'}")


@stigmergy_app.command("trails")
def trails(
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Détecter les patterns de coordination émergents."""
    root = _root(project_root)
    board = stig.load_board(root)
    patterns = stig.analyze_trails(board)
    if not patterns:
        console.print("[dim]Aucun pattern émergent.[/dim]")
        return
    for pat in patterns:
        console.print(f"[magenta]{pat.pattern_type}[/magenta] @ [cyan]{pat.location or '—'}[/cyan]")
        console.print(f"   {pat.description}")


@stigmergy_app.command("evaporate")
def evaporate(
    dry_run: Annotated[bool, _DRYRUN_OPTION] = False,
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Purger les signaux morts (sous le seuil de détection)."""
    root = _root(project_root)
    board = stig.load_board(root)
    before = len(board.pheromones)
    _, removed = stig.evaporate(board)
    if dry_run:
        console.print(f"[dim]Aperçu :[/dim] {removed} signal(aux) à purger, {before - removed} restant(s).")
        return
    stig.save_board(root, board)
    console.print(f"[green]✓[/green] {removed} purgé(s), {len(board.pheromones)} restant(s).")


@stigmergy_app.command("stats")
def stats(
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Statistiques rapides du tableau."""
    root = _root(project_root)
    board = stig.load_board(root)
    active = stig.sense_pheromones(board)
    by_type: dict[str, int] = {}
    for p, _ in active:
        by_type[p.pheromone_type] = by_type.get(p.pheromone_type, 0) + 1
    console.print(f"Actifs : [b]{len(active)}[/b] · émis : {board.total_emitted} · "
                  f"évaporés : {board.total_evaporated} · demi-vie : {board.half_life_hours:g} h")
    if by_type:
        console.print("  " + " · ".join(f"{k} {v}" for k, v in sorted(by_type.items())))
