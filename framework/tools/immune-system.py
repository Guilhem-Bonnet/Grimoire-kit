#!/usr/bin/env python3
"""
immune-system.py — Système immunitaire BMAD.
===============================================

Double couche de protection :
  - Innée : règles statiques (patterns connus, OWASP, conventions)
  - Adaptative : apprend des incidents passés (failure-museum)

Intégration avec cc-verify.sh pour la vérification de complétion.

Features :
  1. `scan`     — Scan complet (inné + adaptatif)
  2. `innate`   — Vérification innée uniquement (règles statiques)
  3. `adaptive` — Vérification adaptative (basée sur l'historique)
  4. `learn`    — Enregistrer un nouvel incident (enrichir le système adaptatif)
  5. `report`   — Rapport complet de l'état immunitaire

Usage :
  python3 immune-system.py --project-root . scan
  python3 immune-system.py --project-root . scan --target src/auth.py
  python3 immune-system.py --project-root . innate
  python3 immune-system.py --project-root . adaptive
  python3 immune-system.py --project-root . learn --type "injection" --desc "SQLi dans user input" --fix "parameterized queries"
  python3 immune-system.py --project-root . report --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.immune_system")

# ── Constantes ────────────────────────────────────────────────────────────────

IMMUNE_VERSION = "1.0.0"
ANTIBODY_FILE = "immune-memory.json"  # Mémoire adaptative

# Sévérité
class Severity:
    CRITICAL = "🔴 CRITICAL"
    HIGH = "🟠 HIGH"
    MEDIUM = "🟡 MEDIUM"
    LOW = "🟢 LOW"
    INFO = "ℹ️ INFO"


# ── Innate Rules (OWASP-inspired + conventions) ─────────────────────────────

INNATE_RULES = [
    # Injection
    {
        "id": "INJ-001",
        "name": "SQL Injection potentielle",
        "severity": Severity.CRITICAL,
        "pattern": r'(?:execute|cursor\.execute|query)\s*\(\s*["\'].*%s',
        "extensions": [".py"],
        "fix": "Utiliser des requêtes paramétrées (?, %s avec tuple)",
    },
    {
        "id": "INJ-002",
        "name": "Command Injection potentielle",
        "severity": Severity.CRITICAL,
        "pattern": r'(?:os\.system|subprocess\.(?:call|run|Popen))\s*\([^)]*\+',
        "extensions": [".py"],
        "fix": "Utiliser subprocess avec liste d'arguments, jamais de concaténation",
    },
    {
        "id": "INJ-003",
        "name": "Shell injection via f-string",
        "severity": Severity.HIGH,
        "pattern": r'(?:os\.system|subprocess)\s*\(\s*f["\']',
        "extensions": [".py"],
        "fix": "Ne jamais utiliser f-strings dans les commandes shell",
    },
    # Secrets
    {
        "id": "SEC-001",
        "name": "Secret potentiel hardcodé",
        "severity": Severity.CRITICAL,
        "pattern": r'(?:password|secret|api_key|token|private_key)\s*=\s*["\'][^"\']{8,}',
        "extensions": [".py", ".ts", ".js", ".yaml", ".yml", ".env"],
        "fix": "Utiliser des variables d'environnement ou un vault",
    },
    {
        "id": "SEC-002",
        "name": "Clé privée dans le code",
        "severity": Severity.CRITICAL,
        "pattern": r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----',
        "extensions": None,  # Tous les fichiers
        "fix": "Stocker les clés dans un vault, jamais dans le code",
    },
    # Auth
    {
        "id": "AUTH-001",
        "name": "Absence de vérification d'authentification",
        "severity": Severity.HIGH,
        "pattern": r'def\s+(?:get|post|put|delete|patch)_.*(?:request|req)\s*[,)](?:(?!auth|login|permission|token).)*$',
        "extensions": [".py"],
        "fix": "Ajouter une vérification d'authentification sur les endpoints",
    },
    # Configuration
    {
        "id": "CFG-001",
        "name": "Debug/verbose activé en production",
        "severity": Severity.MEDIUM,
        "pattern": r'(?:DEBUG|VERBOSE)\s*=\s*(?:True|true|1)',
        "extensions": [".py", ".ts", ".js", ".yaml", ".yml", ".env"],
        "fix": "Désactiver DEBUG en production",
    },
    {
        "id": "CFG-002",
        "name": "CORS trop permissif",
        "severity": Severity.HIGH,
        "pattern": r'(?:allow_origins|Access-Control-Allow-Origin)\s*[=:]\s*["\']?\*',
        "extensions": [".py", ".ts", ".js", ".yaml"],
        "fix": "Restreindre CORS aux origines autorisées",
    },
    # BMAD-specific
    {
        "id": "BMAD-001",
        "name": "Fichier mémoire sans protection",
        "severity": Severity.MEDIUM,
        "pattern": r'\.write_text\s*\(.*(?:shared[-_]context|session[-_]state)',
        "extensions": [".py"],
        "fix": "Valider les données avant écriture dans la mémoire partagée",
    },
    {
        "id": "BMAD-002",
        "name": "Exécution de code non sandboxé",
        "severity": Severity.HIGH,
        "pattern": r'(?:exec|eval)\s*\(',
        "extensions": [".py"],
        "fix": "Éviter exec/eval, utiliser des alternatives sûres",
    },
    # Error handling
    {
        "id": "ERR-001",
        "name": "Exception silencieuse (bare except)",
        "severity": Severity.MEDIUM,
        "pattern": r'except\s*:',
        "extensions": [".py"],
        "fix": "Capturer des exceptions spécifiques, ne jamais utiliser bare except",
    },
    {
        "id": "ERR-002",
        "name": "Exception avalée (pass dans except)",
        "severity": Severity.LOW,
        "pattern": r'except.*:\s*\n\s*pass',
        "extensions": [".py"],
        "fix": "Logger l'erreur au minimum dans le except",
    },
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """Résultat d'une détection."""
    rule_id: str
    rule_name: str
    severity: str
    file: str
    line: int = 0
    match: str = ""
    fix: str = ""
    source: str = "innate"  # innate | adaptive

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.rule_name,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "match": self.match[:100],
            "fix": self.fix,
            "source": self.source,
        }


