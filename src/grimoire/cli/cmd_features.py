"""CLI ``grimoire features`` — canaux de features (stable / beta / experimental).

Liste et bascule les features beta d'un projet. Le canal beta est l'espace
de test des capacités candidates : fonctionnelles, testées, journalisées,
opt-in — leur promotion en stable se décide sur métriques d'usage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from grimoire.tools import features as feat

console = Console()
features_app = typer.Typer(
    help="Canaux de features : lister et activer les capacités beta.",
    no_args_is_help=True,
)

_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet (défaut : dossier courant).")

_CHANNEL_STYLE = {"stable": "green", "beta": "yellow", "experimental": "magenta"}


def _root(project_root: Path | None) -> Path:
    return (project_root or Path.cwd()).resolve()


def _apply_side_effects(root: Path, feature_id: str, enabled: bool) -> str | None:
    """Actions réelles portées par certaines features (au-delà du drapeau)."""
    if feature_id == "stigmergy-hooks":
        from grimoire.tools import stigmergy_hooks as sh

        if enabled:
            return sh.install_hooks(root)
        removed, note = sh.uninstall_hooks(root)
        return f"{removed} fichier(s) de hook retiré(s) · {note}"
    return None


@features_app.command("list")
def list_cmd(
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Lister les features à canal et leur état pour ce projet."""
    root = _root(project_root)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Feature")
    table.add_column("Canal")
    table.add_column("État")
    table.add_column("Description", overflow="fold")
    for entry in feat.list_features(root):
        style = _CHANNEL_STYLE.get(str(entry["channel"]), "white")
        state = "[green]activée[/green]" if entry["enabled"] else "[dim]désactivée[/dim]"
        if not entry["toggleable"]:
            state += " [dim](fixe)[/dim]"
        table.add_row(
            f"[b]{entry['id']}[/b]",
            f"[{style}]{entry['channel']}[/{style}]",
            state,
            str(entry["description"]),
        )
    console.print(table)


def _toggle(project_root: Path | None, feature_id: str, enabled: bool) -> None:
    root = _root(project_root)
    try:
        feature = feat.set_enabled(root, feature_id, enabled)
    except KeyError:
        known = ", ".join(sorted(feat.FEATURES))
        console.print(f"[red]✗[/red] Feature inconnue : {feature_id}. Connues : {known}")
        raise typer.Exit(2) from None
    except ValueError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(2) from None
    verb = "activée" if enabled else "désactivée"
    console.print(f"[green]✓[/green] [b]{feature.id}[/b] {verb} ({feature.channel}).")
    note = _apply_side_effects(root, feature_id, enabled)
    if note:
        console.print(f"  [dim]{note}[/dim]")


@features_app.command("enable")
def enable(
    feature_id: Annotated[str, typer.Argument(help="Identifiant de la feature.")],
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Activer une feature beta pour ce projet."""
    _toggle(project_root, feature_id, True)


@features_app.command("disable")
def disable(
    feature_id: Annotated[str, typer.Argument(help="Identifiant de la feature.")],
    project_root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Désactiver une feature beta pour ce projet."""
    _toggle(project_root, feature_id, False)
