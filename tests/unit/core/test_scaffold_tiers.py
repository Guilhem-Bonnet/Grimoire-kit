"""Write tiers: what a kit update is allowed to overwrite, and what it isn't.

These are the guarantees that make ``grimoire up`` safe to run as an update
command — the property the pre-boundary layout could not offer, which is why it
never updated anything at all.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.scaffold import (
    TIER_KIT,
    TIER_SEED,
    FileCopy,
    ProjectScaffolder,
    ScaffoldPlan,
    TemplateRender,
    is_build_artifact,
)


def _scaffolder(tmp_path: Path) -> ProjectScaffolder:
    from grimoire.core.archetype_resolver import ResolvedArchetype

    return ProjectScaffolder(
        tmp_path,
        project_name="demo",
        user_name="Tester",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=ResolvedArchetype(
            archetype="minimal", stack_agents=(), feature_agents=(),
            reason="test", archetypes=("minimal",),
        ),
        backend="local",
    )


class TestSeedTier:
    def test_seed_template_is_not_overwritten(self, tmp_path: Path) -> None:
        dst = tmp_path / "decisions-log.md"
        dst.write_text("my decisions", encoding="utf-8")
        plan = ScaffoldPlan(templates=[
            TemplateRender(dst=dst, content="fresh template", label="log", tier=TIER_SEED),
        ])
        result = _scaffolder(tmp_path).execute(plan)
        assert dst.read_text(encoding="utf-8") == "my decisions"
        assert "log" in result.preserved_files

    def test_seed_template_is_created_when_missing(self, tmp_path: Path) -> None:
        dst = tmp_path / "decisions-log.md"
        plan = ScaffoldPlan(templates=[
            TemplateRender(dst=dst, content="fresh", label="log", tier=TIER_SEED),
        ])
        _scaffolder(tmp_path).execute(plan)
        assert dst.read_text(encoding="utf-8") == "fresh"

    def test_seed_copy_is_not_overwritten(self, tmp_path: Path) -> None:
        src = tmp_path / "src.md"
        src.write_text("kit version", encoding="utf-8")
        dst = tmp_path / "out" / "dst.md"
        dst.parent.mkdir()
        dst.write_text("mine", encoding="utf-8")
        plan = ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="f", tier=TIER_SEED)])
        _scaffolder(tmp_path).execute(plan)
        assert dst.read_text(encoding="utf-8") == "mine"


class TestKitTier:
    def test_kit_file_is_overwritten_by_a_new_version(self, tmp_path: Path) -> None:
        src = tmp_path / "src.md"
        src.write_text("version 2", encoding="utf-8")
        dst = tmp_path / "out" / "dst.md"
        dst.parent.mkdir()
        dst.write_text("version 1", encoding="utf-8")
        plan = ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="f", tier=TIER_KIT)])
        result = _scaffolder(tmp_path).execute(plan)
        assert dst.read_text(encoding="utf-8") == "version 2"
        assert "f" in result.copied_files

    def test_identical_content_is_reported_unchanged(self, tmp_path: Path) -> None:
        src = tmp_path / "src.md"
        src.write_text("same", encoding="utf-8")
        dst = tmp_path / "out" / "dst.md"
        dst.parent.mkdir()
        dst.write_text("same", encoding="utf-8")
        plan = ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="f")])
        result = _scaffolder(tmp_path).execute(plan)
        assert result.copied_files == []
        assert "f" in result.unchanged_files

    def test_unchanged_file_is_not_rewritten(self, tmp_path: Path) -> None:
        # mtime must survive: a rewrite would show up as a phantom git diff.
        src = tmp_path / "src.md"
        src.write_text("same", encoding="utf-8")
        dst = tmp_path / "out" / "dst.md"
        dst.parent.mkdir()
        dst.write_text("same", encoding="utf-8")
        before = dst.stat().st_mtime_ns
        _scaffolder(tmp_path).execute(ScaffoldPlan(copies=[FileCopy(src=src, dst=dst)]))
        assert dst.stat().st_mtime_ns == before

    def test_template_rewrite_only_when_content_differs(self, tmp_path: Path) -> None:
        dst = tmp_path / "gen.md"
        dst.write_text("generated", encoding="utf-8")
        before = dst.stat().st_mtime_ns
        result = _scaffolder(tmp_path).execute(ScaffoldPlan(templates=[
            TemplateRender(dst=dst, content="generated", label="g"),
        ]))
        assert dst.stat().st_mtime_ns == before
        assert "g" in result.unchanged_files


class TestDirectoryCopies:
    def test_build_artifacts_are_never_copied(self, tmp_path: Path) -> None:
        src = tmp_path / "tree"
        (src / "__pycache__").mkdir(parents=True)
        (src / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"junk")
        (src / "real.py").write_text("code", encoding="utf-8")
        dst = tmp_path / "out"
        _scaffolder(tmp_path).execute(ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="tree")]))
        assert (dst / "real.py").is_file()
        assert not (dst / "__pycache__").exists()

    def test_second_copy_of_identical_tree_reports_nothing(self, tmp_path: Path) -> None:
        src = tmp_path / "tree"
        src.mkdir()
        (src / "a.py").write_text("code", encoding="utf-8")
        dst = tmp_path / "out"
        scaffolder = _scaffolder(tmp_path)
        scaffolder.execute(ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="tree")]))
        result = scaffolder.execute(ScaffoldPlan(copies=[FileCopy(src=src, dst=dst, label="tree")]))
        assert result.copied_files == []
        assert "tree" in result.unchanged_files


class TestBuildArtifactDetection:
    def test_pyc_and_cache_dirs_are_artifacts(self) -> None:
        assert is_build_artifact(Path("mod.pyc"))
        assert is_build_artifact(Path("__pycache__/mod.cpython-312.pyc"))
        assert is_build_artifact(Path("pkg/.ruff_cache/x"))

    def test_source_files_are_not(self) -> None:
        assert not is_build_artifact(Path("agents/dev.md"))
        assert not is_build_artifact(Path("memory/backends/backend_local.py"))
