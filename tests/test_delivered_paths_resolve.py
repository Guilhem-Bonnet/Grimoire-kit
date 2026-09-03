"""Every ``_grimoire/…`` path the kit writes into a project must resolve there.

The kit moved its files into tiers (``_grimoire/kit/``, ``_grimoire/overrides/``)
and migrated the *code*. The shipped *content* kept naming the pre-boundary
locations, so a fresh install carried dozens of instructions pointing at files
that were never created — invisible to ``grimoire doctor``, which only checks
that its own directories exist.

This test reads what a real scaffold writes and fails on any input path that
does not resolve. It is the guard that defect class needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder

#: Files an agent *writes* on first use rather than reads at activation. Naming
#: one is a promise to create it, not a promise that it already exists.
WRITTEN_ON_FIRST_USE = frozenset({
    "_grimoire/_memory/agent-changelog.md",
    "_grimoire/_memory/knowledge-digest.md",
    "_grimoire/_memory/agent-learnings/fix-loop-patterns.md",
    "_grimoire/_memory/agent-learnings/meta-review",
    "_grimoire/overrides/documentation-standards.md",
    "_grimoire/_memory/mcp-audit.jsonl",
})

#: Only kit-provided inputs are in scope. ``_grimoire-output/`` holds artifacts a
#: run is meant to produce; naming one before it exists is the point.
INPUT_PREFIX = "_grimoire/"

PATH_RE = re.compile(r"_grimoire/[A-Za-z0-9_./-]+")
READABLE = {".md", ".yaml", ".yml", ".csv", ".json", ".sh", ".py", ".toml"}


def _install(target: Path, archetypes: tuple[str, ...]) -> None:
    resolved = ResolvedArchetype(
        archetype=archetypes[0],
        stack_agents=(),
        feature_agents=(),
        reason="test",
        archetypes=archetypes,
    )
    scaffolder = ProjectScaffolder(
        target,
        project_name="paths-test",
        user_name="Test User",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=resolved,
        backend="local",
    )
    scaffolder.execute(scaffolder.plan())


def _dead_references(root: Path) -> list[str]:
    dead: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in READABLE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in PATH_RE.finditer(line):
                raw = match.group(0).rstrip("./,);:`*").removeprefix("./")
                if not raw.startswith(INPUT_PREFIX) or "{" in raw or "*" in raw:
                    continue
                # A name the run completes at write time — ``fer-{id}.yaml``,
                # ``proposal-*.yaml`` — reaches the regex as a bare prefix.
                if raw.endswith("-") or line[match.end():match.end() + 1] in {"{", "*"}:
                    continue
                if raw in WRITTEN_ON_FIRST_USE or (root / raw).exists():
                    continue
                rel = path.relative_to(root)
                dead.append(f"{rel}:{lineno} -> {raw}")
    return dead


@pytest.mark.parametrize(
    "archetypes",
    [("minimal",), ("infra-ops",), ("platform-engineering", "infra-ops", "fix-loop")],
    ids=["minimal", "infra-ops", "composite"],
)
def test_no_shipped_path_is_dead_on_arrival(tmp_path: Path, archetypes: tuple[str, ...]) -> None:
    _install(tmp_path, archetypes)
    dead = _dead_references(tmp_path)
    assert not dead, "chemins du kit cités mais absents à l'installation :\n  " + "\n  ".join(dead)


def test_the_guard_actually_catches_a_dead_path(tmp_path: Path) -> None:
    """A guard that cannot fail proves nothing — make it fail on purpose."""
    _install(tmp_path, ("minimal",))
    assert not _dead_references(tmp_path)

    planted = tmp_path / "_grimoire" / "kit" / "agents" / "concierge.md"
    planted.write_text(
        planted.read_text(encoding="utf-8") + "\nCharger `_grimoire/kit/nowhere.md`.\n",
        encoding="utf-8",
    )
    dead = _dead_references(tmp_path)
    assert any("_grimoire/kit/nowhere.md" in entry for entry in dead)


def test_agent_base_protocol_references_are_installed(tmp_path: Path) -> None:
    """The socle tells every agent to load its siblings — they must be there."""
    _install(tmp_path, ("minimal",))
    framework = tmp_path / "_grimoire" / "kit" / "framework"
    base = (framework / "agent-base.md").read_text(encoding="utf-8")

    referenced = {
        Path(match).name
        for match in re.findall(r"_grimoire/kit/framework/([a-z0-9-]+\.md)", base)
    }
    assert referenced, "agent-base.md ne renvoie plus vers aucun protocole"
    missing = sorted(name for name in referenced if not (framework / name).is_file())
    assert not missing, f"protocoles cités par le socle et non livrés : {missing}"
