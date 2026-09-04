#!/usr/bin/env python3
"""
doc-drift-detector.py — Doc-as-Code drift detection (Phase 1).
================================================================

Lit le manifeste `doc-manifest.yaml` et détecte les écarts entre
fichiers source et documentation miroir.

Usage
-----
    # Check drift entre HEAD et working tree (= ce que l'agent vient de modifier)
    python3 doc-drift-detector.py --project-root . --since HEAD

    # Check entre deux refs
    python3 doc-drift-detector.py --project-root . --since HEAD~1 --until HEAD

    # Check un set explicite de fichiers (pour usage hook)
    python3 doc-drift-detector.py --project-root . --files src/grimoire/mcp/server.py

    # Sortie JSON (pour consommation par hooks ou CI)
    python3 doc-drift-detector.py --project-root . --since HEAD --json

Sortie
------
Exit code :
  0 = pas de drift
  1 = drift détecté (severity=enforcing ou info)
  2 = drift BLOCKING détecté

JSON :
  {
    "version": 1,
    "changed_files": [...],
    "drifts": [
      {
        "source": "src/grimoire/mcp/server.py",
        "mirror": "docs/mcp-integration.md",
        "severity": "enforcing",
        "reason": "source modifié, mirror intact",
        "require_failures": [],
        "mirror_touched": false
      }
    ],
    "summary": {"blocking": 0, "enforcing": 1, "info": 0}
  }

Design
------
- Stdlib only + pyyaml (déjà dépendance Grimoire via mkdocs).
- Déterministe : aucune heuristique LLM. Le manifeste dicte.
- Fail-open : si le manifeste est absent ou invalide → exit 0 avec warning
  (pour ne pas bloquer le dev sur un problème d'outil).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # PyYAML — déjà présent via mkdocs
except ImportError:
    yaml = None  # type: ignore


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convertit un glob étendu (supportant `**`) en regex.

    fnmatch ne supporte pas `**` correctement (il matche `*` uniquement
    dans un segment). On convertit à la main pour supporter les chemins
    récursifs typiques comme `src/grimoire/mcp/**/*.py`.
    """
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern[i:i+3] == "**/":
            parts.append("(?:.*/)?")
            i += 3
        elif pattern[i:i+2] == "**":
            parts.append(".*")
            i += 2
        elif c == "*":
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == ".":
            parts.append(r"\.")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


@dataclass
class Mapping:
    source: str | None = None
    source_glob: str | None = None
    mirrors: list[str] = field(default_factory=list)
    severity: str = "enforcing"
    require: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)

    def matches(self, path: str) -> bool:
        if self.source and self.source == path:
            return True
        if self.source_glob:
            # Essai fnmatch d'abord (rapide, cas simple)
            if fnmatch.fnmatch(path, self.source_glob):
                return True
            # Fallback regex pour les patterns `**`
            if "**" in self.source_glob:
                return bool(_glob_to_regex(self.source_glob).match(path))
        return False


@dataclass
class Drift:
    source: str
    mirror: str
    severity: str
    reason: str
    require_failures: list[str] = field(default_factory=list)
    mirror_touched: bool = False


def _load_manifest(manifest_path: Path) -> list[Mapping]:
    if not manifest_path.is_file():
        return []
    if yaml is None:
        print("warning: pyyaml not available, drift detection skipped",
              file=sys.stderr)
        return []
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"warning: manifest parse error: {exc}", file=sys.stderr)
        return []
    if not raw or "mappings" not in raw:
        return []
    return [Mapping(**m) for m in raw["mappings"]]


def _git_changed_files(
    project_root: Path,
    since: str | None,
    until: str | None,
) -> list[str]:
    """Files changed between `since` and `until` (default: working tree vs HEAD)."""
    if since is None:
        return []
    try:
        if until:
            out = subprocess.check_output(
                ["git", "diff", "--name-only", f"{since}..{until}"],
                cwd=project_root, text=True, stderr=subprocess.DEVNULL,
            )
        else:
            # `git diff --name-only HEAD` = working tree + staged vs HEAD
            out = subprocess.check_output(
                ["git", "diff", "--name-only", since],
                cwd=project_root, text=True, stderr=subprocess.DEVNULL,
            )
            # ajouter les fichiers non-trackés (si l'agent vient d'en créer)
            try:
                untracked = subprocess.check_output(
                    ["git", "ls-files", "--others", "--exclude-standard"],
                    cwd=project_root, text=True, stderr=subprocess.DEVNULL,
                )
                out = out + "\n" + untracked
            except subprocess.CalledProcessError:
                pass
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _git_diff_content(project_root: Path, path: str, since: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "diff", since, "--", path],
            cwd=project_root, text=True, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return ""


