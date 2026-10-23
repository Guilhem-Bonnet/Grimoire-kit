"""``grimoire task ask|answer|accept|wait|resume|wake|note|decisions|lint-decisions`` — l'attente nommée (K6, issue #771).

Ces commandes s'enregistrent sur ``task_app`` (:func:`register`, appelée en fin de
``cmd_task``) : le module des tâches dépasse déjà de quoi se relire. Elles ne
décident rien — la machine à états, l'autorité du canal et les écritures sont dans
:class:`grimoire.missions.service.TaskService` ; ici, on présente.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.missions.waiting import QUESTION_KINDS

console = Console(stderr=True)

_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet.")]
_LEDGER_ROOT = Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")]
_ACTOR = Annotated[str, typer.Option("--actor", help="Qui agit.")]
_EPOCH = Annotated[
    int | None, typer.Option("--epoch", help="Epoch du bail tenu (défaut : celui du claim de --actor).")
]
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def register(task_app: typer.Typer) -> None:
    """Pose les commandes d'attente sur ``grimoire task``."""
    from grimoire.cli import cmd_task as ct

    def run(ctx: typer.Context, project_root: Path, ledger_root: Path, task_id: str, gesture: Any) -> None:
        ct._steer(ctx, project_root, ledger_root, task_id, gesture)

    @task_app.command("ask")
    def task_ask(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        text: Annotated[str, typer.Argument(help="La question posée à l'humain (sans `--from`).")] = "",
        kind: Annotated[str, typer.Option("--kind", help=f"Nature : {', '.join(QUESTION_KINDS)}.")] = "choice",
        option: Annotated[list[str] | None, typer.Option("--option", "-o", help="Réponse proposée (répétable).")] = None,
        non_blocking: Annotated[
            bool, typer.Option("--non-blocking", help="Ne gare pas la tâche : l'agent continue, l'humain répond quand il veut.")
        ] = False,
        why: Annotated[str, typer.Option("--why", help="Pourquoi la question est posée (ce que le reçu montre).")] = "",
        key: Annotated[str, typer.Option("--key", help="Identité stable : une reprise ne repose pas une question déjà répondue.")] = "",
        from_file: Annotated[
            str, typer.Option("--from", help="Un lot de questions (JSON ou YAML, `-` : entrée standard), posé en une interruption.")
        ] = "",
        epoch: _EPOCH = None,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Pose une question à l'humain (ou un lot, `--from`) : la tâche se gare en `waiting(human)` et remonte au cockpit."""
        if not from_file and not (non_blocking or why or key):
            if not text.strip():
                console.print("[red]✗[/red] une question (texte) ou un lot (`--from`)")
                raise typer.Exit(1)
            run(ctx, project_root, ledger_root, task_id,
                lambda s: s.ask(task_id, text, actor, kind=kind, options=tuple(option or ()), epoch=epoch))
            return
        from grimoire.cli.cmd_task_questions import emit_batch, read_specs
        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.questions import QuestionSpec
        from grimoire.missions.service import TaskRefusedError

        try:
            specs = read_specs(from_file) if from_file else [
                QuestionSpec(text, kind, tuple(option or ()), blocking=not non_blocking, why=why, key=key)
            ]
        except ValueError as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        service = ct._service(project_root, ledger_root)
        ct._require_task(service, task_id)
        try:
            outcome = service.ask_batch(task_id, specs, actor, epoch=epoch)
        except TaskRefusedError as refused:
            ct._refuse(ctx, refused)
        except (GrimoireError, ValueError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        emit_batch(ctx, outcome)

    @task_app.command("answer")
    def task_answer(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        text: Annotated[str, typer.Argument(help="La réponse (ou l'une des options proposées).")],
        question: Annotated[str, typer.Option("--question", help="Identifiant de la question (q-…).")] = "",
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Répond à la question en attente — canal humain seulement ; la tâche est rendue (`ready`)."""
        run(ctx, project_root, ledger_root, task_id,
            lambda s: s.answer(task_id, text, actor, origin=ct._comment_origin(), question_id=question))

    @task_app.command("accept")
    def task_accept(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        reason: Annotated[str, typer.Option("--reason", help="Ce qui a été relu, et pourquoi la preuve suffit.")],
        close: Annotated[
            bool, typer.Option("--close/--no-close", help="Fermer la tâche une fois le verdict rangé.")
        ] = True,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        evidence_root: Annotated[
            Path, typer.Option("--evidence-root", help="Racine de l'EvidenceService.")
        ] = Path("_grimoire-runtime-output/evidence"),
        actor: _ACTOR = "cli",
    ) -> None:
        """Accepte la preuve déclarative d'une tâche en revue — canal humain seulement — puis la ferme (#865)."""
        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.human_acceptance import accept_task
        from grimoire.missions.schemas import TaskState

        service = ct._service(project_root, ledger_root)
        task = ct._require_task(service, task_id)
        root = project_root.resolve()
        origin = ct._comment_origin()
        try:
            outcome = accept_task(
                task, root, evidence_root if evidence_root.is_absolute() else root / evidence_root, reason, actor,
                origin=origin,
            )
        except (GrimoireError, ValueError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        service.comment(
            task_id, f"Acceptation humaine ({outcome.verdict.id}, pack {outcome.pack.id}) : {reason.strip()}",
            actor, origin=origin,
        )
        if _fmt(ctx) == "json":
            if not close:  # avec --close, la sortie JSON est celle de la transition
                typer.echo(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
        else:
            console.print(
                f"[green]ACCEPTÉ[/green] {task_id} — verdict {outcome.verdict.id} (passed, "
                f"{outcome.verdict.created_by}), pack {outcome.pack.id}"
            )
        if close:
            ct._transition(ctx, task_id, TaskState.CLOSED, project_root, ledger_root, actor)

    @task_app.command("wait")
    def task_wait(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        reason: Annotated[str, typer.Option("--reason", help="ci, quota ou paused (une attente humaine : `task ask`).")],
        until: Annotated[str, typer.Option("--until", help="Échéance ISO 8601, ou `unknown`.")] = "",
        note: Annotated[str, typer.Option("--note", help="Pourquoi, en une ligne.")] = "",
        epoch: _EPOCH = None,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Gare la tâche avec une raison nommée (`waiting`), sans question."""
        run(ctx, project_root, ledger_root, task_id,
            lambda s: s.wait(task_id, reason, actor, until=until, note=note, epoch=epoch))

    @task_app.command("resume")
    def task_resume(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        epoch: _EPOCH = None,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Reprend sur place une tâche en attente (même session, même epoch)."""
        run(ctx, project_root, ledger_root, task_id, lambda s: s.resume(task_id, actor, epoch=epoch))

    @task_app.command("note")
    def task_note(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        text: Annotated[str, typer.Argument(help="Ce qui a avancé.")],
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Note une progression (`task.progress_noted`) — information, jamais décision ni preuve."""
        from grimoire.core.exceptions import GrimoireError

        service = ct._service(project_root, ledger_root)
        ct._require_task(service, task_id)
        try:
            task = service.note_progress(task_id, text, actor)
        except (GrimoireError, ValueError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        ct._emit_task(ctx, task, "progression notée")

    @task_app.command("wake")
    def task_wake(
        ctx: typer.Context,
        apply: Annotated[bool, typer.Option("--apply", help="Écrire (sinon : montrer seulement).")] = False,
        no_notify: Annotated[bool, typer.Option("--no-notify", help="Ne pas lancer notify.command.")] = False,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "tick",
    ) -> None:
        """La part « attente » du tick : rend les attentes levées, notifie chaque question une fois.

        Une `waiting(human)` ne sort jamais au minuteur : seule une réponse humaine la rend.
        """
        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.decisions import notify_pending

        service = ct._service(project_root, ledger_root)
        try:
            decisions = service.wake_due(apply=apply)
            outcomes = [] if no_notify else notify_pending(
                service.ledger, service.project_root, apply=apply, actor=actor
            )
        except GrimoireError as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        if _fmt(ctx) == "json":
            typer.echo(json.dumps(
                {"applied": apply, "wake": [d.to_dict() for d in decisions], "notify": [o.to_dict() for o in outcomes]},
                indent=2, ensure_ascii=False,
            ))
            return
        if not decisions:
            console.print("[green]OK[/green] Aucune tâche en attente.")
        for d in decisions:
            verb = "rendue" if d.action == "wake" else "attend encore"
            console.print(f"  - {d.task_id} waiting({d.reason}) : {verb} ({d.cause})")
        for o in outcomes:
            if o.status in ("sent", "failed", "pending", "gave_up"):
                console.print(f"  - notification {o.task_id} : {o.status}" + (f" — {o.error}" if o.error else ""))
        if not apply and decisions:
            console.print("[dim]Aperçu : relancer avec --apply pour écrire.[/dim]")

    @task_app.command("decisions")
    def task_decisions(
        ctx: typer.Context,
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    ) -> None:
        """Les décisions qui attendent un humain, la plus ancienne d'abord."""
        from datetime import UTC, datetime

        service = ct._service(project_root, ledger_root)
        now = datetime.now(UTC)
        pending = service.pending_decisions()
        if _fmt(ctx) == "json":
            typer.echo(json.dumps([d.to_dict(now) for d in pending], indent=2, ensure_ascii=False))
            return
        if not pending:
            console.print("[green]OK[/green] Aucune décision en attente.")
            return
        for d in pending:
            for q in d.questions or (d.question,):
                flag = "" if q.blocking else " [non bloquante]"
                console.print(f"  - {d.task_id} [{q.kind}]{flag} {q.text}  ({q.id})")
                if q.options:
                    console.print(f"    [dim]options : {' | '.join(q.options)}[/dim]")

    @task_app.command("lint-decisions")
    def task_lint_decisions(
        ctx: typer.Context,
        task: Annotated[str, typer.Option("--task", help="Une seule tâche.")] = "",
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    ) -> None:
        """Signale les commentaires qui posent une décision sans `task ask` (indicateur heuristique, pas une preuve)."""
        from grimoire.missions.decisions import lint_decisions

        service = ct._service(project_root, ledger_root)
        findings = lint_decisions(service.ledger, task or None)
        if _fmt(ctx) == "json":
            typer.echo(json.dumps([f.to_dict() for f in findings], indent=2, ensure_ascii=False))
            return
        if not findings:
            console.print("[green]OK[/green] Aucune décision implicite repérée (heuristique : cela ne prouve rien).")
            return
        for f in findings:
            console.print(f"  - {f.task_id} ({f.source}) : {f.text}")
        console.print(
            "[dim]Heuristique : une question de fusion ou de permission sans `grimoire task ask`. "
            "Poser la question par `task ask` pour qu'elle remonte.[/dim]"
        )
        raise typer.Exit(1)

    # W6-07 : `task questions` (le registre) et `task resolve` (réponse déduite, source citée).
    from grimoire.cli.cmd_task_questions import register as register_questions

    register_questions(task_app)


__all__ = ["register"]
