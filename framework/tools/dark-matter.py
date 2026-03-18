#!/usr/bin/env python3
"""
dark-matter.py — Détecteur de matière noire Grimoire.
====================================================

Détecte le savoir tribal, les conventions non-écrites, et les hypothèses
implicites qui ne sont documentées nulle part mais que tout le monde
"sait" — et qui causent des problèmes quand quelqu'un ne les connaît pas.

Analyse :
  1. Patterns d'usage non documentés dans le code
  2. Hypothèses implicites dans les artefacts
  3. Conventions de nommage non formalisées
  4. Dépendances tacites entre composants
  5. Connaissances détenues par un seul agent/personne (bus factor)

Features :
  1. `scan`     — Scan complet de matière noire
  2. `patterns` — Conventions non-écrites détectées
  3. `silos`    — Silos de connaissance (bus factor)
  4. `implicit` — Hypothèses implicites
  5. `document` — Générer la documentation manquante

Usage :
  python3 dark-matter.py --project-root . scan
  python3 dark-matter.py --project-root . patterns
  python3 dark-matter.py --project-root . silos
  python3 dark-matter.py --project-root . implicit
  python3 dark-matter.py --project-root . document
  python3 dark-matter.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.dark_matter")

# ── Constantes ────────────────────────────────────────────────────────────────

DARK_MATTER_VERSION = "1.0.0"

# Types de matière noire
class DarkType:
    CONVENTION = "📐 Convention non-écrite"
    ASSUMPTION = "💭 Hypothèse implicite"
    SILO = "🏝️ Silo de connaissance"
    DEPENDENCY = "🔗 Dépendance tacite"
    MAGIC = "✨ Valeur magique"
    RITUAL = "🕯️ Rituel non documenté"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DarkMatterItem:
    """Un élément de matière noire détecté."""
    dark_type: str
    title: str
    description: str
    location: str = ""
    confidence: float = 0.5   # 0.0-1.0
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.dark_type,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "confidence": round(self.confidence, 2),
            "recommendation": self.recommendation,
        }


@dataclass
class DarkMatterReport:
    """Rapport complet de matière noire."""
    items: list[DarkMatterItem] = field(default_factory=list)
    bus_factor: int = 0
    documentation_coverage: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def by_type(self) -> dict[str, list[DarkMatterItem]]:
        grouped: dict[str, list[DarkMatterItem]] = defaultdict(list)
        for item in self.items:
            grouped[item.dark_type].append(item)
        return dict(grouped)


# ── Git Helpers ──────────────────────────────────────────────────────────────

def _git_file_authors(project_root: Path) -> dict[str, set[str]]:
    """Retourne les auteurs par fichier."""
    try:
        r = subprocess.run(
            ["git", "log", "--format=%an", "--name-only", "--since=180 days ago"],
            capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if r.returncode != 0:
            return {}

        result: dict[str, set[str]] = defaultdict(set)
        current_author = ""
        for line in r.stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Les lignes sans "/" ni "." sont des noms d'auteurs
            if "/" not in line and "." not in line and len(line) < 50:
                current_author = line
            elif current_author:
                result[line].add(current_author)
        return dict(result)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


# ── Detectors ────────────────────────────────────────────────────────────────

def detect_magic_values(project_root: Path) -> list[DarkMatterItem]:
    """Détecte les valeurs magiques non documentées."""
    items = []
    magic_pattern = re.compile(
        r'(?:=\s*|:\s*|,\s*)(\d{2,}(?:\.\d+)?)\s*(?:#|//|$)',
    )

    exclude = {"__pycache__", "node_modules", ".git", ".venv"}

    for fpath in project_root.rglob("*.py"):
        if any(ex in fpath.parts for ex in exclude):
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), 1):
                # Skip import, comment, docstring
                stripped = line.strip()
                if stripped.startswith(("#", "import", "from", '"""', "'''")):
                    continue
                matches = magic_pattern.findall(line)
                for m in matches:
                    val = float(m)
                    # Ignorer les valeurs triviales
                    if val in (0, 1, 2, 10, 100, 1000, 0.0, 1.0, 0.5):
                        continue
                    # Vérifier si c'est dans une constante nommée
                    if re.match(r'^[A-Z_]+\s*=', stripped):
                        continue  # C'est nommé → OK
                    items.append(DarkMatterItem(
                        dark_type=DarkType.MAGIC,
                        title=f"Valeur magique : {m}",
                        description="Nombre non documenté utilisé directement",
                        location=f"{fpath.relative_to(project_root)}:{i}",
                        confidence=0.6,
                        recommendation="Extraire dans une constante nommée avec commentaire",
                    ))
        except (OSError, UnicodeDecodeError) as _exc:
            _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return items[:20]  # Cap


