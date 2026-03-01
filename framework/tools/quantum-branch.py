#!/usr/bin/env python3
"""Quantum Branch — Timelines parallèles et multivers de projet.

Gère des branches de configuration/architecture parallèles pour
explorer des alternatives sans risquer l'état principal.
Combine Quantum Branching (#30) et Multivers (#121).

Usage:
    python quantum-branch.py --project-root ./mon-projet fork --name "sans-analyst"
    python quantum-branch.py --project-root ./mon-projet list
    python quantum-branch.py --project-root ./mon-projet compare --branches main,sans-analyst
    python quantum-branch.py --project-root ./mon-projet merge --source sans-analyst
    python quantum-branch.py --project-root ./mon-projet prune --branch old-experiment
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

BRANCH_DIR = ".bmad-branches"
BRANCH_META = "branch-meta.json"
SNAP_DIRS = ("framework", "archetypes", "docs")
SNAP_EXTS = {".py", ".md", ".yaml", ".yml", ".xml", ".json", ".sh"}

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class BranchMeta:
    """Métadonnées d'une branche."""

    name: str
    created: str = ""
    parent: str = "main"
    description: str = ""
    status: str = "active"  # active, merged, pruned
    files_count: int = 0
    checksum: str = ""


@dataclass
class BranchDiff:
    """Différence entre deux branches."""

    source: str = ""
    target: str = ""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[dict[str, str]] = field(default_factory=list)
    unchanged: int = 0


# ── Utilitaires ─────────────────────────────────────────────────


def _branch_root(root: Path) -> Path:
    """Répertoire des branches."""
    return root / BRANCH_DIR


def _branch_path(root: Path, name: str) -> Path:
    """Chemin d'une branche spécifique."""
    return _branch_root(root) / name


def _compute_hash(filepath: Path) -> str:
    """Hash SHA-256 tronqué."""
    try:
        return hashlib.sha256(filepath.read_bytes()).hexdigest()[:16]
    except (OSError, PermissionError):
        return "unreadable"


def _list_project_files(root: Path) -> list[Path]:
    """Liste les fichiers pertinents du projet."""
    files: list[Path] = []
    for snap_dir in SNAP_DIRS:
        base = root / snap_dir
        if not base.exists():
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            dp = Path(dirpath)
            if "__pycache__" in str(dp) or ".git" in str(dp):
                continue
            for fname in filenames:
                fpath = dp / fname
                if fpath.suffix in SNAP_EXTS:
                    files.append(fpath)
    return files


def _file_map(root: Path, files: list[Path] | None = None) -> dict[str, str]:
    """Crée un map chemin_relatif → hash pour les fichiers du projet."""
    if files is None:
        files = _list_project_files(root)
    result: dict[str, str] = {}
    for fpath in files:
        try:
            rel = str(fpath.relative_to(root))
        except ValueError:
            rel = str(fpath)
        result[rel] = _compute_hash(fpath)
    return result


def _load_meta(branch_dir: Path) -> BranchMeta | None:
    """Charge les métadonnées d'une branche."""
    meta_file = branch_dir / BRANCH_META
    if not meta_file.exists():
        return None
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    return BranchMeta(**{k: v for k, v in data.items() if k in BranchMeta.__dataclass_fields__})


def _save_meta(branch_dir: Path, meta: BranchMeta) -> None:
    """Sauvegarde les métadonnées d'une branche."""
    meta_file = branch_dir / BRANCH_META
    meta_file.write_text(json.dumps(asdict(meta), indent=2, default=str), encoding="utf-8")


# ── Commandes ───────────────────────────────────────────────────


def cmd_fork(root: Path, name: str, description: str, as_json: bool) -> dict[str, Any]:
    """Crée une nouvelle branche (timeline parallèle)."""
    branch_dir = _branch_path(root, name)

    if branch_dir.exists():
        msg = f"La branche '{name}' existe déjà"
        if not as_json:
            print(f"❌ {msg}", file=sys.stderr)
        return {"error": msg}

    # Copier les fichiers du projet
    files = _list_project_files(root)
    branch_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    for fpath in files:
        rel = fpath.relative_to(root)
        dest = branch_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpath, dest)
        copied += 1

    # Créer les métadonnées
    fmap = _file_map(root, files)
    overall_hash = hashlib.sha256(json.dumps(fmap, sort_keys=True).encode()).hexdigest()[:16]

    meta = BranchMeta(
        name=name,
        created=datetime.now().isoformat(),
        parent="main",
        description=description,
        files_count=copied,
        checksum=overall_hash,
    )
    _save_meta(branch_dir, meta)

    result = {
        "action": "fork",
        "branch": name,
        "files_copied": copied,
        "checksum": overall_hash,
        "path": str(branch_dir),
    }

    if not as_json:
        print(f"🔀 Branche '{name}' créée")
        print(f"   Fichiers copiés : {copied}")
        print(f"   Checksum : {overall_hash}")
        print(f"   Description : {description or '(aucune)'}")
        print("\n   Pour modifier cette branche, éditez les fichiers dans :")
        print(f"   {branch_dir}/")

    return result


