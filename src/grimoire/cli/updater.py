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
    """Highest *stable* release advertised on PyPI — never a pre-release.

    ``info.version`` can lag the CDN by minutes after a publish, which would
    make ``grimoire update`` report "already up to date" (or upgrade to a stale
    version) right after a release. Prefer the max of the ``releases`` keys —
    skipping yanked and pre-releases — and fall back to ``info.version`` only
    when it is itself a readable stable version: PyPI reports a pre-release
    there when nothing stable exists, and the ``stable`` channel must not take it.
    Pre-releases are not on PyPI at all (``publish.yml``); the ``preview``
    channel reads them from GitHub (:mod:`grimoire.tools.update_channels`).
    """
    from grimoire.tools.update_channels import stable_only

    info_raw = pypi_data.get("info", {})
    info_version = stable_only(info_raw.get("version") if isinstance(info_raw, dict) else None)

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
        if parsed.is_prerelease or parsed.is_devrelease:
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
    offline_dir: Path | None = None,
) -> list[str]:
    """Installe le fichier déjà vérifié, jamais « la dernière version » : rien n'est retéléchargé.

    Les dépendances, elles, viennent toujours de l'index (leurs empreintes ne
    sont pas publiées par le kit), sauf avec *offline_dir* (bundle hors ligne) : aucun index,
    aucun socket, les dépendances ne viennent que des wheels de ce dossier (ou de ce que
    l'environnement possède déjà), jamais d'une source à compiler. *carry* : options ``uv tool install`` qui
    rejouent l'installation d'origine (``--with``, ``--python``) ; sans elles
    ``--force`` repart de zéro et les paquets ajoutés par l'utilisateur sont perdus.
    """
    name = f"grimoire-kit[{','.join(extras)}]" if extras else "grimoire-kit"
    spec = f"{name} @ {wheel.resolve().as_uri()}"
    kind, binary = manager or _detect_manager()
    where = str(offline_dir) if offline_dir is not None else None
    if kind == "uv" and binary:
        local = ["--offline", "--no-build", "--find-links", where] if where else []
        return [binary, "tool", "install", "--force", *local, *carry, spec]
    if kind == "pipx" and binary:
        pip_args = shlex.join(["--no-index", "--only-binary", ":all:", "--find-links", where]) if where else ""
        return [binary, "install", "--force", *([f"--pip-args={pip_args}"] if where else []), spec]
    local = ["--no-index", "--only-binary", ":all:", "--find-links", where] if where else []
    return [sys.executable, "-m", "pip", "install", "--upgrade", *local, spec]


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




def _report(console: Any, as_json: bool, status: str, **fields: Any) -> None:
    """Résultat de ``--check`` : un statut parmi ``up-to-date``, ``update-available``, ``offline``."""
    if as_json:
        typer.echo(json.dumps({"status": status, **fields}, indent=2, ensure_ascii=False))
        return
    latest, channel = fields.get("latest"), fields.get("channel")
    detail = {
        "up-to-date": f"up to date ({fields.get('installed')}, channel {channel})",
        "update-available": f"update available: {latest} (channel {channel}, via {fields.get('source')})",
        "offline": "no network — nothing was checked (use --from <bundle> to update without one)",
    }[status]
    console.print(f"  status: {status} — {detail}")


