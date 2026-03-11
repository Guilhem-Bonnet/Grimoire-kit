#!/usr/bin/env python3
"""
agent-lint.py — Linter structurel pour les agents BMAD.
=======================================================

Vérifie l'intégrité structurelle de chaque fichier agent .md :
  - Commandes menu (cmd) uniques par agent (pas de doublon)
  - Blocs persona obligatoires présents (<voice>, <decision_framework>, etc.)
  - Synchronisation agent ↔ manifest CSV (nom, module, displayName)
  - Workflows/fichiers référencés existent sur disque
  - Handlers déclarés correspondent aux attributs utilisés dans le menu

Usage :
  python3 agent-lint.py --project-root .                    # Lint tous les agents
  python3 agent-lint.py --project-root . --agent analyst    # Lint un agent
  python3 agent-lint.py --project-root . --json             # Sortie JSON
  python3 agent-lint.py --project-root . --fix              # Suggestions auto-fix

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.agent_lint")

AGENT_LINT_VERSION = "1.0.0"

# ── Sévérités ────────────────────────────────────────────────────────────────

class Severity:
    ERROR = "🔴 ERROR"
    WARNING = "🟡 WARNING"
    INFO = "🟢 INFO"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """Un problème détecté."""
    agent: str
    severity: str
    rule: str
    message: str
    fix_hint: str = ""

    @property
    def is_error(self) -> bool:
        return "ERROR" in self.severity


@dataclass
class LintReport:
    """Rapport complet de lint."""
    findings: list[Finding] = field(default_factory=list)
    agents_checked: int = 0
    agents_clean: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.is_error]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if "WARNING" in f.severity]


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_agents(project_root: Path) -> list[tuple[str, Path]]:
    """Découvre tous les fichiers agent .md dans _bmad/*/agents/."""
    bmad_dir = project_root / "_bmad"
    agents = []
    if not bmad_dir.is_dir():
        return agents

    for module_dir in sorted(bmad_dir.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith("_"):
            continue
        agents_dir = module_dir / "agents"
        if not agents_dir.is_dir():
            continue
        # Direct .md files
        for md_file in sorted(agents_dir.glob("*.md")):
            agents.append((module_dir.name, md_file))
        # Subdirectory agents (e.g., tech-writer/tech-writer.md, storyteller/storyteller.md)
        for sub_dir in sorted(agents_dir.iterdir()):
            if sub_dir.is_dir():
                for md_file in sorted(sub_dir.glob("*.md")):
                    if md_file.stem == sub_dir.name:  # match dir name
                        agents.append((module_dir.name, md_file))
    return agents


def load_manifest(project_root: Path) -> dict[str, dict]:
    """Charge le agent-manifest.csv en dict indexé par name."""
    manifest_path = project_root / "_bmad" / "_config" / "agent-manifest.csv"
    if not manifest_path.is_file():
        return {}
    result = {}
    with manifest_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "").strip().strip('"')
            result[name] = {k: v.strip().strip('"') for k, v in row.items()}
    return result


# ── Parsing ──────────────────────────────────────────────────────────────────

def extract_frontmatter_name(content: str) -> str:
    """Extrait le 'name' du frontmatter YAML."""
    m = re.search(r'^name:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_agent_id(content: str) -> str:
    """Extrait l'agent id de la balise <agent>."""
    m = re.search(r'<agent\s+id="([^"]+)"', content)
    return m.group(1) if m else ""


def extract_agent_name(content: str) -> str:
    """Extrait le name= de la balise <agent>."""
    m = re.search(r'<agent\s[^>]*name="([^"]+)"', content)
    return m.group(1) if m else ""


def extract_config_path(content: str) -> str:
    """Extrait le chemin config.yaml de l'activation step 2."""
    m = re.search(r'Load and read \{project-root\}/([^\s]+config\.yaml)', content)
    return m.group(1) if m else ""


def extract_menu_cmds(content: str) -> list[tuple[str, str, dict]]:
    """Extrait toutes les commandes menu avec leurs attributs.
    
    Retourne: [(cmd_code, description, {attr: value, ...}), ...]
    """
    items = []
    # Match <item cmd="..." ...>[CODE] Description</item>
    pattern = re.compile(
        r'<item\s+([^>]+)>\s*\[([A-Z]{2,3})\]\s*(.*?)</item>',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        attrs_str = m.group(1)
        cmd_code = m.group(2)
        description = m.group(3).strip()

        # Parse attributes
        attrs = {}
        for attr_m in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
            attrs[attr_m.group(1)] = attr_m.group(2)

        items.append((cmd_code, description, attrs))
    return items


def extract_persona_blocks(content: str) -> dict[str, bool]:
    """Vérifie la présence des blocs persona obligatoires."""
    required = [
        "role", "identity", "voice", "decision_framework",
        "weaknesses", "output_preferences", "communication_style", "principles"
    ]
    result = {}
    for block in required:
        # Match both <block> and <block ...>
        pattern = rf'<{block}[\s>]'
        result[block] = bool(re.search(pattern, content))
    return result


def extract_handler_types(content: str) -> set[str]:
    """Extrait les types de handlers déclarés."""
    types = set()
    for m in re.finditer(r'<handler\s+type="(\w+)"', content):
        types.add(m.group(1))
    return types


# ── Lint Rules ───────────────────────────────────────────────────────────────

def lint_unique_cmds(agent_name: str, cmds: list[tuple[str, str, dict]]) -> list[Finding]:
    """Règle: chaque cmd code doit être unique dans un agent."""
    findings = []
    seen: dict[str, int] = {}
    for code, _desc, _ in cmds:
        seen[code] = seen.get(code, 0) + 1

    for code, count in seen.items():
        if count > 1:
            findings.append(Finding(
                agent=agent_name,
                severity=Severity.ERROR,
                rule="unique-cmd",
                message=f"Commande [{code}] dupliquée ({count}x)",
                fix_hint=f"Renommer l'une des commandes [{code}] avec un code unique"
            ))
    return findings


def lint_persona_completeness(agent_name: str, blocks: dict[str, bool]) -> list[Finding]:
    """Règle: tous les blocs persona obligatoires doivent être présents."""
    findings = []
    for block, present in blocks.items():
        if not present:
            findings.append(Finding(
                agent=agent_name,
                severity=Severity.ERROR if block in ("voice", "decision_framework") else Severity.WARNING,
                rule="persona-complete",
                message=f"Bloc <{block}> manquant dans la persona",
                fix_hint=f"Ajouter le bloc <{block}> dans la section <persona>"
            ))
    return findings


def lint_manifest_sync(
    agent_name: str,
    frontmatter_name: str,
    agent_xml_name: str,
    module: str,
    manifest: dict[str, dict],
) -> list[Finding]:
    """Règle: l'agent doit être dans le manifest avec des données cohérentes."""
    findings = []

    # Trouver l'entrée manifest correspondante
    # Le manifest utilise le frontmatter name ou une version slug
    slug = frontmatter_name.lower().replace(" ", "-")
    manifest_entry = manifest.get(slug) or manifest.get(frontmatter_name)

    if not manifest_entry:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="manifest-sync",
            message=f"Agent '{slug}' absent du agent-manifest.csv",
            fix_hint="Ajouter une entrée dans _bmad/_config/agent-manifest.csv"
        ))
        return findings

    # Check module match
    manifest_module = manifest_entry.get("module", "")
    if manifest_module and manifest_module != module:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="manifest-sync",
            message=f"Module mismatch: agent dans '{module}' mais manifest dit '{manifest_module}'",
            fix_hint="Corriger le module dans agent-manifest.csv"
        ))

    # Check displayName match
    manifest_display = manifest_entry.get("displayName", "")
    if manifest_display and agent_xml_name and manifest_display != agent_xml_name:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="manifest-sync",
            message=f"DisplayName mismatch: agent='{agent_xml_name}' vs manifest='{manifest_display}'",
            fix_hint="Synchroniser displayName dans agent-manifest.csv"
        ))

    return findings


def lint_referenced_files(
    agent_name: str,
    cmds: list[tuple[str, str, dict]],
    project_root: Path,
) -> list[Finding]:
    """Règle: les fichiers référencés (exec, workflow, data) doivent exister."""
    findings = []
    file_attrs = ("exec", "workflow", "data")

    for code, _desc, attrs in cmds:
        for attr in file_attrs:
            if attr not in attrs:
                continue
            path_str = attrs[attr]
            if path_str == "todo":
                findings.append(Finding(
                    agent=agent_name,
                    severity=Severity.WARNING,
                    rule="file-exists",
                    message=f"[{code}] {attr}=\"todo\" — workflow non implémenté",
                    fix_hint=f"Implémenter le workflow pour [{code}]"
                ))
                continue

            # Resolve {project-root}
            resolved = path_str.replace("{project-root}/", "")
            full_path = project_root / resolved
            if not full_path.is_file():
                findings.append(Finding(
                    agent=agent_name,
                    severity=Severity.ERROR,
                    rule="file-exists",
                    message=f"[{code}] {attr}=\"{resolved}\" — fichier introuvable",
                    fix_hint="Vérifier le chemin ou créer le fichier manquant"
                ))

    return findings


def lint_handler_coverage(
    agent_name: str,
    cmds: list[tuple[str, str, dict]],
    declared_handlers: set[str],
) -> list[Finding]:
    """Règle: les handlers déclarés doivent couvrir les attributs utilisés dans le menu."""
    findings = []
    used_types: set[str] = set()

    for _code, _desc, attrs in cmds:
        for attr_type in ("exec", "workflow", "data", "action"):
            if attr_type in attrs:
                used_types.add(attr_type)

    missing = used_types - declared_handlers
    for handler_type in missing:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="handler-coverage",
            message=f"Menu utilise '{handler_type}=' mais aucun <handler type=\"{handler_type}\"> déclaré",
            fix_hint=f"Ajouter un handler type=\"{handler_type}\" dans <menu-handlers>"
        ))

    return findings


def lint_config_path(
    agent_name: str,
    config_path: str,
    module: str,
    project_root: Path,
) -> list[Finding]:
    """Règle: le config.yaml référencé doit exister et correspondre au module."""
    findings = []
    if not config_path:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="config-path",
            message="Pas de chargement config.yaml trouvé dans l'activation step 2",
            fix_hint="Ajouter le chargement de config.yaml dans l'étape 2 d'activation"
        ))
        return findings

    # Check file exists
    full_path = project_root / config_path
    if not full_path.is_file():
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="config-path",
            message=f"Config '{config_path}' introuvable",
            fix_hint="Vérifier le chemin du config.yaml"
        ))

    # Check module match
    expected_prefix = f"_bmad/{module}/"
    if not config_path.startswith(expected_prefix):
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="config-path",
            message=f"Config path '{config_path}' ne correspond pas au module '{module}'",
            fix_hint=f"Utiliser _bmad/{module}/config.yaml"
        ))

    return findings


# ── Main Lint ────────────────────────────────────────────────────────────────

def lint_agent(
    module: str,
    agent_path: Path,
    project_root: Path,
    manifest: dict[str, dict],
) -> list[Finding]:
    """Lint complet d'un fichier agent."""
    content = agent_path.read_text(encoding="utf-8")
    agent_name = agent_path.stem
    if agent_path.parent.name != "agents":
        # subdirectory agent (tech-writer/tech-writer.md)
        agent_name = agent_path.parent.name

    findings: list[Finding] = []

    # Extractions
    frontmatter_name = extract_frontmatter_name(content)
    agent_xml_name = extract_agent_name(content)
    config_path = extract_config_path(content)
    cmds = extract_menu_cmds(content)
    persona_blocks = extract_persona_blocks(content)
    handler_types = extract_handler_types(content)

    # Rules
    findings.extend(lint_unique_cmds(agent_name, cmds))
    findings.extend(lint_persona_completeness(agent_name, persona_blocks))
    findings.extend(lint_manifest_sync(agent_name, frontmatter_name, agent_xml_name, module, manifest))
    findings.extend(lint_referenced_files(agent_name, cmds, project_root))
    findings.extend(lint_handler_coverage(agent_name, cmds, handler_types))
    findings.extend(lint_config_path(agent_name, config_path, module, project_root))

    return findings


def run_lint(project_root: Path, target_agent: str | None = None) -> LintReport:
    """Exécute le lint sur tous les agents (ou un seul)."""
    report = LintReport()
    manifest = load_manifest(project_root)
    agents = discover_agents(project_root)

    for module, agent_path in agents:
        agent_name = agent_path.stem
        if agent_path.parent.name != "agents":
            agent_name = agent_path.parent.name

        if target_agent and agent_name != target_agent:
            continue

        report.agents_checked += 1
        findings = lint_agent(module, agent_path, project_root, manifest)
        report.findings.extend(findings)

        if not any(f.agent == agent_name for f in findings):
            report.agents_clean += 1

    return report


# ── Output ───────────────────────────────────────────────────────────────────

def format_text(report: LintReport) -> str:
    """Formatage texte du rapport."""
    lines = [
        f"{'=' * 60}",
        f"  Agent Lint Report — v{AGENT_LINT_VERSION}",
        f"  Agents: {report.agents_checked} checked, {report.agents_clean} clean",
        f"  Findings: {len(report.errors)} errors, {len(report.warnings)} warnings, "
        f"{len(report.findings) - len(report.errors) - len(report.warnings)} info",
        f"{'=' * 60}",
    ]

    if not report.findings:
        lines.append("\n  ✅ Tous les agents sont conformes !")
        return "\n".join(lines)

    # Group by agent
    by_agent: dict[str, list[Finding]] = {}
    for f in report.findings:
        by_agent.setdefault(f.agent, []).append(f)

    for agent, findings in sorted(by_agent.items()):
        error_count = sum(1 for f in findings if f.is_error)
        warn_count = len(findings) - error_count
        status = "❌" if error_count else "⚠️"
        lines.append(f"\n{status} {agent} ({error_count} errors, {warn_count} warnings)")
        for f in findings:
            lines.append(f"  {f.severity} [{f.rule}] {f.message}")
            if f.fix_hint:
                lines.append(f"    💡 {f.fix_hint}")

    return "\n".join(lines)


def format_json(report: LintReport) -> str:
    """Formatage JSON du rapport."""
    return json.dumps({
        "version": AGENT_LINT_VERSION,
        "agents_checked": report.agents_checked,
        "agents_clean": report.agents_clean,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "findings": [
            {
                "agent": f.agent,
                "severity": f.severity,
                "rule": f.rule,
                "message": f.message,
                "fix_hint": f.fix_hint,
            }
            for f in report.findings
        ],
    }, indent=2, ensure_ascii=False)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint structurel des agents BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, required=True,
        help="Racine du projet BMAD",
    )
    parser.add_argument(
        "--agent", type=str, default=None,
        help="Nom de l'agent à linter (par défaut: tous)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Sortie en format JSON",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Logs détaillés",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    project_root = args.project_root.resolve()
    if not (project_root / "_bmad").is_dir():
        print(f"❌ Pas de répertoire _bmad/ trouvé dans {project_root}", file=sys.stderr)
        return 1

    report = run_lint(project_root, args.agent)

    if args.json:
        print(format_json(report))
    else:
        print(format_text(report))

    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