def cmd_list(root: Path, as_json: bool) -> dict[str, Any]:
    """Liste toutes les branches."""
    br = _branch_root(root)
    branches: list[dict[str, Any]] = []

    # Branche principale (état actuel)
    fmap = _file_map(root)
    main_hash = hashlib.sha256(json.dumps(fmap, sort_keys=True).encode()).hexdigest()[:16]
    branches.append({
        "name": "main",
        "status": "active",
        "files": len(fmap),
        "checksum": main_hash,
        "created": "origin",
        "description": "État actuel du projet",
    })

    if br.exists():
        for item in sorted(br.iterdir()):
            if item.is_dir():
                meta = _load_meta(item)
                if meta:
                    branches.append(asdict(meta))

    result = {"branches": branches, "total": len(branches)}

    if not as_json:
        print(f"🌿 Branches ({len(branches)}) :")
        print()
        for branch in branches:
            status_icon = {"active": "🟢", "merged": "✅", "pruned": "🗑️"}.get(branch.get("status", ""), "⚪")
            print(f"  {status_icon} {branch['name']}")
            if branch.get("description"):
                print(f"     {branch['description']}")
            print(f"     Fichiers : {branch.get('files_count', branch.get('files', '?'))} | "
                  f"Checksum : {branch.get('checksum', '?')}")
            if branch.get("created") and branch["created"] != "origin":
                print(f"     Créée : {branch['created'][:19]}")
            print()

    return result


def cmd_compare(root: Path, branch_names: list[str], as_json: bool) -> dict[str, Any]:
    """Compare deux branches."""
    if len(branch_names) != 2:
        return {"error": "Exactement 2 branches requises pour la comparaison"}

    name_a, name_b = branch_names

    # Charger les maps
    def get_fmap(name: str) -> dict[str, str]:
        if name == "main":
            return _file_map(root)
        bp = _branch_path(root, name)
        if not bp.exists():
            return {}
        files: list[Path] = []
        for dirpath, _dirs, filenames in os.walk(bp):
            dp = Path(dirpath)
            if BRANCH_META in filenames:
                continue
            for fname in filenames:
                fpath = dp / fname
                if fpath.suffix in SNAP_EXTS:
                    files.append(fpath)
        return _file_map(bp, files)

    map_a = get_fmap(name_a)
    map_b = get_fmap(name_b)

    if not map_a:
        return {"error": f"Branche '{name_a}' introuvable ou vide"}
    if not map_b:
        return {"error": f"Branche '{name_b}' introuvable ou vide"}

    diff = BranchDiff(source=name_a, target=name_b)
    all_keys = set(map_a.keys()) | set(map_b.keys())

    for key in sorted(all_keys):
        in_a = key in map_a
        in_b = key in map_b
        if in_a and not in_b:
            diff.removed.append(key)
        elif not in_a and in_b:
            diff.added.append(key)
        elif map_a[key] != map_b[key]:
            diff.modified.append({"file": key, "hash_a": map_a[key], "hash_b": map_b[key]})
        else:
            diff.unchanged += 1

    result = asdict(diff)
    result["total_differences"] = len(diff.added) + len(diff.removed) + len(diff.modified)
    result["divergence_ratio"] = round(
        result["total_differences"] / max(len(all_keys), 1) * 100, 1
    )

    if not as_json:
        print(f"🔍 Comparaison : {name_a} ↔ {name_b}")
        print(f"   Divergence : {result['divergence_ratio']}%")
        print(f"   Total différences : {result['total_differences']}")
        print()
        if diff.added:
            print(f"  ➕ Uniquement dans {name_b} ({len(diff.added)}) :")
            for fpath in diff.added[:10]:
                print(f"     {fpath}")
        if diff.removed:
            print(f"  ➖ Uniquement dans {name_a} ({len(diff.removed)}) :")
            for fpath in diff.removed[:10]:
                print(f"     {fpath}")
        if diff.modified:
            print(f"  ✏️ Modifiés ({len(diff.modified)}) :")
            for mod in diff.modified[:10]:
                print(f"     {mod['file']}")
        print(f"  ═ Identiques : {diff.unchanged}")

    return result


