"""``grimoire cadrage`` — comprendre avant de construire (B4)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from grimoire.core.cadrage import PHASES, check, scaffold, status

if TYPE_CHECKING:
    from grimoire.missions.cadrage_plan import CadragePlan

console = Console()
cadrage_app = typer.Typer(help="Cadrage produit : brief → brainstorm → compréhension → exigences → cahier des charges.")

_path_arg = typer.Argument(Path(), help="Racine du projet.")
_force_opt = typer.Option(False, "--force", "-f", help="Réécrire les gabarits existants.")
_name_opt = typer.Option("", "--name", help="Nom du projet (défaut : nom du dossier).")


@cadrage_app.command("init")
def cadrage_init(
    path: Path = _path_arg, name: str = _name_opt, force: bool = _force_opt
) -> None:
    """Pose les cinq phases du cadrage sous _grimoire/cadrage/."""
    root = path.resolve()
    written = scaffold(root, project_name=name or root.name, force=force)
    if written:
        console.print(f"[green]cadrage initialisé[/green] — {len(written)} gabarit(s) :")
        for p in written:
            console.print(f"  {p.relative_to(root)}")
    else:
        console.print("[yellow]déjà initialisé[/yellow] — rien à écrire (--force pour réécrire).")
    console.print(
        "\n[dim]Le chemin : brief → brainstorm → compréhension → exigences → "
        "cahier des charges. Remplissez avec vos agents, puis :[/dim] "
        "[cyan]grimoire cadrage check[/cyan]"
    )


@cadrage_app.command("status")
def cadrage_status(ctx: typer.Context, path: Path = _path_arg) -> None:
    """Progression du cadrage, phase par phase."""
    root = path.resolve()
    report = status(root)
    if not report["initialized"]:
        console.print(
            "[yellow]cadrage non initialisé[/yellow] — lancez "
            "[cyan]grimoire cadrage init[/cyan]"
        )
        raise typer.Exit(1)
    table = Table(title=f"Cadrage — {report['progress']} phases remplies")
    table.add_column("Phase")
    table.add_column("État")
    table.add_column("Sections à compléter")
    for r in report["phases"]:
        missing = [s for s, st in r["sections"].items() if st != "rempli"]
        style = {"rempli": "green", "partiel": "yellow"}.get(r["state"], "red")
        table.add_row(
            r["phase"] + (" [dim](gate)[/dim]" if r["gate"] else ""),
            f"[{style}]{r['state']}[/{style}]",
            ", ".join(missing) if missing else "—",
        )
    console.print(table)


@cadrage_app.command("check")
def cadrage_check(path: Path = _path_arg) -> None:
    """Gate de complétude : exigences + cahier des charges doivent être remplis."""
    root = path.resolve()
    errors, warnings = check(root)
    for w in warnings:
        console.print(f"[yellow]avertissement[/yellow] : {w}")
    for e in errors:
        console.print(f"[red]erreur[/red] : {e}")
    if errors:
        console.print(
            f"\n[red]cadrage incomplet[/red] — {len(errors)} phase(s) gate "
            "à compléter avant de construire."
        )
        raise typer.Exit(1)
    console.print(
        f"[green]cadrage complet[/green] — les {len(PHASES)} phases tiennent "
        "la route. Construisez."
        if not warnings
        else "[green]gate du cadrage passé[/green] — exigences et cahier des "
        "charges complets (phases amont partielles : voir avertissements)."
    )


_STATUS_LABEL = {
    "planned": "[green]à créer[/green]", "covered": "[green]couverte[/green]", "out_of_scope": "[dim]hors périmètre[/dim]",
    "conflict": "[red]conflit[/red]", "missing_criterion": "[red]sans critère[/red]",
}


@cadrage_app.command("plan")
def cadrage_plan(
    ctx: typer.Context,
    path: Path = _path_arg,
    dry_run: bool = typer.Option(False, "--dry-run", help="Montrer le plan sans rien écrire ni demander."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Écrire le plan sans confirmation (tâches et questions)."),
    check_only: bool = typer.Option(
        False, "--check", help="Couverture seulement : rend 1 si une exigence reste sans tâche, sans critère "
        "vérifiable ou bloquée par une question.",
    ),
    output: str = typer.Option("", "--output", "-o", help="text ou json (remplace le --output global)."),
    actor: str = typer.Option("cli", "--actor", help="Qui écrit (tâches et questions)."),
) -> None:
    """Du cadrage aux tâches du ledger : une tâche par exigence REQ-NNN, ambiguïtés posées en questions."""
    import json
    import sys

    from grimoire.core.cadrage import CADRAGE_DIR
    from grimoire.missions.cadrage_plan import apply_plan, build_plan
    from grimoire.missions.service import TaskService

    root = path.resolve()
    fmt = output or str((ctx.obj or {}).get("output", "text"))
    if not (root / CADRAGE_DIR).is_dir():
        console.print("[yellow]pas de cadrage[/yellow] — lancez [cyan]grimoire cadrage init[/cyan]")
        raise typer.Exit(1)
    service = TaskService(root)
    plan = build_plan(root, service)
    writes = bool(plan.proposals) or any(q.status in ("new", "unanswered") for q in plan.questions)
    shown = False
    if writes and not (dry_run or check_only):
        interactive = fmt != "json" and sys.stdin.isatty() and sys.stdout.isatty()
        if not yes and interactive:
            _print_plan(plan)
            shown = True
            n_questions = sum(1 for q in plan.questions if q.status in ("new", "unanswered"))
            yes = typer.confirm(
                f"Créer {len(plan.proposals)} tâche(s) et poser {n_questions} question(s) ?", default=True
            )
        if yes:
            plan = apply_plan(service, plan, actor)
    code = plan.exit_code(check=check_only)
    if fmt == "json":
        typer.echo(json.dumps({**plan.to_dict(), "exit_code": code}, indent=2, ensure_ascii=False))
    elif shown and not plan.applied:
        console.print("[dim]rien n'est écrit.[/dim]")
    else:
        _print_plan(plan, check_only=check_only, writes=writes)
    raise typer.Exit(code)


def _print_plan(plan: CadragePlan, *, check_only: bool = False, writes: bool = False) -> None:
    reqs = plan.requirements
    files = ", ".join(reqs.files_read) or "aucun fichier d'exigences"
    if not reqs.identified:
        console.print(
            f"[yellow]aucune exigence identifiée[/yellow] ({files}) — rien à planifier, le cadrage reste valide.\n"
            "Pour le planifier, préfixez chaque exigence et mettez ses critères en sous-liste :\n"
            "  [cyan]- REQ-001 [Must] : l'utilisateur se connecte par lien magique[/cyan]\n"
            "  [cyan]  - `pytest tests/test_login.py`[/cyan]"
        )
        return
    data = plan.to_dict()
    console.print(f"[bold]Cadrage[/bold] — {len(reqs.requirements)} exigence(s) identifiée(s) ({files})")
    proposals = {t["covers"][0]: t for t in data["tasks"]}
    for row in data["requirements"]:
        status = row["status"]
        detail = ""
        if row["id"] in proposals:
            task = proposals[row["id"]]
            status = "covered" if task.get("task_id") else status
            detail = f"{task['verifiability']} " + (task.get("task_id") or "")
        elif status == "covered":
            detail = ", ".join(row.get("tasks", []))
        console.print(
            f"  {row['id']:<9} {_STATUS_LABEL.get(status, status):<28} {detail:<12} « {row['title'] or 'sans titre'} »"
        )
    questions = [q for q in data["questions"] if q["status"] != "answered"]
    for q in questions:
        tag = "bloquante" if q["blocking"] else "non bloquante"
        where = {"new": "à poser", "open": f"posée sur {q.get('task_id', '')}",
                 "unanswered": "sans réponse, à reposer", "deferred": "différée"}.get(q["status"], q["status"])
        console.print(f"  [yellow]?[/yellow] \\[{tag}] {escape(q['text'])} [dim]({where})[/dim]")
    if any(q["status"] == "open" and q["blocking"] for q in questions):
        console.print("  [dim]→ répondez dans le cockpit (vue Décisions), ou par `grimoire task answer`.[/dim]")
    for note in data["notes"]:
        console.print(f"  [dim]{note}[/dim]")
    cov = data["coverage"]
    if cov["uncovered"] or cov["orphans"] or cov["unlinked"]:
        console.print(
            f"Couverture : {len(cov['uncovered'])} exigence(s) sans tâche"
            + (f" ({', '.join(cov['uncovered'])})" if cov["uncovered"] else "")
            + f", {len(cov['orphans'])} tâche(s) sur une exigence inconnue"
            + f", {len(cov['unlinked'])} tâche(s) ouverte(s) hors cadrage."
        )
    if plan.applied:
        created = [t["task_id"] for t in data["tasks"] if t.get("task_id")]
        console.print(f"[green]écrit[/green] — {len(created)} tâche(s) créée(s)" + (f" : {', '.join(created)}" if created else ""))
    elif writes and not check_only:
        console.print("[dim]Aperçu : rien n'est écrit.[/dim] [cyan]grimoire cadrage plan --yes[/cyan] [dim]l'écrit.[/dim]")
    if data["missing"] or data["conflicts"]:
        console.print(
            "[red]incomplet[/red] — sans critère vérifiable : " + (", ".join(data["missing"]) or "aucune")
            + (f" ; conflits : {', '.join(data['conflicts'])}" if data["conflicts"] else "")
        )
