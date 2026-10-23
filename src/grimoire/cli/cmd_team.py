"""``grimoire team unit …`` (W7-02) : unités de travail partagées entre humains par git.

La logique vit dans :mod:`grimoire.missions.team_units` ; ici, l'identité, la
sortie texte/JSON et le commit facultatif du seul fichier écrit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import team_units as tu

team_app = typer.Typer(help="Work shared between humans through git (team units).", no_args_is_help=True)
unit_app = typer.Typer(help="Publish, claim, adopt and deliver shared units of work.", no_args_is_help=True)
team_app.add_typer(unit_app, name="unit")

RootOpt = Annotated[Path, typer.Option("--project-root", help="Racine du dépôt partagé.")]
AsOpt = Annotated[str, typer.Option("--as", help="Identité déclarée (défaut : `git config user.email`).")]
CommitOpt = Annotated[bool, typer.Option("--commit", help="Committer le seul fichier d'événement écrit.")]
HoursOpt = Annotated[float, typer.Option("--hours", help="Durée du bail partagé, en heures.")]


def _json(ctx: typer.Context) -> bool:
    return str((ctx.obj or {}).get("output", "text")) == "json"


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"team unit: {exc}", err=True)
    return typer.Exit(1)


def _write(
    ctx: typer.Context, root: Path, unit: str, kind: str, declared: str, commit: bool,
    data: dict[str, Any] | None = None, hours: float = tu.DEFAULT_TTL_HOURS,
) -> None:
    try:
        actor, source = tu.resolve_identity(root, declared)
        if kind == "deliver":
            data = {"evidence": tu.resolve_evidence(root, (data or {}).get("refs", []))}
        event, path = tu.record(root, unit, kind, actor, identity=source, data=data, hours=hours)
    except GrimoireMissionError as exc:
        raise _fail(exc) from exc
    rel = path.relative_to(root).as_posix()
    committed = ""
    if commit:
        add = tu.run_git(root, "add", "--", rel)
        done = tu.run_git(root, "commit", "-q", "-m", f"team(unit): {kind} {unit} par {actor}", "--", rel)
        if add.returncode or done.returncode:
            typer.echo(f"team unit: événement écrit ({rel}) mais commit refusé : {(done.stderr or add.stderr).strip()}", err=True)
            raise typer.Exit(1)
        committed = tu.run_git(root, "rev-parse", "HEAD").stdout.strip()
    state = tu.load_unit(root, unit)
    now = datetime.now(UTC)
    if _json(ctx):
        out: dict[str, Any] = {
            "event": event.id, "kind": kind, "actor": actor, "identity": source, "file": rel, "commit": committed,
        }
        out["unit"] = state.to_dict(now) if state is not None else None
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        return
    typer.echo(f"{kind} {unit} par {actor} (identité déclarée : {source}) -> {rel}")
    if state is not None:
        typer.echo(_line(state, now))
    typer.echo(f"Commit : {committed[:12]}" if committed else f"À partager : git add {rel} && git commit && git push")


def _line(state: tu.UnitState, now: datetime) -> str:
    if state.status == "contested":
        sides = "; ".join(f"{b.head[:12]} {b.status} {b.holder or '-'}" for b in state.branches)
        return f"{state.unit:24} CONTESTED  {sides}  — `grimoire team unit resolve {state.unit} --keep <tête>`"
    if state.status in ("invalid", "incomplete"):
        return f"{state.unit:24} {state.status.upper():10} " + "; ".join(state.faults)
    holder = state.holder or "-"
    until = ""
    if state.status == "claimed":
        until = f" jusqu'à {state.expires_at}" + (" (EXPIRÉ : adoptable)" if state.expired(now) else "")
    return f"{state.unit:24} {state.status:10} {holder}{until}  {state.title}"


@unit_app.command("publish")
def publish(
    ctx: typer.Context,
    unit: Annotated[str, typer.Argument(help="Identifiant (minuscules, chiffres, tirets).")],
    title: Annotated[str, typer.Option("--title", help="Ce que l'unité livre.")],
    criterion: Annotated[list[str], typer.Option("--criterion", help="Critère d'acceptation (répétable, au moins un).")],
    project_root: RootOpt = Path(),
    as_: AsOpt = "",
    commit: CommitOpt = False,
) -> None:
    """Propose a unit of work with its acceptance criteria."""
    if not title.strip() or not [c for c in criterion if c.strip()]:
        raise _fail(GrimoireMissionError("publish exige --title et au moins un --criterion"))
    data = {"title": title.strip(), "criteria": [c.strip() for c in criterion if c.strip()]}
    _write(ctx, project_root, unit, "publish", as_, commit, data)


@unit_app.command("claim")
def claim(
    ctx: typer.Context, unit: Annotated[str, typer.Argument()], project_root: RootOpt = Path(),
    as_: AsOpt = "", hours: HoursOpt = tu.DEFAULT_TTL_HOURS, commit: CommitOpt = False,
) -> None:
    """Reserve an open unit (exclusive, with a shared lease)."""
    _write(ctx, project_root, unit, "claim", as_, commit, hours=hours)


@unit_app.command("renew")
def renew(
    ctx: typer.Context, unit: Annotated[str, typer.Argument()], project_root: RootOpt = Path(),
    as_: AsOpt = "", hours: HoursOpt = tu.DEFAULT_TTL_HOURS, commit: CommitOpt = False,
) -> None:
    """Extend your own lease on a unit."""
    _write(ctx, project_root, unit, "renew", as_, commit, hours=hours)


@unit_app.command("release")
def release(
    ctx: typer.Context, unit: Annotated[str, typer.Argument()], project_root: RootOpt = Path(),
    as_: AsOpt = "", commit: CommitOpt = False,
) -> None:
    """Give a unit you hold back to the team."""
    _write(ctx, project_root, unit, "release", as_, commit)


@unit_app.command("adopt")
def adopt(
    ctx: typer.Context, unit: Annotated[str, typer.Argument()], project_root: RootOpt = Path(),
    as_: AsOpt = "", hours: HoursOpt = tu.DEFAULT_TTL_HOURS, commit: CommitOpt = False,
) -> None:
    """Take over a unit whose holder's lease has expired."""
    _write(ctx, project_root, unit, "adopt", as_, commit, hours=hours)


