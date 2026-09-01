"""``grimoire migrate`` — move a project onto the kit/overrides boundary.

A one-shot operation, not part of the update cycle. Before the boundary
existed, the kit copied its files into ``_grimoire/_config/custom/`` — the same
directory a project used for its own customisations — so nothing could be
regenerated without risking user work, and in practice nothing ever was. This
command separates the two piles:

* content the kit has shipped at some point (recognised by digest against
  :mod:`grimoire.core.kit_hashes`) is dropped and regenerated in
  ``_grimoire/kit/``, where every future ``grimoire up`` will update it;
* everything else is the project's own and moves to ``_grimoire/overrides/``,
  which shadows the kit tier and is never overwritten.

Unrecognised content is always treated as the project's own: the failure that
matters here is destroying a customisation, not carrying one stale file over.

After migrating, ``grimoire up`` is the only command a project needs to take a
new kit version.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core import kit_hashes, layout

console = Console(stderr=True)

#: Where snapshots live — one directory per run, restorable by timestamp.
SNAPSHOT_ROOT = "_grimoire-output/.migrations"

#: Legacy location → tier-relative destination for files the project owns.
#: Order matters: the first matching prefix wins, so specific subtrees are
#: listed before the catch-all framework directory.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("_grimoire/_config/custom/agents", layout.AGENTS_SUBDIR),
    ("_grimoire/_config/custom/workflows", layout.WORKFLOWS_SUBDIR),
    ("_grimoire/_config/custom/prompt-templates", layout.PROMPT_TEMPLATES_SUBDIR),
    ("_grimoire/_config/custom", layout.FRAMEWORK_SUBDIR),
    ("_grimoire/_memory/backends", f"{layout.MEMORY_CODE_SUBDIR}/backends"),
)

#: Individual legacy files that were kit code sitting in the memory store.
_MEMORY_CODE_FILES = ("maintenance.py", "session-save.py")

#: Build artifacts — deleted outright, never carried into overrides.
_JUNK_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})
_JUNK_SUFFIXES = frozenset({".pyc", ".pyo"})

#: Files the kit derives from the project's own state. Their content never
#: matches a shipped digest (it depends on which agents are installed), yet
#: nobody edits them by hand — treating them as customisations would freeze a
#: stale index in overrides.
_DERIVED_NAMES = frozenset({"agent-manifest.csv", "archetype.dna.yaml"})


def _is_junk(path: Path) -> bool:
    return path.suffix in _JUNK_SUFFIXES or any(part in _JUNK_DIRS for part in path.parts)


@dataclass(slots=True)
class FileVerdict:
    """What becomes of one legacy file."""

    source: Path              # absolute, current location
    relative: str             # project-relative, for display
    action: str               # "regenerate" | "override"
    destination: str = ""     # project-relative target, when moved
    shipped_version: str = ""  # kit version that shipped this content
    shadows_kit: bool = False  # an override that masks a file the kit ships


@dataclass(slots=True)
class MigrationPlan:
    """Everything the migration intends to do — inspectable before applying."""

    target: Path
    verdicts: list[FileVerdict] = field(default_factory=list)
    already_migrated: bool = False

    @property
    def regenerate(self) -> list[FileVerdict]:
        return [v for v in self.verdicts if v.action == "regenerate"]

    @property
    def overrides(self) -> list[FileVerdict]:
        return [v for v in self.verdicts if v.action == "override"]

    @property
    def shadowing(self) -> list[FileVerdict]:
        """Overrides that mask a kit file — the ones worth a human's review.

        Each is a file the project will stop receiving updates for. Either it
        holds a real customisation (correct), or it is an old kit file the
        catalog could not recognise, and ``--adopt-kit`` should drop it.
        """
        return [v for v in self.verdicts if v.action == "override" and v.shadows_kit]


# ── Planning ─────────────────────────────────────────────────────────────────


def _legacy_files(target: Path) -> list[Path]:
    """Every file still sitting in a pre-boundary location."""
    found: list[Path] = []
    for legacy_root, _dest in _ROUTES:
        root = target / legacy_root
        if not root.is_dir():
            continue
        found.extend(p for p in sorted(root.rglob("*")) if p.is_file() and not _is_junk(p))
    for name in _MEMORY_CODE_FILES:
        p = target / "_grimoire" / "_memory" / name
        if p.is_file():
            found.append(p)
    manifest = target / "_grimoire" / "_config" / "agent-manifest.csv"
    if manifest.is_file():
        found.append(manifest)
    # rglob over nested routes can surface the same file twice.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _destination(target: Path, path: Path) -> str:
    """Project-relative override path for a legacy *path*."""
    rel = path.relative_to(target).as_posix()
    for legacy_root, dest_subdir in _ROUTES:
        if rel.startswith(legacy_root + "/"):
            tail = rel[len(legacy_root) + 1:]
            return f"{layout.OVERRIDES_DIR}/{dest_subdir}/{tail}"
    if path.name in _MEMORY_CODE_FILES:
        return f"{layout.OVERRIDES_DIR}/{layout.MEMORY_CODE_SUBDIR}/{path.name}"
    return f"{layout.OVERRIDES_DIR}/{path.name}"


def _shadowed(destination: str, kit_destinations: frozenset[str]) -> bool:
    """True when *destination* masks a file the kit writes at the same path."""
    prefix = layout.OVERRIDES_DIR + "/"
    rel = destination.removeprefix(prefix)
    # Fallback sets hold base names, not paths; accept either shape.
    return rel in kit_destinations or rel.rsplit("/", 1)[-1] in kit_destinations


def plan_migration(target: Path) -> MigrationPlan:
    """Classify every legacy file without touching the filesystem."""
    target = target.resolve()
    plan = MigrationPlan(target=target)
    files = _legacy_files(target)
    if not files:
        plan.already_migrated = True
        return plan

    kit_destinations = _kit_destinations(target)
    for path in files:
        rel = path.relative_to(target).as_posix()
        entry = kit_hashes.shipped_by_kit(path)
        if entry is not None or path.name in _DERIVED_NAMES:
            plan.verdicts.append(FileVerdict(
                source=path,
                relative=rel,
                action="regenerate",
                shipped_version=str(entry.get("version", "")) if entry else "derived",
            ))
        else:
            destination = _destination(target, path)
            plan.verdicts.append(FileVerdict(
                source=path,
                relative=rel,
                action="override",
                destination=destination,
                shadows_kit=_shadowed(destination, kit_destinations),
            ))
    return plan


def _kit_destinations(target: Path) -> frozenset[str]:
    """Tier-relative paths the kit would write, for shadow detection.

    Compared by path, not by base name: ``agents/_archived/concierge.md`` is a
    file the project archived, not a shadow of the kit's ``agents/concierge.md``.
    Matching on the name alone marked it as shadowing, and ``--adopt-kit``
    would then delete it without anything regenerating it — a silent loss of
    the project's own archive.

    Falls back to base names when the plan cannot be built (unreadable config),
    which is the previous, coarser behaviour rather than no detection at all.
    """
    from grimoire.cli.cmd_up import _infer_resolved, _load_config_quiet
    from grimoire.core.scaffold import ProjectScaffolder

    kit_root = layout.kit_dir(target)
    try:
        cfg = _load_config_quiet(target)
        scaffolder = ProjectScaffolder(
            target,
            project_name=(cfg.project.name if cfg else "") or target.name,
            user_name=(cfg.user.name if cfg else ""),
            language=(cfg.user.language if cfg else ""),
            skill_level=(cfg.user.skill_level if cfg else ""),
            scan=None,
            resolved=_infer_resolved(target, cfg),
            backend=(cfg.memory.backend if cfg else "") or "local",
        )
        plan = scaffolder.plan()
    except Exception:  # detection must never break the migration
        return _kit_file_names()

    destinations: set[str] = set()
    for dst in [c.dst for c in plan.copies] + [t.dst for t in plan.templates]:
        try:
            destinations.add(dst.relative_to(kit_root).as_posix())
        except ValueError:
            continue  # outside the kit tier: not something an override shadows
    return frozenset(destinations)


def _kit_file_names() -> frozenset[str]:
    """Base names the kit currently ships — coarse fallback for shadow detection."""
    from grimoire.archetypes import bundled_path as archetypes_path
    from grimoire.data import framework_path

    names: set[str] = set()
    for root in (archetypes_path(), framework_path()):
        if root and root.is_dir():
            names.update(p.name for p in root.rglob("*") if p.is_file())
    return frozenset(names)


# ── Execution ────────────────────────────────────────────────────────────────


def _snapshot(plan: MigrationPlan, stamp: str) -> Path:
    """Copy every file the migration will touch into a restorable snapshot."""
    snap = plan.target / SNAPSHOT_ROOT / stamp
    files_dir = snap / "files"
    entries = []
    for verdict in plan.verdicts:
        dst = files_dir / verdict.relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(verdict.source, dst)
        entries.append({
            "relative": verdict.relative,
            "action": verdict.action,
            "destination": verdict.destination,
            "shipped_version": verdict.shipped_version,
        })
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "manifest.json").write_text(
        json.dumps({"stamp": stamp, "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    return snap


def _prune_empty(root: Path, stop: Path) -> None:
    """Remove *root* and its now-empty parents, never climbing past *stop*."""
    current = root
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def apply_migration(plan: MigrationPlan, stamp: str) -> tuple[Path, list[str]]:
    """Move overrides, drop regenerable files, then rebuild the kit tier."""
    from grimoire.cli.cmd_up import refresh_kit_tier

    snapshot = _snapshot(plan, stamp)

    for verdict in plan.overrides:
        dst = plan.target / verdict.destination
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(verdict.source), str(dst))

    for verdict in plan.regenerate:
        verdict.source.unlink(missing_ok=True)

    # Build artifacts left behind by the old layout: nothing references them.
    junk_roots = [plan.target / legacy for legacy, _dest in _ROUTES]
    junk_roots.append(plan.target / "_grimoire" / "_memory")
    for root in junk_roots:
        if not root.is_dir():
            continue
        for junk in sorted((p for p in root.rglob("*") if p.is_file() and _is_junk(p)), reverse=True):
            junk.unlink(missing_ok=True)
        for cache in sorted((p for p in root.rglob("*") if p.is_dir() and p.name in _JUNK_DIRS), reverse=True):
            shutil.rmtree(cache, ignore_errors=True)

    for legacy_root, _dest in _ROUTES:
        root = plan.target / legacy_root
        if root.is_dir():
            for d in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
                _prune_empty(d, plan.target)
            _prune_empty(root, plan.target)

    result = refresh_kit_tier(plan.target)
    regenerated = list(result.copied_files) + list(result.rendered_files)
    return snapshot, regenerated


def restore_migration(target: Path, stamp: str) -> list[str]:
    """Put back every file captured by the snapshot *stamp*."""
    snap = target.resolve() / SNAPSHOT_ROOT / stamp
    manifest_path = snap / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no snapshot manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for entry in manifest.get("entries", []):
        rel = str(entry.get("relative", ""))
        src = snap / "files" / rel
        if not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel)
        moved = str(entry.get("destination", ""))
        if moved:
            (target / moved).unlink(missing_ok=True)
    return restored


# ── CLI ──────────────────────────────────────────────────────────────────────


def _render_plan(plan: MigrationPlan) -> None:
    table = Table(title="grimoire migrate — plan", show_lines=False)
    table.add_column("File", overflow="fold")
    table.add_column("Verdict")
    table.add_column("Becomes", overflow="fold")
    for verdict in plan.verdicts:
        if verdict.action == "regenerate":
            table.add_row(
                verdict.relative,
                f"[green]kit content[/green] (shipped in {verdict.shipped_version or '?'})",
                "regenerated in _grimoire/kit/",
            )
        else:
            kind = "[yellow]yours (shadows a kit file)[/yellow]" if verdict.shadows_kit else "[yellow]yours[/yellow]"
            table.add_row(verdict.relative, kind, verdict.destination)
    console.print(table)


_path_arg = typer.Argument(Path(), help="Project to migrate.")
_apply_opt = typer.Option(False, "--apply", help="Perform the migration (default: plan only).")
_restore_opt = typer.Option("", "--restore", help="Restore a snapshot by its timestamp.")
_adopt_opt = typer.Option(
    False, "--adopt-kit",
    help="Drop overrides that merely shadow a kit file (take the kit version instead).",
)


def migrate_command(
    ctx: typer.Context,
    path: Path = _path_arg,
    apply: bool = _apply_opt,
    restore: str = _restore_opt,
    adopt_kit: bool = _adopt_opt,
) -> None:
    """Move a project onto the kit/overrides boundary (one-shot).

    [cyan]grimoire migrate[/cyan]           Show what would move
    [cyan]grimoire migrate --apply[/cyan]   Do it, with a restorable snapshot
    """
    target = path.resolve()
    fmt = (ctx.obj or {}).get("output", "text")

    if restore:
        try:
            restored = restore_migration(target, restore)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            console.print(f"[red]Cannot restore:[/red] {exc}")
            raise typer.Exit(1) from None
        if fmt == "json":
            typer.echo(json.dumps({"restored": restored}, indent=2))
        else:
            console.print(f"[green]Restored {len(restored)} file(s) from snapshot {restore}.[/green]")
        return

    plan = plan_migration(target)
    if adopt_kit:
        # The user asserts these hold no customisation worth keeping: let the
        # kit version win so the files start receiving updates again. The
        # snapshot still makes it reversible.
        for verdict in plan.shadowing:
            verdict.action = "regenerate"
            verdict.destination = ""
            verdict.shipped_version = "adopted"

    if plan.already_migrated:
        if fmt == "json":
            typer.echo(json.dumps({"already_migrated": True, "files": 0}, indent=2))
        else:
            console.print("[green]Nothing to migrate[/green] — this project already uses the kit/overrides layout.")
            console.print("[dim]Updates land with: grimoire up[/dim]")
        return

    if not apply:
        if fmt == "json":
            typer.echo(json.dumps({
                "already_migrated": False,
                "regenerate": [v.relative for v in plan.regenerate],
                "overrides": {v.relative: v.destination for v in plan.overrides},
                "shadowing": [v.relative for v in plan.shadowing],
            }, indent=2))
        else:
            _render_plan(plan)
            console.print(
                f"\n[bold]{len(plan.regenerate)}[/bold] kit file(s) will be regenerated, "
                f"[bold]{len(plan.overrides)}[/bold] of your file(s) will move to overrides."
            )
            shadowing = plan.shadowing
            if shadowing:
                console.print(
                    f"[yellow]{len(shadowing)}[/yellow] of them shadow a file the kit ships: they will "
                    "stop receiving updates. Review them, or re-run with [bold]--adopt-kit[/bold] to "
                    "take the kit version instead."
                )
            console.print("[dim]Run with --apply to perform it (a snapshot is taken first).[/dim]")
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        snapshot, regenerated = apply_migration(plan, stamp)
    except OSError as exc:
        console.print(f"[red]Migration failed:[/red] {exc}")
        console.print(f"[dim]Restore with: grimoire migrate --restore {stamp}[/dim]")
        raise typer.Exit(1) from None

    if fmt == "json":
        typer.echo(json.dumps({
            "snapshot": snapshot.relative_to(target).as_posix(),
            "moved_to_overrides": [v.destination for v in plan.overrides],
            "regenerated": len(regenerated),
        }, indent=2))
        return

    console.print(f"[green]Migrated.[/green] {len(plan.overrides)} file(s) moved to overrides, "
                  f"{len(regenerated)} kit artifact(s) regenerated.")
    console.print(f"[dim]Snapshot: {snapshot.relative_to(target)} "
                  f"(restore with: grimoire migrate --restore {stamp})[/dim]")
    console.print("[dim]From now on, updates land with: grimoire up[/dim]")
