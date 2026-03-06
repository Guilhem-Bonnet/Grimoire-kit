#!/usr/bin/env python3
"""
auto-doc.py — Synchronisation automatique de la documentation.
================================================================

Introspect le code et met à jour les sections structurées du README :
  - Nombre de tests et fichiers de test
  - Liste des outils CLI
  - Table des tests par fichier
  - Sections manquantes pour les nouveaux outils

Usage :
  python3 auto-doc.py --project-root . check    # vérifie le drift
  python3 auto-doc.py --project-root . sync      # met à jour README.md
  python3 auto-doc.py --project-root . check --json   # JSON output

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
import logging

_log = logging.getLogger("grimoire.auto_doc")

# ── Constantes ────────────────────────────────────────────────────────────────

AUTODOC_VERSION = "1.0.0"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DriftItem:
    """Un écart détecté entre le code et la doc."""
    section: str
    current: str
    expected: str
    line: int = 0
    auto_fixable: bool = True


@dataclass
class DocReport:
    """Rapport de synchronisation."""
    version: str = AUTODOC_VERSION
    readme_path: str = ""
    drifts: list[DriftItem] = field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return len(self.drifts)

    @property
    def fixable_count(self) -> int:
        return sum(1 for d in self.drifts if d.auto_fixable)


# ── Introspection ─────────────────────────────────────────────────────────────

def count_tests(project_root: Path) -> tuple[int, int]:
    """Compte les tests unitaires Python.

    Returns:
        (test_count, file_count)
    """
    tests_dir = project_root / "tests"
    if not tests_dir.is_dir():
        return 0, 0

    test_files = sorted(tests_dir.glob("test_*.py"))
    file_count = len(test_files)

    # Run unittest discover to get actual count
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", str(tests_dir), "-p", "test_*.py"],
            capture_output=True, text=True, timeout=120,
            cwd=str(project_root),
        )
        output = result.stderr + result.stdout
        match = re.search(r"Ran (\d+) test", output)
        if match:
            return int(match.group(1)), file_count
    except (subprocess.TimeoutExpired, FileNotFoundError) as _exc:
        _log.debug("subprocess.TimeoutExpired, FileNotFoundError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues

    # Fallback: count test methods
    count = 0
    for f in test_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            count += len(re.findall(r"def test_", text))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return count, file_count


def list_tools(project_root: Path) -> list[str]:
    """Liste les outils Python dans framework/tools/."""
    tools_dir = project_root / "framework" / "tools"
    if not tools_dir.is_dir():
        return []
    return sorted(f.stem for f in tools_dir.glob("*.py"))


def count_tests_per_file(project_root: Path) -> dict[str, int]:
    """Compte les tests par fichier de test."""
    tests_dir = project_root / "tests"
    if not tests_dir.is_dir():
        return {}

    result = {}
    for f in sorted(tests_dir.glob("test_*.py")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            count = len(re.findall(r"def test_", text))
            result[f.name] = count
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    return result


def get_tool_for_test(test_name: str) -> str:
    """Déduit le nom de l'outil depuis le fichier de test."""
    # test_dream.py → Dream Mode
    # test_nso.py → NSO
    mapping = {
        "test_python_tools.py": "Tous les outils (base)",
        "test_context_guard_advanced.py": "Context Guard avancé",
        "test_maintenance_advanced.py": "Maintenance mémoire",
        "test_agent_forge.py": "Agent Forge",
        "test_agent_bench.py": "Agent Bench",
        "test_dna_evolve.py": "DNA Evolve",
        "test_session_save.py": "Session Save",
        "test_gen_tests.py": "Gen Tests (scaffolding)",
        "test_dream.py": "Dream Mode",
        "test_adversarial_consensus.py": "Adversarial Consensus",
        "test_antifragile_score.py": "Anti-Fragile Score",
        "test_reasoning_stream.py": "Reasoning Stream",
        "test_cross_migrate.py": "Cross-Project Migration",
        "test_agent_darwinism.py": "Agent Darwinism",
        "test_stigmergy.py": "Stigmergy",
        "test_memory_lint.py": "Memory Lint",
        "test_nso.py": "NSO Orchestrator",
        "test_robustness.py": "Robustesse (fuzzing)",
    }
    return mapping.get(test_name, test_name.replace("test_", "").replace(".py", "").replace("_", " ").title())


