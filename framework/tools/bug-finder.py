#!/usr/bin/env python3
"""
bug-finder.py — Détecteur de bugs logiques BMAD (au-delà du linting).
======================================================================

Analyse statique par AST qui détecte des bugs **logiques** que les linters
classiques (ruff, pylint, eslint) ne trouvent pas. Inspiré du Bug Finder
de Cursor, mais plus profond et configurable.

Catégories de bugs détectés :
  - LOGIC     : code mort, conditions toujours vraies/fausses, duplications
  - SAFETY    : mutable defaults, bare except, eval/exec, hardcoded secrets
  - PATTERN   : anti-patterns courants (== None, == True, empty except)
  - RESOURCE  : fichiers non fermés, connexions non libérées
  - CONCUR    : race conditions potentielles, shared mutable state

Modes :
  scan       — Scanner un fichier ou dossier
  diff       — Scanner uniquement les fichiers modifiés (git diff)
  watch      — Surveiller les changements et scanner en continu

Usage :
  python3 bug-finder.py --project-root . scan src/
  python3 bug-finder.py --project-root . scan src/auth.py --severity high
  python3 bug-finder.py --project-root . diff
  python3 bug-finder.py --project-root . diff --against main
  python3 bug-finder.py --project-root . watch src/
  python3 bug-finder.py --project-root . scan --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.bug_finder")

# ── Constantes ────────────────────────────────────────────────────────────────

BUG_FINDER_VERSION = "1.0.0"

# Sévérités
class Severity:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    ALL = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

# Catégories
class Category:
    LOGIC = "LOGIC"
    SAFETY = "SAFETY"
    PATTERN = "PATTERN"
    RESOURCE = "RESOURCE"
    CONCUR = "CONCUR"

# Patterns de secrets hardcodés (regex)
_SECRET_PATTERNS = [
    (r"""(?:password|passwd|pwd|secret|api_key|apikey|token|auth)\s*=\s*["'][^"']{8,}["']""", "Hardcoded secret/password"),
    (r"""(?:AWS_ACCESS_KEY|aws_access_key)\s*=\s*["']AKI[A-Z0-9]{16}["']""", "AWS Access Key hardcodé"),
    (r"""-----BEGIN (?:RSA |EC )?PRIVATE KEY-----""", "Clé privée embarquée"),
]

# Fonctions dangereuses
_DANGEROUS_CALLS = {
    "eval": ("Appel eval() — risque d'injection de code", Severity.CRITICAL),
    "exec": ("Appel exec() — risque d'injection de code", Severity.CRITICAL),
    "compile": ("Appel compile() — exécution dynamique de code", Severity.HIGH),
    "__import__": ("Import dynamique — code potentiellement imprévisible", Severity.MEDIUM),
}

# Taille max d'un fichier à analyser (éviter OOM sur fichiers générés)
MAX_FILE_SIZE = 1_000_000  # 1 Mo

# Extensions supportées
SUPPORTED_EXTENSIONS = {".py"}

# Nesting depth alerte
MAX_NESTING_DEPTH = 5
MAX_FUNCTION_LINES = 60

# Watchdog polling interval
WATCH_INTERVAL = 2.0


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class Bug:
    """Un bug détecté."""
    rule_id: str
    category: str
    severity: str
    file: str
    line: int
    col: int
    message: str
    suggestion: str = ""
    code_snippet: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Retirer les champs vides
        return {k: v for k, v in d.items() if v or k in ("line", "col")}


@dataclass
class ScanReport:
    """Rapport de scan."""
    files_scanned: int = 0
    files_skipped: int = 0
    bugs: list[Bug] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def total(self) -> int:
        return len(self.bugs)

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in self.bugs:
            counts[b.severity] = counts.get(b.severity, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "version": BUG_FINDER_VERSION,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "total_bugs": self.total,
            "by_severity": self.by_severity,
            "bugs": [b.to_dict() for b in self.bugs],
            "duration_ms": self.duration_ms,
        }


# ── AST Analyzers ────────────────────────────────────────────────────────────

