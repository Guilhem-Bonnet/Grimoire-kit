#!/usr/bin/env python3
"""
skill-validator.py — Validateur déterministe de skills Grimoire.
================================================================

Vérifie la conformité des SKILL.md et fichiers associés dans .github/skills/.
14 règles déterministes inspirées du BMAD upstream skill-validator.

Usage :
  python3 skill-validator.py --project-root .
  python3 skill-validator.py --project-root . --skill grimoire-edge-case-hunter
  python3 skill-validator.py --project-root . --json
  python3 skill-validator.py --project-root . --strict

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKILL_VALIDATOR_VERSION = "1.0.0"

# ── Sévérité ──────────────────────────────────────────────────────────────────

CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}

# ── Regex ─────────────────────────────────────────────────────────────────────

NAME_REGEX = re.compile(r"^grimoire-[a-z0-9]+(-[a-z0-9]+)*$")
STEP_FILENAME_REGEX = re.compile(r"^step-\d{2}[a-z]?-[a-z0-9-]+\.md$")
TIME_ESTIMATE_PATTERNS = [
    re.compile(r"takes?\s+\d+\s*min", re.IGNORECASE),
    re.compile(r"~\s*\d+\s*min", re.IGNORECASE),
    re.compile(r"estimated\s+time", re.IGNORECASE),
    re.compile(r"\bETA\b"),
]
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "message": self.message,
        }


@dataclass
class SkillReport:
    skill_dir: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == CRITICAL for f in self.findings)

    @property
    def has_high_or_above(self) -> bool:
        return any(f.severity in (CRITICAL, HIGH) for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill_dir,
            "findings_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


# ── Frontmatter parser ───────────────────────────────────────────────────────


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse simple YAML frontmatter from markdown content."""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().strip("'\"")
            result[key.strip()] = value
    return result


def body_after_frontmatter(content: str) -> str:
    """Return content after the closing --- of frontmatter."""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return content
    return content[m.end() :].strip()


# ── Règles ────────────────────────────────────────────────────────────────────


