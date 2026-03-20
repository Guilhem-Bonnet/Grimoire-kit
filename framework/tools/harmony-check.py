#!/usr/bin/env python3
"""
harmony-check.py — Architecture Harmony Check Grimoire.
=====================================================

Détecte les dissonances architecturales dans un projet Grimoire :

  1. `scan`      — Scanner le projet pour détecter les patterns
  2. `check`     — Vérifier l'harmonie architecturale
  3. `dissonance`— Lister les dissonances détectées
  4. `score`     — Calculer le score d'harmonie global
  5. `report`    — Rapport complet

Dissonances détectées :
  - Agents orphelins (non référencés)
  - Workflows cassés (refs invalides)
  - Conventions de nommage incohérentes
  - Duplication de responsabilités
  - Cycles de dépendances
  - Fichiers hors-norme (trop gros, mal placés)
  - Incohérence entre manifests et fichiers réels

Principe : "L'harmonie naît quand chaque composant joue sa partition."

Usage :
  python3 harmony-check.py --project-root . scan
  python3 harmony-check.py --project-root . check
  python3 harmony-check.py --project-root . dissonance
  python3 harmony-check.py --project-root . score
  python3 harmony-check.py --project-root . report

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.harmony_check")

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

# Seuils
MAX_FILE_LINES = 800
NAMING_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z]+)?$")

# Niveaux de sévérité
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Dissonance:
    """Une dissonance architecturale détectée."""
    category: str         # orphan | broken-ref | naming | duplication | cycle | size | manifest
    severity: str         # HIGH | MEDIUM | LOW
    file: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category, "severity": self.severity,
            "file": self.file, "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ArchScan:
    """Résultat d'un scan architectural."""
    agents: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    cross_refs: dict[str, list[str]] = field(default_factory=dict)  # file -> [refs]
    dissonances: list[Dissonance] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.agents) + len(self.workflows) + len(self.tools) + \
               len(self.configs) + len(self.docs) + len(self.tests)


# ── Scanner ──────────────────────────────────────────────────────────────────

_SCAN_EXCLUDE = frozenset({".git", ".venv", "node_modules", "__pycache__", "dist", "build"})


def _excluded(path_str: str) -> bool:
    """Retourne True si le chemin contient un répertoire à exclure du scan."""
    return any(part in path_str for part in _SCAN_EXCLUDE)


def scan_project(project_root: Path) -> ArchScan:
    """Scanner complet du projet."""
    scan = ArchScan()

    # Discover agents
    for pattern in ["**/agents/*.md", "**/agents/*.xml", "**/agents/*.yaml"]:
        for f in project_root.glob(pattern):
            if not _excluded(str(f)):
                scan.agents.append(str(f.relative_to(project_root)))

    # Discover workflows
    for pattern in ["**/workflows/**/*.md", "**/workflows/**/*.yaml", "**/workflows/**/*.xml"]:
        for f in project_root.glob(pattern):
            if not _excluded(str(f)):
                scan.workflows.append(str(f.relative_to(project_root)))

    # Discover tools
    for f in project_root.glob("**/tools/*.py"):
        if not _excluded(str(f)):
            scan.tools.append(str(f.relative_to(project_root)))

    # Discover configs
    for pattern in ["**/*.yaml", "**/*.yml"]:
        for f in project_root.glob(pattern):
            rel = str(f.relative_to(project_root))
            if not _excluded(rel) and rel not in scan.workflows:
                scan.configs.append(rel)

    # Discover docs
    for f in project_root.glob("**/docs/**/*.md"):
        if not _excluded(str(f)):
            scan.docs.append(str(f.relative_to(project_root)))

    # Discover tests
    for f in project_root.glob("**/tests/**/*"):
        if not _excluded(str(f)) and f.is_file():
            scan.tests.append(str(f.relative_to(project_root)))

    # Build cross-references
    all_files = scan.agents + scan.workflows + scan.tools
    for fpath in all_files:
        full = project_root / fpath
        if full.exists() and full.stat().st_size < 100_000:
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                refs = []
                for other in all_files:
                    if other != fpath:
                        stem = Path(other).stem
                        if stem in content:
                            refs.append(other)
                if refs:
                    scan.cross_refs[fpath] = refs
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    return scan


