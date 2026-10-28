"""``grimoire chat`` — démonstration du pont ACP : un message à l'agent de l'hôte, le flux imprimé.

L'agent de l'hôte tourne en sous-processus (``acp_command`` du profil d'hôte) dans le dossier du
projet ; ses demandes de permission sont posées dans le terminal. Rien n'est accordé sans réponse.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from grimoire.acp import AcpClient, AcpError, PermissionRequest
from grimoire.hosts.capabilities import profile_for, resolve_host

console = Console(stderr=True)

_ALLOW = ("allow_once", "allow_always")


def ask_in_terminal(request: PermissionRequest) -> str | None:
    """Pose la demande au terminal ; une réponse vide, invalide ou un EOF annule."""
    console.print(f"\n[bold yellow]Permission demandée[/bold yellow] (outil {request.tool_call_id})")
    for i, option in enumerate(request.options, 1):
        console.print(f"  {i}. {option['name']} [dim]({option['kind']})[/dim]")
    try:
        raw = input("Choix (numéro, vide = annuler) : ").strip()
    except EOFError:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(request.options):
        return request.options[int(raw) - 1]["option_id"]
    return None


def chat_command(
    message: Annotated[str, typer.Argument(help="Message à envoyer à l'agent.")],
    host: Annotated[str, typer.Option("--host", help="Hôte : claude, gemini, copilot, cursor, codex, opencode.")],
    project_root: Annotated[Path, typer.Option("--project-root", help="Dossier de travail de l'agent.")] = Path(),
    resume: Annotated[str | None, typer.Option("--resume", help="Identifiant d'une session à reprendre (session/load).")] = None,
    agent_command: Annotated[
        str | None, typer.Option("--agent-command", help="Remplace la commande ACP du profil d'hôte.", hidden=True)
    ] = None,
) -> None:
    """Envoie MESSAGE à l'agent ACP de l'hôte et imprime le flux de sa réponse."""
    host_id = resolve_host(host)
    if host_id is None:
        console.print(f"[red]Hôte inconnu :[/red] {host}")
        raise typer.Exit(2)
    profile = profile_for(host_id)
    command = tuple(shlex.split(agent_command)) if agent_command else profile.acp_command
    if not command:
        console.print(f"[red]{profile.display_name} n'a pas de mode ACP connu.[/red]")
        raise typer.Exit(2)
    try:
        with AcpClient(command, project_root) as acp:
            session = acp.load_session(resume) if resume else acp.new_session()
            console.print(f"[dim]session {session} · {profile.display_name} · {' '.join(command)}[/dim]")
            _stream(acp, message)
    except AcpError as exc:
        console.print(f"[red]ACP :[/red] {exc}")
        raise typer.Exit(1) from exc


def _stream(acp: AcpClient, message: str) -> None:
    ended_with_newline = True
    try:
        for event in acp.prompt(message, on_permission=ask_in_terminal):
            kind = event["type"]
            if kind == "update" and event["kind"] == "agent_message_chunk" and event["text"] is not None:
                sys.stdout.write(event["text"])
                sys.stdout.flush()
                ended_with_newline = event["text"].endswith("\n")
            elif kind == "update":
                console.print(f"[dim]· {event['kind']}[/dim]")
            elif kind == "permission_resolved":
                console.print(f"[dim]· permission {event['outcome']}[/dim]")
            elif kind == "turn_ended":
                if not ended_with_newline:
                    sys.stdout.write("\n")
                console.print(f"[dim]fin du tour : {event['stop_reason']}[/dim]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interruption : annulation du tour…[/yellow]")
        acp.cancel()
        acp.drain()
