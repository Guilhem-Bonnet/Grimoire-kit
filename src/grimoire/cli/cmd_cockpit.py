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
from urllib.parse import parse_qs, urlparse

import typer
from rich.console import Console
from rich.table import Table

from grimoire.data import site_script, web_path
from grimoire.tools.project_registry import (
    classify_registry,
    crawl_projects,
    load_registry,
    looks_grimoire,
    projects_payload,
    read_state,
    register_project,
    registry_file,
    registry_home,
    save_registry,
    selected_slug,
    set_selected_slug,
    state_file,
    write_state,
)

cockpit_app = typer.Typer(
    help="Local multi-project governance cockpit (serves the bundled site).",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console(stderr=True)


# ── Paths ───────────────────────────────────────────────────────────────────
# Le registre, ses chemins et la découverte vivent dans
# ``grimoire.tools.project_registry`` : le cockpit et l'atelier lisent et
# écrivent le même fichier, et deux copies de cette logique finiraient par
# diverger. Seul le dossier de service reste propre au cockpit.

def _serve_dir() -> Path:
    return registry_home() / "serve"


# ── Daemon helpers (background start/stop) ────────────────────────────────────

def _read_state() -> dict[str, Any] | None:
    """État du démon, ``None`` quand il n'y en a pas — les appelants testent la
    présence d'un cockpit démarré, pas le contenu d'un dictionnaire vide."""
    return read_state() or None


def _clear_state() -> None:
    state_file().unlink(missing_ok=True)


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
# Délégué à ``grimoire.tools.project_registry`` (voir les imports en tête).

def _resolve_project_path(slug: str | None) -> Path | None:
    """Map a registry slug to its absolute project path (first project if slug is empty)."""
    projects = load_registry()
    if slug:
        for p in projects:
            if p.get("slug") == slug:
                return Path(p["path"])
        return None
    return Path(projects[0]["path"]) if projects else None


_API_CACHE: dict[Path, Any] = {}


def _project_api(proot: Path) -> Any:
    """Instance ``ForgeAPI`` du projet, mémoïsée par racine.

    ``ForgeAPI`` ne fait que résoudre des chemins à la construction : le cache
    évite N constructions par page sans jamais figer de l'état métier.
    """
    cached = _API_CACHE.get(proot)
    if cached is None:
        from grimoire.cli.cmd_blueprint import _kit_root
        from grimoire.tools.forge_server import ForgeAPI

        cached = ForgeAPI(proot, _kit_root(), None)
        _API_CACHE[proot] = cached
    return cached


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

# Longueur d'argument bornée : une requête n'a pas à pousser un roman dans argv.
_MAX_ARGUMENT_LEN = 512


def _is_plain_argument(value: str) -> bool:
    """Vrai si la valeur peut être passée à la CLI comme simple positionnel.

    Le dispatch n'utilise pas de shell, donc il n'y a pas d'injection de shell
    possible — mais une valeur commençant par ``-`` serait lue comme une option
    par la sous-commande. On refuse ce cas, les octets nuls et les longueurs
    déraisonnables, et on passe malgré tout les valeurs après ``--``.
    """
    return bool(value) and not value.startswith("-") and "\x00" not in value and len(value) <= _MAX_ARGUMENT_LEN


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

    # ── Lectures : registre + API projet, repli statique ──────────────────

    def _query_slug(self) -> str:
        """Projet visé par la requête : ``?project=`` explicite, sinon la sélection courante."""
        asked = parse_qs(urlparse(self.path).query).get("project", [""])[0]
        return asked or selected_slug()

    def do_GET(self) -> None:  # http.server contract
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            super().do_GET()
            return
        if self.client_address[0] not in _LOCAL_HOSTS:
            self._send_json(403, {"ok": False, "error": "cockpit local only"})
            return
        if path == "/api/projects":
            self._send_json(200, projects_payload())
            return
        proot = _resolve_project_path(self._query_slug() or None)
        if proot is None or not proot.is_dir():
            self._send_json(404, {"ok": False, "error": "projet inconnu"})
            return
        try:
            from grimoire.tools.forge_routes import API_GET_UNHANDLED, api_get

            payload = api_get(_project_api(proot), path, parse_qs(urlparse(self.path).query))
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if payload is API_GET_UNHANDLED:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        # Même forme de réponse que l'atelier : la charge utile brute, pour que
        # le même code de page fonctionne contre les deux serveurs. Seul
        # ``/api/status`` est enrichi, pour que l'UI sache qu'elle est sur un
        # hôte multi-projets en lecture seule et n'y propose pas de mutation.
        if path == "/api/status" and isinstance(payload, dict):
            payload = {**payload, "host": "cockpit", "readOnly": True, "project": self._query_slug()}
        self._send_json(200, payload)

    def do_POST(self) -> None:  # http.server contract
        if self.client_address[0] not in _LOCAL_HOSTS:
            self._send_json(403, {"ok": False, "error": "cockpit local only"})
            return
        if self.path == "/api/projects/select":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"ok": False, "error": "bad json"})
                return
            slug = str(data.get("slug", "")).strip()
            if not slug:
                # L'UI envoie historiquement un chemin ; on le tolère.
                wanted = str(data.get("path", "")).strip()
                slug = next(
                    (str(x.get("slug", "")) for x in load_registry() if x.get("path") == wanted),
                    "",
                )
            if not set_selected_slug(slug):
                self._send_json(404, {"ok": False, "error": "projet inconnu"})
                return
            self._send_json(200, {"ok": True, "selected": slug})
            return
        if self.path != "/api/memory":
            self._send_json(404, {"ok": False, "error": "not found"})
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
        values: list[str] = []
        if is_read:
            flags = list(_ALLOWED_ACTIONS[action])
            if action == "search":
                query = str(data.get("query", "")).strip()
                if not query:
                    self._send_json(400, {"ok": False, "error": "query requise"})
                    return
                values = [query]
        else:
            spec = _MUTATION_ACTIONS[action]
            subcmd = spec.subcommand or action
            flags = list(spec.args)
            if spec.needs_id:
                entry_id = str(data.get("id", "")).strip()
                if not entry_id:
                    self._send_json(400, {"ok": False, "error": "id d'entrée requis"})
                    return
                values = [entry_id]
        if not all(_is_plain_argument(v) for v in values):
            self._send_json(400, {"ok": False, "error": "valeur d'argument refusée"})
            return
        # Les valeurs venant de la requête passent après ``--`` : la sous-commande
        # les lit comme des positionnels, jamais comme des options.
        cmd = [sys.executable, "-m", "grimoire", "--output", "json", "memory", subcmd, *flags]
        if values:
            cmd += ["--", *values]
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
    mark = "" if looks_grimoire(proot) else "  [yellow](pas de marqueur Grimoire détecté)[/yellow]"
    console.print(f"[green]+[/green] {disp} [dim]({slug})[/dim] → {proot}{mark}")


