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
import subprocess
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated

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


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "project"


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
    disp = name or proot.name
    slug = _slug(disp)
    projects = _load_registry()
    if any(p.get("path") == str(proot) for p in projects):
        console.print(f"[yellow]•[/yellow] Already registered: {proot}")
        return
    # Disambiguate slug collisions.
    existing = {p.get("slug") for p in projects}
    base_slug, n = slug, 2
    while slug in existing:
        slug = f"{base_slug}-{n}"
        n += 1
    projects.append({"name": disp, "path": str(proot), "slug": slug})
    _save_registry(projects)
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

    handler = partial(SimpleHTTPRequestHandler, directory=str(serve_dir))
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


@cockpit_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Default to ``serve`` when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)
