"""BMAD CLI entry point — ``bmad [command]``."""

from __future__ import annotations

import typer

from bmad.__version__ import __version__

app = typer.Typer(
    name="bmad",
    help="BMAD Kit — Composable AI agent platform.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bmad-kit {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True,
                                 help="Show version and exit."),
) -> None:
    """BMAD Kit — Composable AI agent platform."""