@dataclass
class Antibody:
    """Anticorps adaptatif — pattern appris d'un incident."""
    id: str
    type: str           # type d'incident
    pattern: str        # regex à détecter
    description: str
    fix: str
    severity: str = Severity.MEDIUM
    learned_from: str = ""
    date: str = field(default_factory=lambda: datetime.now().isoformat()[:10])


@dataclass
class ImmuneReport:
    """Rapport complet du système immunitaire."""
    findings: list[Finding] = field(default_factory=list)
    innate_checks: int = 0
    adaptive_checks: int = 0
    files_scanned: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if "CRITICAL" in f.severity]

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if "HIGH" in f.severity]

    @property
    def health_score(self) -> int:
        """Score de santé 0-100 (inversé)."""
        penalty = len(self.critical) * 20 + len(self.high) * 10 + len(self.findings) * 2
        return max(0, 100 - penalty)


# ── Scanning Engine ──────────────────────────────────────────────────────────

def scan_innate(project_root: Path, target: str = "") -> tuple[list[Finding], int]:
    """Scan avec les règles innées."""
    findings = []
    files_scanned = 0

    # Dossiers à scanner
    scan_dirs = [project_root]
    if target:
        target_path = project_root / target
        if target_path.is_file():
            scan_dirs = [target_path.parent]
        elif target_path.is_dir():
            scan_dirs = [target_path]

    # Dossiers à exclure
    exclude = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "_bmad-output", ".mypy_cache"}

    for scan_dir in scan_dirs:
        for fpath in scan_dir.rglob("*"):
            if not fpath.is_file():
                continue
            if any(ex in fpath.parts for ex in exclude):
                continue
            if fpath.stat().st_size > 1_000_000:  # Skip > 1MB
                continue

            for rule in INNATE_RULES:
                # Vérifier l'extension
                exts = rule.get("extensions")
                if exts and fpath.suffix not in exts:
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8")
                    files_scanned += 1

                    for i, line in enumerate(content.split("\n"), 1):
                        if re.search(rule["pattern"], line, re.IGNORECASE):
                            findings.append(Finding(
                                rule_id=rule["id"],
                                rule_name=rule["name"],
                                severity=rule["severity"],
                                file=str(fpath.relative_to(project_root)),
                                line=i,
                                match=line.strip()[:100],
                                fix=rule.get("fix", ""),
                                source="innate",
                            ))
                except (OSError, UnicodeDecodeError) as _exc:
                    _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                    # Silent exception — add logging when investigating issues

    return findings, files_scanned


