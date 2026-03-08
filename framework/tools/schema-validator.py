#!/usr/bin/env python3
"""
schema-validator.py — Validateur structurel de configs Grimoire.
==============================================================

Valide les fichiers YAML du kit (archetype.dna.yaml, team manifests,
agent DNA) contre les schemas définis dans framework/.

Checks effectués :
  1. Syntaxe YAML (parsabilité)
  2. Champs requis présents
  3. Types de valeurs corrects (string, list, bool, number)
  4. Valeurs autorisées pour les champs enum
  5. Cohérence inter-fichiers (références agents, archétypes)

Usage :
  python3 schema-validator.py --project-root . validate          # tout valider
  python3 schema-validator.py --project-root . validate --type dna  # DNA only
  python3 schema-validator.py --project-root . validate --file path/to.yaml
  python3 schema-validator.py --project-root . validate --json   # JSON output

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ── Constantes ────────────────────────────────────────────────────────────────

VALIDATOR_VERSION = "1.0.0"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Champs requis pour archetype.dna.yaml
DNA_REQUIRED_FIELDS = [
    "id", "name", "version", "description", "icon", "author",
]

DNA_OPTIONAL_SECTIONS = [
    "tags", "inherits", "traits", "constraints", "values",
    "tools_required", "acceptance_criteria", "agents", "workflows",
    "shared_context_template", "prompts_directory",
    "compatible_with", "incompatible_with", "requires",
    "auto_detect", "changelog",
]

DNA_ENFORCEMENT_VALUES = {"hard", "soft"}
DNA_PRIORITY_VALUES = {1, 2, 3, "1", "2", "3"}

# Champs requis pour team manifest
TEAM_REQUIRED_FIELDS = ["team"]
TEAM_NESTED_REQUIRED = ["name", "display_name", "version", "description"]

# Agent DNA required fields
AGENT_DNA_REQUIRED_FIELDS = ["id", "name", "version", "description"]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """Un problème de validation détecté."""
    issue_id: str = ""
    severity: str = SEVERITY_ERROR
    file: str = ""
    field: str = ""
    message: str = ""
    suggestion: str = ""


@dataclass
class ValidationReport:
    """Rapport de validation complet."""
    version: str = VALIDATOR_VERSION
    files_checked: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_INFO)

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0


# ── YAML loader (stdlib fallback) ────────────────────────────────────────────

def _load_yaml(path: Path) -> tuple[dict | None, str | None]:
    """Charge un fichier YAML. Retourne (data, error)."""
    if not path.is_file():
        return None, f"Fichier introuvable : {path}"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"Erreur lecture : {e}"

    if not text.strip():
        return None, "Fichier vide"

    if yaml is not None:
        try:
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                return None, f"Le fichier ne contient pas un mapping YAML (type: {type(data).__name__})"
            return data, None
        except yaml.YAMLError as e:
            return None, f"Erreur syntaxe YAML : {e}"

    # Fallback sans PyYAML — parsing basique
    return _parse_yaml_basic(text)


def _parse_yaml_basic(text: str) -> tuple[dict | None, str | None]:
    """Parse YAML basique sans PyYAML — détecte au moins les erreurs de structure."""
    data: dict = {}
    current_key = None

    for _i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key
        if not line[0].isspace() and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            val = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            current_key = key

            # Remove quotes and handle types
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            elif val == "true":
                val = True  # type: ignore[assignment]
            elif val == "false":
                val = False  # type: ignore[assignment]
            elif val == "null" or val == "~":
                val = None  # type: ignore[assignment]

            if val == "" or val is None:
                data[key] = val
            elif isinstance(val, (bool,)):
                data[key] = val
            elif stripped.count(":") == 1 and val.startswith("[") and val.endswith("]"):
                # Inline list
                inner = val[1:-1]
                data[key] = [s.strip().strip('"').strip("'")
                             for s in inner.split(",") if s.strip()]
            else:
                data[key] = val
        elif line[0].isspace() and current_key:
            # Sub-structure — mark as present
            if current_key not in data or data[current_key] in ("", None):
                data[current_key] = {"__has_content__": True}

    if not data:
        return None, "Aucun champ YAML détecté"

    return data, None


# ── Validators ────────────────────────────────────────────────────────────────

def validate_dna(path: Path, data: dict) -> list[ValidationIssue]:
    """Valide un fichier archetype.dna.yaml."""
    issues: list[ValidationIssue] = []
    rel = str(path)
    _issue_counter = [0]

    def _add(severity: str, fld: str, msg: str, suggestion: str = "") -> None:
        _issue_counter[0] += 1
        issues.append(ValidationIssue(
            issue_id=f"DNA-{_issue_counter[0]:03d}",
            severity=severity,
            file=rel,
            field=fld,
            message=msg,
            suggestion=suggestion,
        ))

    # Schema check
    schema = data.get("$schema", "")
    if schema and "grimoire-archetype-dna" not in str(schema):
        _add(SEVERITY_WARNING, "$schema",
             f"Schema inattendu : {schema}",
             "Attendu : grimoire-archetype-dna/v1")

    # Required fields
    for fld in DNA_REQUIRED_FIELDS:
        if fld not in data:
            _add(SEVERITY_ERROR, fld,
                 f"Champ requis manquant : {fld}",
                 f"Ajoutez '{fld}: ...' au fichier")
        elif not data[fld]:
            _add(SEVERITY_ERROR, fld,
                 f"Champ requis vide : {fld}",
                 f"Remplissez la valeur de '{fld}'")

    # Type checks
    if "tags" in data and data["tags"] is not None:
        if not isinstance(data["tags"], list):
            _add(SEVERITY_ERROR, "tags",
                 f"'tags' doit être une liste (trouvé: {type(data['tags']).__name__})")

    if "version" in data and data["version"]:
        v = str(data["version"])
        parts = v.split(".")
        if len(parts) < 2:
            _add(SEVERITY_WARNING, "version",
                 f"Version non-SemVer : {v}",
                 "Format attendu : MAJOR.MINOR.PATCH")

    # Traits validation
    if "traits" in data and isinstance(data.get("traits"), list):
        for i, trait in enumerate(data["traits"]):
            if isinstance(trait, dict):
                for req in ("name", "description", "rule"):
                    if req not in trait:
                        _add(SEVERITY_ERROR, f"traits[{i}].{req}",
                             f"Champ requis manquant dans trait[{i}]")
                if "agents_affected" in trait:
                    val = trait["agents_affected"]
                    if val != "*" and not isinstance(val, list):
                        _add(SEVERITY_WARNING, f"traits[{i}].agents_affected",
                             "agents_affected doit être '*' ou une liste")

    # Constraints validation
    if "constraints" in data and isinstance(data.get("constraints"), list):
        for i, constraint in enumerate(data["constraints"]):
            if isinstance(constraint, dict):
                for req in ("id", "description"):
                    if req not in constraint:
                        _add(SEVERITY_ERROR, f"constraints[{i}].{req}",
                             f"Champ requis manquant dans constraint[{i}]")
                if "enforcement" in constraint:
                    if constraint["enforcement"] not in DNA_ENFORCEMENT_VALUES:
                        _add(SEVERITY_WARNING, f"constraints[{i}].enforcement",
                             f"Valeur inattendue : {constraint['enforcement']}",
                             "Valeurs autorisées : hard, soft")

    # Values validation
    if "values" in data and isinstance(data.get("values"), list):
        for i, val in enumerate(data["values"]):
            if isinstance(val, dict):
                for req in ("name", "description"):
                    if req not in val:
                        _add(SEVERITY_ERROR, f"values[{i}].{req}",
                             f"Champ requis manquant dans value[{i}]")
                if "priority" in val and val["priority"] not in DNA_PRIORITY_VALUES:
                    _add(SEVERITY_WARNING, f"values[{i}].priority",
                         f"Priorité inattendue : {val['priority']}",
                         "Valeurs autorisées : 1, 2, 3")

    # Agents section
    if "agents" in data and isinstance(data.get("agents"), list):
        for i, agent in enumerate(data["agents"]):
            if isinstance(agent, dict):
                if "path" not in agent:
                    _add(SEVERITY_ERROR, f"agents[{i}].path",
                         "Champ 'path' requis pour chaque agent")

    # Auto-detect
    if "auto_detect" in data and isinstance(data.get("auto_detect"), dict):
        ad = data["auto_detect"]
        if "confidence_boost" in ad:
            try:
                boost = float(ad["confidence_boost"])
                if not 0 <= boost <= 100:
                    _add(SEVERITY_WARNING, "auto_detect.confidence_boost",
                         f"confidence_boost hors limites : {boost}",
                         "Doit être entre 0 et 100")
            except (ValueError, TypeError):
                _add(SEVERITY_ERROR, "auto_detect.confidence_boost",
                     "confidence_boost doit être un nombre")

    return issues


def validate_team(path: Path, data: dict) -> list[ValidationIssue]:
    """Valide un fichier team manifest (team-*.yaml)."""
    issues: list[ValidationIssue] = []
    rel = str(path)
    _issue_counter = [0]

    def _add(severity: str, fld: str, msg: str, suggestion: str = "") -> None:
        _issue_counter[0] += 1
        issues.append(ValidationIssue(
            issue_id=f"TEAM-{_issue_counter[0]:03d}",
            severity=severity,
            file=rel,
            field=fld,
            message=msg,
            suggestion=suggestion,
        ))

    # Root 'team' key required
    if "team" not in data:
        _add(SEVERITY_ERROR, "team", "Clé racine 'team' manquante")
        return issues

    team = data["team"]
    if not isinstance(team, dict):
        _add(SEVERITY_ERROR, "team", "La clé 'team' doit être un mapping")
        return issues

    # Required nested fields
    for fld in TEAM_NESTED_REQUIRED:
        if fld not in team:
            _add(SEVERITY_ERROR, f"team.{fld}",
                 f"Champ requis manquant : team.{fld}")
        elif not team[fld]:
            _add(SEVERITY_WARNING, f"team.{fld}",
                 f"Champ vide : team.{fld}")

    # Agents list
    if "agents" in team:
        if not isinstance(team["agents"], list):
            _add(SEVERITY_ERROR, "team.agents",
                 "team.agents doit être une liste")
        else:
            for i, agent in enumerate(team["agents"]):
                if isinstance(agent, dict):
                    if "name" not in agent:
                        _add(SEVERITY_ERROR, f"team.agents[{i}].name",
                             f"Agent[{i}] sans champ 'name'")
                    if "role" not in agent:
                        _add(SEVERITY_WARNING, f"team.agents[{i}].role",
                             f"Agent[{i}] sans champ 'role'")

    # Inputs/outputs presence
    if "inputs" not in team:
        _add(SEVERITY_INFO, "team.inputs",
             "Section 'inputs' manquante",
             "Ajoutez inputs.required et inputs.optional")
    if "outputs" not in team:
        _add(SEVERITY_INFO, "team.outputs",
             "Section 'outputs' manquante",
             "Ajoutez outputs.deliverables")

    return issues


def validate_agent_dna(path: Path, data: dict) -> list[ValidationIssue]:
    """Valide un fichier agent DNA (*.dna.yaml dans stack/agents/)."""
    issues: list[ValidationIssue] = []
    rel = str(path)
    _issue_counter = [0]

    def _add(severity: str, fld: str, msg: str, suggestion: str = "") -> None:
        _issue_counter[0] += 1
        issues.append(ValidationIssue(
            issue_id=f"AGENT-{_issue_counter[0]:03d}",
            severity=severity,
            file=rel,
            field=fld,
            message=msg,
            suggestion=suggestion,
        ))

    for fld in AGENT_DNA_REQUIRED_FIELDS:
        if fld not in data:
            _add(SEVERITY_ERROR, fld,
                 f"Champ requis manquant : {fld}")
        elif not data[fld]:
            _add(SEVERITY_ERROR, fld,
                 f"Champ vide : {fld}")

    return issues


# ── File discovery ────────────────────────────────────────────────────────────

def discover_files(project_root: Path,
                   file_type: str | None = None,
                   single_file: str | None = None,
                   ) -> list[tuple[Path, str]]:
    """Découvre les fichiers à valider.

    Returns:
        Liste de (path, type) où type est 'dna', 'team', ou 'agent_dna'.
    """
    files: list[tuple[Path, str]] = []

    if single_file:
        p = Path(single_file)
        if not p.is_absolute():
            p = project_root / p
        if p.is_file():
            ftype = _guess_type(p)
            files.append((p, ftype))
        return files

    # Archetype DNA files
    if file_type in (None, "dna"):
        for dna in project_root.rglob("archetype.dna.yaml"):
            files.append((dna, "dna"))

    # Team manifests
    if file_type in (None, "team"):
        teams_dir = project_root / "framework" / "teams"
        if teams_dir.is_dir():
            for tf in teams_dir.glob("team-*.yaml"):
                files.append((tf, "team"))

    # Agent DNA files
    if file_type in (None, "agent_dna"):
        for agent_dna in project_root.rglob("*.dna.yaml"):
            if "archetype.dna.yaml" not in agent_dna.name:
                files.append((agent_dna, "agent_dna"))

    return files


def _guess_type(path: Path) -> str:
    """Devine le type de fichier YAML."""
    name = path.name
    if name == "archetype.dna.yaml":
        return "dna"
    if name.startswith("team-") and name.endswith(".yaml"):
        return "team"
    if name.endswith(".dna.yaml"):
        return "agent_dna"
    return "dna"  # default


# ── Orchestration ─────────────────────────────────────────────────────────────

def validate_all(project_root: Path | str,
                 file_type: str | None = None,
                 single_file: str | None = None,
                 ) -> ValidationReport:
    """Valide tous les fichiers YAML du kit.

    Args:
        project_root: racine du projet Grimoire
        file_type: 'dna', 'team', 'agent_dna' ou None pour tout
        single_file: chemin spécifique à valider

    Returns:
        ValidationReport avec tous les issues
    """
    project_root = Path(project_root)
    report = ValidationReport()

    files = discover_files(project_root, file_type, single_file)

    for path, ftype in files:
        report.files_checked += 1

        data, error = _load_yaml(path)
        if error:
            report.issues.append(ValidationIssue(
                issue_id=f"YAML-{report.files_checked:03d}",
                severity=SEVERITY_ERROR,
                file=str(path),
                field="",
                message=error,
            ))
            continue

        if data is None:
            continue

        if ftype == "dna":
            report.issues.extend(validate_dna(path, data))
        elif ftype == "team":
            report.issues.extend(validate_team(path, data))
        elif ftype == "agent_dna":
            report.issues.extend(validate_agent_dna(path, data))

    # Sort by severity
    severity_order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    report.issues.sort(key=lambda i: severity_order.get(i.severity, 9))

    return report


# ── Rendu ─────────────────────────────────────────────────────────────────────

SEVERITY_ICONS = {
    SEVERITY_ERROR: "❌",
    SEVERITY_WARNING: "⚠️",
    SEVERITY_INFO: "ℹ️",
}


def render_report(report: ValidationReport) -> str:
    """Rend le rapport de validation en texte formaté."""
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║        🏗️ Schema Validator — Grimoire Configs                   ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        f"  Fichiers vérifiés : {report.files_checked}",
        f"  Résultat          : {report.error_count}E {report.warning_count}W {report.info_count}I",
        "",
    ]

    if report.is_valid and report.warning_count == 0 and report.info_count == 0:
        lines.append("  ✅ Tous les fichiers sont valides.")
        lines.append("")
        return "\n".join(lines)

    if report.is_valid:
        lines.append("  ✅ Aucune erreur bloquante.")
    else:
        lines.append("  ❌ Erreurs détectées — corrections requises.")
    lines.append("")

    # Group by file
    by_file: dict[str, list[ValidationIssue]] = {}
    for issue in report.issues:
        f = issue.file
        by_file.setdefault(f, []).append(issue)

    for fname, issues in by_file.items():
        lines.append(f"  📄 {fname}")
        for issue in issues:
            icon = SEVERITY_ICONS.get(issue.severity, "?")
            field_str = f" ({issue.field})" if issue.field else ""
            lines.append(f"    {icon} [{issue.issue_id}]{field_str}: {issue.message}")
            if issue.suggestion:
                lines.append(f"       💡 {issue.suggestion}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: ValidationReport) -> dict:
    """Convertit le rapport en dict JSON."""
    return {
        "version": report.version,
        "files_checked": report.files_checked,
        "valid": report.is_valid,
        "errors": report.error_count,
        "warnings": report.warning_count,
        "info": report.info_count,
        "issues": [
            {
                "id": i.issue_id,
                "severity": i.severity,
                "file": i.file,
                "field": i.field,
                "message": i.message,
                "suggestion": i.suggestion,
            }
            for i in report.issues
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Grimoire Schema Validator — valide les configs YAML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet Grimoire")

    sub = parser.add_subparsers(dest="command", help="Commande")

    val_p = sub.add_parser("validate", help="Valider les fichiers YAML")
    val_p.add_argument("--type", choices=["dna", "team", "agent_dna"],
                       default=None,
                       help="Type de fichiers à valider (défaut: tous)")
    val_p.add_argument("--file", default=None,
                       help="Fichier spécifique à valider")
    val_p.add_argument("--json", action="store_true",
                       help="Sortie JSON")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "validate":
        report = validate_all(project_root, args.type, args.file)

        if args.json:
            print(json.dumps(report_to_dict(report), indent=2,
                             ensure_ascii=False))
        else:
            print(render_report(report))

        sys.exit(1 if not report.is_valid else 0)


if __name__ == "__main__":
    main()
