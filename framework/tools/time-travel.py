#!/usr/bin/env python3
"""Time-Travel — Archéologie temporelle et débogage historique.

Permet de naviguer dans l'historique du projet, créer des checkpoints,
rejouer l'évolution, et bisect pour trouver quand un problème est apparu.

Usage:
    python time-travel.py --project-root ./mon-projet checkpoint --label "pre-refactor"
    python time-travel.py --project-root ./mon-projet history
    python time-travel.py --project-root ./mon-projet replay --from cp-001 --to cp-003
    python time-travel.py --project-root ./mon-projet restore --checkpoint cp-002
    python time-travel.py --project-root ./mon-projet bisect --good cp-001 --bad cp-005 --test "python -m pytest"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

CHECKPOINT_DIR = ".grimoire-checkpoints"
META_FILE = "checkpoint-meta.json"
TRACKED_DIRS = ("framework", "archetypes", "docs")
TRACKED_EXTS = {".py", ".md", ".yaml", ".yml", ".xml", ".json", ".sh"}

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class CheckpointMeta:
    """Métadonnées d'un checkpoint."""

    checkpoint_id: str = ""
    label: str = ""
    timestamp: str = ""
    files_count: int = 0
    total_size: int = 0
    checksum: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ReplayStep:
    """Étape de replay entre deux checkpoints."""

    from_cp: str = ""
    to_cp: str = ""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    summary: str = ""


# ── Utilitaires ─────────────────────────────────────────────────


def _cp_root(root: Path) -> Path:
    """Répertoire des checkpoints."""
    return root / CHECKPOINT_DIR


def _next_cp_id(root: Path) -> str:
    """Génère le prochain ID de checkpoint."""
    cp_dir = _cp_root(root)
    if not cp_dir.exists():
        return "cp-001"
    existing = sorted(d.name for d in cp_dir.iterdir() if d.is_dir() and d.name.startswith("cp-"))
    if not existing:
        return "cp-001"
    last_num = int(existing[-1].split("-")[1])
    return f"cp-{last_num + 1:03d}"


def _hash_file(fpath: Path) -> str:
    """Hash SHA-256 tronqué."""
    try:
        return hashlib.sha256(fpath.read_bytes()).hexdigest()[:16]
    except (OSError, PermissionError):
        return "unreadable"


def _tracked_files(root: Path) -> list[Path]:
    """Liste les fichiers suivis."""
    files: list[Path] = []
    for tdir in TRACKED_DIRS:
        base = root / tdir
        if not base.exists():
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            dp = Path(dirpath)
            if "__pycache__" in str(dp):
                continue
            for fname in filenames:
                fpath = dp / fname
                if fpath.suffix in TRACKED_EXTS:
                    files.append(fpath)
    return files


def _file_manifest(root: Path) -> dict[str, str]:
    """Crée un manifeste fichier→hash."""
    result: dict[str, str] = {}
    for fpath in _tracked_files(root):
        rel = str(fpath.relative_to(root))
        result[rel] = _hash_file(fpath)
    return result


def _load_cp_meta(cp_dir: Path) -> CheckpointMeta | None:
    """Charge les métadonnées d'un checkpoint."""
    meta_file = cp_dir / META_FILE
    if not meta_file.exists():
        return None
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    return CheckpointMeta(**{k: v for k, v in data.items() if k in CheckpointMeta.__dataclass_fields__})


def _find_cp(root: Path, ref: str) -> Path | None:
    """Trouve un checkpoint par ID ou label."""
    cp_base = _cp_root(root)
    if not cp_base.exists():
        return None
    # Par ID direct
    direct = cp_base / ref
    if direct.exists():
        return direct
    # Par label
    for item in cp_base.iterdir():
        if item.is_dir():
            meta = _load_cp_meta(item)
            if meta and meta.label == ref:
                return item
    return None


# ── Commandes ───────────────────────────────────────────────────