def load_antibodies(project_root: Path) -> list[Antibody]:
    """Charge les anticorps adaptatifs."""
    ab_file = project_root / "_bmad" / "_memory" / ANTIBODY_FILE
    if not ab_file.exists():
        return []
    try:
        data = json.loads(ab_file.read_text(encoding="utf-8"))
        return [
            Antibody(
                id=ab.get("id", ""),
                type=ab.get("type", ""),
                pattern=ab.get("pattern", ""),
                description=ab.get("description", ""),
                fix=ab.get("fix", ""),
                severity=ab.get("severity", Severity.MEDIUM),
                learned_from=ab.get("learned_from", ""),
                date=ab.get("date", ""),
            )
            for ab in data.get("antibodies", [])
        ]
    except (json.JSONDecodeError, OSError):
        return []


def save_antibodies(project_root: Path, antibodies: list[Antibody]):
    """Sauvegarde les anticorps adaptatifs."""
    ab_file = project_root / "_bmad" / "_memory" / ANTIBODY_FILE
    ab_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": IMMUNE_VERSION,
        "antibodies": [
            {
                "id": ab.id,
                "type": ab.type,
                "pattern": ab.pattern,
                "description": ab.description,
                "fix": ab.fix,
                "severity": ab.severity,
                "learned_from": ab.learned_from,
                "date": ab.date,
            }
            for ab in antibodies
        ],
    }
    ab_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def scan_adaptive(project_root: Path, target: str = "") -> tuple[list[Finding], int]:
    """Scan avec les anticorps adaptatifs."""
    antibodies = load_antibodies(project_root)
    if not antibodies:
        return [], 0

    findings = []
    exclude = {".git", "node_modules", "__pycache__", ".venv"}
    scan_root = project_root / target if target else project_root

    for fpath in scan_root.rglob("*"):
        if not fpath.is_file() or any(ex in fpath.parts for ex in exclude):
            continue
        if fpath.suffix not in (".py", ".ts", ".js", ".yaml", ".yml", ".md"):
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            for ab in antibodies:
                if not ab.pattern:
                    continue
                for i, line in enumerate(content.split("\n"), 1):
                    try:
                        if re.search(ab.pattern, line, re.IGNORECASE):
                            findings.append(Finding(
                                rule_id=ab.id,
                                rule_name=ab.description,
                                severity=ab.severity,
                                file=str(fpath.relative_to(project_root)),
                                line=i,
                                match=line.strip()[:100],
                                fix=ab.fix,
                                source="adaptive",
                            ))
                    except re.error as _exc:
                        _log.debug("re.error suppressed: %s", _exc)
                        # Silent exception — add logging when investigating issues
        except (OSError, UnicodeDecodeError) as _exc:
            _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return findings, len(antibodies)


# ── Formatters ───────────────────────────────────────────────────────────────

