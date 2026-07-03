"""Grimoire CLI — ``grimoire self``, ``grimoire completion``, ``grimoire plugins``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from grimoire.__version__ import __version__
from grimoire.cli._cli_helpers import _get_fmt, _log_operation

console = Console(stderr=True)

# ── Connectivity check ────────────────────────────────────────────────────────

_online_cache: bool | None = None


def _is_online(*, timeout: float = 1.0) -> bool:
    """Quick connectivity test — returns *False* if unreachable.

    Uses DNS resolution (works behind corporate proxies) and falls back to a
    direct socket connection. Respects ``GRIMOIRE_OFFLINE=1`` env override.
    Result is cached for the process lifetime.
    """
    if os.environ.get("GRIMOIRE_OFFLINE", "").lower() in ("1", "true"):
        return False
    import socket

    try:
        socket.getaddrinfo("dns.google", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return True
    except OSError:
        pass
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=timeout).close()
    except OSError:
        return False
    return True


def is_online() -> bool:
    """Cached online check — probes network at most once per process."""
    global _online_cache
    if _online_cache is None:
        _online_cache = _is_online(timeout=0.5)
    return _online_cache


# ── grimoire self ─────────────────────────────────────────────────────────────

self_app = typer.Typer(help="Grimoire self-management utilities.")


@self_app.command("version")
def self_version(ctx: typer.Context) -> None:
    """Show installed grimoire-kit version and check for updates on PyPI."""
    fmt = _get_fmt(ctx)
    installed = __version__

    install_path = Path(__file__).resolve().parent.parent
    editable = (install_path / "__pycache__").parent.parent.name != "site-packages"

    latest: str | None = None
    if is_online():
        try:
            from urllib.request import urlopen

            url = "https://pypi.org/pypi/grimoire-kit/json"
            with urlopen(url, timeout=5) as resp:  # noqa: S310
                pypi_data = json.loads(resp.read())
                latest = pypi_data.get("info", {}).get("version")
        except Exception:
            latest = None

    update_available = bool(latest and latest != installed)

    if fmt == "json":
        data: dict[str, Any] = {
            "installed": installed,
            "latest": latest,
            "update_available": update_available,
            "editable_install": editable,
            "install_path": str(install_path),
        }
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"[bold]grimoire-kit[/bold]  {installed}")
    if editable:
        console.print("  [dim]Editable install (development mode)[/dim]")
    if latest and update_available:
        console.print(f"  [yellow]Update available:[/yellow] {latest}")
        console.print("  [dim]Run: grimoire self update[/dim]")
    elif latest:
        console.print("  [green]Up to date[/green]")
    else:
        console.print("  [dim]Could not check for updates[/dim]")


@self_app.command("update")
def self_update() -> None:
    """Update grimoire-kit to the latest version from PyPI."""
    import shutil
    import subprocess

    installed = __version__
    console.print(f"[bold]grimoire-kit[/bold]  {installed}\n")

    if not is_online():
        console.print("[red]No internet connection — cannot check for updates.[/red]")
        raise typer.Exit(1)

    latest: str | None = None
    try:
        from urllib.request import urlopen

        url = "https://pypi.org/pypi/grimoire-kit/json"
        with urlopen(url, timeout=5) as resp:  # noqa: S310
            pypi_data = json.loads(resp.read())
            latest = pypi_data.get("info", {}).get("version")
    except Exception:
        console.print("[red]Could not reach PyPI.[/red]")
        raise typer.Exit(1) from None

    if latest == installed:
        console.print(f"  [green]Already up to date ({installed})[/green]")
        raise typer.Exit(0)

    console.print(f"  [yellow]Updating:[/yellow] {installed} → {latest}\n")

    use_pipx = False
    pipx_bin = shutil.which("pipx")
    if pipx_bin:
        try:
            result = subprocess.run(
                [pipx_bin, "list", "--short"],
                capture_output=True, text=True, timeout=10,
            )
            if "grimoire-kit" in result.stdout:
                use_pipx = True
        except Exception:
            pass  # pipx detection is best-effort

    if use_pipx:
        cmd = [pipx_bin, "upgrade", "grimoire-kit"]
        console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "grimoire-kit"]
        console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")

    result = subprocess.run(cmd, timeout=120)
    if result.returncode != 0:
        console.print("\n[red]Update failed.[/red] Try manually:")
        hint = "pipx upgrade grimoire-kit" if use_pipx else "pip install --upgrade grimoire-kit"
        console.print(f"  [dim]{hint}[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green]✓ Updated to {latest}[/green]")
    console.print("[dim]Run 'grimoire self version' to verify.[/dim]")


@self_app.command("diagnose")
def self_diagnose(ctx: typer.Context) -> None:
    """Run a self-diagnostic on the grimoire-kit installation."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    fmt = _get_fmt(ctx)

    checks: list[dict[str, Any]] = []

    required_deps = ("ruamel.yaml", "typer", "rich")
    optional_deps = ("mcp",)

    for dep in required_deps:
        try:
            ver = pkg_version(dep)
            checks.append({"name": dep, "status": "ok", "version": ver, "required": True})
        except PackageNotFoundError:
            checks.append({"name": dep, "status": "missing", "version": None, "required": True})

    for dep in optional_deps:
        try:
            ver = pkg_version(dep)
            checks.append({"name": dep, "status": "ok", "version": ver, "required": False})
        except PackageNotFoundError:
            checks.append({"name": dep, "status": "missing", "version": None, "required": False})

    py_ver = sys.version_info
    py_ok = py_ver >= (3, 12)
    checks.append({
        "name": "python",
        "status": "ok" if py_ok else "warn",
        "version": f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        "required": True,
        "detail": None if py_ok else "Python 3.12+ recommended",
    })

    import shutil
    grimoire_bin = shutil.which("grimoire")
    checks.append({
        "name": "grimoire-cli",
        "status": "ok" if grimoire_bin else "warn",
        "version": None,
        "required": True,
        "detail": grimoire_bin or "Not found in PATH",
    })

    all_ok = all(c["status"] == "ok" for c in checks if c["required"])

    if fmt == "json":
        typer.echo(json.dumps({"all_ok": all_ok, "checks": checks}, indent=2))
        return

    console.print("[bold]grimoire self diagnose[/bold]\n")
    for c in checks:
        icon = "[green]✓[/green]" if c["status"] == "ok" else (
            "[yellow]![/yellow]" if c["status"] == "warn" else "[red]✗[/red]"
        )
        ver = f" {c['version']}" if c.get("version") else ""
        opt = " [dim](optional)[/dim]" if not c["required"] else ""
        console.print(f"  {icon} {c['name']}{ver}{opt}")
        if c.get("detail"):
            console.print(f"    [dim]{c['detail']}[/dim]")

    console.print()
    if all_ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print("[bold yellow]Some issues found — see above.[/bold yellow]")


