"""``grimoire workflows`` — inspecter, installer et auditer les workflows.

Sorti de :mod:`grimoire.cli.app` : le groupe pesait cinq cents lignes dans le
module de câblage, celui qui est sous ratchet de taille. Les commandes et leurs
aides vivent ici ; ``app.py`` ne fait plus que monter le Typer.

La découverte et les métadonnées vivent un cran plus bas, dans
:mod:`grimoire.workflows.registry` — un catalogue de workflows n'est pas du
câblage CLI.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, TypedDict, cast

import typer
from rich.table import Table

from grimoire.cli._shared import console
from grimoire.core import layout
from grimoire.data import framework_path
from grimoire.workflows.registry import (
    KIND_COMMAND,
    KIND_ORCHESTRATION,
    find_framework_workflow,
    find_workflow,
    is_deprecated_file,
    load_workflows,
    workflow_slug,
)
from grimoire.workflows.teams import load_team, load_teams

__all__ = ["workflows_app"]


class _WorkflowDiffPayload(TypedDict):
    file: str
    slug: str
    diff: list[str]


workflows_app = typer.Typer(help="Inspect available Copilot workflows.")

_WORKFLOW_PATH_ARGUMENT = typer.Argument(Path(), help="Project root (optional).")


def _get_fmt(ctx: typer.Context) -> str:
    """Return the output format from context — 'text' or 'json'."""
    return cast(str, (ctx.obj or {}).get("output", "text"))


def _sha256_file(path: Path) -> str:
    """Return hex SHA256 for a text file."""
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _workflow_inventory(project_root: Path) -> tuple[dict[str, Path], dict[str, Path], list[str], list[str], list[str]]:
    """Return framework/project workflow maps and drift lists."""
    project_dir = project_root / ".github" / "prompts"
    framework_dir = framework_path() / "copilot" / "prompts"

    # Les prompts remplacés par une commande CLI ne sont plus déployés : les
    # attendre ferait rapporter « manquant » sur tout projet neuf, et `sync`
    # les réinstallerait aussitôt.
    expected = (
        {p.name: p for p in sorted(framework_dir.glob("*.prompt.md")) if not is_deprecated_file(p)}
        if framework_dir.is_dir()
        else {}
    )
    actual = {p.name: p for p in sorted(project_dir.glob("*.prompt.md"))} if project_dir.is_dir() else {}
    missing = sorted(name for name in expected if name not in actual)
    modified = sorted(
        name for name in expected if name in actual and _sha256_file(expected[name]) != _sha256_file(actual[name])
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


def _collect_workflow_rows(project_root: Path, *, include_deprecated: bool = True) -> list[dict[str, str]]:
    """Lignes de catalogue, dédoublonnées, projet d'abord.

    Déléguée au registre : la liste ne balayait que ``.github/prompts`` et
    ignorait les workflows d'orchestration installés sous le tier kit.
    """
    return [entry.to_dict() for entry in load_workflows(project_root, include_deprecated=include_deprecated)]


def _workflow_table(title: str, rows: list[dict[str, Any]]) -> Table:
    """Tableau du catalogue — la nature et les agents sont ce qui distingue
    un workflow d'orchestration d'une commande d'hygiène."""
    table = Table(title=title)
    table.add_column("Command", style="bold")
    table.add_column("Kind")
    table.add_column("Description")
    table.add_column("Agents")
    table.add_column("Source")
    for row in rows:
        kind = str(row.get("kind", KIND_COMMAND))
        style = "cyan" if kind == KIND_ORCHESTRATION else "dim"
        agents = ", ".join(row.get("agents") or []) or "—"
        replacement = str(row.get("deprecated_by") or "")
        description = str(row.get("description") or "—")
        if replacement:
            description = f"[dim]{description}[/dim]\n[yellow]remplacé par[/yellow] [bold]{replacement}[/bold]"
        table.add_row(
            str(row["command"]),
            f"[{style}]{kind}[/{style}]",
            description,
            agents,
            str(row.get("source", "")),
        )
    return table


@workflows_app.command("list")
def workflows_list(
    ctx: typer.Context,
    path: Path = _WORKFLOW_PATH_ARGUMENT,
    kind: str = typer.Option("", "--kind", "-k", help=f"Filter by kind: {KIND_ORCHESTRATION} | {KIND_COMMAND}."),
    all_: bool = typer.Option(False, "--all", "-a", help="Include workflows a CLI command has replaced."),
) -> None:
    """List available workflows — orchestrations and hygiene commands alike.

    Workflows that merely restate a CLI command are hidden unless ``--all``:
    four of the seven shipped prompts did exactly that, and they filled most
    of the catalogue.
    """
    root = path.resolve()
    rows = _collect_workflow_rows(root, include_deprecated=all_)
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]

    if _get_fmt(ctx) == "json":
        counts = {
            KIND_ORCHESTRATION: sum(1 for r in rows if r.get("kind") == KIND_ORCHESTRATION),
            KIND_COMMAND: sum(1 for r in rows if r.get("kind") == KIND_COMMAND),
        }
        typer.echo(json.dumps({"count": len(rows), "counts_by_kind": counts, "workflows": rows}, indent=2))
        return

    if not rows:
        console.print("[yellow]No workflows found.[/yellow]")
        return

    console.print(_workflow_table("Grimoire Workflows", rows))
    orchestrated = sum(1 for row in rows if row.get("kind") == KIND_ORCHESTRATION)
    console.print(
        f"\n[dim]{len(rows)} workflow(s) available — {orchestrated} orchestration(s).[/dim]"
    )
    if not all_:
        hidden = len(_collect_workflow_rows(root)) - len(_collect_workflow_rows(root, include_deprecated=False))
        if hidden:
            console.print(
                f"[dim]{hidden} remplacé(s) par une commande CLI — [bold]--all[/bold] pour les voir.[/dim]"
            )