def check_skill_01(skill_dir: Path) -> list[Finding]:
    """SKILL-01: SKILL.md must exist."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [
            Finding(
                "SKILL-01",
                CRITICAL,
                str(skill_dir),
                "SKILL.md manquant dans le répertoire du skill",
            )
        ]
    return []


def check_skill_02(skill_md: Path, fm: dict[str, str]) -> list[Finding]:
    """SKILL-02: SKILL.md must have 'name' in frontmatter."""
    if "name" not in fm:
        return [
            Finding(
                "SKILL-02",
                CRITICAL,
                str(skill_md),
                "Champ 'name' manquant dans le frontmatter",
            )
        ]
    return []


def check_skill_03(skill_md: Path, fm: dict[str, str]) -> list[Finding]:
    """SKILL-03: SKILL.md must have 'description' in frontmatter."""
    if "description" not in fm:
        return [
            Finding(
                "SKILL-03",
                CRITICAL,
                str(skill_md),
                "Champ 'description' manquant dans le frontmatter",
            )
        ]
    return []


def check_skill_04(skill_md: Path, fm: dict[str, str]) -> list[Finding]:
    """SKILL-04: name format — must start with grimoire- and use lowercase."""
    name = fm.get("name", "")
    if name and not NAME_REGEX.match(name):
        return [
            Finding(
                "SKILL-04",
                HIGH,
                str(skill_md),
                f"Le nom '{name}' ne respecte pas le format grimoire-[a-z0-9-]+",
            )
        ]
    return []


def check_skill_05(
    skill_md: Path, fm: dict[str, str], skill_dir: Path
) -> list[Finding]:
    """SKILL-05: name must match directory name."""
    name = fm.get("name", "")
    dirname = skill_dir.name
    if name and name != dirname:
        return [
            Finding(
                "SKILL-05",
                HIGH,
                str(skill_md),
                f"Le nom '{name}' ne correspond pas au répertoire '{dirname}'",
            )
        ]
    return []


def check_skill_06(skill_md: Path, fm: dict[str, str]) -> list[Finding]:
    """SKILL-06: description quality — length and 'Use when' trigger."""
    desc = fm.get("description", "")
    findings: list[Finding] = []
    if desc and len(desc) < 30:
        findings.append(
            Finding(
                "SKILL-06",
                MEDIUM,
                str(skill_md),
                f"Description trop courte ({len(desc)} chars, min recommandé: 30)",
            )
        )
    if desc and "use when" not in desc.lower():
        findings.append(
            Finding(
                "SKILL-06",
                MEDIUM,
                str(skill_md),
                "Description sans phrase 'Use when:' — rend le trigger matching moins fiable",
            )
        )
    return findings


def check_skill_07(skill_md: Path, content: str) -> list[Finding]:
    """SKILL-07: SKILL.md must have body content after frontmatter."""
    body = body_after_frontmatter(content)
    if not body or len(body) < 20:
        return [
            Finding(
                "SKILL-07",
                HIGH,
                str(skill_md),
                "SKILL.md n'a pas de contenu après le frontmatter",
            )
        ]
    return []


def check_wf_01(workflow_md: Path, fm: dict[str, str]) -> list[Finding]:
    """WF-01: workflow.md frontmatter must NOT have 'name'."""
    if "name" in fm:
        return [
            Finding(
                "WF-01",
                MEDIUM,
                str(workflow_md),
                "workflow.md ne doit pas avoir de champ 'name' dans le frontmatter",
            )
        ]
    return []


def check_wf_02(workflow_md: Path, fm: dict[str, str]) -> list[Finding]:
    """WF-02: workflow.md frontmatter must NOT have 'description'."""
    if "description" in fm:
        return [
            Finding(
                "WF-02",
                MEDIUM,
                str(workflow_md),
                "workflow.md ne doit pas avoir de champ 'description' dans le frontmatter",
            )
        ]
    return []


def check_step_01(step_file: Path) -> list[Finding]:
    """STEP-01: step filename format."""
    if not STEP_FILENAME_REGEX.match(step_file.name):
        return [
            Finding(
                "STEP-01",
                MEDIUM,
                str(step_file),
                f"Nom de fichier '{step_file.name}' ne respecte pas le format step-NN[-suffix].md",
            )
        ]
    return []


def check_step_06(step_file: Path, fm: dict[str, str]) -> list[Finding]:
    """STEP-06: step frontmatter must NOT have name or description."""
    findings: list[Finding] = []
    if "name" in fm:
        findings.append(
            Finding(
                "STEP-06",
                LOW,
                str(step_file),
                "Les fichiers step ne doivent pas avoir de champ 'name'",
            )
        )
    if "description" in fm:
        findings.append(
            Finding(
                "STEP-06",
                LOW,
                str(step_file),
                "Les fichiers step ne doivent pas avoir de champ 'description'",
            )
        )
    return findings


def check_step_07(skill_dir: Path) -> list[Finding]:
    """STEP-07: step count 2-10 if step files exist."""
    step_files = list(skill_dir.glob("step-*.md"))
    # Also check subdirectories named "steps"
    steps_dir = skill_dir / "steps"
    if steps_dir.is_dir():
        step_files.extend(steps_dir.glob("step-*.md"))
    if not step_files:
        return []  # No steps is fine (inlined skills)
    count = len(step_files)
    if count < 2:
        return [
            Finding(
                "STEP-07",
                MEDIUM,
                str(skill_dir),
                f"Step-file architecture avec seulement {count} step (min: 2)",
            )
        ]
    if count > 10:
        return [
            Finding(
                "STEP-07",
                LOW,
                str(skill_dir),
                f"Step-file architecture avec {count} steps (max recommandé: 10)",
            )
        ]
    return []


def check_seq_02(skill_dir: Path) -> list[Finding]:
    """SEQ-02: no time estimates in skill files."""
    findings: list[Finding] = []
    for md_file in skill_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in TIME_ESTIMATE_PATTERNS:
            if pattern.search(content):
                findings.append(
                    Finding(
                        "SEQ-02",
                        LOW,
                        str(md_file),
                        f"Estimation de temps détectée (pattern: {pattern.pattern})",
                    )
                )
                break  # One per file is enough
    return findings


# ── Orchestration ─────────────────────────────────────────────────────────────


def validate_skill(skill_dir: Path) -> SkillReport:
    """Run all deterministic checks on a single skill directory."""
    report = SkillReport(skill_dir=skill_dir.name)

    # SKILL-01
    report.findings.extend(check_skill_01(skill_dir))
    if report.has_critical:
        return report  # Can't continue without SKILL.md

    skill_md = skill_dir / "SKILL.md"
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        report.findings.append(
            Finding("SKILL-01", CRITICAL, str(skill_md), "SKILL.md illisible")
        )
        return report

    fm = parse_frontmatter(content)

    # SKILL-02..07
    report.findings.extend(check_skill_02(skill_md, fm))
    report.findings.extend(check_skill_03(skill_md, fm))
    report.findings.extend(check_skill_04(skill_md, fm))
    report.findings.extend(check_skill_05(skill_md, fm, skill_dir))
    report.findings.extend(check_skill_06(skill_md, fm))
    report.findings.extend(check_skill_07(skill_md, content))

    # WF-01, WF-02 — workflow.md if it exists
    workflow_md = skill_dir / "workflow.md"
    if workflow_md.exists():
        try:
            wf_content = workflow_md.read_text(encoding="utf-8")
            wf_fm = parse_frontmatter(wf_content)
            report.findings.extend(check_wf_01(workflow_md, wf_fm))
            report.findings.extend(check_wf_02(workflow_md, wf_fm))
        except (OSError, UnicodeDecodeError):
            pass

    # STEP-01, STEP-06 — step files
    for step_file in sorted(skill_dir.rglob("step-*.md")):
        report.findings.extend(check_step_01(step_file))
        try:
            step_content = step_file.read_text(encoding="utf-8")
            step_fm = parse_frontmatter(step_content)
            report.findings.extend(check_step_06(step_file, step_fm))
        except (OSError, UnicodeDecodeError):
            pass

    # STEP-07
    report.findings.extend(check_step_07(skill_dir))

    # SEQ-02
    report.findings.extend(check_seq_02(skill_dir))

    return report


def discover_skills(project_root: Path) -> list[Path]:
    """Discover all skill directories under .github/skills/."""
    skills_dir = project_root / ".github" / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(
        d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


# ── Output formatters ────────────────────────────────────────────────────────


def format_human(reports: list[SkillReport]) -> str:
    """Format reports for human-readable output."""
    lines: list[str] = []
    total_findings = sum(len(r.findings) for r in reports)

    lines.append(f"Grimoire Skill Validator v{SKILL_VALIDATOR_VERSION}")
    lines.append(f"Skills analysés: {len(reports)}")
    lines.append(f"Findings totaux: {total_findings}")
    lines.append("")

    for report in reports:
        if not report.findings:
            lines.append(f"  ✅ {report.skill_dir}")
        else:
            icon = '❌' if report.has_critical else '⚠️'
            count = len(report.findings)
            lines.append(f"  {icon} {report.skill_dir} ({count} findings)")
            for f in sorted(report.findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 99)):
                lines.append(f"    [{f.severity}] {f.rule}: {f.message}")
                if f.file != str(report.skill_dir):
                    lines.append(f"      → {f.file}")

    lines.append("")
    if total_findings == 0:
        lines.append("✅ Tous les skills sont conformes.")
    else:
        by_severity: dict[str, int] = {}
        for r in reports:
            for f in r.findings:
                by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        sorted_sev = sorted(
            by_severity.items(),
            key=lambda x: SEVERITY_ORDER.get(x[0], 99),
        )
        summary = ", ".join(f"{count} {sev}" for sev, count in sorted_sev)
        lines.append(f"⚠️  {summary}")

    return "\n".join(lines)


def format_json(reports: list[SkillReport]) -> str:
    """Format reports as JSON."""
    return json.dumps(
        {
            "version": SKILL_VALIDATOR_VERSION,
            "skills_count": len(reports),
            "findings_count": sum(len(r.findings) for r in reports),
            "reports": [r.to_dict() for r in reports],
        },
        indent=2,
        ensure_ascii=False,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validateur déterministe de skills Grimoire",
    )
    p.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Racine du projet (défaut: répertoire courant)",
    )
    p.add_argument(
        "--skill",
        type=str,
        default=None,
        help="Valider un seul skill par nom de répertoire",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Sortie au format JSON",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 si findings HIGH ou CRITICAL",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SKILL_VALIDATOR_VERSION}",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = (args.project_root or Path.cwd()).resolve()

    if args.skill:
        skill_dir = project_root / ".github" / "skills" / args.skill
        if not skill_dir.is_dir():
            print(f"❌ Skill non trouvé: {skill_dir}", file=sys.stderr)
            return 1
        skill_dirs = [skill_dir]
    else:
        skill_dirs = discover_skills(project_root)

    if not skill_dirs:
        print("Aucun skill trouvé dans .github/skills/", file=sys.stderr)
        return 0

    reports = [validate_skill(d) for d in skill_dirs]

    if args.json_output:
        print(format_json(reports))
    else:
        print(format_human(reports))

    if args.strict and any(r.has_high_or_above for r in reports):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
