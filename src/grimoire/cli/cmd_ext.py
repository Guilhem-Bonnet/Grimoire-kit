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
    install_blueprint_from_registry,
    install_extension,
    install_from_registry,
    list_installed,
    publish_blueprint,
    publish_extension,
    remove_extension,
    verify_extension,
)

ext_app = typer.Typer(help="Extensions : bundles d'artefacts gouvernés installés dans le projet.")

_PROJECT_ROOT_OPTION = typer.Option(
    Path.cwd(), "--project-root", help="Racine du projet cible.", show_default=False
)
_SOURCE_ARGUMENT = typer.Argument(..., help="Dossier de l'extension, ou id si --registry.")
_PUBLISH_SOURCE_ARGUMENT = typer.Argument(..., help="Dossier de l'extension à publier.")
_EXT_ID_ARGUMENT = typer.Argument(..., metavar="ID")
_REGISTRY_OPTION = typer.Option(None, "--registry", help="Installer depuis ce registry.")
_REGISTRY_REQUIRED_OPTION = typer.Option(..., "--registry", help="Dossier du registry cible.")
_VERSION_OPTION = typer.Option(None, "--version", help="Version précise (registry).")
_SKIP_SCRIPTS_OPTION = typer.Option(False, "--skip-scripts", help="Ignorer les étapes script.")
_FORCE_OPTION = typer.Option(False, "--force", help="Réinstaller par-dessus l'existant.")


def _fail(exc: ExtensionError) -> None:
    typer.secho(f"Erreur : {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@ext_app.command("add")
def ext_add(
    source: str = _SOURCE_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
    registry: Path = _REGISTRY_OPTION,
    version: str = _VERSION_OPTION,
    skip_scripts: bool = _SKIP_SCRIPTS_OPTION,
    force: bool = _FORCE_OPTION,
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
    ext_id: str = _EXT_ID_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
    skip_scripts: bool = _SKIP_SCRIPTS_OPTION,
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
    source: Path = _PUBLISH_SOURCE_ARGUMENT,
    registry: Path = _REGISTRY_REQUIRED_OPTION,
) -> None:
    """Publier une extension ou un blueprint (checksum déterministe)."""
    try:
        if str(source).endswith(".blueprint.json"):
            bp = publish_blueprint(source, registry)
            typer.secho(
                f"Blueprint publié : {bp['summary']['name']} — {bp['file']} "
                f"({bp['checksum'][:19]}…)",
                fg=typer.colors.GREEN,
            )
            return
        release = publish_extension(source, registry)
    except ExtensionError as exc:
        _fail(exc)
        return
    typer.secho(
        f"Publié : {release['summary']['name']} {release['version']} — "
        f"{release['archive']} ({release['checksum'][:19]}…)",
        fg=typer.colors.GREEN,
    )


@ext_app.command("add-blueprint")
def ext_add_blueprint(
    bp_id: str = _EXT_ID_ARGUMENT,
    registry: Path = _REGISTRY_REQUIRED_OPTION,
    project_root: Path = _PROJECT_ROOT_OPTION,
    force: bool = _FORCE_OPTION,
) -> None:
    """Installer un blueprint publié (checksum vérifié, extensions requises rapportées)."""
    try:
        result = install_blueprint_from_registry(
            bp_id, registry, project_root, force=force
        )
    except ExtensionError as exc:
        _fail(exc)
        return
    typer.secho(f"Blueprint installé : {result['installed']} -> {result['path']}", fg=typer.colors.GREEN)
    if result["missingExtensions"]:
        typer.echo("  Extensions requises non installées : " + ", ".join(result["missingExtensions"]))


@ext_app.command("verify")
def ext_verify(
    ext_id: str = _EXT_ID_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Exécuter la vérification post-installation d'une extension."""
    try:
        verify_extension(ext_id, project_root)
    except ExtensionError as exc:
        _fail(exc)