def _trigger_hit(diff_text: str, triggers: list[str]) -> bool:
    """Un trigger est un substring recherché dans les lignes AJOUTÉES/SUPPRIMÉES."""
    if not triggers:
        return True  # pas de trigger = toujours actif
    relevant = "\n".join(
        line for line in diff_text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    return any(t in relevant for t in triggers)


def detect_drifts(
    project_root: Path,
    since: str | None,
    until: str | None,
    explicit_files: list[str] | None,
) -> tuple[list[str], list[Drift]]:
    manifest = _load_manifest(project_root / "framework" / "tools" / "doc-manifest.yaml")
    if not manifest:
        return [], []

    changed = explicit_files or _git_changed_files(project_root, since, until)

    changed_set = set(changed)
    drifts: list[Drift] = []

    for path in changed:
        for mapping in manifest:
            if not mapping.matches(path):
                continue

            # Vérifier les triggers (substring dans le diff)
            if mapping.triggers and since:
                diff_text = _git_diff_content(project_root, path, since)
                if not _trigger_hit(diff_text, mapping.triggers):
                    continue

            for mirror in mapping.mirrors:
                if mirror in changed_set:
                    # mirror mis à jour en même temps → pas de drift
                    continue
                mirror_path = project_root / mirror
                if not mirror_path.is_file():
                    drifts.append(Drift(
                        source=path, mirror=mirror, severity=mapping.severity,
                        reason=f"mirror introuvable ({mirror})",
                        mirror_touched=False,
                    ))
                    continue
                drifts.append(Drift(
                    source=path, mirror=mirror, severity=mapping.severity,
                    reason="source modifié, mirror intact",
                    require_failures=list(mapping.require),  # rules not checked in P1
                    mirror_touched=False,
                ))

    return changed, drifts


def _render_text(changed: list[str], drifts: list[Drift]) -> str:
    lines = [
        "",
        "┌─────────────────────────────────────────────────────────┐",
        "│   Grimoire Doc-Drift Detector — Phase 1 (detection)    │",
        "└─────────────────────────────────────────────────────────┘",
        "",
        f"  Files changed : {len(changed)}",
        f"  Drifts        : {len(drifts)}",
    ]
    by_sev = {"blocking": 0, "enforcing": 0, "info": 0}
    for d in drifts:
        by_sev[d.severity] = by_sev.get(d.severity, 0) + 1
    lines.append(
        f"  By severity   : blocking={by_sev['blocking']} "
        f"enforcing={by_sev['enforcing']} info={by_sev['info']}"
    )
    lines.append("")
    if not drifts:
        lines.append("  ✓ Documentation en phase avec le code.")
        lines.append("")
        return "\n".join(lines)

    # Grouper par source
    by_source: dict[str, list[Drift]] = {}
    for d in drifts:
        by_source.setdefault(d.source, []).append(d)

    for source, ds in by_source.items():
        tag = {"blocking": "[BLOCKING]", "enforcing": "[ENFORCE ]",
               "info": "[  INFO  ]"}[ds[0].severity]
        lines.append(f"  {tag} {source}")
        for d in ds:
            lines.append(f"      → mirror: {d.mirror}  ({d.reason})")
            if d.require_failures:
                lines.append(f"        require: {', '.join(d.require_failures)}")
        lines.append("")
    return "\n".join(lines)


def _to_dict(changed: list[str], drifts: list[Drift]) -> dict:
    by_sev = {"blocking": 0, "enforcing": 0, "info": 0}
    for d in drifts:
        by_sev[d.severity] = by_sev.get(d.severity, 0) + 1
    return {
        "version": 1,
        "changed_files": changed,
        "drifts": [
            {
                "source": d.source, "mirror": d.mirror,
                "severity": d.severity, "reason": d.reason,
                "require_failures": d.require_failures,
                "mirror_touched": d.mirror_touched,
            }
            for d in drifts
        ],
        "summary": by_sev,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Doc-as-Code drift detector (Phase 1 — deterministic)",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--since", default="HEAD",
                        help="Git ref (default: HEAD = working tree vs HEAD)")
    parser.add_argument("--until", default=None)
    parser.add_argument("--files", nargs="*", default=None,
                        help="Explicit files to check (skips git diff)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    changed, drifts = detect_drifts(
        project_root, args.since, args.until, args.files,
    )

    if args.json:
        print(json.dumps(_to_dict(changed, drifts), indent=2, ensure_ascii=False))
    else:
        print(_render_text(changed, drifts))

    # Exit code
    if any(d.severity == "blocking" for d in drifts):
        sys.exit(2)
    if drifts:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
