"""Architecture Harmony Check — detect architectural dissonances.

Importable as a library::

    from bmad.tools.harmony_check import HarmonyCheck

    hc = HarmonyCheck(project_root=Path("."))
    result = hc.run()
    print(result.score, result.grade)

Or run from CLI::

    python -m bmad.tools.harmony_check --project-root . report
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad.tools._common import BmadTool

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_FILE_LINES = 800
NAMING_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z]+)?$")

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Dissonance:
    """A single architectural dissonance."""

    category: str
    severity: str
    file: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(slots=True)
class ArchScan:
    """Raw architectural scan result."""

    agents: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    cross_refs: dict[str, list[str]] = field(default_factory=dict)
    dissonances: list[Dissonance] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return (
            len(self.agents) + len(self.workflows) + len(self.tools)
            + len(self.configs) + len(self.docs) + len(self.tests)
        )


@dataclass(frozen=True, slots=True)
class HarmonyResult:
    """Structured result of a harmony check."""

    score: int
    grade: str
    total_files: int
    dissonances: tuple[Dissonance, ...]
    category_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "total_files": self.total_files,
            "dissonances": [d.to_dict() for d in self.dissonances],
            "category_counts": dict(self.category_counts),
        }


# ── Detectors ─────────────────────────────────────────────────────────────────

def _scan_project(project_root: Path) -> ArchScan:
    """Full project scan."""
    scan = ArchScan()

    for pattern in ["**/agents/*.md", "**/agents/*.xml", "**/agents/*.yaml"]:
        for f in project_root.glob(pattern):
            if ".git" not in str(f):
                scan.agents.append(str(f.relative_to(project_root)))

    for pattern in ["**/workflows/**/*.md", "**/workflows/**/*.yaml", "**/workflows/**/*.xml"]:
        for f in project_root.glob(pattern):
            if ".git" not in str(f):
                scan.workflows.append(str(f.relative_to(project_root)))

    for f in project_root.glob("**/tools/*.py"):
        if ".git" not in str(f) and "__pycache__" not in str(f):
            scan.tools.append(str(f.relative_to(project_root)))

    for pattern in ["**/*.yaml", "**/*.yml"]:
        for f in project_root.glob(pattern):
            rel = str(f.relative_to(project_root))
            if ".git" not in rel and rel not in scan.workflows:
                scan.configs.append(rel)

    for f in project_root.glob("**/docs/**/*.md"):
        if ".git" not in str(f):
            scan.docs.append(str(f.relative_to(project_root)))

    for f in project_root.glob("**/tests/**/*"):
        if ".git" not in str(f) and f.is_file() and "__pycache__" not in str(f):
            scan.tests.append(str(f.relative_to(project_root)))

    all_files = scan.agents + scan.workflows + scan.tools
    for fpath in all_files:
        full = project_root / fpath
        if full.exists() and full.stat().st_size < 100_000:
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                refs = [
                    other for other in all_files
                    if other != fpath and Path(other).stem in content
                ]
                if refs:
                    scan.cross_refs[fpath] = refs
            except OSError:
                pass

    return scan


def _detect_orphans(scan: ArchScan) -> list[Dissonance]:
    referenced: set[str] = set()
    for refs in scan.cross_refs.values():
        referenced.update(refs)
    return [
        Dissonance(
            "orphan", SEVERITY_MEDIUM, agent,
            "Agent orphelin — non référencé par aucun workflow ou outil",
            "Vérifier si cet agent est utilisé ou s'il peut être retiré",
        )
        for agent in scan.agents
        if agent not in referenced and agent not in scan.cross_refs
    ]


def _detect_naming(scan: ArchScan) -> list[Dissonance]:
    return [
        Dissonance(
            "naming", SEVERITY_LOW, fpath,
            f"Nom de fichier '{Path(fpath).stem}' ne respecte pas la convention kebab-case",
            "Renommer en kebab-case : lettres minuscules et tirets",
        )
        for fpath in scan.agents + scan.workflows + scan.tools
        if not NAMING_PATTERN.match(Path(fpath).stem)
    ]


def _detect_oversized(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    dissonances: list[Dissonance] = []
    for fpath in scan.agents + scan.workflows + scan.tools:
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
            except OSError:
                pass
    return dissonances


def _detect_manifest_mismatch(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    dissonances: list[Dissonance] = []
    agent_stems = {Path(a).stem for a in scan.agents}
    for mpath in project_root.glob("**/agent-manifest.csv"):
        try:
            for line in mpath.read_text(encoding="utf-8").splitlines():
                parts = line.split(",")
                if parts:
                    name = parts[0].strip().lower()
                    if (
                        name
                        and name not in ("agent", "name", "id", "#")
                        and name not in agent_stems
                        and not name.startswith("#")
                        and re.match(r"^[a-z][a-z0-9-]+$", name)
                    ):
                        dissonances.append(Dissonance(
                            "manifest", SEVERITY_MEDIUM,
                            str(mpath.relative_to(project_root)),
                            f"'{name}' référencé dans le manifest mais pas trouvé",
                            "Vérifier si l'agent existe ou retirer du manifest",
                        ))
        except OSError:
            pass
    return dissonances


_REF_PATTERN = re.compile(
    r'(?:include|load|source|import|ref)\s*[=:]\s*["\']?([a-zA-Z0-9_/.@-]+)'
)


def _detect_broken_refs(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    dissonances: list[Dissonance] = []
    for fpath in scan.agents + scan.workflows + scan.tools:
        full = project_root / fpath
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            for match in _REF_PATTERN.finditer(content):
                ref = match.group(1)
                if not ref.startswith("http") and ("/" in ref or ref.endswith((".md", ".yaml", ".py", ".xml"))):
                    if not (project_root / ref).exists() and not (full.parent / ref).exists():
                        dissonances.append(Dissonance(
                            "broken-ref", SEVERITY_HIGH, fpath,
                            f"Référence cassée : '{ref}' — fichier introuvable",
                            "Corriger le chemin ou retirer la référence",
                        ))
        except OSError:
            pass
    return dissonances


def _detect_duplication(scan: ArchScan, project_root: Path) -> list[Dissonance]:
    dissonances: list[Dissonance] = []
    summaries: dict[str, set[str]] = {}
    for agent_path in scan.agents:
        full = project_root / agent_path
        if full.exists():
            try:
                content = full.read_text(encoding="utf-8", errors="replace")[:500].lower()
                summaries[agent_path] = set(re.findall(r"\b\w{4,}\b", content))
            except OSError:
                pass
    items = list(summaries.items())
    for i, (path_a, words_a) in enumerate(items):
        for path_b, words_b in items[i + 1:]:
            if words_a and words_b:
                jaccard = len(words_a & words_b) / len(words_a | words_b)
                if jaccard > 0.6:
                    dissonances.append(Dissonance(
                        "duplication", SEVERITY_MEDIUM, path_a,
                        f"Possiblement similaire à '{path_b}' (Jaccard={jaccard:.0%})",
                        "Vérifier si les responsabilités se chevauchent",
                    ))
    return dissonances


# ── Score ─────────────────────────────────────────────────────────────────────

_SEVERITY_PENALTY = {SEVERITY_HIGH: 8, SEVERITY_MEDIUM: 4, SEVERITY_LOW: 2}


def _compute_score(dissonances: list[Dissonance]) -> tuple[int, str, dict[str, int]]:
    if not dissonances:
        return 100, "A+", {}
    penalty = sum(_SEVERITY_PENALTY.get(d.severity, 0) for d in dissonances)
    cat_counts: dict[str, int] = {}
    for d in dissonances:
        cat_counts[d.category] = cat_counts.get(d.category, 0) + 1
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
    return score, grade, cat_counts


# ── Tool Class ────────────────────────────────────────────────────────────────

class HarmonyCheck(BmadTool):
    """Architecture harmony checker — returns a :class:`HarmonyResult`."""

    def run(self, **kwargs: Any) -> HarmonyResult:
        scan = _scan_project(self.project_root)

        scan.dissonances.extend(_detect_orphans(scan))
        scan.dissonances.extend(_detect_naming(scan))
        scan.dissonances.extend(_detect_oversized(scan, self.project_root))
        scan.dissonances.extend(_detect_manifest_mismatch(scan, self.project_root))
        scan.dissonances.extend(_detect_broken_refs(scan, self.project_root))
        scan.dissonances.extend(_detect_duplication(scan, self.project_root))

        score, grade, cats = _compute_score(scan.dissonances)
        return HarmonyResult(
            score=score,
            grade=grade,
            total_files=scan.total_files,
            dissonances=tuple(scan.dissonances),
            category_counts=cats,
        )


# ── CLI wrapper ───────────────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="BMAD Architecture Harmony Check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="?", default="report",
                        choices=["scan", "check", "score", "report", "dissonance"])
    args = parser.parse_args()

    hc = HarmonyCheck(Path(args.project_root))
    result = hc.run()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Score: {result.score}/100 ({result.grade})")
        print(f"Files scanned: {result.total_files}")
        if result.dissonances:
            print(f"Dissonances: {len(result.dissonances)}")
            for d in result.dissonances:
                icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(d.severity, "•")
                print(f"  {icon} [{d.category}] {d.file}: {d.message}")
        else:
            print("✅ No dissonances — architecture is harmonious!")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
