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
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
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


def _detect_manager() -> tuple[str, str | None]:
    """Gestionnaire qui a installé grimoire-kit : ``("uv" | "pipx" | "pip", binaire)``."""
    import shutil
    import subprocess

    def _lists_grimoire(tool_bin: str, sub: list[str]) -> bool:
        try:
            result = subprocess.run(
                [tool_bin, *sub],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
        except Exception:  # detection is best-effort; failure means "not here"
            return False
        return "grimoire-kit" in result.stdout

    uv_bin = shutil.which("uv")
    if uv_bin and _lists_grimoire(uv_bin, ["tool", "list"]):
        return "uv", uv_bin
    pipx_bin = shutil.which("pipx")
    if pipx_bin and _lists_grimoire(pipx_bin, ["list", "--short"]):
        return "pipx", pipx_bin
    return "pip", None


def detect_update_cmd() -> tuple[list[str], str]:
    """Upgrade command matching how grimoire-kit was installed.

    Order matters: ``uv tool`` and ``pipx`` manage isolated environments that
    usually lack ``pip``, so a bare ``python -m pip install --upgrade`` fails —
    or silently upgrades the wrong environment. Detect those managers first,
    and only fall back to pip for a plain ``pip install`` layout. Returns
    ``(argv, manual_hint)``. Sert au conseil manuel ; l'installation elle-même
    passe par :func:`verified_install_cmd`.
    """
    kind, binary = _detect_manager()
    if kind == "uv" and binary:
        return [binary, "tool", "upgrade", "grimoire-kit"], "uv tool upgrade grimoire-kit"
    if kind == "pipx" and binary:
        return [binary, "upgrade", "grimoire-kit"], "pipx upgrade grimoire-kit"
    return (
        [sys.executable, "-m", "pip", "install", "--upgrade", "grimoire-kit"],
        "pip install --upgrade grimoire-kit",
    )


def verified_install_cmd(
    wheel: Path,
    extras: list[str],
    *,
    manager: tuple[str, str | None] | None = None,
    carry: Sequence[str] = (),
) -> list[str]:
    """Installe le fichier déjà vérifié, jamais « la dernière version » : rien n'est retéléchargé.

    Les dépendances, elles, viennent toujours de l'index (leurs empreintes ne
    sont pas publiées par le kit). *carry* : options ``uv tool install`` qui
    rejouent l'installation d'origine (``--with``, ``--python``) ; sans elles
    ``--force`` repart de zéro et les paquets ajoutés par l'utilisateur sont perdus.
    """
    name = f"grimoire-kit[{','.join(extras)}]" if extras else "grimoire-kit"
    spec = f"{name} @ {wheel.resolve().as_uri()}"
    kind, binary = manager or _detect_manager()
    if kind == "uv" and binary:
        return [binary, "tool", "install", "--force", *carry, spec]
    if kind == "pipx" and binary:
        return [binary, "install", "--force", spec]
    return [sys.executable, "-m", "pip", "install", "--upgrade", spec]


def _query_dir(argv: list[str]) -> Path | None:
    """Dossier que le gestionnaire annonce (``uv tool dir``, ``pipx environment --value ...``)."""
    import subprocess

    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception:  # best-effort : sans dossier, on n'a ni options à rejouer ni reçu à corriger
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str) or not result.stdout.strip():
        return None
    found = Path(result.stdout.strip().splitlines()[0])
    return found if found.is_dir() else None


def _receipt_file(manager: tuple[str, str | None]) -> Path | None:
    """Fichier où ``uv`` / ``pipx`` consignent la façon dont grimoire-kit a été installé."""
    from grimoire.tools import install_receipt as receipt

    kind, binary = manager
    if kind == "uv" and binary:
        tools = _query_dir([binary, "tool", "dir"])
        return receipt.uv_receipt_path(tools) if tools else None
    if kind == "pipx" and binary:
        venvs = _query_dir([binary, "environment", "--value", "PIPX_LOCAL_VENVS"])
        return receipt.pipx_metadata_path(venvs) if venvs else None
    return None


