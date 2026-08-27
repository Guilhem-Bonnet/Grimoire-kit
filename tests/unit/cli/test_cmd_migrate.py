"""The one-shot unfork: what moves to overrides, what gets regenerated.

The migration has exactly one way to be harmful — deciding a user's file was
kit content and deleting it. Every classification test below exists to pin that
decision down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from grimoire.cli import cmd_migrate
from grimoire.core import layout

LEGACY_CUSTOM = "_grimoire/_config/custom"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "demo"\n  type: "generic"\nagents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    return tmp_path


def _legacy(project: Path, relative: str, content: str) -> Path:
    p = project / LEGACY_CUSTOM / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _as_kit_content(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Make the catalog recognise exactly *paths* as shipped kit content."""
    from grimoire.core import kit_hashes

    known = {kit_hashes.digest_of(p): {"version": "3.0.0", "path": p.name} for p in paths}
    monkeypatch.setattr(cmd_migrate.kit_hashes, "shipped_by_kit",
                        lambda path: known.get(kit_hashes.digest_of(path)))


class TestClassification:
    def test_untouched_kit_file_is_regenerated(self, project: Path, monkeypatch) -> None:
        f = _legacy(project, "agents/dev.md", "shipped by the kit")
        _as_kit_content(monkeypatch, f)
        plan = cmd_migrate.plan_migration(project)
        assert [v.relative for v in plan.regenerate] == [f"{LEGACY_CUSTOM}/agents/dev.md"]
        assert plan.overrides == []

    def test_user_written_file_becomes_an_override(self, project: Path, monkeypatch) -> None:
        _legacy(project, "agents/mine.md", "my own agent")
        _as_kit_content(monkeypatch)  # catalog recognises nothing
        plan = cmd_migrate.plan_migration(project)
        assert plan.regenerate == []
        assert plan.overrides[0].destination == f"{layout.OVERRIDES_DIR}/agents/mine.md"

    def test_unrecognised_content_is_never_deleted(self, project: Path, monkeypatch) -> None:
        # The failure that matters: an empty//missing catalog must not turn
        # every customisation into a deletion.
        _legacy(project, "agents/mine.md", "my own agent")
        monkeypatch.setattr(cmd_migrate.kit_hashes, "load_catalog", dict)
        monkeypatch.setattr(cmd_migrate.kit_hashes, "shipped_by_kit", lambda path: None)
        plan = cmd_migrate.plan_migration(project)
        assert plan.regenerate == []
        assert len(plan.overrides) == 1

    def test_derived_files_are_regenerated_not_preserved(self, project: Path, monkeypatch) -> None:
        manifest = project / "_grimoire" / "_config" / "agent-manifest.csv"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("name,file\n", encoding="utf-8")
        _as_kit_content(monkeypatch)
        plan = cmd_migrate.plan_migration(project)
        assert [v.relative for v in plan.regenerate] == ["_grimoire/_config/agent-manifest.csv"]

    def test_build_artifacts_are_ignored(self, project: Path, monkeypatch) -> None:
        _legacy(project, "workflows/__pycache__/x.cpython-312.pyc", "junk")
        _as_kit_content(monkeypatch)
        plan = cmd_migrate.plan_migration(project)
        assert plan.verdicts == []
        assert plan.already_migrated

    def test_routes_map_each_legacy_subtree(self, project: Path, monkeypatch) -> None:
        _legacy(project, "agents/a.md", "a")
        _legacy(project, "workflows/w.md", "w")
        _legacy(project, "prompt-templates/p.md", "p")
        _legacy(project, "agent-base.md", "b")
        _as_kit_content(monkeypatch)
        destinations = {v.relative.split("/")[-1]: v.destination
                        for v in cmd_migrate.plan_migration(project).overrides}
        assert destinations["a.md"] == f"{layout.OVERRIDES_DIR}/agents/a.md"
        assert destinations["w.md"] == f"{layout.OVERRIDES_DIR}/workflows/w.md"
        assert destinations["p.md"] == f"{layout.OVERRIDES_DIR}/prompt-templates/p.md"
        assert destinations["agent-base.md"] == f"{layout.OVERRIDES_DIR}/framework/agent-base.md"

    def test_already_migrated_project_plans_nothing(self, project: Path) -> None:
        assert cmd_migrate.plan_migration(project).already_migrated


