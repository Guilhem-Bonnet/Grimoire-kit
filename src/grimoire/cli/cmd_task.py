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
    """Détaille une tâche, et ce que son prochain pas exigera.

    Le dernier ``task.dispatched`` du ledger porte la relecture (#327) : le
    montrer ici évite de rouvrir le rapport de ``dispatch`` pour savoir si le
    vert précédent mérite un regard.
    """
    from grimoire.missions.board import board_status_of
    from grimoire.missions.gates import GatesFileError, declared_transitions
    from grimoire.missions.verifiability import as_dict as verifiability_as_dict

    service = _service(project_root, ledger_root)
    task = _require_task(service, task_id)
    verifiabilite = verifiability_as_dict(task)
    dispatch_events = [e for e in service.ledger.list_events(task_id) if e.event_type == "task.dispatched"]
    last_dispatch = dispatch_events[-1].payload if dispatch_events else None
    if _fmt(ctx) == "json":
        payload = task.to_dict()
        payload["verifiability"] = verifiabilite
        payload["last_dispatch"] = last_dispatch
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{task.id}[/bold] — {task.title}")
    console.print(f"  état    : {task.status.value} (board : {board_status_of(task.status)})")
    console.print(f"  accepte : {', '.join(task.acceptance) or '—'}")
    console.print(f"  vérifiabilité : {verifiabilite['class']} — {verifiabilite['explanation']}")
    for entree in verifiabilite["criteria"]:
        motif = entree["pattern"] or "non reconnu"
        console.print(f"    [dim]- {escape(entree['criterion'])} → {motif}[/dim]")
    if last_dispatch is not None and last_dispatch.get("review") is not None:
        _print_review(
            last_dispatch["review"],
            [str(p) for p in last_dispatch.get("review_files") or ()],
            last_dispatch.get("review_note"),
        )
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


@task_app.command("recall")
def task_recall(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
) -> None:
    """Ce que la mémoire du projet sait de cette tâche et de ses voisines.

    Même rappel que celui que le hook SessionStart injecte au claim, et que
    l'outil MCP `task_recall` — historique propre de la tâche, tâches liées ou
    au titre proche avec leurs causes d'arrêt, et ce que la mémoire du projet a
    consolidé sur des sujets voisins. Borné en tokens.
    """
    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    recall = service.recall(task_id)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(recall.to_dict(), indent=2, ensure_ascii=False, default=str))
        return
    console.print(recall.text)


@task_app.command("trace")
def task_trace(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche (ou `bootstrap`).")],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    kernel_root: Annotated[Path, typer.Option("--kernel-root", help="Racine du RuntimeKernel.")] = Path("_grimoire-runtime-output/runtime"),
    causes_only: Annotated[bool, typer.Option("--causes", help="N'afficher que ce qui explique un arrêt.")] = False,
) -> None:
    """Timeline unifiée d'une tâche : transitions, outils refusés, gates rouges, checkpoints, preuves.

    Lit le Mission Ledger, le TraceLedger des hooks, le RuntimeKernel et
    l'EvidenceService, indexés par tâche et triés dans le temps. Une source
    absente est dite absente ; rien n'est créé, rien n'est inventé.
    """
    from grimoire.missions.trace import build_task_timeline

    timeline = build_task_timeline(project_root.resolve(), task_id, ledger_root=ledger_root, kernel_root=kernel_root)
    if timeline.is_empty:
        console.print(f"[red]✗[/red] Aucune trace de {task_id} : ni au ledger, ni dans les journaux.")
        absentes = [k for k, v in timeline.sources.items() if v is None]
        if absentes:
            console.print(f"[dim]journaux absents : {', '.join(absentes)}[/dim]")
        console.print("[dim]`grimoire task list` montre ce que le ledger porte.[/dim]")
        raise typer.Exit(1)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(timeline.to_dict(), indent=2, ensure_ascii=False, default=str))
        return

    if timeline.task is not None:
        console.print(f"[bold]{timeline.task.id}[/bold] — {timeline.task.title}  [dim]({timeline.task.status.value})[/dim]")
    else:
        console.print(f"[bold]{task_id}[/bold]  [dim](inconnue du ledger — journaux seulement)[/dim]")
    absentes = [k for k, v in timeline.sources.items() if v is None]
    if absentes:
        console.print(f"[dim]sources absentes : {', '.join(absentes)}[/dim]")

    entries = timeline.causes if causes_only else timeline.entries
    for entry in entries:
        marque = "[red]✗[/red]" if entry.failure else " "
        heure = entry.at[11:19] if len(entry.at) >= 19 else entry.at
        console.print(f"  {marque} {escape(heure)}  [dim]{entry.source:8}[/dim] {escape(entry.summary)}")

    causes = timeline.causes
    if causes:
        console.print(f"\n[red]Cause(s) d'arrêt : {len(causes)}[/red]")
        for entry in causes:
            console.print(f"  - {escape(entry.summary)}  [dim]({entry.source}, {entry.kind})[/dim]")
    else:
        console.print("\n[green]Aucune cause d'arrêt enregistrée.[/green]")