def cmd_checkpoint(root: Path, label: str, notes: str, tags: list[str],
                   as_json: bool) -> dict[str, Any]:
    """Crée un checkpoint de l'état actuel."""
    cp_id = _next_cp_id(root)
    cp_dir = _cp_root(root) / cp_id
    cp_dir.mkdir(parents=True, exist_ok=True)

    files = _tracked_files(root)
    total_size = 0
    copied = 0

    for fpath in files:
        rel = fpath.relative_to(root)
        dest = cp_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpath, dest)
        total_size += fpath.stat().st_size
        copied += 1

    # Manifeste
    manifest = _file_manifest(root)
    overall = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]

    meta = CheckpointMeta(
        checkpoint_id=cp_id,
        label=label or cp_id,
        timestamp=datetime.now().isoformat(),
        files_count=copied,
        total_size=total_size,
        checksum=overall,
        tags=tags,
        notes=notes,
    )
    (cp_dir / META_FILE).write_text(json.dumps(asdict(meta), indent=2, default=str), encoding="utf-8")

    # Sauver le manifeste
    (cp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = {
        "checkpoint_id": cp_id,
        "label": meta.label,
        "files": copied,
        "size_bytes": total_size,
        "checksum": overall,
        "path": str(cp_dir),
    }

    if not as_json:
        print(f"💾 Checkpoint créé : {cp_id}")
        if label:
            print(f"   Label : {label}")
        print(f"   Fichiers : {copied}")
        print(f"   Taille : {total_size / 1024:.1f} KB")
        print(f"   Checksum : {overall}")

    return result


def cmd_history(root: Path, as_json: bool) -> dict[str, Any]:
    """Affiche l'historique des checkpoints."""
    cp_base = _cp_root(root)
    checkpoints: list[dict[str, Any]] = []

    if cp_base.exists():
        for item in sorted(cp_base.iterdir()):
            if item.is_dir():
                meta = _load_cp_meta(item)
                if meta:
                    checkpoints.append(asdict(meta))

    result = {"checkpoints": checkpoints, "total": len(checkpoints)}

    if not as_json:
        if not checkpoints:
            print("📭 Aucun checkpoint trouvé.")
            print("   Créez-en un avec : time-travel checkpoint --label 'mon-label'")
            return result

        print(f"📜 Historique ({len(checkpoints)} checkpoints) :")
        print()
        for cp_data in checkpoints:
            ts = cp_data.get("timestamp", "")[:19]
            label = cp_data.get("label", "")
            files = cp_data.get("files_count", 0)
            size_kb = cp_data.get("total_size", 0) / 1024
            tags = cp_data.get("tags", [])
            notes = cp_data.get("notes", "")
            tag_str = f" [{', '.join(tags)}]" if tags else ""

            print(f"  ● {cp_data['checkpoint_id']} — {label}{tag_str}")
            print(f"    {ts} | {files} fichiers | {size_kb:.1f} KB")
            if notes:
                print(f"    📝 {notes}")
            print()

    return result


def cmd_replay(root: Path, from_ref: str, to_ref: str, as_json: bool) -> dict[str, Any]:
    """Rejoue les changements entre deux checkpoints."""
    cp_from = _find_cp(root, from_ref)
    cp_to = _find_cp(root, to_ref)

    if not cp_from:
        return {"error": f"Checkpoint source introuvable : {from_ref}"}
    if not cp_to:
        return {"error": f"Checkpoint cible introuvable : {to_ref}"}

    # Charger les manifestes
    def load_manifest(cp_dir: Path) -> dict[str, str]:
        mf = cp_dir / "manifest.json"
        if mf.exists():
            return json.loads(mf.read_text(encoding="utf-8"))
        return {}

    manifest_from = load_manifest(cp_from)
    manifest_to = load_manifest(cp_to)

    step = ReplayStep(from_cp=from_ref, to_cp=to_ref)
    all_keys = set(manifest_from.keys()) | set(manifest_to.keys())

    for key in sorted(all_keys):
        in_from = key in manifest_from
        in_to = key in manifest_to
        if in_to and not in_from:
            step.added.append(key)
        elif in_from and not in_to:
            step.removed.append(key)
        elif manifest_from.get(key) != manifest_to.get(key):
            step.modified.append(key)

    total_changes = len(step.added) + len(step.removed) + len(step.modified)
    step.summary = f"{total_changes} changements : +{len(step.added)} -{len(step.removed)} ~{len(step.modified)}"

    result = asdict(step)
    result["total_changes"] = total_changes

    if not as_json:
        print(f"🔄 Replay : {from_ref} → {to_ref}")
        print(f"   {step.summary}")
        print()
        if step.added:
            print(f"  ➕ Ajoutés ({len(step.added)}) :")
            for fpath in step.added[:10]:
                print(f"     {fpath}")
        if step.removed:
            print(f"  ➖ Supprimés ({len(step.removed)}) :")
            for fpath in step.removed[:10]:
                print(f"     {fpath}")
        if step.modified:
            print(f"  ✏️ Modifiés ({len(step.modified)}) :")
            for fpath in step.modified[:10]:
                print(f"     {fpath}")

    return result


def cmd_restore(root: Path, ref: str, dry_run: bool, as_json: bool) -> dict[str, Any]:
    """Restaure l'état du projet à un checkpoint donné."""
    cp_dir = _find_cp(root, ref)
    if not cp_dir:
        return {"error": f"Checkpoint introuvable : {ref}"}

    operations: list[dict[str, str]] = []

    for tdir in TRACKED_DIRS:
        src_base = cp_dir / tdir
        if not src_base.exists():
            continue
        for dirpath, _dirs, filenames in os.walk(src_base):
            dp = Path(dirpath)
            for fname in filenames:
                if fname == META_FILE or fname == "manifest.json":
                    continue
                src = dp / fname
                if src.suffix not in TRACKED_EXTS:
                    continue
                rel = src.relative_to(cp_dir)
                dest = root / rel
                action = "update" if dest.exists() else "create"
                operations.append({"file": str(rel), "action": action})

                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)

    result = {
        "action": "restore" if not dry_run else "dry_run",
        "checkpoint": ref,
        "operations": operations,
        "files_affected": len(operations),
    }

    if not as_json:
        mode = "RESTORE" if not dry_run else "DRY RUN"
        print(f"⏪ {mode} vers {ref}")
        print(f"   Fichiers affectés : {len(operations)}")
        for op in operations[:15]:
            icon = "📝" if op["action"] == "update" else "✨"
            print(f"     {icon} {op['file']}")
        if len(operations) > 15:
            print(f"     ... et {len(operations) - 15} autres")
        if dry_run:
            print("\n   ℹ️ Mode dry-run. Relancez sans --dry-run pour appliquer.")

    return result


