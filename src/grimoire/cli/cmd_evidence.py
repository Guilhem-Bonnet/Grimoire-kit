"""``grimoire evidence resolve <range>`` (D-13) : chaque preuve d'une plage git, résolue ou non."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grimoire.core.standard_checks.evidence_resolve import RangeError, resolve_range

evidence_app = typer.Typer(help="Resolve the proofs written in a git range.", no_args_is_help=True)


@evidence_app.command("resolve")
def resolve_command(
    ctx: typer.Context,
    range_spec: str = typer.Argument(..., metavar="RANGE", help="<base>..<head>, or one commit (its own changes)."),
    project_root: Path = typer.Option(Path(), "--project-root", help="Project root inside the git repository."),  # noqa: B008
) -> None:
    """Judge every proof of the claim-ledger rows written in RANGE against git at its head.

    Each proof is resolved, stale, missing or unknown; only resolved counts.
    Exit 1 as soon as one proof is not resolved, 2 on a bad range.
    """
    try:
        report = resolve_range(project_root, range_spec)
    except RangeError as exc:
        typer.echo(f"evidence resolve: {exc}", err=True)
        raise typer.Exit(2) from exc
    if str((ctx.obj or {}).get("output", "text")) == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        published = {True: "published", False: "not published", None: "no remote"}[report.published]
        base = report.base[:12] if report.base else "(root)"
        typer.echo(f"Range {base}..{report.head[:12]}: {report.commits} commit(s), judged at head ({published}).")
        for proof in report.proofs:
            mark = proof.status if proof.status == "resolved" else proof.status.upper()
            shown = proof.token or "(no proof)"
            typer.echo(f"{mark:9} {proof.task_id} {proof.claim}  {shown}  ({proof.reason})")
        counts = report.counts()
        if report.verdict == "empty":
            typer.echo("No proof written in this range: nothing proven, nothing refuted.")
        else:
            typer.echo(
                f"{len(report.proofs)} proof(s): {counts['resolved']} resolved, {counts['stale']} stale, "
                f"{counts['missing']} missing, {counts['unknown']} unknown."
            )
    if report.verdict == "unresolved":
        raise typer.Exit(1)
