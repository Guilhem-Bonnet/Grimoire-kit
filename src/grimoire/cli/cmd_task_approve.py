"""``grimoire task approve-check`` (W3-03).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_approve_commands` l'accroche au même groupe ``task``. La logique
vit dans :mod:`grimoire.missions.check_gate`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape


def register_approve_commands(app: typer.Typer) -> None:
    """Accroche ``approve-check`` au groupe *app*."""
    app.command("approve-check")(task_approve_check)


def task_approve_check(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche dont on approuve les commandes.")],
    cmd: Annotated[
        list[str] | None,
        typer.Option("--cmd", help="Approuver cette commande exacte plutôt que les `checks` déclarés de la tâche."),
    ] = None,
    allow_unattested: Annotated[
        bool,
        typer.Option(
            "--allow-unattested",
            help="Terminal nu : accepter une approbation que aucun tour humain n'atteste (journalisée `unattested`).",
        ),
    ] = False,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Approuve par empreinte les commandes de vérification d'une tâche (W3-03).

    Sans approbation, `task verify` et le dispatch de flow (profils governed et
    production) ne lancent aucune commande. L'approbation vaut pour la commande
    exacte et ce qu'elle doit rendre, pour CETTE tâche : modifier un octet la
    révoque, et l'approbation d'une autre tâche ne vaut pas. Depuis une
    session d'hôte ou un agent, elle exige un tour humain postérieur au challenge
    que `task verify` a émis (W2-01) ; les caractères cachés (U+202E, U+200B…) sont refusés.
    """
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.missions.check_gate import ApprovalRefusedError, CheckSpec, approve_checks, specs_of_task, visible

    root = project_root.resolve()
    task = _require_task(_service(project_root, ledger_root), task_id)
    specs = tuple(CheckSpec(c) for c in cmd if c.strip()) if cmd else specs_of_task(task)
    if not specs:
        console.print(f"[red]✗[/red] {task_id} ne déclare aucune commande : rien à approuver (`--cmd` en nomme une)")
        raise typer.Exit(2)
    try:
        outcome = approve_checks(root, task_id, specs, actor=actor, allow_unattested=allow_unattested)
    except ApprovalRefusedError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(3 if exc.code in ("awaiting_human", "unattested") else 2) from exc
    if _fmt(ctx) == "json":
        typer.echo(
            json.dumps(
                {
                    "status": "approved",
                    "task_id": task_id,
                    "approved": list(outcome.approved),
                    "actor": outcome.actor,
                    "attestation": outcome.attestation,
                    "attestation_reason": outcome.attestation_reason,
                },
                indent=2,
            )
        )
        return
    console.print(f"[green]OK[/green] {len(specs)} commande(s) approuvée(s) pour {task_id} — acteur {outcome.actor}, {outcome.attestation}")
    for spec in specs:
        console.print(f"  - {escape(visible(spec.cmd))}  [dim]{spec.sha256[:12]}[/dim]")
    if outcome.attestation != "attested":
        console.print(
            f"[yellow]![/yellow] non attestée ({outcome.attestation_reason}) : aucun tour humain ne le prouve — "
            "l'approbation rend la falsification plus coûteuse, pas impossible."
        )


def report_unapproved(exc: Exception, task_id: str, *, as_json: bool, console: Any) -> None:
    """Rend le refus de `task verify` sur des commandes non approuvées (rien n'a été lancé)."""
    blocked = exc.blocked  # type: ignore[attr-defined]
    if as_json:
        rows = [{"index": b.index, "command_sha256": b.command_sha256, "reason": b.reason} for b in blocked]
        typer.echo(json.dumps({"status": "unapproved", "task_id": task_id, "unapproved": rows}, indent=2))
        return
    console.print(f"[red]✗[/red] unapproved — {exc}")
    for b in blocked:
        console.print(f"  - {escape(b.display)}  [dim]sha256 {b.command_sha256[:12]} : {b.reason}[/dim]")
    console.print(f"[dim]Lis ces commandes, puis `grimoire task approve-check {task_id}`.[/dim]")


def verify_payload(outcome: Any) -> dict[str, Any]:
    """Charge JSON de `task verify` : pack, verdict et reçus d'exécution."""
    return {
        "pack": outcome.pack.to_dict(),
        "verdict": outcome.verdict.to_dict(),
        "receipts": [{k: r[k] for k in ("command_sha256", "tree_fingerprint", "exit", "reused")} for r in outcome.receipts],
    }


def report_declarative(task: Any, root: Path, evidence_root: Path, paths: list[str], *, as_json: bool, console: Any) -> int:
    """``task verify --evidence`` (KAN-FRICTIONS) : écrit le pack déclaratif, le présente, rend le code de sortie.

    4 quand le pack est écrit (jamais 0 : ce n'est pas un vert mécanique), 2 quand un fichier est refusé.
    """
    from grimoire.missions.declarative_evidence import DeclarativeEvidenceError, declare_evidence

    try:
        outcome = declare_evidence(task, root, evidence_root, paths)
    except DeclarativeEvidenceError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        return 2
    if as_json:
        typer.echo(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
        return 4
    console.print(
        f"[yellow]DÉCLARATIF[/yellow] {task.id} — pack {outcome.pack.id} : {len(outcome.files)} fichier(s) présents et hachés, "
        f"aucune commande lancée ; verdict {outcome.verdict.verdict.value} ({outcome.verdict.id}), jamais un vert mécanique."
    )
    for rel, digest, size in outcome.files:
        console.print(f"  - {escape(rel)}  [dim]sha256 {digest[:12]}, {size} octets[/dim]")
    console.print("  La revue peut s'ouvrir (`task move --to needs_verification`) ; la clôture reste à un humain.")
    return 4
