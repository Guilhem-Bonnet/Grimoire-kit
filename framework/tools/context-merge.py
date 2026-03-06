#!/usr/bin/env python3
"""
context-merge.py — Context Merge et Diff BMAD (BM-44 Story 5.2).
============================================================

Compare et fusionne le contexte de deux branches de conversation.
Détecte les décisions conflictuelles, les artifacts créés et les
learnings ajoutés dans chaque branche.

Modes :
  diff    — Compare deux branches (décisions, artifacts, learnings)
  merge   — Fusionne une branche source vers une cible
  log     — Historique des merges
  preview — Aperçu d'un merge sans exécution

Usage :
  python3 context-merge.py --project-root . diff --branch-a main --branch-b explore-graphql
  python3 context-merge.py --project-root . merge --source explore-graphql --into main
  python3 context-merge.py --project-root . log
  python3 context-merge.py --project-root . preview --source explore-graphql

Stdlib only — importe conversation-branch.py par importlib.

Références :
  - Three-way merge: https://en.wikipedia.org/wiki/Merge_(version_control)#Three-way_merge
  - Operational Transformation: https://en.wikipedia.org/wiki/Operational_transformation
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
import logging

_log = logging.getLogger("grimoire.context_merge")

# ── Version ──────────────────────────────────────────────────────────────────

CONTEXT_MERGE_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

RUNS_DIR = "_bmad-output/.runs"
MERGE_LOG_FILE = "_bmad/_memory/merge-log.md"
DEFAULT_BRANCH = "main"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ArtifactDiff:
    """Différence d'un artifact entre deux branches."""

    path: str
    status: str = ""  # "added" | "modified" | "deleted" | "same"
    branch: str = ""
    size_bytes: int = 0
    modified_at: str = ""


@dataclass
class DecisionDiff:
    """Différence de décision entre deux branches."""

    decision: str
    branch_a_value: str = ""
    branch_b_value: str = ""
    conflict: bool = False


@dataclass
class BranchDiff:
    """Résultat de la comparaison de deux branches."""

    branch_a: str
    branch_b: str
    artifacts_only_a: list[ArtifactDiff] = field(default_factory=list)
    artifacts_only_b: list[ArtifactDiff] = field(default_factory=list)
    artifacts_modified: list[ArtifactDiff] = field(default_factory=list)
    artifacts_same: list[str] = field(default_factory=list)
    decisions_a: list[str] = field(default_factory=list)
    decisions_b: list[str] = field(default_factory=list)
    decisions_conflicts: list[DecisionDiff] = field(default_factory=list)
    learnings_a: list[str] = field(default_factory=list)
    learnings_b: list[str] = field(default_factory=list)
    total_differences: int = 0
    has_conflicts: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MergeAction:
    """Action de merge."""

    action_type: str  # "copy" | "skip" | "conflict" | "overwrite"
    source_path: str
    target_path: str = ""
    detail: str = ""


@dataclass
class MergeResult:
    """Résultat d'un merge."""

    merge_id: str = ""
    source_branch: str = ""
    target_branch: str = ""
    status: str = "pending"  # "completed" | "partial" | "conflict" | "failed"
    actions: list[MergeAction] = field(default_factory=list)
    files_copied: int = 0
    conflicts_found: int = 0
    merged_at: str = ""
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.merge_id:
            self.merge_id = f"merge-{uuid.uuid4().hex[:8]}"
        if not self.merged_at:
            self.merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return asdict(self)


# ── Module Loader ───────────────────────────────────────────────────────────


def _load_branch_module():
    mod_name = "conversation_branch"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = Path(__file__).parent / "conversation-branch.py"
    if not mod_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Context Merger ──────────────────────────────────────────────────────────


