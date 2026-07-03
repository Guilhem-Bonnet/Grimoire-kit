"""CLI commands for Mission Ledger, tasks, and evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import RiskProfile, TaskState, TaskType

missions_app = typer.Typer(
    name="missions",
    help="Mission Ledger — track missions, tasks, and evidence.",
    no_args_is_help=True,
)

tasks_app = typer.Typer(
    name="tasks",
    help="Task operations within the Mission Ledger.",
    no_args_is_help=True,
)

evidence_app = typer.Typer(
    name="evidence",
    help="Evidence packs and verification.",
    no_args_is_help=True,
)

console = Console(stderr=True)

_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")
_DEFAULT_EVIDENCE = Path("_grimoire-runtime-output/evidence")
_DEFAULT_TASK_FLOW_EVENTS = Path("_grimoire-runtime-output/task-flow/events.jsonl")


def _ledger(root: Path | None = None) -> MissionLedger:
    return MissionLedger(root or _DEFAULT_LEDGER)


def _evidence_svc(root: Path | None = None) -> EvidenceService:
    return EvidenceService(root or _DEFAULT_EVIDENCE)


def _get_fmt(ctx: typer.Context) -> str:
    return (ctx.obj or {}).get("output", "text")


def _resolve_task_flow_events(path: Path) -> Path:
    if path.exists() or path.is_absolute():
        return path
    for parent in Path.cwd().resolve().parents:
        candidate = parent / path
        if candidate.exists():
            return candidate
    return path


# ── grimoire missions list ────────────────────────────────────────────────────

@missions_app.command("list")
def missions_list(
    ctx: typer.Context,
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """List all missions."""
    ledger = _ledger(ledger_root)
    missions = ledger.list_missions()
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps([m.to_dict() for m in missions], indent=2))
        return

    if not missions:
        console.print("[dim]No missions found.[/dim]")
        return

    tbl = Table(title="Missions")
    tbl.add_column("ID", style="bold cyan")
    tbl.add_column("Title")
    tbl.add_column("Status")
    tbl.add_column("Risk")
    for m in missions:
        tbl.add_row(m.id, m.title, m.status.value, m.risk_profile.value)
    console.print(tbl)


# ── grimoire missions import-task-flow ────────────────────────────────────────

@missions_app.command("import-task-flow")
def missions_import_task_flow(
    ctx: typer.Context,
    events_path: Path = typer.Option(_DEFAULT_TASK_FLOW_EVENTS, "--events", help="Task-flow events JSONL path."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
    mission_id: str = typer.Option("MIS-task-flow-001", "--mission-id", help="Stable mission ID for task-flow imports."),
    mission_title: str = typer.Option("Grimoire task-flow runtime", "--mission-title", help="Mission title."),
) -> None:
    """Import task-flow runtime events into MissionLedger idempotently."""
    from grimoire.missions.task_flow_adapter import import_task_flow_events

    ledger = _ledger(ledger_root)
    report = import_task_flow_events(
        ledger,
        _resolve_task_flow_events(events_path),
        mission_id=mission_id,
        mission_title=mission_title,
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return
    console.print("[green]Task-flow events imported[/green]")
    console.print(f"  Mission : {report.mission_id}")
    console.print(f"  Events  : {report.events_imported} imported, {report.events_skipped} skipped")
    console.print(f"  Tasks   : {report.tasks_created} created, {report.tasks_updated} updated")
    console.print(f"  Incidents: {report.incidents_created}")


# ── grimoire missions create ──────────────────────────────────────────────────

@missions_app.command("create")
def missions_create(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Mission title."),
    origin: str = typer.Option("user", "--origin", help="Origin (user/system/etc)."),
    risk: str = typer.Option("standard", "--risk", help="Risk profile (light/standard/strict/security_critical/release)."),
    description: str = typer.Option("", "--description", "-d", help="Mission description."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Create a new mission (status: draft)."""
    ledger = _ledger(ledger_root)
    mission = ledger.create_mission(
        title,
        origin=origin,
        description=description,
        risk_profile=RiskProfile(risk),
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(mission.to_dict(), indent=2))
    else:
        console.print(f"[green]Created mission:[/green] {mission.id}")
        console.print(f"  Title:  {mission.title}")
        console.print(f"  Status: {mission.status.value}")


