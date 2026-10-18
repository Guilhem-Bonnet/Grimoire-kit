"""``grimoire task wave-checkpoint`` (W5-05) : le checkpoint humain d'une vague.

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
la logique vit dans :mod:`grimoire.missions.wave_checkpoint`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def register_wave_commands(app: typer.Typer) -> None:
    app.command("wave-checkpoint")(task_wave_checkpoint)


def task_wave_checkpoint(
    ctx: typer.Context,
    mission: Annotated[str, typer.Argument(help="Mission du plan.")],
    wave: Annotated[int, typer.Argument(help="Numéro de vague (voir `task waves`).")],
    action: Annotated[
        str, typer.Option("--action", help="status (défaut), request, require, approve ou reject.")
    ] = "status",
    fingerprint: Annotated[str, typer.Option("--fingerprint", help="Empreinte lue dans `request` (approve/reject).")] = "",
    reason: Annotated[str, typer.Option("--reason", help="Motif de la décision.")] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (dépôt git).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
) -> None:
    """Présente, exige ou décide le checkpoint d'une vague, lié à l'empreinte du diff fusionné."""
    from grimoire.cli.cmd_task import _fmt, _service, console
    from grimoire.missions import wave_checkpoint as wc

    service = _service(project_root, ledger_root)
    ok = True
    if action == "approve" or action == "reject":
        outcome = wc.decide(service, mission, wave, action, fingerprint, reason)
        ok, state, line = outcome.ok, outcome.state, f"{action} : {outcome.reason or outcome.status}"
    elif action == "request":
        state = wc.request(service, mission, wave)
        line = state.status
    elif action == "require":
        state = wc.require(service, mission, wave)
        line = f"requis ({state.status})"
    elif action == "status":
        state = wc.wave_state(service, mission, wave)
        line = state.status
    else:
        console.print(f"[red]✗[/red] --action {action!r} inconnue (status, request, require, approve, reject)")
        raise typer.Exit(2)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"ok": ok, **state.to_dict(), "message": line}, indent=2, ensure_ascii=False))
    else:
        mark = "[green]OK[/green]" if ok else "[red]✗[/red]"
        console.print(f"{mark} vague {wave} de {mission} — {line}")
        if state.fingerprint:
            console.print(f"  empreinte : {state.fingerprint}")
        if state.reason:
            console.print(f"  {state.reason}")
    if not ok:
        raise typer.Exit(1)
