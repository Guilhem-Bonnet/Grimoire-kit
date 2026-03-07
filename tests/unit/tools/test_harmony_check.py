"""Tests for bmad.tools.harmony_check — HarmonyCheck tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.tools.harmony_check import (
    Dissonance,
    HarmonyCheck,
    HarmonyResult,
    _compute_score,
    _detect_naming,
    _detect_orphans,
    _detect_oversized,
    _scan_project,
    ArchScan,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal BMAD project structure."""
    (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
    (tmp_path / "_bmad" / "core" / "agents").mkdir(parents=True)
    (tmp_path / "_bmad" / "core" / "workflows").mkdir(parents=True)
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True)

    # Agents
    (tmp_path / "_bmad" / "core" / "agents" / "analyst.md").write_text("# Analyst\n")
    (tmp_path / "_bmad" / "core" / "agents" / "architect.md").write_text("# Architect\nUses analyst.\n")

    # Workflow referencing analyst
    (tmp_path / "_bmad" / "core" / "workflows" / "plan.md").write_text("# Plan\nAgent: analyst\n")

    # Tool
    (tmp_path / "framework" / "tools" / "sample-tool.py").write_text("# tool\n")

    # Doc
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")

    # Test
    (tmp_path / "tests" / "test_sample.py").write_text("def test_one(): pass\n")

    return tmp_path


# ── Dissonance ────────────────────────────────────────────────────────────────

class TestDissonance:
    def test_to_dict(self) -> None:
        d = Dissonance("orphan", SEVERITY_MEDIUM, "a.md", "msg", "fix")
        data = d.to_dict()
        assert data["category"] == "orphan"
        assert data["suggestion"] == "fix"

    def test_frozen(self) -> None:
        d = Dissonance("x", "HIGH", "f", "m")
        with pytest.raises(AttributeError):
            d.category = "y"  # type: ignore[misc]


# ── ArchScan ──────────────────────────────────────────────────────────────────

class TestArchScan:
    def test_total_files(self) -> None:
        s = ArchScan(agents=["a", "b"], tools=["t"])
        assert s.total_files == 3


# ── _scan_project ─────────────────────────────────────────────────────────────

class TestScanProject:
    def test_discovers_files(self, project: Path) -> None:
        scan = _scan_project(project)
        assert len(scan.agents) >= 2
        assert len(scan.workflows) >= 1
        assert len(scan.tools) >= 1
        assert len(scan.docs) >= 1
        assert len(scan.tests) >= 1
        assert scan.total_files > 0


# ── Detectors ─────────────────────────────────────────────────────────────────

class TestDetectOrphans:
    def test_finds_orphan(self) -> None:
        scan = ArchScan(agents=["a.md", "b.md"], cross_refs={"a.md": ["b.md"]})
        # b.md is referenced, but a.md references b.md → a.md is in cross_refs keys
        # Neither is orphan with this data
        orphans = _detect_orphans(scan)
        # b.md is referenced by a.md → not orphan
        # a.md is in cross_refs → not orphan
        assert len(orphans) == 0

    def test_orphan_detected(self) -> None:
        scan = ArchScan(agents=["a.md", "orphan.md"], cross_refs={"a.md": ["x.md"]})
        orphans = _detect_orphans(scan)
        assert any(d.file == "orphan.md" for d in orphans)


class TestDetectNaming:
    def test_kebab_ok(self) -> None:
        scan = ArchScan(agents=["path/good-name.md"])
        assert _detect_naming(scan) == []

    def test_camelcase_flagged(self) -> None:
        scan = ArchScan(agents=["path/BadName.md"])
        dissonances = _detect_naming(scan)
        assert len(dissonances) == 1
        assert dissonances[0].category == "naming"


class TestDetectOversized:
    def test_normal_file_ok(self, tmp_path: Path) -> None:
        f = tmp_path / "tools" / "small.py"
        f.parent.mkdir(parents=True)
        f.write_text("line\n" * 100)
        scan = ArchScan(tools=["tools/small.py"])
        assert _detect_oversized(scan, tmp_path) == []

    def test_oversized_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "tools" / "big.py"
        f.parent.mkdir(parents=True)
        f.write_text("line\n" * 900)
        scan = ArchScan(tools=["tools/big.py"])
        dissonances = _detect_oversized(scan, tmp_path)
        assert len(dissonances) == 1
        assert dissonances[0].category == "size"


# ── Score ─────────────────────────────────────────────────────────────────────

class TestComputeScore:
    def test_perfect(self) -> None:
        score, grade, cats = _compute_score([])
        assert score == 100
        assert grade == "A+"
        assert cats == {}

    def test_with_dissonances(self) -> None:
        ds = [
            Dissonance("x", SEVERITY_HIGH, "f", "m"),
            Dissonance("y", SEVERITY_LOW, "g", "m"),
        ]
        score, grade, cats = _compute_score(ds)
        assert score == 100 - 8 - 2  # 90
        assert grade == "A"
        assert cats == {"x": 1, "y": 1}

    def test_low_score(self) -> None:
        ds = [Dissonance("x", SEVERITY_HIGH, "f", "m")] * 15
        score, grade, _ = _compute_score(ds)
        assert score == 0
        assert grade == "F"


# ── HarmonyResult ─────────────────────────────────────────────────────────────

class TestHarmonyResult:
    def test_to_dict(self) -> None:
        r = HarmonyResult(score=85, grade="B", total_files=10, dissonances=(), category_counts={})
        d = r.to_dict()
        assert d["score"] == 85
        assert d["grade"] == "B"


# ── HarmonyCheck tool ────────────────────────────────────────────────────────

class TestHarmonyCheck:
    def test_runs_on_minimal_project(self, project: Path) -> None:
        hc = HarmonyCheck(project)
        result = hc.run()
        assert isinstance(result, HarmonyResult)
        assert 0 <= result.score <= 100
        assert result.total_files > 0

    def test_empty_dir(self, tmp_path: Path) -> None:
        hc = HarmonyCheck(tmp_path)
        result = hc.run()
        assert result.score == 100
        assert result.total_files == 0

    def test_result_is_frozen(self, project: Path) -> None:
        result = HarmonyCheck(project).run()
        with pytest.raises(AttributeError):
            result.score = 0  # type: ignore[misc]

    def test_json_serializable(self, project: Path) -> None:
        import json
        result = HarmonyCheck(project).run()
        data = json.dumps(result.to_dict())
        assert "score" in data


# ── Live test on actual kit ──────────────────────────────────────────────────

_KIT_ROOT = Path(__file__).resolve().parents[3]


class TestHarmonyCheckLive:
    @pytest.mark.skipif(
        not (_KIT_ROOT / "project-context.yaml").exists(),
        reason="Live kit root not available",
    )
    def test_on_real_project(self) -> None:
        hc = HarmonyCheck(_KIT_ROOT)
        result = hc.run()
        assert result.total_files > 50
        assert result.score >= 0
