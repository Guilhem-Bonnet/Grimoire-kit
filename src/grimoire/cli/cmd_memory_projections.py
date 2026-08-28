"""``grimoire memory graph`` / ``vector`` — projections Memory OS.

Extrait de :mod:`grimoire.cli.cmd_memory` pour que le module principal repasse
sous son plafond de taille (ratchet R2) — même motif que
:mod:`grimoire.cli.cmd_memory_ops`. Les commandes s'enregistrent sur
``graph_app`` et ``vector_app`` à l'import : importer ce module est ce qui
câble ``memory graph`` et ``memory vector`` dans la CLI, et
``cmd_memory_lexical`` l'importe pour cet effet de bord.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer

from grimoire.cli.cmd_memory import (
    _get_fmt,
    _graph_evidence_opt,
    _graph_exclude_opt,
    _graph_ledger_opt,
    _graph_paths_opt,
    _load_config_context,
    _load_manager_context,
    _load_neo4j_graph,
    _parse_exclude,
    _parse_granularity,
    _parse_paths,
    _vector_granularity_opt,
    _vector_sync_graph_opt,
    console,
    graph_app,
    vector_app,
)

__all__ = ["graph_app", "vector_app"]


def _print_vector_stats(stats: dict[str, Any]) -> None:
    console.print(f"  Vector entries : {stats.get('vector_entries', stats.get('expected', 0))}")
    if "upserted" in stats:
        console.print(f"  Upserted       : {stats['upserted']}")
    if "skipped" in stats:
        console.print(f"  Skipped        : {stats['skipped']}")
    if "code_files" in stats:
        console.print(f"  Code files     : {stats['code_files']}")
    if "code_symbols" in stats:
        console.print(f"  Code symbols   : {stats['code_symbols']}")
    if "code_methods" in stats:
        console.print(f"  Code methods   : {stats['code_methods']}")
    if "code_tests" in stats:
        console.print(f"  Code tests     : {stats['code_tests']}")
    if "code_contracts" in stats:
        console.print(f"  Code contracts : {stats['code_contracts']}")
    if "issues" in stats:
        for issue in cast(list[str], stats["issues"]):
            console.print(f"  [red]-[/red] {issue}")


# ── grimoire memory graph ────────────────────────────────────────────────────


@graph_app.command("sync-code")
def memory_graph_sync_code(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
) -> None:
    """Parse configured code paths and upsert CodeNode/CODE_EDGE data into Neo4j."""
    from grimoire.memory.projections import sync_code_graph_projection

    cfg, root = _load_config_context()
    graph = _load_neo4j_graph(cfg)
    try:
        stats = sync_code_graph_projection(
            graph,
            project_root=root,
            paths=_parse_paths(paths),
            exclude=_parse_exclude(exclude),
        )
    finally:
        graph.close()

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return
    console.print("[green]Code graph synced to Neo4j[/green]")
    console.print(f"  Files : {stats['files']}")
    console.print(f"  Nodes : {stats['code_nodes']}")
    console.print(f"  Edges : {stats['code_edges']}")


@graph_app.command("sync-tasks")
def memory_graph_sync_tasks(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    ledger_root: Path = _graph_ledger_opt,
    evidence_root: Path = _graph_evidence_opt,
) -> None:
    """Upsert mission ledger, task, incident, evidence, and verdict data into Neo4j."""
    from grimoire.evidence.service import EvidenceService
    from grimoire.memory.projections import sync_task_memory_projection
    from grimoire.missions.ledger import MissionLedger

    cfg, root = _load_config_context()
    graph = _load_neo4j_graph(cfg)
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
    try:
        stats = sync_task_memory_projection(
            graph,
            ledger=MissionLedger(ledger_path),
            evidence=EvidenceService(evidence_path),
            project_root=root,
            code_paths=_parse_paths(paths),
            code_exclude=_parse_exclude(exclude),
        )
    finally:
        graph.close()

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return
    console.print("[green]Task memory synced to Neo4j[/green]")
    console.print(f"  Missions : {stats['missions']}")
    console.print(f"  Tasks    : {stats['tasks']}")
    console.print(f"  Events   : {stats['ledger_events']}")
    console.print(f"  Evidence : {stats['evidence_packs']}")
    console.print(f"  Code refs: {stats.get('task_code_links', 0)} task, {stats.get('evidence_code_links', 0)} evidence")


@graph_app.command("verify")
def memory_graph_verify(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    ledger_root: Path = _graph_ledger_opt,
    evidence_root: Path = _graph_evidence_opt,
) -> None:
    """Verify local code/task projection sources are represented in Neo4j."""
    from grimoire.evidence.service import EvidenceService
    from grimoire.memory.projections import graph_projection_verify
    from grimoire.missions.ledger import MissionLedger

    cfg, root = _load_config_context()
    graph = _load_neo4j_graph(cfg)
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
    try:
        stats = graph_projection_verify(
            graph,
            project_root=root,
            code_paths=_parse_paths(paths),
            code_exclude=_parse_exclude(exclude),
            ledger=MissionLedger(ledger_path),
            evidence=EvidenceService(evidence_path),
        )
    finally:
        graph.close()

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
    else:
        status = "[green]OK[/green]" if stats["ok"] else "[red]FAIL[/red]"
        console.print(f"{status} Graph projection verification")
        console.print(f"  Expected: {stats['expected']}")
        console.print(f"  Actual  : {stats['actual']}")
        for issue in cast(list[str], stats["issues"]):
            console.print(f"  [red]-[/red] {issue}")

    if not stats["ok"]:
        raise typer.Exit(1)


# ── grimoire memory vector ───────────────────────────────────────────────────


@vector_app.command("sync-code")
def memory_vector_sync_code(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    granularity: str = _vector_granularity_opt,
    sync_graph: bool = _vector_sync_graph_opt,
) -> None:
    """Upsert deterministic semantic code chunks into the vector backend."""
    from grimoire.memory.projections import sync_code_graph_projection, sync_code_vector_projection

    mgr, _, root = _load_manager_context()
    graph_stats: dict[str, Any] = {"skipped": True}
    if sync_graph and mgr.memory_graph is not None:
        graph_stats = sync_code_graph_projection(
            mgr.memory_graph,
            project_root=root,
            paths=_parse_paths(paths),
            exclude=_parse_exclude(exclude),
        )
    stats = sync_code_vector_projection(
        mgr,
        project_root=root,
        paths=_parse_paths(paths),
        exclude=_parse_exclude(exclude),
        granularity=_parse_granularity(granularity),
    )
    result = {"vector": stats, "graph": graph_stats}

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    console.print("[green]Code vector projection synced[/green]")
    _print_vector_stats(stats)
    if not graph_stats.get("skipped"):
        console.print(f"  Graph nodes    : {graph_stats['code_nodes']}")


@vector_app.command("sync-tasks")
def memory_vector_sync_tasks(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    ledger_root: Path = _graph_ledger_opt,
    evidence_root: Path = _graph_evidence_opt,
    sync_graph: bool = _vector_sync_graph_opt,
) -> None:
    """Upsert deterministic semantic mission/task documents into the vector backend."""
    from grimoire.evidence.service import EvidenceService
    from grimoire.memory.projections import sync_task_memory_projection, sync_task_vector_projection
    from grimoire.missions.ledger import MissionLedger

    mgr, _, root = _load_manager_context()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
    ledger = MissionLedger(ledger_path)
    evidence = EvidenceService(evidence_path)
    graph_stats: dict[str, Any] = {"skipped": True}
    if sync_graph and mgr.memory_graph is not None:
        graph_stats = sync_task_memory_projection(
            mgr.memory_graph,
            ledger=ledger,
            evidence=evidence,
            project_root=root,
            code_paths=_parse_paths(paths),
            code_exclude=_parse_exclude(exclude),
        )
    stats = sync_task_vector_projection(mgr, ledger=ledger, evidence=evidence)
    result = {"vector": stats, "graph": graph_stats}

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    console.print("[green]Task vector projection synced[/green]")
    _print_vector_stats(stats)
    if not graph_stats.get("skipped"):
        console.print(f"  Graph tasks    : {graph_stats['tasks']}")


@vector_app.command("verify")
def memory_vector_verify(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    granularity: str = _vector_granularity_opt,
    ledger_root: Path = _graph_ledger_opt,
    evidence_root: Path = _graph_evidence_opt,
) -> None:
    """Verify code/task vector projections exist and match current content hashes."""
    from grimoire.evidence.service import EvidenceService
    from grimoire.memory.projections import (
        build_code_vector_entries,
        build_task_vector_entries,
        vector_projection_verify,
    )
    from grimoire.missions.ledger import MissionLedger

    mgr, _, root = _load_manager_context()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
    expected = [
        *build_code_vector_entries(
            root,
            _parse_paths(paths),
            exclude=_parse_exclude(exclude),
            granularity=_parse_granularity(granularity),
        ),
        *build_task_vector_entries(MissionLedger(ledger_path), evidence=EvidenceService(evidence_path)),
    ]
    stats = vector_projection_verify(mgr, expected)

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
    else:
        status = "[green]OK[/green]" if stats["ok"] else "[red]FAIL[/red]"
        console.print(f"{status} Vector projection verification")
        console.print(f"  Expected: {stats['expected']}")
        console.print(f"  Actual  : {stats['actual']}")
        for issue in cast(list[str], stats["issues"]):
            console.print(f"  [red]-[/red] {issue}")

    if not stats["ok"]:
        raise typer.Exit(1)
