"""Lazy command-tree loading for the ``grimoire`` CLI (issue #405).

Building the full Typer/Click command tree eagerly means importing every
sub-command module — and its heavy dependencies (``grimoire.flows``,
``grimoire.missions.dispatch``, ``grimoire.memory``, ``grimoire.cli.cmd_cockpit``...)
— before Typer even knows which command the user asked for. That import cost
used to be paid by *every* invocation, including ``grimoire --version`` and
every hook/MCP call (see the measurement in issue #405).

:class:`LazyTyperGroup` defers that import to the moment a specific
sub-command is actually resolved — real dispatch, or that sub-command's own
``--help``. Importing ``grimoire.cli.app`` or running an unrelated command
never touches a lazy sub-command's module.

The two registries (:class:`LazyGroupSpec` for ``add_typer``-style sub-apps,
:class:`LazyCommandSpec` for single ``@app.command`` functions) exist purely
as name -> (module, attribute) mappings plus the metadata Typer needs to
render the parent's own ``--help`` panel (short description, hidden flag,
rich panel) — see ``app.py`` for the actual data.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, ClassVar

import typer
from typer.core import TyperGroup
from typer.models import CommandInfo, TyperInfo

# Typer's own default (``typer.Typer(pretty_exceptions_short=True)``).
# Nothing in this CLI overrides it on the root app or any sub-app (verified:
# no ``pretty_exceptions_short=`` anywhere under ``grimoire.cli``), and the
# value isn't retrievable from a built click.Group afterwards — so it's
# fixed here rather than threaded through every lazy build call.
_PRETTY_EXCEPTIONS_SHORT = True


@dataclass(frozen=True)
class LazyGroupSpec:
    """A sub-``typer.Typer`` app, registered by name, imported on first use."""

    module: str
    attr: str
    rich_help_panel: str | None = None
    hidden: bool = False


@dataclass(frozen=True)
class LazyCommandSpec:
    """A single command function, registered by name, imported on first use."""

    module: str
    attr: str
    rich_help_panel: str | None = None
    hidden: bool = False


# Two aliases can point at the same backing module with *different* metadata
# (``debugger``: visible, panel "Data" / ``dbg``: hidden, no panel) — the
# built Click object carries that metadata, so it must be cached per (name,
# spec), not per (module, attr). Re-importing the module for the second alias
# is free anyway (``sys.modules`` already has it); only the Click-object
# build is repeated, once, only if both aliases are ever resolved in the same
# process (never happens on the measured paths — only on a full `--help`).
_build_cache: dict[tuple[str, Any], Any] = {}


def _import_attr(module: str, attr: str) -> Any:
    return getattr(importlib.import_module(module), attr)


def _build_group(name: str, spec: LazyGroupSpec, owner: TyperGroup) -> Any:
    key = (name, spec)
    built = _build_cache.get(key)
    if built is None:
        sub_typer = _import_attr(spec.module, spec.attr)
        info = TyperInfo(
            sub_typer,
            name=name,
            hidden=spec.hidden,
            rich_help_panel=spec.rich_help_panel,
        )
        built = typer.main.get_group_from_info(
            info,
            pretty_exceptions_short=_PRETTY_EXCEPTIONS_SHORT,
            rich_markup_mode=owner.rich_markup_mode,
            suggest_commands=owner.suggest_commands,
        )
        _build_cache[key] = built
    return built


def _build_command(name: str, spec: LazyCommandSpec, owner: TyperGroup) -> Any:
    key = (name, spec)
    built = _build_cache.get(key)
    if built is None:
        func = _import_attr(spec.module, spec.attr)
        info = CommandInfo(
            name=name,
            callback=func,
            hidden=spec.hidden,
            rich_help_panel=spec.rich_help_panel,
        )
        built = typer.main.get_command_from_info(
            info,
            pretty_exceptions_short=_PRETTY_EXCEPTIONS_SHORT,
            rich_markup_mode=owner.rich_markup_mode,
        )
        _build_cache[key] = built
    return built


class LazyTyperGroup(TyperGroup):
    """``TyperGroup`` that resolves a registered subset of commands lazily.

    Call :meth:`configure` once, right after building the ``typer.Typer``
    instance that uses this class (``typer.Typer(cls=LazyTyperGroup, ...)``),
    with the name -> spec registries. Eager commands/sub-typers registered
    the normal way (``@app.command``, ``app.add_typer``) are unaffected —
    they still live in ``self.commands``.

    ``order`` is the exact name sequence ``grimoire --help`` rendered before
    this class existed (every ``@app.command``/``app.command()`` in file
    order, then every ``add_typer()`` in call order — Typer's own rule for
    populating a group's ``commands`` dict). Sub-command panels are grouped
    by ``rich_help_panel``, and rich renders panels/rows in first-seen order,
    so without ``order`` the panel and row order would depend on which names
    are eager vs. lazy instead of matching the pre-#405 output byte-for-byte.
    """

    _lazy_groups: ClassVar[dict[str, LazyGroupSpec]] = {}
    _lazy_commands: ClassVar[dict[str, LazyCommandSpec]] = {}
    _lazy_order: ClassVar[list[str]] = []

    @classmethod
    def configure(
        cls,
        *,
        groups: dict[str, LazyGroupSpec],
        commands: dict[str, LazyCommandSpec],
        order: list[str] | None = None,
    ) -> None:
        cls._lazy_groups = groups
        cls._lazy_commands = commands
        cls._lazy_order = order or []

    def list_commands(self, ctx: Any) -> list[str]:
        names = set(self.commands) | set(self._lazy_groups) | set(self._lazy_commands)
        ordered = [n for n in self._lazy_order if n in names]
        remaining = [n for n in names if n not in self._lazy_order]
        return ordered + remaining

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        cmd = self.commands.get(cmd_name)
        if cmd is not None:
            return cmd
        group_spec = self._lazy_groups.get(cmd_name)
        if group_spec is not None:
            built = _build_group(cmd_name, group_spec, self)
            self.commands[cmd_name] = built
            return built
        command_spec = self._lazy_commands.get(cmd_name)
        if command_spec is not None:
            built = _build_command(cmd_name, command_spec, self)
            self.commands[cmd_name] = built
            return built
        return None
