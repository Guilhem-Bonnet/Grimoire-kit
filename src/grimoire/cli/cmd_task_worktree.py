"""``grimoire task worktree`` et ``grimoire task merge`` (W5-02).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_worktree_commands` les accroche au même groupe ``task``. La
logique vit dans :mod:`grimoire.missions.task_worktree`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def register_worktree_commands(app: typer.Typer) -> None:
    """Accroche ``worktree`` et ``merge`` au groupe *app*."""
    app.command("worktree")(task_worktree)
    app.command("merge")(task_merge)


def task_worktree(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    base: Annotated[str, typer.Option("--base", help="Base du worktree, épinglée à son SHA.")] = "HEAD",
    into: Annotated[str, typer.Option("--into", help="Branche où la tâche se fusionnera (défaut : la branche extraite).")] = "",
    path: Annotated[Path | None, typer.Option("--path", help="Dossier du worktree (défaut : sous le dossier git commun).")] = None,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (dépôt git).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Crée le worktree ``task/<id>`` de la tâche sur une base épinglée ; idempotent."""
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.task_worktree import create_task_worktree

    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    try:
        wt = create_task_worktree(service, task_id, repo=project_root, base=base, into=into, path=path, actor=actor)
    except GrimoireMissionError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(wt.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] {wt.branch} sur {wt.base_sha[:12]} → {wt.path} (fusion vers {wt.into})")


def task_merge(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    check: Annotated[
        str | None,
        typer.Option("--check", help="Commande de vérification (défaut : besoin test-runner du projet)."),
    ] = None,
    sha: Annotated[str, typer.Option("--sha", help="SHA relu : une tête de branche différente refuse.")] = "",
    timeout: Annotated[float, typer.Option("--timeout", help="Délai du check, en secondes.")] = 1800.0,
    allow_test_changes: Annotated[
        bool, typer.Option("--allow-test-changes", help="Autorise la modification de tests protégés (journalisé).")
    ] = False,
    protect: Annotated[
        list[str] | None, typer.Option("--protect", help="Motif protégé en plus des défauts (relatif au projet, répétable).")
    ] = None,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (dépôt git).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Fusionne la tâche dans sa cible, en série, si le check est vert sur le SHA fusionné. Ne pousse pas."""
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.task_worktree import merge_task

    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    try:
        outcome = merge_task(service, task_id, repo=project_root, check=check, expected_sha=sha, actor=actor, timeout=timeout,
                             allow_test_changes=allow_test_changes, protect=tuple(protect or ()))
    except GrimoireMissionError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
    else:
        mark = "[green]OK[/green]" if outcome.ok else "[red]✗[/red]"
        console.print(f"{mark} {task_id} — {outcome.status} : {outcome.reason}")
    if not outcome.ok:
        raise typer.Exit(1)
