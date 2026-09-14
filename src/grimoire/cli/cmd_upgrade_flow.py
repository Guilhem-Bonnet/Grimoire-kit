"""``grimoire upgrade-flow`` — updating a project is a flow, not a bare `up` (issue #490).

Named ``upgrade-flow`` rather than ``upgrade``: that name is already taken
by :mod:`grimoire.cli.cmd_upgrade` (the v2 → v3 structure migration). Two
unrelated meanings of "upgrade" needed two commands, not one overloaded one.

``run`` drives ``registry/blueprints/project-upgrade.blueprint.json``
through the real flow engine (:mod:`grimoire.flows.engine`) — the same
primitives ``grimoire flow run``/``resume`` expose for any blueprint. Its
default, ``--executor interactive``, submits each mechanical/proposal
node's output *itself*, calling straight into
:mod:`grimoire.tools.project_upgrade` — no LLM involved, because none of
backup/preview/apply/orphans or the three proposal-writers need judgment to
execute (the judgment is "which proposal to write", not "how to write a
proposal"). ``--executor dispatch`` instead delegates every node to the
provider cascade, exactly like ``grimoire flow run --executor dispatch``
would for this same blueprint file — useful when a host wants a model to
attempt ad hoc remediation beyond what the mechanical default does. The
flow always stops, undecided, at the ``destructive`` checkpoint node: this
command never submits a ``checkpoint_decision`` on anyone's behalf.

``probe-hook``, ``orphans``, ``backup``, ``preview``, ``apply``, ``propose``
and ``check`` are the utility subcommands the blueprint's own acceptance
commands invoke (``python -m grimoire upgrade-flow check <node>``) — they
are not a second implementation, they call the exact same
:mod:`grimoire.tools.project_upgrade` functions ``run`` uses as its
interactive node handlers.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireRuntimeError

console = Console(stderr=True)

upgrade_flow_app = typer.Typer(
    help=(
        "Mettre à jour un projet comme un flow, pas un `up` nu : sauvegarde, aperçu, "
        "orphelins, application, propositions, vérification.\n\n"
        "Ordre des 9 nœuds (`run` les exécute dans cet ordre, jamais un autre) : "
        "backup -> preview -> orphans -> apply -> overrides -> memory -> needs-hosts "
        "-> verify -> destructive.\n\n"
        "Trois catégories :\n"
        "V0 (mécanique, appliqué directement) : backup, preview, orphans, apply, verify.\n"
        "V1 (jugement, ne fait jamais qu'écrire une proposition — jamais l'artefact) : "
        "overrides, memory, needs-hosts.\n"
        "V2 (checkpoint humain forcé) : destructive — `run` s'y arrête toujours, non "
        "décidé ; personne ne soumet de décision à la place d'un humain.\n\n"
        "Sauvegarde : tarball + manifeste mémoire sous "
        "`_archive/<date>-pre-<version>/` à la racine du projet (jamais écrasés — un "
        "second `backup` le même jour dont le contenu a changé prend un suffixe "
        "`-2`, `-3`, …)."
    ),
    no_args_is_help=False,
)

_PROJECT_ROOT = Annotated[Path, typer.Option("--project-root", help="Racine du projet cible.")]
_JSON_OPTION = Annotated[bool, typer.Option("--json", help="Sortie JSON.")]


def _fmt(ctx: typer.Context, *, json_flag: bool = False) -> str:
    return "json" if json_flag or (ctx.obj or {}).get("output") == "json" else "text"


def _fail(ctx: typer.Context, message: str, *, json_flag: bool = False) -> None:
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        console.print(f"[red]refusé[/red] : {message}")
    raise typer.Exit(1)


# ── utility subcommands — also what the blueprint's own acceptances call ────


@upgrade_flow_app.command("backup")
def upgrade_flow_backup(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), json_flag: _JSON_OPTION = False) -> None:
    """Tarball + manifeste SHA-256 de la mémoire, sous `_archive/<date>-pre-<version>/`."""
    from grimoire.tools.project_upgrade import backup_project

    result = backup_project(project_root.resolve())
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] tarball : {result.tarball} ({result.tarball_entries} entrée(s))")
    console.print(f"[green]OK[/green] manifeste : {result.manifest} ({result.memory_files} fichier(s))")


@upgrade_flow_app.command("preview")
def upgrade_flow_preview(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), json_flag: _JSON_OPTION = False) -> None:
    """`up --dry-run` + `host sync --dry-run`, écrits dans `_grimoire-output/upgrade/<date>/preview.md`."""
    from grimoire.tools.project_upgrade import preview_upgrade

    result = preview_upgrade(project_root.resolve())
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] aperçu écrit : {result.report_path}")


@upgrade_flow_app.command("apply")
def upgrade_flow_apply(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), json_flag: _JSON_OPTION = False) -> None:
    """`grimoire up`, puis `doctor -o json` et le hook SessionStart rejoué comme acceptance."""
    from grimoire.tools.project_upgrade import apply_upgrade

    result = apply_upgrade(project_root.resolve())
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": result.ok, **result.to_dict()}, ensure_ascii=False))
    elif result.ok:
        if result.repairs_proposed:
            console.print(
                f"[green]OK[/green] mis à niveau, {result.repairs_proposed} défaut(s) préexistant(s) en proposition "
                "(`grimoire proposals list`)"
            )
        else:
            console.print("[green]OK[/green] up appliqué, doctor vert, hook sans erreur")
    else:
        console.print(f"[red]anomalie[/red] doctor={list(result.doctor_failures)} hook={result.hook.detail}")
    if not result.ok:
        raise typer.Exit(1)


@upgrade_flow_app.command("probe-hook")
def upgrade_flow_probe_hook(
    ctx: typer.Context,
    project_root: _PROJECT_ROOT = Path(),
    host: Annotated[str, typer.Option("--host", help="Hôte dont le hook est rejoué.")] = "claude",
    json_flag: _JSON_OPTION = False,
) -> None:
    """Rejoue le hook SessionStart et refuse si sa réponse porte une mention d'erreur (régression #423)."""
    from grimoire.tools.project_upgrade import probe_hook

    result = probe_hook(project_root.resolve(), host=host)
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": result.ok, **result.to_dict()}, ensure_ascii=False))
    else:
        tag = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
        console.print(f"{tag} {result.detail}")
    if not result.ok:
        raise typer.Exit(1)


