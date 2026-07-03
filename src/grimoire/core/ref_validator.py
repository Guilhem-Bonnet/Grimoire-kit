"""Cross-reference validator — detect stale and broken references.

Inspired by gstack's ref staleness detection.  Scans Markdown files
for internal file-path references and validates they point to
existing files.

Usage::

    from grimoire.core.ref_validator import RefValidator

    rv = RefValidator(project_root=Path("."))
    report = rv.validate()
    print(report.broken_count, report.stale_count)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["RefIssue", "RefReport", "RefValidator"]

REF_VALIDATOR_VERSION = "1.0.0"

# ── Patterns ─────────────────────────────────────────────────────────────────

# Markdown link: [text](path)
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
# Inline code reference to a file path
_FILE_REF = re.compile(r"`([a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})`")

_SKIP_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"})
_SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "data:")

# Staleness: files not modified in this many days
_STALENESS_DAYS = 90


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RefIssue:
    """A single reference problem."""

    source_file: str
    line: int
    ref: str
    issue_type: str  # broken, stale
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_file,
            "line": self.line,
            "ref": self.ref,
            "type": self.issue_type,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RefReport:
    """Validation report across all scanned files."""

    files_scanned: int
    refs_checked: int
    issues: tuple[RefIssue, ...]
    timestamp: str

    @property
    def broken_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == "broken")

    @property
    def stale_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == "stale")

    @property
    def clean(self) -> bool:
        return len(self.issues) == 0

    def to_markdown(self) -> str:
        lines = [
            f"# Reference Validation Report — {self.timestamp}",
            "",
            f"**Files scanned**: {self.files_scanned} | "
            f"**Refs checked**: {self.refs_checked} | "
            f"**Broken**: {self.broken_count} | "
            f"**Stale**: {self.stale_count}",
            "",
        ]
        if self.clean:
            lines.append("All references valid.")
        else:
            lines.append("| Source | Line | Reference | Issue | Detail |")
            lines.append("|---|---|---|---|---|")
            for issue in self.issues:
                lines.append(
                    f"| {issue.source_file} | {issue.line} | "
                    f"`{issue.ref}` | {issue.issue_type} | {issue.detail} |"
                )
        return "\n".join(lines)


# ── Core implementation ──────────────────────────────────────────────────────


class RefValidator:
    """Scans Markdown files for broken or stale references.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    scan_dirs :
        Directories to scan (relative to project root).
        Defaults to common documentation locations.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        scan_dirs: tuple[str, ...] | None = None,
        staleness_days: int = _STALENESS_DAYS,
    ) -> None:
        self._root = project_root
        self._scan_dirs = scan_dirs or (
            "docs",
            "_grimoire-runtime-output",
            ".github/skills",
            "_grimoire-runtime",
        )
        self._staleness_days = staleness_days

    def validate(self, *, check_stale: bool = True) -> RefReport:
        """Run full validation across all Markdown files.

        Parameters
        ----------
        check_stale :
            Whether to flag stale references (slow for large trees).
        """
        md_files = self._find_markdown_files()
        issues: list[RefIssue] = []
        refs_checked = 0

        for md_file in md_files:
            file_issues, file_refs = self._check_file(md_file, check_stale=check_stale)
            issues.extend(file_issues)
            refs_checked += file_refs

        return RefReport(
            files_scanned=len(md_files),
            refs_checked=refs_checked,
            issues=tuple(issues),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def validate_file(self, file_path: Path, *, check_stale: bool = True) -> list[RefIssue]:
        """Validate a single file."""
        issues, _ = self._check_file(file_path, check_stale=check_stale)
        return issues

    def _find_markdown_files(self) -> list[Path]:
        """Find all .md files in scan directories."""
        files: list[Path] = []
        for dir_name in self._scan_dirs:
            d = self._root / dir_name
            if d.is_dir():
                files.extend(d.rglob("*.md"))
        return sorted(files)

    def _check_file(self, file_path: Path, *, check_stale: bool) -> tuple[list[RefIssue], int]:
        """Check all references in a single file."""
        issues: list[RefIssue] = []
        refs_checked = 0
        rel_source = str(file_path.relative_to(self._root))

        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            return issues, 0

        for line_num, line in enumerate(content.splitlines(), 1):
            # Markdown links
            for _text, href in _MD_LINK.findall(line):
                if any(href.startswith(p) for p in _SKIP_PREFIXES):
                    continue
                # Strip anchor
                clean = href.split("#")[0]
                if not clean:
                    continue
                ext = Path(clean).suffix.lower()
                if ext in _SKIP_EXTENSIONS:
                    continue
                refs_checked += 1
                issue = self._validate_ref(rel_source, line_num, clean, check_stale)
                if issue:
                    issues.append(issue)

            # Inline code file references
            for ref in _FILE_REF.findall(line):
                if "/" not in ref and "\\" not in ref:
                    continue  # likely not a file path
                refs_checked += 1
                issue = self._validate_ref(rel_source, line_num, ref, check_stale)
                if issue:
                    issues.append(issue)

        return issues, refs_checked

    def _validate_ref(self, source: str, line: int, ref: str, check_stale: bool) -> RefIssue | None:
        """Validate a single reference."""
        # Try relative to source file's directory, then project root
        source_dir = (self._root / source).parent
        candidates = [source_dir / ref, self._root / ref]

        resolved = None
        for candidate in candidates:
            if candidate.exists():
                resolved = candidate
                break

        if resolved is None:
            return RefIssue(
                source_file=source,
                line=line,
                ref=ref,
                issue_type="broken",
                detail="File not found",
            )

        if check_stale and resolved.is_file():
            mtime = resolved.stat().st_mtime
            age_days = (time.time() - mtime) / 86400
            if age_days > self._staleness_days:
                return RefIssue(
                    source_file=source,
                    line=line,
                    ref=ref,
                    issue_type="stale",
                    detail=f"Last modified {int(age_days)} days ago",
                )

        return None
