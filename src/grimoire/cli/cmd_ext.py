"""CLI ``grimoire ext`` — gestion des extensions (bundles d'artefacts gouvernés).

Wrapper Typer autour de :mod:`grimoire.tools.ext_manager`. Distinct de
``grimoire plugins`` (découverte d'entry-points Python) : une extension
s'installe dans un projet, pas dans l'environnement Python.
"""

from __future__ import annotations

from pathlib import Path

import typer

from grimoire.tools.ext_manager import (
    ExtensionError,
    install_extension,
    install_from_registry,
    list_installed,
    publish_extension,
    remove_extension,
    verify_extension,
)

ext_app = typer.Typer(help="Extensions : bundles d'artefacts gouvernés installés dans le projet.")

_PROJECT_ROOT_OPTION = typer.Option(
    Path.cwd(), "--project-root", help="Racine du projet cible.", show_default=False
)


def _fail(exc: ExtensionError) -> None:
    typer.secho(f"Erreur : {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@ext_app.command("add")
def ext_add(
    source: str = typer.Argument(..., help="Dossier de l'extension, ou id si --registry."),
    project_root: Path = _PROJECT_ROOT_OPTION,
    registry: Path = typer.Option(None, "--registry", help="Installer depuis ce registry."),
    version: str = typer.Option(None, "--version", help="Version précise (registry)."),
    skip_scripts: bool = typer.Option(False, "--skip-scripts", help="Ignorer les étapes script."),
    force: bool = typer.Option(False, "--force", help="Réinstaller par-dessus l'existant."),
) -> None:
    """Installer une extension depuis un dossier ou un registry (checksum vérifié)."""
    try:
        if registry:
            result = install_from_registry(
                source, registry, project_root,
                version=version, skip_scripts=skip_scripts, force=force,
            )
        else:
            result = install_extension(
                Path(source), project_root, skip_scripts=skip_scripts, force=force
            )
    except ExtensionError as exc:
        _fail(exc)
        return
    typer.secho(f"Installé : {result.extension_id} v{result.version}", fg=typer.colors.GREEN)
    for rel in result.copied:
        typer.echo(f"  copie   : {rel}")
    for item in result.executed:
        typer.echo(f"  exécuté : {item}")
    for item in result.skipped:
        typer.echo(f"  ignoré  : {item}")


@ext_app.command("list")
def ext_list(project_root: Path = _PROJECT_ROOT_OPTION) -> None:
    """Lister les extensions installées."""
    state = list_installed(project_root)
    if not state:
        typer.echo("Aucune extension installée.")
        return
    for ext_id, entry in sorted(state.items()):
        patterns = ", ".join(entry.get("patterns", []))
        typer.echo(f"{ext_id} v{entry['version']} — patterns : {patterns}")


@ext_app.command("remove")
def ext_remove(
    ext_id: str = typer.Argument(..., metavar="ID"),
    project_root: Path = _PROJECT_ROOT_OPTION,
    skip_scripts: bool = typer.Option(False, "--skip-scripts", help="Ignorer les scripts de désinstallation."),
) -> None:
    """Désinstaller une extension."""
    try:
        remove_extension(ext_id, project_root, skip_scripts=skip_scripts)
    except ExtensionError as exc:
        _fail(exc)
        return
    typer.secho(f"Désinstallé : {ext_id}", fg=typer.colors.GREEN)


@ext_app.command("publish")
def ext_publish(
    source: Path = typer.Argument(..., help="Dossier de l'extension à publier."),
    registry: Path = typer.Option(..., "--registry", help="Dossier du registry cible."),
) -> None:
    """Publier une extension dans un registry (archive déterministe + checksum)."""
    try:
        release = publish_extension(source, registry)
    except ExtensionError as exc:
        _fail(exc)
        return
    typer.secho(
        f"Publié : {release['summary']['name']} {release['version']} — "
        f"{release['archive']} ({release['checksum'][:19]}…)",
        fg=typer.colors.GREEN,
    )


@ext_app.command("verify")
def ext_verify(
    ext_id: str = typer.Argument(..., metavar="ID"),
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Exécuter la vérification post-installation d'une extension."""
    try:
        verify_extension(ext_id, project_root)
    except ExtensionError as exc:
        _fail(exc)
