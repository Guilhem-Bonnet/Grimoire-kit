#!/usr/bin/env python3
"""
code-review.py — Revue de code automatisée sur git diff.
==========================================================

Effectue une revue de code adversariale et structurée directement sur le
diff git. Va au-delà d'un simple linter : analyse la cohérence des
changements, détecte les patterns suspects, vérifie la couverture test.

Comparable au "Review Mode" de Cursor, mais avec plus de profondeur
(intégration failure-museum, immune-system, bug-finder).

Dimensions de revue :
  - Cohérence   : les changements sont-ils cohérents entre eux ?
  - Sécurité    : patterns de sécurité OWASP dans le diff
  - Tests       : les tests couvrent-ils les changements ?
  - Complexité  : les changements augmentent-ils la complexité ?
  - Conventions : respect des conventions du projet

Modes :
  review    — Revue complète du diff courant
  pr        — Revue d'un diff entre branches
  file      — Revue d'un fichier spécifique
  summary   — Résumé rapide des changements

Usage :
  python3 code-review.py --project-root . review
  python3 code-review.py --project-root . review --staged
  python3 code-review.py --project-root . pr --base main --head feature/auth
  python3 code-review.py --project-root . file src/auth.py
  python3 code-review.py --project-root . summary
  python3 code-review.py --project-root . review --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.code_review")

# ── Constantes ────────────────────────────────────────────────────────────────

CODE_REVIEW_VERSION = "1.0.0"

# Sévérités
class Severity:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

# Catégories de findings
class Category:
    SECURITY = "SECURITY"
    LOGIC = "LOGIC"
    TESTS = "TESTS"
    COMPLEXITY = "COMPLEXITY"
    CONVENTION = "CONVENTION"
    CONSISTENCY = "CONSISTENCY"

# Security patterns dans le diff (lignes ajoutées)
_SECURITY_PATTERNS: list[tuple[str, str, str]] = [
    (r"\beval\s*\(", "Appel eval() détecté", Severity.CRITICAL),
    (r"\bexec\s*\(", "Appel exec() détecté", Severity.CRITICAL),
    (r"subprocess\.(?:call|run|Popen)\s*\([^)]*shell\s*=\s*True",
     "subprocess avec shell=True — risque d'injection", Severity.HIGH),
    (r"os\.system\s*\(", "os.system() — préférer subprocess", Severity.HIGH),
    (r"""(?:password|secret|token|api_key)\s*=\s*["'][^"']{6,}["']""",
     "Secret potentiel hardcodé", Severity.CRITICAL),
    (r"# nosec|# noqa.*S", "Marqueur de suppression de sécurité", Severity.MEDIUM),
    (r"\.format\(.*input|%s.*input", "String formatting avec input utilisateur potentiel", Severity.MEDIUM),
    (r"pickle\.load", "Désérialisation pickle — risque d'exécution de code", Severity.HIGH),
    (r"yaml\.load\([^)]*\)(?!.*Loader)", "yaml.load sans Loader explicite", Severity.MEDIUM),
    (r"verify\s*=\s*False", "Vérification TLS désactivée", Severity.HIGH),
    (r"chmod\s*\(\s*0?o?777", "Permissions trop permissives (777)", Severity.HIGH),
]

# Test file patterns
_TEST_PATTERNS = re.compile(r"(?:test_|_test\.py|tests/|spec\.)")

# Max lines in a single function for complexity
_MAX_FUNCTION_LINES = 50
_MAX_ADDED_LINES_NO_TEST = 30  # Si plus de N lignes ajoutées sans test → finding

# Files that don't need test coverage
_TEST_EXEMPT = {".md", ".yaml", ".yml", ".toml", ".json", ".txt", ".cfg", ".ini", ".csv"}


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class DiffFile:
    """Un fichier dans le diff."""
    path: str
    status: str  # A(dded), M(odified), D(eleted), R(enamed)
    added_lines: int = 0
    removed_lines: int = 0
    hunks: list[str] = field(default_factory=list)
    added_content: list[str] = field(default_factory=list)  # Lignes ajoutées (sans le +)

    @property
    def is_test(self) -> bool:
        return bool(_TEST_PATTERNS.search(self.path))

    @property
    def ext(self) -> str:
        return Path(self.path).suffix


@dataclass
class Finding:
    """Un finding de revue de code."""
    rule_id: str
    category: str
    severity: str
    file: str
    line: int
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v or k in ("line",)}


@dataclass
class ReviewReport:
    """Rapport de revue de code."""
    diff_files: list[DiffFile] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def total_added(self) -> int:
        return sum(f.added_lines for f in self.diff_files)

    @property
    def total_removed(self) -> int:
        return sum(f.removed_lines for f in self.diff_files)

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    @property
    def by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.category] = counts.get(f.category, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "version": CODE_REVIEW_VERSION,
            "files_changed": len(self.diff_files),
            "total_added": self.total_added,
            "total_removed": self.total_removed,
            "total_findings": self.total_findings,
            "by_severity": self.by_severity,
            "by_category": self.by_category,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "duration_ms": self.duration_ms,
        }


# ── Git Diff Parser ──────────────────────────────────────────────────────────

def parse_diff_stat(project_root: Path, base: str | None = None,
                    head: str | None = None, staged: bool = False) -> list[DiffFile]:
    """Parse les fichiers modifiés depuis git."""
    cmd = ["git", "diff", "--numstat", "--diff-filter=ACDMR"]
    if staged:
        cmd.append("--cached")
    if base and head:
        cmd.extend([f"{base}...{head}"])
    elif base:
        cmd.append(base)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if result.returncode != 0:
            _log.warning("git diff failed: %s", result.stderr.strip())
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    files: list[DiffFile] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0] != "-" else 0
        removed = int(parts[1]) if parts[1] != "-" else 0
        path = parts[2]

        # Déterminer le statut
        status = "M"
        if added > 0 and removed == 0:
            status = "A"
        elif added == 0 and removed > 0:
            status = "D"

        files.append(DiffFile(
            path=path, status=status,
            added_lines=added, removed_lines=removed,
        ))

    return files


def get_diff_content(project_root: Path, filepath: str,
                     base: str | None = None, head: str | None = None,
                     staged: bool = False) -> list[str]:
    """Récupère les lignes ajoutées dans le diff pour un fichier."""
    cmd = ["git", "diff", "-U0"]
    if staged:
        cmd.append("--cached")
    if base and head:
        cmd.extend([f"{base}...{head}"])
    elif base:
        cmd.append(base)
    cmd.append("--")
    cmd.append(filepath)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    added: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])  # Retirer le +

    return added


# ── Review Analyzers ─────────────────────────────────────────────────────────

def check_security(diff_file: DiffFile) -> list[Finding]:
    """Vérifie les patterns de sécurité dans les lignes ajoutées."""
    findings: list[Finding] = []
    for i, line in enumerate(diff_file.added_content, 1):
        for pattern, message, severity in _SECURITY_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(Finding(
                    rule_id="CR-SEC",
                    category=Category.SECURITY,
                    severity=severity,
                    file=diff_file.path,
                    line=i,
                    message=message,
                    suggestion="Vérifier et corriger avant merge",
                ))
    return findings


def check_test_coverage(diff_files: list[DiffFile]) -> list[Finding]:
    """Vérifie que les fichiers modifiés ont des tests correspondants."""
    findings: list[Finding] = []

    source_files = [f for f in diff_files if not f.is_test and f.ext not in _TEST_EXEMPT]
    test_files = {f.path for f in diff_files if f.is_test}

    for sf in source_files:
        if sf.added_lines < _MAX_ADDED_LINES_NO_TEST:
            continue

        # Chercher un test correspondant
        stem = Path(sf.path).stem
        has_test = any(
            stem in tf or f"test_{stem}" in tf
            for tf in test_files
        )

        if not has_test:
            findings.append(Finding(
                rule_id="CR-TEST",
                category=Category.TESTS,
                severity=Severity.MEDIUM,
                file=sf.path,
                line=0,
                message=f"{sf.added_lines} lignes ajoutées sans test correspondant dans le diff",
                suggestion=f"Ajouter ou modifier test_{stem}.py",
            ))

    return findings


def check_complexity(diff_file: DiffFile) -> list[Finding]:
    """Vérifie la complexité des changements."""
    findings: list[Finding] = []

    # Grande quantité de changements dans un seul fichier
    if diff_file.added_lines > 200:
        findings.append(Finding(
            rule_id="CR-SIZE",
            category=Category.COMPLEXITY,
            severity=Severity.MEDIUM,
            file=diff_file.path,
            line=0,
            message=f"Fichier avec {diff_file.added_lines} lignes ajoutées — changement très large",
            suggestion="Envisager de découper en commits/PRs plus petits",
        ))

    # Détecter les fonctions longues dans les lignes ajoutées
    in_function = False
    func_name = ""
    func_lines = 0
    func_start = 0

    for i, line in enumerate(diff_file.added_content, 1):
        func_match = re.match(r"^\s*(?:async\s+)?def\s+(\w+)", line)
        if func_match:
            # Flush previous function
            if in_function and func_lines > _MAX_FUNCTION_LINES:
                findings.append(Finding(
                    rule_id="CR-FUNC",
                    category=Category.COMPLEXITY,
                    severity=Severity.LOW,
                    file=diff_file.path,
                    line=func_start,
                    message=f"Fonction `{func_name}` ajoutée avec {func_lines} lignes",
                    suggestion="Décomposer en sous-fonctions",
                ))
            in_function = True
            func_name = func_match.group(1)
            func_lines = 0
            func_start = i

        if in_function:
            func_lines += 1

    # Flush last function
    if in_function and func_lines > _MAX_FUNCTION_LINES:
        findings.append(Finding(
            rule_id="CR-FUNC",
            category=Category.COMPLEXITY,
            severity=Severity.LOW,
            file=diff_file.path,
            line=func_start,
            message=f"Fonction `{func_name}` ajoutée avec {func_lines} lignes",
            suggestion="Décomposer en sous-fonctions",
        ))

    return findings


def check_conventions(diff_file: DiffFile) -> list[Finding]:
    """Vérifie les conventions de code."""
    findings: list[Finding] = []

    for i, line in enumerate(diff_file.added_content, 1):
        # Debug prints laissés
        if re.match(r"^\s*print\s*\(", line) and not diff_file.is_test:
            findings.append(Finding(
                rule_id="CR-PRINT",
                category=Category.CONVENTION,
                severity=Severity.LOW,
                file=diff_file.path,
                line=i,
                message="print() dans du code non-test — utiliser logging",
                suggestion="Remplacer par _log.debug() ou _log.info()",
            ))

        # Commentaires TODO/FIXME ajoutés
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", line):
            findings.append(Finding(
                rule_id="CR-TODO",
                category=Category.CONVENTION,
                severity=Severity.INFO,
                file=diff_file.path,
                line=i,
                message="TODO/FIXME ajouté — créer un ticket",
            ))

    return findings


def check_consistency(diff_files: list[DiffFile]) -> list[Finding]:
    """Vérifie la cohérence globale des changements."""
    findings: list[Finding] = []

    # Config modifiée sans doc
    config_changed = any(
        f.ext in (".yaml", ".yml", ".toml", ".json") and not f.is_test
        for f in diff_files
    )
    doc_changed = any(f.ext == ".md" for f in diff_files)

    if config_changed and not doc_changed:
        config_files = [f.path for f in diff_files if f.ext in (".yaml", ".yml", ".toml", ".json")]
        findings.append(Finding(
            rule_id="CR-DOC",
            category=Category.CONSISTENCY,
            severity=Severity.LOW,
            file=config_files[0] if config_files else "",
            line=0,
            message="Configuration modifiée sans mise à jour de documentation",
            suggestion="Mettre à jour le README ou la doc associée",
        ))

    # Détection de fichiers orphelins (ajoutés sans import/référence)
    added_files = [f for f in diff_files if f.status == "A" and f.ext == ".py"]
    if len(added_files) > 3:
        findings.append(Finding(
            rule_id="CR-BATCH",
            category=Category.CONSISTENCY,
            severity=Severity.INFO,
            file="",
            line=0,
            message=f"{len(added_files)} nouveaux fichiers Python ajoutés — vérifier la cohérence",
            suggestion="S'assurer que tous les fichiers sont référencés/importés",
        ))

    return findings


# ── Review Orchestrator ──────────────────────────────────────────────────────

def run_review(project_root: Path, base: str | None = None,
               head: str | None = None, staged: bool = False,
               target_file: str | None = None) -> ReviewReport:
    """Exécuter une revue de code complète."""
    start = time.monotonic()
    report = ReviewReport()

    # 1. Récupérer les fichiers modifiés
    diff_files = parse_diff_stat(project_root, base, head, staged)

    if target_file:
        diff_files = [f for f in diff_files if f.path == target_file]

    # 2. Récupérer le contenu ajouté pour chaque fichier
    for df in diff_files:
        df.added_content = get_diff_content(project_root, df.path, base, head, staged)

    report.diff_files = diff_files

    # 3. Exécuter les analyses
    for df in diff_files:
        report.findings.extend(check_security(df))
        report.findings.extend(check_complexity(df))
        report.findings.extend(check_conventions(df))

    # 4. Analyses globales
    report.findings.extend(check_test_coverage(diff_files))
    report.findings.extend(check_consistency(diff_files))

    # 5. Summary
    source_files = [f for f in diff_files if not f.is_test and f.ext not in _TEST_EXEMPT]
    test_files = [f for f in diff_files if f.is_test]

    report.summary = {
        "source_files_changed": len(source_files),
        "test_files_changed": len(test_files),
        "total_added": report.total_added,
        "total_removed": report.total_removed,
        "test_ratio": f"{len(test_files)}/{len(source_files)}" if source_files else "N/A",
    }

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


# ── Display ───────────────────────────────────────────────────────────────────

def format_review(report: ReviewReport, as_json: bool = False) -> str:
    """Formatter le rapport de revue."""
    if as_json:
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    lines: list[str] = []
    lines.append("\n  🔍 Code Review Report")
    lines.append(f"  {'─' * 55}")
    lines.append(f"  Fichiers modifiés : {len(report.diff_files)}")
    lines.append(f"  Lignes ajoutées   : +{report.total_added}")
    lines.append(f"  Lignes retirées   : -{report.total_removed}")
    lines.append(f"  Findings          : {report.total_findings}")

    if report.summary.get("test_ratio"):
        lines.append(f"  Ratio test/src    : {report.summary['test_ratio']}")

    lines.append(f"  Durée             : {report.duration_ms}ms")

    # Fichiers modifiés
    if report.diff_files:
        lines.append("\n  📁 Fichiers")
        lines.append(f"  {'─' * 55}")
        for df in sorted(report.diff_files, key=lambda x: x.path):
            status_icon = {"A": "🆕", "M": "✏️", "D": "🗑️"}.get(df.status, "❓")
            test_icon = " 🧪" if df.is_test else ""
            lines.append(f"  {status_icon} {df.path}{test_icon}  (+{df.added_lines}/-{df.removed_lines})")

    # Findings
    if report.findings:
        lines.append("\n  ⚠️  Findings")
        lines.append(f"  {'─' * 55}")

        severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                          Severity.LOW: 3, Severity.INFO: 4}
        sorted_findings = sorted(report.findings,
                                 key=lambda f: severity_order.get(f.severity, 5))

        icons = {
            Severity.CRITICAL: "🔴", Severity.HIGH: "🔴",
            Severity.MEDIUM: "🟡", Severity.LOW: "🟢", Severity.INFO: "ℹ️",
        }

        for f in sorted_findings:
            icon = icons.get(f.severity, "⚪")
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"  {icon} [{f.rule_id}][{f.severity}] {f.message}")
            if loc:
                lines.append(f"      📍 {loc}")
            if f.suggestion:
                lines.append(f"      💡 {f.suggestion}")
    else:
        lines.append("\n  ✅ Aucun finding — code propre !")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Code Review — Revue de code automatisée Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path(),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--version", action="version", version=f"code-review {CODE_REVIEW_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # review
    review_p = sub.add_parser("review", help="Revue complète du diff courant")
    review_p.add_argument("--staged", action="store_true", help="Uniquement les changements staged")

    # pr
    pr_p = sub.add_parser("pr", help="Revue d'un diff entre branches")
    pr_p.add_argument("--base", required=True, help="Branche de base")
    pr_p.add_argument("--head", default="HEAD", help="Branche HEAD (défaut: HEAD)")

    # file
    file_p = sub.add_parser("file", help="Revue d'un fichier spécifique")
    file_p.add_argument("target", help="Fichier à reviewer")

    # summary
    sub.add_parser("summary", help="Résumé rapide des changements")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    as_json = getattr(args, "json", False)

    if args.command == "review":
        report = run_review(project_root, staged=getattr(args, "staged", False))
        print(format_review(report, as_json))

    elif args.command == "pr":
        report = run_review(project_root, base=args.base, head=args.head)
        print(format_review(report, as_json))

    elif args.command == "file":
        report = run_review(project_root, target_file=args.target)
        print(format_review(report, as_json))

    elif args.command == "summary":
        report = run_review(project_root)
        print(f"\n  📊 Résumé: {len(report.diff_files)} fichiers, "
              f"+{report.total_added}/-{report.total_removed}, "
              f"{report.total_findings} findings\n")


if __name__ == "__main__":
    main()