def registry_install_cmd(manager: tuple[str, str | None], extras: list[str], carry: Sequence[str] = ()) -> str:
    """Commande qui refait une installation du registre (reçu sans chemin de fichier)."""
    name = f"grimoire-kit[{','.join(extras)}]" if extras else "grimoire-kit"
    kind, _ = manager
    if kind == "uv":
        return " ".join(["uv", "tool", "install", "--force", *(shlex.quote(c) for c in carry), shlex.quote(name)])
    if kind == "pipx":
        return f"pipx install --force {shlex.quote(name)}"
    return f"pip install --upgrade {shlex.quote(name)}"


def run_update(installed: str, *, online: bool, console: Any) -> None:
    """Update grimoire-kit to the latest version from PyPI.

    *console* is the caller's Rich console; *online* is the connectivity check
    result (injected so the CLI layer owns the probe and tests can patch it).

    La wheel est téléchargée, vérifiée (empreinte PyPI et ``SHA256SUMS`` de la
    release), puis installée depuis ce fichier : si une vérification échoue,
    rien n'est installé.
    """
    import subprocess
    import tempfile
    import urllib.request

    from rich.markup import escape

    from grimoire.tools import install_receipt as receipt
    from grimoire.tools import install_trust as trust

    console.print(f"[bold]grimoire-kit[/bold]  {installed}\n")

    if not online:
        console.print("[red]No internet connection — cannot check for updates.[/red]")
        raise typer.Exit(1)

    latest: str | None = None
    try:
        url = "https://pypi.org/pypi/grimoire-kit/json"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
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

    with tempfile.TemporaryDirectory(prefix="grimoire-update-") as scratch:
        try:
            ref = trust.select_wheel(pypi_data, latest)
            wheel = trust.download_wheel(ref, Path(scratch), urllib.request.urlopen)
            sums = trust.fetch_sums(latest, urllib.request.urlopen)
            verdict = trust.check_wheel(wheel, version=latest, pypi_sha256=ref.sha256, sums=sums)
        except trust.TrustError as exc:
            console.print(f"[red]Update refused — {escape(str(exc))}[/red]")
            console.print("  [dim]Nothing was installed.[/dim]")
            raise typer.Exit(1) from None

        trust_style = "green" if verdict.level == "verified" else "yellow"
        console.print(f"  [{trust_style}]trust_level: {verdict.level}[/{trust_style}] — {escape(verdict.detail)}")

        extras = trust.installed_extras()
        manager = _detect_manager()
        receipt_file = _receipt_file(manager)
        carry: list[str] = []
        if manager[0] == "uv" and receipt_file is not None:
            try:
                carry = receipt.uv_carried_options(receipt_file)
            except receipt.UnsupportedReceiptError as exc:
                _, manual_hint = detect_update_cmd()
                console.print(f"[red]Update refused — {escape(str(exc))}[/red]")
                console.print("  [dim]Nothing was installed. Without the file check, you can still run:[/dim]")
                console.print(f"  [dim]{escape(manual_hint)}[/dim]")
                raise typer.Exit(1) from None

        cmd = verified_install_cmd(wheel, extras, manager=manager, carry=carry)
        console.print(f"  [dim]$ {escape(' '.join(cmd))}[/dim]")

        update_result = subprocess.run(cmd, timeout=300)
        if update_result.returncode == 0 and receipt_file is not None:
            # L'outil retient le chemin de la wheel du dossier temporaire, supprimé juste après.
            pin = receipt.pin_uv_receipt if manager[0] == "uv" else receipt.pin_pipx_metadata
            reason = pin(receipt_file, wheel)
            if reason is not None:
                console.print(f"\n[yellow]The tool still remembers a temporary file ({escape(reason)}).[/yellow]")
                console.print("  Its own upgrade command would fail; restore a registry install with:")
                console.print(f"  [dim]{escape(registry_install_cmd(manager, extras, carry))}[/dim]")
    if update_result.returncode != 0:
        _, manual_hint = detect_update_cmd()
        console.print("\n[red]Update failed.[/red] Try manually:")
        console.print(f"  [dim]{escape(manual_hint)}[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green][OK] Updated to {latest}[/green]", highlight=False)
    console.print("[dim]Run 'grimoire verify-install --online' to check the installed files.[/dim]")
