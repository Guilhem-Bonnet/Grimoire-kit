#!/usr/bin/env python3
"""
agent-integrity.py — Vérification d'intégrité des fichiers agents (S4).
========================================================================

Calcule et vérifie les checksums SHA256 des fichiers agents au boot.
Alerte si un fichier a été modifié de manière inattendue (supply chain).

Modes :
  snapshot  — Crée un snapshot des checksums actuels
  verify    — Vérifie contre le dernier snapshot
  diff      — Affiche les fichiers modifiés

Usage :
  python3 agent-integrity.py --project-root . snapshot
  python3 agent-integrity.py --project-root . verify
  python3 agent-integrity.py --project-root . diff

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "1.0.0"

INTEGRITY_DIR = "_grimoire-output/.agent-integrity"
SNAPSHOT_FILE = "checksums.json"

# Patterns de fichiers agents à surveiller
AGENT_PATTERNS = [
    "framework/agent-base.md",
    "framework/agent-base-compact.md",
    "framework/agent-rules.md",
    "framework/agent2agent.md",
    "archetypes/*/dna.yaml",
    "archetypes/*/team-manifest.yaml",
]


@dataclass
class IntegrityReport:
    status: str = "unknown"  # clean, modified, no-snapshot
    total_files: int = 0
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "total_files": self.total_files,
            "modified": self.modified,
            "added": self.added,
            "removed": self.removed,
            "timestamp": self.timestamp,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _collect_agent_files(project_root: Path) -> dict[str, str]:
    """Collecte tous les fichiers agents et leurs SHA256."""
    checksums: dict[str, str] = {}

    for pattern in AGENT_PATTERNS:
        for f in project_root.glob(pattern):
            if f.is_file():
                rel = str(f.relative_to(project_root))
                checksums[rel] = _sha256(f)

    return checksums


def snapshot(project_root: Path) -> dict:
    """Crée un snapshot des checksums actuels."""
    checksums = _collect_agent_files(project_root)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    data = {
        "version": VERSION,
        "timestamp": ts,
        "file_count": len(checksums),
        "checksums": checksums,
    }

    out_dir = project_root / INTEGRITY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SNAPSHOT_FILE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {"status": "snapshot_created", "files": len(checksums), "timestamp": ts}


def verify(project_root: Path) -> IntegrityReport:
    """Vérifie les fichiers actuels contre le snapshot."""
    snap_file = project_root / INTEGRITY_DIR / SNAPSHOT_FILE
    report = IntegrityReport(timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    if not snap_file.exists():
        report.status = "no-snapshot"
        return report

    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    saved_checksums = snap_data.get("checksums", {})
    current_checksums = _collect_agent_files(project_root)

    report.total_files = len(current_checksums)

    # Fichiers modifiés
    for path, current_hash in current_checksums.items():
        if path in saved_checksums:
            if saved_checksums[path] != current_hash:
                report.modified.append(path)
        else:
            report.added.append(path)

    # Fichiers supprimés
    for path in saved_checksums:
        if path not in current_checksums:
            report.removed.append(path)

    if report.modified or report.added or report.removed:
        report.status = "modified"
    else:
        report.status = "clean"

    return report


def mcp_agent_integrity(project_root: str, action: str = "verify") -> dict:
    """MCP tool bmad_agent_integrity — vérification intégrité agents."""
    root = Path(project_root).resolve()
    if action == "snapshot":
        return snapshot(root)
    report = verify(root)
    return report.to_dict()


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-integrity",
        description="Vérification d'intégrité des fichiers agents",
    )
    parser.add_argument("--project-root", type=Path, default=Path())
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("snapshot", help="Créer un snapshot des checksums")
    subs.add_parser("verify", help="Vérifier contre le snapshot")
    subs.add_parser("diff", help="Afficher les modifications")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    if args.command == "snapshot":
        result = snapshot(root)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  ✅ Snapshot créé — {result['files']} fichiers")

    elif args.command in ("verify", "diff"):
        report = verify(root)
        if args.as_json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            if report.status == "no-snapshot":
                print("  ℹ️ Aucun snapshot. Lancez d'abord: agent-integrity snapshot")
            elif report.status == "clean":
                print(f"  ✅ Intégrité OK — {report.total_files} fichiers vérifiés")
            else:
                print("  ⚠️ Modifications détectées :")
                for f in report.modified:
                    print(f"    🔄 Modifié : {f}")
                for f in report.added:
                    print(f"    ➕ Ajouté  : {f}")
                for f in report.removed:
                    print(f"    ➖ Supprimé: {f}")


if __name__ == "__main__":
    main()
