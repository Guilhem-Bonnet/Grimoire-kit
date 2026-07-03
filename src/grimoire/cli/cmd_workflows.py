"""Grimoire CLI — ``grimoire workflows`` and ``grimoire registry`` sub-commands."""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.cli._cli_helpers import _get_fmt
from grimoire.data import framework_path as _data_framework_path

console = Console(stderr=True)


def framework_path() -> Path:
    """Return the framework root, honoring app-level monkeypatches used in tests."""
    try:
        from grimoire.cli import app as cli_app
    except Exception:
        return _data_framework_path()

    provider = getattr(cli_app, "framework_path", _data_framework_path)
    return provider()

# ── grimoire workflows ────────────────────────────────────────────────────────

workflows_app = typer.Typer(help="Inspect available Copilot workflows.")

_WF_DESCRIPTIONS: dict[str, str] = {
    "grimoire-session-bootstrap": "Reprendre le travail avec contexte complet",
    "grimoire-health-check": "Diagnostic global de santé projet",
    "grimoire-dream": "Consolider les apprentissages inter-sessions",
    "grimoire-pre-push": "Valider avant push (tests/lint/checks)",
    "grimoire-changelog": "Générer un changelog depuis l'historique",
    "grimoire-status": "Obtenir un snapshot rapide du projet",
    "grimoire-self-heal": "Diagnostiquer et réparer les pannes courantes",
}


def _workflow_source_dirs(project_root: Path) -> list[tuple[str, Path]]:
    """Return ordered workflow sources (project first, then framework fallback)."""
    sources: list[tuple[str, Path]] = []
    project_dir = project_root / ".github" / "prompts"
    if project_dir.is_dir():
        sources.append(("project", project_dir))

    fw_dir = framework_path() / "copilot" / "prompts"
    if fw_dir.is_dir():
        sources.append(("framework", fw_dir))

    return sources


def _extract_workflow_slug(filename: str) -> str:
    """Map '*.prompt.md' filename to command slug."""
    if filename.endswith(".prompt.md"):
        return filename.removesuffix(".prompt.md")
    return Path(filename).stem


def _sha256_file(path: Path) -> str:
    """Return hex SHA256 for a text file."""
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _workflow_inventory(
    project_root: Path,
) -> tuple[dict[str, Path], dict[str, Path], list[str], list[str], list[str]]:
    """Return framework/project workflow maps and drift lists."""
    project_dir = project_root / ".github" / "prompts"
    framework_dir = framework_path() / "copilot" / "prompts"

    expected = {p.name: p for p in sorted(framework_dir.glob("*.prompt.md"))} if framework_dir.is_dir() else {}
    actual = {p.name: p for p in sorted(project_dir.glob("*.prompt.md"))} if project_dir.is_dir() else {}
    missing = sorted(name for name in expected if name not in actual)
    modified = sorted(
        name for name in expected
        if name in actual and _sha256_file(expected[name]) != _sha256_file(actual[name])
    )
    extra = sorted(name for name in actual if name not in expected)
    return expected, actual, missing, modified, extra


def _workflow_unified_diff(expected: Path, actual: Path) -> list[str]:
    """Return unified diff lines between framework and project workflow files."""
    return list(
        difflib.unified_diff(
            expected.read_text(encoding="utf-8").splitlines(),
            actual.read_text(encoding="utf-8").splitlines(),
            fromfile=f"framework/{expected.name}",
            tofile=f"project/{actual.name}",
            lineterm="",
        )
    )


def _resolve_workflow_file(project_root: Path, workflow: str) -> tuple[str, Path] | None:
    """Resolve a workflow by slug or filename, preferring project over framework."""
    filename = workflow if workflow.endswith(".prompt.md") else f"{workflow}.prompt.md"
    for source, src_dir in _workflow_source_dirs(project_root):
        candidate = src_dir / filename
        if candidate.is_file():
            return source, candidate
    return None


def _workflow_filename(workflow: str) -> str:
    """Normalize workflow slug or filename to '*.prompt.md'."""
    return workflow if workflow.endswith(".prompt.md") else f"{workflow}.prompt.md"


def _collect_workflow_rows(project_root: Path) -> list[dict[str, str]]:
    """Collect de-duplicated workflow metadata rows (project first)."""
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for source, src_dir in _workflow_source_dirs(project_root):
        for prompt_file in sorted(src_dir.glob("*.prompt.md")):
            slug = _extract_workflow_slug(prompt_file.name)
            if slug in seen:
                continue
            seen.add(slug)
            rows.append({
                "command": f"/{slug}",
                "slug": slug,
                "file": prompt_file.name,
                "source": source,
                "description": _WF_DESCRIPTIONS.get(slug, "Workflow Copilot"),
                "path": str(prompt_file),
            })
    return rows


