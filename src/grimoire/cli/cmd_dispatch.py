"""CLI ``grimoire dispatch`` — comptabilité continue du dispatch (issue #442).

Point 5 de l'audit de positionnement du 2026-09-12 : le kit journalise déjà
chaque cascade (``missions.dispatch._record_dispatch_outcome``, tag
``dispatch.outcome``) mais rien ne l'agrégeait en continu avant cette
commande. ``stats`` est la sœur de ``grimoire providers history`` (#312) —
même idée (compter, ne pas classifier), autre source (le journal de traces,
pas le Mission Ledger) et un angle différent : coût par tâche résolue et
fiabilité (pass^k), pas taux d'escalade par couple (type de tâche, classe).

Le contrôle du standard ``dispatch.cost_slo``
(:mod:`grimoire.core.standard_checks.controls`) lit exactement la même
:meth:`~grimoire.traces.ledger.TraceLedger.dispatch_outcome_stats` — cette
commande est la vue humaine sur les mêmes chiffres, jamais un second calcul.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.traces.ledger import TraceLedger

dispatch_app = typer.Typer(help="Comptabilité continue du dispatch : coût par tâche résolue, pass^k.", no_args_is_help=True)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")
_SINCE_OPTION = typer.Option("--since", help="Ne garder que les dispatchs depuis cette ancienneté, ex. `30d`.")

_SINCE_PATTERN = re.compile(r"^(?P<amount>\d+)(?P<unit>[dh])$")


def _get_fmt(ctx: typer.Context) -> str:
    """Format hérité du ``-o/--output`` global, si l'appelant ne force pas ``--json``."""
    return str((ctx.obj or {}).get("output", "text") or "text")


def _since_iso(since: str | None, *, now: datetime | None = None) -> str | None:
    """Convertit ``--since`` (``"30d"``, ``"12h"``) en horodatage ISO plancher.

    ``None`` sans ``--since`` — aucun filtrage. Format volontairement étroit
    (un entier suivi de ``d``/``h``) : un format libre inviterait à parser du
    texte arbitraire pour un filtre qui n'a besoin que de deux unités.
    """
    if since is None:
        return None
    match = _SINCE_PATTERN.match(since.strip())
    if match is None:
        raise typer.BadParameter(f"--since invalide : {since!r} (attendu un entier suivi de `d` ou `h`, ex. `30d`)")
    amount = int(match.group("amount"))
    delta = timedelta(days=amount) if match.group("unit") == "d" else timedelta(hours=amount)
    reference = now or datetime.now(tz=UTC)
    return (reference - delta).isoformat()


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"${value:.4f}"


@dispatch_app.command("stats")
def dispatch_stats(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    since: Annotated[str | None, _SINCE_OPTION] = None,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Coût par tâche résolue, taux d'escalade, part d'inexécutable, et pass^k.

    Lit les événements ``dispatch.outcome`` du journal de traces — écrits une
    fois par cascade de ``grimoire task dispatch`` ou ``grimoire flow run
    --executor dispatch``, jamais par tentative individuelle (voir
    ``grimoire providers history`` pour cet historique-là, au grain de la
    tentative et par couple type de tâche/classe). pass^k : un même node de
    blueprint (ou une même tâche, hors flow) rejoué plusieurs fois compte
    comme une série ; le taux est la part de séries entièrement vertes.
    """
    root = project_root.resolve()
    as_json = json_output or _get_fmt(ctx) == "json"
    try:
        since_iso = _since_iso(since)
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    traces_path = root / TRACES_DIR
    if not (traces_path / "traces.jsonl").is_file():
        if as_json:
            typer.echo(json.dumps({"overall": None}, indent=2, ensure_ascii=False))
            return
        console.print(f"[dim]Aucun journal de traces sous {traces_path}.[/dim]")
        return

    try:
        stats = TraceLedger(traces_path).dispatch_outcome_stats(since_iso=since_iso)
    except GrimoireRuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False))
        return

    if stats.overall.total == 0:
        console.print("[dim]Aucun événement `dispatch.outcome` dans cette fenêtre (`grimoire task dispatch`).[/dim]")
        return

    table = Table(title="Comptabilité du dispatch")
    for column in ("Groupe", "Dispatchs", "Résolus", "Coût/tâche résolue", "Escalade", "Inexécutable"):
        table.add_column(column)

    def _row(name: str, group: object) -> None:
        table.add_row(
            name,
            str(group.total),  # type: ignore[attr-defined]
            str(group.resolved),  # type: ignore[attr-defined]
            _usd(group.cost_per_resolved_task_usd),  # type: ignore[attr-defined]
            _pct(group.escalation_rate),  # type: ignore[attr-defined]
            _pct(group.inexecutable_share),  # type: ignore[attr-defined]
        )

    _row("[bold]ensemble[/bold]", stats.overall)
    for class_name, group in stats.by_class.items():
        _row(f"classe {class_name}", group)
    for provider_name, group in stats.by_provider.items():
        _row(f"fournisseur {provider_name}", group)
    console.print(table)

    if stats.pass_k_observations == 0:
        console.print("[dim]pass^k : aucune série rejouée (un même node exécuté au moins deux fois).[/dim]")
    else:
        console.print(
            f"pass^k : {_pct(stats.pass_k_rate)} ({stats.pass_k_fully_green}/{stats.pass_k_observations} séries "
            "entièrement vertes)"
        )