@workflows_app.command("search")
def workflows_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Keyword to search in workflows."),
    path: Path = _WORKFLOW_PATH_ARGUMENT,
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

    console.print(_workflow_table(f"Workflow Search: {query}", matches))
    console.print(f"\n[dim]{len(matches)} match(es).[/dim]")


@workflows_app.command("show")
def workflows_show(
    ctx: typer.Context,
    workflow: str = typer.Argument(..., help="Workflow slug or prompt filename."),
    path: Path = _WORKFLOW_PATH_ARGUMENT,
) -> None:
    """Show a workflow: what it declares, the team it runs, and its content."""
    root = path.resolve()
    entry = find_workflow(root, workflow)
    if entry is None:
        console.print(f"[red]Workflow not found:[/red] {workflow}")
        raise typer.Exit(1)

    content = entry.path.read_text(encoding="utf-8")
    team = load_team(root, entry.team) if entry.team else None

    if _get_fmt(ctx) == "json":
        payload = entry.to_dict()
        payload["content"] = content
        if team is not None:
            payload["team_manifest"] = team.to_dict()
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    console.print(f"[bold]{entry.path.name}[/bold]")
    console.print(f"Command: {entry.command}")
    console.print(f"Kind: {entry.kind}")
    console.print(f"Source: {entry.source}")
    console.print(f"Description: {entry.description or '—'}")
    if entry.is_deprecated:
        console.print(f"[yellow]Remplacé par :[/yellow] [bold]{entry.deprecated_by}[/bold]")
    if entry.agents:
        console.print(f"Agents: {', '.join(entry.agents)}")
    if entry.patterns:
        console.print(f"Patterns: {', '.join(entry.patterns)}")
    if entry.memory:
        console.print(f"Mémoire: {', '.join(entry.memory)}")
    if entry.triggers:
        console.print("Triggers:")
        for trigger in entry.triggers:
            console.print(f"  - {trigger}")
    if team is not None:
        console.print(f"\n[bold]Team {team.name}[/bold] — {team.description}")
        for member in team.agents:
            flag = "" if member.required else " [dim](optionnel)[/dim]"
            console.print(f"  - [bold]{member.name}[/bold] · {member.role}{flag}")
        if team.handoff_to:
            console.print(f"  handoff → [bold]{team.handoff_to}[/bold] · {team.handoff_trigger}")
    elif entry.team:
        console.print(f"[yellow]Team declared but not installed:[/yellow] {entry.team}")
    console.print()
    console.print(content)


@workflows_app.command("teams")
def workflows_teams(
    ctx: typer.Context,
    path: Path = _WORKFLOW_PATH_ARGUMENT,
) -> None:
    """List the team manifests a workflow can run — members and handoff chain.

    The manifests shipped with the kit had no reader: three files and a schema
    that nothing in the SDK ever loaded.
    """
    root = path.resolve()
    teams = load_teams(root)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(
            {"count": len(teams), "teams": [team.to_dict() for team in teams]},
            indent=2, ensure_ascii=False,
        ))
        return

    if not teams:
        console.print("[yellow]No team manifest found.[/yellow]")
        return

    table = Table(title="Grimoire Teams")
    table.add_column("Team", style="bold")
    table.add_column("Specialty")
    table.add_column("Agents")
    table.add_column("Handoff")
    for team in teams:
        table.add_row(
            team.name,
            team.specialty or team.description or "—",
            ", ".join(member.name for member in team.agents) or "—",
            team.handoff_to or "—",
        )
    console.print(table)
    console.print(f"\n[dim]{len(teams)} team(s).[/dim]")


@workflows_app.command("install")
def workflows_install(
    ctx: typer.Context,
    workflow: str = typer.Argument(..., help="Workflow slug or prompt filename."),
    path: Path = _WORKFLOW_PATH_ARGUMENT,
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite project version if it already exists."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the installation without writing files."),
) -> None:
    """Install a framework workflow into the project — prompt or orchestration.

    An orchestration lands in ``_grimoire/workflows/`` and a hygiene command in
    ``.github/prompts/``: the destination follows the kind, so installing an
    orchestration no longer fails on a filename that never existed.
    """
    root = path.resolve()
    entry = find_framework_workflow(root, workflow)
    if entry is None:
        console.print(f"[red]Unknown framework workflow:[/red] {workflow}")
        raise typer.Exit(1)

    framework_file = entry.path
    filename = framework_file.name
    project_dir = (
        layout.kit_dir(root) / layout.WORKFLOWS_SUBDIR
        if entry.is_orchestration
        else root / ".github" / "prompts"
    )
    project_file = project_dir / filename

    if project_file.exists() and not overwrite:
        action = "skip-existing"
    else:
        action = "overwrite" if project_file.exists() else "install"

    if not dry_run and action in {"install", "overwrite"}:
        project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(framework_file, project_file)

    payload = {
        "ok": True,
        "workflow": entry.slug,
        "file": filename,
        "kind": entry.kind,
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
    path: Path = _WORKFLOW_PATH_ARGUMENT,
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
    path: Path = _WORKFLOW_PATH_ARGUMENT,
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
    path: Path = _WORKFLOW_PATH_ARGUMENT,
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
    path: Path = _WORKFLOW_PATH_ARGUMENT,
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

    payload: list[_WorkflowDiffPayload] = []
    for name in targets:
        diff_lines = _workflow_unified_diff(expected[name], actual[name])
        if not diff_lines:
            continue
        payload.append({
            "file": name,
            "slug": workflow_slug(name),
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
