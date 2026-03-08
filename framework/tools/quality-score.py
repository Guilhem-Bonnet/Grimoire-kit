#!/usr/bin/env python3
"""
quality-score.py — Runtime Quality Scoring for Grimoire agents (D7).
═══════════════════════════════════════════════════════════════════

Évalue la qualité des sorties d'agents sur plusieurs dimensions :
  - Complétude (toutes les sections requises sont-elles présentes)
  - Cohérence (pas de contradictions structurelles)
  - CC Compliance (le Completion Contract est-il respecté)
  - Format (structure Markdown / YAML valide)

Modes :
  score     — Évalue un fichier de sortie
  batch     — Évalue tous les artefacts d'un dossier
  threshold — Vérifie que le score dépasse un seuil

Usage :
  python3 quality-score.py --project-root . score path/to/output.md
  python3 quality-score.py --project-root . batch _grimoire-output/planning-artifacts/
  python3 quality-score.py --project-root . threshold path/to/output.md --min 70

MCP interface :
  mcp_quality_score(file_path, project_root) → {score, dimensions, details}

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

QUALITY_SCORE_VERSION = "1.0.0"

# ── Scoring dimensions ──────────────────────────────────────────

# Each dimension is scored 0-100.  Final score is weighted average.
DIMENSIONS = {
    "completeness": 0.30,
    "structure": 0.25,
    "cc_compliance": 0.25,
    "consistency": 0.20,
}


def _score_completeness(content: str, ext: str) -> tuple[int, list[str]]:
    """Évalue si les sections attendues sont présentes."""
    details: list[str] = []
    score = 100

    if ext == ".md":
        # Expect at least one heading
        headings = re.findall(r"^#+\s+.+", content, re.MULTILINE)
        if not headings:
            score -= 40
            details.append("No markdown headings found")
        elif len(headings) < 2:
            score -= 15
            details.append("Only 1 heading — may be incomplete")

        # Expect some content
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if len(lines) < 10:
            score -= 30
            details.append(f"Very short document ({len(lines)} non-empty lines)")
        elif len(lines) < 30:
            score -= 10
            details.append(f"Short document ({len(lines)} non-empty lines)")

        # Check for TODO/FIXME left behind
        todos = re.findall(r"\bTODO\b|\bFIXME\b|\bHACK\b", content)
        if todos:
            penalty = min(20, len(todos) * 5)
            score -= penalty
            details.append(f"{len(todos)} TODO/FIXME markers found")

    elif ext in (".yaml", ".yml"):
        lines = [ln for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if len(lines) < 3:
            score -= 40
            details.append(f"Very short YAML ({len(lines)} lines)")

    return max(0, score), details


def _score_structure(content: str, ext: str) -> tuple[int, list[str]]:
    """Évalue la qualité structurelle."""
    details: list[str] = []
    score = 100

    if ext == ".md":
        # Check heading hierarchy (no jumps from # to ###)
        headings = re.findall(r"^(#+)\s+", content, re.MULTILINE)
        levels = [len(h) for h in headings]
        for i in range(1, len(levels)):
            if levels[i] > levels[i - 1] + 1:
                score -= 10
                details.append(f"Heading jump: H{levels[i-1]} → H{levels[i]}")
                break

        # Check for unclosed code blocks
        fences = content.count("```")
        if fences % 2 != 0:
            score -= 20
            details.append("Unclosed code fence (odd number of ```)")

        # Check for very long lines (>200 chars, excluding URLs)
        for i, line in enumerate(content.splitlines(), 1):
            if len(line) > 200 and "http" not in line:
                score -= 5
                details.append(f"Very long line ({len(line)} chars) at line {i}")
                break

    elif ext in (".yaml", ".yml"):
        # Basic YAML structure check — look for indentation consistency
        indent_sizes: set[int] = set()
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                indent = len(line) - len(stripped)
                if indent > 0:
                    indent_sizes.add(indent)
        if len(indent_sizes) > 4:
            score -= 15
            details.append(f"Inconsistent indentation ({len(indent_sizes)} different indent levels)")

    return max(0, score), details


def _score_cc_compliance(content: str, _ext: str) -> tuple[int, list[str]]:
    """Évalue la conformité au Completion Contract."""
    details: list[str] = []
    score = 100

    # Look for CC markers
    cc_pass = bool(re.search(r"CC\s+PASS|✅\s*CC", content))
    cc_fail = bool(re.search(r"CC\s+FAIL|🔴\s*CC", content))

    if cc_fail:
        score -= 50
        details.append("CC FAIL marker found — output not validated")
    elif cc_pass:
        details.append("CC PASS marker found")
    else:
        # No CC marker — neutral (many outputs don't need CC)
        score -= 5
        details.append("No CC marker (may be expected for non-code artifacts)")

    # Check for placeholder/template markers left behind
    placeholders = re.findall(r"\{\{[^}]+\}\}|\{[A-Z_]+\}", content)
    if placeholders:
        unique = set(placeholders)
        score -= min(30, len(unique) * 10)
        details.append(f"{len(unique)} unresolved placeholder(s): {', '.join(list(unique)[:3])}")

    return max(0, score), details


def _score_consistency(content: str, ext: str) -> tuple[int, list[str]]:
    """Évalue la cohérence interne."""
    details: list[str] = []
    score = 100

    if ext == ".md":
        # Check for duplicate headings
        headings = re.findall(r"^#+\s+(.+)", content, re.MULTILINE)
        seen: dict[str, int] = {}
        for h in headings:
            h_lower = h.strip().lower()
            seen[h_lower] = seen.get(h_lower, 0) + 1
        dups = {h: c for h, c in seen.items() if c > 1}
        if dups:
            score -= min(20, len(dups) * 10)
            details.append(f"Duplicate headings: {', '.join(dups.keys())}")

        # Check for empty sections (heading immediately followed by heading)
        lines = content.splitlines()
        for i in range(len(lines) - 1):
            if re.match(r"^#+\s+", lines[i]) and re.match(r"^#+\s+", lines[i + 1]):
                score -= 5
                details.append(f"Empty section at line {i + 1}: {lines[i].strip()}")
                break

    return max(0, score), details


def score_artifact(file_path: Path) -> dict[str, Any]:
    """Score un artefact de sortie sur toutes les dimensions."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    ext = file_path.suffix.lower()

    scorers = {
        "completeness": _score_completeness,
        "structure": _score_structure,
        "cc_compliance": _score_cc_compliance,
        "consistency": _score_consistency,
    }

    results: dict[str, Any] = {
        "file": str(file_path.name),
        "dimensions": {},
        "details": [],
    }
    weighted_total = 0.0

    for dim, weight in DIMENSIONS.items():
        dim_score, dim_details = scorers[dim](content, ext)
        results["dimensions"][dim] = dim_score
        results["details"].extend(dim_details)
        weighted_total += dim_score * weight

    results["score"] = round(weighted_total)
    return results