@workflows_app.command("list")
def workflows_list(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
) -> None:
    """List available Copilot workflows from project and/or framework."""
    root = path.resolve()
    rows = _collect_workflow_rows(root)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"count": len(rows), "workflows": rows}, indent=2))
        return

    if not rows:
        console.print("[yellow]No workflows found.[/yellow]")
        return

    table = Table(title="Copilot Workflows")
    table.add_column("Command", style="bold")
    table.add_column("Description")
    table.add_column("Source")
    for row in rows:
        table.add_row(row["command"], row["description"], row["source"])

    console.print(table)
    console.print(f"\n[dim]{len(rows)} workflow(s) available.[/dim]")


@workflows_app.command("search")
def workflows_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Keyword to search in workflows."),
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    include_content: bool = typer.Option(True, "--content/--no-content", help="Also search inside prompt content."),
) -> None:
    """Search workflows by slug, description, and optionally file content."""
    root = path.resolve()
    q = query.strip().lower()
    rows = _collect_workflow_rows(root)
    matches: list[dict[str, str]] = []

    for row in rows:
        haystacks = [row["slug"].lower(), row["description"].lower(), row["file"].lower()]
        if include_content:
            content = Path(row["path"]).read_text(encoding="utf-8").lower()
            haystacks.append(content)
        if any(q in h for h in haystacks):
            matches.append(row)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"count": len(matches), "query": query, "results": matches}, indent=2))
        return

    if not matches:
        console.print(f"[yellow]No workflows matching '{query}'.[/yellow]")
        return

    table = Table(title=f"Workflow Search: {query}")
    table.add_column("Command", style="bold")
    table.add_column("Description")
    table.add_column("Source")
    for row in matches:
        table.add_row(row["command"], row["description"], row["source"])

    console.print(table)
    console.print(f"\n[dim]{len(matches)} match(es).[/dim]")


@workflows_app.command("show")
def workflows_show(
    ctx: typer.Context,
    workflow: str = typer.Argument(..., help="Workflow slug or prompt filename."),
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
) -> None:
    """Show the content and source of a workflow prompt."""
    root = path.resolve()
    resolved = _resolve_workflow_file(root, workflow)
    if resolved is None:
        console.print(f"[red]Workflow not found:[/red] {workflow}")
        raise typer.Exit(1)

    source, file_path = resolved
    slug = _extract_workflow_slug(file_path.name)
    content = file_path.read_text(encoding="utf-8")

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "slug": slug,
            "command": f"/{slug}",
            "source": source,
            "file": file_path.name,
            "path": str(file_path),
            "description": _WF_DESCRIPTIONS.get(slug, "Workflow Copilot"),
            "content": content,
        }, indent=2))
        return

    console.print(f"[bold]{file_path.name}[/bold]")
    console.print(f"Source: {source}")
    console.print(f"Command: /{slug}")
    console.print(f"Description: {_WF_DESCRIPTIONS.get(slug, 'Workflow Copilot')}\n")
    console.print(content)


@workflows_app.command("install")
def workflows_install(
    ctx: typer.Context,
    workflow: str = typer.Argument(..., help="Workflow slug or prompt filename."),
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite project version if it already exists."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the installation without writing files."),
) -> None:
    """Install a single framework workflow into the project prompts directory."""
    root = path.resolve()
    filename = _workflow_filename(workflow)
    framework_file = framework_path() / "copilot" / "prompts" / filename
    project_dir = root / ".github" / "prompts"
    project_file = project_dir / filename

    if not framework_file.is_file():
        console.print(f"[red]Unknown framework workflow:[/red] {filename}")
        raise typer.Exit(1)

    if project_file.exists() and not overwrite:
        action = "skip-existing"
    else:
        action = "overwrite" if project_file.exists() else "install"

    if not dry_run and action in {"install", "overwrite"}:
        project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(framework_file, project_file)

    payload = {
        "ok": True,
        "workflow": _extract_workflow_slug(filename),
        "file": filename,
        "action": action,
        "dry_run": dry_run,
        "overwrite": overwrite,
        "source": str(framework_file),
        "destination": str(project_file),
    }

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2))
        return

    console.print("[bold]Workflow Install[/bold]")
    if action == "skip-existing":
        console.print(f"  [dim]skip existing[/dim] {filename}")
        return
    tag = "plan" if dry_run else "done"
    color = "yellow" if action == "overwrite" else "green"
    console.print(f"  [{color}]{tag}[/{color}] {action} {filename}")


