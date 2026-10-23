"""CLI ``grimoire self bundle`` : construire le bundle que ``grimoire self update --from`` installe hors ligne (D-15)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

_DEST = typer.Argument(help="Dossier du bundle à créer (absent ou vide).")
_VERSION = typer.Option("--version", help="Version stable de PyPI (défaut : la plus haute stable).")
_RELEASE = typer.Option("--from-release", help="Tag d'une release GitHub (vX.Y.Z ou vX.Y.Z-rc1), pré-versions comprises.")
_WHEEL = typer.Option("--wheel", help="Wheel grimoire-kit locale (build du dépôt) : aucune source à vérifier.")
_DEPS = typer.Option("--with-deps", help="Télécharger aussi les dépendances (wheels seulement) pour que l'installation n'ait besoin de rien.")
_PLATFORM = typer.Option("--platform", help="Étiquette de plateforme cible de pip (ex. manylinux2014_x86_64, win_amd64), répétable ; exige --with-deps.")
_PYVER = typer.Option("--python-version", help="Version de Python cible (ex. 3.12) ; exige --with-deps.")
_IMPL = typer.Option("--implementation", help="Implémentation cible de pip (ex. cp) ; exige --with-deps.")
_ABI = typer.Option("--abi", help="ABI cible de pip (ex. cp312), répétable ; exige --with-deps.")
_INDEX = typer.Option("--index-url", help="Index que pip interroge pour les dépendances (miroir interne).")
_LINKS = typer.Option("--find-links", help="Dossier ou URL de wheels que pip consulte en plus, répétable.")
_PYTHON = typer.Option("--python", help="Interpréteur dont le pip télécharge (utile quand grimoire tourne sans pip, ex. uv tool).")
_SKIP = typer.Option("--skip-attestation", help="Ne pas rejouer gh attestation verify (consigné « skipped » dans PROVENANCE.json).")
_JSON = typer.Option("--json", help="Sortie JSON.")


def self_bundle_command(
    dest: Annotated[Path, _DEST],
    version: Annotated[str | None, _VERSION] = None,
    from_release: Annotated[str | None, _RELEASE] = None,
    wheel: Annotated[Path | None, _WHEEL] = None,
    with_deps: Annotated[bool, _DEPS] = False,
    platform: Annotated[list[str] | None, _PLATFORM] = None,
    python_version: Annotated[str | None, _PYVER] = None,
    implementation: Annotated[str | None, _IMPL] = None,
    abi: Annotated[list[str] | None, _ABI] = None,
    index_url: Annotated[str | None, _INDEX] = None,
    find_links: Annotated[list[str] | None, _LINKS] = None,
    python: Annotated[str | None, _PYTHON] = None,
    skip_attestation: Annotated[bool, _SKIP] = False,
    json_output: Annotated[bool, _JSON] = False,
) -> None:
    """Construire un bundle hors ligne vérifiable (wheel, dépendances, SHA256SUMS, PROVENANCE.json) pour `self update --from`."""
    from rich.markup import escape

    from grimoire.cli import app as cli
    from grimoire.tools.update_bundle_build import BuildError, build_bundle

    try:
        report = build_bundle(
            dest, wheel=wheel, version=version, release=from_release, with_deps=with_deps,
            platforms=platform or (), python_version=python_version, implementation=implementation,
            abis=abi or (), index_url=index_url, find_links=find_links or (), pip_python=python,
            skip_attestation=skip_attestation,
        )
    except BuildError as exc:
        cli.console.print(f"[red]Bundle refused — {escape(str(exc))}[/red]")
        cli.console.print("  [dim]Nothing was written.[/dim]")
        raise typer.Exit(1) from None
    if json_output:
        cli.console.print_json(json.dumps({
            "directory": report.directory.as_posix(), "version": report.version, "source": report.source,
            "build_trust": report.build_trust, "attestation": report.attestation, "wheel": report.wheel,
            "dependencies": list(report.dependencies),
        }))
        return
    cli.console.print(f"[green][OK] Bundle {report.version}[/green] → {escape(str(report.directory))}")
    cli.console.print(f"  wheel: {escape(report.wheel)}  (+ {len(report.dependencies)} dépendance(s))")
    cli.console.print(f"  source: {report.source}  vérification à la construction: {report.build_trust}  attestation: {report.attestation}")
    if report.attestation not in ("verified", "not-applicable"):
        cli.console.print("  [yellow]Attestation non vérifiée : à la rejouer en ligne (gh attestation verify) avant de diffuser.[/yellow]")
    cli.console.print(f"  [dim]Installer : grimoire self update --from {escape(str(report.directory))}[/dim]")
