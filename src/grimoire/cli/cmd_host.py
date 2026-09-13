"""CLI ``grimoire host`` — projeter le projet sur les surfaces de chaque hôte.

Une seule description du projet (agents, compétences, commandes, gouvernance),
autant de rendus qu'il y a d'hôtes. ``sync`` écrit, ``status`` dit ce qui est
réellement exécuté par l'hôte courant, ``hook`` est le point d'entrée que les
configurations générées invoquent.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.bridges.host import HostBridge
from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import all_profiles, gaps_for, profile_for, resolve_host
from grimoire.hosts.collect import build_surface, collect_commands, entry_agent_name
from grimoire.hosts.detection import alias_for_host, enabled_host_ids
from grimoire.hosts.emitters import apply_plan, emitter_for, owned_managed_paths, supported_hosts
from grimoire.hosts.runtime import parse_event, run_hook
from grimoire.hosts.surface import Enforcement

host_app = typer.Typer(help="Surfaces hôtes : agents, compétences, commandes et hooks par hôte.")
console = Console()

_PROJECT_ROOT_OPTION = typer.Option(Path.cwd(), "--project-root", help="Racine du projet.", show_default=False)
_HOST_OPTION = typer.Option("auto", "--host", help="claude | copilot | codex | cursor | gemini | all | auto.")
_DRY_RUN_OPTION = typer.Option(False, "--dry-run", help="Montrer sans écrire.")
_FORCE_OPTION = typer.Option(False, "--force", help="Écraser un fichier non généré occupant un chemin géré.")
_FORCE_HOST_OPTION = typer.Option(
    False, "--force-host", help="Synchroniser un hôte non activé (ignore `hosts.enabled`)."
)
_PRUNE_DISABLED_OPTION = typer.Option(
    False,
    "--prune-disabled",
    help="Supprimer les fichiers gérés des hôtes désactivés (opt-in, jamais par défaut).",
)
_HOOK_HOST_OPTION = typer.Option(..., "--host", help="Hôte appelant.")
_HOOK_EVENT_OPTION = typer.Option(..., "--event", help="Événement de cycle de vie.")
_HOOK_ROOT_OPTION = typer.Option(None, "--project-root", help="Racine du projet.")
_HOOK_DECISION_OPTION = typer.Option(None, "--decision", help="Décision explicite à exécuter.")
_RUN_SLUG_ARGUMENT = typer.Argument(..., help="Slug de la commande.")


def _get_fmt(ctx: typer.Context) -> str:
    """Output format from the root app's global ``-o/--output``."""
    return str((ctx.obj or {}).get("output", "text") or "text")


@dataclass(slots=True)
class _HostStatus:
    """One host's answer to: does this project run here as it claims to?"""

    host: str
    display_name: str
    in_sync: bool
    pending: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blocking_hooks: list[str] = field(default_factory=list)
    unsupported_events: list[str] = field(default_factory=list)
    degradations: list[dict[str, str]] = field(default_factory=list)
    capability_gaps: list[str] = field(default_factory=list)
    agents_with_inferred_tools: list[str] = field(default_factory=list)
    #: Persona d'entrée retenue ; vide si le projet n'en veut aucune ou si la
    #: clé nomme un agent qui n'existe pas. La clé déclarée est gardée à part
    #: pour que l'écart soit visible.
    entry_agent: str = ""
    entry_agent_declared: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "display_name": self.display_name,
            "in_sync": self.in_sync,
            "pending": self.pending,
            "conflicts": self.conflicts,
            "blocking_hooks": self.blocking_hooks,
            "unsupported_events": self.unsupported_events,
            "degradations": self.degradations,
            "capability_gaps": self.capability_gaps,
            "agents_with_inferred_tools": self.agents_with_inferred_tools,
            "entry_agent": self.entry_agent,
            "entry_agent_declared": self.entry_agent_declared,
        }


def _detected_host() -> HostId:
    return HostBridge().detect().host_id