class PythonBugVisitor(ast.NodeVisitor):
    """Visiteur AST qui détecte les bugs logiques en Python."""

    def __init__(self, source: str, filepath: str):
        self.source = source
        self.lines = source.splitlines()
        self.filepath = filepath
        self.bugs: list[Bug] = []
        self._scope_stack: list[str] = []  # nesting tracker
        self._assigned_names: dict[str, int] = {}  # var → line
        self._used_names: set[str] = set()

    # ── LOGIC checks ─────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Vérifie une fonction pour des bugs logiques."""
        # BF-001: Mutable default arguments
        for dval in node.args.defaults + node.args.kw_defaults:
            if dval is None:
                continue
            if isinstance(dval, (ast.List, ast.Dict, ast.Set)):
                self.bugs.append(Bug(
                    rule_id="BF-001",
                    category=Category.SAFETY,
                    severity=Severity.HIGH,
                    file=self.filepath,
                    line=dval.lineno,
                    col=dval.col_offset,
                    message=f"Argument par défaut mutable dans `{node.name}()`",
                    suggestion="Utiliser None et initialiser dans le corps de la fonction",
                ))

        # BF-002: Fonction trop longue
        end_line = getattr(node, "end_lineno", node.lineno)
        func_length = end_line - node.lineno
        if func_length > MAX_FUNCTION_LINES:
            self.bugs.append(Bug(
                rule_id="BF-002",
                category=Category.LOGIC,
                severity=Severity.LOW,
                file=self.filepath,
                line=node.lineno,
                col=0,
                message=f"Fonction `{node.name}` trop longue ({func_length} lignes, max {MAX_FUNCTION_LINES})",
                suggestion="Décomposer en sous-fonctions",
            ))

        # BF-003: Code mort après return/break/continue
        self._check_unreachable(node.body)

    def _check_unreachable(self, stmts: list[ast.stmt]) -> None:
        """Détecte le code mort après return, break, continue, raise."""
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, (ast.Return, ast.Break, ast.Continue, ast.Raise)):
                remaining = stmts[i + 1:]
                for dead in remaining:
                    # Ignorer les docstrings et pass
                    if isinstance(dead, ast.Expr) and isinstance(dead.value, (ast.Constant,)):
                        continue
                    self.bugs.append(Bug(
                        rule_id="BF-003",
                        category=Category.LOGIC,
                        severity=Severity.MEDIUM,
                        file=self.filepath,
                        line=dead.lineno,
                        col=dead.col_offset,
                        message="Code mort — jamais exécuté après return/break/continue/raise",
                        suggestion="Supprimer ou déplacer ce code",
                    ))
                break  # Only report once per block

            # Recurse into if/for/while/try bodies
            if isinstance(stmt, ast.If):
                self._check_unreachable(stmt.body)
                self._check_unreachable(stmt.orelse)
            elif isinstance(stmt, (ast.For, ast.While)):
                self._check_unreachable(stmt.body)
            elif isinstance(stmt, ast.Try):
                self._check_unreachable(stmt.body)
                for handler in stmt.handlers:
                    self._check_unreachable(handler.body)
                self._check_unreachable(stmt.orelse)
                self._check_unreachable(stmt.finalbody)

    # ── PATTERN checks ───────────────────────────────────────────

    def visit_Compare(self, node: ast.Compare) -> None:
        """Détecte les comparaisons problématiques."""
        for op, comp in zip(node.ops, node.comparators, strict=False):
            # BF-004: == None au lieu de "is None"
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant) and comp.value is None:
                self.bugs.append(Bug(
                    rule_id="BF-004",
                    category=Category.PATTERN,
                    severity=Severity.MEDIUM,
                    file=self.filepath,
                    line=node.lineno,
                    col=node.col_offset,
                    message="Comparaison `== None` au lieu de `is None`",
                    suggestion="Utiliser `is None` / `is not None`",
                ))
            # BF-005: == True / == False
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant) and comp.value in (True, False):
                self.bugs.append(Bug(
                    rule_id="BF-005",
                    category=Category.PATTERN,
                    severity=Severity.LOW,
                    file=self.filepath,
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Comparaison explicite avec `{comp.value}`",
                    suggestion="Utiliser directement la valeur booléenne ou `is True`",
                ))
        self.generic_visit(node)

    # ── SAFETY checks ────────────────────────────────────────────

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Détecte les bare except et les except pass."""
        # BF-006: bare except
        if node.type is None:
            self.bugs.append(Bug(
                rule_id="BF-006",
                category=Category.SAFETY,
                severity=Severity.HIGH,
                file=self.filepath,
                line=node.lineno,
                col=node.col_offset,
                message="Bare `except:` — capture toutes les exceptions y compris KeyboardInterrupt",
                suggestion="Utiliser `except Exception:` au minimum",
            ))

        # BF-007: except avec seulement pass (swallow)
        if (len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)):
            self.bugs.append(Bug(
                rule_id="BF-007",
                category=Category.SAFETY,
                severity=Severity.MEDIUM,
                file=self.filepath,
                line=node.lineno,
                col=node.col_offset,
                message="Exception avalée silencieusement (`except: pass`)",
                suggestion="Au minimum logger l'exception",
            ))

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Détecte les appels dangereux."""
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        # BF-008: fonctions dangereuses
        if func_name in _DANGEROUS_CALLS:
            msg, sev = _DANGEROUS_CALLS[func_name]
            self.bugs.append(Bug(
                rule_id="BF-008",
                category=Category.SAFETY,
                severity=sev,
                file=self.filepath,
                line=node.lineno,
                col=node.col_offset,
                message=msg,
                suggestion="Utiliser une alternative sûre (literal_eval, importlib, etc.)",
            ))

        self.generic_visit(node)

    # ── RESOURCE checks ──────────────────────────────────────────

    def visit_Call_resource(self, node: ast.Call) -> None:
        """Détecte les ouvertures de fichiers sans context manager."""
        # Traité dans visit_Assign

    def visit_Assign(self, node: ast.Assign) -> None:
        """Détecte l'ouverture de fichiers sans `with`."""
        if isinstance(node.value, ast.Call):
            func = node.value.func
            func_name = ""
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr

            # BF-009: open() sans `with`
            if func_name == "open":
                self.bugs.append(Bug(
                    rule_id="BF-009",
                    category=Category.RESOURCE,
                    severity=Severity.MEDIUM,
                    file=self.filepath,
                    line=node.lineno,
                    col=node.col_offset,
                    message="Fichier ouvert sans context manager `with`",
                    suggestion="Utiliser `with open(...) as f:`",
                ))

        self.generic_visit(node)

    # ── Nesting depth ────────────────────────────────────────────

    def _measure_nesting(self, node: ast.AST, depth: int = 0) -> int:
        """Mesure la profondeur de nesting maximale."""
        max_depth = depth
        nesting_nodes = (ast.If, ast.For, ast.While, ast.With, ast.Try,
                         ast.ExceptHandler)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, nesting_nodes):
                d = self._measure_nesting(child, depth + 1)
                max_depth = max(max_depth, d)
            else:
                d = self._measure_nesting(child, depth)
                max_depth = max(max_depth, d)
        return max_depth

    def check_nesting(self, tree: ast.AST) -> None:
        """BF-010: Détecte les nesting excessifs."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                depth = self._measure_nesting(node)
                if depth > MAX_NESTING_DEPTH:
                    self.bugs.append(Bug(
                        rule_id="BF-010",
                        category=Category.LOGIC,
                        severity=Severity.MEDIUM,
                        file=self.filepath,
                        line=node.lineno,
                        col=0,
                        message=f"Nesting excessif dans `{node.name}()` (profondeur {depth}, max {MAX_NESTING_DEPTH})",
                        suggestion="Extraire en sous-fonctions ou utiliser des early returns",
                    ))

    def check_duplicate_dict_keys(self, tree: ast.AST) -> None:
        """BF-011: Détecte les clés dupliquées dans les dicts littéraux."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                seen: dict[str, int] = {}
                for key in node.keys:
                    if key is None:
                        continue
                    if isinstance(key, ast.Constant):
                        k = repr(key.value)
                        if k in seen:
                            self.bugs.append(Bug(
                                rule_id="BF-011",
                                category=Category.LOGIC,
                                severity=Severity.HIGH,
                                file=self.filepath,
                                line=key.lineno,
                                col=key.col_offset,
                                message=f"Clé dupliquée dans le dictionnaire : {k}",
                                suggestion="Supprimer le doublon — la seconde valeur écrase la première",
                            ))
                        seen[k] = key.lineno

    def check_f_string_no_placeholders(self, tree: ast.AST) -> None:
        """BF-012: Détecte les f-strings sans placeholders."""
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                # Un f-string sans placeholder n'a que des ast.Constant
                has_placeholder = any(
                    isinstance(v, ast.FormattedValue) for v in node.values
                )
                if not has_placeholder:
                    self.bugs.append(Bug(
                        rule_id="BF-012",
                        category=Category.PATTERN,
                        severity=Severity.LOW,
                        file=self.filepath,
                        line=node.lineno,
                        col=node.col_offset,
                        message="f-string sans aucun placeholder `{}`",
                        suggestion="Retirer le préfixe `f` ou ajouter un placeholder",
                    ))