# ── Drift detection ──────────────────────────────────────────────────────────

def detect_drifts(project_root: Path) -> DocReport:
    """Détecte les écarts entre le code et le README."""
    report = DocReport()

    readme_path = project_root / "README.md"
    if not readme_path.is_file():
        return report

    report.readme_path = str(readme_path)

    try:
        readme_text = readme_path.read_text(encoding="utf-8")
    except OSError:
        return report

    readme_lines = readme_text.splitlines()

    # 1. Test count drift
    actual_tests, actual_files = count_tests(project_root)
    if actual_tests > 0:
        # Find test count mentions in README
        for i, line in enumerate(readme_lines, 1):
            match = re.search(r"(\d+)\+?\s*tests", line, re.IGNORECASE)
            if match:
                stated = int(match.group(1))
                if stated != actual_tests:
                    report.drifts.append(DriftItem(
                        section="Test count",
                        current=f"{stated} tests",
                        expected=f"{actual_tests} tests",
                        line=i,
                    ))
            # File count
            match2 = re.search(r"(\d+)\s*fichiers?,?\s*(\d+)\s*tests", line, re.IGNORECASE)
            if match2:
                stated_files = int(match2.group(1))
                if stated_files != actual_files:
                    report.drifts.append(DriftItem(
                        section="Test file count",
                        current=f"{stated_files} fichiers",
                        expected=f"{actual_files} fichiers",
                        line=i,
                    ))

    # 2. Test table drift
    tests_per_file = count_tests_per_file(project_root)
    for i, line in enumerate(readme_lines, 1):
        match = re.search(r"\|\s*`?(test_\w+\.py)`?\s*\|.*?\|\s*(\d+)\s*\|", line)
        if match:
            fname = match.group(1)
            stated = int(match.group(2))
            actual = tests_per_file.get(fname)
            if actual is not None and actual != stated:
                report.drifts.append(DriftItem(
                    section=f"Test table: {fname}",
                    current=f"{stated}",
                    expected=f"{actual}",
                    line=i,
                ))

    # 3. Missing test files in table
    table_files = set()
    for line in readme_lines:
        match = re.search(r"\|\s*`?(test_\w+\.py)`?\s*\|", line)
        if match:
            table_files.add(match.group(1))

    for fname in tests_per_file:
        if fname not in table_files:
            report.drifts.append(DriftItem(
                section="Test table: missing entry",
                current="(absent)",
                expected=f"{fname} | {get_tool_for_test(fname)} | {tests_per_file[fname]}",
                line=0,
                auto_fixable=True,
            ))

    # 4. Tools mentioned in README
    tools = list_tools(project_root)
    readme_lower = readme_text.lower()
    for tool in tools:
        # Check if tool is mentioned anywhere in README
        tool_name = tool.replace("-", "[-_]?").replace("_", "[-_]?")
        if not re.search(tool_name, readme_lower):
            report.drifts.append(DriftItem(
                section=f"Tool not documented: {tool}",
                current="(absent)",
                expected=f"Tool '{tool}' devrait être mentionné dans le README",
                line=0,
                auto_fixable=False,
            ))

    return report


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_readme(project_root: Path) -> tuple[DocReport, int]:
    """Synchronise le README avec l'état réel du code.

    Returns:
        (report, changes_made)
    """
    report = detect_drifts(project_root)
    readme_path = project_root / "README.md"

    if not readme_path.is_file() or not report.drifts:
        return report, 0

    try:
        text = readme_path.read_text(encoding="utf-8")
    except OSError:
        return report, 0

    changes = 0
    actual_tests, actual_files = count_tests(project_root)
    tests_per_file = count_tests_per_file(project_root)

    # Fix test count mentions
    def _fix_test_count(m: re.Match) -> str:
        nonlocal changes
        stated = int(m.group(1))
        if stated != actual_tests:
            changes += 1
            return m.group(0).replace(m.group(1), str(actual_tests))
        return m.group(0)

    text = re.sub(r"(\d+)\+?\s*tests\)", _fix_test_count, text)
    text = re.sub(r"(\d+)\+?\s*tests\b", _fix_test_count, text)

    # Fix file count in combined pattern "N fichiers, M tests"
    def _fix_file_count(m: re.Match) -> str:
        nonlocal changes
        stated = int(m.group(1))
        if stated != actual_files:
            changes += 1
            return m.group(0).replace(m.group(1), str(actual_files), 1)
        return m.group(0)

    text = re.sub(r"(\d+)\s*fichiers?,?\s*\d+\s*tests",
                  _fix_file_count, text)

    # Fix individual test counts in table
    def _fix_table_row(m: re.Match) -> str:
        nonlocal changes
        fname = m.group(1)
        stated = int(m.group(2))
        actual = tests_per_file.get(fname, stated)
        if actual != stated:
            changes += 1
            return m.group(0).replace(str(stated), str(actual))
        return m.group(0)

    text = re.sub(
        r"\|\s*`?(test_\w+\.py)`?\s*\|.*?\|\s*(\d+)\s*\|",
        _fix_table_row, text,
    )

    # Add missing test files to table
    # Find the last row of the test table
    lines = text.splitlines()
    table_end_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\|\s*`?test_\w+\.py`?\s*\|", line):
            table_end_idx = i

    if table_end_idx is not None:
        table_files = set()
        for line in lines:
            m = re.search(r"\|\s*`?(test_\w+\.py)`?\s*\|", line)
            if m:
                table_files.add(m.group(1))

        new_rows = []
        for fname in sorted(tests_per_file):
            if fname not in table_files:
                tool_name = get_tool_for_test(fname)
                new_rows.append(
                    f"| `{fname}` | {tool_name} | {tests_per_file[fname]} |"
                )
                changes += 1

        if new_rows:
            for j, row in enumerate(new_rows):
                lines.insert(table_end_idx + 1 + j, row)
            text = "\n".join(lines)

    if changes > 0:
        readme_path.write_text(text, encoding="utf-8")

    return report, changes


