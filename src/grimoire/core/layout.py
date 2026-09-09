"""The boundary between what the kit generates and what the project owns.

Every file in a Grimoire project belongs to exactly one of three tiers, and the
tier decides who may write it:

``kit``
    Lives under ``_grimoire/kit/``. Regenerated in full on every ``grimoire
    up``, overwritten without ceremony. Never edit it — edits are lost on the
    next update, which is the point: this is how a project keeps receiving kit
    improvements instead of freezing at its install version.

``overrides``
    Lives under ``_grimoire/overrides/``, mirroring the kit tree. The only
    editable layer that shadows kit files. An override wins over its kit
    counterpart at resolution time and survives every update by construction.

``seed`` / data
    Everything else — ``project-context.yaml``, the memory store, run outputs.
    Written once when missing, never rewritten.

Reading a kit-provided artifact therefore means asking *this* module, not
hardcoding a path: ``resolve()`` walks overrides → kit → legacy locations, so a
project migrated from the pre-boundary layout keeps working until ``grimoire
migrate`` moves its files.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Tier roots (relative to the project root) ────────────────────────────────

GRIMOIRE_DIR = "_grimoire"
KIT_DIR = f"{GRIMOIRE_DIR}/kit"
OVERRIDES_DIR = f"{GRIMOIRE_DIR}/overrides"

#: Kit sub-trees, named once so scaffolder and readers cannot drift apart.
AGENTS_SUBDIR = "agents"
WORKFLOWS_SUBDIR = "workflows"
TEAMS_SUBDIR = "teams"
PROMPT_TEMPLATES_SUBDIR = "prompt-templates"
FRAMEWORK_SUBDIR = "framework"
MEMORY_CODE_SUBDIR = "memory"
TOOLS_SUBDIR = "tools"

#: Pre-boundary locations, still read so an unmigrated project keeps working.
#: Order matters: most recent layout first. Kept until ``grimoire migrate`` has
#: had a full release cycle to move projects over.
LEGACY_KIT_ROOTS: tuple[str, ...] = (
    f"{GRIMOIRE_DIR}/_config/custom",
    f"{GRIMOIRE_DIR}/_config",
)

#: Legacy agent directories, in resolution order (see :func:`agent_dirs`).
LEGACY_AGENT_DIRS: tuple[str, ...] = (
    f"{GRIMOIRE_DIR}/_config/custom/agents",
    f"{GRIMOIRE_DIR}/_config/agents",
    f"{GRIMOIRE_DIR}/agents",
)


def kit_dir(project_root: Path) -> Path:
    """Absolute path of the kit tier for *project_root*."""
    return project_root / KIT_DIR


def overrides_dir(project_root: Path) -> Path:
    """Absolute path of the overrides tier for *project_root*."""
    return project_root / OVERRIDES_DIR


def search_roots(project_root: Path, *, include_legacy: bool = True) -> tuple[Path, ...]:
    """Roots to search for a kit-provided artifact, highest priority first."""
    roots = [overrides_dir(project_root), kit_dir(project_root)]
    if include_legacy:
        roots.extend(project_root / legacy for legacy in LEGACY_KIT_ROOTS)
    return tuple(roots)


def resolve(project_root: Path, relative: str | Path, *, include_legacy: bool = True) -> Path | None:
    """First existing path for *relative* across the tiers, or ``None``.

    *relative* is expressed against a tier root — ``"agents/dev.md"``, not
    ``"_grimoire/kit/agents/dev.md"`` — so the caller never encodes which tier
    a file happens to live in today.
    """
    for root in search_roots(project_root, include_legacy=include_legacy):
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def agent_dirs(project_root: Path, *, include_legacy: bool = True) -> tuple[Path, ...]:
    """Directories holding agent definitions, highest priority first.

    Legacy directories come last and are deduplicated against the tier roots,
    so a project mid-migration never reports the same agent twice.
    """
    dirs = [
        overrides_dir(project_root) / AGENTS_SUBDIR,
        kit_dir(project_root) / AGENTS_SUBDIR,
    ]
    if include_legacy:
        dirs.extend(project_root / legacy for legacy in LEGACY_AGENT_DIRS)
    seen: set[Path] = set()
    ordered: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return tuple(ordered)


def layered_files(
    project_root: Path,
    subdir: str,
    *,
    suffix: str = ".md",
    include_legacy: bool = True,
) -> dict[str, Path]:
    """Map ``stem -> path`` for *subdir* across the tiers, overrides winning.

    A file present in both tiers appears once, resolved to the override. This
    is what makes "customise one agent" possible without forking the other
    thirty.
    """
    found: dict[str, Path] = {}
    for root in search_roots(project_root, include_legacy=include_legacy):
        directory = root / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix == suffix:
                found.setdefault(path.stem, path)
    return found


_AGENT_TAG_RE = re.compile(r"^name:\s*\"?([\w-]+)\"?", re.MULTILINE)
_AGENT_PERSONA_RE = re.compile(r'<agent[^>]*\bname="([^"]+)"')


def agent_identity(path: Path) -> tuple[str, str] | None:
    """Return ``(tag, persona name)`` read from an agent file, or ``None``.

    The one parse both readers of "who is this agent" must share: the doctor
    deciding whether an installed agent exists, and the routing map deciding
    whether the entry-point persona can reach it. Reading either from a
    different source than the other is how an override agent went invisible
    to one while being routable by the other (issue #345).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    tag = _AGENT_TAG_RE.search(text)
    if not tag:
        return None
    persona = _AGENT_PERSONA_RE.search(text)
    return tag.group(1), (persona.group(1) if persona else tag.group(1))


def installed_agents(project_root: Path, *, include_legacy: bool = True) -> dict[str, tuple[str, Path]]:
    """Map ``tag -> (persona name, path)`` for every agent actually on disk.

    Reads :func:`agent_dirs`, the same tier order the rest of the kit resolves
    against — overrides shadow the kit tier, never the other way round, and an
    agent present in both counts once. Neither a manifest nor a scaffolder's
    in-flight copy plan is a *fact about this install*; the files on disk are.
    """
    found: dict[str, tuple[str, Path]] = {}
    for directory in agent_dirs(project_root, include_legacy=include_legacy):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not (path.is_file() and path.suffix == ".md"):
                continue
            identity = agent_identity(path)
            if identity is None:
                continue
            tag, persona = identity
            found.setdefault(tag, (persona, path))
    return found


def is_kit_owned(project_root: Path, path: Path) -> bool:
    """True when *path* sits in the kit tier and will be overwritten on update."""
    try:
        path.resolve().relative_to(kit_dir(project_root).resolve())
    except ValueError:
        return False
    return True
