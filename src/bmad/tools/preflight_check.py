"""Preflight Check — pre-execution environment scan.

Verifies project readiness before agent tasks:
- Project structure (dirs, config)
- Git state (conflicts, uncommitted)
- Required tools (git, python3)
- Memory health (stale session, contradictions)

Usage::

    from bmad.tools.preflight_check import PreflightCheck

    pc = PreflightCheck(Path("."))
    report = pc.run()
    print(report.go_nogo)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bmad.tools._common import BmadTool

# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CheckItem:
    """A single preflight check result."""

    name: str
    severity: str  # "blocker", "warning", "info"
    message: str
    fix_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "fix_hint": self.fix_hint,
        }


@dataclass(slots=True)
class PreflightReport:
    """Aggregated preflight report."""

    checks: list[CheckItem] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def blockers(self) -> list[CheckItem]:
        return [c for c in self.checks if c.severity == "blocker"]

    @property
    def warnings(self) -> list[CheckItem]:
        return [c for c in self.checks if c.severity == "warning"]

    @property
    def go_nogo(self) -> str:
        if self.blockers:
            return "NO-GO"
        if self.warnings:
            return "GO-WITH-WARNINGS"
        return "GO"

    def to_dict(self) -> dict[str, Any]:
        return {
            "go_nogo": self.go_nogo,
            "blockers": len(self.blockers),
            "warnings": len(self.warnings),
            "total_checks": len(self.checks),
            "checks": [c.to_dict() for c in self.checks],
            "timestamp": self.timestamp,
        }


# ── Tool ──────────────────────────────────────────────────────────────────────

class PreflightCheck(BmadTool):
    """Pre-flight environment validator."""

    def run(self, **kwargs: Any) -> PreflightReport:
        report = PreflightReport()
        report.checks.extend(self._check_structure())
        report.checks.extend(self._check_tools())
        report.checks.extend(self._check_git())
        report.checks.extend(self._check_memory())
        return report

    def _check_structure(self) -> list[CheckItem]:
        checks: list[CheckItem] = []
        required_dirs = [
            ("_bmad", "BMAD directory"),
            ("_bmad/_memory", "Memory directory"),
        ]
        for rel, label in required_dirs:
            if not (self._project_root / rel).is_dir():
                checks.append(CheckItem(
                    name="structure",
                    severity="blocker",
                    message=f"{label} missing: {rel}",
                    fix_hint="Run: bmad init",
                ))

        config = self._project_root / "project-context.yaml"
        if not config.is_file():
            checks.append(CheckItem(
                name="config",
                severity="blocker",
                message="project-context.yaml not found",
                fix_hint="Run: bmad init",
            ))
        return checks

    def _check_tools(self) -> list[CheckItem]:
        checks: list[CheckItem] = []
        for tool in ("git", "python3"):
            if not shutil.which(tool):
                checks.append(CheckItem(
                    name="tool-missing",
                    severity="blocker",
                    message=f"Required tool not found: {tool}",
                    fix_hint=f"Install {tool}",
                ))
        return checks

    def _check_git(self) -> list[CheckItem]:
        checks: list[CheckItem] = []
        if not (self._project_root / ".git").is_dir():
            checks.append(CheckItem(name="git", severity="info", message="No Git repository detected"))
            return checks

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=10,
            )
            if result.stdout.strip():
                conflicted = result.stdout.strip().split("\n")
                checks.append(CheckItem(
                    name="merge-conflict",
                    severity="blocker",
                    message=f"{len(conflicted)} file(s) with merge conflicts",
                    fix_hint="Resolve conflicts before proceeding",
                ))

            result = subprocess.run(
                ["git", "status", "--porcelain", "--", "_bmad/"],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=10,
            )
            if result.stdout.strip():
                n = len(result.stdout.strip().split("\n"))
                checks.append(CheckItem(
                    name="uncommitted",
                    severity="warning",
                    message=f"{n} uncommitted change(s) in _bmad/",
                ))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            checks.append(CheckItem(name="git-error", severity="info", message="Could not run git commands"))
        return checks

    def _check_memory(self) -> list[CheckItem]:
        checks: list[CheckItem] = []
        mem_dir = self._project_root / "_bmad" / "_memory"
        if not mem_dir.is_dir():
            return checks

        # Stale session
        session = mem_dir / "session-state.md"
        if session.is_file():
            try:
                age_hours = (datetime.now() - datetime.fromtimestamp(session.stat().st_mtime)).total_seconds() / 3600
                if age_hours > 168:  # > 1 week
                    checks.append(CheckItem(
                        name="stale-session",
                        severity="info",
                        message=f"session-state.md is {age_hours:.0f}h old",
                    ))
            except OSError:
                pass

        # Contradictions
        contradictions = mem_dir / "contradiction-log.md"
        if contradictions.is_file():
            try:
                content = contradictions.read_text(encoding="utf-8")
                unresolved = content.count("- [ ]")
                if unresolved > 0:
                    checks.append(CheckItem(
                        name="contradictions",
                        severity="warning",
                        message=f"{unresolved} unresolved contradiction(s)",
                    ))
            except OSError:
                pass

        return checks