# ── Rendu ─────────────────────────────────────────────────────────────────────

def render_report(report: DocReport, changes: int = 0) -> str:
    """Rend le rapport de sync en texte formaté."""
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║        📝 Auto-Doc Sync — README Drift Check               ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        f"  README    : {report.readme_path}",
        f"  Drifts    : {report.drift_count}",
        f"  Fixables  : {report.fixable_count}",
    ]

    if changes > 0:
        lines.append(f"  Corrigés  : {changes}")

    lines.append("")

    if not report.drifts:
        lines.append("  ✅ Documentation synchronisée — aucun drift détecté.")
        lines.append("")
        return "\n".join(lines)

    for drift in report.drifts:
        fixable = "🔧" if drift.auto_fixable else "📝"
        loc = f" (L{drift.line})" if drift.line > 0 else ""
        lines.append(f"  {fixable} {drift.section}{loc}")
        lines.append(f"     Actuel  : {drift.current}")
        lines.append(f"     Attendu : {drift.expected}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: DocReport, changes: int = 0) -> dict:
    """Convertit le rapport en dict JSON."""
    return {
        "version": report.version,
        "readme": report.readme_path,
        "drift_count": report.drift_count,
        "fixable_count": report.fixable_count,
        "changes_applied": changes,
        "drifts": [
            {
                "section": d.section,
                "current": d.current,
                "expected": d.expected,
                "line": d.line,
                "auto_fixable": d.auto_fixable,
            }
            for d in report.drifts
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BMAD Auto-Doc Sync — synchronise README avec le code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet BMAD")

    sub = parser.add_subparsers(dest="command", help="Commande")

    check_p = sub.add_parser("check", help="Vérifier le drift")
    check_p.add_argument("--json", action="store_true", help="Sortie JSON")

    sync_p = sub.add_parser("sync", help="Mettre à jour le README")
    sync_p.add_argument("--json", action="store_true", help="Sortie JSON")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "check":
        report = detect_drifts(project_root)
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2,
                             ensure_ascii=False))
        else:
            print(render_report(report))
        sys.exit(1 if report.drift_count > 0 else 0)

    if args.command == "sync":
        report, changes = sync_readme(project_root)
        if args.json:
            print(json.dumps(report_to_dict(report, changes), indent=2,
                             ensure_ascii=False))
        else:
            print(render_report(report, changes))
        sys.exit(0)


if __name__ == "__main__":
    main()
