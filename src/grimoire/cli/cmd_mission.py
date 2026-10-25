"""``grimoire mission`` : ouvrir une mission (P49, R25) et la vue d'une mission, tirée des journaux (W4-04, OBS-6).

Ce module ne fait que présenter ce que :mod:`grimoire.missions.report` assemble et ce que
:meth:`grimoire.missions.service.TaskService.open_mission` décide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from grimoire.missions.report import (
    DEFAULT_EVIDENCE_RELPATH,
    DEFAULT_LEDGER_RELPATH,
    build_mission_report,
    render_json,
    render_markdown,
)

mission_app = typer.Typer(help="Missions : ouverture, rapport de coût, d'acceptance, de palier et de preuves.", no_args_is_help=True)

# Le rapport part sur stdout (redirigeable), les messages sur stderr.
console = Console(stderr=True)


@mission_app.command("report")
def mission_report(
    ctx: typer.Context,
    mission_id: Annotated[str, typer.Argument(help="Identifiant de la mission.")],
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON au lieu du Markdown.")] = False,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.", show_default=False)] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = DEFAULT_LEDGER_RELPATH,
    evidence_root: Annotated[Path, typer.Option("--evidence-root", help="Racine des preuves.")] = DEFAULT_EVIDENCE_RELPATH,
) -> None:
    """Rapport d'une mission, chaque ligne avec sa source : coût, acceptance, palier, preuves, rejeu.

    Lecture seule et déterministe : deux exécutions sur les mêmes journaux
    donnent les mêmes octets. Un coût qu'aucun journal ne donne est écrit
    « coût inconnu », jamais un total numérique ; un agrégat partiel est une
    borne basse. Le lien d'une tentative avec sa trace de cascade est exact ou
    absent (``trace_link``).
    """
    report = build_mission_report(project_root, mission_id, ledger_root=ledger_root, evidence_root=evidence_root)
    if report is None:
        console.print(f"[red]✗[/red] Mission inconnue : {mission_id} (ledger absent ou sans cette mission).")
        raise typer.Exit(1)
    as_json = json_output or str((ctx.obj or {}).get("output", "text")) == "json"
    typer.echo(render_json(report) if as_json else render_markdown(report), nl=False)


@mission_app.command("open")
def mission_open(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Titre de la mission (unique dans le ledger, casse et espaces mis à part).")],
    description: Annotated[str, typer.Option("--description", "-d", help="Ce que la mission vise.")] = "",
    scope_repo: Annotated[list[str] | None, typer.Option("--scope-repo", help="Dépôt du périmètre (répétable).")] = None,
    scope_surface: Annotated[list[str] | None, typer.Option("--scope-surface", help="Surface du périmètre (répétable).")] = None,
    scope_pack: Annotated[list[str] | None, typer.Option("--scope-pack", help="Pack du périmètre (répétable).")] = None,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.", show_default=False)] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = DEFAULT_LEDGER_RELPATH,
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Ouvre une mission dans le Mission Ledger et rend son identifiant (stdout).

    `grimoire task add --mission <identifiant>` y rattache ensuite des tâches ;
    `task_context` (MCP) rend le périmètre `--scope-*` avec la tâche. Un titre
    déjà pris est refusé, avec l'identifiant de la mission homonyme ; rien n'est
    écrit sur un refus.
    """
    from grimoire.cli.cmd_task import _service
    from grimoire.core.exceptions import GrimoireError

    try:
        mission = _service(project_root, ledger_root).open_mission(
            title, description=description, scope_repos=tuple(scope_repo or ()),
            scope_surfaces=tuple(scope_surface or ()), scope_packs=tuple(scope_pack or ()), actor=actor,
        )
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if str((ctx.obj or {}).get("output", "text")) == "json":
        typer.echo(json.dumps(mission.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] mission ouverte : {escape(mission.title)}")
    typer.echo(mission.id)
