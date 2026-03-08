"""Tests for bmad.core.merge — non-destructive merge engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad.core.exceptions import BmadMergeError
from bmad.core.merge import MergeEngine

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    d = tmp_path / "source"
    d.mkdir()
    (d / "_bmad").mkdir()
    (d / "_bmad" / "config.yaml").write_text("config: true\n")
    (d / "_bmad" / "agents").mkdir()
    (d / "_bmad" / "agents" / "architect.md").write_text("# Architect\n")
    (d / ".github").mkdir()
    (d / ".github" / "copilot-instructions.md").write_text("# Instructions\n")
    (d / "new-file.txt").write_text("new content\n")
    return d


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    d = tmp_path / "target"
    d.mkdir()
    return d


@pytest.fixture()
def engine(source: Path, target: Path) -> MergeEngine:
    return MergeEngine(source, target)


# ── Construction ──────────────────────────────────────────────────────────────


class TestConstruction:
    def test_valid(self, source: Path, target: Path) -> None:
        e = MergeEngine(source, target)
        assert e.source == source
        assert e.target == target

    def test_source_missing(self, tmp_path: Path, target: Path) -> None:
        with pytest.raises(BmadMergeError, match="Source"):
            MergeEngine(tmp_path / "nope", target)

    def test_target_missing(self, source: Path, tmp_path: Path) -> None:
        with pytest.raises(BmadMergeError, match="Target"):
            MergeEngine(source, tmp_path / "nope")


# ── Analysis ──────────────────────────────────────────────────────────────────


class TestAnalyze:
    def test_empty_target_no_conflicts(self, engine: MergeEngine) -> None:
        plan = engine.analyze()
        assert len(plan.conflicts) == 0
        assert len(plan.files_to_create) >= 3  # config + architect + instructions + new-file

    def test_detects_conflicts(self, engine: MergeEngine, target: Path) -> None:
        # Create a conflicting file
        (target / "new-file.txt").write_text("existing\n")
        plan = engine.analyze()
        conflict_paths = [c.path for c in plan.conflicts]
        assert "new-file.txt" in conflict_paths

    def test_sensitive_file_warning(self, engine: MergeEngine, target: Path) -> None:
        # Create the sensitive file
        gh = target / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("existing\n")
        plan = engine.analyze()
        assert len(plan.warnings) >= 1
        assert any("copilot-instructions.md" in w for w in plan.warnings)

    def test_directories_to_create(self, engine: MergeEngine) -> None:
        plan = engine.analyze()
        # _bmad, _bmad/agents, .github should need creation
        assert len(plan.directories_to_create) >= 1

    def test_no_modification(self, engine: MergeEngine, target: Path) -> None:
        """Analyze must not modify the target."""
        before = set(target.rglob("*"))
        engine.analyze()
        after = set(target.rglob("*"))
        assert before == after


# ── Execution ─────────────────────────────────────────────────────────────────


class TestExecute:
    def test_creates_files(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        result = engine.execute(plan)
        assert len(result.files_created) >= 3
        assert (target / "new-file.txt").is_file()
        assert (target / "_bmad" / "config.yaml").is_file()

    def test_creates_directories(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        engine.execute(plan)
        assert (target / "_bmad" / "agents").is_dir()

    def test_skips_conflicts(self, engine: MergeEngine, target: Path) -> None:
        (target / "new-file.txt").write_text("keep me\n")
        plan = engine.analyze()
        result = engine.execute(plan)
        assert "new-file.txt" in result.files_skipped
        assert (target / "new-file.txt").read_text() == "keep me\n"

    def test_force_overwrites(self, engine: MergeEngine, target: Path) -> None:
        (target / "new-file.txt").write_text("old\n")
        plan = engine.analyze()
        result = engine.execute(plan, force=True)
        assert "new-file.txt" in result.files_created
        assert (target / "new-file.txt").read_text() == "new content\n"

    def test_dry_run_no_changes(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        result = engine.execute(plan, dry_run=True)
        assert len(result.files_created) >= 3
        # But nothing actually written
        assert not (target / "new-file.txt").exists()

    def test_merge_log_created(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        result = engine.execute(plan)
        assert result.log_path is not None
        assert result.log_path.is_file()
        log = json.loads(result.log_path.read_text())
        assert "files_created" in log
        assert "timestamp" in log


# ── Rollback ──────────────────────────────────────────────────────────────────


class TestUndo:
    def test_undo_deletes_created_files(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        result = engine.execute(plan)
        assert (target / "new-file.txt").is_file()
        assert result.log_path is not None

        deleted = MergeEngine.undo(result.log_path)
        assert "new-file.txt" in deleted
        assert not (target / "new-file.txt").exists()
        # Log itself is also deleted
        assert not result.log_path.exists()

    def test_undo_missing_log(self, tmp_path: Path) -> None:
        with pytest.raises(BmadMergeError, match="not found"):
            MergeEngine.undo(tmp_path / "nope.json")

    def test_undo_ignores_already_deleted(self, engine: MergeEngine, target: Path) -> None:
        plan = engine.analyze()
        result = engine.execute(plan)
        assert result.log_path is not None
        # Manually delete a file first
        (target / "new-file.txt").unlink()
        deleted = MergeEngine.undo(result.log_path)
        assert "new-file.txt" not in deleted


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_source(self, tmp_path: Path) -> None:
        src = tmp_path / "empty_src"
        tgt = tmp_path / "empty_tgt"
        src.mkdir()
        tgt.mkdir()
        e = MergeEngine(src, tgt)
        plan = e.analyze()
        assert plan.files_to_create == ()
        assert plan.conflicts == ()
        result = e.execute(plan)
        assert result.files_created == ()
        assert result.log_path is None  # nothing to log

    def test_nested_new_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        tgt = tmp_path / "tgt"
        src.mkdir()
        tgt.mkdir()
        deep = src / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "file.txt").write_text("deep\n")
        e = MergeEngine(src, tgt)
        plan = e.analyze()
        e.execute(plan)
        assert (tgt / "a" / "b" / "c" / "file.txt").is_file()
