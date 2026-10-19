"""``grimoire standard claims --resolve`` (W3-02) : résout la cellule Preuve de chaque claim.

Module à part : ``cmd_standard.py`` est au ras du plafond du ratchet de taille.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grimoire.core.standard_checks.base import _text_file
from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows
from grimoire.core.standard_checks.claim_proof_resolve import resolve_proof_cell
from grimoire.core.standard_generation import EVIDENCE_DIR, normalize_task_id


def resolve_ledger(root: Path, task_id: str) -> list[dict[str, object]]:
    """Une entrée par claim portant une preuve : ``{claim, proof, resolutions}``."""
    text = _text_file(root, EVIDENCE_DIR / task_id / "claim-ledger.md")
    scan = scan_claim_rows(text)
    rows: list[dict[str, object]] = []
    for _line, cells in scan.evaluated + scan.unevaluated_cells:
        if cells[3]:
            resolutions = resolve_proof_cell(root, task_id, cells[3])
            rows.append({"claim": cells[0], "proof": cells[3], "resolutions": [r.to_dict() for r in resolutions]})
    return rows


def claims_command(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id."),
    resolve: bool = typer.Option(False, "--resolve", help="Resolve every proof cell; exit 1 if one does not resolve."),
) -> None:
    """List claim proofs; with --resolve, check that each reference exists."""
    if not resolve:
        typer.echo("Nothing to do: pass --resolve to check the proof cells.", err=True)
        raise typer.Exit(2)
    rows = resolve_ledger(project_root, normalize_task_id(task_id))
    bad = [
        (row["claim"], res)
        for row in rows
        for res in row["resolutions"]  # type: ignore[attr-defined]
        if res["kind"] != "unstructured" and not res["resolved"]
    ]
    if str((ctx.obj or {}).get("output", "text")) == "json":
        typer.echo(json.dumps({"claims": rows, "unresolved": len(bad)}, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            for res in row["resolutions"]:  # type: ignore[attr-defined]
                mark = "ok" if res["resolved"] else ("free-text" if res["kind"] == "unstructured" else "UNRESOLVED")
                reason = f" ({res['reason']})" if res["reason"] else ""
                typer.echo(f"{row['claim']}  {mark:10}  {res['token']}{reason}")
        typer.echo(f"{len(rows)} claim(s) with a proof, {len(bad)} unresolved reference(s).")
    if bad:
        raise typer.Exit(1)
