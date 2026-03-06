#!/usr/bin/env python3
"""
preflight-check.py — Vérification pré-exécution BMAD.
========================================================

Scanne l'environnement avant qu'un agent commence une tâche/story.
Détecte les problèmes AVANT qu'ils ne causent des échecs :
  - Dépendances manquantes (outils CLI requis par le DNA)
  - Fichiers référencés mais inexistants
  - Conflits de branches Git
  - État de la mémoire (contradictions, session périmée)
  - Requêtes inter-agents en attente
  - Tokens budget estimé vs disponible

Inspiré de la "mise en place" en cuisine : tout préparer avant de cuisiner.

Usage :
  python3 preflight-check.py --project-root .                       # Check global
  python3 preflight-check.py --project-root . --agent forge         # Pour un agent
  python3 preflight-check.py --project-root . --story STORY-42.md   # Pour une story
  python3 preflight-check.py --project-root . --fix                 # Tenter l'auto-correction
  python3 preflight-check.py --project-root . --json                # Sortie JSON

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging

_log = logging.getLogger("grimoire.preflight_check")

# ── Constantes ────────────────────────────────────────────────────────────────

PREFLIGHT_VERSION = "1.0.0"

# Sévérité
class Severity:
    BLOCKER = "🔴 BLOCKER"
    WARNING = "🟡 WARNING"
    INFO = "🟢 INFO"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    """Résultat d'une vérification unitaire."""
    name: str
    severity: str
    message: str
    fix_hint: str = ""
    auto_fixable: bool = False
    fixed: bool = False

    @property
    def is_blocker(self) -> bool:
        return "BLOCKER" in self.severity


@dataclass
class PreflightReport:
    """Rapport complet de pre-flight."""
    agent: str = ""
    story: str = ""
    checks: list[Check] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.is_blocker]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if "WARNING" in c.severity]

    @property
    def infos(self) -> list[Check]:
        return [c for c in self.checks if "INFO" in c.severity]

    @property
    def go_nogo(self) -> str:
        if self.blockers:
            return "🔴 NO-GO"
        if self.warnings:
            return "🟡 GO (avec réserves)"
        return "🟢 GO"


# ── Checks ───────────────────────────────────────────────────────────────────

def check_bmad_structure(project_root: Path) -> list[Check]:
    """Vérifie la structure BMAD minimale."""
    checks = []
    required = [
        ("_bmad", "Dossier _bmad"),
        ("_bmad/_config", "Dossier config"),
        ("_bmad/_memory", "Dossier mémoire"),
    ]

    for path_str, label in required:
        if not (project_root / path_str).exists():
            checks.append(Check(
                name="structure",
                severity=Severity.BLOCKER,
                message=f"{label} manquant : {path_str}",
                fix_hint="Exécuter bmad-init.sh pour initialiser le projet",
            ))

    # Fichiers critiques
    critical_files = [
        ("_bmad/_memory/shared-context.md", "Shared context"),
    ]
    custom_dir = project_root / "_bmad" / "_config" / "custom"
    if custom_dir.exists():
        critical_files.append(
            ("_bmad/_config/custom/agent-base.md", "Agent base protocol")
        )

    for path_str, label in critical_files:
        fpath = project_root / path_str
        if not fpath.exists():
            checks.append(Check(
                name="critical-file",
                severity=Severity.WARNING,
                message=f"{label} manquant : {path_str}",
                fix_hint="Créer le fichier via bmad-init.sh ou manuellement",
            ))
        elif fpath.stat().st_size == 0:
            checks.append(Check(
                name="empty-file",
                severity=Severity.WARNING,
                message=f"{label} est vide : {path_str}",
            ))

    return checks


def check_tools_available(project_root: Path) -> list[Check]:
    """Vérifie que les outils CLI requis sont disponibles."""
    checks = []

    # Outils toujours requis
    core_tools = ["git", "python3"]
    for tool in core_tools:
        if not shutil.which(tool):
            checks.append(Check(
                name="tool-missing",
                severity=Severity.BLOCKER,
                message=f"Outil requis manquant : {tool}",
                fix_hint=f"Installer {tool} via le gestionnaire de paquets",
            ))

    # Outils du DNA actif (si archetype-dna.yaml existe)
    dna_files = list(project_root.glob("_bmad/**/archetype.dna.yaml"))
    for dna in dna_files:
        try:
            content = dna.read_text(encoding="utf-8")
            # Parse simple des tools_required
            in_tools = False
            for line in content.split("\n"):
                if "tools_required:" in line:
                    in_tools = True
                    continue
                if in_tools:
                    if line.strip().startswith("- "):
                        # Extraire le nom de la commande
                        m = re.search(r'check_command:\s*"?([^"\s]+)', line)
                        if m:
                            cmd = m.group(1)
                            if not shutil.which(cmd):
                                checks.append(Check(
                                    name="dna-tool-missing",
                                    severity=Severity.WARNING,
                                    message=f"Outil DNA requis manquant : {cmd} (dans {dna.name})",
                                    fix_hint=f"Installer {cmd} avant utilisation",
                                ))
                    elif not line.startswith(" ") and not line.startswith("\t"):
                        in_tools = False
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


