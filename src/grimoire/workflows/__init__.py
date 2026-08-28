"""Workflow catalogue — discovery, metadata and team manifests.

A workflow is not CLI wiring: it is an artefact with a kind, a set of agents,
sometimes a team, and a place it resolves from. This package owns that model;
:mod:`grimoire.cli.cmd_workflows` only renders it.
"""

from __future__ import annotations

__all__ = ["WorkflowEntry", "load_team", "load_workflows"]


def __getattr__(name: str) -> object:
    """Resolve the public API lazily — importing the package stays cheap."""
    if name in ("WorkflowEntry", "load_workflows"):
        from grimoire.workflows import registry

        return getattr(registry, name)
    if name == "load_team":
        from grimoire.workflows.teams import load_team

        return load_team
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
