"""Rendering for ``grimoire registry dispatches`` — kept out of ``app.py``.

``app.py`` is grandfathered above the code-size ratchet's threshold
(``scripts/check-code-ratchet.py``, R2): it may only shrink, never grow.
Adding the delegation report (#657) to the existing
choices/misses/freshness output would have appended lines there instead —
this module holds the render logic so ``app.py`` keeps only the thin command
function that gathers the data and calls :func:`render`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from grimoire.traces.ledger import FreshnessReport


def render(
    console: Console,
    *,
    fmt: str,
    counts: dict[str, dict[str, Any]],
    misses: dict[str, dict[str, Any]],
    delegations: dict[str, dict[str, Any]],
    bursts: dict[str, Any],
    freshness: FreshnessReport | None,
    freshness_payload: dict[str, Any] | None,
) -> None:
    """Render the choices/misses/delegations/freshness report gathered by ``registry_dispatches``."""
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "dispatches": counts,
                    "misses": misses,
                    "delegations": delegations,
                    "bursts": bursts,
                    "freshness": freshness_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not counts:
        console.print("[yellow]Aucun choix d'agent enregistré.[/yellow]")
    else:
        tbl = Table(title="Choix d'agent observés")
        tbl.add_column("Agent", style="bold")
        tbl.add_column("Occurrences", justify="right")
        tbl.add_column("Dernier choix")

        for agent_id, stats in sorted(counts.items(), key=lambda kv: kv[1]["count"], reverse=True):
            tbl.add_row(agent_id, str(stats["count"]), stats["last_seen"] or "—")

        console.print(tbl)

    if not misses:
        console.print("[yellow]Aucun non-choix enregistré.[/yellow]")
    else:
        miss_tbl = Table(title="Non-choix observés — spécialité manquante")
        miss_tbl.add_column("Spécialité", style="bold")
        miss_tbl.add_column("Occurrences", justify="right")
        miss_tbl.add_column("Dernier non-choix")

        for specialty, stats in sorted(misses.items(), key=lambda kv: kv[1]["count"], reverse=True):
            miss_tbl.add_row(specialty, str(stats["count"]), stats["last_seen"] or "—")

        console.print(miss_tbl)

    if delegations:
        # Issue #657 : la délégation observée (agent, modèle),
        # dans le même rapport que les choix de persona d'entrée ci-dessus —
        # même journal, même commande, pas un second endroit à consulter.
        deleg_tbl = Table(title="Délégations vers un sous-agent observées")
        deleg_tbl.add_column("Agent délégué", style="bold")
        deleg_tbl.add_column("Occurrences", justify="right")
        deleg_tbl.add_column("Avec modèle explicite", justify="right")
        deleg_tbl.add_column("Dernier modèle")
        deleg_tbl.add_column("Dernière délégation")

        for agent_id, stats in sorted(delegations.items(), key=lambda kv: kv[1]["count"], reverse=True):
            deleg_tbl.add_row(
                agent_id,
                str(stats["count"]),
                f"{stats['with_model_count']}/{stats['count']}",
                stats["last_model"] or "—",
                stats["last_seen"] or "—",
            )

        console.print(deleg_tbl)

    if bursts["sessions_with_delegation"]:
        # #687 : mesure de la règle « plusieurs sous-agents dans un seul message ».
        console.print(
            f"Rafales de délégation (>= 2 en {bursts['window_seconds']:g} s, par session) : "
            f"{bursts['sessions_with_burst']}/{bursts['sessions_with_delegation']} sessions avec délégation, "
            f"{bursts['burst_count']} rafale(s), taille moyenne {bursts['mean_burst_size']:g}."
        )
        if bursts["sessions_timing_approx"]:
            console.print(
                f"[yellow]{bursts['sessions_timing_approx']} session(s) avec délégation datée à sa fin "
                "(durée absente) : rafales possiblement sous-estimées.[/yellow]"
            )
    else:
        console.print("[dim]Rafales de délégation : aucune session avec délégation datée.[/dim]")

    console.print()
    if freshness is None:
        console.print("[dim]Fraîcheur des agents : non évaluée (liste des agents indisponible).[/dim]")
    elif not freshness.judged:
        span = freshness.journal_span_days
        if span is None:
            console.print(
                f"[dim]Fraîcheur des agents : aucun historique — non évaluée (seuil {freshness.threshold_days} j).[/dim]"
            )
        else:
            console.print(
                f"[dim]Fraîcheur des agents : journal de {span} j, insuffisant pour le seuil de "
                f"{freshness.threshold_days} j — non évaluée.[/dim]"
            )
    elif not freshness.stale_entries:
        console.print(f"[green]Fraîcheur des agents : aucun agent sans invocation depuis {freshness.threshold_days} j.[/green]")
    else:
        never = [e.name for e in freshness.stale_entries if e.last_seen is None]
        if never:
            console.print(f"[yellow]Agents jamais choisis (seuil {freshness.threshold_days} j) : {', '.join(never)}[/yellow]")
        stale_seen = [e for e in freshness.stale_entries if e.last_seen is not None]
        if stale_seen:
            parts = ", ".join(f"{e.name} (il y a {e.days_since} j)" for e in stale_seen)
            console.print(f"[yellow]Agents sans invocation récente (seuil {freshness.threshold_days} j) : {parts}[/yellow]")

    if freshness is not None and freshness.too_recent_entries:
        names = ", ".join(e.name for e in freshness.too_recent_entries)
        console.print(f"[dim]Agents trop récents pour juger (seuil {freshness.threshold_days} j) : {names}[/dim]")