@upgrade_flow_app.command("orphans")
def upgrade_flow_orphans(
    ctx: typer.Context,
    project_root: _PROJECT_ROOT = Path(),
    apply_flag: Annotated[bool, typer.Option("--apply", help="Archive réellement les orphelins (sinon : liste seule).")] = False,
    json_flag: _JSON_OPTION = False,
) -> None:
    """Liste les agents/wrappers que le kit installé ne livre plus ; `--apply` les déplace vers `_archive/.../orphans/` (jamais de suppression)."""
    from grimoire.tools.project_upgrade import archive_orphans, find_orphans

    root = project_root.resolve()
    report = find_orphans(root)
    moved: list[str] = []
    if apply_flag and report.orphans:
        moved = archive_orphans(root, report)

    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": True, "orphans": report.to_dict()["orphans"], "archived": moved}, ensure_ascii=False))
        return
    if not report.orphans:
        console.print("[green]Aucun orphelin.[/green]")
        return
    for orphan in report.orphans:
        console.print(f"  {orphan.name} : {', '.join(str(p) for p in orphan.paths)}")
    if apply_flag:
        console.print(f"[green]OK[/green] {len(moved)} fichier(s) archivé(s)")
    else:
        console.print(f"[yellow]{len(report.orphans)} orphelin(s)[/yellow] — relancez avec --apply pour archiver")


@upgrade_flow_app.command("propose")
def upgrade_flow_propose(
    ctx: typer.Context,
    node: Annotated[str, typer.Argument(help="overrides | memory | needs-hosts")],
    project_root: _PROJECT_ROOT = Path(),
    json_flag: _JSON_OPTION = False,
) -> None:
    """Écrit les propositions (jamais l'artefact) du nœud de jugement nommé."""
    from grimoire.tools.project_upgrade import propose_memory_links, propose_needs_hosts, propose_override_migrations

    handlers = {
        "overrides": propose_override_migrations,
        "memory": propose_memory_links,
        "needs-hosts": propose_needs_hosts,
    }
    handler = handlers.get(node)
    if handler is None:
        _fail(ctx, f"nœud de jugement inconnu : {node!r} (attendu : {', '.join(handlers)})", json_flag=json_flag)
        return

    proposals = handler(project_root.resolve())
    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": True, "proposals": [p.slug for p in proposals]}, ensure_ascii=False))
        return
    if not proposals:
        console.print("[green]Rien à proposer.[/green]")
        return
    for proposal in proposals:
        console.print(f"  {proposal.slug} — {proposal.carrier_reason}")


