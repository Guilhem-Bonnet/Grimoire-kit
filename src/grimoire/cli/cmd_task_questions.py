"""``grimoire task questions|resolve`` et le lot de ``task ask`` — les questions structurées (W6-07, issue #564).

Enregistrées sur ``task_app`` par :func:`register` (appelée depuis ``cmd_task_waiting``). Rien n'est
décidé ici : le registre et ses statuts sont dans :mod:`grimoire.missions.questions`, les écritures dans
:mod:`grimoire.missions.question_gestures`, la réponse humaine dans ``TaskService.answer`` (K6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

console = Console(stderr=True)

_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet.")]
_LEDGER_ROOT = Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")]
_ACTOR = Annotated[str, typer.Option("--actor", help="Qui agit.")]
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")

_STATUS_LABEL = {
    "open": "ouverte", "answered": "répondue", "deduced": "déduite", "not_awaited": "sans réponse, plus attendue",
}


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def read_mapping(source: str) -> dict[str, Any]:
    """Une question v2 d'un fichier JSON ou YAML (``-`` : l'entrée standard). Lève ``ValueError``."""
    from ruamel.yaml import YAML

    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
        raw = YAML(typ="safe").load(text)
    except Exception as exc:
        raise ValueError(f"question illisible : {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("`--spec` : un objet (une seule question)")
    return dict(raw)


def read_specs(source: str) -> list[Any]:
    """Les questions d'un fichier JSON ou YAML (``-`` : l'entrée standard). Lève ``ValueError``."""
    from ruamel.yaml import YAML

    from grimoire.missions.questions import parse_specs

    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"lot de questions illisible : {exc}") from exc
    try:
        raw = YAML(typ="safe").load(text)
    except Exception as exc:  # YAML est un sur-ensemble de JSON : une seule lecture pour les deux
        raise ValueError(f"lot de questions illisible : {exc}") from exc
    return parse_specs(raw)


def emit_batch(ctx: typer.Context, outcome: Any) -> None:
    """Ce qu'un lot a écrit : posées (une interruption), déduites, déjà connues."""
    data = outcome.to_dict()
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return
    if data["parked"]:
        console.print(f"[green]OK[/green] {outcome.task.id} attend un humain — {len(data['asked'])} question(s) en un lot")
    elif data["asked"]:
        console.print(f"[green]OK[/green] {outcome.task.id} — {len(data['asked'])} question(s) non bloquante(s) inscrite(s)")
    for q in data["asked"]:
        console.print(f"  - {q['id']} {'[bloquante]' if q.get('blocking', True) else '[non bloquante]'} {q['text']}")
    for q in data["deduced"]:
        console.print(f"  - {q['id']} déduite : {q['answer']} [dim](source : {q['source']})[/dim]")
    for r in data["reused"]:
        console.print(f"  - {r['question']['id']} déjà au registre, non reposée : {r['question']['text']}")


def list_precedents(ctx: typer.Context, project_root: Path) -> None:
    """``task questions --precedents`` : les réponses humaines retenues par classe, la plus récente d'abord."""
    from grimoire.missions.precedents import PrecedentError, load

    try:
        entries = load(project_root)
    except PrecedentError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps([entry.to_dict() for entry in entries], indent=2, ensure_ascii=False))
        return
    if not entries:
        console.print("[green]OK[/green] Aucun précédent enregistré.")
        return
    for entry in reversed(entries):
        console.print(
            f"  - {entry.id} — classe {entry.question_class} — « {entry.option} » — "
            f"{entry.answered_at or 'date inconnue'} [dim](question : {entry.question})[/dim]"
        )


def forget_precedent(ctx: typer.Context, project_root: Path, precedent_id: str) -> None:
    """``task questions --forget <p-…>`` : retire un précédent — le canal humain seul le peut."""
    from grimoire.cli import cmd_task as ct
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.precedents import PrecedentError, forget
    from grimoire.missions.schemas import ORIGIN_HUMAN

    origin = ct._comment_origin()
    if origin != ORIGIN_HUMAN:
        console.print("[red]✗[/red] retirer un précédent est un geste d'humain (canal interactif attesté).")
        raise typer.Exit(1)
    try:
        removed = forget(project_root, precedent_id)
    except (GrimoireMissionError, PrecedentError) as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(removed.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] précédent {removed.id} retiré ({removed.question_class}).")


