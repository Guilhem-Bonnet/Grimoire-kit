"""``grimoire task fold-usage`` et l'usage de ``grimoire task show`` (W4-01).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_usage_commands` accroche la commande au même groupe ``task``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape


def register_usage_commands(app: typer.Typer) -> None:
    """Accroche ``fold-usage`` au groupe *app*."""
    app.command("fold-usage")(task_fold_usage)


def _usage_of(task: Any, root: Path) -> dict[str, Any]:
    """Tokens et coût repliés pour cette tâche (W4-01), sans rien créer sur le disque."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.capabilities import recorded_contract, resolve_host
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import task_usage

    traces_path = root / TRACES_DIR
    traces = TraceLedger(traces_path).list_traces(task_id=task.id) if (traces_path / "traces.jsonl").is_file() else []
    host = resolve_host(task.claim.host_id) if task.claim else None
    available = None if host is None else recorded_contract(host).verified("local_token_usage")
    return task_usage(traces, host_usage_available=available)


def _usage_line(usage: dict[str, Any]) -> str:
    from grimoire.costs import format_usd

    source = usage["usage_source"]
    if source in ("none", "unavailable"):
        return f"{source}" + (" (l'hôte n'expose pas de journal d'usage vérifié)" if source == "unavailable" else "")
    if source == "reported":
        return f"rapporté par l'ouvrier seulement ({usage['reported']['calls']} appel(s))"
    cout = format_usd(usage["cost_usd"], usage["cost_status"], usage["unpriced_calls"])
    ligne = (
        f"{usage['calls']} appel(s), {usage['input_tokens']} in / {usage['output_tokens']} out, "
        f"cache {usage['cache_read_tokens']} lu / {usage['cache_creation_tokens']} écrit, coût {cout} [{source}]"
    )
    if usage["discrepancy"] is not None:
        ligne += f" — écart avec le coût rapporté : {usage['discrepancy']['relative_gap']:.0%}"
    return ligne


def task_fold_usage(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    transcript: Annotated[Path, typer.Argument(help="Journal de session Claude Code (.jsonl).")],
    mission_id: Annotated[str, typer.Option("--mission-id")] = "",
    since: Annotated[str, typer.Option("--since", help="Début de la fenêtre de la tâche (ISO 8601, inclus).")] = "",
    until: Annotated[str, typer.Option("--until", help="Fin de la fenêtre de la tâche (ISO 8601, inclus).")] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (pour les gates de preuve).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    traces_root: Annotated[Path, typer.Option("--traces-root", help="Racine du TraceLedger.")] = Path("_grimoire-output/traces"),
) -> None:
    """Replie l'usage de tokens d'un journal de session dans le TraceLedger, rattaché à la tâche (W4-01).

    Idempotent : rejouer la commande, ou la rejouer sur un journal qui a grandi,
    n'ajoute que les appels nouveaux. Un appel dont le coût ne peut pas être
    affirmé (modèle non tarifé, tokens de cache) est replié avec un coût inconnu.

    Un appel n'appartient qu'à une tâche : un journal qui en couvre plusieurs se
    répartit avec ``--since`` / ``--until`` (sinon la première tâche repliée
    reçoit tout) ; les appels déjà tenus par une autre tâche sont nommés.
    """
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import fold_claude_transcript

    root = project_root.resolve()
    _require_task(_service(project_root, ledger_root), task_id)
    traces_path = traces_root if traces_root.is_absolute() else root / traces_root
    try:
        result = fold_claude_transcript(
            TraceLedger(traces_path), transcript, task_id=task_id, mission_id=mission_id, since=since, until=until
        )
    except ValueError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(2) from None
    except OSError as exc:
        console.print(f"[red]✗[/red] Journal illisible : {escape(str(exc))}")
        raise typer.Exit(1) from None
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(
        f"[green]OK[/green] {result.new_calls} appel(s) replié(s), {result.already_folded} déjà connu(s)"
        f" ({result.unreadable_lines} ligne(s) illisible(s), {result.invalid_usage} sans compteurs valides)"
    )
    if result.outside_window:
        console.print(f"[dim]{result.outside_window} appel(s) hors de la fenêtre --since/--until, non attribués.[/dim]")
    for holder, count in sorted(result.held_by_other_tasks.items()):
        console.print(
            f"[yellow]![/yellow] {count} appel(s) déjà attribué(s) à {escape(holder)}, non réattribués"
            " (un appel n'appartient qu'à une tâche ; --since/--until répartit un journal)."
        )