@workflows_app.command("prune")
def workflows_prune(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview deletions without removing files."),
) -> None:
    """Remove project-only workflows not found in framework defaults."""
    root = path.resolve()
    project_dir = root / ".github" / "prompts"
    framework_dir = framework_path() / "copilot" / "prompts"

    if not framework_dir.is_dir():
        console.print("[red]Framework workflows directory not found.[/red]")
        raise typer.Exit(1)

    _expected, actual, _missing, _modified, extra = _workflow_inventory(root)
    actions: list[dict[str, str]] = [{"action": "delete", "file": name} for name in extra]

    if not dry_run:
        for name in extra:
            target = actual[name]
            if target.is_file():
                target.unlink()

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": True,
            "dry_run": dry_run,
            "project_root": str(root),
            "count": len(actions),
            "actions": actions,
        }, indent=2))
        return

    if not project_dir.is_dir():
        console.print("[yellow]Project workflows directory missing:[/yellow] .github/prompts")
        return

    console.print("[bold]Workflows Prune[/bold]")
    if not actions:
        console.print("[green]No extra workflows to prune.[/green]")
        return

    tag = "plan" if dry_run else "done"
    for action in actions:
        console.print(f"  [yellow]{tag}[/yellow] delete {action['file']}")


@workflows_app.command("doctor")
def workflows_doctor(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    strict: bool = typer.Option(False, "--strict", help="Treat extra project workflows as failures."),
) -> None:
    """Audit project workflows against framework defaults."""
    root = path.resolve()
    project_dir = root / ".github" / "prompts"
    framework_dir = framework_path() / "copilot" / "prompts"

    if not framework_dir.is_dir():
        console.print("[red]Framework workflows directory not found.[/red]")
        raise typer.Exit(1)

    expected, actual, missing, modified, extra = _workflow_inventory(root)

    failing = bool(missing or modified or (strict and extra))

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": not failing,
            "strict": strict,
            "project_root": str(root),
            "counts": {
                "expected": len(expected),
                "project": len(actual),
                "missing": len(missing),
                "modified": len(modified),
                "extra": len(extra),
            },
            "missing": missing,
            "modified": modified,
            "extra": extra,
        }, indent=2))
        if failing:
            raise typer.Exit(1)
        return

    if not project_dir.is_dir():
        console.print("[yellow]Project workflows directory missing:[/yellow] .github/prompts")
    console.print("[bold]Workflows Doctor[/bold]")
    console.print(f"  Expected (framework): {len(expected)}")
    console.print(f"  Present (project):    {len(actual)}")

    if missing:
        console.print("\n[red]Missing workflows[/red]")
        for name in missing:
            console.print(f"  - {name}")

    if modified:
        console.print("\n[red]Modified workflows[/red]")
        for name in modified:
            console.print(f"  - {name}")

    if extra:
        tag = "[red]Extra workflows[/red]" if strict else "[yellow]Extra workflows[/yellow]"
        console.print(f"\n{tag}")
        for name in extra:
            console.print(f"  - {name}")

    if failing:
        console.print("\n[red]Workflow audit failed.[/red]")
        raise typer.Exit(1)

    console.print("\n[green]Workflow audit passed.[/green]")


@workflows_app.command("sync")
def workflows_sync(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite modified project workflows."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without writing files."),
) -> None:
    """Sync framework workflows into the project prompts directory."""
    root = path.resolve()
    project_dir = root / ".github" / "prompts"
    framework_dir = framework_path() / "copilot" / "prompts"

    if not framework_dir.is_dir():
        console.print("[red]Framework workflows directory not found.[/red]")
        raise typer.Exit(1)

    expected, _actual, missing, modified, _extra = _workflow_inventory(root)
    actions: list[dict[str, str]] = []

    for name in missing:
        actions.append({"action": "copy", "file": name})
    if overwrite:
        for name in modified:
            actions.append({"action": "overwrite", "file": name})
    else:
        for name in modified:
            actions.append({"action": "skip-modified", "file": name})

    if not dry_run:
        project_dir.mkdir(parents=True, exist_ok=True)
        for item in actions:
            if item["action"] not in {"copy", "overwrite"}:
                continue
            src = expected[item["file"]]
            dst = project_dir / item["file"]
            shutil.copy2(src, dst)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": True,
            "dry_run": dry_run,
            "overwrite": overwrite,
            "project_root": str(root),
            "actions": actions,
            "applied": [item for item in actions if item["action"] in {"copy", "overwrite"}],
        }, indent=2))
        return

    console.print("[bold]Workflows Sync[/bold]")
    if not actions:
        console.print("[green]Everything up to date.[/green]")
        return

    for item in actions:
        if item["action"] == "copy":
            tag = "plan" if dry_run else "done"
            console.print(f"  [green]{tag}[/green] copy {item['file']}")
        elif item["action"] == "overwrite":
            tag = "plan" if dry_run else "done"
            console.print(f"  [yellow]{tag}[/yellow] overwrite {item['file']}")
        else:
            console.print(f"  [dim]skip modified[/dim] {item['file']}")


