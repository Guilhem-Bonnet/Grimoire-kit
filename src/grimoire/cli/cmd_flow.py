"""CLI ``grimoire flow`` — le moteur conduit, l'hôte exécute un node (#204).

Quatre commandes fines sur :class:`grimoire.flows.engine.FlowEngine` : ce
module ne prend aucune décision, il présente ce que le moteur rend et
traduit les erreurs en code de sortie. La logique — dérivation du contrat,
vérification de sortie, avancement du kernel — vit dans ``grimoire.flows``.

``grimoire blueprint compile`` reste la sortie pour les hôtes sans exécuteur
de node : cette commande ajoute une seconde sortie, elle ne retire rien.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.schemas import FlowStatusView, ResumeOutcome
from grimoire.runtime.schemas import WorkflowInstance

flow_app = typer.Typer(
    help="Conduire un blueprint node par node : le kernel avance, l'hôte exécute.",
    no_args_is_help=False,
)

# Le rendu humain part sur stderr : le JSON de stdout reste pipeable, comme le
# reste de la CLI (cf. cmd_task.py).
console = Console(stderr=True)

_KERNEL_RELPATH = Path("_grimoire-runtime-output/runtime")
_FLOWS_RELPATH = Path("_grimoire-runtime-output/flows")

_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet cible.")]
_MISSION_OPTION = Annotated[str, typer.Option("--mission", help="Mission de rattachement (Mission Ledger).")]
_TASK_OPTION = Annotated[str, typer.Option("--task-id", help="Tâche de rattachement (Mission Ledger).")]


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def _engine(project_root: Path) -> FlowEngine:
    root = project_root.resolve()
    return FlowEngine(kernel_root=root / _KERNEL_RELPATH, flows_root=root / _FLOWS_RELPATH)


def _executor(ctx: typer.Context) -> InteractiveNodeExecutor:
    """Un exécuteur silencieux : l'engine doit en recevoir un (protocole
    ``NodeExecutor``), mais c'est cette commande — pas l'exécuteur — qui
    écrit la seule sortie sur stdout, en y incluant le ``run_id`` que le
    contrat seul ne porte pas. Écrire ici *et* dans le contrat produirait
    deux objets JSON concaténés sur le même flux.
    """
    return InteractiveNodeExecutor(json_output=_fmt(ctx) == "json", stream=io.StringIO())


def _emit_status(ctx: typer.Context, view: FlowStatusView) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(view.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{view.run_id}[/bold] — {view.blueprint_id} — [cyan]{view.status}[/cyan]")
    console.print(f"  fait      : {', '.join(view.completed_nodes) or '(aucun)'}")
    console.print(f"  courant   : {view.current_node or '(aucun)'}")
    console.print(f"  restant   : {', '.join(view.pending_nodes) or '(aucun)'}")
    if view.last_refusal:
        console.print(
            f"  [yellow]dernier refus[/yellow] : {view.last_refusal['node_id']} — {view.last_refusal['reason']}"
        )
    if view.contract:
        console.print("")
        console.print(view.contract.to_text())


def _emit_instance(ctx: typer.Context, wfi: WorkflowInstance, note: str = "") -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(wfi.to_dict(), indent=2, ensure_ascii=False))
        return
    suffix = f" [dim]({note})[/dim]" if note else ""
    console.print(f"[green]OK[/green] {wfi.id} — {wfi.status.value}{suffix}")


def _emit_resume(ctx: typer.Context, run_id: str, outcome: ResumeOutcome) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"run_id": run_id, **outcome.to_dict()}, indent=2, ensure_ascii=False))
        return
    if not outcome.ok:
        console.print(f"[red]suspendu[/red] {run_id} — node {outcome.node_id}")
        for fault in outcome.faults:
            console.print(f"  - {fault}")
        return
    if outcome.finished:
        console.print(f"[green]terminé[/green] {run_id} — dernier node : {outcome.node_id}")
        return
    console.print(f"[green]OK[/green] {run_id} — node suivant : {outcome.node_id}")
    if outcome.contract:
        console.print("")
        console.print(outcome.contract.to_text())


def _fail(ctx: typer.Context, exc: GrimoireRuntimeError) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
    else:
        console.print(f"[red]✗[/red] {exc}")
    raise typer.Exit(1) from exc


@flow_app.command("run")
def flow_run(
    ctx: typer.Context,
    blueprint: Annotated[
        Path | None, typer.Argument(help="Fichier .blueprint.json à exécuter. Omis : liste les runs.")
    ] = None,
    project_root: _PROJECT_ROOT = Path(),
    mission: _MISSION_OPTION = "",
    task_id: _TASK_OPTION = "",
) -> None:
    """Démarre un run et présente le contrat du premier node.

    Sans argument, liste les runs connus — ``grimoire flow run`` seul répond
    à « qu'est-ce qui tourne » sans qu'il faille une commande séparée.
    """
    engine = _engine(project_root)
    if blueprint is None:
        views = engine.list_runs()
        if _fmt(ctx) == "json":
            typer.echo(json.dumps([v.to_dict() for v in views], indent=2, ensure_ascii=False))
            return
        if not views:
            console.print("[dim]Aucun run.[/dim]")
            return
        for view in views:
            console.print(f"  {view.run_id}  [{view.status}]  {view.blueprint_id}  courant={view.current_node}")
        return

    try:
        wfi, contract = engine.run(blueprint, executor=_executor(ctx), mission_id=mission, task_id=task_id)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    if _fmt(ctx) == "json":
        typer.echo(
            json.dumps(
                {"run_id": wfi.id, "status": wfi.status.value, "contract": contract.to_dict()},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        console.print(f"[green]OK[/green] {wfi.id} démarré — node courant : {contract.node_id}")
        console.print("")
        console.print(contract.to_text())


@flow_app.command("status")
def flow_status(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Identifiant du run (WFI-...).")],
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """État du run : node courant, nodes faits, dernier refus, contrat courant."""
    engine = _engine(project_root)
    try:
        view = engine.status(run_id)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    _emit_status(ctx, view)


@flow_app.command("resume")
def flow_resume(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Identifiant du run (WFI-...).")],
    output: Annotated[
        Path, typer.Option("--output", help="Fichier JSON : la sortie produite par l'hôte pour le node courant.")
    ],
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Vérifie la sortie du node courant contre son contrat, avance ou suspend.

    Conforme : le node suivant est ouvert et son contrat présenté (ou le run
    se termine s'il n'en reste plus). Non conforme : le run est suspendu,
    nommant le node et la pin fautifs — code de sortie non nul.
    """
    engine = _engine(project_root)
    try:
        payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(ctx, GrimoireRuntimeError(f"{output} : illisible ({exc})"))
        return
    try:
        outcome = engine.resume(run_id, output=payload, executor=_executor(ctx))
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    _emit_resume(ctx, run_id, outcome)
    if not outcome.ok:
        raise typer.Exit(1)


@flow_app.command("abort")
def flow_abort(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Identifiant du run (WFI-...).")],
    reason: Annotated[str, typer.Option("--reason", help="Pourquoi ce run est abandonné.")] = "",
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Abandonne un run — terminal, jamais repris ensuite."""
    engine = _engine(project_root)
    try:
        wfi = engine.abort(run_id, reason=reason)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    _emit_instance(ctx, wfi, "abandonné")
