"""CLI ``grimoire autonomy`` — octroi d'autonomie à périmètre, coût et durée, révocable (W5-06).

La logique vit dans :mod:`grimoire.flows.autonomy` ; ce module présente ce
qu'elle rend et traduit ses refus en code de sortie : ``0`` octroi inscrit ou
lecture faite, ``1`` demande invalide, ``3`` décision humaine requise (le
challenge reste ouvert : un tour humain de cette session, puis la même
commande relancée).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.attestation import ATTEST_FROM_HINT, Attestation
from grimoire.flows.autonomy import COST_SCOPE, REVOKE_MANUAL, GrantJournal, GrantRequest
from grimoire.hosts.decisions.authority_guard import LIMIT_NOTE

autonomy_app = typer.Typer(
    help="Octroyer, relire ou révoquer l'autonomie d'un flow governed (périmètre, coût, durée).",
    no_args_is_help=True,
)

console = Console(stderr=True)

_FLOWS_RELPATH = Path("_grimoire-runtime-output/flows")
_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet cible.")]
_ATTEST_FROM = Annotated[
    Path | None,
    typer.Option(
        "--attest-from",
        help="Racine du projet où tourne ta session : son journal des tours humains atteste l'octroi "
        "(session ouverte dans un autre projet que la cible). Jamais déduit du dossier courant.",
    ),
]
#: Décision humaine requise : distinct d'une erreur de saisie.
_AWAITING_HUMAN_EXIT = 3


def _fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def _journal(project_root: Path) -> GrantJournal:
    root = project_root.resolve()
    return GrantJournal(root / _FLOWS_RELPATH, root)


def _emit(ctx: typer.Context, payload: dict[str, Any]) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _hint(attestation: Attestation, attest_from: Path | None) -> str:
    """L'orientation vers ``--attest-from`` d'un refus ``human_authority_unprovable`` qui ne l'a pas utilisé."""
    return ATTEST_FROM_HINT if attestation.reason == "human_authority_unprovable" and attest_from is None else ""


def _awaiting(ctx: typer.Context, attestation: Attestation, message: str, attest_from: Path | None) -> None:
    hint = _hint(attestation, attest_from)
    payload: dict[str, Any] = {
        "granted": False,
        "challenge_id": attestation.challenge_id,
        "attestation": attestation.to_dict(),
    }
    _emit(ctx, {**payload, "hint": hint} if hint else payload)
    if _fmt(ctx) != "json":
        console.print(f"[yellow]décision humaine requise[/yellow] — challenge {attestation.challenge_id}")
        console.print(f"  {message}")
        if hint:
            console.print(f"  {hint}")
    raise typer.Exit(_AWAITING_HUMAN_EXIT)


