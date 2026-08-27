"""``grimoire task`` — surface des tâches agentiques.

Premier maillon : l'export du task board depuis le Mission Ledger (ADR-005). Le
ledger est la source ; ``_grimoire/standard/task-board.yaml`` est régénéré depuis
lui, jamais l'inverse.

Les commandes d'écriture sur le ledger arrivent au lot suivant (issue #137) ;
cette porte d'entrée existe pour qu'elles aient déjà leur place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

task_app = typer.Typer(
    help="Tâches agentiques : projection du Mission Ledger vers le board gouverné.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
board_app = typer.Typer(help="Le board gouverné, projeté depuis le ledger.", no_args_is_help=True)
task_app.add_typer(board_app, name="board")

# Le rendu humain part sur stderr : le JSON de stdout reste pipeable.
console = Console(stderr=True)

_BOARD_RELPATH = Path("_grimoire/standard/task-board.yaml")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


@board_app.command("export")
def board_export(
    ctx: typer.Context,
    project_root: Annotated[Path, typer.Argument(help="Racine du projet cible.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = _DEFAULT_LEDGER,
    mission: Annotated[str | None, typer.Option("--mission", help="N'exporter qu'une mission.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Chemin de sortie (défaut : le board du standard).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Afficher la projection sans écrire.")] = False,
) -> None:
    """Régénère le task board depuis le Mission Ledger.

    Le fichier produit est un artefact de sortie : l'éditer à la main ne change
    rien au ledger, et le prochain export l'écrasera.
    """
    from grimoire.missions.board import build_board
    from grimoire.missions.ledger import MissionLedger

    root = project_root.resolve()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    if not (ledger_path / "events.jsonl").is_file():
        console.print(f"[red]✗[/red] Aucun Mission Ledger sous {ledger_path}.")
        console.print("[dim]Le board reste ce qu'il est — rien n'a été écrasé.[/dim]")
        raise typer.Exit(1)

    board = build_board(MissionLedger(ledger_path), project=root.name, mission_id=mission)
    dest = output if output is not None else root / _BOARD_RELPATH

    if dry_run:
        typer.echo(json.dumps(board, indent=2, ensure_ascii=False))
        return

    _write_yaml(dest, board)
    counts = _counts(board)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"path": str(dest), "tasks": len(board["tasks"]), "by_status": counts}, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] {len(board['tasks'])} tâche(s) projetée(s) → {dest}")
    if counts:
        console.print("[dim]" + " · ".join(f"{k} {v}" for k, v in counts.items()) + "[/dim]")


def _counts(board: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for task in board["tasks"]:
        status = str(task["status"])
        out[status] = out.get(status, 0) + 1
    return out


def _write_yaml(dest: Path, data: dict[str, Any]) -> None:
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 120
    stream = io.StringIO()
    yaml.dump(data, stream)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(stream.getvalue(), encoding="utf-8")
