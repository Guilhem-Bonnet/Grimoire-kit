#!/usr/bin/env python3
"""CRISPR — Édition chirurgicale de workflows.

Permet d'insérer, remplacer, supprimer et transplanter des segments
précis dans les workflows YAML/MD sans toucher au reste.
Comme CRISPR-Cas9 pour l'ADN, mais pour les workflows.

Usage:
    python crispr.py --project-root ./mon-projet scan --workflow review-cycle
    python crispr.py --project-root ./mon-projet splice --workflow review-cycle --at step:3 --insert "validation_gate"
    python crispr.py --project-root ./mon-projet excise --workflow review-cycle --segment step:5
    python crispr.py --project-root ./mon-projet transplant --from review-cycle:step:2 --to deploy-flow:step:1
    python crispr.py --project-root ./mon-projet validate --workflow review-cycle
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.crispr")

VERSION = "1.0.0"

EDIT_LOG_DIR = ".bmad-crispr"

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class WorkflowSegment:
    """Segment identifié d'un workflow."""

    segment_id: str = ""
    segment_type: str = ""  # step, phase, gate, condition, action
    name: str = ""
    line_start: int = 0
    line_end: int = 0
    content: str = ""
    indent_level: int = 0
    dependencies: list[str] = field(default_factory=list)


@dataclass
class EditOperation:
    """Opération d'édition CRISPR."""

    operation: str = ""  # splice, excise, transplant, mutate
    workflow: str = ""
    target: str = ""
    timestamp: str = ""
    before_hash: str = ""
    after_hash: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ── Parser de workflows ─────────────────────────────────────────


def _find_workflows(root: Path) -> dict[str, Path]:
    """Trouve tous les workflows du projet."""
    workflows: dict[str, Path] = {}
    for search_dir in ("framework/workflows", "archetypes"):
        base = root / search_dir
        if not base.exists():
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix in (".yaml", ".yml", ".md"):
                    name = fpath.stem
                    workflows[name] = fpath
    return workflows


def _parse_segments(content: str, filepath: Path) -> list[WorkflowSegment]:
    """Parse un fichier workflow en segments éditables."""
    segments: list[WorkflowSegment] = []
    lines = content.splitlines()
    is_yaml = filepath.suffix in (".yaml", ".yml")

    if is_yaml:
        segments = _parse_yaml_segments(lines)
    else:
        segments = _parse_md_segments(lines)

    return segments


def _parse_yaml_segments(lines: list[str]) -> list[WorkflowSegment]:
    """Parse les segments d'un workflow YAML."""
    segments: list[WorkflowSegment] = []
    current_segment: list[str] = []
    current_start = 0
    current_name = ""
    current_type = ""
    current_indent = 0
    seg_counter = 0

    step_patterns = [
        (r"^\s*-\s*(?:name|step|id)\s*:\s*(.+)", "step"),
        (r"^\s*(\w+)\s*:", "section"),
        (r"^\s*-\s+(.+)", "item"),
    ]

    for i, line in enumerate(lines):
        matched = False
        for pattern, stype in step_patterns:
            match = re.match(pattern, line)
            if match:
                # Sauver le segment précédent
                if current_segment:
                    seg_counter += 1
                    segments.append(WorkflowSegment(
                        segment_id=f"seg-{seg_counter:03d}",
                        segment_type=current_type or "block",
                        name=current_name or f"block-{seg_counter}",
                        line_start=current_start + 1,
                        line_end=i,
                        content="\n".join(current_segment),
                        indent_level=current_indent,
                    ))
                current_segment = [line]
                current_start = i
                current_name = match.group(1).strip().strip("'\"")
                current_type = stype
                current_indent = len(line) - len(line.lstrip())
                matched = True
                break

        if not matched and line.strip():
            current_segment.append(line)

    # Dernier segment
    if current_segment:
        seg_counter += 1
        segments.append(WorkflowSegment(
            segment_id=f"seg-{seg_counter:03d}",
            segment_type=current_type or "block",
            name=current_name or f"block-{seg_counter}",
            line_start=current_start + 1,
            line_end=len(lines),
            content="\n".join(current_segment),
            indent_level=current_indent,
        ))

    return segments