class ContextMerger:
    """
    Merge et diff de contexte entre branches de conversation.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.runs_dir = project_root / RUNS_DIR
        self._branch_mod = _load_branch_module()

    def _branch_dir(self, name: str) -> Path:
        return self.runs_dir / name

    def _list_artifacts(self, branch_name: str) -> dict[str, ArtifactDiff]:
        """Liste les artifacts d'une branche."""
        branch_dir = self._branch_dir(branch_name)
        artifacts: dict[str, ArtifactDiff] = {}
        if not branch_dir.exists():
            return artifacts

        for f in sorted(branch_dir.rglob("*")):
            if f.is_file() and f.name not in ("branch.json", "state.json", ".active-branch"):
                rel = str(f.relative_to(branch_dir))
                try:
                    stat = f.stat()
                    artifacts[rel] = ArtifactDiff(
                        path=rel,
                        branch=branch_name,
                        size_bytes=stat.st_size,
                        modified_at=time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(stat.st_mtime),
                        ),
                    )
                except OSError:
                    continue
        return artifacts

    def _extract_decisions(self, branch_name: str) -> list[str]:
        """Extrait les décisions d'une branche (depuis branch.json)."""
        branch_dir = self._branch_dir(branch_name)
        manifest = branch_dir / "branch.json"
        if not manifest.exists():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            snapshot = data.get("context_snapshot", {})
            return snapshot.get("decisions_in_branch", [])
        except (json.JSONDecodeError, KeyError):
            return []

    def _extract_learnings(self, branch_name: str) -> list[str]:
        """Extrait les learnings d'une branche (fichiers *.md contenant 'learning')."""
        branch_dir = self._branch_dir(branch_name)
        learnings = []
        if not branch_dir.exists():
            return learnings
        for f in branch_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                # Extract lines with "learning" or "apprentissage"
                for line in content.split("\n"):
                    if re.search(r"learning|apprentissage|leçon|insight", line, re.IGNORECASE):
                        learnings.append(line.strip()[:200])
            except OSError:
                continue
        return learnings

    def diff(self, branch_a: str, branch_b: str) -> BranchDiff:
        """
        Compare deux branches.

        Returns:
            BranchDiff avec les différences détaillées
        """
        arts_a = self._list_artifacts(branch_a)
        arts_b = self._list_artifacts(branch_b)

        only_a = []
        only_b = []
        modified = []
        same = []

        all_paths = set(arts_a.keys()) | set(arts_b.keys())
        for path in sorted(all_paths):
            in_a = path in arts_a
            in_b = path in arts_b
            if in_a and not in_b:
                ad = arts_a[path]
                ad.status = "added"
                only_a.append(ad)
            elif in_b and not in_a:
                ad = arts_b[path]
                ad.status = "added"
                only_b.append(ad)
            elif in_a and in_b:
                if arts_a[path].size_bytes != arts_b[path].size_bytes:
                    ad = arts_a[path]
                    ad.status = "modified"
                    modified.append(ad)
                else:
                    same.append(path)

        decisions_a = self._extract_decisions(branch_a)
        decisions_b = self._extract_decisions(branch_b)
        learnings_a = self._extract_learnings(branch_a)
        learnings_b = self._extract_learnings(branch_b)

        # Detect decision conflicts (same topic, different value)
        conflicts = []
        for da in decisions_a:
            for db in decisions_b:
                # Simple heuristic: if first 20 chars match but full doesn't, it's a conflict
                if da[:20].lower() == db[:20].lower() and da != db:
                    conflicts.append(DecisionDiff(
                        decision=da[:20],
                        branch_a_value=da,
                        branch_b_value=db,
                        conflict=True,
                    ))

        total_diff = len(only_a) + len(only_b) + len(modified)

        return BranchDiff(
            branch_a=branch_a,
            branch_b=branch_b,
            artifacts_only_a=only_a,
            artifacts_only_b=only_b,
            artifacts_modified=modified,
            artifacts_same=same,
            decisions_a=decisions_a,
            decisions_b=decisions_b,
            decisions_conflicts=conflicts,
            learnings_a=learnings_a,
            learnings_b=learnings_b,
            total_differences=total_diff,
            has_conflicts=len(conflicts) > 0,
        )

    def preview(self, source: str, target: str = DEFAULT_BRANCH) -> list[MergeAction]:
        """Aperçu des actions de merge."""
        diff_result = self.diff(target, source)
        actions = []

        # Files only in source -> copy
        for art in diff_result.artifacts_only_b:
            actions.append(MergeAction(
                action_type="copy",
                source_path=str(self._branch_dir(source) / art.path),
                target_path=str(self._branch_dir(target) / art.path),
                detail=f"Copier {art.path} depuis {source}",
            ))

        # Modified files -> conflict or overwrite
        for art in diff_result.artifacts_modified:
            actions.append(MergeAction(
                action_type="conflict",
                source_path=str(self._branch_dir(source) / art.path),
                target_path=str(self._branch_dir(target) / art.path),
                detail=f"Fichier modifié dans les deux branches: {art.path}",
            ))

        return actions

    def merge(
        self,
        source: str,
        target: str = DEFAULT_BRANCH,
        force: bool = False,
    ) -> MergeResult:
        """
        Fusionne une branche source vers une cible.

        Args:
            source: branche source
            target: branche cible (défaut: main)
            force: forcer le merge même avec des conflits

        Returns:
            MergeResult
        """
        result = MergeResult(
            source_branch=source,
            target_branch=target,
        )

        # Check branches exist
        source_dir = self._branch_dir(source)
        target_dir = self._branch_dir(target)
        if not source_dir.exists():
            result.status = "failed"
            result.errors.append(f"Branche source '{source}' non trouvée")
            return result
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)

        # Get diff
        diff_result = self.diff(target, source)

        if diff_result.has_conflicts and not force:
            result.status = "conflict"
            result.conflicts_found = len(diff_result.decisions_conflicts)
            for c in diff_result.decisions_conflicts:
                result.actions.append(MergeAction(
                    action_type="conflict",
                    source_path=source,
                    target_path=target,
                    detail=f"Décision conflictuelle: {c.decision}",
                ))
            return result

        # Copy new artifacts from source to target
        for art in diff_result.artifacts_only_b:
            src_file = source_dir / art.path
            dst_file = target_dir / art.path
            try:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_file), str(dst_file))
                result.files_copied += 1
                result.actions.append(MergeAction(
                    action_type="copy",
                    source_path=str(src_file),
                    target_path=str(dst_file),
                    detail=f"Copié {art.path}",
                ))
            except OSError as e:
                result.errors.append(f"Erreur copie {art.path}: {e}")

        # Handle modified files
        for art in diff_result.artifacts_modified:
            if force:
                src_file = source_dir / art.path
                dst_file = target_dir / art.path
                try:
                    shutil.copy2(str(src_file), str(dst_file))
                    result.files_copied += 1
                    result.actions.append(MergeAction(
                        action_type="overwrite",
                        source_path=str(src_file),
                        target_path=str(dst_file),
                        detail=f"Écrasé {art.path}",
                    ))
                except OSError as e:
                    result.errors.append(f"Erreur overwrite {art.path}: {e}")
            else:
                result.conflicts_found += 1
                result.actions.append(MergeAction(
                    action_type="conflict",
                    source_path=str(source_dir / art.path),
                    target_path=str(target_dir / art.path),
                    detail=f"Conflit: {art.path} modifié dans les deux branches",
                ))

        # Merge decisions
        target_manifest = target_dir / "branch.json"
        if target_manifest.exists():
            try:
                data = json.loads(target_manifest.read_text(encoding="utf-8"))
                snapshot = data.get("context_snapshot", {})
                existing_decisions = set(snapshot.get("decisions_in_branch", []))
                for d in diff_result.decisions_b:
                    if d not in existing_decisions:
                        existing_decisions.add(d)
                snapshot["decisions_in_branch"] = sorted(existing_decisions)
                data["context_snapshot"] = snapshot
                target_manifest.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except (json.JSONDecodeError, KeyError) as e:
                result.errors.append(f"Erreur merge decisions: {e}")

        result.status = "completed" if not result.errors and result.conflicts_found == 0 else "partial"

        # Update source branch status
        if self._branch_mod:
            try:
                mgr_cls = getattr(self._branch_mod, "BranchManager", None)
                if mgr_cls:
                    mgr = mgr_cls(self.project_root)
                    source_info = mgr.get_info(source)
                    if source_info:
                        source_info.status = "merged"
                        mgr._save_branch_info(source_info)
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Write merge log
        self._write_merge_log(result)

        return result

    def _write_merge_log(self, result: MergeResult) -> None:
        """Écrit le résultat du merge dans le merge log."""
        log_path = self.project_root / MERGE_LOG_FILE
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = (
            f"\n## Merge {result.merge_id}\n\n"
            f"- **Date** : {result.merged_at}\n"
            f"- **Source** : {result.source_branch}\n"
            f"- **Target** : {result.target_branch}\n"
            f"- **Status** : {result.status}\n"
            f"- **Files copied** : {result.files_copied}\n"
            f"- **Conflicts** : {result.conflicts_found}\n"
        )
        if result.errors:
            entry += f"- **Erreurs** : {', '.join(result.errors)}\n"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    def get_merge_log(self) -> str:
        """Lit le merge log."""
        log_path = self.project_root / MERGE_LOG_FILE
        if log_path.exists():
            return log_path.read_text(encoding="utf-8")
        return "(aucun merge enregistré)"


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_context_merge(
    project_root: str,
    action: str = "diff",
    branch_a: str = "main",
    branch_b: str = "",
    source: str = "",
    target: str = "main",
    force: bool = False,
) -> dict:
    """
    MCP tool `bmad_context_merge` — diff et merge de branches.
    """
    root = Path(project_root).resolve()
    merger = ContextMerger(root)

    if action == "diff":
        if not branch_b:
            return {"error": "Le paramètre 'branch_b' est requis"}
        diff_result = merger.diff(branch_a, branch_b)
        return diff_result.to_dict()
    elif action == "merge":
        if not source:
            return {"error": "Le paramètre 'source' est requis"}
        result = merger.merge(source, target, force=force)
        return result.to_dict()
    elif action == "preview":
        if not source:
            return {"error": "Le paramètre 'source' est requis"}
        actions = merger.preview(source, target)
        return {"actions": [asdict(a) for a in actions]}
    elif action == "log":
        return {"log": merger.get_merge_log()}
    else:
        return {"error": f"Action inconnue: {action}"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Context Merge — Diff et merge de branches de conversation BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"context-merge {CONTEXT_MERGE_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # diff
    diff_p = sub.add_parser("diff", help="Comparer deux branches")
    diff_p.add_argument("--branch-a", default="main", help="Branche A")
    diff_p.add_argument("--branch-b", required=True, help="Branche B")
    diff_p.add_argument("--json", action="store_true", help="Output JSON")

    # merge
    merge_p = sub.add_parser("merge", help="Fusionner une branche")
    merge_p.add_argument("--source", required=True, help="Branche source")
    merge_p.add_argument("--into", dest="target", default="main", help="Branche cible")
    merge_p.add_argument("--force", action="store_true", help="Forcer le merge")
    merge_p.add_argument("--json", action="store_true", help="Output JSON")

    # preview
    prev_p = sub.add_parser("preview", help="Aperçu du merge")
    prev_p.add_argument("--source", required=True, help="Branche source")
    prev_p.add_argument("--into", dest="target", default="main", help="Branche cible")
    prev_p.add_argument("--json", action="store_true", help="Output JSON")

    # log
    sub.add_parser("log", help="Historique des merges")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    merger = ContextMerger(project_root)

    if args.command == "diff":
        diff_result = merger.diff(args.branch_a, args.branch_b)
        if getattr(args, "json", False):
            print(json.dumps(diff_result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"\n  📊 Diff : {diff_result.branch_a} ↔ {diff_result.branch_b}")
            print(f"  {'─' * 55}")
            print(f"  Différences totales : {diff_result.total_differences}")
            print(f"  Conflits            : {'⚠️ Oui' if diff_result.has_conflicts else '✅ Non'}")
            print()

            if diff_result.artifacts_only_a:
                print(f"  Seulement dans {diff_result.branch_a} ({len(diff_result.artifacts_only_a)}) :")
                for a in diff_result.artifacts_only_a:
                    print(f"    + {a.path}")
            if diff_result.artifacts_only_b:
                print(f"  Seulement dans {diff_result.branch_b} ({len(diff_result.artifacts_only_b)}) :")
                for a in diff_result.artifacts_only_b:
                    print(f"    + {a.path}")
            if diff_result.artifacts_modified:
                print(f"  Modifiés ({len(diff_result.artifacts_modified)}) :")
                for a in diff_result.artifacts_modified:
                    print(f"    ~ {a.path}")
            if diff_result.decisions_conflicts:
                print("\n  ⚠️  Conflits de décision :")
                for c in diff_result.decisions_conflicts:
                    print(f"    {c.decision}")
                    print(f"      A: {c.branch_a_value[:80]}")
                    print(f"      B: {c.branch_b_value[:80]}")
            print()

    elif args.command == "merge":
        result = merger.merge(args.source, args.target, force=args.force)
        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            icon = {"completed": "✅", "partial": "⚠️", "conflict": "⚡", "failed": "❌"}.get(result.status, "❓")
            print(f"\n  {icon} Merge {result.merge_id}")
            print(f"    {result.source_branch} → {result.target_branch}")
            print(f"    Status    : {result.status}")
            print(f"    Copied    : {result.files_copied}")
            print(f"    Conflicts : {result.conflicts_found}")
            if result.errors:
                for err in result.errors:
                    print(f"    ❌ {err}")
            print()

    elif args.command == "preview":
        actions = merger.preview(args.source, args.target)
        if getattr(args, "json", False):
            print(json.dumps([asdict(a) for a in actions], ensure_ascii=False, indent=2))
        else:
            print(f"\n  🔍 Preview merge : {args.source} → {args.target}")
            print(f"  {'─' * 50}")
            if not actions:
                print("  (aucune action à effectuer)")
            for a in actions:
                icon = {"copy": "📋", "conflict": "⚡", "overwrite": "✏️"}.get(a.action_type, "•")
                print(f"    {icon} [{a.action_type}] {a.detail}")
            print()

    elif args.command == "log":
        log = merger.get_merge_log()
        print(log)


if __name__ == "__main__":
    main()
