"""Does the kit's own output hold together, once it is on disk?

``grimoire doctor`` checked that its directories existed. It never checked that
the paths written *inside* the files it had just installed led anywhere, nor
that the agents those files routed to were installed. A project could therefore
pass 20/20 while carrying ninety-nine dead path references and a routing map
whose agents did not exist.

Two checks close that gap, and both read only what is on disk:

``dead_path_references``
    Every ``_grimoire/…`` input path a delivered file names must resolve.

``roster_incoherences``
    Every agent a delivered file routes to must be in the manifest, and every
    agent in the manifest should be reachable from a routing map.

Both are deliberately narrow. They answer "is what we shipped self-consistent",
not "is this project healthy" — a check that flags normal usage gets ignored,
and an ignored check is worse than no check.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Only kit-provided *inputs* are in scope. ``_grimoire-output/`` holds what a
#: run is supposed to produce; naming a file before it exists is the point.
_INPUT_PREFIX = "_grimoire/"

#: Written by an agent on first use rather than read at activation. Naming one
#: is a promise to create it, not a claim that it is already there.
_WRITTEN_ON_FIRST_USE = frozenset({
    "_grimoire/_memory/agent-changelog.md",
    "_grimoire/_memory/knowledge-digest.md",
    "_grimoire/_memory/mcp-audit.jsonl",
    "_grimoire/_memory/agent-learnings/fix-loop-patterns.md",
    "_grimoire/_memory/agent-learnings/meta-review",
    "_grimoire/overrides/documentation-standards.md",
    # A destination the migration creates when it runs, not an input to read.
    "_grimoire/_memory/migration/weaviate-neo4j",
})

_PATH_RE = re.compile(r"_grimoire/[A-Za-z0-9_./-]+")
_AGENT_TAG_RE = re.compile(r'<agent\s+tag="([\w-]+)"')
_READABLE_SUFFIXES = frozenset({".md", ".yaml", ".yml", ".csv", ".json", ".sh", ".py", ".toml"})

#: Directories whose contents are the project's, not the kit's.
_SKIPPED_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", "_grimoire-output"})


@dataclass(frozen=True, slots=True)
class DeadReference:
    """A path a delivered file names, which does not exist in the project."""

    source: str
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line} → {self.target}"


@dataclass(slots=True)
class RosterReport:
    """How a routing map compares with the agents actually installed."""

    routed_but_absent: list[str] = field(default_factory=list)
    installed_but_unrouted: list[str] = field(default_factory=list)
    maps_found: int = 0

    @property
    def coherent(self) -> bool:
        return not self.routed_but_absent


def has_kit_tier(project_root: Path) -> bool:
    """Whether this project was scaffolded by a kit that owns a ``kit/`` tier.

    A hand-made or pre-boundary tree has no ``_grimoire/kit/``: there is nothing
    the kit shipped, so there is nothing to hold to its own promises. Reporting
    drift there would name files the user never asked for — ``grimoire migrate``
    is that project's path forward, not this check.
    """
    return (project_root / "_grimoire" / "kit").is_dir()


def _readable_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(project_root.rglob("*")):
        if any(part in _SKIPPED_DIRS for part in path.relative_to(project_root).parts):
            continue
        if path.is_file() and path.suffix in _READABLE_SUFFIXES:
            files.append(path)
    return files


def dead_path_references(project_root: Path) -> list[DeadReference]:
    """Input paths named by delivered files that do not resolve in the project."""
    dead: list[DeadReference] = []
    for path in _readable_files(project_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _PATH_RE.finditer(line):
                target = match.group(0).rstrip("./,);:`*").removeprefix("./")
                if not target.startswith(_INPUT_PREFIX) or "{" in target or "*" in target:
                    continue
                # ``fer-{id}.yaml`` and ``proposal-*.yaml`` reach the regex as a
                # bare prefix: the run completes the name at write time.
                if target.endswith("-") or line[match.end():match.end() + 1] in {"{", "*"}:
                    continue
                if target in _WRITTEN_ON_FIRST_USE or (project_root / target).exists():
                    continue
                dead.append(DeadReference(
                    source=path.relative_to(project_root).as_posix(),
                    line=lineno,
                    target=target,
                ))
    return dead


def installed_agent_tags(project_root: Path) -> set[str]:
    """Agent tags listed in the project's generated manifest."""
    manifest = project_root / "_grimoire" / "kit" / "agent-manifest.csv"
    if not manifest.is_file():
        return set()
    try:
        with manifest.open(encoding="utf-8", newline="") as handle:
            return {row["name"] for row in csv.DictReader(handle) if row.get("name")}
    except (OSError, csv.Error, KeyError):
        return set()


def roster_incoherences(project_root: Path) -> RosterReport:
    """Compare every routing map in the project against the installed roster."""
    report = RosterReport()
    installed = installed_agent_tags(project_root)
    if not installed:
        return report

    routed: set[str] = set()
    for path in _readable_files(project_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tags = set(_AGENT_TAG_RE.findall(text))
        if tags:
            report.maps_found += 1
            routed |= tags

    if not routed:
        return report
    report.routed_but_absent = sorted(routed - installed)
    report.installed_but_unrouted = sorted(installed - routed)
    return report


def integrity_checks(project_root: Path) -> list[tuple[str, bool, str]]:
    """The two checks as ``(name, passed, detail)``, ready for any reporter.

    Rendering lives here rather than in the CLI: ``cli/app.py`` is under a
    size ratchet that only lets it shrink, and a check's wording belongs with
    the check anyway.
    """
    if not has_kit_tier(project_root):
        return []

    records: list[tuple[str, bool, str]] = []

    dead = dead_path_references(project_root)
    if dead:
        shown = ", ".join(str(ref) for ref in dead[:3])
        more = f" (+{len(dead) - 3})" if len(dead) > 3 else ""
        records.append((
            "paths_resolve", False,
            f"{len(dead)} chemin(s) du kit cité(s) mais absent(s) : {shown}{more}",
        ))
    else:
        records.append(("paths_resolve", True, "tous les chemins du kit cités se résolvent"))

    roster = roster_incoherences(project_root)
    if roster.routed_but_absent:
        records.append((
            "roster_coherent", False,
            f"{len(roster.routed_but_absent)} agent(s) routé(s) mais non installé(s) : "
            + ", ".join(roster.routed_but_absent),
        ))
    elif roster.maps_found:
        records.append((
            "roster_coherent", True,
            f"carte de routage cohérente avec le manifeste ({roster.maps_found} carte(s))",
        ))
    return records
