"""Render every host surface, and say so when it cannot.

Three commands regenerated the surfaces (agents, skills, commands, hooks)
behind their own copy of ``except Exception: return []``, each justified by
"``host status`` reports the drift". A project whose surfaces failed to render
therefore left ``init`` with a green report and no hook, and the user only
learned it by going to look. One writer, one outcome, one explicit failure.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.hosts import collect
from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts

__all__ = ["HostSyncOutcome", "sync_host_surfaces"]


@dataclass(frozen=True, slots=True)
class HostSyncOutcome:
    """What the sync wrote — or why it wrote nothing."""

    written: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def warning(self) -> str:
        """The one line a caller prints when the sync failed; empty otherwise."""
        if self.ok:
            return ""
        return f"surfaces hôtes non régénérées ({self.error}) — relance `grimoire host sync` après correction"

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "written": list(self.written), "error": self.error}


def sync_host_surfaces(project_root: Path) -> HostSyncOutcome:
    """Render the project onto every known host. Never raises, never hides.

    An emitter problem must not fail an otherwise successful install or
    repair, so the failure comes back as data — and is written once to
    stderr, so that a caller in JSON mode cannot swallow it either.
    """
    out: list[str] = []
    try:
        surface = collect.build_surface(project_root)
        for host_id in supported_hosts():
            emitter = emitter_for(host_id)
            if emitter is None:  # pragma: no cover - registry is complete
                continue
            out.extend(apply_plan(emitter.plan(surface, project_root), project_root).written)
    except Exception as exc:
        outcome = HostSyncOutcome(tuple(out), f"{type(exc).__name__}: {exc}")
        print(f"[grimoire] {outcome.warning}", file=sys.stderr)
        return outcome
    return HostSyncOutcome(tuple(out))