def detect_naming_conventions(project_root: Path) -> list[DarkMatterItem]:
    """Détecte les conventions de nommage implicites."""
    items = []

    # Analyser les patterns de nommage dans _grimoire
    grimoire_dir = project_root / "_grimoire"
    if not grimoire_dir.exists():
        return items

    # Collecter les patterns de nommage
    patterns: dict[str, list[str]] = defaultdict(list)
    for fpath in grimoire_dir.rglob("*.md"):
        name = fpath.stem
        # Détecter le pattern (UPPER-, kebab-, snake_, etc.)
        if re.match(r'^[A-Z]+-', name):
            patterns["UPPER-PREFIX"].append(name)
        elif re.match(r'^[a-z]+-[a-z]+', name):
            patterns["kebab-case"].append(name)
        elif re.match(r'^[a-z]+_[a-z]+', name):
            patterns["snake_case"].append(name)
        else:
            patterns["other"].append(name)

    # Détecter les incohérences
    if len(patterns) > 2:
        items.append(DarkMatterItem(
            dark_type=DarkType.CONVENTION,
            title="Conventions de nommage mixtes",
            description=f"Détecté {len(patterns)} patterns différents : {', '.join(patterns.keys())}",
            location="_grimoire/",
            confidence=0.8,
            recommendation="Documenter la convention de nommage dans le README ou CONTRIBUTING",
        ))

    # Vérifier les prefixes de fichiers
    prefixes = Counter()
    for fpath in (project_root / "_grimoire-output").rglob("*.md") if (project_root / "_grimoire-output").exists() else []:
        prefix = fpath.stem.split("-")[0].upper()
        if len(prefix) >= 2:
            prefixes[prefix] += 1

    undocumented_prefixes = []
    docs_content = ""
    readme = project_root / "README.md"
    if readme.exists():
        try:
            docs_content = readme.read_text(encoding="utf-8").lower()
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    for prefix, count in prefixes.most_common(10):
        if prefix.lower() not in docs_content and count >= 2:
            undocumented_prefixes.append(prefix)

    if undocumented_prefixes:
        items.append(DarkMatterItem(
            dark_type=DarkType.CONVENTION,
            title=f"Prefixes de fichier non documentés : {', '.join(undocumented_prefixes[:5])}",
            description="Ces prefixes sont utilisés mais non expliqués dans la doc",
            location="_grimoire-output/",
            confidence=0.7,
            recommendation="Ajouter une section 'Conventions de nommage' dans le README",
        ))

    return items


def detect_silos(project_root: Path) -> list[DarkMatterItem]:
    """Détecte les silos de connaissance (bus factor)."""
    items = []
    author_map = _git_file_authors(project_root)

    if not author_map:
        return items

    # Chercher les fichiers modifiés par un seul auteur
    single_author_files = []
    for fpath, authors in author_map.items():
        if len(authors) == 1 and not any(ex in fpath for ex in ["test", "lock", "generated"]):
            single_author_files.append((fpath, next(iter(authors))))

    if single_author_files:
        # Grouper par auteur
        by_author: dict[str, list[str]] = defaultdict(list)
        for fpath, author in single_author_files:
            by_author[author].append(fpath)

        for author, files in by_author.items():
            if len(files) >= 3:
                items.append(DarkMatterItem(
                    dark_type=DarkType.SILO,
                    title=f"Silo de connaissance : {author}",
                    description=f"Seul contributeur sur {len(files)} fichiers "
                                f"({', '.join(Path(f).name for f in files[:3])}...)",
                    confidence=0.75,
                    recommendation="Organiser un knowledge transfer pour ces fichiers",
                ))

    # Bus factor global
    all_authors = set()
    for authors in author_map.values():
        all_authors.update(authors)

    if len(all_authors) <= 1:
        items.append(DarkMatterItem(
            dark_type=DarkType.SILO,
            title="Bus factor = 1",
            description="Un seul contributeur sur tout le projet",
            confidence=0.95,
            recommendation="Impliquer un second contributeur dans la maintenance",
        ))

    return items


