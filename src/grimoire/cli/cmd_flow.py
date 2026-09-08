"""CLI ``grimoire flow`` — le moteur conduit, l'hôte exécute un node (#204).

Quatre commandes fines sur :class:`grimoire.flows.engine.FlowEngine` : ce
module ne prend aucune décision, il présente ce que le moteur rend et
traduit les erreurs en code de sortie. La logique — dérivation du contrat,
vérification de sortie, avancement du kernel — vit dans ``grimoire.flows``.

``grimoire blueprint compile`` reste la sortie pour les hôtes sans exécuteur
de node : cette commande ajoute une seconde sortie, elle ne retire rien.

``--executor dispatch`` (#311) est la seconde option d'exécuteur : ``run`` et
``resume`` délèguent alors chaque node à la cascade par classe de
vérifiabilité (``grimoire.flows.dispatch_executor``) au lieu d'attendre un
``--result`` de l'hôte, et enchaînent les nodes tant qu'elle reste verte.
``interactive`` reste le défaut, comportement inchangé.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.dispatch_executor import (
    FlowDispatchOutcome,
    node_dispatch_history,
    resume_with_dispatch,
    run_with_dispatch,
)
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.schemas import FlowStatusView, ResumeOutcome
from grimoire.missions.dispatch import DEFAULT_CALL_TIMEOUT_S
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS
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
_EXECUTOR_OPTION = Annotated[
    str,
    typer.Option("--executor", help="interactive (défaut, l'hôte exécute) ou dispatch (délègue à la cascade, #311)."),
]
_MAX_TIER_OPTION = Annotated[
    str | None, typer.Option("--max-tier", help="dispatch seul : ne pas dépasser ce palier (cheap, mid, strong).")
]
_TIMEOUT_OPTION = Annotated[
    float, typer.Option("--timeout", help="dispatch seul : timeout d'un appel fournisseur, en secondes.")
]


def _check_executor_name(ctx: typer.Context, executor: str) -> None:
    if executor not in ("interactive", "dispatch"):
        _fail(ctx, GrimoireRuntimeError(f"--executor inconnu : {executor!r} (attendu : interactive, dispatch)"))


def _check_max_tier(ctx: typer.Context, max_tier: str | None) -> None:
    if max_tier is not None and max_tier not in SUPPORTED_MODEL_TIERS:
        _fail(
            ctx,
            GrimoireRuntimeError(f"--max-tier inconnu : {max_tier!r} (attendu : {', '.join(SUPPORTED_MODEL_TIERS)})"),
        )


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


def _node_order(view: FlowStatusView) -> list[str]:
    """Les nodes du run dans l'ordre — recomposé, ``FlowStatusView`` ne le porte pas d'un bloc."""
    order = list(view.completed_nodes)
    if view.current_node:
        order.append(view.current_node)
    order.extend(view.pending_nodes)
    return order


def _emit_status(ctx: typer.Context, view: FlowStatusView, project_root: Path) -> None:
    history = node_dispatch_history(project_root.resolve(), view.run_id, _node_order(view))
    if _fmt(ctx) == "json":
        payload = view.to_dict()
        payload["dispatch"] = history
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{view.run_id}[/bold] — {view.blueprint_id} — [cyan]{view.status}[/cyan]")
    console.print(f"  fait      : {', '.join(view.completed_nodes) or '(aucun)'}")
    console.print(f"  courant   : {view.current_node or '(aucun)'}")
    console.print(f"  restant   : {', '.join(view.pending_nodes) or '(aucun)'}")
    if view.last_refusal:
        console.print(
            f"  [yellow]dernier refus[/yellow] : {view.last_refusal['node_id']} — {view.last_refusal['reason']}"
        )
    if history:
        console.print("")
        console.print("[bold]dispatch (#311)[/bold] :")
        for row in history:
            relire = " [magenta]à relire[/magenta]" if row["needs_review"] else ""
            console.print(
                f"  - {row['node_id']} : {row['verdict']} par {row['provider']} ({row['tier']}), "
                f"{row['attempts']} tentative(s), coût {row['cost_usd']}{relire}"
            )
            for u in row["uncertainties"]:
                console.print(f"      [dim]incertitude : {u.get('where')} — {u.get('what')}[/dim]")
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


def _emit_dispatch_outcome(ctx: typer.Context, outcome: FlowDispatchOutcome) -> None:
    """Rapport de ``--executor dispatch`` : coût total connu et escalades (lot 0, #311)."""
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
        return
    for node in outcome.nodes:
        relire = " [magenta]à relire[/magenta]" if node.needs_review else ""
        console.print(
            f"  {node.node_id} : {node.verdict} par {node.provider} — {node.attempts} tentative(s), "
            f"{node.escalations} escalade(s), coût {node.cost_usd}{relire}"
        )
    console.print(
        f"[bold]coût total connu[/bold] : {outcome.total_cost_usd} — [bold]escalades[/bold] : {outcome.escalations}"
    )
    if outcome.status == "finished":
        console.print(f"[green]terminé[/green] {outcome.run_id}")
        return
    if outcome.status == "waiting_host":
        console.print(f"[yellow]suspendu[/yellow] {outcome.run_id} — node « {outcome.node_id} » revient à l'hôte")
        if outcome.host_reason:
            console.print(f"  [dim]{outcome.host_reason}[/dim]")
        if outcome.contract:
            console.print("")
            console.print(outcome.contract.to_text())
        return
    console.print(f"[red]bloqué[/red] {outcome.run_id} — node « {outcome.node_id} »")
    for fault in outcome.faults:
        console.print(f"  - {fault}")


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
    executor: _EXECUTOR_OPTION = "interactive",
    max_tier: _MAX_TIER_OPTION = None,
    timeout: _TIMEOUT_OPTION = DEFAULT_CALL_TIMEOUT_S,
) -> None:
    """Démarre un run et présente le contrat du premier node.

    Sans argument, liste les runs connus — ``grimoire flow run`` seul répond
    à « qu'est-ce qui tourne » sans qu'il faille une commande séparée.

    ``--executor dispatch`` enchaîne les nodes tant que la cascade reste
    verte, checkpointe après chacun, et s'arrête au premier node rouge ou
    V2 — avec le rapport (fournisseur, tentatives, coût, escalades) plutôt
    qu'un contrat à présenter à l'hôte.
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

    _check_executor_name(ctx, executor)
    _check_max_tier(ctx, max_tier)

    if executor == "dispatch":
        try:
            outcome = run_with_dispatch(
                engine,
                blueprint,
                project_root=project_root.resolve(),
                mission_id=mission,
                task_id=task_id,
                max_tier=max_tier,
                call_timeout=timeout,
            )
        except GrimoireRuntimeError as exc:
            _fail(ctx, exc)
            return
        _emit_dispatch_outcome(ctx, outcome)
        if outcome.status == "blocked":
            raise typer.Exit(1)
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
    """État du run : node courant, nodes faits, dernier refus, contrat courant.

    Si des nodes ont été dispatchés (``--executor dispatch``), leur ligne
    porte fournisseur, tentatives, verdict, relecture requise et incertitudes
    déclarées — lus depuis l'événement ``task.dispatched`` du Mission Ledger,
    jamais depuis un état en mémoire qui n'aurait pas survécu à l'appel CLI.
    """
    engine = _engine(project_root)
    try:
        view = engine.status(run_id)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    _emit_status(ctx, view, project_root)


@flow_app.command("resume")
def flow_resume(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Identifiant du run (WFI-...).")],
    # `--result`, pas `--output` : ce dernier est déjà le format de sortie
    # global de la CLI (`--output json`), et un même mot pour deux choses
    # sur la même commande aurait fini par tromper quelqu'un.
    # Optionnel : ``--executor dispatch`` ne relit la sortie d'aucun fichier,
    # il refait tourner la cascade sur le node courant.
    output: Annotated[
        Path | None, typer.Option("--result", help="interactive seul : fichier JSON produit par l'hôte.")
    ] = None,
    project_root: _PROJECT_ROOT = Path(),
    executor: _EXECUTOR_OPTION = "interactive",
    max_tier: _MAX_TIER_OPTION = None,
    timeout: _TIMEOUT_OPTION = DEFAULT_CALL_TIMEOUT_S,
) -> None:
    """Vérifie la sortie du node courant contre son contrat, avance ou suspend.

    Conforme : le node suivant est ouvert et son contrat présenté (ou le run
    se termine s'il n'en reste plus). Non conforme : le run est suspendu,
    nommant le node et la pin fautifs — code de sortie non nul.

    ``--executor dispatch`` reprend le node courant (celui qu'un crash a
    laissé ouvert) en le relançant dans la cascade, puis enchaîne comme
    ``flow run --executor dispatch`` — sans ``--result``, l'hôte n'ayant
    justement rien produit lui-même à relire.
    """
    engine = _engine(project_root)
    _check_executor_name(ctx, executor)
    _check_max_tier(ctx, max_tier)

    if executor == "dispatch":
        try:
            outcome = resume_with_dispatch(
                engine, run_id, project_root=project_root.resolve(), max_tier=max_tier, call_timeout=timeout
            )
        except GrimoireRuntimeError as exc:
            _fail(ctx, exc)
            return
        _emit_dispatch_outcome(ctx, outcome)
        if outcome.status == "blocked":
            raise typer.Exit(1)
        return

    if output is None:
        _fail(ctx, GrimoireRuntimeError("--result est requis avec --executor interactive"))
        return
    try:
        payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(ctx, GrimoireRuntimeError(f"{output} : illisible ({exc})"))
        return
    try:
        outcome_resume = engine.resume(run_id, output=payload, executor=_executor(ctx))
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    _emit_resume(ctx, run_id, outcome_resume)
    if not outcome_resume.ok:
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