# ── grimoire completion ───────────────────────────────────────────────────────

completion_app = typer.Typer(help="Shell completion utilities.")

_SUPPORTED_SHELLS = frozenset({"bash", "zsh", "fish"})


def _generate_completion_script(shell: str) -> str:
    """Generate a shell completion script via subprocess — raise typer.Exit(1) on failure."""
    import subprocess

    if shell not in _SUPPORTED_SHELLS:
        console.print(f"[red]Unsupported shell:[/red] {shell} (supported: {', '.join(sorted(_SUPPORTED_SHELLS))})")
        raise typer.Exit(1)

    env = {**os.environ, "_GRIMOIRE_COMPLETE": f"{shell}_source"}
    result = subprocess.run(
        [sys.executable, "-m", "grimoire"],
        capture_output=True, text=True, env=env, timeout=10,
    )

    if result.returncode != 0 or not result.stdout.strip():
        console.print("[yellow]Completion script could not be generated.[/yellow]")
        raise typer.Exit(1)

    return result.stdout.strip()


@completion_app.command("install")
def completion_install(
    shell: str = typer.Option(
        ...,
        "--shell", "-s",
        help="Shell to install completion for: bash, zsh, fish.",
    ),
) -> None:
    """Install shell auto-completion for grimoire CLI."""
    script = _generate_completion_script(shell)

    shell_rc = {"bash": "~/.bashrc", "zsh": "~/.zshrc", "fish": "~/.config/fish/completions/grimoire.fish"}
    target = Path(shell_rc[shell]).expanduser()

    if shell == "fish":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script + "\n", encoding="utf-8")
        console.print(f"[green]✓[/green] Completion installed to {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        marker = "# grimoire shell completion"
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        if marker in existing:
            console.print(f"[dim]Completion already installed in {target}[/dim]")
            return
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{marker}\n{script}\n")
        console.print(f"[green]✓[/green] Completion appended to {target}")
        console.print(f"[dim]Reload with: source {target}[/dim]")

    _log_operation("completion_install", {"shell": shell})


@completion_app.command("export")
def completion_export(
    shell: str = typer.Option(
        ...,
        "--shell", "-s",
        help="Shell to export completion for: bash, zsh, fish.",
    ),
) -> None:
    """Export shell completion script to stdout (for piping/redirection)."""
    typer.echo(_generate_completion_script(shell))


# ── grimoire plugins ──────────────────────────────────────────────────────────

plugins_app = typer.Typer(help="Discover installed plugins.")


@plugins_app.command("list")
def plugins_list(ctx: typer.Context) -> None:
    """List discovered tools and backend plugins."""
    from grimoire.registry.discovery import discover_backends, discover_tools

    fmt = _get_fmt(ctx)
    tools = discover_tools()
    backends = discover_backends()

    if fmt == "json":
        data: dict[str, Any] = {
            "tools": sorted(tools),
            "backends": sorted(backends),
        }
        typer.echo(json.dumps(data, indent=2))
        return

    if tools:
        console.print("\n[bold]Tools[/bold]")
        for tid in sorted(tools):
            console.print(f"  [green]•[/green] {tid}")

    if backends:
        console.print("\n[bold]Backends[/bold]")
        for bid in sorted(backends):
            console.print(f"  [green]•[/green] {bid}")

    if not tools and not backends:
        console.print("[dim]No plugins discovered.[/dim]")

    console.print()
