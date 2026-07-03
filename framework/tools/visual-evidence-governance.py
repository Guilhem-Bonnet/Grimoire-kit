#!/usr/bin/env python3
"""
visual-evidence-governance.py — Validation et retention des preuves visuelles.

Fonctions:
- check: valide retention-manifest.json et proof-pack.md
- purge: supprime les captures expirees et nettoie le manifeste

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
DEFAULT_EVIDENCE_DIR = Path("_grimoire-runtime-output/implementation-artifacts/visual-evidence")
DEFAULT_MANIFEST = DEFAULT_EVIDENCE_DIR / "retention-manifest.json"
DEFAULT_PROOF_PACK = Path("_grimoire-runtime-output/implementation-artifacts/proof-pack.md")
DEFAULT_REVIEW_FILE = DEFAULT_EVIDENCE_DIR / "ux-visual-da-review.md"


@dataclass
class Finding:
    level: str
    message: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_iso(ts: str) -> datetime | None:
    raw = (ts or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _isoz(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _extract_captures(manifest: Any) -> list[dict[str, Any]]:
    if isinstance(manifest, list):
        return [c for c in manifest if isinstance(c, dict)]
    if isinstance(manifest, dict):
        captures = manifest.get("captures")
        if isinstance(captures, list):
            return [c for c in captures if isinstance(c, dict)]
    return []


def _get_ticket_id(manifest: Any, capture: dict[str, Any]) -> str:
    cap_ticket = str(capture.get("ticket_id") or "").strip()
    if cap_ticket:
        return cap_ticket
    if isinstance(manifest, dict):
        return str(manifest.get("ticket_id") or "").strip()
    return ""


def _find_capture_path(capture: dict[str, Any], evidence_dir: Path) -> Path | None:
    for key in ("path", "file", "capture_path"):
        value = str(capture.get(key) or "").strip()
        if value:
            return evidence_dir / value
    return None


def _compute_expiry(capture: dict[str, Any]) -> datetime | None:
    expires_at = _parse_iso(str(capture.get("expires_at") or ""))
    if expires_at is not None:
        return expires_at
    created_at = _parse_iso(str(capture.get("created_at") or ""))
    if created_at is None:
        return None
    ttl_raw = capture.get("ttl_days")
    try:
        ttl_days = int(ttl_raw)
    except (TypeError, ValueError):
        return None
    if ttl_days <= 0:
        return None
    return created_at + timedelta(days=ttl_days)


def _contains_non_regression_pass(proof_pack: str) -> bool:
    header = re.search(r"(?im)^##\s+Non-regression visuelle\s*$", proof_pack)
    if not header:
        return False
    tail = proof_pack[header.end():]
    next_header = re.search(r"(?m)^##\s+", tail)
    section = tail[: next_header.start()] if next_header else tail
    has_checkbox = re.search(r"(?im)-\s*\[x\]\s+", section) is not None
    has_pass_word = re.search(r"(?im)\b(pass|ok|valide|validated)\b", section) is not None
    return has_checkbox or has_pass_word


def _parse_review_scores(review_text: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for key, pattern in {
        "ux": r"(?im)^\s*[-*]\s*\*\*UX\*\*\s*:\s*(\d)\s*/\s*5\s*$",
        "visuel": r"(?im)^\s*[-*]\s*\*\*Visuel\*\*\s*:\s*(\d)\s*/\s*5\s*$",
        "direction artistique": r"(?im)^\s*[-*]\s*\*\*Direction artistique\*\*\s*:\s*(\d)\s*/\s*5\s*$",
    }.items():
        match = re.search(pattern, review_text)
        if match is None:
            continue
        try:
            scores[key] = int(match.group(1))
        except (TypeError, ValueError):
            continue
    return scores


def _contains_required_sections(review_text: str, required_sections: list[str]) -> list[str]:
    missing: list[str] = []
    for section in required_sections:
        heading = section.strip()
        if not heading:
            continue
        if re.search(rf"(?im)^##\s+{re.escape(heading)}\s*$", review_text) is None:
            missing.append(heading)
    return missing


def _contains_required_surfaces(review_text: str, required_surfaces: list[str]) -> list[str]:
    missing: list[str] = []
    lowered = review_text.lower()
    for surface in required_surfaces:
        needle = surface.strip().lower()
        if needle and needle not in lowered:
            missing.append(surface.strip())
    return missing


def command_check(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    evidence_dir = project_root / args.evidence_dir
    manifest_path = project_root / args.manifest
    proof_pack_path = project_root / args.proof_pack
    review_path = project_root / args.review_file

    findings: list[Finding] = []

    if not manifest_path.exists():
        findings.append(Finding("error", f"Manifeste introuvable: {manifest_path}"))
        return _report(findings)

    try:
        manifest = _json_load(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("error", f"Impossible de lire le manifeste: {exc}"))
        return _report(findings)

    captures = _extract_captures(manifest)
    if len(captures) < args.min_captures:
        findings.append(Finding("error", f"Captures insuffisantes: {len(captures)} < {args.min_captures}"))

    viewport_coverage: set[str] = set()
    states: set[str] = set()

    for idx, capture in enumerate(captures, start=1):
        ticket_id = _get_ticket_id(manifest, capture)
        if not ticket_id:
            findings.append(Finding("error", f"Capture #{idx}: ticket_id manquant"))

        for key in ("objective", "source_tool", "created_at", "ttl_days"):
            value = capture.get(key)
            if value is None or str(value).strip() == "":
                findings.append(Finding("error", f"Capture #{idx}: champ requis manquant '{key}'"))

        created_at = _parse_iso(str(capture.get("created_at") or ""))
        if created_at is None:
            findings.append(Finding("error", f"Capture #{idx}: created_at invalide"))

        try:
            ttl_days = int(capture.get("ttl_days"))
            if ttl_days <= 0:
                raise ValueError("ttl_days <= 0")
        except (TypeError, ValueError):
            findings.append(Finding("error", f"Capture #{idx}: ttl_days doit etre un entier > 0"))

        expiry = _compute_expiry(capture)
        if expiry is None:
            findings.append(Finding("error", f"Capture #{idx}: impossible de calculer expires_at"))

        viewport = str(capture.get("viewport") or "").strip().lower()
        if viewport:
            viewport_coverage.add(viewport)

        state = str(capture.get("state") or "").strip().lower()
        if state:
            states.add(state)

        cap_path = _find_capture_path(capture, evidence_dir)
        if cap_path is None:
            findings.append(Finding("error", f"Capture #{idx}: chemin fichier manquant (path|file|capture_path)"))
        elif not cap_path.exists():
            findings.append(Finding("error", f"Capture #{idx}: fichier absent '{cap_path}'"))

    for req_viewport in args.required_viewports:
        rv = req_viewport.strip().lower()
        if rv and rv not in viewport_coverage:
            findings.append(Finding("error", f"Viewport requis absent: {rv}"))

    if len(states) < args.min_states:
        findings.append(Finding("error", f"Etats insuffisants: {len(states)} < {args.min_states}"))

    if not proof_pack_path.exists():
        findings.append(Finding("error", f"Proof pack introuvable: {proof_pack_path}"))
    else:
        try:
            proof_pack = proof_pack_path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(Finding("error", f"Impossible de lire proof-pack.md: {exc}"))
        else:
            if re.search(r"(?im)^##\s+Non-regression visuelle\s*$", proof_pack) is None:
                findings.append(Finding("error", "Section obligatoire manquante: '## Non-regression visuelle'"))
            elif not _contains_non_regression_pass(proof_pack):
                findings.append(Finding("error", "Section non-regression presente mais sans marqueur de validation (PASS/OK/- [x])"))

    if not review_path.exists():
        findings.append(Finding("error", f"Revue UX/Visuel/DA introuvable: {review_path}"))
    else:
        try:
            review_text = review_path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(Finding("error", f"Impossible de lire la revue UX/Visuel/DA: {exc}"))
        else:
            missing_sections = _contains_required_sections(review_text, args.required_review_sections)
            for section in missing_sections:
                findings.append(Finding("error", f"Section revue obligatoire manquante: '## {section}'"))

            missing_surfaces = _contains_required_surfaces(review_text, args.required_surfaces)
            for surface in missing_surfaces:
                findings.append(Finding("error", f"Surface auditee manquante dans la revue: {surface}"))

            scores = _parse_review_scores(review_text)
            for key in ("ux", "visuel", "direction artistique"):
                if key not in scores:
                    findings.append(Finding("error", f"Score manquant dans la revue: {key} (format attendu: n/5)"))
                    continue
                if scores[key] < args.min_review_score:
                    findings.append(Finding("error", f"Score {key} insuffisant: {scores[key]}/5 < {args.min_review_score}/5"))

    return _report(findings)


def command_purge(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    evidence_dir = project_root / args.evidence_dir
    manifest_path = project_root / args.manifest
    now = _now_utc()

    if not manifest_path.exists():
        print(f"INFO: manifeste absent, rien a purger: {manifest_path}")
        return 0

    try:
        manifest = _json_load(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: lecture manifeste impossible: {exc}")
        return 1

    captures = _extract_captures(manifest)
    kept: list[dict[str, Any]] = []
    removed = 0

    for capture in captures:
        expiry = _compute_expiry(capture)
        if expiry is None or expiry > now:
            kept.append(capture)
            continue

        cap_path = _find_capture_path(capture, evidence_dir)
        if cap_path and cap_path.exists() and cap_path.is_file():
            if args.dry_run:
                print(f"DRY-RUN: supprimer {cap_path}")
            else:
                cap_path.unlink(missing_ok=True)
                print(f"PURGE: supprime {cap_path}")
        removed += 1

    if removed == 0:
        print("INFO: aucune capture expiree")
        return 0

    if not args.dry_run:
        if isinstance(manifest, list):
            _json_dump(manifest_path, kept)
        elif isinstance(manifest, dict):
            manifest["captures"] = kept
            _json_dump(manifest_path, manifest)

    print(f"INFO: captures expirees purgees: {removed}")
    return 0


def command_scaffold(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    evidence_dir = project_root / args.evidence_dir
    manifest_path = project_root / args.manifest
    proof_pack_path = project_root / args.proof_pack
    review_path = project_root / args.review_file

    evidence_dir.mkdir(parents=True, exist_ok=True)

    ticket_id = args.ticket_id.strip()
    if not ticket_id:
        print("ERROR: ticket_id vide")
        return 1

    base = _now_utc().replace(microsecond=0)
    captures_spec = [
        (
            "cap-001",
            "capture-desktop-home.txt",
            "desktop",
            "home",
            "Baseline hero section and first CTA visibility.",
            base,
        ),
        (
            "cap-002",
            "capture-mobile-home.txt",
            "mobile",
            "home",
            "Mobile fold readability and primary CTA reachability.",
            base + timedelta(minutes=2),
        ),
        (
            "cap-003",
            "capture-desktop-interaction.txt",
            "desktop",
            "interaction",
            "Open state validation for navigation and focus indicator.",
            base + timedelta(minutes=5),
        ),
    ]

    captures_manifest: list[dict[str, Any]] = []
    for cap_id, filename, viewport, state, objective, created_at in captures_spec:
        expires_at = created_at + timedelta(days=args.ttl_days)
        capture_path = evidence_dir / filename
        capture_body = (
            "Visual capture placeholder\n"
            f"Ticket: {ticket_id}\n"
            f"Viewport: {viewport}\n"
            f"State: {state}\n"
            f"Objective: {objective}\n"
        )
        _write_text(capture_path, capture_body, force=args.force)

        captures_manifest.append(
            {
                "id": cap_id,
                "ticket_id": ticket_id,
                "objective": objective,
                "source_tool": "visual-evidence-governance:scaffold",
                "path": filename,
                "file_path": filename,
                "viewport": viewport,
                "state": state,
                "created_at": _isoz(created_at),
                "ttl_days": args.ttl_days,
                "expires_at": _isoz(expires_at),
                "status": "active",
            }
        )

    manifest_data = {
        "ticket_id": ticket_id,
        "captures": captures_manifest,
    }

    if not manifest_path.exists() or args.force:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        _json_dump(manifest_path, manifest_data)

    proof_pack_content = (
        "# Proof Pack\n\n"
        "## Validation Authority\n\n"
        "- [ ] User approval: pending\n"
        "- [ ] UX approval: pending\n"
        "- [ ] Art direction approval: pending\n"
        "- [ ] Technical approval: pending\n\n"
        "## Checks\n\n"
        "- Evidence storage path conforms to policy.\n"
        "- Retention metadata complete for all captures.\n"
        "- Ticket scope is explicit and consistent.\n\n"
        "## Non-regression visuelle\n\n"
        "- [ ] PASS: desktop and mobile baseline checks completed.\n"
        "- [ ] PASS: interaction-state checks completed.\n\n"
        "## Open Risks\n\n"
        "- Real screenshots still required before release acceptance.\n\n"
        "## Assumptions\n\n"
        "- This file starts as a checklist scaffold and must be updated with real evidence.\n"
    )
    _write_text(proof_pack_path, proof_pack_content, force=args.force)

    review_content = (
        "# Revue UX / Visuel / Direction artistique\n\n"
        "## UX\n\n"
        "- **UX**: 0/5\n"
        "- Surface: runtime-views-report.html\n"
        "- Surface: http://127.0.0.1:4174/\n"
        "- Observation: a completer\n\n"
        "## Visuel\n\n"
        "- **Visuel**: 0/5\n"
        "- Observation: a completer\n\n"
        "## Direction artistique\n\n"
        "- **Direction artistique**: 0/5\n"
        "- Observation: a completer\n"
    )
    _write_text(review_path, review_content, force=args.force)

    print(f"OK: scaffold visuel cree pour ticket_id={ticket_id}")
    print(f"INFO: evidence_dir={evidence_dir}")
    print(f"INFO: manifest={manifest_path}")
    print(f"INFO: proof_pack={proof_pack_path}")
    print(f"INFO: review={review_path}")
    return 0


def _report(findings: list[Finding]) -> int:
    if not findings:
        print("OK: gouvernance des preuves visuelles validee")
        return 0

    exit_code = 0
    for finding in findings:
        prefix = finding.level.upper()
        print(f"{prefix}: {finding.message}")
        if finding.level.lower() == "error":
            exit_code = 1
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visual evidence governance")
    parser.add_argument("--project-root", default=".", help="Workspace root")
    parser.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR), help="Visual evidence directory (relative to project-root)")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Retention manifest path (relative to project-root)")
    parser.add_argument("--proof-pack", default=str(DEFAULT_PROOF_PACK), help="Proof pack path (relative to project-root)")
    parser.add_argument("--review-file", default=str(DEFAULT_REVIEW_FILE), help="UX/Visuel/DA review path (relative to project-root)")
    parser.add_argument("--version", action="version", version=TOOL_VERSION)

    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Validate visual retention and proof requirements")
    check.add_argument("--min-captures", type=int, default=3, help="Minimum number of captures")
    check.add_argument("--min-states", type=int, default=2, help="Minimum number of unique states")
    check.add_argument(
        "--required-viewports",
        default="desktop,mobile",
        help="Comma-separated required viewport values",
    )
    check.add_argument(
        "--required-review-sections",
        default="UX,Visuel,Direction artistique",
        help="Comma-separated required review headings",
    )
    check.add_argument(
        "--required-surfaces",
        default="runtime-views-report.html,http://127.0.0.1:4174/",
        help="Comma-separated required surfaces mentioned in the review",
    )
    check.add_argument("--min-review-score", type=int, default=1, help="Minimum accepted score per UX/Visuel/DA axis (0-5)")

    purge = sub.add_parser("purge", help="Purge expired captures from evidence directory and manifest")
    purge.add_argument("--dry-run", action="store_true", help="Preview purge actions")

    scaffold = sub.add_parser("scaffold", help="Create a visual evidence skeleton for a ticket")
    scaffold.add_argument("--ticket-id", required=True, help="Ticket identifier for the visual delivery scope")
    scaffold.add_argument("--ttl-days", type=int, default=14, help="Default retention in days for scaffolded captures")
    scaffold.add_argument("--force", action="store_true", help="Overwrite existing manifest/proof-pack/placeholders")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if isinstance(getattr(args, "required_viewports", None), str):
        args.required_viewports = [x.strip() for x in args.required_viewports.split(",") if x.strip()]
    if isinstance(getattr(args, "required_review_sections", None), str):
        args.required_review_sections = [x.strip() for x in args.required_review_sections.split(",") if x.strip()]
    if isinstance(getattr(args, "required_surfaces", None), str):
        args.required_surfaces = [x.strip() for x in args.required_surfaces.split(",") if x.strip()]
    if hasattr(args, "min_review_score"):
        args.min_review_score = max(0, min(5, int(args.min_review_score)))

    if args.command == "check":
        return command_check(args)
    if args.command == "purge":
        return command_purge(args)
    if args.command == "scaffold":
        return command_scaffold(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
