"""``grimoire task add`` : ouvrir une tâche, ou en proposer le découpage (R07, R24).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_add_command` l'accroche au groupe ``task``, à la place d'origine.
La logique — critères, risque, finition, écriture — vit dans
:class:`grimoire.missions.service.TaskService`, que l'outil MCP ``task_add`` appelle aussi.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape


def register_add_command(app: typer.Typer) -> None:
    """Accroche ``add`` au groupe *app*."""
    app.command("add")(task_add)


def task_add(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Ce que la tâche accomplit (inutile avec --intake).")] = "",
    acceptance: Annotated[
        list[str] | None, typer.Option("--acceptance", "-a", help="Critère d'acceptation (répétable ; au moins un).")
    ] = None,
    mission: Annotated[str, typer.Option("--mission", help="Mission de rattachement.")] = "",
    owner: Annotated[str, typer.Option("--owner", help="Qui en répond.")] = "",
    evidence: Annotated[list[str] | None, typer.Option("--expect-evidence", help="Preuve attendue (répétable).")] = None,
    ready: Annotated[bool, typer.Option("--ready", help="La rendre réclamable tout de suite (proposed → ready, gate compris).")] = False,
    check: Annotated[
        list[str] | None,
        typer.Option("--check", help="Commande dont le code de sortie 0 rend le verdict (répétable) ; sans elle, jamais V0 (#772)."),
    ] = None,
    files: Annotated[
        list[str] | None,
        typer.Option("--file", help="Fichier que la tâche touchera (répétable) ; `task waves` signale ceux que deux tâches parallèles partagent."),
    ] = None,
    test_depth: Annotated[
        str,
        typer.Option(
            "--test-depth",
            help="Profondeur de test exigée à la fermeture : floor, standard, strict, ou auto (celle de `ceremony suggest`).",
        ),
    ] = "",
    description: Annotated[
        str,
        typer.Option("--description", "-d", help="Contexte, pourquoi, pistes : ce que l'agent qui réclame la tâche doit savoir au-delà du titre (#714)."),
    ] = "",
    description_file: Annotated[
        Path | None,
        typer.Option("--description-file", help="Même chose, lue depuis un fichier texte (brief long)."),
    ] = None,
    finition: Annotated[
        str,
        typer.Option(
            "--finition",
            help="Finition du livrable : maquette ou peaufine. Sans option, celle de la phase de project-context.yaml (R24).",
        ),
    ] = "",
    intake: Annotated[
        str,
        typer.Option(
            "--intake",
            help="Propose, sans rien écrire, le type, le risque et les tâches d'une demande (JSON) : `task add --intake \"<texte>\"` (R07).",
        ),
    ] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (pour les gates de preuve).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Ouvre une tâche dans le Mission Ledger.

    Un critère d'acceptation au moins est exigé — c'est le ledger qui le
    réclame, pas cette commande : une tâche dont on ne sait pas dire quand
    elle est finie ne peut pas être vérifiée, donc pas fermée. Le même
    service sert l'outil MCP ``task_add`` (issue #638).

    ``--intake`` n'ouvre rien : il classe la demande (type, risque, périmètre,
    tâches proposées) par l'intake déterministe et rend le JSON, à relire avant
    de créer les tâches une à une.
    """
    from grimoire.cli.cmd_task import _fmt, _refuse, _service, console
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.schemas import TaskCheck
    from grimoire.missions.service import TaskRefusedError

    if intake:
        _emit_intake(intake, title=title, acceptance=acceptance, console=console)
        return
    if not title.strip():
        console.print("[red]✗[/red] titre manquant : `grimoire task add \"<titre>\" -a \"<critère>\"`")
        raise typer.Exit(2)
    if not acceptance:
        console.print("[red]✗[/red] --acceptance manquant : au moins un critère d'acceptation est exigé")
        raise typer.Exit(2)
    if description and description_file is not None:
        console.print("[red]✗[/red] --description et --description-file sont exclusifs")
        raise typer.Exit(2)
    if description_file is not None:
        try:
            description = description_file.read_text(encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]✗[/red] --description-file illisible : {exc}")
            raise typer.Exit(2) from exc
    service = _service(project_root, ledger_root)
    checks = tuple(TaskCheck(cmd=c) for c in (check or ()) if c.strip())
    if test_depth == "auto":
        test_depth = _auto_test_depth(project_root, title, tuple(acceptance), tuple(evidence or ()), bool(checks))
    try:
        added = service.add(
            title, tuple(acceptance), mission_id=mission, owner=owner,
            expected_evidence=tuple(evidence or ()), actor=actor, ready=ready,
            checks=checks, files=tuple(files or ()), test_depth=test_depth,
            description=description, finition=finition,
        )
    except TaskRefusedError as refused:
        _refuse(ctx, refused)
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(added.to_dict(), indent=2, ensure_ascii=False))
        return
    from grimoire.cli.cmd_task import _emit_task

    if added.mission_created:
        console.print(f"[dim]Mission créée : {added.mission_id} (aucune n'existait).[/dim]")
    _emit_task(ctx, added.task, added.task.status.value)
    _print_verifiability(added.task)


def _emit_intake(text: str, *, title: str, acceptance: list[str] | None, console: Any) -> None:
    """Le JSON de l'intake sur stdout ; refusé, sans rien écrire, s'il se mêle à une création."""
    from grimoire.missions.intake import IntakeRequest, MissionIntakeService

    if title or acceptance:
        console.print("[red]✗[/red] --intake propose sans créer : ne se combine ni avec un titre ni avec --acceptance")
        raise typer.Exit(2)
    if not text.strip():
        console.print("[red]✗[/red] --intake attend le texte de la demande")
        raise typer.Exit(2)
    result = MissionIntakeService().analyze(IntakeRequest(raw_text=text))
    payload = {**result.to_dict(), "written": False, "next": "`grimoire task add \"<titre>\" -a \"<critère>\"` pour chaque tâche retenue"}
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_verifiability(task: Any) -> None:
    """Affiche la classe de vérifiabilité calculée et nomme chaque critère non reconnu (#876)."""
    from grimoire.cli.cmd_task import console
    from grimoire.missions.verifiability import as_dict

    verifiabilite = as_dict(task)
    console.print(f"  vérifiabilité : {verifiabilite['class']} — {escape(verifiabilite['reason'])}")
    for entree in verifiabilite["criteria"]:
        if entree["pattern"] is None:
            console.print(f"    [yellow]non reconnu[/yellow] : {escape(entree['criterion'])}")


def _auto_test_depth(
    project_root: Path, title: str, acceptance: tuple[str, ...], evidence: tuple[str, ...], has_checks: bool,
) -> str:
    """La profondeur de la route que `ceremony suggest` calcule pour cette tâche (W6-01).

    Les fichiers touchés ne sont pas connus à la création : la route ne descend
    pas sous `checked`, donc jamais sous `standard`.
    """
    from grimoire.core.execution_needs import resolve_need
    from grimoire.missions.ceremony import inputs_for, suggest

    inputs = inputs_for(
        title=title, acceptance=acceptance, expected_evidence=evidence, has_checks=has_checks, files=None,
        test_runner=resolve_need("test-runner", project_root.resolve()).command, impact_files=None,
    )
    return suggest(inputs).route.test_depth.value