def register(task_app: typer.Typer) -> None:
    """Pose ``task questions`` et ``task resolve`` sur ``grimoire task``."""
    from grimoire.cli import cmd_task as ct

    @task_app.command("questions")
    def task_questions(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument(help="Une tâche (défaut : toutes celles qui ont des questions).")] = "",
        open_only: Annotated[bool, typer.Option("--open", help="Seulement les questions encore sans réponse.")] = False,
        precedents: Annotated[
            bool, typer.Option("--precedents", help="Lister les précédents du projet (réponses humaines par classe).")
        ] = False,
        forget: Annotated[
            str, typer.Option("--forget", help="Retirer un précédent (p-…) — canal humain seulement.")
        ] = "",
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
    ) -> None:
        """Le registre des questions : statut, bloquante ou non, pourquoi, qui a répondu et sur quelle source.

        ``--precedents`` liste les réponses humaines retenues pour le projet, par classe de question ;
        ``--forget <id>`` en retire une (canal humain).
        """
        from grimoire.missions.questions import OPEN, questions_summary

        if precedents and forget:
            console.print("[red]✗[/red] `--precedents` et `--forget` ne vont pas ensemble.")
            raise typer.Exit(1)
        if forget:
            forget_precedent(ctx, project_root, forget)
            return
        if precedents:
            list_precedents(ctx, project_root)
            return
        service = ct._service(project_root, ledger_root)
        tasks = [ct._require_task(service, task_id)] if task_id else service.ledger.list_tasks()
        summaries = []
        for task in tasks:
            summary = questions_summary(task)
            if open_only:
                summary["questions"] = [q for q in summary["questions"] if q["status"] == OPEN]
            if task_id or summary["questions"]:
                summaries.append(summary)
        if _fmt(ctx) == "json":
            typer.echo(json.dumps(summaries[0] if task_id else summaries, indent=2, ensure_ascii=False))
            return
        if not any(s["questions"] for s in summaries):
            console.print("[green]OK[/green] Aucune question" + (" ouverte." if open_only else " au registre."))
            return
        for s in summaries:
            console.print(
                f"{s['task_id']} — {s['total']} question(s), {s['asked_to_human']} posée(s) à l'humain "
                f"en {s['interruptions']} interruption(s), {s['deduced']} déduite(s)"
            )
            for q in s["questions"]:
                flag = "bloquante" if q["blocking"] else "non bloquante"
                console.print(f"  - {q['id']} [{flag}, {_STATUS_LABEL.get(q['status'], q['status'])}] {q['text']}")
                if q["why"]:
                    console.print(f"    [dim]pourquoi : {q['why']}[/dim]")
                if q.get("answer_origin"):
                    via = f"source : {q['source']}" if q.get("source") else f"canal {q['answer_origin']}"
                    console.print(f"    [dim]réponse : {q['answer']} ({q['answered_by']}, {via})[/dim]")

    @task_app.command("resolve")
    def task_resolve(
        ctx: typer.Context,
        task_id: Annotated[str, typer.Argument()],
        question_id: Annotated[str, typer.Argument(help="La question (q-…), non bloquante.")],
        answer: Annotated[str, typer.Argument(help="La réponse déduite.")],
        source: Annotated[str, typer.Option("--source", help="D'où vient la réponse : fichier, mémoire, commande.")] = "",
        project_root: _PROJECT_ROOT = Path(),
        ledger_root: _LEDGER_ROOT = _DEFAULT_LEDGER,
        actor: _ACTOR = "cli",
    ) -> None:
        """Déduit la réponse d'une question non bloquante, source citée — jamais une bloquante (l'humain seul)."""
        from grimoire.core.exceptions import GrimoireError

        service = ct._service(project_root, ledger_root)
        ct._require_task(service, task_id)
        try:
            task = service.resolve_question(task_id, question_id, answer, source, actor)
        except (GrimoireError, ValueError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc
        ct._emit_task(ctx, task, f"{question_id} déduite")


__all__ = ["emit_batch", "read_specs", "register"]
