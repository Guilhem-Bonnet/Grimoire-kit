"""Memory Lint — cross-file coherence analysis for Grimoire memory.

Detects contradictions, duplicates, orphan decisions, stale entries,
and chronological inconsistencies in the ``_grimoire/_memory`` directory.

Usage::

    from grimoire.tools.memory_lint import MemoryLint

    ml = MemoryLint(Path("."))
    report = ml.run()
    print(report.error_count, report.warning_count)
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from grimoire.tools._common import GrimoireTool

# ── Constants ─────────────────────────────────────────────────────────────────

DUPLICATE_THRESHOLD = 0.75
CONTRADICTION_THRESHOLD = 0.30
STALENESS_DAYS = 90
DECAY_HALFLIFE_DAYS = 30

POSITIVE_MARKERS = ("adopté", "validé", "approuvé", "réussi", "succès",
                    "adopted", "validated", "approved", "succeeded", "success")
NEGATIVE_MARKERS = ("rejeté", "abandonné", "échoué", "failed", "rejected",
                    "abandoned", "deprecated", "obsolete")

_STOPWORDS = frozenset({
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou", "en",
    "à", "au", "aux", "pour", "par", "sur", "dans", "avec", "que", "qui",
    "est", "sont", "a", "ont", "pas", "ne", "ni", "mais",
    "the", "an", "is", "are", "was", "were", "be", "been", "have", "has",
    "had", "do", "does", "did", "will", "would", "shall", "should", "may",
    "might", "can", "could", "of", "to", "in", "for", "on", "with", "at",
    "by", "from", "as", "into", "about", "not", "no", "but", "or", "and",
    "if", "then", "than", "too", "very", "just", "it", "its", "this", "that",
})


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class LintIssue:
    """A single lint finding."""

    issue_id: str
    severity: str  # "error", "warning", "info"
    category: str
    title: str
    description: str
    files: tuple[str, ...] = ()
    entries: tuple[str, ...] = ()
    fix_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "files": list(self.files),
            "entries": list(self.entries),
            "fix_suggestion": self.fix_suggestion,
        }


@dataclass(frozen=True, slots=True)
class MemoryFile:
    """Parsed memory file with its entries."""

    path: str
    kind: str  # "learnings", "decisions", "trace", "failure-museum", etc.
    entries: tuple[tuple[str, str], ...]  # (date, text)


@dataclass(slots=True)
class LintReport:
    """Aggregated lint report."""

    files_scanned: int = 0
    entries_scanned: int = 0
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "entries_scanned": self.entries_scanned,
            "summary": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "info": self.info_count,
                "total": len(self.issues),
            },
            "issues": [i.to_dict() for i in self.issues],
        }


# ── NLP Helpers ───────────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> set[str]:
    """Extract significant keywords from text."""
    words = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity on extracted keywords."""
    ka = _extract_keywords(text_a)
    kb = _extract_keywords(text_b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def _has_polarity(text: str) -> tuple[bool, bool]:
    """Return (is_positive, is_negative) based on marker words."""
    lower = text.lower()
    is_pos = any(m in lower for m in POSITIVE_MARKERS)
    is_neg = any(m in lower for m in NEGATIVE_MARKERS)
    return is_pos, is_neg


# ── Parsing ───────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})")
_TRACE_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*\[(\w+)\]\s*\[([^\]]+)\]\s*(.*)"
)


def _parse_markdown(path: Path) -> list[tuple[str, str]]:
    """Parse a markdown file into (date, text) entries."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _DATE_RE.search(line)
        date = m.group(1) if m else ""
        if line.startswith(("- ", "* ")):
            entries.append((date, line[2:].strip()))
    return entries


def _parse_trace(path: Path) -> list[tuple[str, str]]:
    """Parse Grimoire_TRACE.md into (date, text) entries."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        m = _TRACE_RE.match(line.strip())
        if m:
            date, level, agent, payload = m.groups()
            entries.append((date, f"[{agent}] [{level}] {payload}"))
    return entries


