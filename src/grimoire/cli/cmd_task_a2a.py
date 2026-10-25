"""``grimoire task import-a2a`` (R03) : importer une tâche A2A dans le Mission Ledger.

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ; la
logique vit dans :class:`grimoire.bridges.a2a_adapter.A2AAdapter`. Garde-fou :
une tâche ``completed`` venue de l'extérieur n'est jamais ``closed``, elle atterrit
en ``needs_verification`` (ADR-005, menace THR-004).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def register_a2a_commands(app: typer.Typer) -> None:
    app.command("import-a2a")(task_import_a2a)


def _schema_errors(brut: object) -> list[str]:
    """Écarts de forme d'une tâche A2A, en phrases lisibles (liste vide : la forme est bonne)."""
    if not isinstance(brut, dict):
        return ["le fichier doit contenir un objet JSON (la tâche A2A)"]
    erreurs: list[str] = []
    if not isinstance(brut.get("id"), str) or not brut["id"].strip():
        erreurs.append("champ `id` manquant ou vide (chaîne attendue)")
    status = brut.get("status")
    if status is not None and not (isinstance(status, dict) and isinstance(status.get("state", ""), str)):
        erreurs.append("`status` doit être un objet {\"state\": \"<état>\"}")
    if brut.get("input") is not None and not isinstance(brut["input"], dict):
        erreurs.append("`input` doit être un objet message {role, parts}")
    arts = brut.get("artifacts", [])
    if not isinstance(arts, list) or not all(isinstance(a, dict) for a in arts):
        erreurs.append("`artifacts` doit être une liste d'objets")
    return erreurs


def task_import_a2a(
    ctx: typer.Context,
    fichier: Annotated[Path, typer.Argument(help="Fichier JSON d'une tâche A2A (id, status.state, input, artifacts).")],
    mission: Annotated[str, typer.Option("--mission", help="Mission d'accueil ; vide ou inconnue : une mission est créée.")] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet (porte le ledger).")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path(
        "_grimoire-runtime-output/ledger"
    ),
) -> None:
    """Importe une tâche A2A ; un `completed` externe devient `needs_verification`, jamais `closed`."""
    from grimoire.bridges.a2a_adapter import A2AAdapter, A2ATask, A2ATaskState, is_known_state
    from grimoire.cli.cmd_task import _fmt, _service, console
    from grimoire.evidence.service import EvidenceService
    from grimoire.traces.ledger import TraceLedger

    try:
        brut = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(f"[red]✗[/red] {fichier} illisible ou JSON invalide : {exc}")
        raise typer.Exit(2) from exc
    erreurs = _schema_errors(brut)
    if erreurs:
        console.print(f"[red]✗[/red] {fichier} n'est pas une tâche A2A valide :")
        for erreur in erreurs:
            console.print(f"  - {erreur}")
        raise typer.Exit(2)
    try:
        tache = A2ATask.from_dict(brut)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        console.print(f"[red]✗[/red] {fichier} : structure A2A inattendue ({type(exc).__name__}) ; vérifie `input.parts` et `artifacts`.")
        raise typer.Exit(2) from exc
    if not is_known_state(tache.status_state):
        connus = ", ".join(sorted(v for k, v in vars(A2ATaskState).items() if k.isupper() and isinstance(v, str)))
        console.print(f"[red]✗[/red] état A2A inconnu {tache.status_state!r} (attendus : {connus}) ; rien n'a été écrit.")
        raise typer.Exit(2)

    service = _service(project_root, ledger_root)
    racine = service.ledger_root.parent
    adaptateur = A2AAdapter(service.ledger, EvidenceService(racine / "evidence"), TraceLedger(racine / "traces"))
    rapport = adaptateur.import_task(tache, mission_id=mission)
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(rapport.to_dict(), indent=2, ensure_ascii=False))
    elif rapport.errors:
        console.print(f"[red]✗[/red] {rapport.grimoire_task_id} ← A2A {rapport.a2a_task_id}")
    else:
        console.print(f"[green]OK[/green] {rapport.grimoire_task_id} ← A2A {rapport.a2a_task_id} : {rapport.state_mapped}")
    if rapport.errors:
        if _fmt(ctx) != "json":
            for erreur in rapport.errors:
                console.print(f"  [yellow]![/yellow] {erreur}")
        raise typer.Exit(1)
