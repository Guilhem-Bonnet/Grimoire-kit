"""``grimoire task`` — surface des tâches agentiques.

Premier maillon : l'export du task board depuis le Mission Ledger (ADR-005). Le
ledger est la source ; ``_grimoire/standard/task-board.yaml`` est régénéré depuis
lui, jamais l'inverse.

Les commandes d'écriture (issue #137) rendent ici ce que
:class:`grimoire.missions.service.TaskService` décide : la logique — machine à
états, gate de preuve, écriture, reprojection du board — vit dans le service,
que le serveur MCP appelle aussi (issue #138). Ce module ne fait que présenter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

task_app = typer.Typer(
    help="Tâches agentiques : projection du Mission Ledger vers le board gouverné.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
board_app = typer.Typer(help="Le board gouverné, projeté depuis le ledger.", no_args_is_help=True)
task_app.add_typer(board_app, name="board")

# Le rendu humain part sur stderr : le JSON de stdout reste pipeable.
console = Console(stderr=True)

_BOARD_RELPATH = Path("_grimoire/standard/task-board.yaml")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


@board_app.command("export")
def board_export(
    ctx: typer.Context,
    project_root: Annotated[Path, typer.Argument(help="Racine du projet cible.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = _DEFAULT_LEDGER,
    mission: Annotated[str | None, typer.Option("--mission", help="N'exporter qu'une mission.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Chemin de sortie (défaut : le board du standard).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Afficher la projection sans écrire.")] = False,
) -> None:
    """Régénère le task board depuis le Mission Ledger.

    Le fichier produit est un artefact de sortie : l'éditer à la main ne change
    rien au ledger, et le prochain export l'écrasera.
    """
    from grimoire.missions.board import build_board, write_board
    from grimoire.missions.ledger import MissionLedger

    root = project_root.resolve()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    if not (ledger_path / "events.jsonl").is_file():
        console.print(f"[red]✗[/red] Aucun Mission Ledger sous {ledger_path}.")
        console.print("[dim]Le board reste ce qu'il est — rien n'a été écrasé.[/dim]")
        raise typer.Exit(1)

    board = build_board(MissionLedger(ledger_path), project=root.name, mission_id=mission)
    dest = output if output is not None else root / _BOARD_RELPATH

    if dry_run:
        typer.echo(json.dumps(board, indent=2, ensure_ascii=False))
        return

    write_board(dest, board)
    counts = _counts(board)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"path": str(dest), "tasks": len(board["tasks"]), "by_status": counts}, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] {len(board['tasks'])} tâche(s) projetée(s) → {dest}")
    if counts:
        console.print("[dim]" + " · ".join(f"{k} {v}" for k, v in counts.items()) + "[/dim]")


def _counts(board: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for task in board["tasks"]:
        status = str(task["status"])
        out[status] = out.get(status, 0) + 1
    return out


# ── Surface d'écriture (issue #137) ───────────────────────────────────────────
# Chaque transition franchit deux portes : la machine à états du ledger, qui dit
# si le mouvement est concevable, et le gate de preuve, qui dit s'il est mérité.
# Les deux refusent en nommant ce qui manque — un refus qu'on ne sait pas lire
# se contourne, et un gate qu'on contourne ne garde rien.

_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet (pour les gates de preuve).")]
_LEDGER_ROOT = Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")]
_ACTOR = Annotated[str, typer.Option("--actor", help="Qui agit.")]


def _service(project_root: Path, ledger_root: Path) -> Any:
    from grimoire.missions.service import TaskService

    return TaskService(project_root, ledger_root)


def _require_task(service: Any, task_id: str) -> Any:
    from grimoire.core.exceptions import GrimoireMissionError

    try:
        return service.require(task_id)
    except GrimoireMissionError as exc:
        console.print(f"[red]✗[/red] {exc}")
        console.print("[dim]`grimoire task list` montre ce que le ledger porte.[/dim]")
        raise typer.Exit(1) from exc


def _refuse(ctx: typer.Context, refused: Any) -> None:
    """Rendre un refus de gate : en JSON tel quel, en texte preuve par preuve."""
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(refused.to_dict(), indent=2, ensure_ascii=False))
        raise typer.Exit(1)
    verdict = refused.verdict
    console.print(f"[red]✗[/red] Gate de preuve « {verdict.transition_id} » : "
                  f"{len(verdict.refusals)} exigence(s) non satisfaite(s)")
    for refusal in verdict.refusals:
        console.print(f"  - {refusal.evidence} : {refusal.reason}")
        console.print(f"    [dim]{refusal.remedy}[/dim]")
    raise typer.Exit(1)


def _transition(
    ctx: typer.Context, task_id: str, target: Any, project_root: Path, ledger_root: Path,
    actor: str, reason: str = "", *, claim: Any = None,
) -> None:
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.service import TaskRefusedError

    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    try:
        move = service.transition(task_id, target, actor, reason, claim=claim)
    except TaskRefusedError as refused:
        _refuse(ctx, refused)
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    _emit_move(ctx, move)


def _emit_move(ctx: typer.Context, move: Any) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(move.to_dict(), indent=2, ensure_ascii=False))
        return
    if move.advisories:
        console.print(f"[yellow]![/yellow] Gate de preuve « {move.verdict.transition_id} » : "
                      f"{len(move.advisories)} exigence(s) non satisfaite(s)")
        for line in move.advisories:
            console.print(f"  - {line}")
        console.print(f"[dim]Profil « {move.verdict.strictness} » : signalé, non bloquant.[/dim]")
    task = move.task
    console.print(f"[green]OK[/green] {task.id} — {task.title} [dim]({move.previous.value} → {task.status.value})[/dim]")


def _emit_task(ctx: typer.Context, task: Any, note: str = "") -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] {task.id} — {task.title}" + (f" [dim]({note})[/dim]" if note else ""))


@task_app.command("add")
def task_add(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Ce que la tâche accomplit.")],
    acceptance: Annotated[list[str], typer.Option("--acceptance", "-a", help="Critère d'acceptation (répétable).")],
    mission: Annotated[str, typer.Option("--mission", help="Mission de rattachement.")] = "",
    owner: Annotated[str, typer.Option("--owner", help="Qui en répond.")] = "",
    evidence: Annotated[list[str] | None, typer.Option("--expect-evidence", help="Preuve attendue (répétable).")] = None,
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Ouvre une tâche dans le Mission Ledger.

    Un critère d'acceptation au moins est exigé — c'est le ledger qui le
    réclame, pas cette commande : une tâche dont on ne sait pas dire quand
    elle est finie ne peut pas être vérifiée, donc pas fermée.
    """
    from grimoire.core.exceptions import GrimoireError

    service = _service(project_root, ledger_root)
    ledger = service.ledger
    mission_id = mission
    if not mission_id:
        missions = ledger.list_missions()
        if missions:
            mission_id = missions[0].id
        else:
            created = ledger.create_mission(title="Travaux courants", origin="cli", created_by=actor)
            mission_id = created.id
            console.print(f"[dim]Mission créée : {mission_id} (aucune n'existait).[/dim]")
    try:
        task = ledger.create_task(
            mission_id, title, acceptance=tuple(acceptance), owner=owner,
            expected_evidence=tuple(evidence or ()),
        )
    except GrimoireError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    service.project_board()
    _emit_task(ctx, task, "proposed")