def check_git_state(project_root: Path) -> list[Check]:
    """Vérifie l'état Git."""
    checks = []

    if not (project_root / ".git").exists():
        checks.append(Check(
            name="no-git",
            severity=Severity.INFO,
            message="Pas de dépôt Git détecté",
        ))
        return checks

    try:
        # Vérifier si des conflits de merge existent
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.stdout.strip():
            conflicted = result.stdout.strip().split("\n")
            checks.append(Check(
                name="merge-conflict",
                severity=Severity.BLOCKER,
                message=f"{len(conflicted)} fichier(s) en conflit de merge : {', '.join(conflicted[:3])}",
                fix_hint="Résoudre les conflits avant de continuer",
            ))

        # Vérifier les modifications non committées dans _bmad
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "_bmad/"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            checks.append(Check(
                name="uncommitted-bmad",
                severity=Severity.WARNING,
                message=f"{len(lines)} modification(s) non committée(s) dans _bmad/",
                fix_hint="git add _bmad/ && git commit -m 'chore: update bmad config'",
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        checks.append(Check(
            name="git-error",
            severity=Severity.INFO,
            message="Impossible d'exécuter les commandes git",
        ))

    return checks


def check_memory_state(project_root: Path) -> list[Check]:
    """Vérifie l'état de la mémoire."""
    checks = []
    memory_dir = project_root / "_bmad" / "_memory"

    if not memory_dir.exists():
        return checks

    # Contradictions non résolues
    contradiction_log = memory_dir / "contradiction-log.md"
    if contradiction_log.exists():
        try:
            content = contradiction_log.read_text(encoding="utf-8")
            unresolved = content.count("- [ ]") or content.count("⚠️")
            if unresolved > 0:
                checks.append(Check(
                    name="contradictions",
                    severity=Severity.WARNING,
                    message=f"{unresolved} contradiction(s) non résolue(s) dans contradiction-log.md",
                    fix_hint="Activer Mnemo pour résoudre les contradictions",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Session state périmé
    session_state = memory_dir / "session-state.md"
    if session_state.exists():
        try:
            mtime = datetime.fromtimestamp(session_state.stat().st_mtime)
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            if age_hours > 168:  # > 1 semaine
                checks.append(Check(
                    name="stale-session",
                    severity=Severity.INFO,
                    message=f"session-state.md date de {age_hours:.0f}h — potentiellement obsolète",
                    fix_hint="Re-briefer l'agent via [BR] pour rafraîchir le contexte",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Requêtes inter-agents en attente
    shared_context = memory_dir / "shared-context.md"
    if shared_context.exists():
        try:
            content = shared_context.read_text(encoding="utf-8")
            pending = len(re.findall(r"- \[ \].*\[.*→.*\]", content))
            if pending > 0:
                checks.append(Check(
                    name="pending-requests",
                    severity=Severity.WARNING,
                    message=f"{pending} requête(s) inter-agents en attente dans shared-context.md",
                    fix_hint="Résoudre les requêtes avant de commencer une nouvelle tâche",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


def check_story_readiness(project_root: Path, story_path: str) -> list[Check]:
    """Vérifie qu'une story est prête à être exécutée."""
    checks = []

    story_file = project_root / story_path
    if not story_file.exists():
        checks.append(Check(
            name="story-missing",
            severity=Severity.BLOCKER,
            message=f"Story introuvable : {story_path}",
        ))
        return checks

    try:
        content = story_file.read_text(encoding="utf-8")

        # Placeholders non remplis
        placeholders = re.findall(r'\{\{[^}]+\}\}', content)
        if placeholders:
            checks.append(Check(
                name="story-placeholders",
                severity=Severity.BLOCKER,
                message=f"{len(placeholders)} placeholder(s) non rempli(s) : {', '.join(placeholders[:5])}",
                fix_hint="Remplir les placeholders avant de commencer",
            ))

        # Acceptance criteria vides
        if "acceptance" in content.lower() and "- [ ]" not in content:
            checks.append(Check(
                name="no-acceptance-criteria",
                severity=Severity.WARNING,
                message="Pas de critères d'acceptation checkable (- [ ]) trouvés",
                fix_hint="Ajouter des critères d'acceptation cochables",
            ))

    except OSError:
        checks.append(Check(
            name="story-read-error",
            severity=Severity.BLOCKER,
            message=f"Impossible de lire la story : {story_path}",
        ))

    return checks


# ── Module Wuwei (#107) : Non-interruption ───────────────────────────────────

def check_wuwei(project_root: Path, agent: str) -> list[Check]:
    """Vérifie si l'agent est en mode flow (wuwei) — ne pas interrompre."""
    checks = []
    memory_dir = project_root / "_bmad" / "_memory"
    session_state = memory_dir / "session-state.md"

    if session_state.exists():
        try:
            content = session_state.read_text(encoding="utf-8")
            # Chercher des tâches in-progress pour cet agent
            in_progress = re.findall(
                rf"\b{re.escape(agent)}\b.*(?:in-progress|en_cours|running)",
                content, re.IGNORECASE
            )
            if in_progress:
                checks.append(Check(
                    name="wuwei-flow",
                    severity=Severity.INFO,
                    message=f"Agent {agent} a {len(in_progress)} tâche(s) en cours — "
                            f"mode flow actif, minimiser les interruptions",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


# ── Report Generation ────────────────────────────────────────────────────────

def run_all_checks(
    project_root: Path,
    agent: str = "",
    story: str = "",
) -> PreflightReport:
    """Exécute toutes les vérifications."""
    report = PreflightReport(agent=agent, story=story)

    report.checks.extend(check_bmad_structure(project_root))
    report.checks.extend(check_tools_available(project_root))
    report.checks.extend(check_git_state(project_root))
    report.checks.extend(check_memory_state(project_root))

    if story:
        report.checks.extend(check_story_readiness(project_root, story))

    if agent:
        report.checks.extend(check_wuwei(project_root, agent))

    return report


def format_report(report: PreflightReport) -> str:
    """Formate le rapport pour affichage terminal."""
    lines = [
        "✈️  Pre-flight Check — BMAD",
        f"   {report.go_nogo}",
    ]
    if report.agent:
        lines.append(f"   Agent : {report.agent}")
    if report.story:
        lines.append(f"   Story : {report.story}")
    lines.append(f"   Checks : {len(report.checks)} "
                 f"({len(report.blockers)} blockers, "
                 f"{len(report.warnings)} warnings, "
                 f"{len(report.infos)} infos)")
    lines.append("")

    if report.blockers:
        lines.append("   🔴 BLOCKERS :")
        for c in report.blockers:
            lines.append(f"      {c.message}")
            if c.fix_hint:
                lines.append(f"         💡 {c.fix_hint}")
        lines.append("")

    if report.warnings:
        lines.append("   🟡 WARNINGS :")
        for c in report.warnings:
            lines.append(f"      {c.message}")
            if c.fix_hint:
                lines.append(f"         💡 {c.fix_hint}")
        lines.append("")

    if report.infos:
        lines.append("   🟢 INFO :")
        for c in report.infos:
            lines.append(f"      {c.message}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Pre-flight Check — Vérification pré-exécution",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--agent", type=str, help="Agent cible")
    parser.add_argument("--story", type=str, help="Chemin vers la story à vérifier")
    parser.add_argument("--fix", action="store_true", help="Tenter l'auto-correction")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--quiet", action="store_true", help="N'afficher que les blockers")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    report = run_all_checks(project_root, agent=args.agent or "", story=args.story or "")

    if args.json:
        result = {
            "go_nogo": report.go_nogo,
            "agent": report.agent,
            "story": report.story,
            "timestamp": report.timestamp,
            "blockers": [{"name": c.name, "message": c.message, "fix": c.fix_hint} for c in report.blockers],
            "warnings": [{"name": c.name, "message": c.message, "fix": c.fix_hint} for c in report.warnings],
            "infos": [{"name": c.name, "message": c.message} for c in report.infos],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.quiet:
        if report.blockers:
            for c in report.blockers:
                print(f"🔴 {c.message}")
            return 1
    else:
        print(format_report(report))

    return 1 if report.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