def _parse_jsonl(path: Path) -> list[tuple[str, str]]:
    """Parse a JSONL file into (date, text) entries."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp", "")
        date = ts[:10] if len(ts) >= 10 else ""
        summary = rec.get("details") or rec.get("title") or rec.get("description", "")
        if summary:
            entries.append((date, summary))
    return entries


def collect_memory_files(project_root: Path) -> list[MemoryFile]:
    """Collect and parse all memory files from the project."""
    files: list[MemoryFile] = []
    mem = project_root / "_grimoire" / "_memory"

    # Agent learnings
    learnings = mem / "agent-learnings"
    if learnings.exists():
        for f in sorted(learnings.glob("*.md")):
            entries = _parse_markdown(f)
            if entries:
                files.append(MemoryFile(
                    path=f"learnings/{f.name}", kind="learnings",
                    entries=tuple(entries),
                ))

    # Decisions log
    decisions = mem / "decisions-log.md"
    if decisions.exists():
        entries = _parse_markdown(decisions)
        if entries:
            files.append(MemoryFile(
                path="decisions-log.md", kind="decisions", entries=tuple(entries),
            ))

    # Grimoire TRACE
    trace = project_root / "_grimoire-output" / "Grimoire_TRACE.md"
    if trace.exists():
        entries = _parse_trace(trace)
        if entries:
            files.append(MemoryFile(
                path="Grimoire_TRACE.md", kind="trace", entries=tuple(entries),
            ))

    # Failure museum
    failure = mem / "failure-museum.md"
    if failure.exists():
        entries = _parse_markdown(failure)
        if entries:
            files.append(MemoryFile(
                path="failure-museum.md", kind="failure-museum",
                entries=tuple(entries),
            ))

    # Contradiction log
    contradiction = mem / "contradiction-log.md"
    if contradiction.exists():
        entries = _parse_markdown(contradiction)
        if entries:
            files.append(MemoryFile(
                path="contradiction-log.md", kind="contradictions",
                entries=tuple(entries),
            ))

    # Shared context
    shared = mem / "shared-context.md"
    if shared.exists():
        entries = _parse_markdown(shared)
        if entries:
            files.append(MemoryFile(
                path="shared-context.md", kind="shared-context",
                entries=tuple(entries),
            ))

    # CC-feedback (JSONL)
    cc = mem / "cc-feedback.jsonl"
    if cc.exists():
        entries = _parse_jsonl(cc)
        if entries:
            files.append(MemoryFile(
                path="cc-feedback.jsonl", kind="cc-feedback",
                entries=tuple(entries),
            ))

    return files


# ── Checks ────────────────────────────────────────────────────────────────────

def check_contradictions(files: list[MemoryFile]) -> list[LintIssue]:
    """Detect contradictions: similar topics with opposite polarity across files."""
    issues: list[LintIssue] = []
    positives: list[tuple[str, str]] = []  # (file_path, text)
    negatives: list[tuple[str, str]] = []

    for mf in files:
        for _date, text in mf.entries:
            is_pos, is_neg = _has_polarity(text)
            if is_pos:
                positives.append((mf.path, text))
            if is_neg:
                negatives.append((mf.path, text))

    idx = 0
    for pos_file, pos_text in positives:
        for neg_file, neg_text in negatives:
            if pos_file == neg_file:
                continue
            sim = similarity(pos_text, neg_text)
            if sim >= CONTRADICTION_THRESHOLD:
                idx += 1
                issues.append(LintIssue(
                    issue_id=f"ML-{idx:03d}",
                    severity="error",
                    category="contradiction",
                    title=f"Contradiction between {pos_file} and {neg_file}",
                    description=f"Opposite polarity on similar topic (similarity: {sim:.0%})",
                    files=(pos_file, neg_file),
                    entries=(pos_text[:120], neg_text[:120]),
                    fix_suggestion="Resolve and document in contradiction-log.md",
                ))
    return issues


def check_duplicates(files: list[MemoryFile]) -> list[LintIssue]:
    """Detect near-duplicate entries across different files."""
    issues: list[LintIssue] = []
    seen: list[tuple[str, str]] = []
    idx = 0

    for mf in files:
        for _date, text in mf.entries:
            for prev_file, prev_text in seen:
                if prev_file == mf.path:
                    continue
                if similarity(text, prev_text) >= DUPLICATE_THRESHOLD:
                    idx += 1
                    issues.append(LintIssue(
                        issue_id=f"ML-{idx:03d}",
                        severity="warning",
                        category="duplicate",
                        title=f"Duplicate between {mf.path} and {prev_file}",
                        description="Very similar entries in two memory files",
                        files=(mf.path, prev_file),
                        entries=(text[:120], prev_text[:120]),
                        fix_suggestion="Keep the entry in the most appropriate file",
                    ))
            seen.append((mf.path, text))
    return issues


def check_orphan_decisions(files: list[MemoryFile]) -> list[LintIssue]:
    """Detect decisions in trace with no matching entry in decisions-log."""
    issues: list[LintIssue] = []
    trace = next((f for f in files if f.kind == "trace"), None)
    decisions = next((f for f in files if f.kind == "decisions"), None)
    if not trace or not decisions:
        return issues

    decision_texts = [text for _, text in decisions.entries]
    idx = 0
    for date, text in trace.entries:
        if "[DECISION]" not in text:
            continue
        found = any(similarity(text, dt) >= 0.3 for dt in decision_texts)
        if not found:
            idx += 1
            issues.append(LintIssue(
                issue_id=f"ML-{idx:03d}",
                severity="warning",
                category="orphan",
                title=f"Orphan decision in trace [{date}]",
                description="Decision in trace has no match in decisions-log.md",
                files=("Grimoire_TRACE.md", "decisions-log.md"),
                entries=(text[:150],),
                fix_suggestion="Add to decisions-log.md for traceability",
            ))
    return issues


def check_chronological(files: list[MemoryFile]) -> list[LintIssue]:
    """Detect chronological inconsistencies within a file."""
    issues: list[LintIssue] = []
    idx = 0
    for mf in files:
        dates = [d for d, _ in mf.entries if d]
        if len(dates) < 3:
            continue
        total = len(dates) - 1
        asc = sum(1 for i in range(1, len(dates)) if dates[i] >= dates[i - 1])
        desc = sum(1 for i in range(1, len(dates)) if dates[i] <= dates[i - 1])
        if max(asc, desc) / total < 0.7:
            idx += 1
            issues.append(LintIssue(
                issue_id=f"ML-{idx:03d}",
                severity="info",
                category="chrono",
                title=f"Disordered dates in {mf.path}",
                description=f"{total + 1} dated entries not in chronological order (asc: {asc}, desc: {desc})",
                files=(mf.path,),
                fix_suggestion="Reorder entries by date",
            ))
    return issues


def check_freshness(files: list[MemoryFile], now: datetime | None = None) -> list[LintIssue]:
    """Detect stale memory files (majority of entries > STALENESS_DAYS old)."""
    issues: list[LintIssue] = []
    now = now or datetime.now()
    idx = 0

    for mf in files:
        dated = [(d, t) for d, t in mf.entries if d and len(d) >= 10]
        if not dated:
            continue

        stale = 0
        total_freshness = 0.0
        for date_str, _ in dated:
            try:
                entry_date = datetime(int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10]))
            except (ValueError, IndexError):
                continue
            age = max(0, (now - entry_date).days)
            total_freshness += math.pow(2.0, -age / DECAY_HALFLIFE_DAYS)
            if age > STALENESS_DAYS:
                stale += 1

        if not dated:
            continue
        avg_freshness = total_freshness / len(dated)
        if stale / len(dated) > 0.5:
            idx += 1
            issues.append(LintIssue(
                issue_id=f"ML-{idx:03d}",
                severity="info",
                category="staleness",
                title=f"Stale file: {mf.path}",
                description=(
                    f"{stale}/{len(dated)} entries older than {STALENESS_DAYS} days. "
                    f"Avg freshness: {avg_freshness:.2f}"
                ),
                files=(mf.path,),
                fix_suggestion="Archive old entries or clean up obsolete ones",
            ))
    return issues


# ── Tool ──────────────────────────────────────────────────────────────────────

class MemoryLint(GrimoireTool):
    """Memory coherence linter."""

    def run(self, **kwargs: Any) -> LintReport:
        files = collect_memory_files(self._project_root)
        report = LintReport(
            files_scanned=len(files),
            entries_scanned=sum(len(f.entries) for f in files),
        )
        report.issues.extend(check_contradictions(files))
        report.issues.extend(check_duplicates(files))
        report.issues.extend(check_orphan_decisions(files))
        report.issues.extend(check_chronological(files))
        report.issues.extend(check_freshness(files))

        severity_order = {"error": 0, "warning": 1, "info": 2}
        report.issues.sort(key=lambda i: severity_order.get(i.severity, 9))
        return report