@workflows_app.command("diff")
def workflows_diff(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root (optional)."),
    workflow: str | None = typer.Argument(None, help="Workflow slug or prompt filename."),
) -> None:
    """Show diffs between framework workflows and project workflows."""
    root = path.resolve()
    expected, actual, _missing, modified, _extra = _workflow_inventory(root)

    targets: list[str]
    if workflow:
        filename = workflow if workflow.endswith(".prompt.md") else f"{workflow}.prompt.md"
        if filename not in expected:
            console.print(f"[red]Unknown framework workflow:[/red] {filename}")
            raise typer.Exit(1)
        if filename not in actual:
            console.print(f"[red]Workflow missing in project:[/red] {filename}")
            raise typer.Exit(1)
        targets = [filename]
    else:
        targets = modified

    payload: list[dict[str, object]] = []
    for name in targets:
        diff_lines = _workflow_unified_diff(expected[name], actual[name])
        if not diff_lines:
            continue
        payload.append({
            "file": name,
            "slug": _extract_workflow_slug(name),
            "diff": diff_lines,
        })

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"count": len(payload), "diffs": payload}, indent=2))
        return

    if not payload:
        console.print("[green]No workflow differences found.[/green]")
        return

    console.print("[bold]Workflows Diff[/bold]")
    for item in payload:
        console.print(f"\n[bold]{item['file']}[/bold]")
        for line in item["diff"]:
            if line.startswith(("+++", "---", "@@")):
                console.print(line, style="cyan")
            elif line.startswith("+"):
                console.print(line, style="green")
            elif line.startswith("-"):
                console.print(line, style="red")
            else:
                console.print(line)


# ── grimoire registry ─────────────────────────────────────────────────────────

registry_app = typer.Typer(help="Browse the agent registry.")

_reg_query_arg = typer.Argument(None, help="Search query.")


@registry_app.command("list")
def registry_list(ctx: typer.Context) -> None:
    """List all available archetypes and agents."""
    from grimoire.registry.local import LocalRegistry
    from grimoire.tools._common import find_project_root

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a Grimoire project — cannot locate kit root.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    archs = reg.list_archetypes()
    if not archs:
        console.print("[yellow]No archetypes found.[/yellow]")
        return

    if _get_fmt(ctx) == "json":
        items = []
        for arch_id in archs:
            try:
                dna = reg.inspect_archetype(arch_id)
                items.append({"archetype": arch_id, "agents": len(dna.agents)})
            except Exception:
                items.append({"archetype": arch_id, "agents": None})
        typer.echo(json.dumps(items, indent=2))
        return

    tbl = Table(title="Available Archetypes")
    tbl.add_column("Archetype", style="bold")
    tbl.add_column("Agents", justify="right")

    for arch_id in archs:
        try:
            dna = reg.inspect_archetype(arch_id)
            tbl.add_row(arch_id, str(len(dna.agents)))
        except Exception:
            tbl.add_row(arch_id, "?")

    console.print(tbl)


@registry_app.command("search")
def registry_search(
    ctx: typer.Context,
    query: str = _reg_query_arg,
) -> None:
    """Search agents by keyword."""
    from grimoire.registry.local import LocalRegistry
    from grimoire.tools._common import find_project_root

    if not query:
        console.print("[red]Please provide a search query.[/red]")
        raise typer.Exit(1)

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a Grimoire project.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    results = reg.search(query)

    if not results:
        console.print(f"[yellow]No agents matching '{query}'.[/yellow]")
        return

    if _get_fmt(ctx) == "json":
        items = [{"id": r.id, "archetype": r.archetype, "description": r.description or ""} for r in results]
        typer.echo(json.dumps(items, indent=2))
        return

    tbl = Table(title=f"Search: {query}")
    tbl.add_column("Agent", style="bold")
    tbl.add_column("Archetype")
    tbl.add_column("Description")

    for item in results:
        tbl.add_row(item.id, item.archetype, item.description or "—")

    console.print(tbl)
