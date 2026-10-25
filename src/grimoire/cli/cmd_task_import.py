"""``grimoire task import`` : un plan JSON devient des tâches et leurs liens (P50).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_import_command` l'accroche au même groupe ``task``. La logique —
validation avant écriture, idempotence — vit dans :mod:`grimoire.missions.plan_import`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape


def register_import_command(app: typer.Typer) -> None:
    """Accroche ``import`` au groupe *app*."""
    app.command("import")(task_import)


def task_import(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument(help="Plan JSON : {\"tasks\": [{key, title, acceptance, evidence, priority, depends_on}]}.")],
    mission: Annotated[str, typer.Option("--mission", help="Mission de rattachement (sinon celle du plan, sinon la mission par défaut).")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Valide et montre ce qui serait créé, sans rien écrire.")] = False,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Importe un plan JSON dans le Mission Ledger et rend la table clé du plan → clé du ledger.

    Le plan est validé en entier avant la première écriture : une anomalie
    (champ inconnu, critère manquant, dépendance absente, cycle, mission
    inconnue) refuse tout, sans rien écrire. Relancé, l'import ne recrée rien :
    une entrée déjà importée est reconnue par sa clé, une entrée `existing`
    désigne une tâche déjà au ledger et est sautée. Une tâche existante n'est
    jamais modifiée ; si le plan a changé depuis, la ligne est marquée divergente.
    """
    from grimoire.cli.cmd_task import _fmt, _service, console
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.plan_import import PlanError, import_plan, load_plan

    try:
        plan = load_plan(plan_file)
        report = import_plan(_service(project_root, ledger_root), plan, mission=mission, actor=actor, dry_run=dry_run)
    except PlanError as exc:
        console.print(f"[red]✗[/red] plan refusé, rien n'a été écrit ({len(exc.problems)} anomalie(s)) :")
        for problem in exc.problems:
            console.print(f"  - {escape(problem)}")
        raise typer.Exit(1) from exc
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(("[yellow]--dry-run[/yellow] : rien n'est écrit. " if dry_run else "") + f"Plan « {escape(report.plan_id)} »")
    for row in report.rows:  # la table clé du plan → clé du ledger, sur stdout (colonnes séparées par une tabulation)
        typer.echo(f"{row.key}\t{row.task_id or '-'}\t{row.action}")
    for link in report.links:
        console.print(f"  [dim]lien : {escape(link['task'])} dépend de {escape(link['depends_on'])}"
                      + (f" ({link['state']})" if "state" in link else "") + "[/dim]")