# ── MCP Interface ────────────────────────────────────────────────

def mcp_quality_score(file_path: str, project_root: str = ".") -> dict[str, Any]:
    """MCP tool: score de qualité d'un artefact.

    Args:
        file_path: Chemin relatif ou absolu vers le fichier à évaluer.
        project_root: Racine du projet.

    Returns:
        {score, dimensions, details, file}
    """
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(project_root) / p
    if not p.exists():
        return {"error": f"File not found: {file_path}"}
    return score_artifact(p)


# ── Commands ─────────────────────────────────────────────────────

def cmd_score(args: argparse.Namespace) -> int:
    p = Path(args.file)
    if not p.is_absolute():
        p = Path(args.project_root) / p
    if not p.exists():
        print(f"  ❌ File not found: {args.file}")
        return 1

    result = score_artifact(p)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _display_score(result)
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    d = Path(args.directory)
    if not d.is_absolute():
        d = Path(args.project_root) / d
    if not d.exists():
        print(f"  ❌ Directory not found: {args.directory}")
        return 1

    files = sorted(d.glob("*.*"))
    results = []
    for f in files:
        if f.suffix.lower() in (".md", ".yaml", ".yml"):
            results.append(score_artifact(f))

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not results:
            print("  No scoreable artifacts found.")
            return 0
        print(f"\n  📊 Quality Scores — {len(results)} artifacts\n")
        for r in sorted(results, key=lambda x: x["score"]):
            bar = _score_bar(r["score"])
            print(f"  {bar} {r['score']:3d}/100  {r['file']}")
        avg = sum(r["score"] for r in results) / len(results)
        print(f"\n  Average: {avg:.0f}/100")
    return 0


def cmd_threshold(args: argparse.Namespace) -> int:
    p = Path(args.file)
    if not p.is_absolute():
        p = Path(args.project_root) / p
    if not p.exists():
        print(f"  ❌ File not found: {args.file}")
        return 1

    result = score_artifact(p)
    passed = result["score"] >= args.min

    if args.json:
        result["threshold"] = args.min
        result["passed"] = passed
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _display_score(result)
        if passed:
            print(f"  ✅ Score {result['score']} ≥ threshold {args.min}")
        else:
            print(f"  ❌ Score {result['score']} < threshold {args.min}")
    return 0 if passed else 1


def _score_bar(score: int) -> str:
    """Generate a visual score bar."""
    filled = score // 10
    empty = 10 - filled
    if score >= 80:
        icon = "🟢"
    elif score >= 60:
        icon = "🟡"
    else:
        icon = "🔴"
    return f"{icon} {'█' * filled}{'░' * empty}"


def _display_score(result: dict[str, Any]) -> None:
    """Display a score result in human-readable format."""
    print(f"\n  📊 Quality Score: {result['file']}\n")
    print(f"  Overall: {_score_bar(result['score'])} {result['score']}/100\n")
    for dim, val in result["dimensions"].items():
        weight = DIMENSIONS[dim]
        print(f"  {dim:20s} {val:3d}/100 (×{weight:.2f})")
    if result["details"]:
        print("\n  Details:")
        for d in result["details"]:
            print(f"    • {d}")
    print()


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Grimoire Quality Scoring")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_score = subs.add_parser("score", help="Score a single artifact")
    p_score.add_argument("file", help="File to score")
    p_score.set_defaults(func=cmd_score)

    p_batch = subs.add_parser("batch", help="Score all artifacts in a directory")
    p_batch.add_argument("directory", help="Directory to scan")
    p_batch.set_defaults(func=cmd_batch)

    p_thresh = subs.add_parser("threshold", help="Check score against threshold")
    p_thresh.add_argument("file", help="File to score")
    p_thresh.add_argument("--min", type=int, default=70, help="Minimum score (default: 70)")
    p_thresh.set_defaults(func=cmd_threshold)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
