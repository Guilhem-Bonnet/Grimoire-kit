"""Rendering standard artifacts, and tracking which ones the kit generated.

Not to be confused with :mod:`grimoire.core.standard_profile_manifest`, which
reads the *profile* manifest — the artifact set a project declared. This module
answers a different question: of the files on disk, which ones did the kit
write, and are they still as it wrote them?

Which standard artifacts the kit wrote, and which ones the project owns.

``_grimoire/standard/`` holds two kinds of file that must not share a fate:
policies the kit ships (rule packs, mission brief) and records the project
produces (waivers, compliance scores, task boards). Updating the first is the
whole point of an update; overwriting the second destroys work.

Telling them apart needs a fact neither the file nor the template carries: a
file differing from today's template may have been edited by the project, or
may simply come from an older kit. The generation manifest supplies it — every
artifact the kit writes is recorded with its digest, so an untouched file can
be recognised later and refreshed, and anything else is left alone.

A file absent from the manifest is treated as the project's. That is the safe
direction: keeping one stale policy costs far less than erasing a waiver.
It also hosts the artifact renderer, so that "what the kit would generate" and
"is this still what the kit generated" are decided by the same module.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol


class RenderableProfile(Protocol):
    """What the renderer needs of a profile, and nothing more.

    Declared here rather than imported from :mod:`grimoire.core.agentic_standard`
    so the dependency stays one-way: that module imports this one at runtime,
    and this one never reaches back. A ``TYPE_CHECKING`` import would read as a
    cycle to any tool that does not model the guard — and it would also hide
    how little of the profile is actually used.
    """

    @property
    def id(self) -> str:
        """Identifier of the declared profile, e.g. ``governed``."""

#: Root of the standard artifacts, relative to the project root.
STANDARD_DIR = Path("_grimoire/standard")

#: Where the manifest lives, relative to the project root.
STANDARD_GENERATION_MANIFEST = STANDARD_DIR / ".generated.json"

STANDARD_PROFILE_FILE = STANDARD_DIR / "standard-profile.yaml"

#: Where the governed standard writes what a task produced. Plain paths, kept
#: with the rest of the vocabulary so that naming an artifact never requires
#: importing the engine that fills it.
EVIDENCE_DIR = Path("_grimoire-output/evidence")
CONTEXT_DIR = Path("_grimoire-output/context")
DECISION_DIR = Path("_grimoire-output/decisions")
SCORE_DIR = Path("_grimoire-output/standard")

#: A task id becomes a directory name in generated paths, so it is validated
#: before it is ever joined. Kept here, with the paths it guards, so that
#: reading "which task, which profile" costs nothing: the lifecycle hooks ask
#: that on every tool call, and importing the standard engine for it cost 48 ms
#: a time.
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def normalize_task_id(task_id: str) -> str:
    """Validate task ids before using them in generated paths."""
    normalized = str(task_id).strip()
    if not TASK_ID_PATTERN.fullmatch(normalized) or normalized in {".", ".."}:
        msg = (
            f"Invalid task_id {task_id!r}. Use 1-128 letters, numbers, dots, underscores, "
            "or hyphens, starting with a letter or number."
        )
        raise ValueError(msg)
    return normalized


#: Fields stamped at generation time. They change on every run (or every day)
#: without the artifact meaning anything different, so they are neutralised
#: before comparing — otherwise every ``up`` would rewrite the file and leave a
#: permanent diff in the project's git history.
_STAMP_LINES = (
    re.compile(r"^(- Date:).*$", re.MULTILINE),
    re.compile(r"^(\s*generated_at:).*$", re.MULTILINE),
)


def _strip_stamps(text: str) -> str:
    for pattern in _STAMP_LINES:
        text = pattern.sub(r"\1", text)
    return text


def _same_rendered(existing: str, rendered: str) -> bool:
    """Compare a generated artifact with a fresh render, ignoring stamps."""
    return _strip_stamps(existing) == _strip_stamps(rendered)


def _artifact_digest(path: Path) -> str:
    """SHA-256 of *path*, or ``""`` when unreadable."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _read_text_or_none(path: Path) -> str | None:
    """File contents, or ``None`` when it does not exist or cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_generation_manifest(root: Path) -> dict[str, str]:
    """``destination -> digest`` recorded the last time the kit generated them."""
    path = root / STANDARD_GENERATION_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("artifacts")
    return {str(k): str(v) for k, v in entries.items()} if isinstance(entries, dict) else {}


def save_generation_manifest(root: Path, entries: dict[str, str]) -> None:
    """Persist the generation manifest, merging over what is already recorded."""
    merged = load_generation_manifest(root)
    merged.update({str(k): v for k, v in entries.items()})
    path = root / STANDARD_GENERATION_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "grimoire-standard-generation/v1",
                    "artifacts": dict(sorted(merged.items()))}, indent=1) + "\n",
        encoding="utf-8",
    )


def is_project_owned(root: Path, destination: str | Path, manifest: dict[str, str] | None = None) -> bool:
    """True when *destination* was edited (or created) by the project.

    An artifact absent from the manifest predates it and is treated as the
    project's — the safe side, since the cost of freezing one stale policy is
    far below the cost of erasing a waiver.
    """
    entries = manifest if manifest is not None else load_generation_manifest(root)
    recorded = entries.get(str(destination))
    if not recorded:
        return True
    return _artifact_digest(root / destination) != recorded


def digest(path: Path) -> str:
    """Public alias of the digest helper."""
    return _artifact_digest(path)


def decide(
    root: Path,
    destination: str | Path,
    content: str,
    *,
    force: bool,
    refresh: bool,
    manifest: dict[str, str] | None = None,
) -> str:
    """Whether to write *destination*: ``write``, ``adopt`` or ``keep``.

    ``adopt`` means the file on disk already matches what the kit would
    generate: nothing to write, but worth recording so a project predating the
    manifest becomes refreshable from now on. Without that case, an existing
    project could never be caught up.
    """
    target = root / destination
    existing = _read_text_or_none(target)
    if existing is None:
        return "write"
    if _same_rendered(existing, content):
        return "adopt"
    if force:
        return "write"
    if refresh and not is_project_owned(root, destination, manifest):
        return "write"
    return "keep"


def _single_line_value(value: str) -> str:
    normalized = " ".join(str(value).split()).strip()
    return normalized or "Unnamed project"


def _yaml_double_quoted_value(value: str) -> str:
    return _single_line_value(value).replace("\\", "\\\\").replace('"', '\\"')


def _render_template(
    template: str,
    *,
    project_name: str,
    profile: RenderableProfile,
    generated_at: str,
) -> str:
    text_project_name = _single_line_value(project_name)
    yaml_project_name = _yaml_double_quoted_value(project_name)
    rendered = template
    rendered = rendered.replace("- Project:\n", f"- Project: {text_project_name}\n")
    rendered = rendered.replace("- Selected profile: `starter | controlled | orchestrated | governed | production`\n", f"- Selected profile: `{profile.id}`\n")
    rendered = rendered.replace("- Declared profile: `starter | controlled | orchestrated | governed | production`\n", f"- Declared profile: `{profile.id}`\n")
    rendered = rendered.replace("- Upstream standard reference:\n", "- Upstream standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Standard reference:\n", "- Standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Date:\n", f"- Date: {generated_at}\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{yaml_project_name}\"\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{yaml_project_name}\"\n")
    return rendered