def detect_implicit_assumptions(project_root: Path) -> list[DarkMatterItem]:
    """Détecte les hypothèses implicites."""
    items = []

    # Chercher les patterns suspects
    assumption_patterns = [
        (r'# (?:TODO|FIXME|HACK|XXX|WORKAROUND)', "Hack/workaround non documenté"),
        (r'(?:assumes?|assumer?|suppose)\s+(?:that|que)', "Hypothèse explicite non validée"),
        (r'this\s+(?:should|devrait)\s+(?:work|marcher|fonctionner)', "Espérance non vérifiée"),
        (r'(?:hardcoded|en\s+dur)', "Valeur hardcodée signalée"),
    ]

    exclude = {"__pycache__", "node_modules", ".git", ".venv"}

    for fpath in project_root.rglob("*"):
        if not fpath.is_file() or fpath.suffix not in (".py", ".md", ".yaml", ".yml", ".ts", ".js"):
            continue
        if any(ex in fpath.parts for ex in exclude):
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            for pattern, desc in assumption_patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[:match.start()].count("\n") + 1
                    ctx = content[max(0, match.start()-20):match.end()+40].strip()
                    items.append(DarkMatterItem(
                        dark_type=DarkType.ASSUMPTION,
                        title=desc,
                        description=ctx[:120],
                        location=f"{fpath.relative_to(project_root)}:{line_num}",
                        confidence=0.5,
                        recommendation="Valider l'hypothèse et la documenter ou la résoudre",
                    ))
        except (OSError, UnicodeDecodeError) as _exc:
            _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return items[:30]


def detect_undocumented_dependencies(project_root: Path) -> list[DarkMatterItem]:
    """Détecte les dépendances tacites entre composants."""
    items = []

    # Chercher les imports/references cross-modules dans les outils
    tools_dir = project_root / "framework" / "tools"
    if tools_dir.exists():
        tool_deps: dict[str, list[str]] = defaultdict(list)
        for py in tools_dir.glob("*.py"):
            try:
                content = py.read_text(encoding="utf-8")
                # Chercher les références à d'autres outils
                for other_py in tools_dir.glob("*.py"):
                    if other_py == py:
                        continue
                    other_name = other_py.stem.replace("-", "_")
                    if other_name in content or other_py.stem in content:
                        tool_deps[py.stem].append(other_py.stem)
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Dépendances non documentées
        for tool, deps in tool_deps.items():
            # Vérifier si c'est documenté dans l'outil
            try:
                docstring = (tools_dir / f"{tool}.py").read_text(encoding="utf-8")[:500]
                undoc = [d for d in deps if d not in docstring]
                if undoc:
                    items.append(DarkMatterItem(
                        dark_type=DarkType.DEPENDENCY,
                        title=f"Dépendances tacites de {tool}",
                        description=f"Dépend implicitement de : {', '.join(undoc)}",
                        location=f"framework/tools/{tool}.py",
                        confidence=0.7,
                        recommendation="Documenter les dépendances dans le docstring de l'outil",
                    ))
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    return items


# ── Report Builder ──────────────────────────────────────────────────────────