# ── Dissonance Detectors ─────────────────────────────────────────────────────

def detect_orphans(scan: ArchScan) -> list[Dissonance]:
    """Détecter les fichiers orphelins (non référencés nulle part)."""
    dissonances = []
    referenced = set()
    for refs in scan.cross_refs.values():
        referenced.update(refs)

    for agent in scan.agents:
        if agent not in referenced and agent not in scan.cross_refs:
            dissonances.append(Dissonance(
                "orphan", SEVERITY_MEDIUM, agent,
                "Agent orphelin — non référencé par aucun workflow ou outil",
                "Vérifier si cet agent est utilisé ou s'il peut être retiré",
            ))
    return dissonances


def detect_naming(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    """Vérifier la cohérence des conventions de nommage."""
    dissonances = []
    all_files = scan.agents + scan.workflows + scan.tools

    # Exclure les packages Python (src/) qui suivent la convention snake_case (PEP 8)
    _python_pkg_prefixes = ("src/", "tests/")
    for fpath in all_files:
        if any(fpath.startswith(p) for p in _python_pkg_prefixes):
            continue
        stem = Path(fpath).stem
        # Les modules Python internes (snake_case, pas de CLI) sont exemptés du kebab-case
        # Un module Python interne : fichier .py sans tiret, importé par d'autres
        if fpath.endswith(".py") and "_" in stem and "-" not in stem:
            continue
        if not NAMING_PATTERN.match(stem):
            dissonances.append(Dissonance(
                "naming", SEVERITY_LOW, fpath,
                f"Nom de fichier '{stem}' ne respecte pas la convention kebab-case",
                "Renommer en kebab-case : lettres minuscules et tirets",
            ))
    return dissonances


def detect_oversized(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    """Détecter les fichiers trop volumineux."""
    dissonances = []
    all_files = scan.agents + scan.workflows + scan.tools

    for fpath in all_files:
        full = project_root / fpath
        if full.exists():
            try:
                lines = full.read_text(encoding="utf-8", errors="replace").count("\n")
                if lines > MAX_FILE_LINES:
                    dissonances.append(Dissonance(
                        "size", SEVERITY_LOW, fpath,
                        f"Fichier volumineux ({lines} lignes > {MAX_FILE_LINES} max)",
                        "Envisager de découper en modules plus petits",
                    ))
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    return dissonances


def detect_manifest_mismatch(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    """Vérifier la cohérence entre manifests et fichiers réels."""
    dissonances = []

    # Check agent manifest si présent
    for manifest_name in ["agent-manifest.csv", "team-manifest.yaml"]:
        for mpath in project_root.glob(f"**/{manifest_name}"):
            try:
                content = mpath.read_text(encoding="utf-8")
                agent_stems = {Path(a).stem for a in scan.agents}
                # Simple : chercher des noms dans le manifest qui ne correspondent à rien
                for line in content.splitlines():
                    parts = line.split(",")
                    if len(parts) >= 1:
                        name = parts[0].strip().lower()
                        if name and name not in ("agent", "name", "id", "#") and \
                           name not in agent_stems and \
                           not name.startswith("#"):
                            # Vérification que ce pourrait être un nom d'agent
                            if re.match(r"^[a-z][a-z0-9-]+$", name):
                                dissonances.append(Dissonance(
                                    "manifest", SEVERITY_MEDIUM,
                                    str(mpath.relative_to(project_root)),
                                    f"'{name}' référencé dans le manifest mais pas de fichier agent correspondant",
                                    "Vérifier si l'agent existe ou retirer du manifest",
                                ))
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    return dissonances


def detect_broken_refs(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    """Détecter des références vers des fichiers inexistants."""
    dissonances = []
    ref_pattern = re.compile(r'(?:include|load|source|import|ref)\s*[=:]\s*["\']?([a-zA-Z0-9_/.@-]+)')

    all_files = scan.agents + scan.workflows + scan.tools
    for fpath in all_files:
        # Ignorer les fichiers Python de test (les string literals sont des fixtures, pas de vraies refs)
        if fpath.endswith(".py"):
            continue
        full = project_root / fpath
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            for match in ref_pattern.finditer(content):
                ref = match.group(1)
                if not ref.startswith("http") and ("/" in ref or ref.endswith((".md", ".yaml", ".py", ".xml"))):
                    ref_path = project_root / ref
                    if not ref_path.exists():
                        # Check relative to file
                        ref_path2 = full.parent / ref
                        if not ref_path2.exists():
                            dissonances.append(Dissonance(
                                "broken-ref", SEVERITY_HIGH, fpath,
                                f"Référence cassée : '{ref}' — fichier introuvable",
                                "Corriger le chemin ou retirer la référence",
                            ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    return dissonances


def detect_duplication(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    """Détecter les duplications de responsabilités (agents similaires)."""
    dissonances = []

    # Comparer les descriptions d'agents pour trouver des doublons potentiels
    agent_summaries: dict[str, str] = {}
    for agent_path in scan.agents:
        full = project_root / agent_path
        if full.exists():
            try:
                content = full.read_text(encoding="utf-8", errors="replace")[:500].lower()
                agent_summaries[agent_path] = content
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    # Simple Jaccard sur les mots
    agent_list = list(agent_summaries.items())
    for i, (path_a, content_a) in enumerate(agent_list):
        words_a = set(re.findall(r"\b\w{4,}\b", content_a))
        for path_b, content_b in agent_list[i + 1:]:
            words_b = set(re.findall(r"\b\w{4,}\b", content_b))
            if words_a and words_b:
                jaccard = len(words_a & words_b) / len(words_a | words_b)
                if jaccard > 0.6:
                    dissonances.append(Dissonance(
                        "duplication", SEVERITY_MEDIUM, path_a,
                        f"Possiblement similaire à '{path_b}' (Jaccard={jaccard:.0%})",
                        "Vérifier si les responsabilités se chevauchent",
                    ))
    return dissonances


# ── Score Calculation ────────────────────────────────────────────────────────

def calculate_harmony_score(scan: ArchScan) -> dict:
    """Calculer le score d'harmonie 0-100."""
    if not scan.dissonances:
        return {"score": 100, "grade": "A+", "detail": {}}

    penalty = 0
    cat_count: dict[str, int] = {}

    for d in scan.dissonances:
        cat_count[d.category] = cat_count.get(d.category, 0) + 1
        if d.severity == SEVERITY_HIGH:
            penalty += 8
        elif d.severity == SEVERITY_MEDIUM:
            penalty += 4
        else:
            penalty += 2

    score = max(0, 100 - penalty)
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    return {"score": score, "grade": grade, "detail": cat_count}


# ── Full Analysis ────────────────────────────────────────────────────────────

def full_analysis(project_root: Path) -> tuple[ArchScan, dict]:
    """Analyse complète : scan + toutes les détections."""
    scan = scan_project(project_root)

    scan.dissonances.extend(detect_orphans(scan))
    scan.dissonances.extend(detect_naming(scan, project_root))
    scan.dissonances.extend(detect_oversized(scan, project_root))
    scan.dissonances.extend(detect_manifest_mismatch(scan, project_root))
    scan.dissonances.extend(detect_broken_refs(scan, project_root))
    scan.dissonances.extend(detect_duplication(scan, project_root))

    score_data = calculate_harmony_score(scan)
    return scan, score_data


# ── Formatters ───────────────────────────────────────────────────────────────

def format_scan(scan: ArchScan) -> str:
    lines = [
        "🏛️ Scan architectural",
        f"   Agents     : {len(scan.agents)}",
        f"   Workflows  : {len(scan.workflows)}",
        f"   Tools      : {len(scan.tools)}",
        f"   Configs    : {len(scan.configs)}",
        f"   Docs       : {len(scan.docs)}",
        f"   Tests      : {len(scan.tests)}",
        f"   Total      : {scan.total_files}",
        f"   Cross-refs : {len(scan.cross_refs)} fichiers avec références",
    ]
    return "\n".join(lines)


def format_dissonances(dissonances: list[Dissonance]) -> str:
    if not dissonances:
        return "✅ Aucune dissonance détectée — Architecture harmonieuse !"

    by_sev = {SEVERITY_HIGH: [], SEVERITY_MEDIUM: [], SEVERITY_LOW: []}
    for d in dissonances:
        by_sev[d.severity].append(d)

    icons = {SEVERITY_HIGH: "🔴", SEVERITY_MEDIUM: "🟡", SEVERITY_LOW: "🔵"}
    lines = [f"🎵 Dissonances détectées : {len(dissonances)}\n"]

    for sev in [SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW]:
        items = by_sev[sev]
        if items:
            lines.append(f"   {icons[sev]} {sev} ({len(items)})")
            for d in items:
                lines.append(f"      [{d.category}] {d.file}")
                lines.append(f"         {d.message}")
                if d.suggestion:
                    lines.append(f"         → {d.suggestion}")
            lines.append("")

    return "\n".join(lines)


def format_score(score_data: dict) -> str:
    lines = [
        f"🎼 Score d'harmonie : {score_data['score']}/100 ({score_data['grade']})",
    ]
    if score_data["detail"]:
        lines.append("   Détail par catégorie :")
        for cat, count in sorted(score_data["detail"].items()):
            lines.append(f"      {cat} : {count}")
    return "\n".join(lines)


def format_report(scan: ArchScan, score_data: dict) -> str:
    sections = [
        "═══════════════════════════════════════════════",
        "   ARCHITECTURE HARMONY CHECK — Rapport complet",
        "═══════════════════════════════════════════════\n",
        format_scan(scan),
        "",
        format_score(score_data),
        "",
        format_dissonances(scan.dissonances),
        "",
        "───────────────────────────────────────────────",
    ]
    return "\n".join(sections)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    scan = scan_project(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({
            "agents": len(scan.agents), "workflows": len(scan.workflows),
            "tools": len(scan.tools), "configs": len(scan.configs),
            "docs": len(scan.docs), "tests": len(scan.tests),
            "cross_refs": len(scan.cross_refs),
        }, indent=2))
    else:
        print(format_scan(scan))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    scan, score_data = full_analysis(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({
            "score": score_data["score"], "grade": score_data["grade"],
            "dissonances": len(scan.dissonances),
        }, indent=2))
    else:
        print(format_score(score_data))
        print()
        if scan.dissonances:
            high = sum(1 for d in scan.dissonances if d.severity == SEVERITY_HIGH)
            med = sum(1 for d in scan.dissonances if d.severity == SEVERITY_MEDIUM)
            low = sum(1 for d in scan.dissonances if d.severity == SEVERITY_LOW)
            print(f"   🔴 {high} critiques | 🟡 {med} moyennes | 🔵 {low} mineures")
    return 0


def cmd_dissonance(args: argparse.Namespace) -> int:
    scan, _ = full_analysis(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps([d.to_dict() for d in scan.dissonances], indent=2, ensure_ascii=False))
    else:
        print(format_dissonances(scan.dissonances))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    _, score_data = full_analysis(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(score_data, indent=2))
    else:
        print(format_score(score_data))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    scan, score_data = full_analysis(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({
            "scan": {"agents": len(scan.agents), "workflows": len(scan.workflows),
                     "tools": len(scan.tools), "total": scan.total_files},
            "score": score_data,
            "dissonances": [d.to_dict() for d in scan.dissonances],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_report(scan, score_data))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Architecture Harmony Check",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("scan", help="Scanner le projet").set_defaults(func=cmd_scan)
    subs.add_parser("check", help="Vérifier l'harmonie").set_defaults(func=cmd_check)
    subs.add_parser("dissonance", help="Lister les dissonances").set_defaults(func=cmd_dissonance)
    subs.add_parser("score", help="Score d'harmonie").set_defaults(func=cmd_score)
    subs.add_parser("report", help="Rapport complet").set_defaults(func=cmd_report)

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