@cockpit_app.command("remove")
def remove(
    target: Annotated[str, typer.Argument(help="Slug, name, or path to remove.")],
) -> None:
    """Remove a project from the cockpit registry."""
    projects = load_registry()
    kept = [p for p in projects if target not in (p.get("slug"), p.get("name"), p.get("path"))]
    if len(kept) == len(projects):
        console.print(f"[yellow]•[/yellow] No match for: {target}")
        return
    save_registry(kept)
    console.print(f"[green]−[/green] Removed: {target}")


def _forget_selection_if_gone(kept: list[dict[str, str]]) -> None:
    """Oublie la sélection courante si son projet vient d'être retiré.

    Une sélection qui pointe une entrée purgée fait retomber le cockpit sur le
    projet primaire à chaque lecture, sans jamais le dire. Autant l'effacer au
    moment où l'entrée disparaît.
    """
    if selected_slug() not in {str(p.get("slug", "")) for p in kept}:
        state = _read_state() or {}
        state.pop("selected_project", None)
        write_state(state)


_prune_dry_opt = typer.Option(False, "--dry-run", "-n", help="Montrer le plan sans rien retirer.")
_prune_yes_opt = typer.Option(False, "--yes", "-y", help="Ne pas demander confirmation.")
_prune_stale_opt = typer.Option(
    False,
    "--stale",
    help="Retirer aussi les chemins qui existent mais ne portent plus de marqueur Grimoire.",
)