class TestApplyAndRestore:
    def test_apply_moves_customisation_and_drops_kit_content(
        self, project: Path, monkeypatch,
    ) -> None:
        kit_file = _legacy(project, "agents/dev.md", "shipped by the kit")
        mine = _legacy(project, "agents/mine.md", "my own agent")
        _as_kit_content(monkeypatch, kit_file)
        monkeypatch.setattr(cmd_migrate, "refresh_kit_tier", lambda target: None, raising=False)
        monkeypatch.setattr("grimoire.cli.cmd_up.refresh_kit_tier",
                            lambda target: _FakeResult())

        cmd_migrate.apply_migration(cmd_migrate.plan_migration(project), "STAMP")

        assert not kit_file.exists()
        assert not mine.exists()
        moved = project / layout.OVERRIDES_DIR / "agents" / "mine.md"
        assert moved.read_text(encoding="utf-8") == "my own agent"

    def test_snapshot_restores_the_exact_previous_state(
        self, project: Path, monkeypatch,
    ) -> None:
        kit_file = _legacy(project, "agents/dev.md", "shipped by the kit")
        mine = _legacy(project, "agents/mine.md", "my own agent")
        _as_kit_content(monkeypatch, kit_file)
        monkeypatch.setattr("grimoire.cli.cmd_up.refresh_kit_tier", lambda target: _FakeResult())

        cmd_migrate.apply_migration(cmd_migrate.plan_migration(project), "STAMP")
        restored = cmd_migrate.restore_migration(project, "STAMP")

        assert len(restored) == 2
        assert kit_file.read_text(encoding="utf-8") == "shipped by the kit"
        assert mine.read_text(encoding="utf-8") == "my own agent"
        assert not (project / layout.OVERRIDES_DIR / "agents" / "mine.md").exists()

    def test_restore_without_snapshot_raises(self, project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            cmd_migrate.restore_migration(project, "NOPE")

    def test_snapshot_manifest_records_every_touched_file(
        self, project: Path, monkeypatch,
    ) -> None:
        _legacy(project, "agents/mine.md", "my own agent")
        _as_kit_content(monkeypatch)
        monkeypatch.setattr("grimoire.cli.cmd_up.refresh_kit_tier", lambda target: _FakeResult())
        snapshot, _ = cmd_migrate.apply_migration(cmd_migrate.plan_migration(project), "STAMP")
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        assert [e["relative"] for e in manifest["entries"]] == [f"{LEGACY_CUSTOM}/agents/mine.md"]


@dataclass
class _FakeResult:
    """Stand-in for ScaffoldResult: migration tests do not exercise the rebuild."""

    copied_files: list[str] = field(default_factory=list)
    rendered_files: list[str] = field(default_factory=list)


class TestShadowDetection:
    """Only a file the kit writes at the *same path* is shadowed.

    Matching on the base name alone marked ``agents/_archived/concierge.md`` —
    an agent the project had archived — as shadowing the kit's
    ``agents/concierge.md``. ``--adopt-kit`` then deleted it, and nothing
    regenerated it at that path: a silent loss of the project's own archive.
    """

    def test_archived_copy_does_not_shadow_the_kit_agent(self) -> None:
        kit = frozenset({"agents/concierge.md", "framework/agent-base.md"})
        archived = f"{layout.OVERRIDES_DIR}/agents/_archived/concierge.md"
        assert not cmd_migrate._shadowed(archived, kit)

    def test_same_path_shadows(self) -> None:
        kit = frozenset({"agents/concierge.md"})
        assert cmd_migrate._shadowed(f"{layout.OVERRIDES_DIR}/agents/concierge.md", kit)

    def test_unknown_file_does_not_shadow(self) -> None:
        kit = frozenset({"agents/concierge.md"})
        assert not cmd_migrate._shadowed(f"{layout.OVERRIDES_DIR}/agents/mine.md", kit)

    def test_base_name_fallback_still_matches(self) -> None:
        # When the plan cannot be built, the coarse set holds base names.
        kit = frozenset({"concierge.md"})
        assert cmd_migrate._shadowed(f"{layout.OVERRIDES_DIR}/agents/concierge.md", kit)