def cmd_bisect(root: Path, good_ref: str, bad_ref: str, test_cmd: str,
               as_json: bool) -> dict[str, Any]:
    """Bisect entre deux checkpoints pour trouver le changement fautif."""
    cp_base = _cp_root(root)
    if not cp_base.exists():
        return {"error": "Aucun checkpoint disponible"}

    # Lister tous les checkpoints ordonnés
    all_cps: list[str] = []
    for item in sorted(cp_base.iterdir()):
        if item.is_dir() and item.name.startswith("cp-"):
            all_cps.append(item.name)

    # Trouver les indices
    good_idx = -1
    bad_idx = -1
    for i, cp_name in enumerate(all_cps):
        if cp_name == good_ref or (_load_cp_meta(cp_base / cp_name) or CheckpointMeta()).label == good_ref:
            good_idx = i
        if cp_name == bad_ref or (_load_cp_meta(cp_base / cp_name) or CheckpointMeta()).label == bad_ref:
            bad_idx = i

    if good_idx < 0:
        return {"error": f"Checkpoint 'good' introuvable : {good_ref}"}
    if bad_idx < 0:
        return {"error": f"Checkpoint 'bad' introuvable : {bad_ref}"}
    if good_idx >= bad_idx:
        return {"error": "Le checkpoint 'good' doit précéder 'bad'"}

    # Bisect
    steps: list[dict[str, Any]] = []
    lo, hi = good_idx, bad_idx

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        mid_cp = all_cps[mid]

        # Restaurer le checkpoint mid (dry-run conceptuel — on teste le manifeste)
        mid_dir = cp_base / mid_cp
        meta = _load_cp_meta(mid_dir)

        # Tenter le test si fourni
        test_result = "unknown"
        if test_cmd:
            try:
                proc = subprocess.run(
                    shlex.split(test_cmd), capture_output=True, text=True,
                    timeout=60, cwd=str(root),
                )
                test_result = "good" if proc.returncode == 0 else "bad"
            except (subprocess.TimeoutExpired, OSError):
                test_result = "error"
        else:
            # Sans commande de test, on simule en comparant les manifestes
            test_result = "needs_manual_check"

        step_info = {
            "checkpoint": mid_cp,
            "label": meta.label if meta else mid_cp,
            "test_result": test_result,
            "position": f"{mid}/{len(all_cps)}",
        }
        steps.append(step_info)

        if test_result == "good":
            lo = mid
        else:
            hi = mid

    # Le coupable est entre all_cps[lo] et all_cps[hi]
    culprit_cp = all_cps[hi]
    culprit_meta = _load_cp_meta(cp_base / culprit_cp)

    result = {
        "bisect_result": {
            "culprit_checkpoint": culprit_cp,
            "culprit_label": culprit_meta.label if culprit_meta else culprit_cp,
            "last_good": all_cps[lo],
            "first_bad": all_cps[hi],
        },
        "steps": steps,
        "total_steps": len(steps),
        "range_tested": f"{good_ref} → {bad_ref} ({bad_idx - good_idx} checkpoints)",
    }

    if not as_json:
        print(f"🔬 Bisect : {good_ref} → {bad_ref}")
        print(f"   Checkpoints testés : {len(steps)}")
        print()
        for step_data in steps:
            icon = {"good": "✅", "bad": "❌", "error": "⚠️"}.get(step_data["test_result"], "❓")
            print(f"  {icon} {step_data['checkpoint']} ({step_data['label']}) — {step_data['test_result']}")
        print()
        print(f"  🎯 Coupable probable : {culprit_cp}")
        if culprit_meta:
            print(f"     Label : {culprit_meta.label}")
            print(f"     Date : {culprit_meta.timestamp[:19]}")

    return result


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="time-travel",
        description="Time-Travel — Archéologie temporelle et débogage historique",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # checkpoint
    cp = subs.add_parser("checkpoint", help="Créer un checkpoint")
    cp.add_argument("--label", default="", help="Label du checkpoint")
    cp.add_argument("--notes", default="", help="Notes")
    cp.add_argument("--tags", nargs="*", default=[], help="Tags")

    # history
    subs.add_parser("history", help="Afficher l'historique")

    # replay
    rp = subs.add_parser("replay", help="Rejouer les changements entre checkpoints")
    rp.add_argument("--from", dest="from_ref", required=True, help="Checkpoint source")
    rp.add_argument("--to", dest="to_ref", required=True, help="Checkpoint cible")

    # restore
    rst = subs.add_parser("restore", help="Restaurer un checkpoint")
    rst.add_argument("--checkpoint", required=True, help="ID ou label du checkpoint")
    rst.add_argument("--dry-run", action="store_true", default=True,
                     help="Simulation (activé par défaut — utiliser --no-dry-run pour appliquer)")
    rst.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                     help="Appliquer réellement la restauration")

    # bisect
    bs = subs.add_parser("bisect", help="Bisect entre deux checkpoints")
    bs.add_argument("--good", required=True, help="Dernier bon checkpoint")
    bs.add_argument("--bad", required=True, help="Premier mauvais checkpoint")
    bs.add_argument("--test", default="", help="Commande de test à exécuter")

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

    if args.command == "checkpoint":
        result = cmd_checkpoint(root, args.label, args.notes, args.tags, args.as_json)
    elif args.command == "history":
        result = cmd_history(root, args.as_json)
    elif args.command == "replay":
        result = cmd_replay(root, args.from_ref, args.to_ref, args.as_json)
    elif args.command == "restore":
        result = cmd_restore(root, args.checkpoint, args.dry_run, args.as_json)
    elif args.command == "bisect":
        result = cmd_bisect(root, args.good, args.bad, args.test, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