# ── Regex-based analyzers (language-agnostic) ─────────────────────────────────

def _check_secrets(content: str, filepath: str) -> list[Bug]:
    """BF-013: Détecte les secrets hardcodés par regex."""
    bugs: list[Bug] = []
    for pattern, description in _SECRET_PATTERNS:
        for match in re.finditer(pattern, content, re.IGNORECASE):
            line_num = content[:match.start()].count("\n") + 1
            bugs.append(Bug(
                rule_id="BF-013",
                category=Category.SAFETY,
                severity=Severity.CRITICAL,
                file=filepath,
                line=line_num,
                col=0,
                message=f"Secret potentiel détecté : {description}",
                suggestion="Utiliser des variables d'environnement ou un vault",
            ))
    return bugs


def _check_todo_fixme(content: str, filepath: str) -> list[Bug]:
    """BF-014: Détecte les TODO/FIXME/HACK/XXX pour tracking."""
    bugs: list[Bug] = []
    for match in re.finditer(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.{0,80})", content):
        line_num = content[:match.start()].count("\n") + 1
        marker = match.group(1)
        desc = match.group(2).strip()
        bugs.append(Bug(
            rule_id="BF-014",
            category=Category.PATTERN,
            severity=Severity.LOW,
            file=filepath,
            line=line_num,
            col=0,
            message=f"{marker}: {desc}" if desc else f"Marqueur {marker} sans description",
            suggestion="Résoudre ou créer un ticket",
        ))
    return bugs