def _parse_md_segments(lines: list[str]) -> list[WorkflowSegment]:
    """Parse les segments d'un workflow Markdown."""
    segments: list[WorkflowSegment] = []
    current_segment: list[str] = []
    current_start = 0
    current_name = ""
    current_type = ""
    seg_counter = 0

    for i, line in enumerate(lines):
        # Détecter les titres comme délimiteurs
        header_match = re.match(r"^(#{1,6})\s+(.+)", line)
        step_match = re.match(r"^\s*(?:\d+[\.\)]\s+|[-*]\s+\*\*)(.*?)(?:\*\*)?$", line)

        if header_match or (step_match and not line.strip().startswith("-")):
            if current_segment:
                seg_counter += 1
                segments.append(WorkflowSegment(
                    segment_id=f"seg-{seg_counter:03d}",
                    segment_type=current_type or "section",
                    name=current_name or f"section-{seg_counter}",
                    line_start=current_start + 1,
                    line_end=i,
                    content="\n".join(current_segment),
                    indent_level=0,
                ))

            current_segment = [line]
            current_start = i
            if header_match:
                level = len(header_match.group(1))
                current_type = f"h{level}"
                current_name = header_match.group(2).strip()
            elif step_match:
                current_type = "step"
                current_name = step_match.group(1).strip()
        else:
            current_segment.append(line)

    if current_segment:
        seg_counter += 1
        segments.append(WorkflowSegment(
            segment_id=f"seg-{seg_counter:03d}",
            segment_type=current_type or "section",
            name=current_name or f"section-{seg_counter}",
            line_start=current_start + 1,
            line_end=len(lines),
            content="\n".join(current_segment),
            indent_level=0,
        ))

    return segments


def _hash_content(content: str) -> str:
    """Hash du contenu."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _log_edit(root: Path, edit: EditOperation) -> None:
    """Enregistre une opération d'édition."""
    log_dir = root / EDIT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "edit-log.jsonl"
    with open(log_file, "a", encoding="utf-8") as fobj:
        fobj.write(json.dumps(asdict(edit), default=str) + "\n")


# ── Commandes ───────────────────────────────────────────────────


def cmd_scan(root: Path, workflow_name: str | None, as_json: bool) -> dict[str, Any]:
    """Scanne et affiche la structure d'un ou tous les workflows."""
    workflows = _find_workflows(root)

    if workflow_name:
        if workflow_name not in workflows:
            return {"error": f"Workflow '{workflow_name}' introuvable. "
                    f"Disponibles : {', '.join(sorted(workflows.keys())[:10])}"}
        target = {workflow_name: workflows[workflow_name]}
    else:
        target = workflows

    results: dict[str, Any] = {}

    for wname, wpath in sorted(target.items()):
        content = wpath.read_text(encoding="utf-8", errors="replace")
        segments = _parse_segments(content, wpath)
        results[wname] = {
            "path": str(wpath.relative_to(root)),
            "segments": [asdict(seg) for seg in segments],
            "total_segments": len(segments),
            "total_lines": content.count("\n") + 1,
            "checksum": _hash_content(content),
        }

    output = {"workflows": results, "total_scanned": len(results)}

    if not as_json:
        for wname, wdata in results.items():
            print(f"🔬 Workflow : {wname}")
            print(f"   Fichier : {wdata['path']}")
            print(f"   Segments : {wdata['total_segments']} | Lignes : {wdata['total_lines']}")
            print()
            for seg in wdata["segments"]:
                type_icon = {
                    "step": "📌", "section": "📂", "h1": "📗", "h2": "📕",
                    "h3": "📙", "gate": "🚧", "item": "•", "block": "▪",
                }.get(seg["segment_type"], "▫")
                indent = "  " * seg.get("indent_level", 0)
                lines_range = f"L{seg['line_start']}-L{seg['line_end']}"
                print(f"    {indent}{type_icon} [{seg['segment_id']}] {seg['name']} "
                      f"({seg['segment_type']}) {lines_range}")
            print()

    return output


