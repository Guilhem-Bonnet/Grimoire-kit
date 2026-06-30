"""``grimoire cockpit`` — local multi-project governance dashboard.

The cockpit is the *machine-common* counterpart of the public vitrine: a single
local site that governs every Grimoire project registered on this PC. It bundles
the static ``web/`` site inside the wheel, generates a fresh multi-project data
layer from the registry, and serves it on ``127.0.0.1`` (local only — pilotage
features stay enabled, unlike the public ``*.github.io`` vitrine).

Registry lives at ``~/.grimoire/cockpit/registry.json`` (a JSON list of
``{name, path, slug}`` — the exact format ``gen-site-data.py --registry`` reads).
Override the home dir with ``GRIMOIRE_COCKPIT_HOME``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.data import site_script, web_path

cockpit_app = typer.Typer(
    help="Local multi-project governance cockpit (serves the bundled site).",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console(stderr=True)


# ── Paths ───────────────────────────────────────────────────────────────────

def _home() -> Path:
    env = os.environ.get("GRIMOIRE_COCKPIT_HOME")
    base = Path(env).expanduser() if env else Path.home() / ".grimoire" / "cockpit"
    return base


def _registry_file() -> Path:
    return _home() / "registry.json"


def _serve_dir() -> Path:
    return _home() / "serve"


def _state_file() -> Path:
    return _home() / "cockpit.json"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "project"


# ── Daemon helpers (background start/stop) ────────────────────────────────────

def _read_state() -> dict[str, Any] | None:
    f = _state_file()
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_state(state: dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _clear_state() -> None:
    _state_file().unlink(missing_ok=True)


def _port_alive(port: int) -> bool:
    """True if something accepts connections on 127.0.0.1:<port> (cross-platform)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _spawn_detached(cmd: list[str]) -> int:
    """Launch a fully detached background process and return its PID."""
    if os.name == "posix":
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return proc.pid