@upgrade_flow_app.command("verify")
def upgrade_flow_verify(ctx: typer.Context, project_root: _PROJECT_ROOT = Path(), json_flag: _JSON_OPTION = False) -> None:
    """Recompare le manifeste mémoire pris par `backup` ; seuls les écarts attendus (config.yaml) sont tolérés."""
    from grimoire.tools.project_upgrade import archive_root, verify_upgrade

    root = project_root.resolve()
    manifest = archive_root(root) / "memory-manifest-sha256.txt"
    try:
        result = verify_upgrade(root, manifest)
    except GrimoireRuntimeError as exc:
        _fail(ctx, str(exc), json_flag=json_flag)
        return

    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps({"ok": result.ok, **result.to_dict()}, ensure_ascii=False))
    elif result.ok:
        console.print(f"[green]OK[/green] rapport : {result.report_path}")
    else:
        console.print(f"[red]anomalie[/red] écarts={list(result.unexpected_diffs)} manquants={list(result.missing)}")
    if not result.ok:
        raise typer.Exit(1)


# ── check — the blueprint's own acceptance commands ─────────────────────────


def _check_backup(root: Path) -> tuple[bool, str]:
    from grimoire.tools.project_upgrade import backup_project

    result = backup_project(root)
    ok = result.tarball.is_file() and result.manifest.is_file()
    return ok, f"tarball={result.tarball.is_file()} manifest={result.manifest.is_file()}"


def _check_preview(root: Path) -> tuple[bool, str]:
    from grimoire.tools.project_upgrade import run_output_dir

    report = run_output_dir(root) / "preview.md"
    return report.is_file(), str(report)


def _check_apply(root: Path) -> tuple[bool, str]:
    from grimoire.tools.project_upgrade import apply_upgrade

    result = apply_upgrade(root)
    return result.ok, f"doctor_failures={list(result.doctor_failures)} hook_ok={result.hook.ok}"


def _check_orphans(root: Path) -> tuple[bool, str]:
    from grimoire.tools.project_upgrade import find_orphans

    report = find_orphans(root)
    return not report.orphans, f"orphans={report.names}"


def _check_proposed(artifact_type: str) -> Any:
    def _check(root: Path) -> tuple[bool, str]:
        from grimoire.proposals import list_proposals

        proposals = [p for p in list_proposals(root) if p.artifact_type == artifact_type]
        # Rien à proposer est un succès légitime — l'acceptance ne juge que
        # "la proposition existe si l'état du projet en réclame une", jamais
        # "il doit toujours y en avoir au moins une".
        return True, f"{len(proposals)} proposition(s) `{artifact_type}`"

    return _check


def _check_verify(root: Path) -> tuple[bool, str]:
    from grimoire.tools.project_upgrade import archive_root, verify_upgrade

    manifest = archive_root(root) / "memory-manifest-sha256.txt"
    try:
        result = verify_upgrade(root, manifest)
    except GrimoireRuntimeError as exc:
        return False, str(exc)
    return result.ok, f"unexpected={list(result.unexpected_diffs)} missing={list(result.missing)}"


_CHECKS: dict[str, Any] = {
    "backup": _check_backup,
    "preview": _check_preview,
    "apply": _check_apply,
    "orphans": _check_orphans,
    "overrides": _check_proposed("override-migration"),
    "memory": _check_proposed("memory-link"),
    "needs-hosts": _check_proposed("needs-hosts"),
    "verify": _check_verify,
}


@upgrade_flow_app.command("check")
def upgrade_flow_check(
    ctx: typer.Context,
    node: Annotated[str, typer.Argument(help="Node id du blueprint project-upgrade.")],
    project_root: _PROJECT_ROOT = Path(),
) -> None:
    """Ré-exécute l'acceptance mécanique du nœud nommé — sortie 0/1, c'est ce que les nœuds du blueprint invoquent."""
    check_fn = _CHECKS.get(node)
    if check_fn is None:
        console.print(f"[red]node inconnu[/red] : {node!r}")
        raise typer.Exit(1)
    ok, detail = check_fn(project_root.resolve())
    tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    console.print(f"{tag} {node} : {detail}")
    if not ok:
        raise typer.Exit(1)


# ── run — drives the real flow engine ────────────────────────────────────────


