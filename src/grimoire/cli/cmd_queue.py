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
from typing import TYPE_CHECKING, Annotated, Any

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
    for flag in plan.pause.flags:
        console.print(f"[yellow]pause : {flag.label} — aucune admission sous ce drapeau[/yellow]")
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


@queue_app.command("rules")
def queue_rules(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Les règles d'usage de `capacity.yaml`, la consommation du jour et l'état de chaque règle (lecture seule).

    La consommation est un minimum (`quota.consumed`, écrit par le tick d'après les journaux de session). Sort en 1
    si `capacity.yaml` est illisible ; une règle invalide est listée sous « ignorées », sans faire échouer la commande.
    """
    from datetime import UTC, datetime

    from grimoire.missions.admission import load_capacity
    from grimoire.missions.service import TaskService
    from grimoire.missions.usage_rules import rules_report

    service = TaskService(project_root.resolve(), ledger_root)
    capacity = load_capacity(service.home_root)
    report = rules_report(capacity.usage_rules, capacity.rule_issues, service.ledger.list_events() if service.has_ledger else [], datetime.now(UTC))
    report["quota"] = {"tokens_per_day": capacity.tokens_per_day, "on_unknown": capacity.on_unknown}
    report["capacity_errors"] = dict(capacity.errors)
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _render_rules(report)
    if capacity.errors:
        raise typer.Exit(1)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _bound(today: dict[str, Any], metric: str) -> str:
    return "" if today[f"{metric}_complete"] else " (borne basse : une partie est inconnue)"


def _render_rules(report: dict[str, Any]) -> None:
    today = report["consumption_today"]
    quota = report["quota"]
    console.print(
        f"[bold]Aujourd'hui ({today['day']} UTC)[/bold] : {today['tokens']} tokens{_bound(today, 'tokens')}, "
        f"{today['usd']:.4f} USD{_bound(today, 'usd')}, {today['sessions']} session(s) — "
        f"plafond {quota['tokens_per_day']} tokens/jour, consommation inconnue : {quota['on_unknown']}"
    )
    for section, reason in report["capacity_errors"].items():
        console.print(f"[red]capacité illisible ({section}) : {reason}[/red]")
    if not report["rules"]:
        console.print("[dim]Aucune règle d'usage valide dans capacity.yaml (usage_rules).[/dim]")
    else:
        table = Table(title="Règles d'usage")
        for column in ("règle", "portée", "mesure", "seuil", "action", "consommation", "état"):
            table.add_column(column)
        for r in report["rules"]:
            scope = r["scope"] + (f" {r['scope_key']}" if r["scope_key"] else "")
            table.add_row(r["id"], scope, r["metric"], _number(r["threshold"]), r["action"], _number(r["value"]), r["state"])
        console.print(table)
    for issue in report["issues"]:
        console.print(f"[yellow]{issue}[/yellow]")


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
    from grimoire.missions.mandate import gesture_origin, require_authority
    from grimoire.missions.schemas import ORIGIN_HUMAN
    from grimoire.missions.service import TaskService

    origin, provenance = gesture_origin(_comment_origin())
    service = TaskService(project_root.resolve(), ledger_root)
    if origin != ORIGIN_HUMAN:
        # #876, verrou 6 : l'orchestrateur sous un mandat qui couvre `queue-release` libère aussi.
        try:
            require_authority(
                project_root.resolve(), "queue-release", service.require(task_id), origin, what="Libérer un créneau",
                provenance=provenance,
            )
        except GrimoireError as exc:
            typer.echo(f"Libérer un créneau est un geste d'humain : {exc}", err=True)
            raise typer.Exit(1) from exc
    try:
        service.release_capacity(task_id, pool, actor, reason)
    except (GrimoireError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    console.print(f"Créneau {pool} de {task_id} libéré.")
