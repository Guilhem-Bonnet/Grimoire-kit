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


#: Destinations de ``memory graph sync-code`` : ``neo4j`` est le graphe mémoire (défaut,
#: celui que lisent ``verify`` et la jauge de fraîcheur) ; ``codegraph-neo4j`` pousse le
#: même graphe de code par ``Neo4jCodeGraphBackend`` (requêtes d'impact Cypher), sans
#: toucher aux nœuds de mémoire.
CODEGRAPH_BACKENDS = ("neo4j", "codegraph-neo4j")

_graph_backend_opt = typer.Option(
    "neo4j",
    "--backend",
    help="Destination du graphe de code : " + " | ".join(CODEGRAPH_BACKENDS) + ".",
)


def _load_codegraph_backend(cfg: Any) -> Any:
    """Instancie ``Neo4jCodeGraphBackend`` avec les mêmes accès que le graphe mémoire."""
    import os

    from grimoire.codegraph.backends import neo4j as codegraph_neo4j

    if not cfg.memory.neo4j_uri:
        console.print("[red]Missing Neo4j URI.[/red] Configure memory.neo4j_uri.")
        raise typer.Exit(2)
    password = os.environ.get(cfg.memory.neo4j_password_env, "")
    if not password:
        console.print(f"[red]Missing Neo4j password env:[/red] {cfg.memory.neo4j_password_env}")
        raise typer.Exit(2)
    try:
        return codegraph_neo4j.Neo4jCodeGraphBackend(
            cfg.memory.neo4j_uri,
            auth=(cfg.memory.neo4j_user, password),
            database=cfg.memory.neo4j_database,
        )
    except ImportError as exc:
        console.print(f"[red]Neo4j graph unavailable:[/red] {exc}")
        raise typer.Exit(2) from None
    except Exception as exc:
        console.print(f"[red]Neo4j graph unavailable:[/red] {exc}")
        raise typer.Exit(1) from None


def _sync_code_via_codegraph_backend(cfg: Any, root: Path, paths: str, exclude: str) -> dict[str, Any]:
    from grimoire.memory.projections import build_code_graph

    backend = _load_codegraph_backend(cfg)
    try:
        graph = build_code_graph(root, _parse_paths(paths), exclude=_parse_exclude(exclude) or None)
        pushed = backend.push_graph(graph)
    finally:
        backend.close()
    stats = graph.stats()
    return {"files": stats["files"], "code_nodes": pushed["nodes"], "code_edges": pushed["edges"]}


@graph_app.command("sync-code")
def memory_graph_sync_code(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    backend: str = _graph_backend_opt,
) -> None:
    """Parse configured code paths and upsert CodeNode/CODE_EDGE data into Neo4j."""
    from grimoire.memory.projections import sync_code_graph_projection

    if backend not in CODEGRAPH_BACKENDS:
        console.print(f"[red]Backend inconnu :[/red] {backend} (attendu : {', '.join(CODEGRAPH_BACKENDS)})")
        raise typer.Exit(2)

    cfg, root = _load_config_context()
    if backend == "codegraph-neo4j":
        stats = _sync_code_via_codegraph_backend(cfg, root, paths, exclude)
    else:
        graph = _load_neo4j_graph(cfg, ensure_schema=True)
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


_backfill_collection_opt = typer.Option(
    [], "--collection", help="Collection Weaviate du projet (répétable). Défaut : celle de la configuration.",
)
_backfill_apply_opt = typer.Option(False, "--apply", help="Écrire. Sans ce drapeau : aperçu chiffré seul.")


@graph_app.command("backfill-project")
def memory_graph_backfill_project(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    collection: list[str] = _backfill_collection_opt,
    apply: bool = _backfill_apply_opt,
) -> None:
    """Attribue au projet les nœuds Neo4j écrits avant la clé de projet (aperçu par défaut).

    Souvenirs : par collection Weaviate, puis par ``project_name`` des métadonnées.
    Nœuds de code : par préfixe de chemin (``--paths``) ; leur identifiant reçoit le
    préfixe du projet. Fantômes (modules non indexés) : marqués externes. Ne touche
    jamais un nœud déjà attribué ; relançable.
    """
    cfg, _ = _load_config_context()
    graph = _load_neo4j_graph(cfg, ensure_schema=apply)
    collections = list(collection) or [
        c for c in (cfg.memory.weaviate_collection, cfg.memory.collection_prefix) if c
    ]
    prefixes = [raw.strip().rstrip("/") + "/" for raw in paths.split(",") if raw.strip()]
    try:
        report = graph.backfill_project(collections=collections, code_prefixes=prefixes, apply=apply)
    finally:
        graph.close()
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"project": cfg.project.name, "apply": apply, **report}, indent=2))
        return
    console.print(f"[bold]{cfg.project.name}[/bold] — {'rattrapage appliqué' if apply else 'aperçu (rien écrit)'}")
    for key, value in report.items():
        console.print(f"  {key:<24}: {value}")
    if not apply:
        console.print("  Relancez avec [cyan]--apply[/cyan] pour écrire.")


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
    graph = _load_neo4j_graph(cfg, ensure_schema=True)
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


@graph_app.command("coverage")
def memory_graph_coverage(
    ctx: typer.Context,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
) -> None:
    """Nœuds publics sans TESTED_BY : ce que le code graph voit comme non couvert.

    Construit le graphe en mémoire (sans Neo4j) et liste les fonctions,
    méthodes et classes publiques, hors tests, qu'aucune arête TESTED_BY
    n'atteint. Complète ``memory graph sync-code`` / ``verify``, qui portent
    sur la fidélité de la projection Neo4j, pas sur la couverture elle-même.
    """
    from grimoire.memory.projections import build_code_graph

    _, root = _load_config_context()
    graph = build_code_graph(root, _parse_paths(paths), exclude=_parse_exclude(exclude))
    uncovered = graph.uncovered_nodes()

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(
            {"total_nodes": len(graph.nodes), "uncovered": [n.id for n in uncovered]},
            indent=2,
            default=str,
        ))
        return
    console.print(f"[bold]{len(uncovered)}[/bold] nœud(s) public(s) sans TESTED_BY sur {len(graph.nodes)} indexés")
    for node in uncovered:
        console.print(f"  - {node.id}  [dim]({node.file_path})[/dim]")


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
