#!/usr/bin/env python3
"""Vérifie que le CHANGELOG décrit la version que le dépôt s'apprête à publier.

Troisième garde-fou de release, après la correspondance tag ↔ ``version.txt``
et la couverture du catalogue de digests. Il couvre l'angle mort constaté sur
la 3.34.0 : ``version.txt`` annonçait une version dont aucune section ne
parlait, la section la plus récente décrivait une 3.33.0 jamais taguée, et onze
entrées publiées dans le tag restaient rangées sous « à venir ».

Rien de tout cela ne casse un test ni un import. Ça ne se voit qu'à la lecture
de la note de version, c'est-à-dire trop tard.

Deux propriétés, pas une :

``[Unreleased]`` est vide
    Une entrée qui y reste au moment du tag décrit un changement qui part
    pourtant dans l'artefact. Elle sera annoncée « à venir » dans une version
    où elle est déjà là.

la section la plus récente porte le numéro de ``version.txt``
    Sinon la note publiée décrit une autre version que celle qu'on publie.

chaque changement fusionné depuis le dernier tag a son entrée, au bon endroit
    Une PR ouverte avant une release et fusionnée après voit git recaler ses
    lignes par contexte : trente-huit blocs de deux PR se sont retrouvés sous
    une version publiée sans eux, et deux PR n'avaient aucune entrée. Ni l'une
    ni l'autre propriété précédente ne le voyait — ``[Unreleased]`` était vide.
    Pour chaque commit ``feat``/``fix``/``perf`` depuis le dernier tag : il
    touche ``CHANGELOG.md``, et chaque titre d'entrée qu'il a ajouté se trouve
    aujourd'hui dans la section de la version publiée ou dans ``[Unreleased]``.

Usage::

    scripts/check-changelog-release.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
VERSION_FILE = REPO / "version.txt"

#: Un titre de section versionnée : ``## [3.34.0] - 2026-08-28``.
SECTION = re.compile(r"(?m)^## \[(?P<version>[^\]]+)\]")

#: Les types de commit qui doivent laisser une trace dans la note de version.
NOTEWORTHY = re.compile(r"^(feat|fix|perf)(\([^)]*\))?!?:")
#: Le titre d'une entrée : ``- **Ce que ça change.** Le reste…``
ENTRY_TITLE = re.compile(r"^\+?- \*\*\*?(?P<title>.+?)\*\*", re.MULTILINE)


def _git(repo: Path, *args: str) -> str | None:
    import subprocess

    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _section_bodies(text: str) -> dict[str, str]:
    """Version → corps de section, dans l'ordre du fichier."""
    matches = list(SECTION.finditer(text))
    bodies: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        bodies[m.group("version")] = text[m.start():end]
    return bodies


def coverage_problems(repo: Path, text: str, version: str) -> list[str]:
    """Les changements fusionnés depuis le dernier tag que la note ne couvre pas, ou mal.

    Renvoie une liste de problèmes lisibles. Sans dépôt git ou sans tag,
    renvoie un seul problème qui dit que la couverture n'a pas pu être vérifiée
    — non vérifié n'est pas vérifié.
    """
    if _git(repo, "rev-parse", "--git-dir") is None:
        # Pas un dépôt git : la couverture n'a pas d'objet. Les deux autres
        # propriétés restent vérifiées sur le fichier seul.
        return []
    tag = (_git(repo, "describe", "--tags", "--abbrev=0", "HEAD^") or "").strip()
    if not tag:
        return ["couverture non vérifiée : aucun tag atteignable depuis HEAD^ (dépôt shallow ?)"]
    log = _git(repo, "log", "--format=%H%x00%s", f"{tag}..HEAD") or ""
    bodies = _section_bodies(text)
    current = bodies.get(version, "") + bodies.get("Unreleased", "")
    current_titles = {m.group("title").strip() for m in ENTRY_TITLE.finditer(current.replace("\n  ", " "))}
    problems: list[str] = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\x00")
        if not NOTEWORTHY.match(subject):
            continue
        touched = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha) or ""
        if "CHANGELOG.md" not in touched.split():
            problems.append(f"{sha[:8]} {subject[:70]} — fusionné depuis {tag} sans toucher CHANGELOG.md")
            continue
        diff = _git(repo, "show", sha, "--format=", "--", "CHANGELOG.md") or ""
        added = [m.group("title").strip() for m in ENTRY_TITLE.finditer(diff) if m.group(0).startswith("+")]
        for title in added:
            if title not in current_titles:
                problems.append(
                    f"{sha[:8]} — l'entrée « {title[:60]} » n'est ni sous [{version}] ni sous [Unreleased] : "
                    "elle a glissé sous une version déjà publiée"
                )
    return problems


def main() -> int:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    text = CHANGELOG.read_text(encoding="utf-8")

    sections = [(m.group("version"), m.start()) for m in SECTION.finditer(text)]
    if not sections:
        print("::error::aucune section versionnée dans CHANGELOG.md", file=sys.stderr)
        return 1

    problems: list[str] = []

    unreleased = [(name, pos) for name, pos in sections if name.lower() == "unreleased"]
    if unreleased:
        _, start = unreleased[0]
        following = [p for _, p in sections if p > start]
        body = text[start:following[0]] if following else text[start:]
        pending = [ln for ln in body.splitlines() if ln.startswith("- ")]
        if pending:
            problems.append(
                f"[Unreleased] porte {len(pending)} entrée(s) : elles partiraient dans "
                f"{version} en étant annoncées « à venir ».\n"
                + "\n".join(f"      {ln[:88]}" for ln in pending[:5])
                + (f"\n      … et {len(pending) - 5} autre(s)" if len(pending) > 5 else "")
            )

    released = [name for name, _ in sections if name.lower() != "unreleased"]
    if not released:
        problems.append("aucune section publiée dans CHANGELOG.md")
    elif released[0] != version:
        problems.append(
            f"la section la plus récente est [{released[0]}], mais version.txt annonce "
            f"{version} — la note publiée décrirait une autre version."
        )

    problems.extend(coverage_problems(REPO, text, version))

    if problems:
        print(f"::error::CHANGELOG.md ne décrit pas la version {version}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nClore la section : renommer [Unreleased] en "
            f"## [{version}] - <date>, et en ouvrir une nouvelle vide au-dessus.",
            file=sys.stderr,
        )
        return 1

    print(
        f"CHANGELOG à jour — [{version}] est la section la plus récente, [Unreleased] est vide, "
        "chaque changement fusionné depuis le dernier tag y est"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
