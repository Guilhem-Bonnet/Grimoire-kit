"""``grimoire task arbitrate`` (GAO-r-le-arbitre-001).

Le seul geste d'une session arbitre : voter une décision JSON, validée par du code. Hors de
:mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ; :func:`register_arbitrate_commands`
l'accroche au même groupe ``task``. La logique vit dans :mod:`grimoire.missions.arbiter`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape


def register_arbitrate_commands(app: typer.Typer) -> None:
    """Accroche ``arbitrate`` au groupe *app*."""
    app.command("arbitrate")(task_arbitrate)


def task_arbitrate(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="La tâche arbitrée.")],
    payload: Annotated[
        str,
        typer.Option("--json", help="La décision (schéma grimoire-arbiter-decision/v1), en JSON."),
    ],
    request: Annotated[
        str, typer.Option("--request", help="L'identifiant de la demande d'arbitrage présentée à la session.")
    ] = "",
    model: Annotated[str, typer.Option("--model", help="Le modèle de la session arbitre, s'il en est un.")] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.")] = Path(),
    ledger_root: Annotated[
        Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")
    ] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Vote une décision d'arbitrage : JSON validé, puis inscrit au ledger (``task.arbitration_decided``).

    Seule une session arbitre du tick (canal ``task-arbitrate`` du journal d'autorité), sous un mandat actif qui
    couvre ``answer``, peut voter : le geste passe par :func:`~grimoire.missions.mandate.require_authority`. Un
    JSON invalide n'est jamais corrigé : il est inscrit comme refus (``task.arbitration_rejected``) et l'humain
    reçoit la question avec cette analyse. L'application de la décision est au tick (``task.arbitrated``).
    """
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.arbiter import ARBITRATION_CHANNEL, ArbiterDecisionError, record_decision
    from grimoire.missions.mandate import require_authority
    from grimoire.missions.schemas import ORIGIN_ORCHESTRATOR

    service = _service(project_root, ledger_root)
    task = _require_task(service, task_id)
    try:
        # La session tourne dans le worktree de la tâche ; le mandat, lui, est inscrit pour la racine du projet
        # (``home_root``), jamais pour le worktree (qui ouvrirait un second projet).
        auth = require_authority(
            service.home_root, "answer", task, ORIGIN_ORCHESTRATOR, what=f"Arbitrage refusé pour {task_id}",
            provenance={"kind": "rule", "ref": f"arbitre:{request or task_id}"},
            channel=ARBITRATION_CHANNEL,
        )
        decision = record_decision(
            service, task_id, payload, request_id=request, actor=actor, model=model,
            mandate_id=auth.mandate_id if auth is not None else "",
        )
    except (GrimoireMissionError, ArbiterDecisionError, ValueError) as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(
        f"[green]OK[/green] arbitrage {decision.decision} pour {task_id} "
        f"[dim](confiance {decision.confidence:.2f}, appliqué au prochain tick)[/dim]"
    )