def _node_handlers() -> dict[str, Any]:
    """One handler per node this command can execute itself under ``--executor interactive``.

    Every node except the ``destructive`` checkpoint, which is always left
    for a human (or the host driving a different session) to decide — the
    loop in :func:`upgrade_flow_run` stops the moment ``contract.node_id``
    is not a key here. The dict's own order is documentation only; the real
    sequence — backup, preview, orphans (before ``apply``: a leftover
    orphan trips ``up``'s/``host sync``'s distinction guard, per the
    2026-09-11 migration report), apply, overrides, memory, needs-hosts,
    verify — comes from the blueprint's edges via ``topo_order``, never
    re-declared here.
    """
    from grimoire.tools.project_upgrade import (
        apply_upgrade,
        archive_orphans,
        archive_root,
        backup_project,
        find_orphans,
        preview_upgrade,
        propose_memory_links,
        propose_needs_hosts,
        propose_override_migrations,
        verify_upgrade,
    )

    def _backup(root: Path) -> dict[str, Any]:
        return backup_project(root).to_dict()

    def _preview(root: Path) -> dict[str, Any]:
        return preview_upgrade(root).to_dict()

    def _apply(root: Path) -> dict[str, Any]:
        result = apply_upgrade(root)
        if not result.ok:
            raise GrimoireRuntimeError(f"apply refusé : doctor={list(result.doctor_failures)} hook={result.hook.detail}")
        return result.to_dict()

    def _orphans(root: Path) -> dict[str, Any]:
        report = find_orphans(root)
        archive_orphans(root, report)
        return report.to_dict()

    def _overrides(root: Path) -> dict[str, Any]:
        return {"proposals": [p.slug for p in propose_override_migrations(root)]}

    def _memory(root: Path) -> dict[str, Any]:
        return {"proposals": [p.slug for p in propose_memory_links(root)]}

    def _needs_hosts(root: Path) -> dict[str, Any]:
        return {"proposals": [p.slug for p in propose_needs_hosts(root)]}

    def _verify(root: Path) -> dict[str, Any]:
        manifest = archive_root(root) / "memory-manifest-sha256.txt"
        result = verify_upgrade(root, manifest)
        if not result.ok:
            raise GrimoireRuntimeError(f"verify refusé : {list(result.unexpected_diffs)} manquants={list(result.missing)}")
        return result.to_dict()

    return {
        "backup": _backup,
        "preview": _preview,
        "apply": _apply,
        "orphans": _orphans,
        "overrides": _overrides,
        "memory": _memory,
        "needs-hosts": _needs_hosts,
        "verify": _verify,
    }


def _submit_envelope(contract: Any, detail: dict[str, Any]) -> dict[str, Any]:
    return {"pins": {pin.pin_id: {"contract": pin.contract, "detail": detail} for pin in contract.outputs}}