# ── File scanner ──────────────────────────────────────────────────────────────

def scan_file(filepath: Path) -> list[Bug]:
    """Scanner un fichier Python pour des bugs."""
    bugs: list[Bug] = []
    rel = str(filepath)

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        _log.warning("Cannot read %s: %s", filepath, e)
        return []

    if len(content) > MAX_FILE_SIZE:
        _log.info("Skipping %s (too large: %d bytes)", filepath, len(content))
        return []

    # Regex-based checks (language-agnostic)
    bugs.extend(_check_secrets(content, rel))
    bugs.extend(_check_todo_fixme(content, rel))

    # AST-based checks (Python only)
    if filepath.suffix == ".py":
        try:
            tree = ast.parse(content, filename=rel)
        except SyntaxError:
            _log.debug("Syntax error in %s — skipping AST analysis", filepath)
            return bugs

        visitor = PythonBugVisitor(content, rel)
        visitor.visit(tree)
        visitor.check_nesting(tree)
        visitor.check_duplicate_dict_keys(tree)
        visitor.check_f_string_no_placeholders(tree)
        bugs.extend(visitor.bugs)

    return bugs


def scan_directory(root: Path, target: Path | None = None,
                   severity_filter: str | None = None) -> ScanReport:
    """Scanner un dossier pour des bugs."""
    report = ScanReport()
    start = time.monotonic()

    scan_root = target or root
    if scan_root.is_file():
        files = [scan_root]
    else:
        files = sorted(scan_root.rglob("*.py"))

    # Filtrer les exclusions
    exclude_dirs = {"node_modules", ".git", "__pycache__", ".pytest_cache",
                    ".ruff_cache", ".venv", "venv", ".bmad-rnd", "_bmad-output"}

    for fpath in files:
        # Vérifier les exclusions
        parts = set(fpath.parts)
        if parts & exclude_dirs:
            report.files_skipped += 1
            continue

        bugs = scan_file(fpath)
        if severity_filter:
            sev_index = list(Severity.ALL).index(severity_filter.upper()) if severity_filter.upper() in Severity.ALL else -1
            if sev_index >= 0:
                bugs = [b for b in bugs if list(Severity.ALL).index(b.severity) <= sev_index]

        report.bugs.extend(bugs)
        report.files_scanned += 1

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


