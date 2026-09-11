"""Composition of the agent freshness rule for `doctor`, `registry`, and the cockpit.

Issue #396's stopping criterion: a delivered or overridden agent that no
`agent.dispatch` trace has named in *threshold_days* is signalled, never
removed. :mod:`grimoire.traces.ledger` computes that verdict from raw ledger
data (:func:`grimoire.traces.ledger.compute_agent_freshness`); this module
gathers the remaining ingredients — the project's known agent names, each
agent's own age, and its configured threshold — so `grimoire doctor`,
`grimoire registry dispatches`, and the cockpit's agent view all run the
exact same check instead of three slightly different ones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.config import AgentsConfig, GrimoireConfig
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.traces.ledger import FreshnessReport, TraceLedger

__all__ = [
    "format_freshness_detail",
    "known_agent_ages",
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


def known_agent_ages(project_root: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Age in days of each known agent's own definition file.

    The floor the freshness rule needs at the per-agent level (issue #396's
    follow-up): "absence of data is not absence of usage" holds for the
    journal as a whole (see :func:`grimoire.traces.ledger.compute_agent_freshness`'s
    ``judged``), but it also holds for a single agent. An override created
    yesterday, or an agent newly delivered by a kit upgrade, has not had
    ``threshold_days`` to be chosen yet — reading its absence from the
    journal as "nobody wants it" would repeat the exact mistake the issue
    already refuses, one level down.

    A separate traversal from :func:`known_agent_names` on purpose: the two
    read different things (names vs. mtimes) from the same
    ``collect_agents`` call, and keeping them independent means a caller
    that only cares about names (or stubs it in a test) never has to reason
    about file timestamps it did not ask for. An agent whose file cannot be
    stat'd (deleted mid-read, permission error) is simply absent from the
    returned mapping — :func:`~grimoire.traces.ledger.compute_agent_freshness`
    treats a missing entry as "age unknown", which judges that agent
    normally rather than either forcing or blocking staleness on a fact we
    could not establish.
    """
    from grimoire.hosts import collect

    now = now or datetime.now(tz=UTC)
    root = project_root.resolve()
    skills = collect.collect_skills(root)
    agents = collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    ages: dict[str, int] = {}
    for agent in agents:
        path = root / agent.definition_ref
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        modified_at = datetime.fromtimestamp(mtime, tz=UTC)
        ages[agent.name] = max(0, (now - modified_at).days)
    return ages


def project_agent_freshness(project_root: Path, cfg: GrimoireConfig | None) -> FreshnessReport:
    """The freshness verdict for every known agent of *project_root*."""
    threshold = resolve_freshness_threshold(cfg)
    ledger = TraceLedger(project_root.resolve() / TRACES_DIR)
    return ledger.agent_freshness_report(
        known_agent_names(project_root),
        agent_ages=known_agent_ages(project_root),
        threshold_days=threshold,
    )


def format_freshness_detail(report: FreshnessReport) -> tuple[str, str]:
    """Render *report* as a ``(level, message)`` pair for CLI/doctor display.

    *level* is one of ``"ok"`` | ``"info"`` | ``"warn"`` — never ``"fail"``:
    this rule is a debt signal, not a health check, and the issue explicitly
    refuses making it block anything.

    An agent flagged ``too_recent`` (its own definition file is younger than
    the threshold) is never counted among the stale ones, but it is named
    separately so a reader is not left wondering why an agent with no
    recorded dispatch is missing from the WARN list.
    """
    too_recent = report.too_recent_entries
    too_recent_note = ""
    if too_recent:
        names = ", ".join(e.name for e in too_recent)
        too_recent_note = f" — trop récent(s) pour juger : {names}"

    if not report.judged:
        span = report.journal_span_days
        if span is None:
            return "info", (
                f"Aucun historique de choix d'agent — fraîcheur non évaluée (seuil {report.threshold_days} j)."
                + too_recent_note
            )
        return "info", (
            f"Journal de {span} j d'historique, insuffisant pour juger au seuil de "
            f"{report.threshold_days} j — aucun agent jugé périmé faute de données." + too_recent_note
        )

    stale = report.stale_entries
    if not stale:
        return "ok", f"Aucun agent sans invocation depuis {report.threshold_days} j." + too_recent_note

    parts = [
        f"{e.name} (jamais)" if e.last_seen is None else f"{e.name} (il y a {e.days_since} j)" for e in stale
    ]
    return "warn", (
        f"{len(stale)} agent(s) sans invocation depuis {report.threshold_days} j : "
        + ", ".join(parts)
        + too_recent_note
    )
