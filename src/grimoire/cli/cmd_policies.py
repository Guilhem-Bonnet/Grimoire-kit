"""CLI ``grimoire policies`` — visibilité sur les politiques temporelles (issue #429, point 3).

``status`` répond à « où en est la session courante vis-à-vis des règles
déclarées dans ``_grimoire/standard/policies.yaml`` ? » : compteurs
(appels, écritures, coût), budgets restants, approbations déjà accordées,
état de refroidissement. Le cockpit n'est pas dans ce lot — cette commande
est la seule surface de visibilité livrée ici.

Une session n'a pas d'identifiant côté CLI (un hook en reçoit un dans son
payload JSON ; un humain qui tape ``grimoire policies status`` n'en a pas).
La résolution est donc, dans l'ordre : ``--session-id`` explicite,
``GRIMOIRE_SESSION_ID`` (si un host l'exporte dans l'environnement du
terminal), sinon le fichier ``_grimoire-output/.runs/session-*.json`` le plus
récemment modifié — l'hypothèse raisonnable étant que l'utilisateur regarde
l'état de la session qui vient de tourner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.policies.rules_config import load_custom_rules
from grimoire.policies.session_state import RuleState, SessionState, load_session_state, session_state_path

policies_app = typer.Typer(
    help="Politiques temporelles : budgets, approbation préalable, refroidissement par session.",
    no_args_is_help=True,
)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_SESSION_ID_OPTION = typer.Option("--session-id", help="Session à inspecter (sinon : la plus récente).")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")


def _most_recent_session_id(root: Path) -> str | None:
    runs_dir = root / "_grimoire-output" / ".runs"
    try:
        candidates = sorted(
            runs_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return None
    if not candidates:
        return None
    name = candidates[0].stem  # "session-<id>"
    return name.removeprefix("session-")


def _resolve_session_id(root: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    import os

    env_id = os.environ.get("GRIMOIRE_SESSION_ID", "").strip()
    if env_id:
        return env_id
    return _most_recent_session_id(root)


def _budget_line(name: str, used: float, limit: float | None, *, unit: str = "") -> str:
    """Plain text, no rich markup: this string is shared verbatim by the
    table renderer and the ``--json`` output, and markup in JSON would be a
    display artefact leaking into a machine-readable field."""
    if limit is None:
        return f"{name}: {used:g}{unit} (pas de plafond)"
    remaining = max(0.0, limit - used)
    marker = "atteint" if used >= limit else f"reste {remaining:g}{unit}"
    return f"{name}: {used:g}/{limit:g}{unit} ({marker})"


def _rule_row(rule_id: str, rule_by_id: dict[str, Any], state: RuleState) -> dict[str, Any]:
    rule = rule_by_id.get(rule_id)
    lines: list[str] = []
    if rule is not None and rule.per_session is not None:
        budget = rule.per_session
        if budget.max_tool_calls is not None:
            lines.append(_budget_line("appels", state.calls, budget.max_tool_calls))
        if budget.max_writes is not None:
            lines.append(_budget_line("écritures", state.writes, budget.max_writes))
        if budget.max_cost_usd is not None:
            lines.append(_budget_line("coût", state.cost_usd, budget.max_cost_usd, unit=" $"))
    if rule is not None and rule.require_approval:
        lines.append("approuvée cette session" if state.approved else "pas encore demandée")
    if rule is not None and rule.cooldown_after is not None:
        lines.append(f"{len(state.hits)} occurrence(s) enregistrée(s)")
    return {
        "rule_id": rule_id,
        "calls": state.calls,
        "writes": state.writes,
        "cost_usd": state.cost_usd,
        "approved": state.approved,
        "hits": len(state.hits),
        "summary": "; ".join(lines) or "—",
    }


@policies_app.command("status")
def policies_status(
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    session_id: Annotated[str | None, _SESSION_ID_OPTION] = None,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Compteurs et budgets restants de la session courante (ou désignée)."""
    root = project_root.resolve()
    try:
        rules = load_custom_rules(root)
    except GrimoirePolicyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    resolved_id = _resolve_session_id(root, session_id)
    if resolved_id is None:
        if json_output:
            console.print_json(data={"session_id": None, "rules": []})
        else:
            console.print("[dim]Aucune session temporelle trouvée (aucun fichier sous _grimoire-output/.runs/).[/dim]")
        return

    state: SessionState = load_session_state(root, resolved_id, now_iso="")
    rule_by_id = {rule.id: rule for rule in rules if rule.is_temporal}
    rows = [_rule_row(rule_id, rule_by_id, rule_state) for rule_id, rule_state in state.rules.items()]
    # Une règle temporelle déclarée mais jamais encore invoquée cette
    # session : compteurs à zéro, pour que son budget reste visible.
    for rule_id in rule_by_id:
        if rule_id not in state.rules:
            rows.append(_rule_row(rule_id, rule_by_id, RuleState()))

    if json_output:
        console.print_json(
            data={
                "session_id": resolved_id,
                "started_at": state.started_at,
                "path": str(session_state_path(root, resolved_id)),
                "rules": rows,
            }
        )
        return

    console.print(f"Session [bold]{resolved_id}[/bold] — démarrée {state.started_at or '?'}")
    if not rows:
        console.print("[dim]Aucune règle temporelle déclarée ou invoquée pour l'instant.[/dim]")
        return
    table = Table()
    table.add_column("Règle")
    table.add_column("Appels", justify="right")
    table.add_column("Écritures", justify="right")
    table.add_column("Coût ($)", justify="right")
    table.add_column("État")
    for row in rows:
        table.add_row(
            row["rule_id"],
            str(row["calls"]),
            str(row["writes"]),
            f"{row['cost_usd']:g}",
            row["summary"],
        )
    console.print(table)
