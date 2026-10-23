"""``grimoire evidence`` : ``resolve <range>`` (D-13, chaque preuve d'une plage git) et ``chain explain`` (CHAIN-ACK)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grimoire.core.standard_checks.evidence_resolve import RangeError, resolve_range

evidence_app = typer.Typer(help="Resolve the proofs written in a git range; explain a break of the evidence log.", no_args_is_help=True)
chain_app = typer.Typer(help="The hash chain of the evidence log.", no_args_is_help=True)
evidence_app.add_typer(chain_app, name="chain")


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


@chain_app.command("explain")
def chain_explain_command(
    task_id: str = typer.Option(..., "--task-id", help="Task whose evidence log is broken (bootstrap when no task)."),
    line: int = typer.Option(..., "--line", min=1, help="Physical line of the log named by `standard verify`."),
    reason: str = typer.Option("<reason>", "--reason", help="Why the line is unchained, used to build the sentence."),
    project_root: Path = typer.Option(Path(), "--project-root", help="Project root."),  # noqa: B008
) -> None:
    """Diagnose a break of the evidence log and print the sentence a human types to explain it.

    Writes nothing: an agent runs this CLI through a shell, so the CLI cannot attest a human origin.
    Only a typed prompt (UserPromptSubmit) does, as for `Override Gate`. The log is never rewritten or
    re-chained. Exit 0 when the line is explainable or already explained, 1 otherwise (a true break
    is not explainable).
    """
    from grimoire.core.standard_checks.evidence_chain import analyze_chain
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath
    from grimoire.core.standard_generation import normalize_task_id
    from grimoire.hosts.decisions.chain_ack import covering_ack, explain_phrase, read_acks

    try:
        task = normalize_task_id(task_id)
    except ValueError as exc:
        typer.echo(f"evidence chain explain: {exc}", err=True)
        raise typer.Exit(2) from exc
    report = analyze_chain(project_root / evidence_log_relpath(task))
    target = next((item for item in report.inserted if item.line == line), None)
    if report.status != "broken" or report.kind != "unchained_inserted" or target is None:
        verdict = report.message or f"journal {report.status}"
        typer.echo(f"Line {line} is not explainable ({report.kind or report.status}): {verdict}", err=True)
        raise typer.Exit(1)
    ack = covering_ack(read_acks(project_root, task), task, target)
    if ack is not None:
        typer.echo(f"Line {line} already explained by {ack.get('ack_id')} on {ack.get('ts')}: {ack.get('reason')}")
        return
    typer.echo(f"Line {line} of the {task} evidence log has no `prev`; the chain continues around it. {report.message}")
    typer.echo("The log is not modified. A human explains it by typing, as a whole prompt:")
    typer.echo(f"  {explain_phrase(task, line, reason)}")
