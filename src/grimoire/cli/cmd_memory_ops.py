"""``grimoire memory up`` / ``status`` — piloter et diagnostiquer la stack mémoire.

Extrait de :mod:`grimoire.cli.cmd_memory` pour que le module principal reste
sous son plafond de taille (ratchet R2). Les commandes s'enregistrent sur
``memory_app`` à l'import : importer ce module est ce qui câble
``memory up`` et ``memory status`` dans la CLI. ``cmd_memory_lexical``
importe ce module pour cet effet de bord, comme il le fait pour
``cmd_memory_bundle`` — ``grimoire.cli.app`` n'a qu'un seul point d'entrée
mémoire à connaître.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from grimoire.cli.cmd_memory import (
    _get_fmt,
    _load_config_context,
    _load_manager_context,
    _load_neo4j_graph,
    console,
    graph_app,
    memory_app,
)
from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.architecture import build_memory_architecture_status
from grimoire.memory.backends.base import BackendStatus
from grimoire.memory.manager import MemoryManager

__all__ = ["memory_app"]


# ── grimoire memory up ────────────────────────────────────────────────────────

_up_profile_opt = typer.Option(
    "full", "--profile", help="Cible : lexical (zéro dépendance), vector, ou full (vecteurs + graphe + chaud).",
)
_up_apply_opt = typer.Option(False, "--apply", help="Écrire le bloc memory: dans project-context.yaml.")


@memory_app.command("up")
def memory_up(
    ctx: typer.Context,
    profile: str = _up_profile_opt,
    apply: bool = _up_apply_opt,
) -> None:
    """Mettre en place la stack mémoire complète — plan par défaut, écriture avec --apply.

    Comble le trou laissé par ``grimoire init``, qui détecte un backend
    vectoriel mais laisse les couches graphe, code, tâches et chaude
    commentées dans le template.

    N'active que les services qui répondent : écrire ``memory_graph: neo4j``
    alors que Neo4j est éteint produirait une config qui échoue en silence.

    [dim]Examples:[/dim]
      [cyan]grimoire memory up[/cyan]                  Plan seul, rien n'est écrit
      [cyan]grimoire memory up --apply[/cyan]          Écrit le bloc memory:
      [cyan]grimoire memory up --profile vector[/cyan] Vecteurs sans graphe
    """
    from grimoire.tools.memory_setup import PROFILES, apply_memory_plan, build_memory_plan

    if profile not in PROFILES:
        console.print(f"[red]Profil inconnu :[/red] {profile} — attendu : {', '.join(PROFILES)}")
        raise typer.Exit(1)

    plan = build_memory_plan(Path.cwd(), profile=profile)
    written = apply_memory_plan(plan) if apply else []
    fmt = _get_fmt(ctx)

    if fmt == "json":
        payload = plan.to_dict()
        payload["applied"] = apply
        payload["written"] = written
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    console.print(f"[bold]Memory OS — profil {profile}[/bold]\n")

    tbl = Table(show_header=True)
    tbl.add_column("Service")
    tbl.add_column("URL")
    tbl.add_column("État")
    tbl.add_column("Extra pip")
    for probe in plan.services.values():
        reach = "[green]en ligne[/green]" if probe.reachable else "[dim]absent[/dim]"
        extra = "[green]installé[/green]" if probe.extra_installed else "[yellow]manquant[/yellow]"
        tbl.add_row(probe.id, probe.url, reach, extra)
    console.print(tbl)

    if plan.changes:
        console.print("\n[bold]Config[/bold]" + (" [green](écrite)[/green]" if apply else " [dim](plan — relancez avec --apply)[/dim]"))
        for change in plan.changes:
            key, new = change["key"], change["new"]
            if change.get("absent"):
                # Clé absente du fichier : c'est un ajout. Afficher une flèche
                # ``'neo4j' → 'neo4j'`` donnerait l'impression d'un no-op.
                effective = change["old"]
                suffix = "" if effective in (None, "") or effective == new else f" [dim](effectif : {effective!r})[/dim]"
                console.print(f"  [green]+[/green] [cyan]{key}[/cyan]: {new!r}{suffix}")
            else:
                console.print(f"  [yellow]~[/yellow] [cyan]{key}[/cyan]: {change['old']!r} → {new!r}")
    else:
        console.print("\n[green]Config déjà alignée sur ce que la machine peut servir.[/green]")

    # Les extras s'écrivent ``grimoire-kit[redis]`` : sans échappement, Rich
    # lit ``[redis]`` comme une balise et affiche une commande fausse.
    from rich.markup import escape

    if plan.warnings:
        console.print("\n[bold yellow]Non activé[/bold yellow]")
        for warning in plan.warnings:
            console.print(f"  {escape(warning)}")

    if plan.next_steps:
        console.print("\n[bold]Étapes suivantes[/bold]")
        for step in plan.next_steps:
            console.print(f"  [cyan]{escape(step)}[/cyan]")


# ── grimoire memory graph sync-memories ───────────────────────────────────────


@graph_app.command("sync-memories")
def memory_graph_sync_memories(ctx: typer.Context) -> None:
    """Rétro-projeter les souvenirs du store durable vers Neo4j.

    La projection se fait normalement à l'écriture. Quand le graphe est
    indisponible à ce moment-là — Neo4j éteint, extra absent, mot de passe non
    exporté — le vecteur atterrit et le nœud non, et rien ne rattrape l'écart
    ensuite : c'est la dérive que `memory status` signale sous `Parity`.

    L'opération fait un MERGE par identifiant : la relancer ne crée pas de
    doublon.
    """
    from grimoire.memory.projections import sync_memory_projection

    mgr, cfg, _ = _load_manager_context()
    graph = _load_neo4j_graph(cfg)
    try:
        stats = sync_memory_projection(graph, mgr.get_all())
    finally:
        graph.close()

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(stats, indent=2))
        return
    console.print(f"[green]Souvenirs projetés[/green] : {stats['projected']}")
    if stats["failed"]:
        console.print(f"  [red]échecs[/red] : {stats['failed']}")


# ── grimoire memory status ────────────────────────────────────────────────────


def _store_graph_parity(mgr: MemoryManager | None, store_entries: int) -> dict[str, Any]:
    """Cheap store↔graph parity probe — the drift a broken projection produces.

    Compares the durable store count with the Neo4j memory nodes and their
    ``WeaviateObject`` references.  This is the signal that catches an object
    written to the vector store whose graph node never landed: a full
    ``memory graph verify`` rebuilds the code graph and is far too heavy for a
    status command, whereas these are three COUNT queries.

    Returns an empty dict when no graph is wired — an absent graph is not drift.
    """
    graph = mgr.memory_graph if mgr is not None else None
    if graph is None:
        return {}
    try:
        stats = graph.stats()
        graph_memories = int(stats.get("memories", 0))
        vector_objects = int(stats.get("weaviate_objects", 0))
    except Exception as exc:  # a status probe never fails the command
        return {"error": str(exc)}
    return {
        "store_entries": store_entries,
        "graph_memories": graph_memories,
        "graph_vector_objects": vector_objects,
        "drift": store_entries - graph_memories,
        "ok": store_entries == graph_memories == vector_objects,
    }


@memory_app.command("status")
def memory_status(ctx: typer.Context) -> None:
    """Show memory backend health, entry count, and configuration.

    Diagnostic command: it never exits on a broken backend.  When the backend
    cannot even be instantiated (missing extra, unreachable server), the layer
    contract is still reported from config so the operator sees *what* is
    declared and *why* it is down — a diagnostic that fails when the subject
    fails is worthless.
    """
    cfg, root = _load_config_context()
    fmt = _get_fmt(ctx)

    mgr: MemoryManager | None = None
    backend_error = ""
    try:
        mgr = MemoryManager.from_config(cfg, project_root=root)
    except GrimoireMemoryError as exc:
        backend_error = str(exc)

    if mgr is not None:
        health = mgr.health_check()
        total = mgr.count()
        facts = mgr.facts_stats() if hasattr(mgr, "facts_stats") else {}
        diary = mgr.diary_stats() if hasattr(mgr, "diary_stats") else {}
    else:
        # The reason travels in the dedicated ``error`` field, not in ``detail``,
        # so it is never rendered twice.
        health = BackendStatus(backend=cfg.memory.backend, healthy=False, entries=0)
        total, facts, diary = 0, {}, {}

    architecture = build_memory_architecture_status(cfg, project_root=root, backend_status=health)
    parity = _store_graph_parity(mgr, total)

    if fmt == "json":
        typer.echo(json.dumps({
            "backend": health.backend,
            "healthy": health.healthy,
            "entries": total,
            "detail": health.detail,
            "error": backend_error,
            "parity": parity,
            "facts": facts,
            "diary": diary,
            "architecture": architecture.to_dict(),
        }, indent=2, default=str))
        return

    # ``\[`` escapes the bracket: Rich would otherwise parse ``[OK]``/``[x]`` as
    # markup tags and drop them, leaving the health marker invisible.
    status_icon = r"[green]\[OK][/green]" if health.healthy else r"[red]\[XX][/red]"
    console.print(f"{status_icon} Backend: [bold]{health.backend}[/bold]")
    console.print(f"  Entries : {total}")
    if backend_error:
        console.print(f"  [red]Backend unavailable:[/red] {backend_error}")
    if health.detail:
        for k, v in health.detail.items():
            console.print(f"  {k}: {v}")
    if facts:
        console.print(f"  Facts   : {facts.get('facts', 0)} active={facts.get('active_facts', 0)}")
    if diary:
        console.print(f"  Diary   : {diary.get('diary_entries', 0)} entries across {diary.get('agents', 0)} agents")
    if parity:
        if parity.get("error"):
            console.print(f"  [yellow]Parity  :[/yellow] graph unreachable — {parity['error']}")
        elif parity["ok"]:
            console.print(f"  Parity  : [green]store = graph = vectors ({total})[/green]")
        else:
            console.print(
                f"  [red]Parity  :[/red] store={parity['store_entries']} "
                f"graph={parity['graph_memories']} vectors={parity['graph_vector_objects']} "
                f"(drift={parity['drift']}) — run [cyan]grimoire memory graph sync-memories[/cyan]"
            )

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


