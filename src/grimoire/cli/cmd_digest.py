"""``grimoire digest`` — résumer un gros texte par le fournisseur local le moins cher.

L'agent interactif dépense son quota à lire des journaux de CI, des sorties de
tick ou des transcripts. ``grimoire digest`` les résume d'abord : le texte est
découpé, un modèle du palier ``cheap`` (``currency: local`` préféré) résume
chaque morceau, puis la synthèse est rendue avec les lignes d'erreur du texte
source citées mot pour mot.

Quand aucun fournisseur n'est admissible — ou que l'appel échoue — la commande
rend un repli déterministe (début, fin, lignes d'erreur) et sort en 0 : le
texte est toujours lu, jamais perdu silencieusement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from grimoire.missions.digest import DEFAULT_MAX_WORDS, digest_text

console = Console()

_PATH_ARGUMENT = typer.Argument(help="Fichier à résumer, ou `-` pour lire l'entrée standard.")
_GOAL_OPTION = typer.Option("--goal", help="Ce que je cherche dans ce texte.")
_MAX_WORDS_OPTION = typer.Option("--max-words", help="Borne du résumé, en mots.")
_PROVIDER_OPTION = typer.Option("--provider", help="Forcer un fournisseur du registre (capacité chat, palier cheap).")
_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)


def digest_command(
    path: Annotated[str, _PATH_ARGUMENT] = "-",
    goal: Annotated[str, _GOAL_OPTION] = "",
    max_words: Annotated[int, _MAX_WORDS_OPTION] = DEFAULT_MAX_WORDS,
    provider: Annotated[str | None, _PROVIDER_OPTION] = None,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
) -> None:
    """Résume un fichier (ou stdin) par le fournisseur `chat`/`cheap` le moins cher.

    Les lignes d'erreur (`FAILED`, `Error`, `Traceback`, `AssertionError`,
    `exit code`) sont extraites du texte source, jamais du modèle, et rendues
    telles quelles sous le résumé avec leur numéro de ligne. Un repli
    déterministe (début, fin, erreurs) est rendu — et la commande sort en 0 —
    quand aucun fournisseur n'est disponible ou que l'appel échoue.
    """
    if max_words < 1:
        raise typer.BadParameter("--max-words doit valoir au moins 1")
    text = _read(path)
    digest = digest_text(
        project_root.resolve(),
        text,
        goal=goal,
        max_words=max_words,
        provider_id=provider,
    )
    typer.echo(digest.render(), nl=False)


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        console.print(f"[red]Lecture impossible :[/red] {exc}")
        raise typer.Exit(1) from exc
