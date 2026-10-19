"""Le portefeuille de ``grimoire task list --all-projects``.

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

console = Console(stderr=True)




def list_all_projects(
    ctx: typer.Context,
    project_root: Path,
    *,
    status: str | None,
    project: str | None,
    live: bool,
    live_minutes: int,
) -> None:
    """Le portefeuille : tableau lisible, ou la charge utile JSON telle quelle."""
    from rich.table import Table

    from grimoire.missions.liveness import label as liveness_label
    from grimoire.missions.liveness import probe_claude_agents
    from grimoire.tools.workspace_portfolio import portfolio_tasks

    view = portfolio_tasks(
        project_root.resolve(),
        state=status,
        project=project,
        live=live,
        live_minutes=live_minutes,
        probe=probe_claude_agents,
    )
    if str((ctx.obj or {}).get("output", "text")) == "json":
        typer.echo(json.dumps(view, indent=2, ensure_ascii=False))
        return
    table = Table(title="Portefeuille de tâches", show_lines=False)
    for column in ("Projet", "Tâche", "État", "Board", "Priorité", "Session", "Mis à jour"):
        table.add_column(column)
    for card in view["tasks"]:
        session = card.get("session") or {}
        if session:
            state = session.get("liveness") or {}
            session_text = f"{liveness_label(state.get('state', 'inconnu'))} · {session.get('id', '')}"
            if session.get("attention"):
                session_text += f" · attention : {session['attention']['reason']}"
        else:
            session_text = "—"
        unread = card.get("unread_directives") or 0
        table.add_row(
            escape(str(card["project"]["name"])),
            escape(f"{card['id']}  {card.get('title', '')}") + (f"  ({unread} consigne(s) non lue(s))" if unread else ""),
            escape(str(card.get("status", ""))),
            escape(str(card.get("board", ""))),
            escape(str(card.get("priority", "") or "—")),
            escape(session_text),
            escape(str(card.get("updated_at", ""))[:19]),
        )
    if view["tasks"]:
        console.print(table)
    else:
        console.print("[dim]Aucune tâche dans le portefeuille pour ces filtres.[/dim]")
    for row in view["projects"]:
        if row["state"] != "ok":
            console.print(f"[red]✗[/red] {escape(row['slug'])} — {escape(str(row['reason']))}")
    summary = view["summary"]
    console.print(
        f"[dim]{summary['projects']} projet(s), {summary['readable']} lisible(s) · "
        f"{summary['tasks']} tâche(s) · vivacité "
        + ", ".join(f"{n} {name}" for name, n in summary["liveness"].items() if n)
        + f" (journal < {view['live_minutes']} min)[/dim]"
    )
