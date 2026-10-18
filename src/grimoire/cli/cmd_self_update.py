"""CLI ``grimoire self update`` (et son alias ``grimoire update``) : canaux, vérification, bundle hors ligne."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

_CHANNEL = typer.Option(
    "--channel", help="stable (PyPI, jamais une pré-version) ou preview (pré-versions GitHub incluses).",
)
_CHECK = typer.Option("--check", help="Dire si une mise à jour existe sans rien télécharger ni installer.")
_OFFLINE = typer.Option("--offline", help="Aucune requête réseau (avec --check : rend « offline » ; sinon : exige --from).")
_FROM = typer.Option(
    "--from", help="Bundle hors ligne : un dossier (wheel + SHA256SUMS + dépendances) ou une wheel à côté de son SHA256SUMS.",
)
_JSON = typer.Option("--json", help="Sortie JSON (avec --check).")


def self_update_command(
    channel: Annotated[str, _CHANNEL] = "stable",
    check: Annotated[bool, _CHECK] = False,
    offline: Annotated[bool, _OFFLINE] = False,
    source: Annotated[Path | None, _FROM] = None,
    json_output: Annotated[bool, _JSON] = False,
) -> None:
    """Update grimoire-kit (PyPI, pré-versions GitHub ou bundle hors ligne), wheel vérifiée avant installation."""
    from grimoire.cli import app as cli
    from grimoire.cli.updater import run_update

    if json_output and not check:
        cli.console.print("[red]--json only applies to --check.[/red]")
        raise typer.Exit(1)
    # Sans réseau demandé, la sonde de connectivité n'est même pas tentée.
    online = False if (offline or source is not None) else cli.is_online()
    run_update(
        cli.__version__,  # type: ignore[attr-defined]  # ré-exporté par app.py, patché tel quel par les tests
        online=online, console=cli.console, channel=channel, check=check,
        offline=offline, source=source, as_json=json_output,
    )