@cockpit_app.command("prune")
def prune(
    ctx: typer.Context,
    dry_run: bool = _prune_dry_opt,
    yes: bool = _prune_yes_opt,
    stale: bool = _prune_stale_opt,
) -> None:
    """Retirer du registre les projets dont le chemin a disparu.

    Le registre accumule des entrées mortes à chaque projet supprimé ou
    déplacé — et, avant le correctif d'isolation des tests, à chaque campagne
    de tests lancée sans garde-fou.

    [dim]Examples:[/dim]
      [cyan]grimoire cockpit prune --dry-run[/cyan]  Voir ce qui partirait
      [cyan]grimoire cockpit prune -y[/cyan]         Purger sans confirmation
      [cyan]grimoire cockpit prune --stale[/cyan]    Inclure les chemins sans marqueur
    """
    projects = load_registry()
    keep, drop = classify_registry(projects, stale=stale)
    fmt = str((ctx.obj or {}).get("output", "text"))

    if fmt == "json":
        payload = {
            "total": len(projects),
            "kept": len(keep),
            "removed": 0 if dry_run else len(drop),
            "candidates": [{"name": e.get("name", ""), "path": e.get("path", "")} for e in drop],
            "dryRun": dry_run,
        }
        if drop and not dry_run:
            save_registry(keep)
            _forget_selection_if_gone(keep)
        typer.echo(json.dumps(payload, indent=2))
        return

    if not drop:
        console.print(f"[green]Registre propre[/green] — {len(projects)} entrée(s), aucune morte.")
        return

    console.print(f"[bold]{len(drop)}[/bold] entrée(s) à retirer sur {len(projects)} :")
    for entry in drop[:10]:
        console.print(f"  [dim]−[/dim] {entry.get('name', '?')} → {entry.get('path', '?')}")
    if len(drop) > 10:
        console.print(f"  [dim]… et {len(drop) - 10} autre(s)[/dim]")

    if dry_run:
        console.print("\n[dim]--dry-run : rien n'a été retiré.[/dim]")
        return
    if not yes and not typer.confirm(f"\nRetirer ces {len(drop)} entrée(s) ?"):
        console.print("[yellow]Annulé.[/yellow]")
        return

    save_registry(keep)

    _forget_selection_if_gone(keep)
    console.print(f"[green]−[/green] {len(drop)} entrée(s) retirée(s), {len(keep)} conservée(s).")


@cockpit_app.command("list")
def list_projects() -> None:
    """List the projects governed by the cockpit."""
    projects = load_registry()
    if not projects:
        console.print("[dim]Aucun projet enregistré. Ajoute-en un : [b]grimoire cockpit add <path>[/b][/dim]")
        return
    table = Table(title="Cockpit — projets gouvernés", title_style="bold")
    table.add_column("Slug", style="cyan")
    table.add_column("Nom")
    table.add_column("Chemin", style="dim")
    table.add_column("", justify="center")
    for p in projects:
        ok = "[green]●[/green]" if looks_grimoire(Path(p.get("path", ""))) else "[yellow]○[/yellow]"
        table.add_row(p.get("slug", ""), p.get("name", ""), p.get("path", ""), ok)
    console.print(table)


# Couches ``data/`` communes au kit — identiques pour tout le monde, sans
# aucune donnée de projet : elles viennent du site embarqué.
_KIT_DATA_LAYERS = (
    "catalogue-export.json",
    "extensions.json",
    "architecture.json",
    "kit-coverage.json",
)


def _sync_site(serve_dir: Path) -> None:
    """Copy the bundled static site into the serve dir.

    The ``data/`` dir is owned by :func:`_generate_data` and must never be
    clobbered by a sync, so it is excluded here — except the kit reference
    layers, which carry no project data and are the same everywhere.

    The bundled ``data/`` also holds the public vitrine snapshot: invented
    projects and another repository's metrics. Seeding it for an empty registry
    used to make the cockpit look populated with somebody else's numbers, so it
    is never copied. An empty registry shows an empty cockpit.
    """
    src = web_path()
    serve_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, serve_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data"))
    data_dir = serve_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for layer in _KIT_DATA_LAYERS:
        bundled = src / "data" / layer
        if bundled.is_file():
            shutil.copy2(bundled, data_dir / layer)


