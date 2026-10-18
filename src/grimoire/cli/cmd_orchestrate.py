"""CLI ``grimoire orchestrate`` — la pause drain/hold et le tick (K11, épopée #705, plan §4.4).

``pause`` pose un drapeau fichier (projet, machine ou tâches nommées), ``resume`` le lève, ``status`` dit ce
qui est en pause et ce qu'un ``hold`` fait vraiment sur chaque hôte, ``tick`` est la fonction que les
déclencheurs appellent. Aucune de ces commandes n'appelle de LLM. ``resume`` et la pause de toute la
machine sont des gestes d'humain : l'origine humaine est exigée comme pour ``queue release``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

if TYPE_CHECKING:
    from grimoire.missions.orchestrate import TickReport
    from grimoire.missions.pause import PauseState

orchestrate_app = typer.Typer(
    help="Pause drain/hold (projet, machine, tâches) et tick d'orchestration : pause, resume, status, tick.",
    no_args_is_help=True,
)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_LEDGER_ROOT_OPTION = typer.Option("--ledger-root", help="Racine du Mission Ledger (relative au projet).")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _as_json(ctx: typer.Context, flag: bool) -> bool:
    return flag or str((ctx.obj or {}).get("output", "text") or "text") == "json"


def _require_human(what: str) -> None:
    from grimoire.cli.cmd_task import _comment_origin
    from grimoire.missions.schemas import ORIGIN_HUMAN

    if _comment_origin() != ORIGIN_HUMAN:
        typer.echo(f"{what} est un geste d'humain : l'origine humaine n'est pas établie.", err=True)
        raise typer.Exit(1)


@orchestrate_app.command("pause")
def orchestrate_pause(
    ctx: typer.Context,
    drain: Annotated[bool, typer.Option("--drain", help="Rien n'est admis, les agents en cours finissent (défaut).")] = False,
    hold: Annotated[
        bool,
        typer.Option("--hold", help="En plus de drain, sur un hôte fencé : les outils mutants sont refusés, sauf le point de reprise."),
    ] = False,
    project: Annotated[bool, typer.Option("--project", help="Le projet (défaut).")] = False,
    global_scope: Annotated[bool, typer.Option("--global", help="Tous les projets de la machine (geste d'humain).")] = False,
    task: Annotated[list[str] | None, typer.Option("--task", help="Ne porter que sur ces tâches (répétable).")] = None,
    reason: Annotated[str, typer.Option("--reason", help="Pourquoi.")] = "",
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    actor: Annotated[str, typer.Option("--actor", help="Qui demande.")] = "cli",
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Pose un drapeau de pause : `--drain` (défaut) ou `--hold`, du projet, de la machine ou de tâches."""
    from grimoire.missions.pause import DRAIN, HOLD, SCOPE_GLOBAL, SCOPE_PROJECT, PauseError, write_pause

    if drain and hold:
        typer.echo("--drain et --hold s'excluent.", err=True)
        raise typer.Exit(2)
    if project and global_scope:
        typer.echo("--project et --global s'excluent.", err=True)
        raise typer.Exit(2)
    if global_scope:
        if task:
            typer.echo("--task ne se combine pas avec --global : un drapeau de machine ne nomme pas de tâche.", err=True)
            raise typer.Exit(2)
        _require_human("Pauser toute la machine")
    try:
        flag = write_pause(
            project_root.resolve(), mode=HOLD if hold else DRAIN, scope=SCOPE_GLOBAL if global_scope else SCOPE_PROJECT,
            tasks=tuple(task or ()), actor=actor, reason=reason,
        )
    except PauseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(flag.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"Pause posée : {flag.label}.")
    console.print("[dim]`grimoire orchestrate resume` la lève ; le tick suivant rend alors les attentes `paused`.[/dim]")


