"""Self-update logic for grimoire-kit — PyPI version resolution + upgrade.

Kept out of ``app.py`` so the command surface stays thin and the install-method
detection can be unit-tested in isolation. The one subtlety worth stating: the
upgrade command must match how grimoire-kit was installed. ``uv tool`` (the
recommended path) and ``pipx`` manage isolated environments that usually lack
``pip``, so a blind ``python -m pip install --upgrade`` fails there — that is
the bug this module exists to prevent.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer


def resolve_latest_version(pypi_data: dict[str, Any]) -> str | None:
    """Highest stable release advertised on PyPI.

    ``info.version`` can lag the CDN by minutes after a publish, which would
    make ``grimoire update`` report "already up to date" (or upgrade to a stale
    version) right after a release. Prefer the max of the ``releases`` keys —
    skipping yanked and pre-releases — and fall back to ``info.version`` when
    parsing is unavailable.
    """
    info_raw = pypi_data.get("info", {})
    info_version = info_raw.get("version") if isinstance(info_raw, dict) else None
    info_version = info_version if isinstance(info_version, str) else None

    releases = pypi_data.get("releases")
    if not isinstance(releases, dict):
        return info_version
    try:
        from packaging.version import InvalidVersion, Version
    except Exception:  # pragma: no cover - packaging ships with our deps
        return info_version

    best: Version | None = None
    for ver, files in releases.items():
        # Skip fully-yanked releases (every artifact yanked) and pre-releases.
        if isinstance(files, list) and files and all(f.get("yanked") for f in files):
            continue
        try:
            parsed = Version(ver)
        except InvalidVersion:
            continue
        if parsed.is_prerelease:
            continue
        if best is None or parsed > best:
            best = parsed
    return str(best) if best is not None else info_version


def is_newer(candidate: str, installed: str) -> bool:
    """True when *candidate* is strictly newer than *installed*.

    Falls back to string inequality when versions can't be parsed, so a dev
    build never trips a spurious downgrade.
    """
    try:
        from packaging.version import Version

        return Version(candidate) > Version(installed)
    except Exception:  # pragma: no cover - defensive, unparseable dev builds
        return candidate != installed


def detect_update_cmd() -> tuple[list[str], str]:
    """Upgrade command matching how grimoire-kit was installed.

    Order matters: ``uv tool`` and ``pipx`` manage isolated environments that
    usually lack ``pip``, so a bare ``python -m pip install --upgrade`` fails —
    or silently upgrades the wrong environment. Detect those managers first,
    and only fall back to pip for a plain ``pip install`` layout. Returns
    ``(argv, manual_hint)``.
    """
    import shutil
    import subprocess

    def _lists_grimoire(tool_bin: str, sub: list[str]) -> bool:
        try:
            result = subprocess.run(
                [tool_bin, *sub],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:  # detection is best-effort; failure means "not here"
            return False
        return "grimoire-kit" in result.stdout

    uv_bin = shutil.which("uv")
    if uv_bin and _lists_grimoire(uv_bin, ["tool", "list"]):
        return [uv_bin, "tool", "upgrade", "grimoire-kit"], "uv tool upgrade grimoire-kit"

    pipx_bin = shutil.which("pipx")
    if pipx_bin and _lists_grimoire(pipx_bin, ["list", "--short"]):
        return [pipx_bin, "upgrade", "grimoire-kit"], "pipx upgrade grimoire-kit"

    return (
        [sys.executable, "-m", "pip", "install", "--upgrade", "grimoire-kit"],
        "pip install --upgrade grimoire-kit",
    )


def run_update(installed: str, *, online: bool, console: Any) -> None:
    """Update grimoire-kit to the latest version from PyPI.

    *console* is the caller's Rich console; *online* is the connectivity check
    result (injected so the CLI layer owns the probe and tests can patch it).
    """
    import subprocess

    console.print(f"[bold]grimoire-kit[/bold]  {installed}\n")

    if not online:
        console.print("[red]No internet connection — cannot check for updates.[/red]")
        raise typer.Exit(1)

    latest: str | None = None
    try:
        from urllib.request import urlopen

        url = "https://pypi.org/pypi/grimoire-kit/json"
        with urlopen(url, timeout=5) as resp:  # noqa: S310
            pypi_data = json.loads(resp.read())
            latest = resolve_latest_version(pypi_data)
    except Exception:
        console.print("[red]Could not reach PyPI.[/red]")
        raise typer.Exit(1) from None

    if latest is None:
        console.print("[red]Could not determine the latest version.[/red]")
        raise typer.Exit(1)

    if not is_newer(latest, installed):
        console.print(f"  [green]Already up to date ({installed})[/green]")
        raise typer.Exit(0)

    console.print(f"  [yellow]Updating:[/yellow] {installed} → {latest}\n")

    cmd, manual_hint = detect_update_cmd()
    console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")

    update_result = subprocess.run(cmd, timeout=120)
    if update_result.returncode != 0:
        console.print("\n[red]Update failed.[/red] Try manually:")
        console.print(f"  [dim]{manual_hint}[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green][OK] Updated to {latest}[/green]", highlight=False)
    console.print("[dim]Run 'grimoire self version' to verify.[/dim]")
