"""The kit/overrides boundary: who wins when the same file exists twice."""

from __future__ import annotations

from pathlib import Path

from grimoire.core import layout


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestResolution:
    def test_override_wins_over_kit(self, tmp_path: Path) -> None:
        _write(layout.kit_dir(tmp_path) / "agents" / "dev.md", "kit")
        _write(layout.overrides_dir(tmp_path) / "agents" / "dev.md", "mine")
        resolved = layout.resolve(tmp_path, "agents/dev.md")
        assert resolved is not None
        assert resolved.read_text(encoding="utf-8") == "mine"

    def test_kit_used_when_no_override(self, tmp_path: Path) -> None:
        _write(layout.kit_dir(tmp_path) / "agents" / "dev.md", "kit")
        resolved = layout.resolve(tmp_path, "agents/dev.md")
        assert resolved is not None
        assert resolved.read_text(encoding="utf-8") == "kit"

    def test_legacy_still_found_before_migration(self, tmp_path: Path) -> None:
        # An unmigrated project must keep working until `grimoire migrate` runs.
        _write(tmp_path / "_grimoire" / "_config" / "custom" / "agents" / "dev.md", "legacy")
        resolved = layout.resolve(tmp_path, "agents/dev.md")
        assert resolved is not None
        assert resolved.read_text(encoding="utf-8") == "legacy"

    def test_missing_file_resolves_to_none(self, tmp_path: Path) -> None:
        assert layout.resolve(tmp_path, "agents/nobody.md") is None


class TestLayeredFiles:
    def test_same_stem_appears_once_resolved_to_override(self, tmp_path: Path) -> None:
        _write(layout.kit_dir(tmp_path) / "agents" / "dev.md", "kit")
        _write(layout.overrides_dir(tmp_path) / "agents" / "dev.md", "mine")
        found = layout.layered_files(tmp_path, "agents")
        assert list(found) == ["dev"]
        assert found["dev"].read_text(encoding="utf-8") == "mine"

    def test_union_of_both_tiers(self, tmp_path: Path) -> None:
        _write(layout.kit_dir(tmp_path) / "agents" / "dev.md")
        _write(layout.kit_dir(tmp_path) / "agents" / "qa.md")
        _write(layout.overrides_dir(tmp_path) / "agents" / "mine.md")
        assert sorted(layout.layered_files(tmp_path, "agents")) == ["dev", "mine", "qa"]

    def test_suffix_filter_excludes_other_files(self, tmp_path: Path) -> None:
        _write(layout.kit_dir(tmp_path) / "agents" / "dev.md")
        _write(layout.kit_dir(tmp_path) / "agents" / "notes.txt")
        assert sorted(layout.layered_files(tmp_path, "agents")) == ["dev"]


class TestAgentDirs:
    def test_priority_order_puts_overrides_first(self, tmp_path: Path) -> None:
        dirs = layout.agent_dirs(tmp_path)
        assert dirs[0] == layout.overrides_dir(tmp_path) / "agents"
        assert dirs[1] == layout.kit_dir(tmp_path) / "agents"

    def test_no_duplicate_directories(self, tmp_path: Path) -> None:
        dirs = layout.agent_dirs(tmp_path)
        assert len(dirs) == len(set(dirs))


class TestOwnership:
    def test_kit_file_is_kit_owned(self, tmp_path: Path) -> None:
        p = _write(layout.kit_dir(tmp_path) / "agents" / "dev.md")
        assert layout.is_kit_owned(tmp_path, p)

    def test_override_is_not_kit_owned(self, tmp_path: Path) -> None:
        p = _write(layout.overrides_dir(tmp_path) / "agents" / "dev.md")
        assert not layout.is_kit_owned(tmp_path, p)

    def test_project_data_is_not_kit_owned(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "_grimoire" / "_memory" / "decisions-log.md")
        assert not layout.is_kit_owned(tmp_path, p)