def cmd_splice(root: Path, workflow_name: str, at_ref: str,
               insert_content: str, position: str, as_json: bool,
               dry_run: bool = True) -> dict[str, Any]:
    """Insère du contenu à un point précis du workflow."""
    workflows = _find_workflows(root)
    if workflow_name not in workflows:
        return {"error": f"Workflow '{workflow_name}' introuvable"}

    wpath = workflows[workflow_name]
    original = wpath.read_text(encoding="utf-8")
    segments = _parse_segments(original, wpath)

    # Trouver le segment cible
    target_seg = None
    for seg in segments:
        if seg.segment_id == at_ref or seg.name == at_ref:
            target_seg = seg
            break

    if not target_seg:
        return {"error": f"Segment '{at_ref}' introuvable dans {workflow_name}"}

    lines = original.splitlines()
    insert_lines = insert_content.splitlines()

    if position == "before":
        insert_at = target_seg.line_start - 1
    elif position == "after":
        insert_at = target_seg.line_end
    else:
        # Replace
        del lines[target_seg.line_start - 1:target_seg.line_end]
        insert_at = target_seg.line_start - 1

    for i, new_line in enumerate(insert_lines):
        lines.insert(insert_at + i, new_line)

    new_content = "\n".join(lines) + "\n"

    if not dry_run:
        wpath.write_text(new_content, encoding="utf-8")

    edit = EditOperation(
        operation="splice",
        workflow=workflow_name,
        target=at_ref,
        timestamp=datetime.now().isoformat(),
        before_hash=_hash_content(original),
        after_hash=_hash_content(new_content),
        details={"position": position, "lines_inserted": len(insert_lines)},
    )
    if not dry_run:
        _log_edit(root, edit)

    result = {
        "operation": "splice",
        "action": "applied" if not dry_run else "dry_run",
        "workflow": workflow_name,
        "target_segment": at_ref,
        "position": position,
        "lines_inserted": len(insert_lines),
        "before_hash": edit.before_hash,
        "after_hash": edit.after_hash,
    }

    if not as_json:
        mode = "SPLICE" if not dry_run else "DRY RUN — splice"
        print(f"✂️ {mode} sur {workflow_name}")
        print(f"   Cible : {at_ref} ({position})")
        print(f"   Lignes insérées : {len(insert_lines)}")
        print(f"   Hash : {edit.before_hash} → {edit.after_hash}")
        if dry_run:
            print("\n   ℹ️ Mode dry-run. Relancez avec --no-dry-run pour appliquer.")

    return result


def cmd_excise(root: Path, workflow_name: str, segment_ref: str,
               as_json: bool, dry_run: bool = True) -> dict[str, Any]:
    """Supprime un segment d'un workflow."""
    workflows = _find_workflows(root)
    if workflow_name not in workflows:
        return {"error": f"Workflow '{workflow_name}' introuvable"}

    wpath = workflows[workflow_name]
    original = wpath.read_text(encoding="utf-8")
    segments = _parse_segments(original, wpath)

    target_seg = None
    for seg in segments:
        if seg.segment_id == segment_ref or seg.name == segment_ref:
            target_seg = seg
            break

    if not target_seg:
        return {"error": f"Segment '{segment_ref}' introuvable"}

    lines = original.splitlines()
    excised = lines[target_seg.line_start - 1:target_seg.line_end]
    del lines[target_seg.line_start - 1:target_seg.line_end]

    new_content = "\n".join(lines) + "\n"

    if not dry_run:
        wpath.write_text(new_content, encoding="utf-8")

    edit = EditOperation(
        operation="excise",
        workflow=workflow_name,
        target=segment_ref,
        timestamp=datetime.now().isoformat(),
        before_hash=_hash_content(original),
        after_hash=_hash_content(new_content),
        details={"lines_removed": len(excised), "excised_content": "\n".join(excised)},
    )
    if not dry_run:
        _log_edit(root, edit)

    result = {
        "operation": "excise",
        "action": "applied" if not dry_run else "dry_run",
        "workflow": workflow_name,
        "segment": segment_ref,
        "lines_removed": len(excised),
        "before_hash": edit.before_hash,
        "after_hash": edit.after_hash,
    }

    if not as_json:
        mode = "EXCISION" if not dry_run else "DRY RUN — excision"
        print(f"🗑️ {mode} sur {workflow_name}")
        print(f"   Segment : {segment_ref} ({target_seg.name})")
        print(f"   Lignes supprimées : {len(excised)}")
        print(f"   Hash : {edit.before_hash} → {edit.after_hash}")
        if dry_run:
            print("\n   ℹ️ Mode dry-run. Relancez avec --no-dry-run pour appliquer.")

    return result


