"""Tests for bmad.tools.preflight_check — PreflightCheck tool."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad.tools.preflight_check import CheckItem, PreflightCheck, PreflightReport

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal BMAD project."""
    (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
    (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture()
def bare_project(tmp_path: Path) -> Path:
    """Project with missing structure."""
    return tmp_path


# ── CheckItem ─────────────────────────────────────────────────────────────────

class TestCheckItem:
    def test_to_dict(self) -> None:
        c = CheckItem(name="test", severity="blocker", message="msg", fix_hint="fix")
        d = c.to_dict()
        assert d["name"] == "test"
        assert d["severity"] == "blocker"
        assert d["fix_hint"] == "fix"

    def test_frozen(self) -> None:
        c = CheckItem(name="x", severity="info", message="m")
        with pytest.raises(AttributeError):
            c.name = "y"  # type: ignore[misc]

    def test_default_fix_hint(self) -> None:
        c = CheckItem(name="x", severity="info", message="m")
        assert c.fix_hint == ""


# ── PreflightReport ──────────────────────────────────────────────────────────

class TestPreflightReport:
    def test_empty_go(self) -> None:
        r = PreflightReport()
        assert r.go_nogo == "GO"
        assert len(r.blockers) == 0
        assert len(r.warnings) == 0

    def test_blocker_no_go(self) -> None:
        r = PreflightReport(checks=[
            CheckItem(name="a", severity="blocker", message="bad"),
        ])
        assert r.go_nogo == "NO-GO"
        assert len(r.blockers) == 1

    def test_warning_go_with_warnings(self) -> None:
        r = PreflightReport(checks=[
            CheckItem(name="a", severity="warning", message="meh"),
        ])
        assert r.go_nogo == "GO-WITH-WARNINGS"
        assert len(r.warnings) == 1

    def test_to_dict(self) -> None:
        r = PreflightReport(checks=[
            CheckItem(name="a", severity="info", message="ok"),
        ])
        d = r.to_dict()
        assert d["go_nogo"] == "GO"
        assert d["total_checks"] == 1
        assert len(d["checks"]) == 1
        assert "timestamp" in d


# ── Structure Checks ─────────────────────────────────────────────────────────

class TestStructureChecks:
    def test_valid_structure(self, project: Path) -> None:
        pc = PreflightCheck(project)
        report = pc.run()
        # No structure blockers since all dirs exist
        blockers = [c for c in report.checks if c.name == "structure"]
        assert len(blockers) == 0

    def test_missing_bmad_dir(self, bare_project: Path) -> None:
        pc = PreflightCheck(bare_project)
        report = pc.run()
        assert report.go_nogo == "NO-GO"
        struct = [c for c in report.checks if c.name == "structure"]
        assert len(struct) >= 1

    def test_missing_config(self, tmp_path: Path) -> None:
        (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
        pc = PreflightCheck(tmp_path)
        report = pc.run()
        config_checks = [c for c in report.checks if c.name == "config"]
        assert len(config_checks) == 1
        assert config_checks[0].severity == "blocker"


# ── Tool Checks ──────────────────────────────────────────────────────────────

class TestToolChecks:
    def test_tools_available(self, project: Path) -> None:
        """git and python3 should be available in test env."""
        pc = PreflightCheck(project)
        report = pc.run()
        missing = [c for c in report.checks if c.name == "tool-missing"]
        assert len(missing) == 0

    @patch("shutil.which", return_value=None)
    def test_missing_tool(self, _mock: object, project: Path) -> None:
        pc = PreflightCheck(project)
        report = pc.run()
        missing = [c for c in report.checks if c.name == "tool-missing"]
        assert len(missing) >= 1
        assert missing[0].severity == "blocker"


# ── Git Checks ────────────────────────────────────────────────────────────────

class TestGitChecks:
    def test_no_git_dir(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  name: t\n")
        (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
        pc = PreflightCheck(tmp_path)
        report = pc.run()
        git_checks = [c for c in report.checks if c.name == "git"]
        assert len(git_checks) == 1
        assert git_checks[0].severity == "info"

    def test_git_with_uncommitted(self, project: Path) -> None:
        # Create uncommitted file in _bmad
        (project / "_bmad" / "new-file.md").write_text("new")
        pc = PreflightCheck(project)
        report = pc.run()
        # May or may not detect depending on real git state
        # Just verify it doesn't crash
        assert isinstance(report.go_nogo, str)

    def test_merge_conflicts_detected(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulate git merge conflict output."""
        call_count = 0

        def _mock_run(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # First call: git diff --diff-filter=U → conflicts
                result.stdout = "src/conflict.py\nsrc/other.py\n"
            else:
                # Second call: git status --porcelain
                result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", _mock_run)
        pc = PreflightCheck(project)
        report = pc.run()
        conflicts = [c for c in report.checks if c.name == "merge-conflict"]
        assert len(conflicts) == 1
        assert conflicts[0].severity == "blocker"
        assert "2 file(s)" in conflicts[0].message

    def test_uncommitted_changes_detected(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulate uncommitted _bmad/ changes."""
        call_count = 0

        def _mock_run(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.stdout = ""  # No conflicts
            else:
                result.stdout = "M _bmad/config.yaml\nM _bmad/agents/analyst.md\n"
            return result

        monkeypatch.setattr(subprocess, "run", _mock_run)
        pc = PreflightCheck(project)
        report = pc.run()
        uncommitted = [c for c in report.checks if c.name == "uncommitted"]
        assert len(uncommitted) == 1
        assert uncommitted[0].severity == "warning"

    def test_git_timeout_handled(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Git timeout should produce info-level check, not crash."""
        def _timeout(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(subprocess, "run", _timeout)
        pc = PreflightCheck(project)
        report = pc.run()
        errors = [c for c in report.checks if c.name == "git-error"]
        assert len(errors) == 1
        assert errors[0].severity == "info"


# ── Memory Checks ────────────────────────────────────────────────────────────

class TestMemoryChecks:
    def test_no_memory_issues(self, project: Path) -> None:
        pc = PreflightCheck(project)
        report = pc.run()
        mem = [c for c in report.checks if c.name in ("stale-session", "contradictions")]
        # No memory files → no issues
        assert len(mem) == 0

    def test_stale_session_detected(self, project: Path) -> None:
        """Session file older than 1 week should produce info check."""
        session = project / "_bmad" / "_memory" / "session-state.md"
        session.write_text("# Session state\n")
        # Set mtime to 10 days ago
        import os
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(session, (old_time, old_time))

        pc = PreflightCheck(project)
        report = pc.run()
        stale = [c for c in report.checks if c.name == "stale-session"]
        assert len(stale) == 1
        assert stale[0].severity == "info"

    def test_fresh_session_no_warning(self, project: Path) -> None:
        """Recent session file should not trigger stale warning."""
        session = project / "_bmad" / "_memory" / "session-state.md"
        session.write_text("# Fresh session\n")
        pc = PreflightCheck(project)
        report = pc.run()
        stale = [c for c in report.checks if c.name == "stale-session"]
        assert len(stale) == 0

    def test_contradiction_entries(self, project: Path) -> None:
        contradictions = project / "_bmad" / "_memory" / "contradiction-log.md"
        contradictions.write_text("# Log\n- [ ] unresolved issue one\n- [ ] unresolved issue two\n")
        pc = PreflightCheck(project)
        report = pc.run()
        contra = [c for c in report.checks if c.name == "contradictions"]
        assert len(contra) == 1
        assert contra[0].severity == "warning"


# ── Integration ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_full_run_clean_project(self, project: Path) -> None:
        pc = PreflightCheck(project)
        report = pc.run()
        assert isinstance(report, PreflightReport)
        assert report.go_nogo in ("GO", "GO-WITH-WARNINGS")

    def test_full_run_broken_project(self, bare_project: Path) -> None:
        pc = PreflightCheck(bare_project)
        report = pc.run()
        assert report.go_nogo == "NO-GO"
