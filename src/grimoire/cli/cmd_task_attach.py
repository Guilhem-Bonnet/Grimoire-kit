"""``grimoire task attach`` : rattache une session d'hôte au claim d'une tâche (K3, #638, #1280 lot C2).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ; :func:`register_attach_command`
accroche la commande au même groupe ``task``. ``--host`` nomme l'hôte d'une session passée par ``--session-id`` :
Copilot n'exporte aucune variable de session documentée, sans cette option la session rattachée n'aurait pas d'hôte
(ni carte de tâche, ni flotte ne pourraient la dire).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def register_attach_command(app: typer.Typer) -> None:
    """Accroche ``attach`` au groupe *app*."""
    app.command("attach")(task_attach)


def task_attach(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    session_id: Annotated[
        str, typer.Option("--session-id", help="Session d'hôte (défaut : GRIMOIRE_SESSION_ID ou CLAUDE_CODE_SESSION_ID).")
    ] = "",
    host_version: Annotated[
        str, typer.Option("--host-version", help="Version de l'hôte (claude --version) : le frein ne se prouve que pour elle.")
    ] = "",
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Hôte de la session (claude, copilot, gemini…). Sans lui, une session nommée par --session-id n'a pas d'hôte.",
        ),
    ] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (pour les gates de preuve).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path(
        "_grimoire-runtime-output/ledger"
    ),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Rattache cette session au claim de la tâche — quand plusieurs claims coexistent."""
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.core.exceptions import GrimoireError
    from grimoire.core.standard_state import current_session
    from grimoire.hosts.capabilities import resolve_host
    from grimoire.hosts.host_version import valid_version

    named_host = resolve_host(host) if host.strip() else None
    if host.strip() and named_host is None:
        console.print(f"[red]✗[/red] Hôte inconnu : {host!r} (claude, copilot, codex, cursor, gemini, opencode, kimi, zed).")
        raise typer.Exit(2)
    env_session, env_host = current_session()
    sid = session_id.strip() or env_session
    if not sid:
        console.print(
            "[red]✗[/red] Aucune session identifiable : ni --session-id, ni GRIMOIRE_SESSION_ID, "
            "ni la variable d'environnement de l'hôte (CLAUDE_CODE_SESSION_ID)."
        )
        raise typer.Exit(1)
    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    try:
        task = service.attach_session(
            task_id,
            sid,
            session_host=named_host.value if named_host is not None else (env_host if not session_id.strip() else ""),
            actor=actor,
            explicit=True,
            host_version=valid_version(host_version),
        )
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"task_id": task.id, "session_id": sid}, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] Session {sid} rattachée à {task.id}")