def cmd_transplant(root: Path, source_ref: str, dest_ref: str,
                   as_json: bool) -> dict[str, Any]:
    """Transplante un segment d'un workflow vers un autre."""
    # Parse source: workflow:segment
    if ":" not in source_ref:
        return {"error": "Format source: 'workflow:segment'"}
    src_wf, src_seg = source_ref.split(":", 1)

    if ":" not in dest_ref:
        return {"error": "Format destination: 'workflow:segment'"}
    dst_wf, dst_seg = dest_ref.split(":", 1)

    workflows = _find_workflows(root)
    if src_wf not in workflows:
        return {"error": f"Workflow source '{src_wf}' introuvable"}
    if dst_wf not in workflows:
        return {"error": f"Workflow destination '{dst_wf}' introuvable"}

    # Extraire le segment source
    src_content = workflows[src_wf].read_text(encoding="utf-8")
    src_segments = _parse_segments(src_content, workflows[src_wf])

    source_segment = None
    for seg in src_segments:
        if seg.segment_id == src_seg or seg.name == src_seg:
            source_segment = seg
            break

    if not source_segment:
        return {"error": f"Segment source '{src_seg}' introuvable dans {src_wf}"}

    # Trouver le point d'insertion dans la destination
    dst_content = workflows[dst_wf].read_text(encoding="utf-8")
    dst_segments = _parse_segments(dst_content, workflows[dst_wf])

    dest_segment = None
    for seg in dst_segments:
        if seg.segment_id == dst_seg or seg.name == dst_seg:
            dest_segment = seg
            break

    if not dest_segment:
        return {"error": f"Segment destination '{dst_seg}' introuvable dans {dst_wf}"}

    # Insérer après le segment destination
    transplant_lines = source_segment.content.splitlines()
    dst_lines = dst_content.splitlines()
    insert_at = dest_segment.line_end

    for i, line in enumerate(transplant_lines):
        dst_lines.insert(insert_at + i, line)

    new_dst = "\n".join(dst_lines) + "\n"
    workflows[dst_wf].write_text(new_dst, encoding="utf-8")

    edit = EditOperation(
        operation="transplant",
        workflow=f"{src_wf}→{dst_wf}",
        target=f"{src_seg}→{dst_seg}",
        timestamp=datetime.now().isoformat(),
        before_hash=_hash_content(dst_content),
        after_hash=_hash_content(new_dst),
        details={"source": source_ref, "dest": dest_ref, "lines": len(transplant_lines)},
    )
    _log_edit(root, edit)

    result = {
        "operation": "transplant",
        "source": source_ref,
        "destination": dest_ref,
        "lines_transplanted": len(transplant_lines),
        "before_hash": edit.before_hash,
        "after_hash": edit.after_hash,
    }

    if not as_json:
        print("🧬 Transplant effectué")
        print(f"   Source : {src_wf}:{source_segment.name}")
        print(f"   Destination : {dst_wf}:{dest_segment.name}")
        print(f"   Lignes transplantées : {len(transplant_lines)}")

    return result