def _fetch_json(url: str, opener: Any, timeout: float) -> Any:
    with opener(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_update(
    installed: str,
    *,
    online: bool,
    console: Any,
    channel: str = "stable",
    check: bool = False,
    offline: bool = False,
    source: Path | None = None,
    as_json: bool = False,
) -> None:
    """Update grimoire-kit from PyPI (``stable``), GitHub pre-releases (``preview``) or a local bundle.

    *console* is the caller's Rich console; *online* is the connectivity check
    result (injected so the CLI layer owns the probe and tests can patch it).
    *offline* est une demande : aucune requête réseau n'est faite, *online* est ignoré.
    *check* ne télécharge ni n'installe rien : il rend ``up-to-date``,
    ``update-available`` ou ``offline``. *source* : bundle hors ligne (voir
    :mod:`grimoire.tools.update_bundle`).

    La wheel est téléchargée, vérifiée (empreinte PyPI et ``SHA256SUMS`` de la
    release ; ``SHA256SUMS`` seul pour une pré-version ou un bundle, niveau dit à
    l'écran), puis installée depuis ce fichier : si une vérification échoue,
    rien n'est installé.
    """
    import subprocess
    import tempfile
    import urllib.request

    from rich.markup import escape

    from grimoire.tools import install_receipt as receipt
    from grimoire.tools import install_trust as trust
    from grimoire.tools import update_bundle as bundles
    from grimoire.tools import update_channels as channels

    quiet = check and as_json
    if not quiet:
        console.print(f"[bold]grimoire-kit[/bold]  {installed}\n")

    if channel not in channels.CHANNELS:
        console.print(f"[red]Unknown channel '{escape(channel)}' — expected: {', '.join(channels.CHANNELS)}.[/red]")
        raise typer.Exit(1)

    def refuse(message: str) -> typer.Exit:
        console.print(f"[red]Update refused — {escape(message)}[/red]")
        console.print("  [dim]Nothing was installed.[/dim]")
        return typer.Exit(1)

    pypi_data: dict[str, Any] = {}
    release: channels.ReleaseRef | None = None
    bundle: bundles.Bundle | None = None
    latest: str | None
    origin: str
    if source is not None:
        try:
            bundle = bundles.open_bundle(source)
        except trust.TrustError as exc:
            raise refuse(str(exc)) from None
        latest, origin = str(bundle.version), "bundle"
        if bundle.version.is_prerelease and channel != "preview":
            raise refuse(f"{latest} est une pré-version : ajouter --channel preview pour l'accepter")
    elif offline or not online:
        if check:
            _report(console, as_json, "offline", installed=installed, latest=None, channel=channel, source=None)
            raise typer.Exit(0)
        if offline:
            console.print("[red]--offline: no network is allowed — give a bundle with --from <dir|wheel>.[/red]")
        else:
            console.print("[red]No internet connection — cannot check for updates.[/red]")
            console.print("  [dim]Without a network: grimoire self update --from <bundle dir|wheel>.[/dim]")
        raise typer.Exit(1)
    else:
        try:
            pypi_data = _fetch_json("https://pypi.org/pypi/grimoire-kit/json", urllib.request.urlopen, 5)
        except Exception:
            console.print("[red]Could not reach PyPI.[/red]")
            raise typer.Exit(1) from None
        latest, origin = resolve_latest_version(pypi_data), "pypi"
        if channel == "preview":
            try:
                listing = _fetch_json(channels.releases_url(), urllib.request.urlopen, 10)
            except Exception:
                console.print("[red]Could not reach the GitHub releases (needed by the preview channel).[/red]")
                raise typer.Exit(1) from None
            chosen = channels.choose(latest, channels.select_preview(listing))
            latest, release = (chosen[0], chosen[1]) if chosen else (None, None)
            origin = "github-release" if release is not None else "pypi"

    if latest is None:
        console.print("[red]Could not determine the latest version.[/red]")
        raise typer.Exit(1)

    if not is_newer(latest, installed):
        if check:
            _report(console, as_json, "up-to-date", installed=installed, latest=latest, channel=channel, source=origin)
        else:
            console.print(f"  [green]Already up to date ({installed})[/green]")
        raise typer.Exit(0)

    if check and bundle is None:
        _report(console, as_json, "update-available", installed=installed, latest=latest, channel=channel, source=origin)
        raise typer.Exit(0)

    if not check:
        console.print(f"  [yellow]Updating:[/yellow] {installed} → {latest}\n")

    with tempfile.TemporaryDirectory(prefix="grimoire-update-") as scratch:
        try:
            if bundle is not None:
                wheel, verdict = bundles.stage_and_verify(bundle, Path(scratch))
            elif release is not None:
                wheel = trust.download_wheel(release.wheel, Path(scratch), urllib.request.urlopen)
                sums = trust.fetch_sums(release.version, urllib.request.urlopen, tag=release.tag)
                verdict = trust.check_release_wheel(wheel, version=release.version, sums=sums)
            else:
                ref = trust.select_wheel(pypi_data, latest)
                wheel = trust.download_wheel(ref, Path(scratch), urllib.request.urlopen)
                sums = trust.fetch_sums(latest, urllib.request.urlopen)
                verdict = trust.check_wheel(wheel, version=latest, pypi_sha256=ref.sha256, sums=sums)
        except trust.TrustError as exc:
            raise refuse(str(exc)) from None

        if check:  # bundle : lu et vérifié, rien d'installé
            _report(
                console, as_json, "update-available", installed=installed, latest=latest, channel=channel,
                source=origin, trust_level=verdict.level,
            )
            raise typer.Exit(0)

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

        offline_dir = (bundle.link_dir or Path(scratch)) if bundle is not None else None
        cmd = verified_install_cmd(wheel, extras, manager=manager, carry=carry, offline_dir=offline_dir)
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
        if bundle is not None:
            # Le conseil du registre rouvrirait le réseau : on ne le propose pas pour un bundle.
            console.print("\n[red]Update failed.[/red] The command above ran offline; the bundle may lack a dependency.")
            raise typer.Exit(1)
        _, manual_hint = detect_update_cmd()
        console.print("\n[red]Update failed.[/red] Try manually:")
        console.print(f"  [dim]{escape(manual_hint)}[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green][OK] Updated to {latest}[/green]", highlight=False)
    console.print("[dim]Run 'grimoire verify-install --online' to check the installed files.[/dim]")
