"""Composition of the agent freshness rule for `doctor`, `registry`, and the cockpit.

Issue #396's stopping criterion: a delivered or overridden agent that no
`agent.dispatch` trace has named in *threshold_days* is signalled, never
removed. :mod:`grimoire.traces.ledger` computes that verdict from raw ledger
data (:func:`grimoire.traces.ledger.compute_agent_freshness`); this module
gathers the two remaining ingredients — the project's known agent names and
its configured threshold — so `grimoire doctor`, `grimoire registry
dispatches`, and the cockpit's agent view all run the exact same check
instead of three slightly different ones.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.config import AgentsConfig, GrimoireConfig
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.traces.ledger import FreshnessReport, TraceLedger

__all__ = [
    "format_freshness_detail",
    "known_agent_names",
    "project_agent_freshness",
    "resolve_freshness_threshold",
]


def resolve_freshness_threshold(cfg: GrimoireConfig | None) -> int:
    """The project's configured threshold, or the documented default when unset.

    Reads the same default (90) as :class:`AgentsConfig`'s field default —
    delegated rather than duplicated as a literal, so the two can never
    silently drift apart.
    """
    if cfg is None:
        return AgentsConfig().freshness_threshold_days
    return cfg.agents.freshness_threshold_days


def known_agent_names(project_root: Path) -> list[str]:
    """Every agent name the project can dispatch to — kit-delivered or overridden.

    Reuses :func:`grimoire.hosts.collect.collect_agents`, the same reading
    `grimoire host status`/`sync` and the cockpit's agent view already use,
    so "known agent" means the same thing everywhere the freshness rule is
    surfaced.
    """
    from grimoire.hosts import collect

    root = project_root.resolve()
    skills = collect.collect_skills(root)
    agents = collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    return [a.name for a in agents]


def project_agent_freshness(project_root: Path, cfg: GrimoireConfig | None) -> FreshnessReport:
    """The freshness verdict for every known agent of *project_root*."""
    threshold = resolve_freshness_threshold(cfg)
    ledger = TraceLedger(project_root.resolve() / TRACES_DIR)
    return ledger.agent_freshness_report(known_agent_names(project_root), threshold_days=threshold)


def format_freshness_detail(report: FreshnessReport) -> tuple[str, str]:
    """Render *report* as a ``(level, message)`` pair for CLI/doctor display.

    *level* is one of ``"ok"`` | ``"info"`` | ``"warn"`` — never ``"fail"``:
    this rule is a debt signal, not a health check, and the issue explicitly
    refuses making it block anything.
    """
    if not report.judged:
        span = report.journal_span_days
        if span is None:
            return "info", (
                f"Aucun historique de choix d'agent — fraîcheur non évaluée (seuil {report.threshold_days} j)."
            )
        return "info", (
            f"Journal de {span} j d'historique, insuffisant pour juger au seuil de "
            f"{report.threshold_days} j — aucun agent jugé périmé faute de données."
        )

    stale = report.stale_entries
    if not stale:
        return "ok", f"Aucun agent sans invocation depuis {report.threshold_days} j."

    parts = [
        f"{e.name} (jamais)" if e.last_seen is None else f"{e.name} (il y a {e.days_since} j)" for e in stale
    ]
    return "warn", (
        f"{len(stale)} agent(s) sans invocation depuis {report.threshold_days} j : " + ", ".join(parts)
    )