# ── grimoire missions open ────────────────────────────────────────────────────

@missions_app.command("open")
def missions_open(
    ctx: typer.Context,
    mission_id: str = typer.Argument(..., help="Mission ID to open."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Transition a mission from draft → open."""
    from grimoire.missions.schemas import MissionState
    ledger = _ledger(ledger_root)
    mission = ledger.transition_mission(mission_id, MissionState.OPEN, actor_id="cli")
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(mission.to_dict(), indent=2))
    else:
        console.print(f"[green]Mission opened:[/green] {mission.id} → {mission.status.value}")


# ── grimoire missions close ───────────────────────────────────────────────────

@missions_app.command("close")
def missions_close(
    ctx: typer.Context,
    mission_id: str = typer.Argument(..., help="Mission ID to close."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Close a mission (verifying → closed)."""
    from grimoire.missions.schemas import MissionState
    ledger = _ledger(ledger_root)
    mission = ledger.transition_mission(mission_id, MissionState.CLOSED, actor_id="cli")
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(mission.to_dict(), indent=2))
    else:
        console.print(f"[green]Mission closed:[/green] {mission.id}")


# ── grimoire missions show ────────────────────────────────────────────────────

@missions_app.command("show")
def missions_show(
    ctx: typer.Context,
    mission_id: str = typer.Argument(..., help="Mission ID."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Show a mission and its tasks."""
    ledger = _ledger(ledger_root)
    mission = ledger.get_mission(mission_id)
    if mission is None:
        console.print(f"[red]Mission not found:[/red] {mission_id}")
        raise typer.Exit(1)
    tasks = ledger.list_tasks(mission_id)

    fmt = _get_fmt(ctx)
    if fmt == "json":
        data: dict[str, Any] = {**mission.to_dict(), "tasks": [t.to_dict() for t in tasks]}
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"\n[bold]{mission.id}[/bold]  {mission.title}")
    console.print(f"  Status: {mission.status.value}  |  Risk: {mission.risk_profile.value}")
    if mission.description:
        console.print(f"  {mission.description}\n")

    if tasks:
        tbl = Table(title=f"Tasks ({len(tasks)})")
        tbl.add_column("ID", style="bold cyan")
        tbl.add_column("Title")
        tbl.add_column("Type")
        tbl.add_column("Status")
        tbl.add_column("Risk")
        for t in tasks:
            tbl.add_row(t.id, t.title, t.type.value, t.status.value, t.risk_profile.value)
        console.print(tbl)
    else:
        console.print("[dim]No tasks yet.[/dim]")


# ── grimoire tasks list ───────────────────────────────────────────────────────

@tasks_app.command("list")
def tasks_list(
    ctx: typer.Context,
    mission_id: str = typer.Option("", "--mission", "-m", help="Filter by mission ID."),
    state: str = typer.Option("", "--state", "-s", help="Filter by state (ready/blocked/running/...)."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """List tasks, optionally filtered by mission or state."""
    ledger = _ledger(ledger_root)
    tasks = ledger.list_tasks(mission_id or None)
    if state:
        try:
            filter_state = TaskState(state)
            tasks = [t for t in tasks if t.status == filter_state]
        except ValueError:
            console.print(f"[red]Unknown state:[/red] {state}")
            raise typer.Exit(1) from None

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps([t.to_dict() for t in tasks], indent=2))
        return

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    tbl = Table(title="Tasks")
    tbl.add_column("ID", style="bold cyan")
    tbl.add_column("Mission")
    tbl.add_column("Title")
    tbl.add_column("Status")
    tbl.add_column("Type")
    for t in tasks:
        tbl.add_row(t.id, t.mission_id, t.title, t.status.value, t.type.value)
    console.print(tbl)


# ── grimoire tasks create ─────────────────────────────────────────────────────

@tasks_app.command("create")
def tasks_create(
    ctx: typer.Context,
    mission_id: str = typer.Argument(..., help="Mission ID."),
    title: str = typer.Argument(..., help="Task title."),
    type_: str = typer.Option("implementation", "--type", "-t", help="Task type."),
    risk: str = typer.Option("standard", "--risk", help="Risk profile."),
    acceptance: str = typer.Option("", "--acceptance", "-a", help="Acceptance criteria (comma-separated)."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Create a new task in a mission."""
    acc = tuple(s.strip() for s in acceptance.split(",") if s.strip()) if acceptance else ("Task completed",)
    ledger = _ledger(ledger_root)
    task = ledger.create_task(
        mission_id,
        title,
        type=TaskType(type_),
        risk_profile=RiskProfile(risk),
        acceptance=acc,
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(task.to_dict(), indent=2))
    else:
        console.print(f"[green]Created task:[/green] {task.id}")
        console.print(f"  Title:  {task.title}")
        console.print(f"  Status: {task.status.value}")


# ── grimoire tasks ready ──────────────────────────────────────────────────────

@tasks_app.command("ready")
def tasks_ready(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to mark ready."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Transition task proposed → ready."""
    ledger = _ledger(ledger_root)
    task = ledger.transition_task(task_id, TaskState.READY, actor_id="cli")
    _print_task_transition(task, ctx)


# ── grimoire tasks claim ──────────────────────────────────────────────────────

@tasks_app.command("claim")
def tasks_claim(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to claim."),
    actor: str = typer.Option("agent", "--actor", help="Actor claiming the task."),
    host: str = typer.Option("host-unknown", "--host", help="Host ID."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Claim a task (ready → claimed)."""
    ledger = _ledger(ledger_root)
    task = ledger.claim_task(task_id, actor_id=actor, host_id=host)
    _print_task_transition(task, ctx)


# ── grimoire tasks run ────────────────────────────────────────────────────────

@tasks_app.command("run")
def tasks_run(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to mark as running."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Transition task claimed → running."""
    ledger = _ledger(ledger_root)
    task = ledger.transition_task(task_id, TaskState.RUNNING, actor_id="cli")
    _print_task_transition(task, ctx)


# ── grimoire tasks verify ─────────────────────────────────────────────────────

@tasks_app.command("verify")
def tasks_verify(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to send to verification."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Transition task running → needs_verification."""
    ledger = _ledger(ledger_root)
    task = ledger.transition_task(task_id, TaskState.NEEDS_VERIFICATION, actor_id="cli")
    _print_task_transition(task, ctx)


# ── grimoire tasks close ──────────────────────────────────────────────────────

@tasks_app.command("close")
def tasks_close(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to close."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Close a task (needs_verification → closed)."""
    ledger = _ledger(ledger_root)
    incidents = ledger.open_incidents(task_id)
    if incidents:
        console.print(f"[red]Cannot close task:[/red] {len(incidents)} open incident(s). Resolve first.")
        raise typer.Exit(1)
    task = ledger.transition_task(task_id, TaskState.CLOSED, actor_id="cli")
    _print_task_transition(task, ctx)


def _print_task_transition(task: Any, ctx: typer.Context) -> None:
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(task.to_dict(), indent=2))
    else:
        console.print(f"[green]{task.id}[/green] → {task.status.value}")


# ── grimoire evidence add ─────────────────────────────────────────────────────

@evidence_app.command("add")
def evidence_add(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID."),
    kind: str = typer.Argument(..., help="Evidence kind: test/log/diff/doc/schema/trace/screenshot/report."),
    uri: str = typer.Argument(..., help="URI or path of the evidence item."),
    summary: str = typer.Option("", "--summary", "-s", help="Short summary."),
    profile: str = typer.Option("standard", "--profile", "-p", help="Evidence profile."),
    evidence_root: Path = typer.Option(_DEFAULT_EVIDENCE, "--evidence", help="Evidence root directory."),
) -> None:
    """Add an evidence item to a task and create/update its EvidencePack."""
    svc = _evidence_svc(evidence_root)

    path = Path(uri)
    if path.exists() and path.is_file():
        item = EvidenceItem.from_file(f"evitem-{task_id}-{kind}", EvidenceKind(kind), path, summary=summary)
    else:
        item = EvidenceItem.from_text(f"evitem-{task_id}-{kind}", EvidenceKind(kind), uri, uri=uri, summary=summary)

    pack = svc.create_pack(task_id, EvidenceProfile(profile), [item])
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(pack.to_dict(), indent=2))
    else:
        console.print(f"[green]Evidence pack created:[/green] {pack.id}")
        console.print(f"  Task:  {pack.task_id}")
        console.print(f"  Items: {len(pack.items)}")


# ── grimoire evidence verify ──────────────────────────────────────────────────

@evidence_app.command("verify")
def evidence_verify(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="Task ID to verify."),
    evidence_root: Path = typer.Option(_DEFAULT_EVIDENCE, "--evidence", help="Evidence root directory."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
) -> None:
    """Verify the latest evidence pack for a task and emit a verdict."""
    svc = _evidence_svc(evidence_root)
    packs = svc.list_packs(task_id)
    if not packs:
        console.print(f"[red]No evidence packs found for task:[/red] {task_id}")
        raise typer.Exit(1)
    latest_pack = packs[-1]
    ledger = _ledger(ledger_root)
    task = ledger.get_task(task_id)
    acceptance = task.acceptance if task else ()
    verdict = svc.verify(latest_pack, acceptance=acceptance)

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(verdict.to_dict(), indent=2))
        return

    icon = "[green]PASSED[/green]" if verdict.verdict.value == "passed" else "[red]FAILED[/red]"
    console.print(f"Verification verdict: {icon}")
    for check in verdict.checks:
        check_icon = "[green]✓[/green]" if check.result.value == "passed" else "[red]✗[/red]"
        line = f"  {check_icon} {check.id}"
        if check.reason:
            line += f"  [dim]{check.reason}[/dim]"
        console.print(line)

    if verdict.decision.close_task:
        console.print("\n[green]→ Task can be closed.[/green]")
    elif verdict.decision.reopen_task:
        console.print("\n[yellow]→ Task needs rework (reopen).[/yellow]")


# ── grimoire evidence list ────────────────────────────────────────────────────

@evidence_app.command("list")
def evidence_list(
    ctx: typer.Context,
    task_id: str = typer.Option("", "--task", "-t", help="Filter by task ID."),
    evidence_root: Path = typer.Option(_DEFAULT_EVIDENCE, "--evidence", help="Evidence root directory."),
) -> None:
    """List evidence packs."""
    svc = _evidence_svc(evidence_root)
    packs = svc.list_packs(task_id or None)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps([p.to_dict() for p in packs], indent=2))
        return

    if not packs:
        console.print("[dim]No evidence packs found.[/dim]")
        return

    tbl = Table(title="Evidence Packs")
    tbl.add_column("ID", style="bold cyan")
    tbl.add_column("Task")
    tbl.add_column("Profile")
    tbl.add_column("Items")
    tbl.add_column("Created")
    for p in packs:
        tbl.add_row(p.id, p.task_id, p.profile.value, str(len(p.items)), p.created_at[:10])
    console.print(tbl)


# ── grimoire intake ───────────────────────────────────────────────────────────

intake_app = typer.Typer(
    name="intake",
    help="Mission Intake — classify and propose tasks from a request.",
    no_args_is_help=True,
)


@intake_app.command("analyze")
def intake_analyze(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Raw request text to analyze."),
) -> None:
    """Classify a request and propose mission tasks via the Intake Service."""
    from grimoire.missions.intake import IntakeRequest, MissionIntakeService
    svc = MissionIntakeService()
    result = svc.analyze(IntakeRequest(raw_text=text))
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    console.print(f"\n[bold]Mission type:[/bold] {result.mission_type}")
    console.print(f"[bold]Risk profile:[/bold] {result.risk_profile.value}")
    console.print(f"[bold]Confidence:[/bold]   {result.confidence:.0%}")
    if result.task_proposals:
        tbl = Table(title=f"Task proposals ({len(result.task_proposals)})")
        tbl.add_column("Type", style="bold cyan")
        tbl.add_column("Title")
        tbl.add_column("Risk")
        for p in result.task_proposals:
            tbl.add_row(p.task_type.value, p.title, p.risk_profile.value)
        console.print(tbl)
    else:
        console.print("[dim]No task proposals generated.[/dim]")


# ── grimoire cockpit ──────────────────────────────────────────────────────────

cockpit_app = typer.Typer(
    name="cockpit",
    help="Cockpit — operator view of missions, workflows, and incidents.",
    no_args_is_help=True,
)

_DEFAULT_KERNEL = Path("_grimoire-runtime-output/kernel")


@cockpit_app.command("show")
def cockpit_show(
    ctx: typer.Context,
    mission_id: str = typer.Option("", "--mission", "-m", help="Filter by mission ID."),
    ledger_root: Path = typer.Option(_DEFAULT_LEDGER, "--ledger", help="Ledger root directory."),
    kernel_root: Path = typer.Option(_DEFAULT_KERNEL, "--kernel", help="Kernel root directory."),
    evidence_root: Path = typer.Option(_DEFAULT_EVIDENCE, "--evidence", help="Evidence root directory."),
) -> None:
    """Display the Cockpit — missions, active workflows, verification queue, incidents."""
    from grimoire.missions.projections import build_cockpit_from_paths
    cockpit = build_cockpit_from_paths(
        ledger_root,
        kernel_root,
        evidence_root=evidence_root if evidence_root.exists() else None,
        mission_id=mission_id or None,
    )
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(cockpit.to_dict(), indent=2))
        return

    console.print(f"\n[bold]Cockpit[/bold]  (generated {cockpit.generated_at[:16]})")
    console.print(f"  Open missions: [bold]{cockpit.open_mission_count}[/bold]  |  "
                  f"Open incidents: [bold]{cockpit.total_incident_count}[/bold]  |  "
                  f"Verification queue: [bold]{len(cockpit.verification_queue)}[/bold]")

    if cockpit.missions:
        tbl = Table(title="Missions")
        tbl.add_column("ID", style="bold cyan")
        tbl.add_column("Title")
        tbl.add_column("Status")
        tbl.add_column("Tasks")
        tbl.add_column("Running")
        tbl.add_column("Incidents")
        for m in cockpit.missions:
            tbl.add_row(
                m.mission_id, m.title[:40], m.status,
                str(m.task_count), str(m.running_count),
                str(len(m.open_incident_ids)),
            )
        console.print(tbl)

    if cockpit.active_workflows:
        tbl2 = Table(title="Active Workflows")
        tbl2.add_column("WFI", style="bold cyan")
        tbl2.add_column("Recipe")
        tbl2.add_column("Status")
        tbl2.add_column("Events")
        for w in cockpit.active_workflows:
            tbl2.add_row(w.wfi_id, w.recipe_id, w.status, str(w.event_count))
        console.print(tbl2)

    if cockpit.verification_queue:
        console.print(f"\n[yellow]Verification queue:[/yellow] {', '.join(cockpit.verification_queue)}")
