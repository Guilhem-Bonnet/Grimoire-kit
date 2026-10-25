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
import logging
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
    migrate_app,
)
from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory import profiles as memory_profiles
from grimoire.memory.architecture import build_memory_architecture_status
from grimoire.memory.backends.base import BackendStatus
from grimoire.memory.manager import MemoryManager

__all__ = ["memory_app"]


# ── grimoire memory up ────────────────────────────────────────────────────────

_up_profile_opt = typer.Option(
    "complet",
    "--profile",
    help=(
        "Composition cible : lexical | standard | graphe | complet "
        "(alias conservés : vector→standard, full→complet)."
    ),
)
_up_apply_opt = typer.Option(False, "--apply", help="Écrire le bloc memory: dans project-context.yaml.")
_up_start_opt = typer.Option(
    False, "--start",
    help=(
        "Démarrer (Docker) les services que le profil cible nécessite et que "
        "cette machine ne sert pas encore — jamais implicite, seulement sur "
        "ce drapeau. Sans effet sur `lexical`/`standard` (aucun service à "
        "démarrer, `standard` embarque Qdrant en local)."
    ),
)


@memory_app.command("up")
def memory_up(
    ctx: typer.Context,
    profile: str = _up_profile_opt,
    apply: bool = _up_apply_opt,
    start: bool = _up_start_opt,
) -> None:
    """Mettre en place la stack mémoire complète — plan par défaut, écriture avec --apply.

    Comble le trou laissé par ``grimoire init``, qui détecte un backend
    vectoriel mais laisse les couches graphe, code, tâches et chaude
    commentées dans le template.

    N'active que les services qui répondent : écrire ``memory_graph: neo4j``
    alors que Neo4j est éteint produirait une config qui échoue en silence.
    ``--start`` lève cette limite pour ``graphe``/``complet`` en démarrant
    d'abord (Docker Compose, gabarits du kit) ce qui manque — jamais sans ce
    drapeau explicite.

    Écrit toujours ``layer_profile`` et ``retrieval_mode`` cohérents avec ce
    qui est réellement servi (#527) — jamais avec le profil demandé si la
    machine ne peut pas le tenir.

    [dim]Examples:[/dim]
      [cyan]grimoire memory up[/cyan]                    Plan seul, rien n'est écrit
      [cyan]grimoire memory up --apply[/cyan]            Écrit le bloc memory:
      [cyan]grimoire memory up --profile standard[/cyan] Vecteurs sans graphe
      [cyan]grimoire memory up --profile complet --start --apply[/cyan]  Démarre Weaviate+Neo4j+Redis puis écrit
    """
    from grimoire.tools.memory_setup import PROFILES, apply_memory_plan, build_memory_plan, start_memory_stack

    if not memory_profiles.is_known(profile):
        console.print(f"[red]Profil inconnu :[/red] {profile} — attendu : {', '.join(PROFILES)}")
        raise typer.Exit(1)

    fmt = _get_fmt(ctx)
    started: list[str] = []
    if start:
        from grimoire.core.exceptions import GrimoireRuntimeError

        try:
            started = start_memory_stack(profile, Path.cwd())
        except GrimoireRuntimeError as exc:
            # Named refusal (unreadable bundled compose template, packaging
            # issue) — never an unhandled traceback for consent the user
            # explicitly gave via --start (#619 review).
            if fmt == "json":
                typer.echo(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            else:
                console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        if fmt != "json":
            for message in started:
                console.print(f"[dim]{message}[/dim]")

    plan = build_memory_plan(Path.cwd(), profile=profile)
    written = apply_memory_plan(plan) if apply else []

    if fmt == "json":
        payload = plan.to_dict()
        payload["applied"] = apply
        payload["written"] = written
        payload["started"] = started
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

    if plan.notes:
        console.print("\n[bold]À savoir[/bold]")
        for note in plan.notes:
            console.print(f"  {escape(note)}")

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
    graph = _load_neo4j_graph(cfg, ensure_schema=True)
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


# ── grimoire memory graph purge-orphans ───────────────────────────────────────

_purge_orphans_apply_opt = typer.Option(
    False, "--apply", help="Supprimer les nœuds orphelins. Sans ce drapeau : aperçu seul (dry-run).",
)


@graph_app.command("purge-orphans")
def memory_graph_purge_orphans(ctx: typer.Context, apply: bool = _purge_orphans_apply_opt) -> None:
    """Retirer les nœuds ``GrimoireMemory`` sans entrée correspondante dans le store.

    ``sync-memories`` n'est qu'additif : un souvenir supprimé du store, ou un
    essai antérieur qui a écrit sous une autre collection, laisse des nœuds
    orphelins pour toujours (cas réel : store=75, graph=3782, #527). Borné à
    la collection du projet (et à l'ancienne collection générique
    ``GrimoireMemory`` d'avant les profils) pour ne jamais toucher un autre
    projet qui partagerait la même instance Neo4j.

    Dry-run par défaut : relancer avec [cyan]--apply[/cyan] pour supprimer.
    """
    from grimoire.memory.projections import prune_orphan_memories

    mgr, cfg, _ = _load_manager_context()
    collection = cfg.memory.weaviate_collection or cfg.memory.collection_prefix
    graph = _load_neo4j_graph(cfg)
    try:
        result = prune_orphan_memories(graph, mgr.get_all(), collection=collection, apply=apply)
    finally:
        graph.close()

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(result, indent=2))
        return

    verb = "Supprimés" if apply else "Candidats (dry-run)"
    console.print(f"[bold]{verb}[/bold] : {result['candidates']} nœud(s) hors collection {result['collection']!r}")
    if apply:
        console.print(f"  [green]purgés[/green] : {result['purged']}")
    elif result["candidates"]:
        console.print("  Relancez avec [cyan]--apply[/cyan] pour supprimer.")


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
    retrieval = (
        memory_profiles.retrieval_in_service(mgr.prefers_hybrid, cfg.memory.layer_profile)
        if mgr is not None else None
    )

    if fmt == "json":
        typer.echo(json.dumps({
            "backend": health.backend,
            "healthy": health.healthy,
            "entries": total,
            "detail": health.detail,
            "error": backend_error,
            "parity": parity,
            "retrieval": retrieval,
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
    if retrieval:
        note = "" if retrieval["honored"] else f" (le profil {retrieval['profile']} annonce l'hybride)"
        console.print(f"  Recherche : {retrieval['label']}{note}")
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




# ── grimoire memory lint ──────────────────────────────────────────────────────


@memory_app.command("lint")
def memory_lint(
    ctx: typer.Context,
    project_root: Path = typer.Option(Path(), "--project-root", help="Racine du projet."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Rapport JSON sur stdout."),
    allow_empty: bool = typer.Option(False, "--allow-empty", help="Accepter une mémoire vide (code 0)."),
) -> None:
    """Cohérence de la mémoire : contradictions, doublons, orphelins, péremption.

    Sort en code 1 dès qu'une erreur est relevée (une contradiction entre deux
    fichiers de mémoire en est une) ; code 2 si aucune mémoire n'a été lue
    (sauf --allow-empty) ; les avertissements n'échouent pas.
    """
    from grimoire.cli.cmd_memory import _load_config_context
    from grimoire.tools.memory_lint import MemoryLint

    # Une racine explicitement donnée mais absente est refusée : sans cela, la
    # remontée adopterait le projet d'un répertoire ancêtre.
    if not project_root.is_dir():
        console.print("[red]Not a Grimoire project[/red] — dossier introuvable ; run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1)
    # Même résolution de racine que les commandes sœurs : remonte jusqu'au
    # project-context.yaml, sort en 1 hors projet.
    _cfg, root = _load_config_context(project_root)
    report = MemoryLint(root).run()
    if as_json or _get_fmt(ctx) == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        console.print(
            f"{report.files_scanned} fichiers, {report.entries_scanned} entrées : "
            f"{report.error_count} erreur(s), {report.warning_count} avertissement(s)"
        )
        for issue in report.issues:
            if issue.severity == "error":
                console.print(f"[red]✗[/red] {issue.title} — {issue.description}")
    if report.error_count:
        raise typer.Exit(1)
    if report.files_scanned == 0 and not allow_empty:
        # Rien lu = rien vérifié : ce n'est pas un vert.
        console.print("[red]Aucune mémoire lue[/red] — rien n'a été vérifié (--allow-empty pour l'accepter).")
        raise typer.Exit(2)


# ── grimoire memory doctor ─────────────────────────────────────────────────────

_doctor_apply_opt = typer.Option(
    False, "--apply",
    help="Purger les nœuds de graphe orphelins trouvés. Sans ce drapeau : aperçu seul (dry-run).",
)


@memory_app.command("doctor")
def memory_doctor(ctx: typer.Context, apply: bool = _doctor_apply_opt) -> None:
    """Entretien minimal de la pile mémoire — santé des couches, dérive du graphe, purge sur confirmation.

    Trois choses, jamais plus, et jamais un ``FAIL`` : c'est un diagnostic,
    pas une passerelle. (1) Santé des couches et pile démarrée ou non — un
    projet peut être *configuré* pour ``complet``/``graphe`` (backend et
    connexions écrits par ``grimoire init``) sans que les conteneurs aient
    jamais tourné (arbitrage 2026-09-18 sur #619) : signalé ici comme ailleurs
    (``grimoire doctor``), avec la commande de démarrage. (2) Dérive du
    graphe : nœuds ``GrimoireMemory`` sans fiche source dans le store durable
    — la même sonde que ``grimoire memory graph purge-orphans``, réutilisée
    telle quelle. (3) Purge sur confirmation (``--apply`` — dry-run sinon,
    identique au contrat de ``purge-orphans``) ; une sauvegarde
    (``grimoire memory migrate export-bundle``) est recommandée avant, cette
    commande ne la lance jamais elle-même.

    [dim]Examples:[/dim]
      [cyan]grimoire memory doctor[/cyan]         Aperçu — rien n'est modifié
      [cyan]grimoire memory doctor --apply[/cyan]  Purge les orphelins trouvés
    """
    cfg, root = _load_config_context()
    fmt = _get_fmt(ctx)

    mgr: MemoryManager | None = None
    backend_error = ""
    try:
        mgr = MemoryManager.from_config(cfg, project_root=root)
    except GrimoireMemoryError as exc:
        backend_error = str(exc)

    health = (
        mgr.health_check() if mgr is not None
        else BackendStatus(backend=cfg.memory.backend, healthy=False, entries=0)
    )
    architecture = build_memory_architecture_status(cfg, project_root=root, backend_status=health)

    from grimoire.tools.memory_setup import unreached_configured_services

    not_started = unreached_configured_services(cfg.memory)

    drift: dict[str, Any] | None = None
    drift_error = ""
    if cfg.memory.knowledge_graph == "neo4j" and cfg.memory.neo4j_uri:
        try:
            from grimoire.memory.projections import prune_orphan_memories

            # Same loader `grimoire memory graph purge-orphans` uses — a
            # missing URI/password raises `typer.Exit`, caught below like any
            # other reason this probe cannot run right now.
            graph = _load_neo4j_graph(cfg)
        except Exception as exc:  # un diagnostic ne doit jamais planter sur son sujet
            drift_error = str(exc) or "Neo4j indisponible"
        else:
            try:
                entries = mgr.get_all() if mgr is not None else []
                collection = cfg.memory.weaviate_collection or cfg.memory.collection_prefix
                drift = prune_orphan_memories(graph, entries, collection=collection, apply=apply)
            except Exception as exc:
                drift_error = str(exc)
            finally:
                graph.close()

    if fmt == "json":
        typer.echo(json.dumps({
            "backend": health.backend,
            "healthy": health.healthy,
            "backend_error": backend_error,
            "not_started": not_started,
            "architecture": architecture.to_dict(),
            "graph_drift": drift,
            "graph_drift_error": drift_error,
            "applied": apply,
        }, indent=2, default=str))
        return

    status_icon = r"[green]\[OK][/green]" if health.healthy else r"[red]\[XX][/red]"
    console.print(f"{status_icon} Backend : [bold]{health.backend}[/bold] (profil {cfg.memory.layer_profile})")
    if backend_error:
        console.print(f"  [red]Indisponible :[/red] {backend_error}")

    if not_started:
        console.print(
            f"  [yellow]Pile non démarrée[/yellow] : {', '.join(not_started)} — "
            f"[cyan]grimoire memory up --profile {cfg.memory.layer_profile} --start --apply[/cyan]"
        )

    console.print("\n[bold]Couches[/bold]")
    tbl = Table(show_header=True)
    tbl.add_column("Layer")
    tbl.add_column("State")
    tbl.add_column("Backend")
    for layer in architecture.layers:
        state_style = {"ready": "green", "partial": "yellow", "planned": "cyan", "disabled": "dim"}.get(layer.state, "white")
        tbl.add_row(layer.label, f"[{state_style}]{layer.state}[/{state_style}]", layer.backend)
    console.print(tbl)

    console.print("\n[bold]Dérive du graphe[/bold]")
    if drift_error:
        console.print(f"  [yellow]Non vérifiable :[/yellow] {drift_error}")
    elif drift is None:
        console.print("  [dim]Pas de graphe configuré (memory.knowledge_graph != neo4j).[/dim]")
    else:
        candidates = drift["candidates"]
        if candidates == 0:
            console.print("  [green]Aucun nœud orphelin.[/green]")
        elif apply:
            console.print(f"  [green]Purgés[/green] : {drift['purged']}/{candidates} nœud(s) sans fiche source.")
        else:
            console.print(
                f"  [yellow]{candidates}[/yellow] nœud(s) sans fiche source dans le store durable "
                f"(collection {drift['collection']!r})."
            )
            console.print(
                "  Sauvegarde recommandée avant toute purge : "
                "[cyan]grimoire memory migrate export-bundle[/cyan]"
            )
            console.print("  Relancez avec [cyan]--apply[/cyan] pour purger.")




# ── grimoire memory split ─────────────────────────────────────────────────────

_split_source_opt = typer.Option(
    "", "--source-collection",
    help="Collection partagée à découper (défaut : celle de la configuration courante).",
)
_split_projects_opt = typer.Option(
    "", "--projects", help="Ne découper que ces projets (noms séparés par des virgules). Défaut : tous.",
)
_split_export_opt = typer.Option(
    None, "--export", help="Fichier JSON de sauvegarde de la source (défaut : _grimoire/_memory/migration/split-*.json).",
)
_split_unassigned_opt = typer.Option(
    False, "--allow-unassigned",
    help="Autoriser --apply alors que des souvenirs sans project_name resteront dans le segment partagé "
    "(donc invisibles pour les projets repointés).",
)
_split_apply_opt = typer.Option(
    False, "--apply", help="Copier et vérifier. Sans ce drapeau : aperçu seul. La source n'est jamais supprimée.",
)


def _registry_configs(source_identity: tuple[str, str], current_root: Path) -> dict[str, Path]:
    """``nom de projet → project-context.yaml`` pour les projets du registre qui lisent la source."""
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import store_identity
    from grimoire.tools.project_registry import load_registry

    found: dict[str, Path] = {}
    seen: set[Path] = set()
    candidates = [str(current_root)] + [str(e.get("path", "")) for e in load_registry()]
    for raw in candidates:
        if not raw:
            continue
        root = Path(raw)
        config_path = root / "project-context.yaml"
        if config_path in seen or not config_path.is_file():
            continue
        seen.add(config_path)
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
        except Exception as exc:  # un projet du registre illisible n'arrête pas la découpe des autres
            logging.getLogger(__name__).info("registre : %s ignoré (%s)", config_path, exc)
            continue
        if store_identity(cfg, root) == source_identity:
            found.setdefault(cfg.project.name, config_path)
    return found


@memory_app.command("split")
def memory_split(
    ctx: typer.Context,
    source_collection: str = _split_source_opt,
    projects: str = _split_projects_opt,
    export: Path | None = _split_export_opt,
    apply: bool = _split_apply_opt,
    allow_unassigned: bool = _split_unassigned_opt,
) -> None:
    """Découper un segment mémoire partagé en un segment par projet — identifiants conservés.

    Groupe les souvenirs de la collection partagée par ``project_name``, les copie par
    upsert dans ``<collection>-<projet>`` (mêmes identifiants, donc ``superseded_by``
    reste valide), vérifie les comptes et les identifiants, puis repointe
    ``memory.collection_prefix`` de chaque projet du registre qui lisait le segment.

    Aperçu par défaut. Avec ``--apply`` : export JSON de la source d'abord, copie, vérification,
    mise à jour des configurations seulement si tout est vérifié. La source n'est ni modifiée
    ni supprimée ; relançable sans doublon.
    """
    import time
    from dataclasses import replace as _dc_replace

    from grimoire.memory.manager import store_identity
    from grimoire.memory.split import SplitError, apply_split, plan_split, render_collection_prefix
    from grimoire.memory.store_probe import store_exists

    cfg, root = _load_config_context()
    source_mem = _dc_replace(cfg.memory, collection_prefix=source_collection or cfg.memory.collection_prefix)
    if source_collection and source_mem.backend == "weaviate-server":
        source_mem = _dc_replace(source_mem, weaviate_collection=source_collection)
    source_cfg = _dc_replace(cfg, memory=source_mem)
    if store_exists(source_cfg, root) is False:
        console.print(
            f"[red]Source introuvable[/red] ({source_mem.backend}, {source_mem.collection_prefix!r}) : aucun store de ce nom. "
            "Vérifiez --source-collection ; rien n'a été créé."
        )
        raise typer.Exit(2)
    try:
        source_mgr = MemoryManager.from_config(source_cfg, project_root=root)
    except GrimoireMemoryError as exc:
        console.print(f"[red]Backend source indisponible ({source_mem.backend}) :[/red] {exc}")
        raise typer.Exit(1) from None
    entries = source_mgr.get_all()

    wanted = [p.strip() for p in projects.split(",") if p.strip()]
    try:
        plan = plan_split(
            entries,
            source_collection=source_mem.collection_prefix,
            configs=_registry_configs(store_identity(source_cfg, root), root),
            only=wanted,
        )
    except SplitError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None

    def _target_cfg(collection: str) -> Any:
        mem = _dc_replace(source_mem, collection_prefix=collection)
        if mem.backend == "weaviate-server":  # Weaviate lit weaviate_collection avant collection_prefix
            mem = _dc_replace(mem, weaviate_collection=collection)
        return _dc_replace(cfg, memory=mem)

    if apply and plan.groups:
        source_identity = store_identity(source_cfg, root)
        same = [g.collection for g in plan.groups if store_identity(_target_cfg(g.collection), root) == source_identity]
        if same:
            console.print(
                f"[red]Refus : la cible {same[0]!r} désigne le même store que la source[/red] — la copie "
                "s'écrirait sur elle-même, puis les configurations pointeraient vers rien. Rien n'est écrit."
            )
            raise typer.Exit(2)
        if plan.unassigned and not allow_unassigned:
            console.print(
                f"[red]Refus : {len(plan.unassigned)} souvenir(s) sans project_name ne seraient copiés nulle part.[/red] "
                "Les projets repointés ne les verraient plus. Attribuez-les d'abord, ou passez "
                "--allow-unassigned en connaissance de cause. Rien n'est écrit."
            )
            raise typer.Exit(2)

    report = None
    if apply and plan.groups:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        export_path = export or root / "_grimoire" / "_memory" / "migration" / f"split-{stamp}.json"

        def _open_target(collection: str) -> MemoryManager:
            return MemoryManager.from_config(_target_cfg(collection), project_root=root)

        try:
            report = apply_split(
                plan, entries, export_path=export_path, open_target=_open_target, render_config=render_collection_prefix,
            )
        except SplitError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"apply": apply, **plan.to_dict(), "report": report.to_dict() if report else None}, indent=2))
        return

    table = Table(title=f"Découpe de « {plan.source_collection} » ({plan.total} souvenir(s))")
    table.add_column("Projet")
    table.add_column("Souvenirs", justify="right")
    table.add_column("Collection cible")
    table.add_column("Configuration à repointer")
    for g in plan.groups:
        table.add_row(g.project, str(g.count), g.collection, str(g.config_path) if g.config_path else "[dim]hors registre[/dim]")
    console.print(table)
    if plan.unassigned:
        consequence = (
            "--allow-unassigned est passé : ils deviennent invisibles pour les projets repointés."
            if allow_unassigned
            else "--apply est refusé sans --allow-unassigned (ils deviendraient invisibles pour les projets repointés)."
        )
        console.print(f"  [yellow]{len(plan.unassigned)} souvenir(s) sans project_name[/yellow] restent dans le segment partagé : {consequence}")
    if report is None:
        console.print("  Aperçu : aucune donnée n'est écrite. Relancez avec [cyan]--apply[/cyan] (export JSON d'abord, source intacte).")
        return
    console.print(f"  [green]Copiés et vérifiés[/green] : {sum(report.copied.values())} — export : {report.export_path}")
    for path in report.configs_updated:
        console.print(f"  [green]config repointée[/green] : {path}")
    console.print("  La source est intacte : la supprimer est un geste séparé, une fois la découpe vérifiée.")


# ── grimoire memory migrate run ───────────────────────────────────────────────
# Déplacé depuis cmd_memory (plafond de taille, ratchet R2) ; s'enregistre sur ``migrate_app``.

_migrate_run_source_opt = typer.Option(
    "",
    "--source",
    help="Backend d'origine (ex: lexical). Détecté depuis la sauvegarde de `memory up --apply` si omis.",
)
_migrate_run_apply_opt = typer.Option(
    False, "--apply", help="Écrire dans le backend courant. Sans ce drapeau : aperçu seul.",
)
_migrate_run_project_opt = typer.Option(
    "", "--project", help="Ne migrer que les souvenirs de ce projet (metadata.project_name).",
)
_migrate_run_overwrite_opt = typer.Option(
    False, "--overwrite",
    help="Remplacer aussi les identifiants déjà présents dans la cible (défaut : ils sont laissés tels quels).",
)
_migrate_run_all_projects_opt = typer.Option(
    False, "--all-projects",
    help="Avec --source-collection sans --project : accepter que les souvenirs des autres projets du segment entrent.",
)
_migrate_run_collection_opt = typer.Option(
    "", "--source-collection",
    help="Collection de la source, si elle diffère de celle de la cible (même type de backend accepté).",
)


@migrate_app.command("run")
def memory_migrate_run(
    ctx: typer.Context,
    source: str = _migrate_run_source_opt,
    apply: bool = _migrate_run_apply_opt,
    project: str = _migrate_run_project_opt,
    source_collection: str = _migrate_run_collection_opt,
    overwrite: bool = _migrate_run_overwrite_opt,
    all_projects: bool = _migrate_run_all_projects_opt,
) -> None:
    """Migrer les souvenirs d'un ancien backend vers le backend courant — sans bundle ni fichier.

    Couvre le cas laissé sans commande par #527 : passer d'un backend lexical
    (BM25, pas de vecteur) à un backend vectoriel. ``migrate export-bundle`` /
    ``import-weaviate`` ne savent lire qu'un Qdrant ; ici la source est
    n'importe quel backend Memory OS, lu via l'API publique du manager
    (``get_all``) puis réécrit dans le backend courant, qui recalcule ses
    propres embeddings. Remplace le couple manuel `memory export` + `memory
    import`.

    La source est ``--source``, sinon ``memory.migration_source_backend``,
    sinon la sauvegarde laissée par le dernier `memory up --apply` qui a
    changé de backend.

    La copie se fait par upsert **sous l'identifiant d'origine** (``superseded_by``
    reste valide, ``created_at`` est conservé) et **ne crée que les identifiants
    absents de la cible** : une relance ne défait pas une décision revue ;
    ``--overwrite`` remplace explicitement. Un registre des identifiants déjà copiés
    évite de recréer un souvenir copié puis supprimé dans la cible.
    Un souvenir que la cible refuse est compté et listé, la copie continue ; une
    interruption laisse la source intacte. ``--project`` ne copie que les souvenirs d'un
    projet ; ``--source-collection`` lit une autre collection du même type de
    backend (découpe d'un segment partagé).
    """
    from dataclasses import replace as _dc_replace

    from grimoire.memory.manager import store_identity
    from grimoire.memory.migration import (
        MigrationInterruptedError,
        copied_registry_path,
        load_copied,
        migrate_backend_to_backend,
        portable_metadata,
        read_previous_backend_breadcrumb,
        save_copied,
    )
    from grimoire.memory.store_probe import store_exists

    cfg, root = _load_config_context()
    target_backend = cfg.memory.backend
    source_backend = source or cfg.memory.migration_source_backend or read_previous_backend_breadcrumb(root)
    if not source_backend:
        console.print(
            "[red]Backend source inconnu.[/red] Passez --source, ou relancez juste après "
            "`grimoire memory up --apply` (qui laisse la trace du backend précédent)."
        )
        raise typer.Exit(2)
    source_mem = _dc_replace(cfg.memory, backend=source_backend)
    if source_collection:
        source_mem = _dc_replace(
            source_mem, collection_prefix=source_collection,
            weaviate_collection=source_collection if source_backend == "weaviate-server" else source_mem.weaviate_collection,
        )
    source_cfg = _dc_replace(cfg, memory=source_mem)
    source_key, target_key = repr(store_identity(source_cfg, root)), repr(store_identity(cfg, root))
    if source_key == target_key:
        console.print(f"[yellow]Source et cible identiques ({target_backend}) — rien à migrer.[/yellow]")
        return

    # Les constructeurs de backends créent ce qu'ils ouvrent : on s'assure d'abord que la source existe,
    # sinon une faute de frappe créerait une source vide et afficherait « 0 souvenir ».
    if store_exists(source_cfg, root) is False:
        console.print(
            f"[red]Source introuvable[/red] ({source_backend}, {source_mem.collection_prefix!r}) : aucun store de ce nom. "
            "Vérifiez --source et --source-collection ; rien n'a été créé."
        )
        raise typer.Exit(2)
    try:
        source_mgr = MemoryManager.from_config(source_cfg, project_root=root)
    except GrimoireMemoryError as exc:
        console.print(f"[red]Backend source indisponible ({source_backend}):[/red] {exc}")
        raise typer.Exit(1) from None
    entries = source_mgr.get_all()

    if source_collection and not project and not all_projects:
        names = {str(e.metadata.get("project_name", "")) for e in entries} - {cfg.project.name}
        if names:
            shown = ", ".join(sorted(n or "(sans project_name)" for n in names))
            console.print(
                f"[red]Le segment « {source_collection} » contient des souvenirs d'autres projets ou sans projet "
                f"({shown}).[/red] Passez --project {cfg.project.name} pour ne copier que les vôtres, ou "
                "--all-projects pour accepter que les autres entrent aussi."
            )
            raise typer.Exit(2)

    target_mgr = None
    if apply or store_exists(cfg, root) is not False:
        try:
            target_mgr, _, _ = _load_manager_context()
        except typer.Exit:
            if apply:
                raise
            if _get_fmt(ctx) != "json":  # la sortie JSON reste un document unique
                console.print("[yellow]Cible injoignable : l'aperçu ne peut pas dire ce qui existe déjà.[/yellow]")
    elif _get_fmt(ctx) != "json":
        console.print("[dim]La cible n'existe pas encore (rien n'est créé par l'aperçu) : tout y serait nouveau.[/dim]")

    registry = copied_registry_path(root, source_key, target_key)
    copied = load_copied(registry)
    validate_id = None
    if target_backend in ("qdrant-server", "qdrant-local", "ollama"):
        from grimoire.memory.backends._qdrant_mixin import _point_id

        def validate_id(entry_id: str) -> str | None:
            try:
                _point_id(entry_id)
            except ValueError as exc:
                return str(exc)
            return None

    try:
        result = migrate_backend_to_backend(
            entries,
            source_backend=source_backend,
            target_backend=target_backend,
            upsert=(
                (lambda e: target_mgr.upsert(
                    e.id, e.text, user_id=e.user_id, tags=e.tags, metadata=portable_metadata(e),
                    created_at=e.created_at,
                ))
                if target_mgr is not None and apply else None
            ),
            exists=(lambda i: target_mgr.recall(i) is not None) if target_mgr is not None else None,
            validate_id=validate_id,
            already_copied=copied,
            overwrite=overwrite,
            dry_run=not apply,
            project=project,
        )
    except MigrationInterruptedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    finally:
        if apply:  # même interrompue, la copie déjà faite est consignée
            try:
                save_copied(registry, copied, source_key=source_key, target_key=target_key)
            except OSError as exc:
                console.print(f"[yellow]Registre des copies non écrit ({exc}) : une suppression dans la cible pourrait revenir.[/yellow]")

    all_refused = apply and bool(result.refused) and result.imported == 0
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2))
        if all_refused:
            raise typer.Exit(1)
        return

    verb = "Migrés" if apply else "À migrer (aperçu)"
    console.print(f"[bold]{source_backend} → {target_backend}[/bold]")
    console.print(f"  {verb} : {result.entries} souvenir(s)")
    if project:
        console.print(f"  Projet « {project} » : {result.filtered_out} souvenir(s) d'autres projets laissés")
    console.print(
        f"  Nouveaux : {result.created} — déjà dans la cible : {result.skipped_existing} (laissés tels quels)"
        + (f" — à écraser : {result.overwritten}" if overwrite else "")
    )
    if result.skipped_deleted:
        console.print(
            f"  {result.skipped_deleted} souvenir(s) copiés puis supprimés dans la cible : non recréés "
            f"(registre : {registry.name})."
        )
    for entry_id, reason in result.refused:
        console.print(f"  [red]refusé[/red] {entry_id} : {reason}")
    if apply:
        console.print(f"  [green]écrits[/green] : {result.imported}")
    else:
        console.print("  Aperçu : aucune donnée n'est écrite. Relancez avec [cyan]--apply[/cyan] pour copier.")
    if all_refused:
        raise typer.Exit(1)
