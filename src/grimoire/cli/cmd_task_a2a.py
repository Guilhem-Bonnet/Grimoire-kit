"""``grimoire task import-a2a`` (R03) : importer une tâche A2A dans le Mission Ledger.

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ; la
logique vit dans :class:`grimoire.bridges.a2a_adapter.A2AAdapter`. Garde-fou :
une tâche ``completed`` venue de l'extérieur n'est jamais ``closed``, elle atterrit
en ``needs_verification`` (ADR-005, menace THR-004).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def register_a2a_commands(app: typer.Typer) -> None:
    app.command("import-a2a")(task_import_a2a)


def task_import_a2a(
    ctx: typer.Context,
    fichier: Annotated[Path, typer.Argument(help="Fichier JSON d'une tâche A2A (id, status.state, input, artifacts).")],
    mission: Annotated[str, typer.Option("--mission", help="Mission d'accueil ; vide ou inconnue : une mission est créée.")] = "",
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path(
        "_grimoire-runtime-output/ledger"
    ),
) -> None:
    """Importe une tâche A2A ; un `completed` externe devient `needs_verification`, jamais `closed`."""
    from grimoire.bridges.a2a_adapter import A2AAdapter, A2ATask
    from grimoire.cli.cmd_task import _fmt, console
    from grimoire.evidence.service import EvidenceService
    from grimoire.missions.ledger import MissionLedger
    from grimoire.traces.ledger import TraceLedger

    try:
        tache = A2ATask.from_dict(json.loads(fichier.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        console.print(f"[red]✗[/red] {fichier} n'est pas une tâche A2A lisible : {exc!r}")
        raise typer.Exit(2) from exc

    adaptateur = A2AAdapter(
        MissionLedger(ledger_root),
        EvidenceService(ledger_root.parent / "evidence"),
        TraceLedger(ledger_root.parent / "traces"),
    )
    rapport = adaptateur.import_task(tache, mission_id=mission)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(rapport.to_dict(), indent=2, ensure_ascii=False))
    else:
        console.print(f"[green]OK[/green] {rapport.grimoire_task_id} ← A2A {rapport.a2a_task_id} : {rapport.state_mapped}")
        for erreur in rapport.errors:
            console.print(f"  [yellow]![/yellow] {erreur}")
    if rapport.errors:
        raise typer.Exit(1)