_TRACES_ROOT = Annotated[Path, typer.Option("--traces-root", help="Racine du TraceLedger.")]
_DEFAULT_TRACES = Path("_grimoire-output/traces")


def _estimate_model_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Ordre de grandeur $ à partir de `tools.cost_model.MODEL_RATES` — jamais une facture.

    Reconnaissance par sous-chaîne (``model.lower()`` contient ``"opus"``,
    ``"sonnet"``, ``"haiku"``...) : un modèle non reconnu vaut 0.0, jamais une
    estimation inventée.
    """
    from grimoire.tools.cost_model import MODEL_RATES

    needle = model.lower()
    for family, rates in MODEL_RATES.items():
        if family in needle:
            return round((tokens_in / 1_000_000) * rates["in"] * 1000 + (tokens_out / 1_000_000) * rates["out"] * 1000, 6)
    return 0.0


@task_app.command("record-model-call")
def task_record_model_call(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    model: Annotated[str, typer.Option("--model", help="Nom du modèle appelé.")],
    tokens_in: Annotated[int, typer.Option("--tokens-in", min=0, help="Tokens en entrée.")] = 0,
    tokens_out: Annotated[int, typer.Option("--tokens-out", min=0, help="Tokens en sortie.")] = 0,
    mission_id: Annotated[str, typer.Option("--mission-id")] = "",
    agent_id: Annotated[str, typer.Option("--agent-id", help="Rôle ou id de l'agent appelant.")] = "",
    latency_ms: Annotated[float, typer.Option("--latency-ms", min=0)] = 0.0,
    error: Annotated[bool, typer.Option("--error", help="L'appel a échoué.")] = False,
    project_root: _PROJECT_ROOT = Path(),
    traces_root: _TRACES_ROOT = _DEFAULT_TRACES,
) -> None:
    """Journalise un appel modèle dans le TraceLedger (AG-LLM-004, AG-OBS-003).

    AG-LLM-004 nomme cinq champs : modèle, rôle, coût, latence, erreur — les
    cinq sont capturés (``model``, ``--agent-id``, coût estimé depuis
    `tools.cost_model.MODEL_RATES`, ``--latency-ms``, ``--error``).

    Le kit n'appelle jamais un modèle lui-même — c'est l'hôte (Claude Code,
    Copilot...) qui le fait. Cette commande est le point d'entrée que l'hôte
    (ou un hook) appelle après coup pour que l'appel devienne une preuve
    durable : `grimoire task trace-export --format otel|langfuse` l'exporte
    ensuite comme n'importe quelle autre trace.
    """
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.schemas import TokenUsage, TraceOutcome

    root = project_root.resolve()
    traces_path = traces_root if traces_root.is_absolute() else root / traces_root
    cost = _estimate_model_cost(model, tokens_in, tokens_out)
    usage = TokenUsage(
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        total_tokens=tokens_in + tokens_out,
        estimated_cost_usd=cost,
    )
    ledger = TraceLedger(traces_path)
    trace = ledger.record(
        run_id=f"model-call-{task_id}",
        workflow_instance_id="",
        mission_id=mission_id,
        task_id=task_id,
        recipe_id="grimoire.model-call",
        outcome=TraceOutcome.FAILURE if error else TraceOutcome.SUCCESS,
        started_at=_now_iso(),
        agent_id=agent_id,
        model=model,
        token_usage=usage.to_dict(),
        latency_ms=latency_ms,
        error_count=1 if error else 0,
    )
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False, default=str))
        return
    console.print(f"[green]OK[/green] appel modèle journalisé : {model}, {tokens_in}+{tokens_out} tokens (~${cost:.4f})")


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()


@task_app.command("handoff")
def task_handoff(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    capsule_path: Annotated[Path, typer.Argument(help="Fichier JSON de la capsule SubagentStop.")],
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
) -> None:
    """Dérive un handoff-packet (ORC-03) d'une capsule et le trace au Mission Ledger (AG-ORC-005).

    `tools.handoff.build_handoff` savait déjà produire le packet ; rien ne
    l'appelait. Cette commande lui donne un appelant réel, et journalise la
    communication inter-agents dans le ledger append-only puisque c'est elle
    qui alimente la décision suivante de la tâche.
    """
    from grimoire.missions.ledger import MissionLedger
    from grimoire.tools.handoff import build_handoff, is_subagent_stop

    root = project_root.resolve()
    try:
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]✗[/red] Capsule illisible : {exc}")
        raise typer.Exit(1) from None
    if not is_subagent_stop(capsule):
        console.print("[red]✗[/red] Pas une capsule SubagentStop exploitable (event != 'SubagentStop').")
        raise typer.Exit(1)

    packet = build_handoff(capsule)
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    ledger = MissionLedger(ledger_path)
    ledger.append_event("handoff", task_id, "task", actor_id=str(packet["from"].get("agent", "unknown")), payload=packet)

    if _fmt(ctx) == "json":
        typer.echo(json.dumps(packet, indent=2, ensure_ascii=False, default=str))
        return
    console.print(f"[green]OK[/green] handoff {packet['from']['agent']} → {task_id} tracé au ledger : {packet['summary']}")


_EVIDENCE_ROOT = Annotated[Path, typer.Option("--evidence-root", help="Racine de l'EvidenceService.")]
_DEFAULT_EVIDENCE = Path("_grimoire-runtime-output/evidence")


@task_app.command("pack")
def task_pack(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    pack_id: Annotated[str, typer.Argument(help="Identifiant du pack de preuve (`EVD-...`), vu via `task trace`.")],
    project_root: _PROJECT_ROOT = Path(),
    evidence_root: _EVIDENCE_ROOT = _DEFAULT_EVIDENCE,
) -> None:
    """Détail d'un pack de preuve : items, couverture des critères d'acceptation.

    `grimoire task trace <task_id>` liste les identifiants de pack dans sa
    timeline (`detail.pack_id`) sans jamais en montrer le contenu ; cette
    commande lit le pack complet derrière un id.
    """
    from grimoire.evidence import EvidenceService

    root = project_root.resolve()
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
    service = EvidenceService(evidence_path)
    pack = service.get_pack(pack_id)
    if pack is None:
        console.print(f"[red]✗[/red] Pack introuvable : {pack_id}")
        raise typer.Exit(1)
    if pack.task_id != task_id:
        console.print(f"[yellow]![/yellow] Le pack {pack_id} appartient à la tâche {pack.task_id}, pas {task_id}.")

    if _fmt(ctx) == "json":
        typer.echo(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False, default=str))
        return

    console.print(f"[bold]{pack.id}[/bold] — tâche {pack.task_id}, profil {pack.profile.value}  [dim]({pack.created_at})[/dim]")
    if pack.coverage is not None:
        couverts = ", ".join(pack.coverage.acceptance_covered) or "—"
        console.print(f"  Critères couverts  : {couverts}")
        if pack.coverage.acceptance_missing:
            manquants = ", ".join(pack.coverage.acceptance_missing)
            console.print(f"  [red]Critères manquants[/red] : {manquants}")
    for item in pack.items:
        console.print(f"  - [{item.kind.value}] {escape(item.summary or item.uri)}  [dim]{item.uri}[/dim]")


@task_app.command("trace-export")
def task_trace_export(
    dest: Annotated[Path, typer.Argument(help="Fichier JSONL de sortie.")],
    fmt: Annotated[str, typer.Option("--format", help="otel ou langfuse.")] = "otel",
    mission_id: Annotated[str | None, typer.Option("--mission-id", help="Filtre sur une mission.")] = None,
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Exporte le TraceLedger (traces d'outils, verdicts de policy) en JSONL.

    ``--format otel`` suit les conventions sémantiques OTel GenAI ;
    ``--format langfuse`` le contrat REST ``/api/public/traces`` de Langfuse.
    Aucun SDK tiers requis dans les deux cas.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger

    if fmt not in {"otel", "langfuse"}:
        console.print(f"[red]✗[/red] Format inconnu : {fmt} (attendu : otel, langfuse)")
        raise typer.Exit(2)

    root = project_root.resolve()
    ledger = TraceLedger(root / TRACES_DIR)
    count = ledger.export_otel_jsonl(dest, mission_id=mission_id) if fmt == "otel" else ledger.export_langfuse(dest, mission_id=mission_id)
    console.print(f"[green]OK[/green] {count} trace(s) exportée(s) au format {fmt} → {dest}")


# ── Dispatch par cascade (issue #323) ─────────────────────────────────────────
# Le lot 3 attend le moteur de flow (#204). En attendant, la topologie hybride
# de l'épic — l'hôte tient la session, le kit remet une tranche de travail à un
# exécuteur puis applique le gate — se livre ici au niveau d'une tâche unique,
# en croisant la classe de vérifiabilité (#309) et le registre par palier de
# coût (#310). Pas de juge LLM, pas de worktree automatique : l'utilisateur
# travaille sur une branche propre, et c'est un `--check` mécanique qui décide.

_VERDICT_ICON: dict[str, str] = {
    "green": "[green]vert[/green]",
    "red": "[red]rouge[/red]",
    "rate_limit": "[yellow]limite/429[/yellow]",
    "timeout": "[yellow]timeout[/yellow]",
}


def _emit_dispatch(ctx: typer.Context, report: Any) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        raise typer.Exit(report.exit_code)

    if report.refusal is not None:
        console.print(f"[red]✗[/red] {report.task_id} — {report.refusal_message}")
        raise typer.Exit(report.exit_code)

    if report.dry_run:
        console.print(f"[bold]{report.task_id}[/bold] — vérifiabilité {report.verifiability}")
        console.print(f"  chaîne prévue : {' → '.join(report.planned_chain)}")
        console.print("  [dim]" + escape(report.prompt).replace("\n", "\n  ") + "[/dim]")
        raise typer.Exit(0)

    for attempt in report.attempts:
        icon = _VERDICT_ICON.get(attempt.verdict, attempt.verdict)
        console.print(
            f"  [{attempt.attempt}] {attempt.tier:6} {attempt.provider}/{attempt.model} "
            f"— {icon} [dim]({attempt.duration_s:.1f}s)[/dim]"
        )
        for check in attempt.checks:
            marque = "[green]OK[/green]" if check.ok else "[red]✗[/red]"
            console.print(f"        {marque} {escape(check.cmd)}")

    if report.succeeded:
        console.print(f"[green]OK[/green] {report.task_id} — vert au bout de {len(report.attempts)} tentative(s)")
        if report.transitioned_to:
            console.print(f"[dim]transition : → {report.transitioned_to} (vérification requise, V1)[/dim]")
        elif report.transition_refused:
            console.print(f"[yellow]![/yellow] transition non appliquée : {report.transition_refused}")
        if report.review is not None:
            _print_review(report.review, [str(p) for p in report.review_files], report.review_note)
    else:
        console.print(
            f"[red]✗[/red] {report.task_id} — chaîne épuisée, {len(report.attempts)} tentative(s), aucun vert"
        )
    raise typer.Exit(report.exit_code)


def _print_review(review: str, review_files: list[str], review_note: str | None) -> None:
    """Rendu texte de la classe de relisibilité (#327) — les mêmes données que ``to_dict()``."""
    label = "[red]requise[/red]" if review == "review_required" else "[dim]optionnelle[/dim]"
    console.print(f"[bold]relecture[/bold] : {label}")
    for path in review_files:
        console.print(f"  [dim]- {escape(path)}[/dim]")
    if review_note:
        console.print(f"  [dim]{escape(review_note)}[/dim]")


@task_app.command("dispatch")
def task_dispatch(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    check: Annotated[
        list[str] | None,
        typer.Option("--check", help="Commande dont le code de sortie 0 vaut vert (répétable, toutes doivent l'être)."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Montrer la classe, la chaîne et le prompt sans appeler.")
    ] = False,
    max_tier: Annotated[
        str | None, typer.Option("--max-tier", help="Ne pas dépasser ce palier (cheap, mid, strong).")
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Restreindre la cascade à ce fournisseur.")] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Timeout d'un appel fournisseur, en secondes.")] = 600.0,
    project_root: _PROJECT_ROOT = Path(),
    ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    actor: _ACTOR = "cli",
) -> None:
    """Délègue une tâche en cascade, du palier le moins cher au plus cher.

    La classe de vérifiabilité décide qui a le droit d'appeler : une tâche V2
    (aucun verdict mécanique ni revue reconnue) est refusée avant tout appel.
    V0 cascade depuis `cheap` ; V1 cascade depuis `mid` et, au vert, passe la
    tâche en `needs_verification` au lieu de la considérer close — le check
    mécanique n'est ici qu'un indice, la classe exige encore un regard humain.

    Sans `--check`, le dispatch est refusé : la classe dit que le verdict est
    mécanique, encore faut-il dire lequel. Un fournisseur sans `invocation`
    déclarée n'est jamais candidat.
    """
    from grimoire.missions.dispatch import run_dispatch
    from grimoire.providers.registry import SUPPORTED_MODEL_TIERS

    if max_tier is not None and max_tier not in SUPPORTED_MODEL_TIERS:
        console.print(f"[red]✗[/red] Palier inconnu : {max_tier} (attendu : {', '.join(SUPPORTED_MODEL_TIERS)})")
        raise typer.Exit(2)

    service = _service(project_root, ledger_root)
    _require_task(service, task_id)
    report = run_dispatch(
        service,
        task_id,
        checks=tuple(check or ()),
        max_tier=max_tier,
        provider_id=provider,
        dry_run=dry_run,
        call_timeout=timeout,
        actor=actor,
    )
    _emit_dispatch(ctx, report)
