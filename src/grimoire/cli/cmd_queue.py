"""CLI ``grimoire queue`` — la file des tâches prêtes et leur admission (K10, épopée #705).

``plan`` rend, à blanc, ce que l'admission déciderait maintenant : l'ordre de la file
(priorité, vieillissement, ancienneté), les tâches admises, et pour chaque ``Defer`` sa
raison (``wip``, ``ci``, ``host``, ``llm``, ``quota``, ``unknown``…). ``release`` libère à
la main un créneau CI dont la fin des checks n'est pas observable. Aucune de ces commandes
n'appelle de LLM ni n'écrit hors du ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from grimoire.missions.admission import Admit, Defer, QueuePlan

queue_app = typer.Typer(help="File des tâches prêtes et admission par réservoirs : plan, release.", no_args_is_help=True)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_LEDGER_ROOT_OPTION = typer.Option("--ledger-root", help="Racine du Mission Ledger (relative au projet).")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _as_json(ctx: typer.Context, flag: bool) -> bool:
    return flag or str((ctx.obj or {}).get("output", "text") or "text") == "json"


@queue_app.command("plan")
def queue_plan(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
    routes: Annotated[bool, typer.Option("--routes/--no-routes", help="Lire la route du routeur (sans l'écrire).")] = True,
) -> None:
    """Ce que l'admission déciderait maintenant, sans rien écrire.

    Sort en 1 si la capacité déclarée est illisible (une garde qui échoue fermée refuse),
    et en 2 si une tâche est en famine (différée depuis plus de 24 h).
    """
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve(), ledger_root)
    plan = service.queue_plan(routes=routes)
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        _render(plan)
    if plan.capacity.errors:
        raise typer.Exit(1)
    if plan.famine:
        raise typer.Exit(2)


def _render(plan: QueuePlan) -> None:
    from grimoire.missions.admission import Defer

    capacity = plan.capacity
    quota = "illimité" if capacity.unlimited else f"{capacity.tokens_per_day} tokens/jour"
    used = plan.view.tokens
    consumed = "inconnue" if used.tokens is None else f">= {used.tokens}"
    console.print(
        f"[bold]File[/bold] : {len(plan.order)} prête(s), {len(plan.admitted)} admise(s), {len(plan.deferred)} différée(s) — "
        f"tenues {len(plan.view.held)}, CI {len(plan.view.ci_occupants)}/{capacity.ci_runners}, "
        f"quota {quota} (consommé {consumed})"
    )
    for section, reason in capacity.errors.items():
        console.print(f"[red]capacité illisible ({section}) : {reason}[/red]")
    table = Table(title="Admission")
    for column in ("tâche", "priorité", "décision", "raison"):
        table.add_column(column)
    shown = {t: (b, e) for t, b, e in plan.order}
    decisions: list[Admit | Defer] = [*plan.admitted, *plan.deferred]
    for decision in decisions:
        base, effective = shown.get(decision.task_id, ("", ""))
        priority = effective if base == effective else f"{effective} (vieilli depuis {base})"
        table.add_row(decision.task_id, priority, decision.label, decision.detail if isinstance(decision, Defer) else "")
    console.print(table)
    for task_id, reason in plan.excluded:
        console.print(f"[dim]hors file : {task_id} — {reason}[/dim]")
    for famine in plan.famine:
        console.print(f"[yellow]attention {famine.attention.level} : {famine.task_id} — {famine.reason}[/yellow]")


@queue_app.command("release")
def queue_release(
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche qui tient le créneau.")],
    pool: Annotated[str, typer.Option("--pool", help="Le réservoir à libérer.")] = "ci",
    reason: Annotated[str, typer.Option("--reason", help="Pourquoi le créneau est libre.")] = "",
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    actor: Annotated[str, typer.Option("--actor", help="Qui libère.")] = "cli",
) -> None:
    """Libère un créneau (`capacity.released`) : un geste d'humain, quand la fin des checks n'est pas observable."""
    from grimoire.cli.cmd_task import _comment_origin
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.schemas import ORIGIN_HUMAN
    from grimoire.missions.service import TaskService

    if _comment_origin() != ORIGIN_HUMAN:
        typer.echo("Libérer un créneau est un geste d'humain : l'origine humaine n'est pas établie.", err=True)
        raise typer.Exit(1)
    try:
        TaskService(project_root.resolve(), ledger_root).release_capacity(task_id, pool, actor, reason)
    except (GrimoireError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    console.print(f"Créneau {pool} de {task_id} libéré.")
