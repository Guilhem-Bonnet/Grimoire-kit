"""Tests for bmad.tools.harmony_check — HarmonyCheck tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.tools.harmony_check import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    ArchScan,
    Dissonance,
    HarmonyCheck,
    HarmonyResult,
    _cli,
    _compute_score,
    _detect_broken_refs,
    _detect_duplication,
    _detect_manifest_mismatch,
    _detect_naming,
    _detect_orphans,
    _detect_oversized,
    _scan_project,
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

    def test_missing_file_ignored(self, tmp_path: Path) -> None:
        scan = ArchScan(tools=["tools/gone.py"])
        assert _detect_oversized(scan, tmp_path) == []


class TestDetectManifestMismatch:
    def test_no_manifest(self, tmp_path: Path) -> None:
        scan = ArchScan(agents=["_bmad/core/agents/analyst.md"])
        assert _detect_manifest_mismatch(scan, tmp_path) == []

    def test_matching_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "_bmad" / "_config").mkdir(parents=True)
        (tmp_path / "_bmad" / "core" / "agents").mkdir(parents=True)
        (tmp_path / "_bmad" / "core" / "agents" / "analyst.md").write_text("# Analyst\n")
        manifest = tmp_path / "_bmad" / "_config" / "agent-manifest.csv"
        manifest.write_text("name,title\nanalyst,Analyst\n")
        scan = ArchScan(agents=["_bmad/core/agents/analyst.md"])
        assert _detect_manifest_mismatch(scan, tmp_path) == []

    def test_missing_agent_in_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "_bmad" / "_config").mkdir(parents=True)
        manifest = tmp_path / "_bmad" / "_config" / "agent-manifest.csv"
        manifest.write_text("name,title\nanalyst,Analyst\nghost-agent,Ghost\n")
        scan = ArchScan(agents=[])
        dissonances = _detect_manifest_mismatch(scan, tmp_path)
        # "analyst" and "ghost-agent" referenced but no agent files exist
        names = [d.message for d in dissonances]
        assert any("ghost-agent" in m for m in names)

    def test_header_rows_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "_bmad" / "_config").mkdir(parents=True)
        manifest = tmp_path / "_bmad" / "_config" / "agent-manifest.csv"
        manifest.write_text("name,title\n# comment line\n")
        scan = ArchScan(agents=[])
        assert _detect_manifest_mismatch(scan, tmp_path) == []


class TestDetectBrokenRefs:
    def test_no_refs(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "analyst.md").write_text("# Analyst\nNo refs here.\n")
        scan = ArchScan(agents=["agents/analyst.md"])
        assert _detect_broken_refs(scan, tmp_path) == []

    def test_valid_ref(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "shared.md").write_text("shared\n")
        (tmp_path / "agents" / "a.md").write_text("load: shared.md\n")
        scan = ArchScan(agents=["agents/a.md"])
        # shared.md exists relative to a.md's parent
        assert _detect_broken_refs(scan, tmp_path) == []

    def test_broken_ref_detected(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "a.md").write_text("include: missing/file.md\n")
        scan = ArchScan(agents=["agents/a.md"])
        dissonances = _detect_broken_refs(scan, tmp_path)
        assert len(dissonances) == 1
        assert dissonances[0].category == "broken-ref"
        assert "missing/file.md" in dissonances[0].message

    def test_http_refs_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "a.md").write_text("source: http://example.com/thing.md\n")
        scan = ArchScan(agents=["agents/a.md"])
        assert _detect_broken_refs(scan, tmp_path) == []

    def test_missing_file_ignored(self, tmp_path: Path) -> None:
        scan = ArchScan(agents=["nonexistent/agent.md"])
        assert _detect_broken_refs(scan, tmp_path) == []


class TestDetectDuplication:
    def test_no_duplication(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "a.md").write_text("This agent handles analysis of business requirements and stakeholder needs.")
        (tmp_path / "agents" / "b.md").write_text("This agent handles infrastructure deployment with terraform and kubernetes.")
        scan = ArchScan(agents=["agents/a.md", "agents/b.md"])
        assert _detect_duplication(scan, tmp_path) == []

    def test_similar_agents_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        content = "This agent handles architecture review, design patterns, system design, infrastructure planning, and deployment."
        (tmp_path / "agents" / "a.md").write_text(content)
        (tmp_path / "agents" / "b.md").write_text(content)  # identical
        scan = ArchScan(agents=["agents/a.md", "agents/b.md"])
        dissonances = _detect_duplication(scan, tmp_path)
        assert len(dissonances) == 1
        assert dissonances[0].category == "duplication"

    def test_missing_file_ignored(self, tmp_path: Path) -> None:
        scan = ArchScan(agents=["agents/gone.md"])
        assert _detect_duplication(scan, tmp_path) == []


# ── _scan_project cross-refs ─────────────────────────────────────────────────

class TestScanProjectCrossRefs:
    def test_cross_refs_built(self, project: Path) -> None:
        """Agents that reference others via stem name build cross_refs."""
        scan = _scan_project(project)
        # architect.md contains "analyst" → should reference analyst.md
        has_cross_ref = any("architect" in k for k in scan.cross_refs)
        assert has_cross_ref

    def test_oserror_in_cross_ref_is_ignored(self, project: Path) -> None:
        """Unreadable files should not crash the scan."""
        bad = project / "_bmad" / "core" / "agents" / "broken.md"
        bad.write_text("# broken\n")
        bad.chmod(0o000)
        try:
            scan = _scan_project(project)
            assert scan.total_files > 0
        finally:
            bad.chmod(0o644)


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

    def test_grade_b(self) -> None:
        # 3 HIGH = -24 → score 76 → B
        ds = [Dissonance("x", SEVERITY_HIGH, "f", "m")] * 3
        score, grade, _ = _compute_score(ds)
        assert score == 76
        assert grade == "B"

    def test_grade_c(self) -> None:
        # 5 HIGH = -40 → score 60 → C
        ds = [Dissonance("x", SEVERITY_HIGH, "f", "m")] * 5
        score, grade, _ = _compute_score(ds)
        assert score == 60
        assert grade == "C"

    def test_grade_d(self) -> None:
        # 7 HIGH = -56 → score 44 → D
        ds = [Dissonance("x", SEVERITY_HIGH, "f", "m")] * 7
        score, grade, _ = _compute_score(ds)
        assert score == 44
        assert grade == "D"


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


# ── CLI wrapper ───────────────────────────────────────────────────────────────

class TestCli:
    def test_cli_report(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["harmony_check", "--project-root", str(project)])
        assert _cli() == 0

    def test_cli_json(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["harmony_check", "--project-root", str(project), "--json"])
        assert _cli() == 0

    def test_cli_empty_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["harmony_check", "--project-root", str(tmp_path)])
        assert _cli() == 0

    def test_cli_with_dissonances(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """CLI output with dissonances shows icons and messages."""
        (tmp_path / "_bmad" / "core" / "agents").mkdir(parents=True)
        (tmp_path / "_bmad" / "core" / "agents" / "BadName.md").write_text("# BadName\n")
        monkeypatch.setattr("sys.argv", ["harmony_check", "--project-root", str(tmp_path)])
        assert _cli() == 0
        captured = capsys.readouterr()
        assert "Dissonances:" in captured.out
