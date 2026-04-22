"""contradiction_detector — V4.4.c BM-31 intra-file contradiction scanner.

Companion to ``grimoire.tools.memory_lint.check_contradictions`` which
runs cross-file. This module focuses on a single decisions ledger
(``_grimoire/_memory/decisions-log.md`` by default) and reports
positive/negative pairs on the same subject that are not reconciled by
a later revision marker.

Revision markers tolerated (case-insensitive substring on the newer entry):
  - ``[REVISED]``
  - ``[SUPERSEDES``
  - ``[ADR-`` (e.g. ``[ADR-042 review]``)
  - ``revised by adr-``

CLI:
    python -m grimoire.tools.contradiction_detector \\
        --project-root . [--write-log] [--format json|markdown]

Exit codes: 0 (no contradictions), 1 (contradictions found), 2 (error).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from grimoire.tools.memory_lint import (
    CONTRADICTION_THRESHOLD,
    _has_polarity,
    _parse_markdown,
    similarity,
)

DEFAULT_DECISIONS_PATH = Path("_grimoire/_memory/decisions-log.md")
DEFAULT_CONTRADICTION_LOG_PATH = Path("_grimoire/_memory/contradiction-log.md")

REVISION_MARKERS: tuple[str, ...] = (
    "[revised]",
    "[supersedes",
    "[adr-",
    "revised by adr-",
)


@dataclass(frozen=True)
class DecisionEntry:
    """A single bullet entry parsed from decisions-log.md."""

    line_index: int
    date: str
    text: str

    def is_revision(self) -> bool:
        lower = self.text.lower()
        return any(marker in lower for marker in REVISION_MARKERS)


@dataclass(frozen=True)
class Contradiction:
    """A detected contradiction between two entries on the same subject."""

    contradiction_id: str
    similarity: float
    positive: DecisionEntry
    negative: DecisionEntry
    revised: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "contradiction_id": self.contradiction_id,
            "similarity": round(self.similarity, 3),
            "revised": self.revised,
            "positive": asdict(self.positive),
            "negative": asdict(self.negative),
        }


@dataclass(frozen=True)
class DetectionReport:
    """Aggregate result of a scan."""

    source_path: str
    entry_count: int
    contradictions: tuple[Contradiction, ...]
    threshold: float = CONTRADICTION_THRESHOLD

    @property
    def unresolved(self) -> tuple[Contradiction, ...]:
        return tuple(c for c in self.contradictions if not c.revised)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "entry_count": self.entry_count,
            "threshold": self.threshold,
            "contradiction_count": len(self.contradictions),
            "unresolved_count": len(self.unresolved),
            "contradictions": [c.to_dict() for c in self.contradictions],
        }


def parse_decisions(path: Path) -> list[DecisionEntry]:
    """Parse a decisions-log markdown file into typed entries."""
    raw_entries = _parse_markdown(path)
    return [
        DecisionEntry(line_index=idx, date=date, text=text)
        for idx, (date, text) in enumerate(raw_entries)
    ]


def detect_contradictions(
    entries: Iterable[DecisionEntry],
    *,
    threshold: float = CONTRADICTION_THRESHOLD,
) -> list[Contradiction]:
    """Detect intra-file contradictions.

    Two entries contradict when:
      - one carries a positive polarity marker and the other a negative one
      - their Jaccard similarity on extracted keywords meets ``threshold``

    The newer entry's order in the file disambiguates the ``revised`` flag:
    if the *later* of the two carries a revision marker the contradiction
    is reported but flagged as resolved.
    """
    items = list(entries)
    contradictions: list[Contradiction] = []
    idx = 0
    for i, a in enumerate(items):
        a_pos, a_neg = _has_polarity(a.text)
        if not (a_pos or a_neg):
            continue
        for b in items[i + 1 :]:
            b_pos, b_neg = _has_polarity(b.text)
            if not (b_pos or b_neg):
                continue
            polarised = (a_pos and b_neg) or (a_neg and b_pos)
            if not polarised:
                continue
            sim = similarity(a.text, b.text)
            if sim < threshold:
                continue
            positive, negative = (a, b) if a_pos else (b, a)
            later = a if a.line_index >= b.line_index else b
            revised = later.is_revision()
            idx += 1
            contradictions.append(
                Contradiction(
                    contradiction_id=f"BM31-{idx:03d}",
                    similarity=sim,
                    positive=positive,
                    negative=negative,
                    revised=revised,
                )
            )
    return contradictions


def scan(
    project_root: Path,
    *,
    decisions_path: Path | None = None,
    threshold: float = CONTRADICTION_THRESHOLD,
) -> DetectionReport:
    """Run a full scan against a project root."""
    rel_path = decisions_path or DEFAULT_DECISIONS_PATH
    path = project_root / rel_path
    entries = parse_decisions(path)
    contradictions = detect_contradictions(entries, threshold=threshold)
    return DetectionReport(
        source_path=str(rel_path),
        entry_count=len(entries),
        contradictions=tuple(contradictions),
        threshold=threshold,
    )


def format_markdown(report: DetectionReport) -> str:
    """Render a human-readable markdown report."""
    lines = [
        f"# Contradiction scan — {report.source_path}",
        "",
        f"- Entries scanned: {report.entry_count}",
        f"- Threshold: {report.threshold:.2f}",
        f"- Contradictions: {len(report.contradictions)} "
        f"({len(report.unresolved)} unresolved)",
        "",
    ]
    if not report.contradictions:
        lines.append("_No contradictions detected._")
        return "\n".join(lines) + "\n"
    for c in report.contradictions:
        status = "RESOLVED (revision marker)" if c.revised else "UNRESOLVED"
        lines.append(f"## {c.contradiction_id} — {status}")
        lines.append("")
        lines.append(f"- Similarity: {c.similarity:.0%}")
        lines.append(
            f"- Positive (line {c.positive.line_index}, {c.positive.date or 'no-date'}): "
            f"{c.positive.text}"
        )
        lines.append(
            f"- Negative (line {c.negative.line_index}, {c.negative.date or 'no-date'}): "
            f"{c.negative.text}"
        )
        lines.append("")
    return "\n".join(lines) + "\n"


def append_to_contradiction_log(
    project_root: Path,
    report: DetectionReport,
    *,
    log_path: Path | None = None,
) -> Path | None:
    """Append unresolved contradictions to ``contradiction-log.md``.

    Returns the path written to, or ``None`` if there is nothing to write.
    """
    unresolved = report.unresolved
    if not unresolved:
        return None
    rel = log_path or DEFAULT_CONTRADICTION_LOG_PATH
    target = project_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    block = ["", f"## Scan {report.source_path}"]
    for c in unresolved:
        block.append(
            f"- [{c.contradiction_id}] sim={c.similarity:.0%} "
            f"pos(line {c.positive.line_index})={c.positive.text!r} "
            f"neg(line {c.negative.line_index})={c.negative.text!r}"
        )
    block.append("")
    with target.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grimoire-contradiction-detector",
        description="Scan decisions-log.md for unresolved contradictions (BM-31).",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--decisions-path",
        type=Path,
        default=None,
        help="Override the decisions log path (relative to project root).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=CONTRADICTION_THRESHOLD,
        help="Jaccard similarity threshold (default: %(default).2f).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
    )
    parser.add_argument(
        "--write-log",
        action="store_true",
        help="Append unresolved findings to _memory/contradiction-log.md.",
    )
    args = parser.parse_args(argv)
    try:
        report = scan(
            args.project_root,
            decisions_path=args.decisions_path,
            threshold=args.threshold,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_markdown(report), end="")
    if args.write_log:
        written = append_to_contradiction_log(args.project_root, report)
        if written is not None:
            print(f"appended unresolved findings to {written}", file=sys.stderr)
    return 1 if report.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
