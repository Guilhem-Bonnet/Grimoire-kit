"""``grimoire hooks`` — install and inspect Grimoire git hooks.

Python port of the ``grimoire-init.sh hooks`` subcommand (bash resorption
plan, ``planning/resorption-bash.md``).  Improvements over the bash version:

- hooks directory resolved via ``git rev-parse --git-path hooks`` — correct
  inside git worktrees and with ``core.hooksPath``;
- hook sources resolved from the project checkout (kit repo, nested kit)
  with fallback to the wheel-bundled ``grimoire/data/framework/hooks``.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core import layout
from grimoire.data import framework_path

hooks_app = typer.Typer(help="Install and inspect Grimoire git hooks.")
console = Console(stderr=True)

# git hook name -> source file in framework/hooks/
_HOOK_MAP: dict[str, str] = {
    "pre-commit": "pre-commit-cc.sh",
    "post-checkout": "post-checkout.sh",
    "prepare-commit-msg": "prepare-commit-msg.sh",
    "commit-msg": "commit-msg.sh",
    "post-commit": "post-commit.sh",
    "pre-push": "pre-push.sh",
}
_MNEMO_SOURCE = "mnemo-consolidate.sh"
#: Where the hook scripts become callable *from the project*. ``.git/hooks/``
#: holds the copies git runs; pre-commit needs a tracked path it can spawn,
#: and ``framework/hooks/`` only exists in a checkout of the kit itself.
_PROJECT_HOOKS_DIR = f"{layout.KIT_DIR}/hooks"
_PRECOMMIT_CONFIG_TEMPLATE = ".pre-commit-config.tpl.yaml"


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def _git_path(name: str, cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", name],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    path = Path(out)
    return path if path.is_absolute() else (cwd / path).resolve()


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(out)


def _framework_hooks_dir(project_root: Path) -> Path | None:
    """Locate hook sources: project checkout first, then the bundled wheel data."""
    for candidate in (
        project_root / "framework" / "hooks",
        project_root / "grimoire-kit" / "framework" / "hooks",
    ):
        if candidate.is_dir():
            return candidate
    # ``framework_path()`` already resolves both a wheel install and an editable
    # one; resolving the package data directly missed the editable case, where
    # the framework lives at the repository root rather than under the package.
    try:
        bundled = framework_path() / "hooks"
    except FileNotFoundError:
        return None
    return bundled if bundled.is_dir() else None


def _is_grimoire_hook(path: Path) -> bool:
    try:
        return "Grimoire" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _is_current(dst: Path, src: Path) -> bool:
    """Is the installed hook still the one shipped by this version?

    ``install`` copies the source verbatim, then may append the Mnemo block to
    ``pre-commit`` — a trailing addition is expected, a divergent body is not.
    """
    try:
        installed = dst.read_text(encoding="utf-8", errors="replace")
        source = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return installed.startswith(source)


def _hook_states(hooks_dir: Path, sources: Path) -> dict[str, str]:
    """Per-hook state: installed | stale | third-party | missing.

    ``stale`` matters: a hook installed by an older version keeps running
    silently after an upgrade. Reporting it as installed turned the check into
    a green light for hooks that no longer match what they ship.
    """
    states: dict[str, str] = {}
    for hook_name, src_name in _HOOK_MAP.items():
        dst = hooks_dir / hook_name
        src = sources / src_name
        if not dst.is_file():
            states[hook_name] = "missing"
        elif not _is_grimoire_hook(dst):
            states[hook_name] = "third-party"
        elif src.is_file() and not _is_current(dst, src):
            states[hook_name] = "stale"
        else:
            states[hook_name] = "installed"
    return states


def _resolve_dirs(path: Path) -> tuple[Path, Path, Path]:
    """Return (project_root, hooks_dir, sources_dir) or exit with an error."""
    root = _git_toplevel(path)
    hooks_dir = _git_path("hooks", path)
    if root is None or hooks_dir is None:
        console.print("[red]Not inside a git repository.[/red]")
        raise typer.Exit(1)
    sources = _framework_hooks_dir(root)
    if sources is None:
        console.print("[red]framework/hooks not found (project checkout or bundled data).[/red]")
        raise typer.Exit(1)
    return root, hooks_dir, sources


_hooks_path_arg = typer.Argument(Path(), help="Project directory (default: current).")
_hook_opt = typer.Option("", "--hook", help="Install a single hook by git name.")
_force_opt = typer.Option(False, "--force", "-f", help="Overwrite third-party hooks instead of chaining.")


@hooks_app.command("list")
def hooks_list(ctx: typer.Context, path: Path = _hooks_path_arg) -> None:
    """List available Grimoire hooks and their installation state."""
    _, hooks_dir, sources = _resolve_dirs(path)
    states = _hook_states(hooks_dir, sources)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "hooks_dir": str(hooks_dir),
            "sources": str(sources),
            "hooks": [
                {"name": name, "source": _HOOK_MAP[name], "state": states[name]}
                for name in _HOOK_MAP
            ],
        }, indent=2))
        return
    tbl = Table(title="Grimoire git hooks")
    tbl.add_column("Hook")
    tbl.add_column("Source")
    tbl.add_column("State")
    style = {"installed": "green", "stale": "yellow", "third-party": "yellow", "missing": "red"}
    for name, src in _HOOK_MAP.items():
        tbl.add_row(name, src, f"[{style[states[name]]}]{states[name]}[/{style[states[name]]}]")
    console.print(tbl)


@hooks_app.command("status")
def hooks_status(ctx: typer.Context, path: Path = _hooks_path_arg) -> None:
    """Summarize hook installation state (exit 1 when incomplete)."""
    _, hooks_dir, sources = _resolve_dirs(path)
    states = _hook_states(hooks_dir, sources)
    installed = sum(1 for state in states.values() if state == "installed")
    stale = sum(1 for state in states.values() if state == "stale")
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "installed": installed, "stale": stale, "total": len(states), "states": states,
        }, indent=2))
    else:
        icons = {
            "installed": "[green][OK][/green]",
            "stale": "[yellow][~][/yellow]",
            "third-party": "[yellow][!][/yellow]",
            "missing": "[red][x][/red]",
        }
        for name, state in states.items():
            suffix = " (version obsolète)" if state == "stale" else ""
            console.print(f"  {icons[state]} {name}{suffix}")
        console.print(f"  {installed}/{len(states)} hooks à jour")
        if installed < len(states):
            console.print("  → grimoire hooks install")
    raise typer.Exit(0 if installed == len(states) else 1)


@hooks_app.command("install")
def hooks_install(
    ctx: typer.Context,
    path: Path = _hooks_path_arg,
    hook: str = _hook_opt,
    force: bool = _force_opt,
) -> None:
    """Install Grimoire git hooks into the repository."""
    root, hooks_dir, sources = _resolve_dirs(path)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    targets = [hook] if hook else list(_HOOK_MAP)
    installed: list[str] = []
    chained: list[str] = []
    skipped: list[str] = []

    for hook_name in targets:
        src_name = _HOOK_MAP.get(hook_name)
        if src_name is None:
            console.print(f"[yellow]Unknown hook: {hook_name} — skipped.[/yellow]")
            skipped.append(hook_name)
            continue
        src = sources / src_name
        if not src.is_file():
            console.print(f"[yellow]Missing source: {src} — skipped.[/yellow]")
            skipped.append(hook_name)
            continue
        dst = hooks_dir / hook_name
        if dst.is_file() and not _is_grimoire_hook(dst) and not force:
            # Preserve the third-party hook; drop ours next to it for manual chaining.
            chain_dir = hooks_dir.parent / ".git-hooks-precommit"
            chain_dir.mkdir(parents=True, exist_ok=True)
            chain_dst = chain_dir / f"grimoire-{hook_name}.sh"
            shutil.copyfile(src, chain_dst)
            chain_dst.chmod(chain_dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            chained.append(hook_name)
            continue
        shutil.copyfile(src, dst)
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(hook_name)

    # Mirror the scripts into the kit tier. Without this, both the generated
    # ``.pre-commit-config.yaml`` and the Mnemo chain call ``framework/hooks/…``,
    # a path that exists only in a checkout of the kit — every hook fails on the
    # first run in a real project.
    project_hooks = root / _PROJECT_HOOKS_DIR
    project_hooks.mkdir(parents=True, exist_ok=True)
    for src_name in (*_HOOK_MAP.values(), _MNEMO_SOURCE):
        src_file = sources / src_name
        if not src_file.is_file():
            continue
        dst_file = project_hooks / src_name
        shutil.copyfile(src_file, dst_file)
        dst_file.chmod(dst_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Inject Mnemo consolidation into a Grimoire pre-commit if absent.
    mnemo_src = sources / _MNEMO_SOURCE
    precommit = hooks_dir / "pre-commit"
    mnemo_injected = False
    if mnemo_src.is_file() and precommit.is_file() and _is_grimoire_hook(precommit):
        content = precommit.read_text(encoding="utf-8")
        if "mnemo" not in content:
            with precommit.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n# Grimoire Mnemo consolidation\n"
                    f'bash "$(git rev-parse --show-toplevel)/{_PROJECT_HOOKS_DIR}/{_MNEMO_SOURCE}"\n'
                )
            mnemo_injected = True

    # Seed .pre-commit-config.yaml from the template when absent.
    config_written = False
    template = sources / _PRECOMMIT_CONFIG_TEMPLATE
    config_dst = root / ".pre-commit-config.yaml"
    if template.is_file() and not config_dst.exists():
        rendered = template.read_text(encoding="utf-8").replace(
            "framework/hooks/", f"{_PROJECT_HOOKS_DIR}/",
        )
        config_dst.write_text(rendered, encoding="utf-8")
        config_written = True

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "installed": installed,
            "chained": chained,
            "skipped": skipped,
            "mnemo_injected": mnemo_injected,
            "precommit_config_written": config_written,
        }, indent=2))
        return
    for name in installed:
        console.print(f"  [green][OK][/green] {name} ← {_HOOK_MAP[name]}")
    for name in chained:
        console.print(f"  [yellow][!][/yellow] {name} tiers préservé — copie dans .git-hooks-precommit/")
    if mnemo_injected:
        console.print("  [green][+][/green] mnemo-consolidate injecté dans pre-commit")
    if config_written:
        console.print("  [green][OK][/green] .pre-commit-config.yaml généré")
    console.print(f"  {len(installed)} hook(s) installé(s)")