@upgrade_flow_app.command("run")
def upgrade_flow_run(
    ctx: typer.Context,
    project_root: _PROJECT_ROOT = Path(),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="S'arrête après le nœud `preview` (backup + preview seuls).")] = False,
    executor: Annotated[str, typer.Option("--executor", help="interactive (défaut, mécanique) ou dispatch (cascade).")] = "interactive",
    json_flag: _JSON_OPTION = False,
) -> None:
    """Lance le blueprint `project-upgrade` — mécanique par défaut, jamais d'écriture au-delà de ce que chaque nœud documente.

    Neuf nœuds, toujours dans cet ordre (jamais un autre — orphans avant
    apply, jamais l'inverse, cf. la migration du 2026-09-11) :

    \b
    1. backup       (V0) tarball + manifeste mémoire, sous
                     `_archive/<date>-pre-<version>/` à la racine du projet
                     (jamais écrasés — suffixe `-2`, `-3`, … si le contenu a
                     changé depuis une snapshot déjà prise aujourd'hui).
    2. preview      (V0) `up --dry-run` + `host sync --dry-run`.
    3. orphans      (V0) archive les agents/wrappers qu'un kit antérieur a
                     laissés — jamais un agent encore livré, déclaré ou
                     surchargé par le projet.
    4. apply        (V0) `grimoire up` réel, puis `doctor` et le hook rejoué
                     comme acceptance.
    5. overrides    (V1) propose les migrations d'overrides en dérive —
                     n'écrit jamais l'override lui-même.
    6. memory       (V1) propose de raccorder les fiches mémoire orphelines.
    7. needs-hosts  (V1) propose de déclarer besoins/hôtes non résolus.
    8. verify       (V0) recompare le manifeste mémoire pris par `backup`.
    9. destructive  (V2) checkpoint humain forcé — tout ce qui retirerait
                     quelque chose s'arrête ici, non décidé.

    V0 = mécanique, appliqué directement par cette commande. V1 = jugement,
    ne fait jamais qu'écrire une proposition (`grimoire proposals accept`
    est la seule porte vers l'artefact). V2 = jamais soumis ici : `run`
    s'arrête toujours au nœud `destructive`, non décidé — cette commande ne
    soumet jamais de `checkpoint_decision` à la place d'un humain.
    `--dry-run` s'arrête plus tôt encore, juste après `preview`.
    """
    from grimoire.flows.engine import FlowEngine
    from grimoire.flows.executor import InteractiveNodeExecutor
    from grimoire.tools.project_upgrade import bundled_blueprint_path

    if executor not in ("interactive", "dispatch"):
        _fail(ctx, f"--executor inconnu : {executor!r} (attendu : interactive, dispatch)", json_flag=json_flag)
        return
    if dry_run and executor == "dispatch":
        _fail(ctx, "--dry-run n'est pris en charge qu'avec --executor interactive", json_flag=json_flag)
        return

    root = project_root.resolve()
    blueprint = bundled_blueprint_path()

    if executor == "dispatch":
        from grimoire.flows.dispatch_executor import run_with_dispatch

        try:
            dispatch_outcome = run_with_dispatch(FlowEngine(
                kernel_root=root / "_grimoire-runtime-output" / "runtime",
                flows_root=root / "_grimoire-runtime-output" / "flows",
                project_root=root,
            ), blueprint, project_root=root)
        except GrimoireRuntimeError as exc:
            _fail(ctx, str(exc), json_flag=json_flag)
            return
        if _fmt(ctx, json_flag=json_flag) == "json":
            typer.echo(json.dumps(dispatch_outcome.to_dict(), ensure_ascii=False))
        else:
            console.print(f"[bold]{dispatch_outcome.run_id}[/bold] — {dispatch_outcome.status}")
        if dispatch_outcome.status == "blocked":
            raise typer.Exit(1)
        return

    engine = FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )
    handlers = _node_handlers()

    def _silent_executor() -> InteractiveNodeExecutor:
        return InteractiveNodeExecutor(json_output=False, stream=io.StringIO())

    try:
        wfi, contract = engine.run(blueprint, executor=_silent_executor())
    except GrimoireRuntimeError as exc:
        _fail(ctx, str(exc), json_flag=json_flag)
        return

    run_id = wfi.id
    done: list[str] = []
    stopped_at: str | None = None
    repairs_proposed = 0
    while True:
        node_id = contract.node_id
        if node_id not in handlers:
            stopped_at = node_id
            break
        if dry_run and node_id == "orphans":
            stopped_at = "orphans (--dry-run : arrêté après preview)"
            break
        try:
            detail = handlers[node_id](root)
        except GrimoireRuntimeError as exc:
            _fail(ctx, f"node {node_id} : {exc}", json_flag=json_flag)
            return
        if node_id == "apply":
            repairs_proposed = int(detail.get("repairs_proposed") or 0)
        output = _submit_envelope(contract, detail)
        outcome = engine.resume(run_id, output=output, executor=_silent_executor())
        if not outcome.ok:
            _fail(ctx, f"node {node_id} refusé : {list(outcome.faults)}", json_flag=json_flag)
            return
        done.append(node_id)
        if outcome.finished:
            stopped_at = None
            break
        assert outcome.contract is not None
        contract = outcome.contract

    if _fmt(ctx, json_flag=json_flag) == "json":
        typer.echo(json.dumps(
            {"ok": True, "run_id": run_id, "done": done, "stopped_at": stopped_at, "repairs_proposed": repairs_proposed},
            ensure_ascii=False,
        ))
        return
    if repairs_proposed:
        console.print(
            f"[bold]{run_id}[/bold] — mis à niveau, {repairs_proposed} défaut(s) préexistant(s) en proposition "
            f"(`grimoire proposals list`) — terminé : {', '.join(done)}"
        )
    else:
        console.print(f"[bold]{run_id}[/bold] — terminé : {', '.join(done)}")
    if stopped_at:
        console.print(f"[yellow]en attente[/yellow] au nœud « {stopped_at} » — décision humaine requise")
