"""``grimoire mcp`` — le serveur MCP du kit, par session (stdio) ou partagé (HTTP).

Issue #723 : chaque session d'hôte lançait son propre ``grimoire-mcp`` en
stdio (11 processus pour ≈ 540 Mo mesurés). ``grimoire mcp start`` lance un
seul démon streamable-http sur la loopback, sur le modèle de
``grimoire cockpit start`` (processus détaché, fichier d'état sous
``registry_home()``), et ``grimoire mcp register`` ajoute à ``.mcp.json`` une
entrée ``grimoire-http`` séparée — l'entrée stdio ``grimoire`` reste le défaut
et n'est jamais réécrite. Le projet est résolu par requête côté serveur
(``grimoire.mcp.project``) : ce démon n'est lié à aucun ``cwd``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.tools.project_registry import registry_home

mcp_app = typer.Typer(
    help="Serveur MCP du kit : stdio par session (défaut) ou démon HTTP partagé (#723).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console(stderr=True)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7331
HTTP_ENTRY_NAME = "grimoire-http"
STDIO_ENTRY_NAME = "grimoire"
_START_TIMEOUT_S = 8.0


# ── État du démon ───────────────────────────────────────────────────────────
# Même mécanique que les « Daemon helpers » du cockpit, volontairement non
# partagée avec lui : ces six fonctions sont courtes, et importer
# `cmd_cockpit` ici coûterait son arbre de modules à chaque `grimoire mcp`.

def state_file() -> Path:
    return registry_home() / "mcp-server.json"


def read_state() -> dict[str, Any] | None:
    f = state_file()
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data else None


def write_state(state: dict[str, Any]) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clear_state() -> None:
    state_file().unlink(missing_ok=True)


def server_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/mcp"


def _port_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _spawn_detached(cmd: list[str]) -> int:
    env = dict(os.environ)
    env.pop("GRIMOIRE_MCP_TRANSPORT", None)  # `--http` le pose lui-même ; rien d'hérité
    if os.name == "posix":
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    else:  # pragma: no cover - Windows
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, env=env)
    return proc.pid


def _terminate(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _running() -> dict[str, Any] | None:
    """L'état du démon s'il répond encore ; un état orphelin est nettoyé."""
    state = read_state()
    if state and _port_alive(str(state.get("host", DEFAULT_HOST)), int(state.get("port", 0) or 0)):
        return state
    if state:
        clear_state()
    return None


# ── Serveur ─────────────────────────────────────────────────────────────────

def _run_server(http: bool, host: str, port: int) -> None:
    """Lance le serveur dans ce processus (bloquant). Séparé pour être simulable en test."""
    if http:
        # Avant l'import : `server.py` décide à l'import s'il enregistre la
        # surface du `cwd` (jamais en partagé).
        os.environ["GRIMOIRE_MCP_TRANSPORT"] = "http"
    try:
        from grimoire.mcp import server
    except ImportError as exc:
        console.print(f"[red]✗[/red] SDK MCP absent ({exc}) — [b]pip install grimoire-kit[mcp][/b]")
        raise typer.Exit(1) from exc
    if http:
        server.run_http(host, port)
    else:
        server.main([])


@mcp_app.command("serve")
def serve(
    http: Annotated[bool, typer.Option("--http", help="Streamable-http partagé entre sessions, au lieu de stdio.")] = False,
    host: Annotated[str, typer.Option("--host", help="Adresse d'écoute (HTTP).")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", "-p", help="Port d'écoute (HTTP).")] = DEFAULT_PORT,
) -> None:
    """Servir au premier plan : stdio (ce que lance `.mcp.json`) ou `--http` (partagé)."""
    if http:
        console.print(f"[bold green]MCP partagé[/bold green] → {server_url(host, port)}  [dim](Ctrl-C pour arrêter)[/dim]")
    _run_server(http, host, port)


@mcp_app.command("start")
def start(
    host: Annotated[str, typer.Option("--host", help="Adresse d'écoute.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", "-p", help="Port d'écoute.")] = DEFAULT_PORT,
) -> None:
    """Démarrer le serveur HTTP partagé en arrière-plan (un seul processus pour toutes les sessions)."""
    running = _running()
    if running:
        console.print(f"[yellow]•[/yellow] Serveur MCP déjà démarré → {running.get('url')}")
        return
    if _port_alive(host, port):
        console.print(f"[red]✗[/red] Le port {port} est déjà pris par un autre processus — choisis [b]--port[/b].")
        raise typer.Exit(1)
    cmd = [sys.executable, "-m", "grimoire.mcp.server", "--http", "--host", host, "--port", str(port)]
    pid = _spawn_detached(cmd)
    deadline = time.monotonic() + _START_TIMEOUT_S
    while not _port_alive(host, port):
        if time.monotonic() >= deadline:
            _terminate(pid)
            console.print(f"[red]✗[/red] Le serveur MCP n'a pas répondu sur {host}:{port} à temps "
                          "(SDK MCP installé ? [b]grimoire mcp serve --http[/b] montre l'erreur).")
            raise typer.Exit(1)
        time.sleep(0.25)
    url = server_url(host, port)
    write_state({"pid": pid, "host": host, "port": port, "url": url})
    console.print(f"[bold green]Serveur MCP partagé démarré[/bold green] → {url}")
    console.print(f"[dim]Brancher un projet : [b]grimoire mcp register <projet>[/b] "
                  f"(entrée `{HTTP_ENTRY_NAME}` dans .mcp.json) · arrêt : [b]grimoire mcp stop[/b][/dim]")


@mcp_app.command("stop")
def stop() -> None:
    """Arrêter le serveur HTTP partagé."""
    state = read_state()
    if not state:
        console.print("[dim]Aucun serveur MCP partagé en cours.[/dim]")
        return
    pid = int(state.get("pid", 0) or 0)
    killed = _terminate(pid) if pid else False
    clear_state()
    if killed:
        console.print("[green]−[/green] Serveur MCP partagé arrêté.")
    else:
        console.print("[yellow]•[/yellow] Serveur MCP déjà arrêté (état nettoyé).")


@mcp_app.command("status")
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
) -> None:
    """État du démon partagé et compte des serveurs stdio vivants (RSS cumulée)."""
    from grimoire.tools.mcp_processes import STDIO_WARN_THRESHOLD, scan, stdio_servers, total_rss_kb

    running = _running()
    processes = scan()
    payload: dict[str, Any] = {"shared": running, "measured": processes is not None}
    if processes is not None:
        stdio = stdio_servers(processes)
        payload["stdio_count"] = len(stdio)
        payload["stdio_rss_kb"] = total_rss_kb(stdio)
        payload["processes"] = [
            {"pid": p.pid, "rss_kb": p.rss_kb, "shared": p.shared, "argv": list(p.argv)} for p in processes
        ]
    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if running:
        console.print(f"[green]●[/green] Serveur MCP partagé en cours → {running.get('url')} (pid {running.get('pid')})")
    else:
        console.print("[dim]○ Pas de serveur MCP partagé — démarre-le : [b]grimoire mcp start[/b][/dim]")
    if processes is None:
        console.print("[dim]Serveurs stdio : non mesuré (pas de /proc sur ce système).[/dim]")
        return
    stdio = stdio_servers(processes)
    mb = total_rss_kb(stdio) / 1024
    tag = "[yellow]WARN[/yellow]" if len(stdio) > STDIO_WARN_THRESHOLD else "[dim]○[/dim]"
    console.print(f"{tag} {len(stdio)} serveur(s) MCP stdio vivant(s), {mb:.0f} Mo de RSS cumulée.")


# ── .mcp.json ───────────────────────────────────────────────────────────────

def http_entry(url: str) -> dict[str, str]:
    return {"type": "http", "url": url}


def register_http_entry(project_root: Path, url: str, *, remove: bool = False) -> tuple[Path, str]:
    """Ajoute (ou retire) l'entrée ``grimoire-http`` de ``.mcp.json`` ; tout le reste est préservé.

    Renvoie le fichier et ce qui s'est passé : ``added``, ``updated``,
    ``unchanged``, ``removed``, ``absent``.
    """
    path = project_root / ".mcp.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            msg = f"{path} n'est pas un JSON valide ({exc}) — corrige-le avant d'y ajouter une entrée"
            raise ValueError(msg) from exc
        if not isinstance(loaded, dict):
            msg = f"{path} doit contenir un objet JSON"
            raise ValueError(msg)
        data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    if remove:
        if HTTP_ENTRY_NAME not in servers:
            return path, "absent"
        del servers[HTTP_ENTRY_NAME]
        outcome = "removed"
    else:
        wanted = http_entry(url)
        current = servers.get(HTTP_ENTRY_NAME)
        if current == wanted:
            return path, "unchanged"
        outcome = "updated" if current is not None else "added"
        servers[HTTP_ENTRY_NAME] = wanted
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, outcome


@mcp_app.command("register")
def register(
    project: Annotated[Path, typer.Argument(help="Racine du projet (défaut : dossier courant).")] = Path(),
    url: Annotated[str | None, typer.Option("--url", help="URL du serveur partagé (défaut : le démon en cours, sinon le port par défaut).")] = None,
    remove: Annotated[bool, typer.Option("--remove", help="Retirer l'entrée au lieu de l'ajouter.")] = False,
) -> None:
    """Déclarer le serveur partagé dans le `.mcp.json` du projet (entrée `grimoire-http`, l'entrée stdio reste)."""
    root = project.expanduser().resolve()
    if not root.is_dir():
        console.print(f"[red]✗[/red] {root} n'est pas un dossier.")
        raise typer.Exit(1)
    running = _running()
    target = url or (str(running.get("url")) if running else server_url(DEFAULT_HOST, DEFAULT_PORT))
    try:
        path, outcome = register_http_entry(root, target, remove=remove)
    except ValueError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if outcome in ("added", "updated"):
        console.print(f"[green]✓[/green] `{HTTP_ENTRY_NAME}` → {target} dans {path} ({outcome}).")
        if not running:
            console.print("[yellow]•[/yellow] Aucun démon ne répond pour l'instant : [b]grimoire mcp start[/b]. "
                          f"L'entrée stdio `{STDIO_ENTRY_NAME}` reste en place et continue de fonctionner.")
        console.print("[dim]Reste à faire côté hôte : désactiver l'entrée stdio `grimoire` dans la session "
                      "qui doit passer par le démon (une session ne devrait charger qu'une des deux).[/dim]")
    elif outcome == "removed":
        console.print(f"[green]−[/green] `{HTTP_ENTRY_NAME}` retirée de {path}.")
    elif outcome == "absent":
        console.print(f"[dim]Aucune entrée `{HTTP_ENTRY_NAME}` dans {path}.[/dim]")
    else:
        console.print(f"[dim]`{HTTP_ENTRY_NAME}` déjà à jour dans {path}.[/dim]")