@task_app.command("list")
def task_list(
    ctx: typer.Context,
    mission: Annotated[str | None, typer.Option("--mission", help="Restreindre à une mission.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="Restreindre à un état du ledger.")] = None,
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
) -> None:
    """Liste les tâches du ledger, avec leur colonne de board."""
    from grimoire.missions.board import board_status_of

    tasks = _service(project_root, ledger_root).list_tasks(mission, status)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps([t.to_dict() for t in tasks], indent=2, ensure_ascii=False))
        return
    if not tasks:
        console.print("[dim]Aucune tâche.[/dim]")
        return
    for task in tasks:
        # Sans échappement, Rich prend les crochets pour une balise de style
        # et fait disparaître l'état — la ligne restait muette sur l'essentiel.
        etat = escape(f"[{task.status.value} · {board_status_of(task.status)}]")
        console.print(f"  {task.id}  {etat}  {task.title}")


@task_app.command("show")
def task_show(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
) -> None:
    """Détaille une tâche, et ce que son prochain pas exigera."""
    from grimoire.missions.board import board_status_of
    from grimoire.missions.gates import GatesFileError, declared_transitions

    task = _require_task(_service(project_root, ledger_root), task_id)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{task.id}[/bold] — {task.title}")
    console.print(f"  état    : {task.status.value} (board : {board_status_of(task.status)})")
    console.print(f"  accepte : {', '.join(task.acceptance) or '—'}")
    if task.owner or task.claim:
        console.print(f"  porté par : {task.owner or (task.claim.actor_id if task.claim else '—')}")
    here = board_status_of(task.status)
    try:
        transitions = declared_transitions(project_root.resolve())
    except GatesFileError as exc:
        console.print(f"  [yellow]![/yellow] {exc} — aucune transition ne passera tant qu'il n'est pas réparé")
        return
    exigences = {to: e.get("required_evidence", []) for (src, to), e in transitions.items() if src == here}
    for to, req in exigences.items():
        console.print(f"  [dim]vers {to} : {', '.join(str(r) for r in req)}[/dim]")


@task_app.command("claim")
def task_claim(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    host: Annotated[str, typer.Option("--host", help="Machine ou runtime qui prend la tâche.")] = "local",
    files: Annotated[list[str] | None, typer.Option("--file", help="Fichier réservé en exclusivité (répétable).")] = None,
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Réclame une tâche prête : ready → claimed."""
    from grimoire.missions.schemas import TaskClaim, TaskState

    claim = TaskClaim(actor_id=actor, host_id=host, exclusive_files=tuple(files or ()))
    _transition(ctx, task_id, TaskState.CLAIMED, project_root, ledger_root, actor, claim=claim)


@task_app.command("move")
def task_move(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    to: Annotated[str, typer.Option("--to", help="État ledger visé (ready, running, needs_verification…).")],
    reason: Annotated[str, typer.Option("--reason", help="Pourquoi.")] = "",
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Déplace une tâche, si l'état le permet et si la preuve suit."""
    from grimoire.missions.schemas import TaskState

    try:
        target = TaskState(to)
    except ValueError:
        console.print(f"[red]✗[/red] État inconnu : {to}")
        console.print("[dim]états : " + ", ".join(s.value for s in TaskState) + "[/dim]")
        raise typer.Exit(1) from None
    _transition(ctx, task_id, target, project_root, ledger_root, actor, reason)


@task_app.command("block")
def task_block(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason", help="Ce qui bloque.")],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Bloque une tâche en disant pourquoi."""
    from grimoire.missions.schemas import TaskState

    _transition(ctx, task_id, TaskState.BLOCKED, project_root, ledger_root, actor, reason)


@task_app.command("close")
def task_close(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Ferme une tâche vérifiée.

    Le gate `review → accepted` exige un verdict de vérification accepté : sans
    lui, la fermeture est refusée. C'est la seule garantie qui empêche un board
    entièrement vert de ne rien prouver.
    """
    from grimoire.missions.schemas import TaskState

    _transition(ctx, task_id, TaskState.CLOSED, project_root, ledger_root, actor)


@task_app.command("link")
def task_link(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    depends_on: Annotated[str, typer.Option("--depends-on", help="Tâche dont celle-ci dépend.")],
    kind: Annotated[str, typer.Option("--kind", help="Nature du lien.")] = "blocks",
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Déclare une dépendance entre deux tâches."""
    from grimoire.missions.schemas import DependencyKind

    service = _service(project_root, ledger_root)
    task = _require_task(service, task_id)
    _require_task(service, depends_on)
    try:
        dependency_kind = DependencyKind(kind)
    except ValueError:
        console.print(f"[red]✗[/red] Nature de lien inconnue : {kind}")
        console.print("[dim]natures : " + ", ".join(k.value for k in DependencyKind) + "[/dim]")
        raise typer.Exit(1) from None
    service.ledger.append_event(
        "task.linked", task.id, "task", actor,
        {"task_id": task.id, "depends_on": depends_on, "kind": dependency_kind.value},
    )
    service.project_board()
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"task": task.id, "depends_on": depends_on, "kind": dependency_kind.value}, indent=2))
        return
    console.print(f"[green]OK[/green] {task.id} dépend de {depends_on} ({dependency_kind.value})")


@task_app.command("context")
def task_context(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
) -> None:
    """Produit le context bundle de cette tâche.

    Le format existait déjà ; il fallait fournir l'identifiant à la main, donc
    rien ne garantissait qu'il désigne une tâche réelle. Ici l'identifiant vient
    du ledger, et une tâche inconnue est refusée avant tout calcul.
    """
    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    artifact = service.context(task_id)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(artifact.data, indent=2, ensure_ascii=False, default=str))
        return
    console.print(f"[green]OK[/green] context bundle de {task_id} → {artifact.path}")