def build_full_report(project_root: Path) -> DarkMatterReport:
    """Construit le rapport complet."""
    report = DarkMatterReport()

    report.items.extend(detect_magic_values(project_root))
    report.items.extend(detect_naming_conventions(project_root))
    report.items.extend(detect_silos(project_root))
    report.items.extend(detect_implicit_assumptions(project_root))
    report.items.extend(detect_undocumented_dependencies(project_root))

    # Bus factor
    author_map = _git_file_authors(project_root)
    all_authors = set()
    for authors in author_map.values():
        all_authors.update(authors)
    report.bus_factor = max(1, len(all_authors))

    # Trier par confiance décroissante
    report.items.sort(key=lambda i: i.confidence, reverse=True)

    return report


# ── Formatters ───────────────────────────────────────────────────────────────

def format_report(report: DarkMatterReport) -> str:
    lines = [
        "🌑 Dark Matter Detector — Matière noire du projet",
        f"   Éléments détectés : {len(report.items)}",
        f"   Bus factor : {report.bus_factor}",
        "",
    ]

    by_type = report.by_type
    for dark_type, items in by_type.items():
        lines.append(f"   {dark_type} ({len(items)})")
        for item in items[:5]:
            lines.append(f"      [{item.confidence:.0%}] {item.title}")
            if item.location:
                lines.append(f"           📍 {item.location}")
            lines.append(f"           {item.description[:80]}")
            if item.recommendation:
                lines.append(f"           💡 {item.recommendation}")
        if len(items) > 5:
            lines.append(f"      ... et {len(items) - 5} de plus")
        lines.append("")

    return "\n".join(lines)


def generate_documentation(report: DarkMatterReport) -> str:
    """Génère un document Markdown de la matière noire."""
    lines = [
        "# 🌑 Matière Noire — Documentation du savoir tribal",
        f"\n> Généré le {report.timestamp[:10]}",
        f"> {len(report.items)} éléments détectés | Bus factor : {report.bus_factor}\n",
    ]

    by_type = report.by_type
    for dark_type, items in by_type.items():
        lines.append(f"\n## {dark_type}\n")
        for item in items:
            lines.append(f"### {item.title}")
            lines.append(f"\n{item.description}")
            if item.location:
                lines.append(f"\n**Localisation** : `{item.location}`")
            if item.recommendation:
                lines.append(f"\n**Action** : {item.recommendation}")
            lines.append(f"\n**Confiance** : {item.confidence:.0%}\n")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_full_report(project_root)
    if args.json:
        print(json.dumps({
            "items": [i.to_dict() for i in report.items],
            "bus_factor": report.bus_factor,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_patterns(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    items = detect_naming_conventions(project_root)
    items.extend(detect_magic_values(project_root))
    for item in items:
        print(f"   {item.dark_type}: {item.title}")
        if item.location:
            print(f"      📍 {item.location}")
    if not items:
        print("✅ Aucune convention non-écrite détectée")
    return 0


def cmd_silos(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    items = detect_silos(project_root)
    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
    else:
        for item in items:
            print(f"   {item.title}")
            print(f"      {item.description}")
        if not items:
            print("✅ Aucun silo de connaissance détecté (ou git non disponible)")
    return 0


def cmd_implicit(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    items = detect_implicit_assumptions(project_root)
    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
    else:
        for item in items:
            print(f"   💭 {item.title}")
            print(f"      {item.description[:100]}")
            print(f"      📍 {item.location}")
            print()
        if not items:
            print("✅ Aucune hypothèse implicite détectée")
    return 0


def cmd_document(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_full_report(project_root)
    md = generate_documentation(report)
    output = project_root / "_grimoire-output" / "dark-matter-report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    print(f"📄 Rapport généré → {output}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Dark Matter Detector — Savoir tribal et conventions implicites",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("scan", help="Scan complet")
    p.set_defaults(func=cmd_scan)

    p = subs.add_parser("patterns", help="Conventions non-écrites")
    p.set_defaults(func=cmd_patterns)

    p = subs.add_parser("silos", help="Silos de connaissance")
    p.set_defaults(func=cmd_silos)

    p = subs.add_parser("implicit", help="Hypothèses implicites")
    p.set_defaults(func=cmd_implicit)

    p = subs.add_parser("document", help="Générer documentation manquante")
    p.set_defaults(func=cmd_document)

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