def cmd_merge(root: Path, source: str, dry_run: bool, as_json: bool) -> dict[str, Any]:
    """Fusionne une branche vers main."""
    branch_dir = _branch_path(root, source)
    if not branch_dir.exists():
        return {"error": f"Branche '{source}' introuvable"}

    meta = _load_meta(branch_dir)
    if meta and meta.status == "merged":
        return {"error": f"Branche '{source}' déjà fusionnée"}

    # Calculer ce qui serait copié
    operations: list[dict[str, str]] = []
    for dirpath, _dirs, filenames in os.walk(branch_dir):
        dp = Path(dirpath)
        for fname in filenames:
            if fname == BRANCH_META:
                continue
            src = dp / fname
            if src.suffix not in SNAP_EXTS:
                continue
            rel = src.relative_to(branch_dir)
            dest = root / rel
            if dest.exists():
                if _compute_hash(src) != _compute_hash(dest):
                    operations.append({"file": str(rel), "action": "update"})
            else:
                operations.append({"file": str(rel), "action": "create"})

    if not dry_run:
        for op in operations:
            src = branch_dir / op["file"]
            dest = root / op["file"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        # Marquer comme fusionnée
        if meta:
            meta.status = "merged"
            _save_meta(branch_dir, meta)

    result = {
        "action": "merge" if not dry_run else "dry_run",
        "source": source,
        "operations": operations,
        "files_affected": len(operations),
    }

    if not as_json:
        mode = "DRY RUN" if dry_run else "MERGE"
        print(f"🔀 {mode} : {source} → main")
        print(f"   Fichiers affectés : {len(operations)}")
        for op in operations[:15]:
            icon = "📝" if op["action"] == "update" else "✨"
            print(f"     {icon} {op['file']} [{op['action']}]")
        if len(operations) > 15:
            print(f"     ... et {len(operations) - 15} autres")
        if dry_run:
            print("\n   ℹ️ Mode dry-run — aucun fichier modifié. Relancez sans --dry-run pour appliquer.")

    return result


def cmd_prune(root: Path, branch_name: str, force: bool, as_json: bool) -> dict[str, Any]:
    """Supprime une branche."""
    branch_dir = _branch_path(root, branch_name)
    if not branch_dir.exists():
        return {"error": f"Branche '{branch_name}' introuvable"}

    meta = _load_meta(branch_dir)
    if meta and meta.status == "active" and not force:
        msg = (f"Branche '{branch_name}' est active. "
               f"Utilisez --force pour supprimer une branche active.")
        if not as_json:
            print(f"⚠️ {msg}", file=sys.stderr)
        return {"error": msg}

    # Compter les fichiers avant suppression
    file_count = sum(1 for _ in branch_dir.rglob("*") if _.is_file())
    shutil.rmtree(branch_dir)

    result = {
        "action": "prune",
        "branch": branch_name,
        "files_removed": file_count,
        "status": "pruned",
    }

    if not as_json:
        print(f"🗑️ Branche '{branch_name}' supprimée")
        print(f"   Fichiers supprimés : {file_count}")

    return result


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="quantum-branch",
        description="Quantum Branch — Timelines parallèles et multivers de projet",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # fork
    fork = subs.add_parser("fork", help="Créer une nouvelle branche")
    fork.add_argument("--name", required=True, help="Nom de la branche")
    fork.add_argument("--description", default="", help="Description")

    # list
    subs.add_parser("list", help="Lister les branches")

    # compare
    cmp = subs.add_parser("compare", help="Comparer deux branches")
    cmp.add_argument("--branches", required=True,
                     help="Noms séparés par virgule (ex: main,experiment)")

    # merge
    mrg = subs.add_parser("merge", help="Fusionner une branche vers main")
    mrg.add_argument("--source", required=True, help="Branche source")
    mrg.add_argument("--dry-run", action="store_true", help="Simulation sans modification")

    # prune
    prn = subs.add_parser("prune", help="Supprimer une branche")
    prn.add_argument("--branch", required=True, help="Branche à supprimer")
    prn.add_argument("--force", action="store_true", help="Forcer la suppression")

    return parser


def main() -> None:
    """Point d'entrée principal."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    result: dict[str, Any] = {}

    if args.command == "fork":
        result = cmd_fork(root, args.name, args.description, args.as_json)
    elif args.command == "list":
        result = cmd_list(root, args.as_json)
    elif args.command == "compare":
        branches = [b.strip() for b in args.branches.split(",")]
        result = cmd_compare(root, branches, args.as_json)
    elif args.command == "merge":
        result = cmd_merge(root, args.source, args.dry_run, args.as_json)
    elif args.command == "prune":
        result = cmd_prune(root, args.branch, args.force, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