def _generate_data(serve_dir: Path, with_tests: bool) -> bool:
    """Regenerate the data layer from the registry. Returns True if projects were generated."""
    projects = load_registry()
    if not projects:
        return False
    gen = site_script("gen-site-data.py")
    cmd = [sys.executable, str(gen), "--registry", str(registry_file()), "--out-dir", str(serve_dir / "data")]
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
        console.print("[dim]Aucun projet enregistré — rien à générer. "
                      "[b]grimoire cockpit scan <dossier>[/b][/dim]")


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
            console.print("[dim]Registre vide → cockpit vide (aucune donnée inventée).[/dim]")
            console.print("[dim]Ajoute des projets : [b]grimoire cockpit add <path>[/b] "
                          "ou [b]grimoire cockpit scan <dossier>[/b][/dim]")

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
        console.print("[dim]Registre vide → cockpit vide. "
                      "[b]grimoire cockpit add <path>[/b] ou [b]scan <dossier>[/b][/dim]")

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

    write_state({"pid": pid, "port": port, "url": url})
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


# ── Project discovery (cockpit scan) ─────────────────────────────────────────
# Le parcours est dans ``project_registry`` : ``grimoire serve`` expose la même
# découverte depuis l'atelier (POST /api/projects/scan).

@cockpit_app.command("scan")
def scan(
    root: Annotated[Path, typer.Argument(help="Root directory to crawl for local projects.")],
    depth: Annotated[int, typer.Option("--depth", help="Maximum crawl depth.")] = 4,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Enrol all grimoire-managed projects without prompting.")] = False,
    include_uninitialized: Annotated[bool, typer.Option(
        "--include-uninitialized",
        help="Also enrol bare git repos that are not grimoire-initialized.",
    )] = False,
) -> None:
    """Discover projects under a root directory and enrol them in the cockpit."""
    base = root.expanduser().resolve()
    if not base.is_dir():
        console.print(f"[red]x[/red] Not a directory: {base}")
        raise typer.Exit(1)

    candidates = crawl_projects(base, depth)
    if not candidates:
        console.print(f"[dim]No candidate project found under {base} (depth {depth}).[/dim]")
        return

    registered_paths = {p.get("path") for p in load_registry()}

    def _rel(p: Path) -> str:
        return "." if p == base else str(p.relative_to(base))

    table = Table(title=f"Cockpit scan — {base}", title_style="bold")
    table.add_column("Path", style="cyan")
    table.add_column("Type")
    table.add_column("Registered", justify="center")
    for cand in candidates:
        kind = "grimoire-managed" if cand.managed else "git repo (uninitialized)"
        reg = "yes" if str(cand.path) in registered_paths else "no"
        table.add_row(_rel(cand.path), kind, reg)
    console.print(table)

    to_enrol = [
        c for c in candidates
        if (c.managed or include_uninitialized) and str(c.path) not in registered_paths
    ]
    uninitialized = [c for c in candidates if not c.managed]

    if to_enrol:
        if yes or typer.confirm(f"Enrol {len(to_enrol)} project(s) in the cockpit?"):
            for cand in to_enrol:
                slug = register_project(cand.path)
                if slug is not None:
                    console.print(f"[green]+[/green] {cand.path.name} [dim]({slug})[/dim] -> {cand.path}")
        else:
            console.print("[dim]Nothing enrolled.[/dim]")
    else:
        console.print("[dim]Nothing new to enrol.[/dim]")

    if uninitialized and not include_uninitialized:
        console.print("[dim]Git repos not initialized for Grimoire (skipped):[/dim]")
        for cand in uninitialized:
            console.print(f"  [yellow]o[/yellow] {_rel(cand.path)}  [dim]hint: grimoire up {cand.path}[/dim]")


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
