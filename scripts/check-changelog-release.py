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

    Repli du 2026-09-08 — le numéro de PR cité dans la section publiée
        Seize PR ont fusionné le même jour sans toucher ``CHANGELOG.md`` à
        leur propre commit. Réécrire l'historique de ``main`` (protégée,
        déjà publique) pour y recoudre l'omission n'était pas une option
        raisonnable pour clore un défaut de convention, pas un incident. La
        garantie de fond n'est pas « chaque commit touche le fichier », mais
        « chaque changement a une entrée au bon endroit » : un commit qui n'a
        pas touché ``CHANGELOG.md`` est donc accepté si son sujet se termine
        par ``(#NNN)`` — la forme que git donne à un squash-merge GitHub — et
        que ``#NNN`` apparaît dans la section de la version publiée par
        ``version.txt``, rédigée à la release pour couvrir justement ce
        rattrapage. La citation ne compte que là : dans ``[Unreleased]`` ou
        une version déjà taguée, elle ne prouve rien sur ce qui part dans ce
        tag-ci. Un commit sans numéro de PR reconnaissable et sans entrée
        reste refusé — le repli couvre un format de sujet connu, pas
        n'importe quelle absence.

Deux profils, car la garde de release refuserait tout travail en cours
(``[Unreleased]`` y est plein par construction) :

``--dev`` (profil de ``grimoire gates repo``, pre-commit, CI de PR)
    La même convention, posée à la PR plutôt qu'au tag : si la branche porte
    un commit ``feat``/``fix``/``perf`` que ``main`` n'a pas, elle ajoute au
    moins une entrée à ``CHANGELOG.md`` (état courant de l'arbre compris, donc
    aussi le contenu indexé ou modifié non encore committé), et chaque entrée
    ajoutée est sous ``[Unreleased]``. Sans base de comparaison atteignable
    (``origin/main`` ou ``main``, ou ``--base``), la garde échoue : une plage
    qu'on ne peut pas lire n'est pas une plage vide.

Usage::

    scripts/check-changelog-release.py                # profil release (tag)
    scripts/check-changelog-release.py --dev          # profil branche/PR
    scripts/check-changelog-release.py --dev --base origin/main
    scripts/check-changelog-release.py --dev --title "fix(x): corrige le calcul"

En ``--dev``, la garde juge le titre de la PR quand il est connu (``--title``,
sinon ``pull_request.title`` de l'événement ``GITHUB_EVENT_PATH`` en CI) : le
squash-merge ne garde que lui, et c'est lui que la garde de release lit. Sans
titre (pre-push local), elle se replie sur les sujets des commits et l'écrit.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
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
#: Le numéro de PR en fin de sujet d'un commit squash-mergé : ``... (#233)``.
PR_NUMBER = re.compile(r"\(#(?P<num>\d+)\)\s*$")


def _pr_cited(subject: str, version_body: str) -> bool:
    """Le sujet cite-t-il un numéro de PR présent dans CETTE section-là ?

    ``version_body`` doit être le corps de la seule section publiée par
    ``version.txt`` — jamais ``[Unreleased]`` ni une version déjà taguée,
    sous peine de revalider n'importe quel commit sur la foi d'une mention
    qui ne dit rien de ce qui part dans ce tag.
    """
    match = PR_NUMBER.search(subject)
    if not match:
        return False
    return f"#{match.group('num')}" in version_body


def _sha_cited(sha: str, version_body: str) -> bool:
    """Le corps de la section publiée cite-t-il le commit par ses huit premiers caractères ?

    Pour un commit poussé sur ``main`` sans PR (donc sans ``(#NNN)`` au sujet)
    et sans toucher ``CHANGELOG.md`` : même rattrapage que le numéro de PR,
    même portée (la seule section publiée par ``version.txt``), mais la
    preuve est le hash cité, explicite et vérifiable, jamais une absence.
    """
    return len(sha) >= 8 and sha[:8] in version_body


def _git(repo: Path, *args: str) -> str | None:
    import subprocess

    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60)
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
            if _pr_cited(subject, bodies.get(version, "")) or _sha_cited(sha, bodies.get(version, "")):
                continue
            match = PR_NUMBER.search(subject)
            detail = f", et #{match.group('num')} n'apparaît pas dans [{version}]" if match else ""
            problems.append(
                f"{sha[:8]} {subject[:70]} — fusionné depuis {tag} sans toucher CHANGELOG.md{detail}"
            )
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


def _resolve_base(repo: Path, requested: str | None) -> str | None:
    """La référence de comparaison : celle demandée, sinon ``origin/main`` puis ``main``."""
    for ref in ([requested] if requested else ["origin/main", "main"]):
        if ref and _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is not None:
            return ref
    return None


def pr_title_from_event(environ: Mapping[str, str] | None = None) -> str | None:
    """Le titre de la PR courante, lu dans l'événement GitHub, ou ``None`` s'il n'y en a pas.

    Le titre vient du fichier ``GITHUB_EVENT_PATH`` (``pull_request.title``) : il
    n'est jamais interpolé dans un shell. Fichier absent, illisible, événement
    sans ``pull_request`` (un ``push``) ou titre vide : ``None``, et l'appelant
    se replie sur les sujets de commits en le disant.
    """
    env = os.environ if environ is None else environ
    path = env.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pull_request = event.get("pull_request") if isinstance(event, dict) else None
    title = pull_request.get("title") if isinstance(pull_request, dict) else None
    return title.strip() if isinstance(title, str) and title.strip() else None


def dev_problems(repo: Path, requested_base: str | None = None, title: str | None = None) -> list[str]:
    """La convention « chaque PR feat/fix/perf ajoute son entrée », vérifiée avant le tag.

    Le squash-merge ne garde que le titre de la PR : c'est lui que la garde de
    release jugera, donc c'est lui qu'on juge quand ``title`` est fourni. Sans
    titre (pre-push local), on se replie sur les sujets des commits de la branche.
    """
    if _git(repo, "rev-parse", "--git-dir") is None:
        return ["profil --dev non vérifié : pas un dépôt git"]
    changelog = repo / "CHANGELOG.md"
    if not changelog.is_file():
        return ["CHANGELOG.md absent"]
    text = changelog.read_text(encoding="utf-8")
    unreleased = _section_bodies(text).get("Unreleased")
    if unreleased is None:
        return ["CHANGELOG.md n'a pas de section [Unreleased] : aucune entrée de PR n'a où atterrir"]
    base = _resolve_base(repo, requested_base)
    if base is None:
        return [
            "aucune base de comparaison (origin/main, main, ou --base) : la plage de la branche "
            "n'a pas pu être lue — clone shallow ou sans remote ? (non vérifié n'est pas vérifié)"
        ]
    merge_base = (_git(repo, "merge-base", base, "HEAD") or "").strip()
    if not merge_base:
        return [f"pas d'ancêtre commun entre {base} et HEAD : plage illisible"]
    noteworthy: list[str] = []
    if title is not None:
        if NOTEWORTHY.match(title.strip()):
            noteworthy.append(f"titre de la PR « {title.strip()[:70]} »")
    else:
        log = _git(repo, "log", "--no-merges", "--format=%H%x00%s", f"{merge_base}..HEAD")
        if log is None:
            return [f"git log {merge_base[:8]}..HEAD a échoué : plage illisible"]
        for line in log.splitlines():
            sha, _, subject = line.partition("\x00")
            if NOTEWORTHY.match(subject):
                noteworthy.append(f"{sha[:8]} {subject[:70]}")
    if not noteworthy:
        return []
    # Contre l'arbre de travail, pas contre HEAD : au pre-commit l'entrée est
    # encore dans l'index, et un garde qui ne la verrait qu'après coup serait
    # rouge pour la mauvaise raison.
    diff = _git(repo, "diff", merge_base, "--", "CHANGELOG.md")
    if diff is None:
        return ["git diff CHANGELOG.md a échoué : entrées ajoutées illisibles"]
    added = [line[3:].strip() for line in diff.splitlines() if line.startswith("+- ")]
    if not added:
        return [
            f"{len(noteworthy)} changement(s) feat/fix/perf depuis {base} sans nouvelle entrée dans CHANGELOG.md "
            "(sous [Unreleased]) : " + " ; ".join(noteworthy[:3])
        ]
    # Une entrée est une puce de premier niveau, quel que soit son style
    # (`- **Titre.** …` des versions publiées, `- fix(scope): …` de [Unreleased]).
    current = {line[2:].strip() for line in unreleased.splitlines() if line.startswith("- ")}
    return [
        f"l'entrée « {entry[:60]} » ajoutée par la branche n'est pas sous [Unreleased]"
        for entry in added
        if entry not in current
    ]


def _dev_main(base: str | None, title: str | None) -> int:
    if title is None:
        title = pr_title_from_event()
        source = "titre de la PR (événement GitHub)" if title else None
    else:
        title = title.strip() or None
        source = "titre de la PR (--title)" if title else None
    if source is None:
        print("jugé sur les sujets des commits de la branche (aucun titre de PR disponible)")
    else:
        print(f"jugé sur le {source} : « {(title or '')[:70]} »")
    problems = dev_problems(REPO, base, title)
    if problems:
        print("::error::CHANGELOG.md ne couvre pas les changements de cette branche", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("CHANGELOG couvre la branche — chaque changement feat/fix/perf a son entrée sous [Unreleased]")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--dev" in args:
        base: str | None = None
        if "--base" in args:
            position = args.index("--base") + 1
            if position >= len(args):
                print("--base demande une référence", file=sys.stderr)
                return 2
            base = args[position]
        title: str | None = None
        if "--title" in args:
            position = args.index("--title") + 1
            if position >= len(args):
                print("--title demande un titre", file=sys.stderr)
                return 2
            title = args[position]
        return _dev_main(base, title)
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