def cmd_validate(root: Path, workflow_name: str, as_json: bool) -> dict[str, Any]:
    """Valide l'intégrité d'un workflow après édition."""
    workflows = _find_workflows(root)
    if workflow_name not in workflows:
        return {"error": f"Workflow '{workflow_name}' introuvable"}

    wpath = workflows[workflow_name]
    content = wpath.read_text(encoding="utf-8")
    segments = _parse_segments(content, wpath)

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # Vérifications structurelles
    if not segments:
        issues.append({"type": "empty", "message": "Le workflow ne contient aucun segment"})

    # Vérifier les segments vides
    for seg in segments:
        if not seg.content.strip():
            warnings.append({"segment": seg.segment_id, "message": f"Segment '{seg.name}' est vide"})

    # Vérifier la cohérence des lignes
    lines = content.splitlines()
    total_lines = len(lines)
    for seg in segments:
        if seg.line_end > total_lines:
            issues.append({
                "segment": seg.segment_id,
                "message": f"Segment déborde au-delà du fichier (L{seg.line_end} > {total_lines})",
            })

    # Vérifier les références internes YAML
    if wpath.suffix in (".yaml", ".yml"):
        # Vérifier l'indentation cohérente
        for i, line in enumerate(lines, 1):
            if line.strip() and not line.startswith("#"):
                indent = len(line) - len(line.lstrip())
                if indent % 2 != 0:
                    warnings.append({
                        "line": str(i),
                        "message": f"Indentation impaire ({indent} espaces) — potentiel problème YAML",
                    })

    # Vérifier le log d'éditions
    log_file = root / EDIT_LOG_DIR / "edit-log.jsonl"
    edit_count = 0
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    edit_data = json.loads(line)
                    if edit_data.get("workflow") == workflow_name:
                        edit_count += 1
                except json.JSONDecodeError as _exc:
                    _log.debug("json.JSONDecodeError suppressed: %s", _exc)

    is_valid = len(issues) == 0
    health = "healthy" if not issues and not warnings else "warning" if not issues else "broken"

    result = {
        "workflow": workflow_name,
        "valid": is_valid,
        "health": health,
        "segments": len(segments),
        "lines": total_lines,
        "issues": issues,
        "warnings": warnings,
        "edit_history_count": edit_count,
        "checksum": _hash_content(content),
    }

    if not as_json:
        icon = "✅" if is_valid else "❌"
        print(f"{icon} Validation : {workflow_name}")
        print(f"   Santé : {health.upper()}")
        print(f"   Segments : {len(segments)} | Lignes : {total_lines}")
        print(f"   Éditions précédentes : {edit_count}")
        if issues:
            print(f"\n   🔴 Problèmes ({len(issues)}) :")
            for issue in issues:
                print(f"     {issue['message']}")
        if warnings:
            print(f"\n   🟡 Avertissements ({len(warnings)}) :")
            for warn in warnings:
                print(f"     {warn['message']}")
        if is_valid and not warnings:
            print("\n   ✨ Workflow en parfait état")

    return result


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="crispr",
        description="CRISPR — Édition chirurgicale de workflows",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # scan
    sc = subs.add_parser("scan", help="Scanner la structure d'un workflow")
    sc.add_argument("--workflow", help="Nom du workflow (tous si omis)")

    # splice
    sp = subs.add_parser("splice", help="Insérer du contenu dans un workflow")
    sp.add_argument("--workflow", required=True, help="Nom du workflow")
    sp.add_argument("--at", dest="at_ref", required=True, help="Segment cible (ID ou nom)")
    sp.add_argument("--insert", required=True, help="Contenu à insérer")
    sp.add_argument("--position", choices=["before", "after", "replace"], default="after",
                    help="Position d'insertion (défaut: after)")
    sp.add_argument("--dry-run", action="store_true", default=True,
                    help="Simulation (activé par défaut — utiliser --no-dry-run pour appliquer)")
    sp.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Appliquer réellement la modification")

    # excise
    ex = subs.add_parser("excise", help="Supprimer un segment")
    ex.add_argument("--workflow", required=True, help="Nom du workflow")
    ex.add_argument("--segment", required=True, help="Segment à supprimer")
    ex.add_argument("--dry-run", action="store_true", default=True,
                    help="Simulation (activé par défaut — utiliser --no-dry-run pour appliquer)")
    ex.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Appliquer réellement la suppression")

    # transplant
    tr = subs.add_parser("transplant", help="Transplanter un segment entre workflows")
    tr.add_argument("--from", dest="source", required=True,
                    help="Source (workflow:segment)")
    tr.add_argument("--to", dest="dest", required=True,
                    help="Destination (workflow:segment)")

    # validate
    va = subs.add_parser("validate", help="Valider l'intégrité d'un workflow")
    va.add_argument("--workflow", required=True, help="Nom du workflow")

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

    if args.command == "scan":
        result = cmd_scan(root, getattr(args, "workflow", None), args.as_json)
    elif args.command == "splice":
        result = cmd_splice(root, args.workflow, args.at_ref,
                            args.insert, args.position, args.as_json,
                            args.dry_run)
    elif args.command == "excise":
        result = cmd_excise(root, args.workflow, args.segment, args.as_json,
                            args.dry_run)
    elif args.command == "transplant":
        result = cmd_transplant(root, args.source, args.dest, args.as_json)
    elif args.command == "validate":
        result = cmd_validate(root, args.workflow, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