@unit_app.command("deliver")
def deliver(
    ctx: typer.Context,
    unit: Annotated[str, typer.Argument()],
    evidence: Annotated[list[str], typer.Option("--evidence", help="Commit ou fichier du dépôt (répétable).")],
    project_root: RootOpt = Path(),
    as_: AsOpt = "",
    commit: CommitOpt = False,
) -> None:
    """Deliver a unit you hold, with proofs resolved against git."""
    _write(ctx, project_root, unit, "deliver", as_, commit, {"refs": evidence})


@unit_app.command("review")
def review(
    ctx: typer.Context,
    unit: Annotated[str, typer.Argument()],
    accept: Annotated[bool, typer.Option("--accept/--reject", help="Verdict sur la livraison.")],
    project_root: RootOpt = Path(),
    as_: AsOpt = "",
    hours: HoursOpt = tu.DEFAULT_TTL_HOURS,
    commit: CommitOpt = False,
) -> None:
    """Review someone else's delivery: accept closes, reject hands it back."""
    _write(ctx, project_root, unit, "review", as_, commit, {"verdict": "accept" if accept else "reject"}, hours)


@unit_app.command("resolve")
def resolve(
    ctx: typer.Context,
    unit: Annotated[str, typer.Argument()],
    keep: Annotated[str, typer.Option("--keep", help="Tête retenue (préfixe de 6 caractères au moins).")],
    project_root: RootOpt = Path(),
    as_: AsOpt = "",
    commit: CommitOpt = False,
) -> None:
    """Settle a contested unit by naming the branch that wins."""
    _write(ctx, project_root, unit, "resolve", as_, commit, {"keep": keep})


def _states(project_root: Path, unit: str) -> list[tu.UnitState]:
    if not unit:
        return tu.list_units(project_root)
    try:
        state = tu.load_unit(project_root, unit)
    except GrimoireMissionError as exc:
        raise _fail(exc) from exc
    if state is None:
        raise _fail(GrimoireMissionError(f"unité inconnue : {unit}"))
    return [state]


@unit_app.command("list")
def list_command(
    ctx: typer.Context,
    unit: Annotated[str, typer.Argument(help="Une seule unité (défaut : toutes).")] = "",
    project_root: RootOpt = Path(),
) -> None:
    """Show every shared unit and who holds it."""
    now = datetime.now(UTC)
    states = _states(project_root, unit)
    if _json(ctx):
        typer.echo(json.dumps([s.to_dict(now) for s in states], indent=2, ensure_ascii=False))
        return
    if not states:
        typer.echo(f"Aucune unité partagée sous {tu.UNITS_DIR.as_posix()}.")
    for state in states:
        typer.echo(_line(state, now))
        if unit:
            for criterion in state.criteria:
                typer.echo(f"  - critère : {criterion}")
            for proof in state.evidence:
                typer.echo(f"  - preuve : {proof.get('ref')} {proof.get('commit') or proof.get('sha256', '')}")


@unit_app.command("check")
def check(ctx: typer.Context, project_root: RootOpt = Path()) -> None:
    """Exit 1 if a unit is contested, invalid or incomplete (run after `git pull`)."""
    now = datetime.now(UTC)
    bad = [s for s in tu.list_units(project_root) if s.status in ("contested", "invalid", "incomplete")]
    if _json(ctx):
        typer.echo(json.dumps({"ok": not bad, "units": [s.to_dict(now) for s in bad]}, indent=2, ensure_ascii=False))
    else:
        for state in bad:
            typer.echo(_line(state, now))
        typer.echo("Unités partagées cohérentes." if not bad else f"{len(bad)} unité(s) à trancher.")
    if bad:
        raise typer.Exit(1)