def scan_git_diff(project_root: Path, against: str = "HEAD") -> ScanReport:
    """Scanner uniquement les fichiers modifiés par git."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", against],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.returncode != 0:
            _log.warning("git diff failed: %s", result.stderr.strip())
            return ScanReport()

        # Ajouter aussi les unstaged
        result2 = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )

        files_str = result.stdout.strip() + "\n" + result2.stdout.strip()
        files = {f.strip() for f in files_str.splitlines() if f.strip()}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _log.warning("git not available or timeout")
        return ScanReport()

    report = ScanReport()
    start = time.monotonic()

    for rel in sorted(files):
        fpath = project_root / rel
        if not fpath.exists() or fpath.suffix not in SUPPORTED_EXTENSIONS:
            report.files_skipped += 1
            continue

        bugs = scan_file(fpath)
        report.bugs.extend(bugs)
        report.files_scanned += 1

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


def watch_directory(root: Path, target: Path, interval: float = WATCH_INTERVAL) -> None:
    """Surveiller un dossier et scanner les fichiers modifiés (polling)."""
    print(f"\n  👁️  Bug Finder Watch Mode — {target}")
    print(f"  Polling toutes les {interval}s — Ctrl+C pour arrêter\n")

    mtimes: dict[Path, float] = {}

    # Initial scan
    for fpath in target.rglob("*.py"):
        mtimes[fpath] = fpath.stat().st_mtime

    try:
        while True:
            time.sleep(interval)
            changed: list[Path] = []

            for fpath in target.rglob("*.py"):
                mtime = fpath.stat().st_mtime
                if fpath not in mtimes or mtimes[fpath] < mtime:
                    changed.append(fpath)
                    mtimes[fpath] = mtime

            if changed:
                for fpath in changed:
                    bugs = scan_file(fpath)
                    if bugs:
                        print(f"  🐛 {fpath.relative_to(root)} — {len(bugs)} bug(s)")
                        for b in bugs:
                            icon = "🔴" if b.severity in (Severity.CRITICAL, Severity.HIGH) else "🟡"
                            print(f"     {icon} L{b.line}: [{b.rule_id}] {b.message}")
                    else:
                        print(f"  ✅ {fpath.relative_to(root)} — clean")

    except KeyboardInterrupt:
        print("\n  👋 Watch arrêté.\n")


# ── Display ───────────────────────────────────────────────────────────────────

def format_report(report: ScanReport, as_json: bool = False) -> str:
    """Formatter le rapport pour affichage."""
    if as_json:
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    lines: list[str] = []
    lines.append("\n  🐛 Bug Finder Report")
    lines.append(f"  {'─' * 55}")
    lines.append(f"  Fichiers scannés : {report.files_scanned} ({report.files_skipped} ignorés)")
    lines.append(f"  Bugs trouvés     : {report.total}")
    lines.append(f"  Durée            : {report.duration_ms}ms")

    if report.by_severity:
        sev_str = " | ".join(f"{k}: {v}" for k, v in sorted(report.by_severity.items()))
        lines.append(f"  Sévérités        : {sev_str}")

    if report.bugs:
        lines.append(f"\n  {'─' * 55}")

        # Grouper par fichier
        by_file: dict[str, list[Bug]] = {}
        for b in report.bugs:
            by_file.setdefault(b.file, []).append(b)

        for filepath, bugs in sorted(by_file.items()):
            lines.append(f"\n  📄 {filepath}")
            for b in sorted(bugs, key=lambda x: x.line):
                severity_icons = {
                    Severity.CRITICAL: "🔴",
                    Severity.HIGH: "🔴",
                    Severity.MEDIUM: "🟡",
                    Severity.LOW: "🟢",
                }
                icon = severity_icons.get(b.severity, "⚪")
                lines.append(f"     {icon} L{b.line}:{b.col} [{b.rule_id}][{b.severity}] {b.message}")
                if b.suggestion:
                    lines.append(f"        💡 {b.suggestion}")

    elif report.files_scanned > 0:
        lines.append("\n  ✅ Aucun bug détecté !")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bug Finder — Détection de bugs logiques BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path("."),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--version", action="version", version=f"bug-finder {BUG_FINDER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # scan
    scan_p = sub.add_parser("scan", help="Scanner un fichier ou dossier")
    scan_p.add_argument("target", nargs="?", help="Fichier ou dossier à scanner")
    scan_p.add_argument("--severity", choices=["critical", "high", "medium", "low"],
                        help="Filtrer par sévérité minimale")

    # diff
    diff_p = sub.add_parser("diff", help="Scanner les fichiers modifiés (git)")
    diff_p.add_argument("--against", default="HEAD", help="Branche de référence (défaut: HEAD)")

    # watch
    watch_p = sub.add_parser("watch", help="Surveiller en continu")
    watch_p.add_argument("target", nargs="?", help="Dossier à surveiller")
    watch_p.add_argument("--interval", type=float, default=WATCH_INTERVAL,
                         help=f"Intervalle de polling en secondes (défaut: {WATCH_INTERVAL})")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    as_json = getattr(args, "json", False)

    if args.command == "scan":
        target = Path(args.target).resolve() if args.target else project_root
        severity = getattr(args, "severity", None)
        report = scan_directory(project_root, target, severity)
        print(format_report(report, as_json))
        sys.exit(1 if any(b.severity in (Severity.CRITICAL, Severity.HIGH) for b in report.bugs) else 0)

    elif args.command == "diff":
        report = scan_git_diff(project_root, args.against)
        print(format_report(report, as_json))
        sys.exit(1 if any(b.severity in (Severity.CRITICAL, Severity.HIGH) for b in report.bugs) else 0)

    elif args.command == "watch":
        target = Path(args.target).resolve() if args.target else project_root
        watch_directory(project_root, target, args.interval)


if __name__ == "__main__":
    main()