def _fail(ctx: typer.Context, exc: GrimoireRuntimeError) -> None:
    if _fmt(ctx) == "json":
        typer.echo(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
    else:
        console.print(f"[red]✗[/red] {exc}")
    raise typer.Exit(1) from exc


@autonomy_app.command("grant")
def autonomy_grant(
    ctx: typer.Context,
    scope: Annotated[
        list[str],
        typer.Option("--scope", help="run:<id> ou blueprint:<id>, '/<node>' en option ; répétable, obligatoire."),
    ],
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Plafond de coût de l'octroi, en USD.")],
    until: Annotated[
        str, typer.Option("--until", help="Échéance : +30m, +2h, +1d ou un instant ISO 8601 avec fuseau.")
    ],
    project_root: _PROJECT_ROOT = Path(),
    attest_from: _ATTEST_FROM = None,
) -> None:
    """Demander un octroi : il n'est inscrit qu'attesté par un tour humain de cette session."""
    try:
        request = GrantRequest.build(scope, max_cost_usd, until)
        grant, attestation = _journal(project_root).request(request, attest_from=attest_from)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    if grant is None:
        message = (
            f"awaiting_human : {attestation.reason} — {attestation.explain()}. Octroi d'autonomie : il faut un tour "
            "humain de la session qui l'a demandé, postérieur au challenge, puis la même commande relancée depuis "
            f"cette session. {LIMIT_NOTE}"
        )
        _awaiting(ctx, attestation, message, attest_from)
        return
    _emit(ctx, {"granted": True, "grant": grant, "cost_scope": COST_SCOPE})
    if _fmt(ctx) != "json":
        console.print(
            f"[green]octroi[/green] {grant['grant_id']} — {', '.join(grant['scope'])}, "
            f"plafond {grant['max_cost_usd']:.4f} USD, jusqu'au {grant['until']} ({grant['attestation']}"
            + (f", depuis {grant['attest_from']}" if grant.get("attest_from") else "")
            + ")"
        )
        console.print(f"  [dim]{COST_SCOPE}[/dim]")


@autonomy_app.command("status")
def autonomy_status(ctx: typer.Context, project_root: _PROJECT_ROOT = Path()) -> None:
    """Relire chaque octroi, réévalué maintenant (une révocation constatée est inscrite)."""
    journal = _journal(project_root)
    states = journal.states()
    _emit(ctx, {"grants": [s.to_dict() for s in states], "events": journal.events(), "cost_scope": COST_SCOPE})
    if _fmt(ctx) == "json":
        return
    if not states:
        console.print("aucun octroi d'autonomie")
        return
    for state in states:
        label = "[green]actif[/green]" if state.active else f"[red]révoqué[/red] ({state.revoked} : {state.explain()})"
        console.print(
            f"{state.grant_id} — {label} — {', '.join(str(s) for s in state.scope)} — "
            f"dépensé {state.spent.render()} / {float(state.grant.get('max_cost_usd') or 0):.4f} USD — "
            f"jusqu'au {state.grant.get('until')}"
        )
    console.print(f"[dim]{COST_SCOPE}[/dim]")


@autonomy_app.command("revoke")
def autonomy_revoke(
    ctx: typer.Context,
    grant_id: Annotated[str, typer.Argument(help="Identifiant de l'octroi (GRT-…).")],
    reason: Annotated[str, typer.Option("--reason", help="Motif, inscrit dans l'événement.")] = "",
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Révoquer un octroi actif — toujours permis : retirer une autorité n'en demande aucune."""
    journal = _journal(project_root)
    state = next((s for s in journal.states() if s.grant_id == grant_id), None)
    if state is None:
        _fail(ctx, GrimoireRuntimeError(f"octroi inconnu : {grant_id}"))
        return
    if not state.active:
        _fail(ctx, GrimoireRuntimeError(f"octroi {grant_id} déjà révoqué ({state.revoked} : {state.explain()})"))
        return
    event = journal.revoke(grant_id, REVOKE_MANUAL, reason)
    _emit(ctx, {"revoked": event})
    if _fmt(ctx) != "json":
        console.print(f"[green]révoqué[/green] {grant_id}" + (f" — {reason}" if reason else ""))


# ── Mandat de l'orchestrateur (#876, lot 1) ─────────────────────────────────

mandate_app = typer.Typer(
    help="Mandat de l'orchestrateur : gestes de validation qu'il peut prendre à ta place, bornés et révocables.",
    no_args_is_help=True,
)
autonomy_app.add_typer(mandate_app, name="mandate")


def _store(project_root: Path) -> Any:
    from grimoire.missions.mandate import MandateStore

    return MandateStore(project_root)


@mandate_app.command("grant")
def mandate_grant(
    ctx: typer.Context,
    gesture: Annotated[
        list[str], typer.Option("--gesture", help="Geste couvert : accept, answer, approve-check, resume, queue-release, merge ; répétable.")
    ],
    task_type: Annotated[list[str], typer.Option("--task-type", help="Type de tâche couvert (implementation, test…) ; répétable.")],
    max_sessions_per_day: Annotated[int, typer.Option("--max-sessions-per-day", help="Plafond de sessions de worker par jour.")],
    until: Annotated[str, typer.Option("--until", help="Échéance : +30m, +2h, +1d ou un instant ISO 8601 avec fuseau.")],
    project_root: _PROJECT_ROOT = Path(),
    attest_from: _ATTEST_FROM = None,
) -> None:
    """Demander un mandat : il n'est inscrit qu'attesté par un tour humain de cette session (comme `autonomy grant`)."""
    from grimoire.missions.mandate import MandateRequest

    try:
        request = MandateRequest.build(gesture, task_type, max_sessions_per_day, until)
        mandate, attestation = _store(project_root).request_grant(request, attest_from=attest_from)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    except Exception as exc:  # MandateRefusedError : demande invalide
        _fail(ctx, GrimoireRuntimeError(str(exc)))
        return
    if mandate is None:
        message = (
            f"awaiting_human : {attestation.reason} — {attestation.explain()}. Un mandat s'accorde par un tour humain de la "
            f"session qui l'a demandé, postérieur au challenge, puis la même commande relancée. {LIMIT_NOTE}"
        )
        _awaiting(ctx, attestation, message, attest_from)
        return
    _emit(ctx, {"granted": True, "mandate": mandate.to_dict()})
    if _fmt(ctx) != "json":
        console.print(
            f"[green]mandat[/green] {mandate.id} — {', '.join(mandate.gestures)} sur {', '.join(mandate.task_types)}, "
            f"{mandate.max_sessions_per_day} session(s)/jour, jusqu'au {mandate.until}"
            + (f", attesté depuis {mandate.attest_from}" if mandate.attest_from else "")
        )


@mandate_app.command("status")
def mandate_status(ctx: typer.Context, project_root: _PROJECT_ROOT = Path()) -> None:
    """Relire l'état du mandat du projet, réévalué maintenant."""
    state = _store(project_root).state()
    _emit(ctx, state.to_dict())
    if _fmt(ctx) == "json":
        return
    if state.mandate is None:
        console.print(f"aucun mandat actif ({state.reason}{' : ' + state.detail if state.detail else ''})")
        return
    label = "[green]actif[/green]" if state.active else f"[red]inactif[/red] ({state.reason} : {state.detail})"
    console.print(f"{state.mandate.id} — {label} — {', '.join(state.mandate.gestures)} — jusqu'au {state.mandate.until}")
    cause = state.revocation
    if cause:
        # #982 : ce qui a révoqué — l'heure, la session, l'outil, le programme lancé et la zone visée (aucun argument).
        who = f" par la session {cause['session_id']}" if cause.get("session_id") else ""
        tool = f" ({cause['tool']})" if cause.get("tool") else ""
        console.print(f"révoqué le {cause.get('ts', '?')}{who}{tool}", markup=False, highlight=False)
        if cause.get("program") or cause.get("target"):
            shown = " → ".join(v for v in (cause.get("program", ""), cause.get("target", "")) if v)
            console.print(f"  cause : {shown}", markup=False, highlight=False)


@mandate_app.command("revoke")
def mandate_revoke(
    ctx: typer.Context,
    reason: Annotated[str, typer.Option("--reason", help="Motif, inscrit dans l'événement.")] = "",
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Révoquer le mandat actif — toujours permis : retirer une autorité n'en demande aucune."""
    revoked = _store(project_root).revoke(REVOKE_MANUAL, reason)
    if revoked is None:
        _fail(ctx, GrimoireRuntimeError("aucun mandat actif à révoquer"))
        return
    _emit(ctx, {"revoked": revoked.to_dict()})
    if _fmt(ctx) != "json":
        console.print(f"[green]révoqué[/green] {revoked.id}" + (f" — {reason}" if reason else ""))


# ── Pilote automatique (P82) ────────────────────────────────────────────────

autopilot_app = typer.Typer(
    help="Pilote automatique : l'orchestrateur tranche aussi les cas bloqués, sauf une liste fermée réservée à l'humain.",
    no_args_is_help=True,
)
autonomy_app.add_typer(autopilot_app, name="autopilot")


def _autopilot_switch(ctx: typer.Context, on: bool, project_root: Path, attest_from: Path | None) -> None:
    from grimoire.missions.autopilot import AutopilotStore

    try:
        entry, attestation = AutopilotStore(project_root).request(on, attest_from=attest_from)
    except GrimoireRuntimeError as exc:
        _fail(ctx, exc)
        return
    except Exception as exc:  # MandateRefusedError : pas de mandat actif, politique invalide, journal rompu
        _fail(ctx, GrimoireRuntimeError(str(exc)))
        return
    if entry is None:
        message = (
            f"awaiting_human : {attestation.reason} — {attestation.explain()}. Le pilote automatique ne s'active et ne "
            f"se coupe que par un tour humain de la session qui le demande, postérieur au challenge, puis la même "
            f"commande relancée. {LIMIT_NOTE}"
        )
        _awaiting(ctx, attestation, message, attest_from)
        return
    _emit(ctx, {"autopilot": entry["state"], "mandate_id": entry["mandate_id"], "ts": entry["ts"]})
    if _fmt(ctx) != "json":
        console.print(f"[green]pilote automatique {'activé' if on else 'coupé'}[/green] — mandat {entry['mandate_id'] or '—'}")


@autopilot_app.command("on")
def autopilot_on(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), attest_from: _ATTEST_FROM = None) -> None:
    """Activer le pilote (il exige un mandat actif) : inscrit seulement attesté par un tour humain de cette session."""
    _autopilot_switch(ctx, True, project_root, attest_from)


@autopilot_app.command("off")
def autopilot_off(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), attest_from: _ATTEST_FROM = None) -> None:
    """Couper le pilote : même canal humain que l'activation (révoquer le mandat le coupe aussi, sans tour humain)."""
    _autopilot_switch(ctx, False, project_root, attest_from)


@autopilot_app.command("status")
def autopilot_status(ctx: typer.Context, project_root: _PROJECT_ROOT = Path()) -> None:
    """Relire l'état du pilote, réévalué maintenant (mandat compris)."""
    from grimoire.missions.autopilot import AutopilotStore, load_autopilot_policy

    state = AutopilotStore(project_root).state()
    policy = load_autopilot_policy(project_root.resolve())
    _emit(ctx, {**state.to_dict(), "policy_error": policy.error, "extra_reserved": list(policy.extra)})
    if _fmt(ctx) == "json":
        return
    if state.active:
        console.print(f"[green]pilote actif[/green] — mandat {state.mandate_id}, depuis {state.since} ({state.via})")
    else:
        console.print(f"pilote inactif ({state.reason})")
    if policy.error:
        console.print(f"[red]politique du pilote invalide[/red] : {policy.error}", markup=True)
