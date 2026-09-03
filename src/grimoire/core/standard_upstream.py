"""Where the bridge stands relative to the normative corpus it traces.

``profile-map.yaml`` names the upstream standard and pins the revision it was
reconciled against. This module answers one question: has the standard moved
since? It never fetches more than a ref listing, and treats an unreachable
remote as "unverified", not as "fine".
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grimoire.core.agentic_standard import load_profile_map

LsRemote = Callable[[str, str], str | None]


@dataclass(frozen=True, slots=True)
class UpstreamStatus:
    """The pin, the remote head, and what their comparison means."""

    repository: str
    remote: str
    pinned_commit: str
    pinned_on: str
    branch: str
    remote_head: str | None
    state: str  # pinned | ahead | unreachable | unpinned

    @property
    def exit_code(self) -> int:
        return {"pinned": 0, "unpinned": 1, "ahead": 2, "unreachable": 3}[self.state]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "remote": self.remote,
            "pinned_commit": self.pinned_commit,
            "pinned_on": self.pinned_on,
            "branch": self.branch,
            "remote_head": self.remote_head,
            "state": self.state,
        }


def git_ls_remote(remote: str, branch: str, *, timeout: float = 15.0) -> str | None:
    """SHA of *branch* on *remote*, or ``None`` when the remote cannot be reached."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", remote, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def upstream_status(*, ls_remote: LsRemote | None = None, branch: str = "main") -> UpstreamStatus:
    # Resolved at call time so a test (or a host) can substitute the lookup.
    if ls_remote is None:
        ls_remote = git_ls_remote
    meta = load_profile_map().get("metadata", {}).get("upstream_standard", {}) or {}
    repository = str(meta.get("repository", ""))
    remote = str(meta.get("remote", ""))
    pinned = str(meta.get("commit", "")).strip()
    pinned_on = str(meta.get("pinned_on", ""))
    if not pinned or not remote:
        return UpstreamStatus(repository, remote, pinned, pinned_on, branch, None, "unpinned")
    head = ls_remote(remote, branch)
    if head is None:
        return UpstreamStatus(repository, remote, pinned, pinned_on, branch, None, "unreachable")
    state = "pinned" if head == pinned else "ahead"
    return UpstreamStatus(repository, remote, pinned, pinned_on, branch, head, state)