def _terminate(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ── Registry I/O ──────────────────────────────────────────────────────────────

def _load_registry() -> list[dict[str, str]]:
    f = _registry_file()
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save_registry(projects: list[dict[str, str]]) -> None:
    f = _registry_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(projects, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _looks_grimoire(p: Path) -> bool:
    return any(
        (p / marker).exists()
        for marker in (".git", "project-context.yaml", "_grimoire", ".github/copilot-instructions.md")
    )


def register_project(path: Path, name: str | None = None) -> str | None:
    """Register a project in the cockpit registry (idempotent).

    Returns the assigned slug if newly added, or ``None`` if the path is not a
    directory or is already registered. Shared by ``cockpit add`` and by
    ``grimoire init`` (auto-enrols a freshly scaffolded project).
    """
    proot = path.expanduser().resolve()
    if not proot.is_dir():
        return None
    projects = _load_registry()
    if any(p.get("path") == str(proot) for p in projects):
        return None
    disp = name or proot.name
    slug = _slug(disp)
    existing = {p.get("slug") for p in projects}
    base_slug, n = slug, 2
    while slug in existing:
        slug = f"{base_slug}-{n}"
        n += 1
    projects.append({"name": disp, "path": str(proot), "slug": slug})
    _save_registry(projects)
    return slug


def _resolve_project_path(slug: str | None) -> Path | None:
    """Map a registry slug to its absolute project path (first project if slug is empty)."""
    projects = _load_registry()
    if slug:
        for p in projects:
            if p.get("slug") == slug:
                return Path(p["path"])
        return None
    return Path(projects[0]["path"]) if projects else None


# ── Local API (cockpit only) ──────────────────────────────────────────────────
# A read-only governance API: dispatches an allowlisted ``grimoire memory``
# subcommand against a registered project. Bound to 127.0.0.1 only — never the
# vitrine. Mutations are intentionally NOT exposed here yet (next increment,
# behind explicit confirmation, still via the Memory OS CLI — never raw SQL).

_ALLOWED_ACTIONS: dict[str, list[str]] = {
    "status": [],
    "gate": ["--soft"],
    "search": [],  # requires a query argument
    "list": [],
    "taxonomy": [],
}


@dataclass(frozen=True)
class _Mutation:
    """A governed write action — runs only with explicit confirmation."""

    args: tuple[str, ...] = ()
    needs_id: bool = False
    subcommand: str | None = None  # defaults to the action name


# Mutations stay deliberately small and well-defined; each maps to a real
# ``grimoire memory`` command and runs only when the request carries
# ``confirm: true`` (the UI gates this behind an explicit confirmation).
_MUTATION_ACTIONS: dict[str, _Mutation] = {
    "gc": _Mutation(),  # consolidate / compact the store
    "delete": _Mutation(args=("--yes",), needs_id=True),  # remove one entry by id
    # resync the Weaviate / Neo4j projections from the source store (reindex, no loss)
    "sync": _Mutation(subcommand="gate", args=("--sync", "--soft")),
}
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class _CockpitHandler(SimpleHTTPRequestHandler):
    """Static file server + a tiny POST ``/api/memory`` governance endpoint."""

    def log_message(self, *args: object) -> None:
        return  # quiet by default

    def _send_json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # http.server contract
        if self.path != "/api/memory":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if self.client_address[0] not in _LOCAL_HOSTS:
            self._send_json(403, {"ok": False, "error": "cockpit local only"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        action = str(data.get("action", ""))
        is_read = action in _ALLOWED_ACTIONS
        is_mutation = action in _MUTATION_ACTIONS
        if not (is_read or is_mutation):
            self._send_json(400, {"ok": False, "error": f"action non autorisée: {action}"})
            return
        if is_mutation and data.get("confirm") is not True:
            self._send_json(403, {"ok": False, "error": "confirmation explicite requise"})
            return
        proot = _resolve_project_path(str(data.get("project", "")) or None)
        if proot is None or not proot.is_dir():
            self._send_json(400, {"ok": False, "error": "projet inconnu"})
            return
        subcmd = action
        if is_read:
            extra = list(_ALLOWED_ACTIONS[action])
            if action == "search":
                query = str(data.get("query", "")).strip()
                if not query:
                    self._send_json(400, {"ok": False, "error": "query requise"})
                    return
                extra = [query, *extra]
        else:
            spec = _MUTATION_ACTIONS[action]
            subcmd = spec.subcommand or action
            extra = list(spec.args)
            if spec.needs_id:
                entry_id = str(data.get("id", "")).strip()
                if not entry_id:
                    self._send_json(400, {"ok": False, "error": "id d'entrée requis"})
                    return
                extra = [entry_id, *extra]
        cmd = [sys.executable, "-m", "grimoire", "--output", "json", "memory", subcmd, *extra]
        try:
            res = subprocess.run(cmd, cwd=str(proot), capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            self._send_json(504, {"ok": False, "error": "timeout"})
            return
        self._send_json(200, {
            "ok": res.returncode == 0, "code": res.returncode,
            "stdout": res.stdout, "stderr": res.stderr, "action": action,
            "mutation": is_mutation,
        })


# ── Commands ──────────────────────────────────────────────────────────────────

@cockpit_app.command("add")
def add(
    path: Annotated[Path, typer.Argument(help="Path to a local Grimoire project.")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="Display name (default: folder name).")] = None,
) -> None:
    """Register a local project in the cockpit."""
    proot = path.expanduser().resolve()
    if not proot.is_dir():
        console.print(f"[red]✗[/red] Not a directory: {proot}")
        raise typer.Exit(1)
    slug = register_project(proot, name)
    if slug is None:
        console.print(f"[yellow]•[/yellow] Already registered: {proot}")
        return
    disp = name or proot.name
    mark = "" if _looks_grimoire(proot) else "  [yellow](pas de marqueur Grimoire détecté)[/yellow]"
    console.print(f"[green]+[/green] {disp} [dim]({slug})[/dim] → {proot}{mark}")


@cockpit_app.command("remove")
def remove(
    target: Annotated[str, typer.Argument(help="Slug, name, or path to remove.")],
) -> None:
    """Remove a project from the cockpit registry."""
    projects = _load_registry()
    kept = [p for p in projects if target not in (p.get("slug"), p.get("name"), p.get("path"))]
    if len(kept) == len(projects):
        console.print(f"[yellow]•[/yellow] No match for: {target}")
        return
    _save_registry(kept)
    console.print(f"[green]−[/green] Removed: {target}")


@cockpit_app.command("list")
def list_projects() -> None:
    """List the projects governed by the cockpit."""
    projects = _load_registry()
    if not projects:
        console.print("[dim]Aucun projet enregistré. Ajoute-en un : [b]grimoire cockpit add <path>[/b][/dim]")
        return
    table = Table(title="Cockpit — projets gouvernés", title_style="bold")
    table.add_column("Slug", style="cyan")
    table.add_column("Nom")
    table.add_column("Chemin", style="dim")
    table.add_column("", justify="center")
    for p in projects:
        ok = "[green]●[/green]" if _looks_grimoire(Path(p.get("path", ""))) else "[yellow]○[/yellow]"
        table.add_row(p.get("slug", ""), p.get("name", ""), p.get("path", ""), ok)
    console.print(table)


def _sync_site(serve_dir: Path) -> None:
    """Copy the bundled static site into the serve dir.

    The ``data/`` dir is owned by :func:`_generate_data` and must never be
    clobbered by a sync, so it is excluded here. On first run (no data yet) the
    bundled demo data is seeded as a working fallback for an empty registry.
    """
    src = web_path()
    serve_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, serve_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data"))
    data_dir = serve_dir / "data"
    if not data_dir.exists() and (src / "data").is_dir():
        shutil.copytree(src / "data", data_dir)


def _generate_data(serve_dir: Path, with_tests: bool) -> bool:
    """Regenerate the data layer from the registry. Returns True if projects were generated."""
    projects = _load_registry()
    if not projects:
        return False
    gen = site_script("gen-site-data.py")
    cmd = [sys.executable, str(gen), "--registry", str(_registry_file()), "--out-dir", str(serve_dir / "data")]
    if with_tests:
        cmd.append("--with-tests")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        console.print("[yellow]⚠[/yellow] Génération partielle (voir détail) — repli sur les données disponibles.")
        if res.stderr.strip():
            console.print(f"[dim]{res.stderr.strip().splitlines()[-1]}[/dim]")
    return True


@cockpit_app.command("refresh")
def refresh(
    with_tests: Annotated[bool, typer.Option("--with-tests", help="Run pytest --collect-only per project (slow).")] = False,
) -> None:
    """Regenerate the cockpit data layer without serving."""
    serve_dir = _serve_dir()
    _sync_site(serve_dir)
    if _generate_data(serve_dir, with_tests):
        console.print(f"[green]✓[/green] Données régénérées → {serve_dir / 'data'}")
    else:
        console.print("[dim]Aucun projet enregistré — données de démo bundlées conservées.[/dim]")


@cockpit_app.command("serve")
def serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Local port.")] = 8420,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the browser.")] = True,
    do_refresh: Annotated[bool, typer.Option("--refresh/--no-refresh", help="Regenerate data before serving.")] = True,
    with_tests: Annotated[bool, typer.Option("--with-tests", help="Run pytest --collect-only per project (slow).")] = False,
) -> None:
    """Serve the cockpit on 127.0.0.1 (local only)."""
    serve_dir = _serve_dir()
    _sync_site(serve_dir)
    if do_refresh:
        if _generate_data(serve_dir, with_tests):
            console.print("[green]✓[/green] Data layer régénéré depuis le registre.")
        else:
            console.print("[dim]Registre vide → site servi avec les données de démo bundlées.[/dim]")
            console.print("[dim]Ajoute des projets : [b]grimoire cockpit add <path>[/b][/dim]")

    handler = partial(_CockpitHandler, directory=str(serve_dir))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        console.print(f"[red]✗[/red] Port {port} indisponible : {exc}")
        raise typer.Exit(1) from exc

    url = f"http://127.0.0.1:{port}/portfolio.html"
    console.print(f"[bold green]Cockpit[/bold green] → [link]{url}[/link]  [dim](Ctrl-C pour arrêter)[/dim]")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Cockpit arrêté.[/dim]")
    finally:
        httpd.server_close()


@cockpit_app.command("start")
def start(
    port: Annotated[int, typer.Option("--port", "-p", help="Local port.")] = 8420,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the browser.")] = True,
    with_tests: Annotated[bool, typer.Option("--with-tests", help="Run pytest --collect-only per project (slow).")] = False,
) -> None:
    """Start the cockpit in the background (non-blocking) and open it."""
    state = _read_state()
    if state and _port_alive(int(state.get("port", 0))):
        console.print(f"[yellow]•[/yellow] Cockpit déjà démarré → [link]{state['url']}[/link]")
        if open_browser:
            webbrowser.open(str(state["url"]))
        return

    serve_dir = _serve_dir()
    _sync_site(serve_dir)
    if _generate_data(serve_dir, with_tests):
        console.print("[green]✓[/green] Data layer régénéré depuis le registre.")
    else:
        console.print("[dim]Registre vide → données de démo bundlées. [b]grimoire cockpit add <path>[/b][/dim]")

    cmd = [sys.executable, "-m", "grimoire", "cockpit", "serve",
           "--port", str(port), "--no-open", "--no-refresh"]
    pid = _spawn_detached(cmd)
    url = f"http://127.0.0.1:{port}/portfolio.html"
    for _ in range(24):
        if _port_alive(port):
            break
        time.sleep(0.25)
    else:
        console.print("[red]✗[/red] Le cockpit n'a pas démarré à temps (port occupé ?).")
        raise typer.Exit(1)

    _write_state({"pid": pid, "port": port, "url": url})
    console.print(f"[bold green]Cockpit démarré[/bold green] → [link]{url}[/link]")
    console.print("[dim]Arrêt : [b]grimoire cockpit stop[/b] · état : [b]grimoire cockpit status[/b][/dim]")
    if open_browser:
        webbrowser.open(url)


@cockpit_app.command("stop")
def stop() -> None:
    """Stop the background cockpit."""
    state = _read_state()
    if not state:
        console.print("[dim]Aucun cockpit en cours.[/dim]")
        return
    pid = int(state.get("pid", 0))
    killed = _terminate(pid) if pid else False
    _clear_state()
    if killed:
        console.print("[green]−[/green] Cockpit arrêté.")
    else:
        console.print("[yellow]•[/yellow] Cockpit déjà arrêté (état nettoyé).")


@cockpit_app.command("status")
def status() -> None:
    """Show whether the cockpit is running."""
    state = _read_state()
    if state and _port_alive(int(state.get("port", 0))):
        console.print(f"[green]●[/green] En cours → [link]{state['url']}[/link]")
        return
    if state:
        _clear_state()
    console.print("[dim]○ Cockpit arrêté — démarre-le : [b]grimoire cockpit start[/b][/dim]")


@cockpit_app.command("open")
def open_browser_cmd() -> None:
    """Open the running cockpit in the browser."""
    state = _read_state()
    if state and _port_alive(int(state.get("port", 0))):
        webbrowser.open(str(state["url"]))
        console.print(f"[green]→[/green] {state['url']}")
        return
    console.print("[yellow]•[/yellow] Cockpit arrêté — lance [b]grimoire cockpit start[/b].")


@cockpit_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Default to ``start`` (background) when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(start)
