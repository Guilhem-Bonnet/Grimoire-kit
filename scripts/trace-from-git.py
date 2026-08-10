#!/usr/bin/env python3
"""Reconstruit un Grimoire_TRACE.md à partir de l'historique git.

``_grimoire-output/Grimoire_TRACE.md`` est alimenté localement par le hook
``post-commit`` et il est gitignoré : un checkout CI ne peut pas l'avoir. Le
benchmark hebdomadaire le lisait quand même et publiait, chaque semaine, un
rapport à zéro entrée conclu par « aucune anomalie détectée » — un vert qui ne
mesurait rien.

Ce script rejoue l'historique dans le format qu'écrit ``post-commit.sh``, ce qui
redonne au benchmark la matière qu'il attend. L'agent est déduit du trailer
``Co-Authored-By`` quand il existe, sinon de l'auteur du commit.

Usage :
    python scripts/trace-from-git.py --out _grimoire-output/Grimoire_TRACE.md
    python scripts/trace-from-git.py --since 2026-01-01 --out -
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = "\x1e"  # record separator — improbable dans un message de commit
FIELD = "\x1f"

BOT_AUTHORS = re.compile(r"bot|actions|dependabot", re.IGNORECASE)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return result.stdout


def collect(since: str | None, branch: str) -> list[dict[str, str]]:
    fmt = FIELD.join(["%H", "%ad", "%an", "%s", "%b"]) + SEP
    args = ["log", f"--pretty=format:{fmt}", "--date=format:%Y-%m-%d %H:%M", "--name-only"]
    if since:
        args.append(f"--since={since}")
    raw = git(*args)

    entries: list[dict[str, str]] = []
    for record in raw.split(SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        head, _, files_blob = record.partition("\n")
        parts = head.split(FIELD)
        if len(parts) < 4:
            continue
        commit_hash, date, author, subject = parts[0], parts[1], parts[2], parts[3]
        body = parts[4] if len(parts) > 4 else ""
        files = [f for f in files_blob.splitlines() if f.strip()]
        entries.append({
            "hash": commit_hash[:8],
            "date": date,
            "agent": resolve_agent(author, body),
            "subject": subject,
            "files": files,
            "branch": branch,
        })
    return entries


def resolve_agent(author: str, body: str) -> str:
    """L'agent est le co-auteur déclaré s'il y en a un, sinon l'auteur humain."""
    match = re.search(r"^Co-Authored-By:\s*([^<]+)", body, re.MULTILINE)
    if match:
        return match.group(1).strip().replace(" ", "-").lower()
    if BOT_AUTHORS.search(author):
        return "bot"
    return author.strip().replace(" ", "-").lower() or "system"


def render(entries: list[dict[str, str]]) -> str:
    out = [
        "# Grimoire_TRACE — Audit Trail",
        "",
        "> Reconstruit depuis l'historique git par `scripts/trace-from-git.py`.",
        "> En local, ce fichier est alimenté en continu par le hook post-commit.",
        "",
    ]
    for e in entries:
        files = ", ".join(e["files"][:20]) or "(aucun)"
        if len(e["files"]) > 20:
            files += f" (+{len(e['files']) - 20})"
        out += [
            f"## {e['date']} | {e['agent']} | git-commit",
            "",
            f"[GIT-COMMIT] hash:{e['hash']} branch:{e['branch']}",
            f"**Message :** {e['subject']}",
            f"**Fichiers :** {files}",
            "",
        ]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="Date de départ (YYYY-MM-DD)")
    parser.add_argument("--branch", default="main", help="Nom de branche à inscrire")
    parser.add_argument("--out", default="-", help="Fichier de sortie, ou - pour stdout")
    parser.add_argument(
        "--min-entries", type=int, default=0,
        help="Échoue si moins d'entrées sont produites — évite de publier un rapport vide",
    )
    args = parser.parse_args()

    entries = collect(args.since, args.branch)
    if len(entries) < args.min_entries:
        print(
            f"trace-from-git: {len(entries)} entrée(s) < minimum {args.min_entries} — "
            "l'historique est-il bien complet (fetch-depth: 0) ?",
            file=sys.stderr,
        )
        return 1

    text = render(entries)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"written: {args.out} ({len(entries)} entrées)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
