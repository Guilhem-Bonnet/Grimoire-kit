#!/usr/bin/env python3
"""Generate ``registry/kit-file-hashes.json`` — the catalog of files the kit
has ever shipped.

``grimoire migrate`` uses it to answer the one question that decides whether a
project keeps a file or lets it be regenerated: *did the user write this, or
did some past version of the kit?* A digest present in the catalog was shipped
by the kit and is safe to replace; anything else is the project's own work and
becomes an override.

Keying by digest rather than by path is deliberate — the same content moved
between layouts across versions, and a path-keyed catalog would call every
relocated file "customised".

Usage::

    scripts/gen-kit-hashes.py                  # current worktree only
    scripts/gen-kit-hashes.py --history        # every released tag, too
    scripts/gen-kit-hashes.py --since v3.20.0  # tags from this one on
    scripts/gen-kit-hashes.py --check          # verify, write nothing

``--check`` is the release gate. Tagging without regenerating the catalog is
silent and expensive: every file the version introduces has an unknown digest,
so ``grimoire migrate`` reads it as a user customisation and freezes it out of
all future updates. The damage lands on projects, months later, and looks like
"the kit stopped updating". The check is one scan — it belongs in front of the
release, not in the incident report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "registry" / "kit-file-hashes.json"

#: Trees whose files land in a project's kit tier.
SHIPPED_ROOTS = ("archetypes", "framework")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _released_tags(since: str | None) -> list[str]:
    """Version tags in release order, optionally starting at *since*."""
    tags = [t for t in _git("tag", "--sort=v:refname").split() if t.startswith("v")]
    if since and since in tags:
        tags = tags[tags.index(since):]
    return tags


def _scan_worktree() -> dict[str, str]:
    """``digest -> path`` for every shipped file in the current worktree."""
    found: dict[str, str] = {}
    for root in SHIPPED_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                found[_digest(p.read_bytes())] = str(p.relative_to(REPO))
    return found


def _scan_tag(tag: str) -> dict[str, str]:
    """``digest -> path`` for a released tag, read straight from git objects."""
    found: dict[str, str] = {}
    try:
        listing = _git("ls-tree", "-r", "--name-only", tag, *SHIPPED_ROOTS)
    except subprocess.CalledProcessError:
        return found
    for rel in listing.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", str(REPO), "show", f"{tag}:{rel}"],
                capture_output=True, check=True,
            ).stdout
        except subprocess.CalledProcessError:
            continue
        found[_digest(blob)] = rel
    return found


def _check(catalog: dict[str, dict[str, str]], version: str) -> int:
    """Fail when a shipped file is absent from the catalog.

    The property is exactly what ``migrate`` relies on: a digest it does not
    recognise is treated as the project's own work. One shipped file missing
    here is one file that no update will ever touch again.
    """
    missing = sorted(
        rel for digest, rel in _scan_worktree().items() if digest not in catalog
    )
    if not missing:
        print(f"catalogue à jour — {len(catalog)} digests couvrent l'arbre livré")
        return 0
    print(
        f"{len(missing)} fichier(s) livré(s) absent(s) du catalogue "
        f"(version.txt = {version}) :",
        file=sys.stderr,
    )
    for rel in missing[:20]:
        print(f"  - {rel}", file=sys.stderr)
    if len(missing) > 20:
        print(f"  … et {len(missing) - 20} autre(s)", file=sys.stderr)
    print(
        "\nCes fichiers seraient lus comme des customisations utilisateur par\n"
        "`grimoire migrate`, et gelés hors des mises à jour. Lancer :\n"
        "  python scripts/gen-kit-hashes.py\n"
        "puis committer registry/kit-file-hashes.json.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true",
                    help="also scan released tags (only needed to backfill a fresh catalog)")
    ap.add_argument("--since", default="", help="oldest tag to scan with --history")
    ap.add_argument("--check", action="store_true",
                    help="verify the catalog covers the worktree; write nothing")
    args = ap.parse_args()

    version = (REPO / "version.txt").read_text(encoding="utf-8").strip()

    # Start from what is already recorded: the catalog is cumulative, and a
    # release only ever adds the digests it introduces.
    catalog: dict[str, dict[str, str]] = {}
    try:
        existing = json.loads(OUT.read_text(encoding="utf-8")).get("digests")
        if isinstance(existing, dict):
            catalog.update(existing)
    except (OSError, json.JSONDecodeError):
        # Pas encore de catalogue, ou illisible : on repart d'un vide.
        pass

    if args.check:
        return _check(catalog, version)

    if args.history:
        for tag in _released_tags(args.since or None):
            for digest, rel in _scan_tag(tag).items():
                # First tag that shipped a digest is the one worth recording.
                catalog.setdefault(digest, {"version": tag.lstrip("v"), "path": rel})
            print(f"  {tag}: {len(catalog)} digests so far", file=sys.stderr)

    for digest, rel in _scan_worktree().items():
        catalog.setdefault(digest, {"version": version, "path": rel})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "schema": "grimoire-kit-file-hashes/v1",
                "generated_for": version,
                "digests": dict(sorted(catalog.items())),
            },
            indent=1,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"{len(catalog)} digests → {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