def format_report(report: ImmuneReport) -> str:
    lines = [
        "🛡️ Système Immunitaire BMAD — Rapport",
        f"   Score de santé : {report.health_score}/100",
        f"   Fichiers scannés : {report.files_scanned}",
        f"   Règles innées : {report.innate_checks} | Anticorps : {report.adaptive_checks}",
        f"   Trouvailles : {len(report.findings)} "
        f"({len(report.critical)} critiques, {len(report.high)} élevées)",
        "",
    ]

    if not report.findings:
        lines.append("   ✅ Aucune vulnérabilité détectée — système sain")
        return "\n".join(lines)

    # Grouper par sévérité
    by_sev = defaultdict(list)
    for f in report.findings:
        by_sev[f.severity].append(f)

    for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
        items = by_sev.get(sev, [])
        if not items:
            continue
        lines.append(f"   {sev} ({len(items)}) :")
        for finding in items[:10]:
            lines.append(f"      [{finding.rule_id}] {finding.rule_name}")
            lines.append(f"         {finding.file}:{finding.line}")
            if finding.match:
                lines.append(f"         → {finding.match[:80]}")
            if finding.fix:
                lines.append(f"         💡 {finding.fix}")
        if len(items) > 10:
            lines.append(f"      ... et {len(items) - 10} de plus")
        lines.append("")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    target = args.target or ""
    report = ImmuneReport()

    innate_findings, files = scan_innate(project_root, target)
    report.findings.extend(innate_findings)
    report.innate_checks = len(INNATE_RULES)
    report.files_scanned = files

    adaptive_findings, ab_count = scan_adaptive(project_root, target)
    report.findings.extend(adaptive_findings)
    report.adaptive_checks = ab_count

    if args.json:
        print(json.dumps({
            "health_score": report.health_score,
            "files_scanned": report.files_scanned,
            "findings": [f.to_dict() for f in report.findings],
            "summary": {
                "critical": len(report.critical),
                "high": len(report.high),
                "total": len(report.findings),
            },
        }, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))

    return 1 if report.critical else 0


def cmd_innate(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    findings, files = scan_innate(project_root, args.target or "")
    report = ImmuneReport(findings=findings, innate_checks=len(INNATE_RULES), files_scanned=files)
    if args.json:
        print(json.dumps({"findings": [f.to_dict() for f in findings]}, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_adaptive(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    findings, ab_count = scan_adaptive(project_root, args.target or "")
    report = ImmuneReport(findings=findings, adaptive_checks=ab_count)
    if args.json:
        print(json.dumps({"findings": [f.to_dict() for f in findings]}, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    antibodies = load_antibodies(project_root)

    # Générer un ID
    next_id = f"AB-{len(antibodies) + 1:03d}"

    ab = Antibody(
        id=next_id,
        type=args.type,
        pattern=args.pattern or "",
        description=args.desc,
        fix=args.fix or "",
        severity=args.severity or Severity.MEDIUM,
        learned_from=args.incident or "",
    )
    antibodies.append(ab)
    save_antibodies(project_root, antibodies)

    print(f"🧬 Anticorps {next_id} enregistré : {args.desc}")
    print(f"   Type : {args.type}")
    if args.pattern:
        print(f"   Pattern : {args.pattern}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    antibodies = load_antibodies(project_root)

    print("🛡️ État du Système Immunitaire\n")
    print(f"   Règles innées : {len(INNATE_RULES)}")
    print(f"   Anticorps adaptatifs : {len(antibodies)}")

    if antibodies:
        print("\n   Anticorps récents :")
        for ab in antibodies[-5:]:
            print(f"      [{ab.id}] {ab.type}: {ab.description}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Immune System — Détection de vulnérabilités multiniveau",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--target", type=str, help="Fichier ou dossier cible")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("scan", help="Scan complet (inné + adaptatif)")
    p.set_defaults(func=cmd_scan)

    p = subs.add_parser("innate", help="Scan inné uniquement")
    p.set_defaults(func=cmd_innate)

    p = subs.add_parser("adaptive", help="Scan adaptatif uniquement")
    p.set_defaults(func=cmd_adaptive)

    p = subs.add_parser("learn", help="Enregistrer un nouvel anticorps")
    p.add_argument("--type", type=str, required=True, help="Type d'incident")
    p.add_argument("--desc", type=str, required=True, help="Description")
    p.add_argument("--pattern", type=str, help="Regex à détecter")
    p.add_argument("--fix", type=str, help="Remédiation")
    p.add_argument("--severity", type=str, help="Sévérité")
    p.add_argument("--incident", type=str, help="Incident d'origine")
    p.set_defaults(func=cmd_learn)

    p = subs.add_parser("report", help="Rapport d'état")
    p.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
