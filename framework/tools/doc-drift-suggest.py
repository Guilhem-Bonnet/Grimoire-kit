#!/usr/bin/env python3
"""
doc-drift-suggest.py — Phase 4 : brief de patch documentation.
==============================================================

Produit un BRIEF MARKDOWN actionable pour les drifts detectes par
`doc-drift-detector.py`. Le brief est destine a etre :
  - consomme par le sub-agent `tech-writer` (dispatch SOG),
  - ou utilise tel quel par un humain pour appliquer les patches.

Design
------
- Deterministe : aucun appel LLM dans cet outil. Il PREPARE le travail du
  tech-writer en agregeant : diff source, etat actuel du mirror (extrait
  pertinent), regles `require` du manifeste, instructions de patch.
- Idempotent : appelable plusieurs fois sans effet de bord.
- Sortie : `_grimoire-runtime-output/doc-drift/brief-<timestamp>.md` ou stdout.

Usage
-----
    # Generer un brief depuis un report JSON existant
    python3 doc-drift-suggest.py --report doc-drift-report.json

    # Generer brief + report en une passe (depuis HEAD)
    python3 doc-drift-suggest.py --project-root . --since HEAD

    # Stdout (pas d'ecriture disque) — pour pipe vers tech-writer
    python3 doc-drift-suggest.py --project-root . --since HEAD --stdout

Exit codes
----------
  0 = brief genere (drift ou non)
  2 = erreur (manifeste absent, project-root invalide)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
MAX_DIFF_LINES = 80
MAX_MIRROR_LINES = 40


def _git_diff(project_root: Path, path: str, since: str) -> str:
    try:
        out = subprocess.run(
            ["git", "diff", since, "--", path],
            cwd=project_root, capture_output=True, text=True, check=False,
        )
        return out.stdout
    except Exception:
        return ""


def _truncate(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = lines[: max_lines - 5]
    return "\n".join(head) + f"\n... [{len(lines) - len(head)} lignes tronquees] ..."


def _load_report(
    project_root: Path,
    since: str,
    report_path: Path | None,
) -> dict:
    if report_path:
        return json.loads(report_path.read_text(encoding="utf-8"))
    # Invoquer le detecteur en mode JSON
    detector = THIS_DIR / "doc-drift-detector.py"
    if not detector.exists():
        sys.stderr.write("[ERR] doc-drift-detector.py introuvable\n")
        sys.exit(2)
    res = subprocess.run(
        [sys.executable, str(detector),
         "--project-root", str(project_root),
         "--since", since, "--json"],
        capture_output=True, text=True, check=False,
    )
    # detector exit 0/1/2 sont tous valides (1=drift, 2=blocking)
    if res.returncode not in (0, 1, 2):
        sys.stderr.write(f"[ERR] detector failed: {res.stderr}\n")
        sys.exit(2)
    return json.loads(res.stdout or "{}")


def _mirror_excerpt(project_root: Path, mirror: str) -> str:
    p = project_root / mirror
    if not p.exists():
        return f"(fichier absent : {mirror})"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(lecture impossible : {exc})"
    return _truncate(text, MAX_MIRROR_LINES)


def render_brief(report: dict, project_root: Path, since: str) -> str:
    drifts = report.get("drifts") or []
    summary = report.get("summary") or {}
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append("# Brief doc-drift — patch suggestion")
    lines.append("")
    lines.append(f"- Generated: {ts}")
    lines.append(f"- Diff base: `{since}`")
    lines.append(f"- Drifts: blocking={summary.get('blocking', 0)} "
                 f"enforcing={summary.get('enforcing', 0)} "
                 f"info={summary.get('info', 0)}")
    lines.append("")

    if not drifts:
        lines.append("Aucun drift detecte. Rien a patcher.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Mission tech-writer")
    lines.append("")
    lines.append("Pour chaque drift ci-dessous :")
    lines.append("")
    lines.append("1. Lire le diff du source.")
    lines.append("2. Identifier ce qui doit etre reflete dans le mirror "
                 "(selon `require` et la nature du changement).")
    lines.append("3. Proposer un patch markdown du mirror "
                 "(diff unifie ou bloc remplacant a appliquer).")
    lines.append("4. Respecter la charte doc "
                 "(`_grimoire-runtime/_memory/tech-writer-sidecar/documentation-standards.md`).")
    lines.append("5. NE PAS toucher au manifeste `doc-manifest.yaml` "
                 "(c'est une PR review separee).")
    lines.append("")

    # Grouper par source
    by_source: dict[str, list[dict]] = {}
    for d in drifts:
        by_source.setdefault(d["source"], []).append(d)

    for idx, (source, items) in enumerate(by_source.items(), start=1):
        sev = items[0].get("severity", "info")
        lines.append(f"## {idx}. `{source}` — severity `{sev}`")
        lines.append("")
        mirrors = ", ".join(f"`{d['mirror']}`" for d in items)
        lines.append(f"- Mirrors a mettre a jour : {mirrors}")
        require = sorted({r for d in items for r in (d.get("require_failures") or [])})
        if require:
            lines.append(f"- Regles `require` non satisfaites : {', '.join(require)}")
        lines.append("")

        diff = _git_diff(project_root, source, since)
        if diff.strip():
            lines.append("<details><summary>Diff source</summary>")
            lines.append("")
            lines.append("```diff")
            lines.append(_truncate(diff, MAX_DIFF_LINES))
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
        else:
            lines.append("_(pas de diff git disponible — fichier explicitement liste)_")
            lines.append("")

        for d in items:
            lines.append(f"### Mirror `{d['mirror']}`")
            lines.append("")
            lines.append(f"- Etat : `mirror_touched={d.get('mirror_touched')}`")
            lines.append(f"- Raison : {d.get('reason', '(n/a)')}")
            lines.append("")
            lines.append("Extrait actuel :")
            lines.append("")
            lines.append("```markdown")
            lines.append(_mirror_excerpt(project_root, d["mirror"]))
            lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Application du patch")
    lines.append("")
    lines.append("Une fois les patches appliques, revalider :")
    lines.append("")
    lines.append("```bash")
    lines.append("cd grimoire-kit && \\")
    lines.append("  .venv/bin/python framework/tools/doc-drift-detector.py "
                 "--project-root . --since HEAD")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 4 — generate doc-drift patch brief for tech-writer.",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--since", default="HEAD")
    parser.add_argument("--report", type=Path, default=None,
                        help="Path to existing JSON report (skip detector run)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Brief output path (default: "
                             "_grimoire-runtime-output/doc-drift/brief-<ts>.md)")
    parser.add_argument("--stdout", action="store_true",
                        help="Print brief to stdout instead of writing to disk")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        sys.stderr.write(f"[ERR] project-root absent: {project_root}\n")
        sys.exit(2)

    report = _load_report(project_root, args.since, args.report)
    brief = render_brief(report, project_root, args.since)

    if args.stdout:
        sys.stdout.write(brief)
        return

    output = args.output
    if output is None:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        # Ecrire dans le repo racine, pas sous grimoire-kit
        repo_root = project_root
        # Si project_root pointe sur grimoire-kit, remonter d'un cran
        if repo_root.name == "grimoire-kit" and (repo_root.parent / ".git").exists():
            repo_root = repo_root.parent
        output = repo_root / "_grimoire-runtime-output" / "doc-drift" / f"brief-{ts}.md"

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(brief, encoding="utf-8")
    sys.stdout.write(f"[OK] Brief ecrit : {output}\n")


if __name__ == "__main__":
    main()
