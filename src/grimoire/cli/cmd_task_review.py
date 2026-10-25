"""``grimoire task review`` (W3-01).

Hors de :mod:`grimoire.cli.cmd_task` pour le plafond de taille d'un module ;
:func:`register_review_commands` l'accroche au même groupe ``task``. La logique
vit dans :mod:`grimoire.missions.review_receipt`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape


def register_review_commands(app: typer.Typer) -> None:
    """Accroche ``review`` au groupe *app*."""
    app.command("review")(task_review)


def task_review(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    verdict: Annotated[str, typer.Option("--verdict", help="READY ou NOT-READY (porté par le fichier avec --criteria).")] = "",
    criteria: Annotated[
        Path | None,
        typer.Option("--criteria", help="Verdict structuré JSON (grimoire-review-verdict/v1) : un statut par critère, porté au pack de preuve."),
    ] = None,
    finding: Annotated[
        list[str] | None,
        typer.Option("--finding", help="Constat « résumé :: commande qui le montre » (répétable) ; sans « :: », une suggestion."),
    ] = None,
    reviewer: Annotated[str, typer.Option("--reviewer", help="Qui a relu (défaut : l'acteur).")] = "",
    model: Annotated[str, typer.Option("--model", help="Modèle du relecteur, s'il en est un.")] = "",
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = Path("_grimoire-runtime-output/ledger"),
    actor: Annotated[str, typer.Option("--actor", help="Qui agit.")] = "cli",
) -> None:
    """Écrit le reçu de revue d'une tâche, lié à l'empreinte présente de l'arbre (W3-01).

    La clôture d'une tâche en `needs_verification` l'exige (V1/V2, et V0 en
    profil governed) et recalcule l'empreinte : tout changement après le reçu
    le périme (`review.receipt_stale`). Un NOT-READY doit porter au moins un
    constat avec sa commande (`review.finding_ungrounded` sinon).

    Avec `--criteria`, le verdict par critère (#876, lot 3) est validé contre
    les critères de la tâche, porté à un pack de preuve (les critères tenus y
    sont couverts) et au reçu, puis noté au ledger.
    """
    from grimoire.cli.cmd_task import _fmt, _require_task, _service, console
    from grimoire.core.standard_state import current_session
    from grimoire.missions.review_receipt import ReviewFinding, ReviewReceiptError, write_receipt
    from grimoire.missions.review_verdict import load_verdict, record_review

    service = _service(project_root, ledger_root)
    task = _require_task(service, task_id)
    findings = []
    for raw in finding or ():
        summary, _, cmd = raw.partition("::")
        findings.append(ReviewFinding(summary.strip(), cmd.strip()))
    session = current_session()[0]
    try:
        if criteria is not None:
            structured = load_verdict(criteria)
            if verdict and verdict.strip().upper() != structured.verdict:
                raise ReviewReceiptError("review.receipt_invalid", f"--verdict {verdict!r} contredit le fichier ({structured.verdict})")
            structured = replace(structured, findings=(*structured.findings, *findings))
            receipt = record_review(service, task, structured, reviewer_id=reviewer or actor, model=model, reviewer_session=session)
        elif not verdict:
            raise ReviewReceiptError("review.receipt_invalid", "--verdict ou --criteria requis")
        else:
            receipt = write_receipt(
                service.project_root, task_id, verdict.strip().upper(), findings=findings,
                reviewer_id=reviewer or actor, model=model, reviewer_session=session,
            )
    except (ReviewReceiptError, ValueError) as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False))
        return
    judged = f", {len(receipt.criteria_met)}/{len(receipt.criteria)} critère(s) tenu(s)" if receipt.criteria else ""
    console.print(
        f"[green]OK[/green] reçu {receipt.verdict} pour {task_id} "
        f"[dim](empreinte {receipt.tree_fingerprint[:12]}, {len(receipt.grounded_findings)} constat(s) vérifiable(s){judged})[/dim]"
    )