@orchestrate_app.command("resume")
def orchestrate_resume(
    ctx: typer.Context,
    global_scope: Annotated[bool, typer.Option("--global", help="Lever la pause de la machine.")] = False,
    task: Annotated[list[str] | None, typer.Option("--task", help="Ne lever que pour ces tâches (répétable).")] = None,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Lève le drapeau (geste d'humain). Le tick suivant repasse les `waiting(paused)` en `ready`, dans l'ordre de la file."""
    from grimoire.missions.pause import SCOPE_GLOBAL, SCOPE_PROJECT, PauseError, clear_pause

    _require_human("Reprendre")
    try:
        lifted = clear_pause(
            project_root.resolve(), scope=SCOPE_GLOBAL if global_scope else SCOPE_PROJECT, tasks=tuple(task or ())
        )
    except PauseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if _as_json(ctx, json_output):
        typer.echo(json.dumps({"lifted": lifted.to_dict() if lifted else None}, indent=2, ensure_ascii=False))
        return
    if lifted is None:
        console.print("Aucune pause à lever.")
        return
    console.print(f"Pause levée : {lifted.label}.")


def _hold_lines(project_root: Path, ledger_root: Path, state: PauseState) -> list[str]:
    """Ce qu'un `hold` fait sur chaque tâche tenue : refus d'outil sur un hôte fencé, drain sinon."""
    from grimoire.hosts.capabilities import claim_host_is_fenced
    from grimoire.missions.pause import effective_mode
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project_root, ledger_root)
    if not service.has_ledger:
        return []
    lines: list[str] = []
    for task in service.ledger.list_tasks():
        mode = state.mode_for(task.id)
        if task.status not in (TaskState.CLAIMED, TaskState.RUNNING) or task.claim is None or mode is None:
            continue
        fenced = claim_host_is_fenced(task.claim.session_host or task.claim.host_id, task.claim.session_host_version)
        applied, note = effective_mode(mode, fenced=fenced)
        lines.append(f"  - {task.id} : {applied}" + (f" — {note}" if note else ""))
    waiting = [t.id for t in service.ledger.list_tasks() if t.status is TaskState.WAITING and t.waiting and t.waiting.reason == "paused"]
    if waiting:
        lines.append(f"  - garées en pause : {', '.join(waiting)}")
    return lines


@orchestrate_app.command("status")
def orchestrate_status(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Les drapeaux de pause en vigueur (projet et machine), leur périmètre, et l'effet d'un hold par tâche tenue."""
    from grimoire.missions.pause import home_root, read_pause

    root = project_root.resolve()
    state = read_pause(home_root(root))
    lines = _hold_lines(root, ledger_root, state) if state.active else []
    if _as_json(ctx, json_output):
        typer.echo(json.dumps({**state.to_dict(), "held_tasks": lines}, indent=2, ensure_ascii=False))
        return
    if not state.active:
        console.print("Pas de pause : le tick admet normalement.")
        return
    for flag in state.flags:
        since = f" depuis {flag.since}" if flag.since else ""
        why = f" ({flag.reason})" if flag.reason else ""
        console.print(f"Pause {flag.label}{since}{why}")
    for attention in state.attention():
        console.print(f"[yellow]attention : {attention}[/yellow]")
    for line in lines:
        console.print(line)


def _render_tick(report: TickReport) -> None:
    if report.status == "paused":
        console.print("Pause en vigueur : le tick sort sans ouvrir le ledger ni rien dispatcher.")
    elif report.status == "idle":
        console.print("Aucun ledger : rien à faire.")
    else:
        plan = report.plan
        admitted = len(plan.admitted) if plan is not None else 0
        deferred = len(plan.deferred) if plan is not None else 0
        console.print(
            f"Tick : {len(report.reaped)} récolte(s), {sum(1 for w in report.woken if w.action == 'wake')} réveil(s), "
            f"{admitted} admise(s), {deferred} différée(s)."
        )
    for attention in report.attention:
        console.print(f"[yellow]attention : {attention}[/yellow]")
    for note in report.notes:
        console.print(f"[dim]{note}[/dim]")


@orchestrate_app.command("tick")
def orchestrate_tick(
    ctx: typer.Context,
    apply: Annotated[bool, typer.Option("--apply", help="Écrire (sinon : montrer seulement).")] = False,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Un tick : lit les drapeaux de pause d'abord, puis récolte, rend les attentes levées et calcule l'admission.

    Sous une pause de tout le périmètre, il sort sans ouvrir le ledger, avec le code 0 : une pause n'est pas une
    panne, un minuteur ne doit pas la lire comme un échec. Sans `--apply`, rien n'est écrit.
    """
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.orchestrate import run_tick

    try:
        report = run_tick(project_root.resolve(), ledger_root=ledger_root, apply=apply)
    except GrimoireError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if _as_json(ctx, json_output):
        typer.echo(json.dumps({**report.to_dict(), "applied": apply}, indent=2, ensure_ascii=False))
    else:
        _render_tick(report)
        if not apply and report.status == "ran":
            console.print("[dim]Aperçu : relancer avec --apply pour écrire.[/dim]")
