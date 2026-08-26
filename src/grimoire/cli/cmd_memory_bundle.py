"""``grimoire memory bundle`` — carry an embedding model into a closed site.

Lives outside :mod:`grimoire.cli.cmd_memory` (and outside ``app.py``): both are
grandfathered in the code-size ratchet and may not grow. Importing this module
registers the sub-app on the shared ``memory_app``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grimoire.cli.cmd_memory import _get_fmt, console, memory_app
from grimoire.memory.bundle import (
    BundleError,
    default_install_root,
    export_bundle,
    install_bundle,
    verify_bundle,
)

bundle_app = typer.Typer(help="Build, install and verify portable embedding-model bundles.")
memory_app.add_typer(bundle_app, name="bundle")

__all__ = ["bundle_app"]

_export_model_opt = typer.Option(
    "sentence-transformers/all-MiniLM-L6-v2",
    "--model",
    "-m",
    help="Hub repo id, or a local model directory to package as-is.",
)
_export_out_opt = typer.Option(
    Path("grimoire-embedding-bundle.tar.gz"),
    "--out",
    "-o",
    help="Path of the bundle archive to write.",
)
_export_name_opt = typer.Option(
    "",
    "--name",
    help="Model id recorded in the manifest. Defaults to --model.",
)
_install_dest_opt = typer.Option(
    None,
    "--dest",
    help="Install root. Defaults to $GRIMOIRE_EMBEDDING_CACHE or ~/.cache/grimoire/embeddings.",
)
_install_force_opt = typer.Option(False, "--force", help="Replace an existing install of the same model.")
_install_configure_opt = typer.Option(
    False,
    "--configure",
    help="Point memory.embedding_model at the installed directory in project-context.yaml.",
)
_install_archive_arg = typer.Argument(..., help="Bundle archive produced by `bundle export`.")
_verify_path_arg = typer.Argument(..., help="Installed bundle directory (or its model/ subdirectory).")
_verify_embed_opt = typer.Option(
    True,
    "--embed/--no-embed",
    help="Load the model with outbound sockets blocked, to prove the offline path works.",
)


def _fail(exc: BundleError) -> typer.Exit:
    console.print(f"[red]{exc}[/red]")
    return typer.Exit(1)


@bundle_app.command("export")
def bundle_export(
    ctx: typer.Context,
    model: str = _export_model_opt,
    out: Path = _export_out_opt,
    name: str = _export_name_opt,
) -> None:
    """Package an embedding model into a portable archive (connected machine)."""
    try:
        manifest = export_bundle(model, out, model_name=name)
    except BundleError as exc:
        raise _fail(exc) from None

    fmt = _get_fmt(ctx)
    archive = out.expanduser().resolve()
    if fmt == "json":
        payload = manifest.to_dict()
        payload["archive"] = str(archive)
        payload["archive_size"] = archive.stat().st_size
        typer.echo(json.dumps(payload, indent=2))
        return
    console.print(f"[green]Bundle written[/green] {archive}")
    console.print(f"  Model    : {manifest.model}")
    console.print(f"  Dimension: {manifest.dim if manifest.dim is not None else 'unknown'}")
    console.print(f"  Files    : {len(manifest.files)} ({manifest.total_size / 1e6:.1f} MB unpacked)")
    console.print(f"  Archive  : {archive.stat().st_size / 1e6:.1f} MB")
    console.print("\nCarry it over, then: [bold]grimoire memory bundle install <archive> --configure[/bold]")


@bundle_app.command("install")
def bundle_install(
    ctx: typer.Context,
    archive: Path = _install_archive_arg,
    dest: Path | None = _install_dest_opt,
    force: bool = _install_force_opt,
    configure: bool = _install_configure_opt,
) -> None:
    """Verify and install a bundle, optionally wiring it into the project."""
    try:
        installed = install_bundle(archive, dest_root=dest, force=force)
    except BundleError as exc:
        raise _fail(exc) from None

    configured = ""
    if configure:
        from grimoire.cli.cmd_memory import _load_config_context
        from grimoire.memory.bundle import configure_project

        _, root = _load_config_context()
        config_path = root / "project-context.yaml"
        try:
            configure_project(config_path, installed.model_dir)
        except BundleError as exc:
            raise _fail(exc) from None
        configured = str(config_path)

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({
            "installed": True,
            "model": installed.manifest.model,
            "model_dir": str(installed.model_dir),
            "dim": installed.manifest.dim,
            "files": len(installed.manifest.files),
            "configured": configured,
        }, indent=2))
        return
    console.print(f"[green]Bundle installed[/green] {installed.model_dir}")
    console.print(f"  Model    : {installed.manifest.model}")
    console.print(f"  Files    : {len(installed.manifest.files)} digest(s) verified")
    if configured:
        console.print(f"  Config   : memory.embedding_model updated in {configured}")
    else:
        console.print("\nWire it in with:")
        console.print("  [bold]memory.embedding_model:[/bold] " + str(installed.model_dir))


@bundle_app.command("verify")
def bundle_verify(
    ctx: typer.Context,
    path: Path = _verify_path_arg,
    embed: bool = _verify_embed_opt,
) -> None:
    """Re-check digests and load the model with the network blocked."""
    try:
        report = verify_bundle(path, embed=embed)
    except BundleError as exc:
        raise _fail(exc) from None

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2))
        raise typer.Exit(0 if report.ok else 1)

    if report.ok:
        console.print(f"[green]Bundle OK[/green] {report.model}")
    else:
        console.print(f"[red]Bundle NOT usable[/red] {report.model}")
    console.print(f"  Files checked : {report.files_checked}")
    if report.mismatched:
        console.print(f"  [red]Wrong digest  :[/red] {', '.join(report.mismatched)}")
    if report.missing:
        console.print(f"  [red]Missing       :[/red] {', '.join(report.missing)}")
    if report.embedded:
        console.print(f"  Offline load  : OK via {report.embed_engine} (dim {report.embed_dim})")
    elif embed:
        console.print("  Offline load  : [yellow]not proven[/yellow]")
    for err in report.errors:
        console.print(f"  [red]{err}[/red]")
    raise typer.Exit(0 if report.ok else 1)


@bundle_app.command("where")
def bundle_where(ctx: typer.Context) -> None:
    """Print the default install root for bundles."""
    root = default_install_root()
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"install_root": str(root), "exists": root.is_dir()}))
        return
    console.print(str(root))
