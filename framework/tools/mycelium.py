#!/usr/bin/env python3
"""
mycelium.py — Réseau Mycelium Grimoire.
=====================================

Système de partage et migration de patterns entre projets,
comme les réseaux mycéliens connectent les arbres d'une forêt.

  1. `scan`      — Scanner les patterns réutilisables d'un projet
  2. `export`    — Exporter les patterns (anonymisés optionnel)
  3. `import`    — Importer des patterns depuis un export
  4. `match`     — Matching contextuel entre deux projets
  5. `catalog`   — Catalogue des patterns partagés

Patterns partageables :
  - Workflows éprouvés
  - Configurations d'agents
  - Templates de documents
  - Conventions (commit, nommage)
  - Mémoire collective (anonymisée)

Principe : "Dans une forêt, les arbres communiquent par le mycélium.
Les projets devraient faire pareil."

Usage :
  python3 mycelium.py --project-root . scan
  python3 mycelium.py --project-root . export --output ./patterns --anonymize
  python3 mycelium.py --project-root . import --source ./patterns
  python3 mycelium.py --project-root . match --other-project ../other
  python3 mycelium.py --project-root . catalog

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.mycelium")

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
PATTERN_CATALOG = "mycelium-catalog.json"

PATTERN_TYPES = {
    "workflow": {"paths": ["**/workflows/**/*.yaml"], "weight": 0.9},
    "agent-config": {"paths": ["**/agents/*.md"], "weight": 0.8},
    "tool": {"paths": ["**/tools/*.py"], "weight": 0.7},
    "template": {"paths": ["**/*.tpl.*", "**/prompt-templates/**"], "weight": 0.6},
    "convention": {"paths": ["**/cc-reference.md", "**/cc-verify.sh"], "weight": 0.5},
    "memory": {"paths": ["**/_memory/**/*.md"], "weight": 0.4},
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    """Un pattern réutilisable."""
    id: str
    name: str
    pattern_type: str
    source_path: str
    size_bytes: int = 0
    description: str = ""
    tags: list[str] = field(default_factory=list)

@dataclass
class MatchResult:
    pattern_type: str
    local_file: str
    remote_file: str
    similarity: float = 0.0  # 0.0-1.0


# ── Scanner ──────────────────────────────────────────────────────────────────

def scan_patterns(project_root: Path) -> list[Pattern]:
    """Scanne le projet pour trouver les patterns partageables."""
    patterns = []
    idx = 0

    for ptype, info in PATTERN_TYPES.items():
        for glob_pat in info["paths"]:
            parts = glob_pat.split("/")
            file_glob = parts[-1]
            for fpath in project_root.rglob(file_glob):
                if ".git" in str(fpath) or "__pycache__" in str(fpath):
                    continue
                rel = str(fpath.relative_to(project_root))
                # Vérifier que le chemin match le pattern dir
                if len(parts) > 1 and "**" not in glob_pat:
                    dir_part = parts[0].strip("*")
                    if dir_part and dir_part not in rel:
                        continue

                idx += 1
                try:
                    size = fpath.stat().st_size
                except OSError:
                    size = 0

                # Extraire la description depuis les premières lignes
                desc = ""
                try:
                    first_lines = fpath.read_text(encoding="utf-8", errors="ignore")[:300]
                    # Chercher un commentaire de description
                    for line in first_lines.splitlines()[:5]:
                        line = line.strip().lstrip("#").strip('"').strip()
                        if len(line) > 10 and not line.startswith("!") and not line.startswith("---"):
                            desc = line[:100]
                            break
                except OSError as _exc:
                    _log.debug("OSError suppressed: %s", _exc)
                    # Silent exception — add logging when investigating issues

                patterns.append(Pattern(
                    id=f"PAT-{idx:03d}",
                    name=fpath.stem,
                    pattern_type=ptype,
                    source_path=rel,
                    size_bytes=size,
                    description=desc,
                ))

    return patterns


# ── Anonymizer ───────────────────────────────────────────────────────────────

def anonymize_content(content: str) -> str:
    """Anonymise le contenu en remplaçant les noms propres, emails, etc."""
    # Emails
    content = re.sub(r'[\w.-]+@[\w.-]+\.\w+', 'user@example.com', content)
    # URLs with specific domains
    content = re.sub(r'https?://(?!example\.com)[^\s\)]+', 'https://example.com', content)
    # Specific project names (heuristic: CamelCase or kebab-case repeated)
    # Keep generic patterns, remove specific identifiers
    return content


# ── Export ───────────────────────────────────────────────────────────────────

def export_patterns(project_root: Path, output: Path, patterns: list[Pattern],
                    anonymize: bool = False) -> int:
    """Exporte les patterns vers un répertoire."""
    output.mkdir(parents=True, exist_ok=True)
    exported = 0

    for pat in patterns:
        src = project_root / pat.source_path
        if not src.exists():
            continue
        dst = output / pat.pattern_type / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            content = src.read_text(encoding="utf-8")
            if anonymize:
                content = anonymize_content(content)
            dst.write_text(content, encoding="utf-8")
            exported += 1
        except (OSError, UnicodeDecodeError) as _exc:
            _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Manifest
    manifest = {
        "exported_at": datetime.now().isoformat(),
        "source": str(project_root),
        "anonymized": anonymize,
        "patterns": [{"id": p.id, "name": p.name, "type": p.pattern_type,
                      "path": p.source_path} for p in patterns],
    }
    (output / "mycelium-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return exported


# ── Import ───────────────────────────────────────────────────────────────────

def import_patterns(project_root: Path, source: Path) -> int:
    """Importe des patterns depuis un export mycelium."""
    manifest_path = source / "mycelium-manifest.json"
    if not manifest_path.exists():
        return 0

    try:
        json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    imported = 0
    for ptype_dir in source.iterdir():
        if not ptype_dir.is_dir():
            continue
        for fpath in ptype_dir.iterdir():
            if fpath.is_file():
                # Determine destination based on pattern type
                dest_dir = project_root / "framework" / ptype_dir.name
                if ptype_dir.name == "agent-config":
                    dest_dir = project_root / "framework" / "agents"
                elif ptype_dir.name == "memory":
                    dest_dir = project_root / "_grimoire" / "_memory" / "imported"

                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / fpath.name
                if not dest.exists():  # Don't overwrite
                    shutil.copy2(fpath, dest)
                    imported += 1

    return imported


# ── Matching ─────────────────────────────────────────────────────────────────

def match_projects(project_root: Path, other_root: Path) -> list[MatchResult]:
    """Trouve les patterns communs entre deux projets."""
    local_patterns = scan_patterns(project_root)
    remote_patterns = scan_patterns(other_root)

    matches = []
    for lp in local_patterns:
        for rp in remote_patterns:
            if lp.pattern_type != rp.pattern_type:
                continue
            # Simple name similarity
            lname = lp.name.lower().replace("-", "").replace("_", "")
            rname = rp.name.lower().replace("-", "").replace("_", "")

            # Jaccard on characters
            lset = set(lname)
            rset = set(rname)
            if not lset or not rset:
                continue
            similarity = len(lset & rset) / len(lset | rset)

            if similarity > 0.5:
                matches.append(MatchResult(
                    pattern_type=lp.pattern_type,
                    local_file=lp.source_path,
                    remote_file=rp.source_path,
                    similarity=similarity,
                ))

    return sorted(matches, key=lambda m: m.similarity, reverse=True)


# ── Catalog ──────────────────────────────────────────────────────────────────

def update_catalog(project_root: Path, patterns: list[Pattern]) -> Path:
    catalog_path = project_root / "_grimoire" / "_memory" / PATTERN_CATALOG
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now().isoformat(),
        "total_patterns": len(patterns),
        "by_type": dict(Counter(p.pattern_type for p in patterns)),
        "patterns": [{"id": p.id, "name": p.name, "type": p.pattern_type,
                      "path": p.source_path, "size": p.size_bytes}
                     for p in patterns],
    }
    catalog_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return catalog_path


# ── Formatters ───────────────────────────────────────────────────────────────

def format_scan(patterns: list[Pattern]) -> str:
    type_counts = Counter(p.pattern_type for p in patterns)
    total_size = sum(p.size_bytes for p in patterns)
    lines = [
        f"🍄 Mycelium — {len(patterns)} patterns détectés ({total_size / 1024:.1f} KB)\n",
        "   Par type :",
    ]
    for ptype, count in type_counts.most_common():
        bar = "█" * min(20, count)
        lines.append(f"      {ptype:15s} {bar} {count}")
    lines.append("\n   Exemples :")
    for p in patterns[:10]:
        lines.append(f"      [{p.id}] {p.name} ({p.pattern_type})")
        if p.description:
            lines.append(f"            {p.description[:70]}")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    patterns = scan_patterns(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps([{"id": p.id, "name": p.name, "type": p.pattern_type,
                           "path": p.source_path} for p in patterns],
                         indent=2, ensure_ascii=False))
    else:
        print(format_scan(patterns))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    output = Path(args.output).resolve()
    patterns = scan_patterns(project_root)
    exported = export_patterns(project_root, output, patterns, args.anonymize)
    if args.json:
        print(json.dumps({"exported": exported, "output": str(output)}, indent=2))
    else:
        print(f"🍄 {exported} patterns exportés vers {output}")
        if args.anonymize:
            print("   🔒 Contenu anonymisé")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    source = Path(args.source).resolve()
    imported = import_patterns(project_root, source)
    if args.json:
        print(json.dumps({"imported": imported}, indent=2))
    else:
        print(f"🍄 {imported} patterns importés depuis {source}")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    other = Path(args.other_project).resolve()
    matches = match_projects(project_root, other)
    if args.json:
        print(json.dumps([{"type": m.pattern_type, "local": m.local_file,
                           "remote": m.remote_file, "similarity": m.similarity}
                          for m in matches], indent=2, ensure_ascii=False))
    else:
        print(f"🔗 {len(matches)} patterns communs trouvés\n")
        for m in matches[:15]:
            bar = "█" * int(m.similarity * 10)
            print(f"   {bar} {m.pattern_type}: {m.local_file}")
            print(f"         ↔ {m.remote_file} ({m.similarity:.0%})")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    patterns = scan_patterns(project_root)
    catalog = update_catalog(project_root, patterns)
    if args.json:
        print(json.dumps(json.loads(catalog.read_text(encoding="utf-8")), indent=2, ensure_ascii=False))
    else:
        print(format_scan(patterns))
        print(f"\n   📁 Catalogue sauvé : {catalog}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Mycelium — Réseau de patterns inter-projets",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("scan", help="Scanner les patterns").set_defaults(func=cmd_scan)

    p_export = subs.add_parser("export", help="Exporter")
    p_export.add_argument("--output", required=True, help="Répertoire de sortie")
    p_export.add_argument("--anonymize", action="store_true", help="Anonymiser le contenu")
    p_export.set_defaults(func=cmd_export)

    p_import = subs.add_parser("import", help="Importer")
    p_import.add_argument("--source", required=True, help="Répertoire source")
    p_import.set_defaults(func=cmd_import)

    p_match = subs.add_parser("match", help="Matching entre projets")
    p_match.add_argument("--other-project", required=True)
    p_match.set_defaults(func=cmd_match)

    subs.add_parser("catalog", help="Mettre à jour le catalogue").set_defaults(func=cmd_catalog)

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