def _resolve_targets(host: str, project_root: Path) -> list[HostId]:
    """Hôtes ciblés par *host*.

    ``"all"`` se filtre sur ``hosts.enabled`` (issue #177) — jamais la
    totalité des hôtes connus par défaut. ``"auto"`` reste la détection de
    l'hôte CLI courant (le processus qui appelle), une notion distincte de la
    déclaration projet, volontairement non filtrée.
    """
    key = host.strip().lower()
    if key == "all":
        enabled = enabled_host_ids(project_root)
        return [h for h in supported_hosts() if h in enabled]
    if key == "auto":
        detected = _detected_host()
        if emitter_for(detected) is None:
            console.print(
                "[yellow]Hôte non détecté[/yellow] — précisez `--host claude|copilot|codex|cursor|gemini|all`."
            )
            raise typer.Exit(code=1)
        return [detected]
    resolved = resolve_host(key)
    if resolved is None or emitter_for(resolved) is None:
        console.print(f"[red]Hôte inconnu : {host}[/red]")
        raise typer.Exit(code=1)
    return [resolved]


@host_app.command("list")
def host_list(ctx: typer.Context) -> None:
    """Lister les hôtes connus et ce qu'ils savent exécuter."""
    detected = _detected_host()
    profiles = all_profiles()
    if _get_fmt(ctx) == "json":
        typer.echo(
            json.dumps(
                {"detected": detected.value, "hosts": [p.to_dict() for p in profiles]},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    table = Table(title="Hôtes connus")
    for column in ("Hôte", "Sous-agents", "Skills", "Commandes", "Hooks bloquants", "Permissions"):
        table.add_column(column)

    def mark(value: bool) -> str:
        return "[green]oui[/green]" if value else "[yellow]non[/yellow]"

    for profile in profiles:
        name = profile.display_name + (" [cyan](détecté)[/cyan]" if profile.host_id == detected else "")
        table.add_row(
            name,
            mark(profile.subagents_native),
            mark(profile.skills_native),
            mark(profile.commands_native),
            mark(profile.blocking_hooks),
            mark(profile.permissions_native),
        )
    console.print(table)


@host_app.command("surface")
def host_surface(ctx: typer.Context, project_root: Path = _PROJECT_ROOT_OPTION) -> None:
    """Afficher la description host-neutre du projet."""
    surface = build_surface(project_root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(surface.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{surface.project_name}[/bold] — standard {'gouverné' if surface.governed else 'non enrôlé'}")
    console.print(
        f"  agents {len(surface.agents)} · compétences {len(surface.skills)} · "
        f"commandes {len(surface.commands)} · hooks {len(surface.hooks)}"
    )
    for hook in surface.hooks:
        tag = "[red]bloquant[/red]" if hook.enforcement is Enforcement.BLOCKING else "[dim]indicatif[/dim]"
        console.print(f"  {hook.event.value} {tag} — {hook.rationale}")


@host_app.command("sync")
def host_sync(
    ctx: typer.Context,
    project_root: Path = _PROJECT_ROOT_OPTION,
    host: str = _HOST_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
    force: bool = _FORCE_OPTION,
    force_host: bool = _FORCE_HOST_OPTION,
    prune_disabled: bool = _PRUNE_DISABLED_OPTION,
) -> None:
    """Générer les surfaces d'un hôte (ou de tous) depuis le projet.

    Un ``--host`` explicite non activé (``hosts.enabled`` dans
    ``project-context.yaml``) est un refus nommé, sauf ``--force-host``
    (issue #177) : un hôte désactivé n'est jamais synchronisé sans le dire.
    """
    key = host.strip().lower()
    if key not in ("all", "auto"):
        resolved = resolve_host(key)
        known = resolved is not None and resolved in supported_hosts()
        refused = known and resolved not in enabled_host_ids(project_root) and not force_host
        if refused:
            assert resolved is not None  # narrowed by `known` above
            alias = alias_for_host(resolved)
            console.print(
                f"[red]Hôte {alias} non activé[/red] — ajoute-le à `hosts.enabled` dans "
                "project-context.yaml, ou relance avec --force-host."
            )
            raise typer.Exit(code=1)

    surface = build_surface(project_root)
    results = []
    for host_id in _resolve_targets(host, project_root):
        emitter = emitter_for(host_id)
        if emitter is None:  # pragma: no cover - filtered by _resolve_targets
            continue
        plan = emitter.plan(surface, project_root)
        results.append(apply_plan(plan, project_root, dry_run=dry_run, force=force))

    pruned: list[str] = []
    if prune_disabled:
        enabled = enabled_host_ids(project_root)
        for host_id in supported_hosts():
            if host_id in enabled:
                continue
            emitter = emitter_for(host_id)
            if emitter is None:  # pragma: no cover - registry is complete
                continue
            plan = emitter.plan(surface, project_root)
            for path in owned_managed_paths(plan, project_root):
                label = path.resolve().relative_to(project_root.resolve()).as_posix()
                if not dry_run:
                    path.unlink()
                pruned.append(label)

    if _get_fmt(ctx) == "json":
        payload: dict[str, Any] = {"hosts": [r.to_dict() for r in results]}
        if prune_disabled:
            payload["pruned"] = pruned
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if all(r.ok for r in results) else 1)

    action = "Écrirait" if dry_run else "Écrit"
    for result in results:
        console.print(f"[bold]{profile_for(result.host_id).display_name}[/bold]")
        console.print(f"  [green]{action}[/green] {len(result.written)} · inchangé {len(result.unchanged)}")
        for label in result.written:
            console.print(f"    [green][OK][/green] {label}")
        for label in result.skipped:
            console.print(f"    [yellow][!][/yellow] {label} — fichier non généré, préservé (utilisez --force)")
        for degradation in result.degradations:
            console.print(f"    [dim]dégradé[/dim] {degradation.surface} — repli : {degradation.fallback}")
    if prune_disabled:
        prune_action = "Retirerait" if dry_run else "Retiré"
        console.print(f"[bold]Hôtes désactivés[/bold] — {prune_action} {len(pruned)} fichier(s) orphelin(s)")
        for label in pruned:
            console.print(f"    [yellow][-][/yellow] {label}")
    raise typer.Exit(0 if all(r.ok for r in results) else 1)


@host_app.command("status")
def host_status(
    ctx: typer.Context,
    project_root: Path = _PROJECT_ROOT_OPTION,
    host: str = _HOST_OPTION,
) -> None:
    """Comparer ce que le projet déclare à ce que l'hôte exécute vraiment."""
    surface = build_surface(project_root)
    payload: list[_HostStatus] = []
    for host_id in _resolve_targets(host, project_root):
        emitter = emitter_for(host_id)
        if emitter is None:  # pragma: no cover
            continue
        profile = profile_for(host_id)
        plan = emitter.plan(surface, project_root)
        pending = apply_plan(plan, project_root, dry_run=True)
        payload.append(
            _HostStatus(
                host=host_id.value,
                display_name=profile.display_name,
                in_sync=not pending.written and not pending.skipped,
                pending=list(pending.written),
                conflicts=list(pending.skipped),
                blocking_hooks=[h.event.value for h in surface.hooks if h.enforcement is Enforcement.BLOCKING],
                unsupported_events=[h.event.value for h in surface.hooks if not profile.supports_event(h.event)],
                degradations=[d.to_dict() for d in plan.degradations],
                capability_gaps=[g.surface for g in gaps_for(profile)],
                agents_with_inferred_tools=[a.name for a in surface.agents if a.tools_origin == "inferred"],
                entry_agent=(entry.name if (entry := surface.entry_agent()) else ""),
                entry_agent_declared=entry_agent_name(project_root),
            )
        )

    # Orphelins d'un hôte désactivé (issue #177) — seulement pertinent quand on
    # regarde tous les hôtes ; un `--host <x>` explicite garde la forme JSON
    # historique (liste nue), pour ne rien casser côté consommateurs existants.
    orphans: list[dict[str, Any]] = []
    if host.strip().lower() == "all":
        enabled = enabled_host_ids(project_root)
        for host_id in supported_hosts():
            if host_id in enabled:
                continue
            emitter = emitter_for(host_id)
            if emitter is None:  # pragma: no cover - registry is complete
                continue
            plan = emitter.plan(surface, project_root)
            files = [
                p.resolve().relative_to(project_root.resolve()).as_posix()
                for p in owned_managed_paths(plan, project_root)
            ]
            if files:
                orphans.append({"host": host_id.value, "display_name": profile_for(host_id).display_name, "files": files})

    if _get_fmt(ctx) == "json":
        if host.strip().lower() == "all":
            typer.echo(
                json.dumps(
                    {"hosts": [item.to_dict() for item in payload], "orphans": orphans},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo(json.dumps([item.to_dict() for item in payload], indent=2, ensure_ascii=False))
        raise typer.Exit(0 if all(item.in_sync for item in payload) else 1)

    for note in surface.notes:
        console.print(f"[dim][!][/dim] {note}")

    for item in payload:
        state = "[green]à jour[/green]" if item.in_sync else "[yellow]désynchronisé[/yellow]"
        console.print(f"[bold]{item.display_name}[/bold] {state}")
        if item.pending:
            console.print(f"  {len(item.pending)} fichier(s) à régénérer — `grimoire host sync`")
        for label in item.conflicts:
            console.print(f"  [yellow][!][/yellow] {label} — fichier non généré au chemin géré")
        if item.blocking_hooks:
            console.print(f"  hooks bloquants : {', '.join(item.blocking_hooks)}")
        if item.unsupported_events:
            console.print(f"  [yellow]non supporté par cet hôte[/yellow] : {', '.join(item.unsupported_events)}")
        for degradation in item.degradations:
            console.print(f"  [dim]dégradé[/dim] {degradation['surface']} — repli : {degradation['fallback']}")
        if item.agents_with_inferred_tools:
            console.print(
                "  [dim]frontière d'outils déduite (ajoutez `tools:` au fichier d'agent pour la fixer) :[/dim] "
                + ", ".join(item.agents_with_inferred_tools)
            )
        if item.entry_agent:
            console.print(f"  persona d'entrée : {item.entry_agent}")
        elif item.entry_agent_declared:
            console.print(
                f"  [yellow]persona d'entrée `{item.entry_agent_declared}` déclarée dans "
                "project-context.yaml, mais aucun agent ne porte ce nom[/yellow]"
            )
        else:
            console.print("  [dim]aucune persona d'entrée (agents.entry vide)[/dim]")
    for orphan in orphans:
        console.print(
            f"[dim]{orphan['display_name']} désactivé[/dim] — {len(orphan['files'])} fichier(s) orphelin(s) : "
            + ", ".join(orphan["files"])
            + " — `grimoire host sync --prune-disabled` pour les retirer"
        )
    raise typer.Exit(0 if all(item.in_sync for item in payload) else 1)


@host_app.command("run")
def host_run(
    slug: str = _RUN_SLUG_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Afficher le corps d'une commande (pour les hôtes sans commandes natives)."""
    for command in collect_commands(project_root):
        if command.slug == slug:
            typer.echo(command.body, nl=False)
            return
    console.print(f"[red]Commande inconnue : {slug}[/red]")
    raise typer.Exit(code=1)


@host_app.command("hook", context_settings={"ignore_unknown_options": True})
def host_hook(
    host: str = _HOOK_HOST_OPTION,
    event: str = _HOOK_EVENT_OPTION,
    project_root: Path | None = _HOOK_ROOT_OPTION,
    decision: str | None = _HOOK_DECISION_OPTION,
) -> None:
    """Exécuter une décision de gouvernance sur une charge utile de hook (stdin -> stdout).

    Toujours en sortie 0 : le verdict est dans le JSON. Un code non nul ferait
    passer une décision de politique pour une panne du hook.
    """
    host_id = resolve_host(host) or HostId.UNKNOWN
    parsed = parse_event(event)
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result, _decision, _hook_input = run_hook(
        payload, host_id=host_id, event=parsed, project_root=project_root, decision_id=decision
    )
    typer.echo(json.dumps(result, ensure_ascii=False), nl=False)
