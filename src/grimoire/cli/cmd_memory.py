"""``grimoire memory`` — inspect and manage the memory subsystem.

Sub-commands: status, search, list, export, import, gc, delete.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireMemoryError
from grimoire.memory.architecture import build_memory_architecture_status
from grimoire.memory.manager import MemoryManager
from grimoire.memory.taxonomy import flatten_taxonomy

memory_app = typer.Typer(help="Inspect and manage the memory subsystem.")
facts_app = typer.Typer(help="Inspect the temporal fact graph sidecar.")
diary_app = typer.Typer(help="Inspect agent diaries stored in the sidecar.")
migrate_app = typer.Typer(help="Plan and export Memory OS migrations.")
graph_app = typer.Typer(help="Sync and verify Memory OS graph projections.")
vector_app = typer.Typer(help="Sync and verify semantic vector projections.")

memory_app.add_typer(facts_app, name="facts")
memory_app.add_typer(diary_app, name="diary")
memory_app.add_typer(migrate_app, name="migrate")
memory_app.add_typer(graph_app, name="graph")
memory_app.add_typer(vector_app, name="vector")

console = Console(stderr=True)

_mempalace_export_palace_opt = typer.Option(..., "--palace", help="Path to the target palace directory.")
_mempalace_import_palace_opt = typer.Option(..., "--palace", help="Path to the source palace directory.")
_migration_bundle_opt = typer.Option(..., "--bundle", "-b", help="Target bundle directory.")
_migration_target_vector_opt = typer.Option("weaviate-server", "--target-vector", help="Target vector backend.")
_migration_target_graph_opt = typer.Option("neo4j", "--target-graph", help="Target graph backend.")
_migration_source_collections_opt = typer.Option(
    "",
    "--source-collections",
    help="Comma-separated Qdrant collections to export. Defaults to all collections from Qdrant REST.",
)
_migration_require_vectors_opt = typer.Option(
    True,
    "--require-vectors/--allow-missing-vectors",
    help="Fail if vectors cannot be preserved.",
)
_migration_weaviate_url_opt = typer.Option("", "--weaviate-url", help="Target Weaviate URL. Defaults to memory.weaviate_url.")
_migration_weaviate_collection_opt = typer.Option(
    "",
    "--collection",
    help="Target Weaviate collection. Defaults to memory.weaviate_collection or collection_prefix.",
)
_migration_batch_size_opt = typer.Option(100, "--batch-size", min=1, help="Weaviate import batch size.")
_migration_neo4j_uri_opt = typer.Option("", "--neo4j-uri", help="Target Neo4j Bolt URI. Defaults to memory.neo4j_uri.")
_migration_neo4j_user_opt = typer.Option("", "--neo4j-user", help="Neo4j user. Defaults to memory.neo4j_user.")
_migration_neo4j_database_opt = typer.Option("", "--database", help="Neo4j database. Defaults to memory.neo4j_database.")
_migration_dry_run_opt = typer.Option(False, "--dry-run", help="Validate inputs without writing to target services.")
_migration_skip_neo4j_opt = typer.Option(False, "--skip-neo4j", help="Verify only the vector store.")
_graph_paths_opt = typer.Option("src,tests", "--paths", help="Comma-separated code paths to index.")
_graph_exclude_opt = typer.Option("__pycache__,.venv,.git,node_modules", "--exclude", help="Comma-separated path parts to skip.")
_graph_ledger_opt = typer.Option(Path("_grimoire-runtime-output/ledger"), "--ledger", help="Mission ledger root.")
_graph_evidence_opt = typer.Option(Path("_grimoire-runtime-output/evidence"), "--evidence", help="Evidence root.")
_gate_bundle_opt = typer.Option(
    None,
    "--bundle",
    help="Migration bundle directory. Defaults to memory.migration_bundle_path.",
)
_gate_sync_opt = typer.Option(True, "--sync/--no-sync", help="Sync Neo4j graph projections before verification.")
_gate_soft_opt = typer.Option(False, "--soft", help="Report failures without returning a non-zero exit code.")
_gate_skip_migration_opt = typer.Option(False, "--skip-migration", help="Skip Weaviate/Neo4j migration parity check.")
_gate_skip_graph_opt = typer.Option(False, "--skip-graph", help="Skip code/task graph projection check.")
_gate_sync_vectors_opt = typer.Option(False, "--sync-vectors/--no-sync-vectors", help="Sync semantic code/task vector projections before verification.")
_gate_skip_vectors_opt = typer.Option(False, "--skip-vectors", help="Skip semantic code/task vector projection check.")
_vector_sync_graph_opt = typer.Option(True, "--sync-graph/--no-sync-graph", help="Sync Neo4j structural graph before linking vector projection entries.")
_vector_granularity_opt = typer.Option(
    "file,symbol,method,test,contract",
    "--granularity",
    help="Comma-separated code vector granularities: file,symbol,method,test,contract.",
)


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def _load_manager_context(path: Path = Path()) -> tuple[MemoryManager, GrimoireConfig, Path]:
    """Resolve project config and return a MemoryManager with source context."""
    cfg, root = _load_config_context(path)
    try:
        return MemoryManager.from_config(cfg, project_root=root), cfg, root
    except GrimoireMemoryError as exc:
        console.print(f"[red]Memory backend error:[/red] {exc}")
        raise typer.Exit(1) from None


def _load_config_context(path: Path = Path()) -> tuple[GrimoireConfig, Path]:
    """Resolve project config without instantiating the memory backend."""
    from grimoire.tools._common import find_project_root

    target = path.resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        try:
            root = find_project_root(target)
            config_path = root / "project-context.yaml"
        except (FileNotFoundError, PermissionError, OSError):
            console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
            raise typer.Exit(1) from None
    else:
        root = target

    try:
        return GrimoireConfig.from_yaml(config_path), root
    except GrimoireConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2) from None


def _load_manager(path: Path = Path()) -> MemoryManager:
    """Resolve project config and return a MemoryManager."""
    mgr, _, _ = _load_manager_context(path)
    return mgr


def _entry_payload(entry: Any) -> dict[str, Any]:
    return entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)


def _filters_dict(wing: str, hall: str, room: str) -> dict[str, str]:
    return {"wing": wing, "hall": hall, "room": room}


def _load_mempalace_backend(palace: Path) -> Any:
    try:
        from grimoire.memory.backends.mempalace import MemPalaceBackend
    except ImportError as exc:
        console.print(f"[red]MemPalace backend unavailable:[/red] {exc}")
        raise typer.Exit(1) from None
    return MemPalaceBackend(palace_path=str(palace))


def _parse_paths(raw: str) -> list[Path]:
    return [Path(item.strip()) for item in raw.split(",") if item.strip()]


def _parse_exclude(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def _parse_granularity(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _load_neo4j_graph(cfg: GrimoireConfig) -> Any:
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    if not cfg.memory.neo4j_uri:
        console.print("[red]Missing Neo4j URI.[/red] Configure memory.neo4j_uri.")
        raise typer.Exit(2)
    password = os.environ.get(cfg.memory.neo4j_password_env, "")
    if not password:
        console.print(f"[red]Missing Neo4j password env:[/red] {cfg.memory.neo4j_password_env}")
        raise typer.Exit(2)
    try:
        return Neo4jMemoryGraph(
            uri=cfg.memory.neo4j_uri,
            user=cfg.memory.neo4j_user,
            password=password,
            database=cfg.memory.neo4j_database,
        )
    except ImportError as exc:
        console.print(f"[red]Neo4j graph unavailable:[/red] {exc}")
        raise typer.Exit(2) from None
    except Exception as exc:
        console.print(f"[red]Neo4j graph unavailable:[/red] {exc}")
        raise typer.Exit(1) from None


def _default_migration_bundle(cfg: GrimoireConfig, root: Path, bundle: Path | None) -> Path:
    raw_bundle = bundle or Path(cfg.memory.migration_bundle_path or "_grimoire/_memory/migration/weaviate-neo4j")
    return raw_bundle if raw_bundle.is_absolute() else root / raw_bundle


def _append_gate_issue(report: dict[str, Any], message: str) -> None:
    report.setdefault("issues", []).append(message)


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


# ── grimoire memory gate ─────────────────────────────────────────────────────


@memory_app.command("gate")
def memory_gate(
    ctx: typer.Context,
    bundle: Path | None = _gate_bundle_opt,
    paths: str = _graph_paths_opt,
    exclude: str = _graph_exclude_opt,
    granularity: str = _vector_granularity_opt,
    ledger_root: Path = _graph_ledger_opt,
    evidence_root: Path = _graph_evidence_opt,
    sync: bool = _gate_sync_opt,
    soft: bool = _gate_soft_opt,
    skip_migration: bool = _gate_skip_migration_opt,
    skip_graph: bool = _gate_skip_graph_opt,
    sync_vectors: bool = _gate_sync_vectors_opt,
    skip_vectors: bool = _gate_skip_vectors_opt,
) -> None:
    """Run the Memory OS parity gate across Weaviate and Neo4j projections."""
    from grimoire.evidence.service import EvidenceService
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection
    from grimoire.memory.migration import verify_migration_bundle
    from grimoire.memory.projections import (
        build_code_vector_entries,
        build_task_vector_entries,
        graph_projection_verify,
        sync_code_graph_projection,
        sync_code_vector_projection,
        sync_task_memory_projection,
        sync_task_vector_projection,
        vector_projection_verify,
    )
    from grimoire.missions.ledger import MissionLedger

    cfg, root = _load_config_context()
    report: dict[str, Any] = {
        "ok": True,
        "mode": "soft" if soft else "strict",
        "migration": {"skipped": True},
        "graph_sync": {"skipped": True},
        "graph_verify": {"skipped": True},
        "vector_sync": {"skipped": True},
        "vector_verify": {"skipped": True},
        "issues": [],
    }

    if not skip_migration:
        bundle_path = _default_migration_bundle(cfg, root, bundle)
        report["migration"] = {"skipped": True, "bundle": str(bundle_path)}
        if not bundle_path.exists():
            _append_gate_issue(report, f"Migration bundle not found: {bundle_path}")
        elif not cfg.memory.weaviate_url:
            _append_gate_issue(report, "memory.weaviate_url is required for migration verification")
        else:
            require_neo4j = bool(cfg.memory.neo4j_uri)
            neo4j_password = os.environ.get(cfg.memory.neo4j_password_env, "")
            if require_neo4j and not neo4j_password:
                _append_gate_issue(report, f"Neo4j password env missing: {cfg.memory.neo4j_password_env}")
            try:
                migration_stats = verify_migration_bundle(
                    bundle_path,
                    weaviate_url=cfg.memory.weaviate_url,
                    collection=normalize_weaviate_collection(
                        cfg.memory.weaviate_collection or cfg.memory.collection_prefix
                    ),
                    api_key=os.environ.get(cfg.memory.weaviate_api_key_env, ""),
                    neo4j_uri=cfg.memory.neo4j_uri,
                    neo4j_user=cfg.memory.neo4j_user,
                    neo4j_password=neo4j_password,
                    neo4j_database=cfg.memory.neo4j_database,
                    require_neo4j=require_neo4j and bool(neo4j_password),
                )
                report["migration"] = migration_stats
                for issue in migration_stats.get("issues", []):
                    _append_gate_issue(report, str(issue))
            except Exception as exc:
                _append_gate_issue(report, f"Migration verification failed: {exc}")

    if not skip_graph:
        ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
        evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
        if not cfg.memory.neo4j_uri:
            _append_gate_issue(report, "memory.neo4j_uri is required for graph projection gate")
        elif not os.environ.get(cfg.memory.neo4j_password_env, ""):
            _append_gate_issue(report, f"Neo4j password env missing: {cfg.memory.neo4j_password_env}")
        else:
            graph = _load_neo4j_graph(cfg)
            try:
                ledger = MissionLedger(ledger_path)
                evidence = EvidenceService(evidence_path)
                if sync:
                    report["graph_sync"] = {
                        "code": sync_code_graph_projection(
                            graph,
                            project_root=root,
                            paths=_parse_paths(paths),
                            exclude=_parse_exclude(exclude),
                        ),
                        "tasks": sync_task_memory_projection(
                            graph,
                            ledger=ledger,
                            evidence=evidence,
                            project_root=root,
                            code_paths=_parse_paths(paths),
                            code_exclude=_parse_exclude(exclude),
                        ),
                    }
                report["graph_verify"] = graph_projection_verify(
                    graph,
                    project_root=root,
                    code_paths=_parse_paths(paths),
                    code_exclude=_parse_exclude(exclude),
                    ledger=ledger,
                    evidence=evidence,
                )
                for issue in report["graph_verify"].get("issues", []):
                    _append_gate_issue(report, str(issue))
            except Exception as exc:
                _append_gate_issue(report, f"Graph projection gate failed: {exc}")
            finally:
                graph.close()

    if not skip_vectors:
        ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
        evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root
        try:
            mgr, _, _ = _load_manager_context()
            ledger = MissionLedger(ledger_path)
            evidence = EvidenceService(evidence_path)
            expected_entries = [
                *build_code_vector_entries(
                    root,
                    _parse_paths(paths),
                    exclude=_parse_exclude(exclude),
                    granularity=_parse_granularity(granularity),
                ),
                *build_task_vector_entries(ledger, evidence=evidence),
            ]
            if sync_vectors:
                report["vector_sync"] = {
                    "code": sync_code_vector_projection(
                        mgr,
                        project_root=root,
                        paths=_parse_paths(paths),
                        exclude=_parse_exclude(exclude),
                        granularity=_parse_granularity(granularity),
                    ),
                    "tasks": sync_task_vector_projection(mgr, ledger=ledger, evidence=evidence),
                }
            report["vector_verify"] = vector_projection_verify(mgr, expected_entries)
            for issue in report["vector_verify"].get("issues", []):
                _append_gate_issue(report, str(issue))
        except Exception as exc:
            _append_gate_issue(report, f"Vector projection gate failed: {exc}")

    report["ok"] = not report["issues"]
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(report, indent=2, default=str))
    else:
        status = "[green]OK[/green]" if report["ok"] else "[red]FAIL[/red]"
        if soft and not report["ok"]:
            status = "[yellow]SOFT FAIL[/yellow]"
        console.print(f"{status} Memory OS gate")
        if not report["migration"].get("skipped"):
            console.print(
                f"  Migration : {report['migration']['bundle']['record_count']} records, "
                f"Weaviate {report['migration']['weaviate']['count']}"
            )
        else:
            console.print("  Migration : skipped")
        if not report["graph_verify"].get("skipped"):
            actual = report["graph_verify"].get("actual", {})
            console.print(
                f"  Graph     : {actual.get('code_nodes', 0)} CodeNode, "
                f"{actual.get('code_edges', 0)} CODE_EDGE"
            )
        else:
            console.print("  Graph     : skipped")
        if not report["vector_verify"].get("skipped"):
            console.print(
                f"  Vectors   : {report['vector_verify'].get('actual', 0)} projected, "
                f"expected {report['vector_verify'].get('expected', 0)}"
            )
        else:
            console.print("  Vectors   : skipped")
        for issue in report["issues"]:
            console.print(f"  [red]-[/red] {issue}")

    if not report["ok"] and not soft:
        raise typer.Exit(1)


# ── grimoire memory migrate ──────────────────────────────────────────────────


@migrate_app.command("plan")
def memory_migrate_plan(
    ctx: typer.Context,
    target_vector: str = _migration_target_vector_opt,
    target_graph: str = _migration_target_graph_opt,
) -> None:
    """Show the non-destructive migration plan for Memory OS data."""
    cfg, _ = _load_config_context()
    fmt = _get_fmt(ctx)
    bundle = cfg.memory.migration_bundle_path or "_grimoire/_memory/migration/weaviate-neo4j"
    plan = {
        "source_backend": cfg.memory.migration_source_backend or cfg.memory.backend,
        "configured_backend": cfg.memory.backend,
        "target_vector_backend": target_vector,
        "target_graph_backend": target_graph,
        "entries": None,
        "bundle_path": bundle,
        "cutover_gate": [
            "export bundle",
            "verify vector_count equals record_count",
            "import Weaviate objects with preserved vectors",
            "load Neo4j Cypher projection",
            "run recall parity checks",
            "switch memory.backend to weaviate-server",
        ],
    }

    if fmt == "json":
        typer.echo(json.dumps(plan, indent=2, default=str))
        return

    console.print("[bold]Memory migration plan[/bold]")
    console.print(f"  Source backend : {plan['source_backend']}")
    console.print("  Entries        : unknown until export-bundle")
    console.print(f"  Target vector  : {target_vector}")
    console.print(f"  Target graph   : {target_graph}")
    console.print(f"  Bundle path    : {bundle}")
    console.print("  Cutover gate   : export, verify, import, parity, switch")


@migrate_app.command("export-bundle")
def memory_migrate_export_bundle(
    ctx: typer.Context,
    bundle: Path = _migration_bundle_opt,
    target_vector: str = _migration_target_vector_opt,
    target_graph: str = _migration_target_graph_opt,
    source_collections: str = _migration_source_collections_opt,
    require_vectors: bool = _migration_require_vectors_opt,
) -> None:
    """Export a portable Qdrant to Weaviate plus Neo4j migration bundle."""
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection
    from grimoire.memory.migration import (
        collections_from_qdrant_rest,
        records_from_memory_entries,
        records_from_qdrant_backend,
        records_from_qdrant_rest,
        write_migration_bundle,
    )

    cfg, _ = _load_config_context()
    if cfg.memory.qdrant_url:
        collections = _parse_source_collections(source_collections) or collections_from_qdrant_rest(cfg.memory.qdrant_url)
        records = []
        for collection in collections:
            records.extend(records_from_qdrant_rest(cfg.memory.qdrant_url, collection))
        source_backend = f"qdrant-rest:{cfg.memory.qdrant_url}"
    else:
        mgr, cfg, _ = _load_manager_context()
        health = mgr.health_check()
        try:
            records = records_from_qdrant_backend(mgr.backend)
            source_backend = health.backend
        except TypeError:
            records = records_from_memory_entries(mgr.get_all())
            source_backend = f"{health.backend}:public-api"

    vector_count = sum(1 for record in records if record.has_vector)
    if require_vectors and vector_count != len(records):
        console.print("[red]Migration bundle would not be vector-lossless.[/red]")
        console.print("Run again with [bold]--allow-missing-vectors[/bold] only for fallback exports.")
        raise typer.Exit(2)

    manifest = write_migration_bundle(
        bundle,
        records,
        source_backend=source_backend,
        target_vector_backend=target_vector,
        target_graph_backend=target_graph,
        weaviate_collection=normalize_weaviate_collection(cfg.memory.weaviate_collection or cfg.memory.collection_prefix),
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({"bundle": str(bundle), "manifest": manifest}, indent=2, default=str))
        return

    console.print(f"[green]Migration bundle written:[/green] {bundle}")
    console.print(f"  Records : {manifest['record_count']}")
    console.print(f"  Vectors : {manifest['vector_count']}")
    console.print(f"  Lossless: {manifest['vector_lossless']}")


@migrate_app.command("import-weaviate")
def memory_migrate_import_weaviate(
    ctx: typer.Context,
    bundle: Path = _migration_bundle_opt,
    weaviate_url: str = _migration_weaviate_url_opt,
    collection: str = _migration_weaviate_collection_opt,
    batch_size: int = _migration_batch_size_opt,
    dry_run: bool = _migration_dry_run_opt,
) -> None:
    """Import bundle objects into Weaviate with preserved vectors."""
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection
    from grimoire.memory.migration import import_weaviate_bundle

    cfg, _ = _load_config_context()
    target_url = weaviate_url or cfg.memory.weaviate_url
    target_collection = collection or normalize_weaviate_collection(cfg.memory.weaviate_collection or cfg.memory.collection_prefix)
    if not target_url:
        console.print("[red]Missing Weaviate URL.[/red] Configure memory.weaviate_url or pass --weaviate-url.")
        raise typer.Exit(2)

    try:
        stats = import_weaviate_bundle(
            bundle,
            weaviate_url=target_url,
            collection=target_collection,
            api_key=os.environ.get(cfg.memory.weaviate_api_key_env, ""),
            batch_size=batch_size,
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Weaviate import failed:[/red] {exc}")
        raise typer.Exit(1) from None

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return

    action = "validated" if dry_run else "imported"
    console.print(f"[green]Weaviate bundle {action}:[/green] {bundle}")
    console.print(f"  Collection: {stats['collection']}")
    console.print(f"  Objects   : {stats['objects']}")
    console.print(f"  Imported  : {stats['imported']}")


@migrate_app.command("import-neo4j")
def memory_migrate_import_neo4j(
    ctx: typer.Context,
    bundle: Path = _migration_bundle_opt,
    neo4j_uri: str = _migration_neo4j_uri_opt,
    neo4j_user: str = _migration_neo4j_user_opt,
    database: str = _migration_neo4j_database_opt,
    dry_run: bool = _migration_dry_run_opt,
) -> None:
    """Import the generated graph projection into Neo4j."""
    from grimoire.memory.migration import import_neo4j_cypher, read_migration_manifest

    cfg, _ = _load_config_context()
    target_uri = neo4j_uri or cfg.memory.neo4j_uri
    target_user = neo4j_user or cfg.memory.neo4j_user
    target_database = database or cfg.memory.neo4j_database
    manifest = read_migration_manifest(bundle)
    cypher_rel = manifest.get("files", {}).get("neo4j_cypher", "neo4j-import.cypher")
    cypher_path = bundle / str(cypher_rel)
    password = os.environ.get(cfg.memory.neo4j_password_env, "")
    if not target_uri:
        console.print("[red]Missing Neo4j URI.[/red] Configure memory.neo4j_uri or pass --neo4j-uri.")
        raise typer.Exit(2)
    if not password and not dry_run:
        console.print(f"[red]Missing Neo4j password env:[/red] {cfg.memory.neo4j_password_env}")
        raise typer.Exit(2)

    try:
        stats = import_neo4j_cypher(
            cypher_path,
            uri=target_uri,
            user=target_user,
            password=password,
            database=target_database,
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Neo4j import failed:[/red] {exc}")
        raise typer.Exit(1) from None

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return

    action = "validated" if dry_run else "imported"
    console.print(f"[green]Neo4j projection {action}:[/green] {cypher_path}")
    console.print(f"  Statements: {stats['statements']}")
    console.print(f"  Executed  : {stats['executed']}")


@migrate_app.command("verify")
def memory_migrate_verify(
    ctx: typer.Context,
    bundle: Path = _migration_bundle_opt,
    weaviate_url: str = _migration_weaviate_url_opt,
    collection: str = _migration_weaviate_collection_opt,
    neo4j_uri: str = _migration_neo4j_uri_opt,
    neo4j_user: str = _migration_neo4j_user_opt,
    database: str = _migration_neo4j_database_opt,
    skip_neo4j: bool = _migration_skip_neo4j_opt,
) -> None:
    """Verify bundle parity against Weaviate and Neo4j before cutover."""
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection
    from grimoire.memory.migration import verify_migration_bundle

    cfg, _ = _load_config_context()
    target_url = weaviate_url or cfg.memory.weaviate_url
    target_collection = collection or normalize_weaviate_collection(cfg.memory.weaviate_collection or cfg.memory.collection_prefix)
    target_uri = neo4j_uri or cfg.memory.neo4j_uri
    target_user = neo4j_user or cfg.memory.neo4j_user
    target_database = database or cfg.memory.neo4j_database
    if not target_url:
        console.print("[red]Missing Weaviate URL.[/red] Configure memory.weaviate_url or pass --weaviate-url.")
        raise typer.Exit(2)

    try:
        stats = verify_migration_bundle(
            bundle,
            weaviate_url=target_url,
            collection=target_collection,
            api_key=os.environ.get(cfg.memory.weaviate_api_key_env, ""),
            neo4j_uri=target_uri,
            neo4j_user=target_user,
            neo4j_password=os.environ.get(cfg.memory.neo4j_password_env, ""),
            neo4j_database=target_database,
            require_neo4j=not skip_neo4j,
        )
    except Exception as exc:
        console.print(f"[red]Migration verification failed:[/red] {exc}")
        raise typer.Exit(1) from None

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
    else:
        status = "[green]OK[/green]" if stats["ok"] else "[red]FAIL[/red]"
        console.print(f"{status} Migration verification: {bundle}")
        console.print(f"  Bundle   : {stats['bundle']['record_count']} records, {stats['bundle']['vector_count']} vectors")
        console.print(f"  Weaviate : {stats['weaviate']['count']} objects in {stats['weaviate']['collection']}")
        if stats["neo4j"].get("skipped"):
            console.print("  Neo4j    : skipped")
        else:
            console.print(
                f"  Neo4j    : {stats['neo4j']['count']} memories, "
                f"{stats['neo4j']['tag_edges']} TAGGED_WITH edges"
            )
        if stats["issues"]:
            for issue in stats["issues"]:
                console.print(f"  [red]-[/red] {issue}")

    if not stats["ok"]:
        raise typer.Exit(1)


def _parse_source_collections(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


# ── grimoire memory status ────────────────────────────────────────────────────


@memory_app.command("status")
def memory_status(ctx: typer.Context) -> None:
    """Show memory backend health, entry count, and configuration."""
    mgr, cfg, root = _load_manager_context()
    health = mgr.health_check()
    total = mgr.count()
    facts = mgr.facts_stats() if hasattr(mgr, "facts_stats") else {}
    diary = mgr.diary_stats() if hasattr(mgr, "diary_stats") else {}
    architecture = build_memory_architecture_status(cfg, project_root=root, backend_status=health)
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({
            "backend": health.backend,
            "healthy": health.healthy,
            "entries": total,
            "detail": health.detail,
            "facts": facts,
            "diary": diary,
            "architecture": architecture.to_dict(),
        }, indent=2, default=str))
        return

    status_icon = "[green][OK][/green]" if health.healthy else "[red][x][/red]"
    console.print(f"{status_icon} Backend: [bold]{health.backend}[/bold]")
    console.print(f"  Entries : {total}")
    if health.detail:
        for k, v in health.detail.items():
            console.print(f"  {k}: {v}")
    if facts:
        console.print(f"  Facts   : {facts.get('facts', 0)} active={facts.get('active_facts', 0)}")
    if diary:
        console.print(f"  Diary   : {diary.get('diary_entries', 0)} entries across {diary.get('agents', 0)} agents")

    console.print("\n[bold]Memory OS layers[/bold]")
    tbl = Table(show_header=True)
    tbl.add_column("Layer")
    tbl.add_column("State")
    tbl.add_column("Backend")
    tbl.add_column("Next")
    for layer in architecture.layers:
        state_style = {
            "ready": "green",
            "partial": "yellow",
            "planned": "cyan",
            "disabled": "dim",
        }.get(layer.state, "white")
        next_action = layer.next_actions[0] if layer.next_actions else "—"
        tbl.add_row(layer.label, f"[{state_style}]{layer.state}[/{state_style}]", layer.backend, next_action)
    console.print(tbl)


# ── grimoire memory search ────────────────────────────────────────────────────


@memory_app.command("search")
def memory_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (keyword or semantic)."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results to return."),
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
    wing: str = typer.Option("", "--wing", help="Filter by palace wing."),
    hall: str = typer.Option("", "--hall", help="Filter by palace hall."),
    room: str = typer.Option("", "--room", help="Filter by palace room."),
) -> None:
    """Search memories by keyword or semantic similarity."""
    mgr = _load_manager()
    results = mgr.search_taxonomy(query, user_id=user_id, limit=limit, wing=wing, hall=hall, room=room)
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps([_entry_payload(e) for e in results], indent=2, default=str))
        return

    if not results:
        console.print(f"[yellow]No memories matching '{query}'.[/yellow]")
        return

    tbl = Table(title=f"Search: {query}")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Text", max_width=60)
    tbl.add_column("Wing")
    tbl.add_column("Hall")
    tbl.add_column("Room")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Tags")

    for entry in results:
        score = f"{entry.score:.3f}" if entry.score else "—"
        tags = ", ".join(entry.tags) if entry.tags else "—"
        text = entry.text[:57] + "…" if len(entry.text) > 60 else entry.text
        tbl.add_row(
            entry.id[:12],
            text,
            str(entry.metadata.get("wing", "—")),
            str(entry.metadata.get("hall", "—")),
            str(entry.metadata.get("room", "—")),
            score,
            tags,
        )

    console.print(tbl)


# ── grimoire memory remember / recall (protocole agent typé, ADR-003) ────────


@memory_app.command("remember")
def memory_remember(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Text to remember."),
    type_: str = typer.Option(..., "--type", "-t", help="Memory type: shared-context | decisions | agent-learnings | failures | stories."),
    agent: str = typer.Option(..., "--agent", "-a", help="Emitting agent tag."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
) -> None:
    """Typed idempotent memory write — same text + agent never duplicates.

    SDK equivalent of the legacy `mem0-bridge.py remember` agent protocol
    (deterministic UUID5 on project + agent + text).
    """
    from grimoire.core.exceptions import GrimoireMemoryError

    mgr = _load_manager()
    tag_tuple = tuple(t.strip() for t in tags.split(",") if t.strip())
    try:
        entry = mgr.remember(type_, agent, text, tags=tag_tuple)
    except GrimoireMemoryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_entry_payload(entry), indent=2, default=str))
        return
    console.print(f"[green]OK[/green] remembered \\[{type_}] as {entry.id[:12]}… (agent: {agent})")


@memory_app.command("recall")
def memory_recall(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query."),
    type_: str = typer.Option("", "--type", "-t", help="Filter by memory type."),
    agent: str = typer.Option("", "--agent", "-a", help="Filter by emitting agent."),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results to return."),
) -> None:
    """Search typed memories written via `grimoire memory remember`."""
    from grimoire.core.exceptions import GrimoireMemoryError

    mgr = _load_manager()
    try:
        results = mgr.recall_typed(query, type_=type_, agent=agent, limit=limit)
    except GrimoireMemoryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([_entry_payload(e) for e in results], indent=2, default=str))
        return

    if not results:
        console.print(f"[yellow]No memories matching '{query}'.[/yellow]")
        return

    tbl = Table(title=f"Recall: {query}")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Type")
    tbl.add_column("Agent")
    tbl.add_column("Text", max_width=60)
    tbl.add_column("Tags")
    for entry in results:
        text_col = entry.text[:57] + "…" if len(entry.text) > 60 else entry.text
        tbl.add_row(
            entry.id[:12],
            str(entry.metadata.get("type", entry.metadata.get("memory_type", "—"))),
            str(entry.metadata.get("agent", entry.user_id or "—")),
            text_col,
            ", ".join(entry.tags) if entry.tags else "—",
        )
    console.print(tbl)


# ── grimoire memory list ──────────────────────────────────────────────────────


@memory_app.command("list")
def memory_list(
    ctx: typer.Context,
    offset: int = typer.Option(0, "--offset", help="Skip first N entries."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to return."),
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
    wing: str = typer.Option("", "--wing", help="Filter by palace wing."),
    hall: str = typer.Option("", "--hall", help="Filter by palace hall."),
    room: str = typer.Option("", "--room", help="Filter by palace room."),
) -> None:
    """List stored memories with pagination."""
    mgr = _load_manager()
    entries = mgr.get_all_filtered(user_id=user_id, offset=offset, limit=limit, wing=wing, hall=hall, room=room)
    total = len(mgr.get_all_filtered(user_id=user_id, wing=wing, hall=hall, room=room))
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({
            "total": total,
            "offset": offset,
            "limit": limit,
            "entries": [_entry_payload(e) for e in entries],
        }, indent=2, default=str))
        return

    if not entries:
        console.print("[yellow]No memories stored.[/yellow]")
        return

    tbl = Table(title=f"Memories ({offset+1}–{offset+len(entries)} of {total})")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Text", max_width=50)
    tbl.add_column("Wing")
    tbl.add_column("Hall")
    tbl.add_column("Room")
    tbl.add_column("Tags")
    tbl.add_column("Created", style="dim")

    for entry in entries:
        tags = ", ".join(entry.tags) if entry.tags else "—"
        text = entry.text[:47] + "…" if len(entry.text) > 50 else entry.text
        created = entry.created_at[:10] if entry.created_at else "—"
        tbl.add_row(
            entry.id[:12],
            text,
            str(entry.metadata.get("wing", "—")),
            str(entry.metadata.get("hall", "—")),
            str(entry.metadata.get("room", "—")),
            tags,
            created,
        )

    console.print(tbl)


# ── grimoire memory taxonomy ──────────────────────────────────────────────────


@memory_app.command("taxonomy")
def memory_taxonomy(
    ctx: typer.Context,
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
    wing: str = typer.Option("", "--wing", help="Filter by wing before aggregating."),
    hall: str = typer.Option("", "--hall", help="Filter by hall before aggregating."),
    room: str = typer.Option("", "--room", help="Filter by room before aggregating."),
) -> None:
    """Show the palace-style wing / hall / room taxonomy."""
    mgr = _load_manager()
    taxonomy = mgr.taxonomy(user_id=user_id, wing=wing, hall=hall, room=room)
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps(taxonomy, indent=2, default=str))
        return

    rows = flatten_taxonomy(taxonomy)
    if not rows:
        console.print("[yellow]No taxonomy data available.[/yellow]")
        return

    tbl = Table(title="Palace Taxonomy")
    tbl.add_column("Wing")
    tbl.add_column("Hall")
    tbl.add_column("Room")
    tbl.add_column("Count", justify="right")

    for row_data in rows:
        tbl.add_row(row_data["wing"], row_data["hall"], row_data["room"], str(row_data["count"]))

    console.print(tbl)


# ── grimoire memory export ────────────────────────────────────────────────────

_export_file_opt = typer.Option(None, "--file", "-f", help="Output file (default: stdout).")


@memory_app.command("export")
def memory_export(
    ctx: typer.Context,
    file: Path = _export_file_opt,
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
) -> None:
    """Export all memories to JSON."""
    mgr = _load_manager()
    entries = mgr.get_all(user_id=user_id)
    data = {
        "version": 1,
        "count": len(entries),
        "entries": [_entry_payload(e) for e in entries],
    }
    payload = json.dumps(data, indent=2, default=str, ensure_ascii=False)

    if file:
        file.write_text(payload, encoding="utf-8")
        console.print(f"[green]Exported {len(entries)} entries →[/green] {file}")
    else:
        typer.echo(payload)


# ── grimoire memory import ────────────────────────────────────────────────────


def _validate_import_data(data: Any) -> list[dict[str, Any]]:
    """Validate imported JSON structure. Returns list of entry dicts."""
    if not isinstance(data, dict):
        console.print("[red]Invalid format:[/red] expected JSON object with 'entries' key.")
        raise typer.Exit(1)
    entries = data.get("entries")
    if not isinstance(entries, list):
        console.print("[red]Invalid format:[/red] 'entries' must be a list.")
        raise typer.Exit(1)
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "text" not in e:
            console.print(f"[red]Invalid entry at index {i}:[/red] must have 'text' field.")
            raise typer.Exit(1)
    return entries


_import_file_arg = typer.Argument(..., help="JSON file to import.", exists=True, readable=True)


@memory_app.command("import")
def memory_import(
    ctx: typer.Context,
    file: Path = _import_file_arg,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show plan without importing."),
) -> None:
    """Import memories from a JSON export file."""
    raw = file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise typer.Exit(1) from None

    entries = _validate_import_data(data)
    fmt = _get_fmt(ctx)

    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({"dry_run": True, "count": len(entries)}, indent=2))
        else:
            console.print(f"[bold]import --dry-run:[/bold] would import {len(entries)} entries.")
        return

    mgr = _load_manager()
    result = mgr.store_many(entries)

    if fmt == "json":
        typer.echo(json.dumps({"imported": len(result)}, indent=2))
    else:
        console.print(f"[green]Imported {len(result)} entries.[/green]")


# ── grimoire memory MemPalace bridge ─────────────────────────────────────────


@memory_app.command("mempalace-export")
def memory_mempalace_export(
    ctx: typer.Context,
    palace: Path = _mempalace_export_palace_opt,
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
    wing: str = typer.Option("", "--wing", help="Filter by palace wing."),
    hall: str = typer.Option("", "--hall", help="Filter by palace hall."),
    room: str = typer.Option("", "--room", help="Filter by palace room."),
) -> None:
    """Export current Grimoire memories into a MemPalace-compatible palace."""
    mgr = _load_manager()
    backend = _load_mempalace_backend(palace)
    entries = mgr.get_all_filtered(user_id=user_id, wing=wing, hall=hall, room=room)
    result = backend.store_many([
        {
            "text": entry.text,
            "user_id": entry.user_id,
            "tags": list(entry.tags),
            "metadata": dict(entry.metadata),
        }
        for entry in entries
    ])
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({"exported": len(result), "palace": str(palace)}, indent=2))
        return

    console.print(f"[green]Exported {len(result)} memories to[/green] {palace}")


@memory_app.command("mempalace-import")
def memory_mempalace_import(
    ctx: typer.Context,
    palace: Path = _mempalace_import_palace_opt,
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
    wing: str = typer.Option("", "--wing", help="Filter by palace wing."),
    hall: str = typer.Option("", "--hall", help="Filter by palace hall."),
    room: str = typer.Option("", "--room", help="Filter by palace room."),
) -> None:
    """Import memories from a MemPalace-compatible palace into the current backend."""
    mgr = _load_manager()
    backend = _load_mempalace_backend(palace)
    entries = backend.get_all_filtered(user_id=user_id, filters=_filters_dict(wing, hall, room))
    result = mgr.store_many([
        {
            "text": entry.text,
            "user_id": entry.user_id,
            "tags": list(entry.tags),
            "metadata": dict(entry.metadata),
        }
        for entry in entries
    ])
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({"imported": len(result), "palace": str(palace)}, indent=2))
        return

    console.print(f"[green]Imported {len(result)} memories from[/green] {palace}")


# ── grimoire memory gc ────────────────────────────────────────────────────────


@memory_app.command("gc")
def memory_gc(ctx: typer.Context) -> None:
    """Consolidate and compact stored memories."""
    mgr = _load_manager()
    fmt = _get_fmt(ctx)
    affected = mgr.consolidate()

    if fmt == "json":
        typer.echo(json.dumps({"consolidated": affected}, indent=2))
    else:
        if affected:
            console.print(f"[green]Consolidated {affected} entries.[/green]")
        else:
            console.print("[dim]Nothing to consolidate.[/dim]")


# ── grimoire memory facts ─────────────────────────────────────────────────────


@facts_app.command("add")
def memory_facts_add(
    ctx: typer.Context,
    subject: str = typer.Argument(..., help="Subject entity."),
    predicate: str = typer.Argument(..., help="Predicate / relationship."),
    object_: str = typer.Argument(..., metavar="object", help="Object entity."),
    valid_from: str = typer.Option("", "--valid-from", help="Validity start date (ISO)."),
    confidence: float = typer.Option(1.0, "--confidence", help="Confidence score."),
    source_entry: str = typer.Option("", "--source-entry", help="Optional source memory entry ID."),
) -> None:
    """Add one temporal fact to the SQLite sidecar."""
    mgr = _load_manager()
    fact = mgr.add_fact(
        subject,
        predicate,
        object_,
        valid_from=valid_from,
        confidence=confidence,
        source_memory_id=source_entry,
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(fact.to_dict(), indent=2, default=str))
        return
    console.print(f"[green]Fact stored:[/green] {fact.subject} -> {fact.predicate} -> {fact.object}")


@facts_app.command("invalidate")
def memory_facts_invalidate(
    ctx: typer.Context,
    subject: str = typer.Argument(..., help="Subject entity."),
    predicate: str = typer.Argument(..., help="Predicate / relationship."),
    object_: str = typer.Argument(..., metavar="object", help="Object entity."),
    ended: str = typer.Option("", "--ended", help="Validity end date (ISO)."),
) -> None:
    """Invalidate one or more active facts."""
    mgr = _load_manager()
    affected = mgr.invalidate_fact(subject, predicate, object_, ended=ended)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({"invalidated": affected}, indent=2))
        return
    console.print(f"[green]Invalidated {affected} fact(s).[/green]")


@facts_app.command("query")
def memory_facts_query(
    ctx: typer.Context,
    entity: str = typer.Argument(..., help="Entity to inspect."),
    as_of: str = typer.Option("", "--as-of", help="Historical date filter (ISO)."),
    direction: str = typer.Option("both", "--direction", help="both, incoming, or outgoing."),
) -> None:
    """Query structured facts for an entity."""
    direction = direction.lower()
    if direction not in {"both", "incoming", "outgoing"}:
        console.print("[red]direction must be one of: both, incoming, outgoing[/red]")
        raise typer.Exit(1)

    mgr = _load_manager()
    facts = mgr.query_facts(entity, as_of=as_of, direction=direction)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps([fact.to_dict() for fact in facts], indent=2, default=str))
        return
    if not facts:
        console.print("[yellow]No facts found.[/yellow]")
        return

    tbl = Table(title=f"Facts: {entity}")
    tbl.add_column("Subject")
    tbl.add_column("Predicate")
    tbl.add_column("Object")
    tbl.add_column("From")
    tbl.add_column("To")
    tbl.add_column("Confidence", justify="right")
    for fact in facts:
        tbl.add_row(
            fact.subject,
            fact.predicate,
            fact.object,
            fact.valid_from or "—",
            fact.valid_to or "—",
            f"{fact.confidence:.2f}",
        )
    console.print(tbl)


@facts_app.command("timeline")
def memory_facts_timeline(
    ctx: typer.Context,
    entity: str = typer.Argument("", help="Optional entity filter."),
) -> None:
    """Show the chronological fact timeline."""
    mgr = _load_manager()
    facts = mgr.facts_timeline(entity)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps([fact.to_dict() for fact in facts], indent=2, default=str))
        return
    if not facts:
        console.print("[yellow]No timeline data available.[/yellow]")
        return

    tbl = Table(title=f"Timeline{' — ' + entity if entity else ''}")
    tbl.add_column("When")
    tbl.add_column("Subject")
    tbl.add_column("Predicate")
    tbl.add_column("Object")
    for fact in facts:
        when = fact.valid_from or fact.created_at or "—"
        tbl.add_row(when, fact.subject, fact.predicate, fact.object)
    console.print(tbl)


@facts_app.command("stats")
def memory_facts_stats(ctx: typer.Context) -> None:
    """Show aggregate fact-graph metrics."""
    mgr = _load_manager()
    stats = mgr.facts_stats()
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return
    console.print(f"Facts: {stats.get('facts', 0)}")
    console.print(f"Active: {stats.get('active_facts', 0)}")
    console.print(f"Expired: {stats.get('expired_facts', 0)}")


# ── grimoire memory diary ─────────────────────────────────────────────────────


@diary_app.command("write")
def memory_diary_write(
    ctx: typer.Context,
    agent_name: str = typer.Argument(..., help="Agent identifier."),
    entry: str = typer.Argument(..., help="Diary entry text."),
    topic: str = typer.Option("general", "--topic", help="Topic label."),
    entry_format: str = typer.Option("markdown", "--format", help="Entry format label."),
    related_entry: str = typer.Option("", "--related-entry", help="Optional linked memory entry ID."),
) -> None:
    """Write one diary entry for an agent."""
    mgr = _load_manager()
    record = mgr.write_diary(
        agent_name,
        entry,
        topic=topic,
        entry_format=entry_format,
        related_memory_id=related_entry,
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(record.to_dict(), indent=2, default=str))
        return
    console.print(f"[green]Diary entry stored for[/green] {record.agent_name}")


@diary_app.command("read")
def memory_diary_read(
    ctx: typer.Context,
    agent_name: str = typer.Argument(..., help="Agent identifier."),
    last_n: int = typer.Option(10, "--last", help="How many recent entries to read."),
) -> None:
    """Read recent diary entries for an agent."""
    mgr = _load_manager()
    entries = mgr.read_diary(agent_name, last_n=last_n)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps([entry.to_dict() for entry in entries], indent=2, default=str))
        return
    if not entries:
        console.print("[yellow]No diary entries found.[/yellow]")
        return

    tbl = Table(title=f"Diary: {agent_name}")
    tbl.add_column("Created")
    tbl.add_column("Topic")
    tbl.add_column("Entry", max_width=80)
    for entry in entries:
        preview = entry.entry[:77] + "…" if len(entry.entry) > 80 else entry.entry
        tbl.add_row(entry.created_at or "—", entry.topic, preview)
    console.print(tbl)


@diary_app.command("stats")
def memory_diary_stats(ctx: typer.Context) -> None:
    """Show aggregate diary metrics."""
    mgr = _load_manager()
    stats = mgr.diary_stats()
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return
    console.print(f"Diary entries: {stats.get('diary_entries', 0)}")
    console.print(f"Agents: {stats.get('agents', 0)}")


# ── grimoire memory delete ────────────────────────────────────────────────────


@memory_app.command("delete")
def memory_delete(
    ctx: typer.Context,
    entry_id: str = typer.Argument(..., help="Entry ID to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a specific memory entry by ID."""
    mgr = _load_manager()
    fmt = _get_fmt(ctx)
    confirm = yes or (ctx.obj or {}).get("yes", False)

    # Verify it exists first
    entry = mgr.recall(entry_id)
    if entry is None:
        if fmt == "json":
            typer.echo(json.dumps({"deleted": False, "reason": "not found"}, indent=2))
        else:
            console.print(f"[yellow]Entry not found:[/yellow] {entry_id}")
        raise typer.Exit(1)

    if not confirm:
        text_preview = entry.text[:60] + "…" if len(entry.text) > 60 else entry.text
        console.print(f"[bold]Delete:[/bold] {entry_id}")
        console.print(f"  Text: {text_preview}")
        if not typer.confirm("Confirm deletion?"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    deleted = mgr.delete(entry_id)

    if fmt == "json":
        typer.echo(json.dumps({"deleted": deleted, "entry_id": entry_id}, indent=2))
    else:
        if deleted:
            console.print(f"[green]Deleted:[/green] {entry_id}")
        else:
            console.print(f"[red]Failed to delete:[/red] {entry_id}")
