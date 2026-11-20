#!/usr/bin/env python3
"""Prépare une release en une commande : branche, version, CHANGELOG, catalogue, roue vérifiée, commit.

Enchaîne la procédure jusque-là faite à la main (PR « chore: release » #1267, #1269) :

1. ``main`` propre (aucune modification suivie) et à jour d'``origin/main`` ;
2. branche ``release/X.Y.Z`` ;
3. numéro de PR ``(#NNNN)`` ajouté à chaque puce de fragment qui n'en a pas — le
   numéro du commit qui a ajouté le fragment. Sans lui, ``check-changelog-release.py``
   refuse un commit ``feat``/``fix``/``perf`` qui n'a touché que ``changelog.d/`` ;
4. ``version.txt``, ``scripts/changelog-assemble.py X.Y.Z``, ``__version__`` ;
5. ``scripts/gen-kit-hashes.py`` puis ``scripts/check-changelog-release.py`` ;
6. ``evals/ceremony-budget.yaml`` : signale une calibration à plus de deux versions
   mineures (la CI échouerait) ; ``--redate-ceremony`` la redate, et l'étape suivante
   rejoue le corpus sur la roue ;
7. roue construite dans un dossier temporaire (``uv build --wheel``), installée dans
   un venv vierge, puis ``grimoire --version`` et ``grimoire ceremony budget`` ;
8. commit ``chore: release X.Y.Z`` ; ``--open-pr`` pousse la branche et ouvre la PR.

Ne fusionne pas, ne tague pas : imprime les commandes de suite. ``--dry-run``
vérifie l'état et annonce chaque étape sans rien écrire.

Usage : ``scripts/release.py 3.93.0 [--dry-run] [--open-pr] [--redate-ceremony]``
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(?:rc|a|b)\d+)?")  # toujours en fullmatch
PR_SUFFIX = re.compile(r"\(#(\d+)\)\s*$")
MAX_CALIBRATION_MINOR_LAG = 2  # grimoire.missions.ceremony_budget.MAX_CALIBRATION_MINOR_LAG
CEREMONY_FILE = "evals/ceremony-budget.yaml"
VERSION_PY = "src/grimoire/__version__.py"
HASHES = "registry/kit-file-hashes.json"


# Étapes franchies, du plus tôt au plus tard : le message d'échec dépend de la dernière.
STAGE_CHECKED, STAGE_BRANCH, STAGE_COMMITTED, STAGE_PUSHED = "vérifié", "branche", "commité", "poussé"


class ReleaseError(RuntimeError):
    """Une précondition ou une étape a échoué."""


def run(cmd: Sequence[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Chemins littéraux : un nom de fichier ne devient jamais un motif ou une « magic pathspec ».
    # Les sous-processus Python écrivent en UTF-8 : sous Windows, la page de code locale (cp1252) donnerait des
    # accents illisibles une fois décodés ci-dessous.
    env = {**os.environ, "GIT_LITERAL_PATHSPECS": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    done = subprocess.run(
        list(cmd), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, env=env
    )
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise ReleaseError(f"échec de `{' '.join(cmd)}` (code {done.returncode})\n{detail}")
    return done


def git(repo: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], repo, check=check).stdout.strip()


def say(message: str) -> None:
    print(message, flush=True)


# ── Préconditions ────────────────────────────────────────────────────────────


def parse_version(text: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(text)
    if not match:
        raise ReleaseError(f"version invalide : {text!r} (attendu X.Y.Z ou X.Y.Z-rcN, sans « v »)")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def version_key(text: str) -> tuple[int, int, int, int, str]:
    """Ordre SemVer réduit : une pré-version précède la version stable du même numéro."""
    core = parse_version(text)
    pre = text.partition("-")[2]
    return (*core, 0 if pre else 1, pre)


def check_version_moves_forward(repo: Path, version: str) -> str:
    current = (repo / "version.txt").read_text(encoding="utf-8").strip()
    if version_key(version) <= version_key(current):
        raise ReleaseError(f"{version} n'avance pas la version courante {current}")
    return current


def check_clean_main(repo: Path, remote: str, base: str, *, fetch: bool = True) -> None:
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != base:
        raise ReleaseError(f"la release part de {base}, la branche courante est {branch}")
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ReleaseError(f"{base} porte des modifications suivies non commitées :\n{dirty}")
    if fetch:
        git(repo, "fetch", "--quiet", remote, base)
    local, upstream = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", f"{remote}/{base}")
    if local != upstream:
        raise ReleaseError(
            f"{base} ({local[:9]}) diffère de {remote}/{base} ({upstream[:9]}) : `git pull --ff-only` d'abord"
        )


def check_fragments_tracked(repo: Path) -> None:
    """Un fragment non suivi serait assemblé puis supprimé sans pouvoir être stagé : le brouillon serait perdu."""
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "--", "changelog.d")
    if untracked:
        raise ReleaseError(f"changelog.d/ porte des fichiers non suivis (commiter ou déplacer d'abord) :\n{untracked}")
    root = repo.resolve()
    for path in fragments(repo):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ReleaseError(
                f"changelog.d/{path.name} est un lien symbolique : changelog-assemble.py lirait sa cible et le "
                "supprimerait ; le remplacer par un fichier"
            )


def check_ref_names(remote: str, base: str) -> None:
    for label, value in (("--remote", remote), ("--base", base)):
        if not value or value.startswith("-") or any(c.isspace() for c in value):
            raise ReleaseError(f"{label} invalide : {value!r}")


def check_branch_absent(repo: Path, branch: str, remote: str) -> None:
    for ref in (f"refs/heads/{branch}", f"refs/remotes/{remote}/{branch}"):
        if run(["git", "show-ref", "--verify", "--quiet", ref], repo, check=False).returncode == 0:
            raise ReleaseError(f"la branche {branch} existe déjà ({ref})")


# ── Fragments : numéro de PR ─────────────────────────────────────────────────


def fragments(repo: Path) -> list[Path]:
    """Même définition que ``scripts/changelog-assemble.py``."""
    directory = repo / "changelog.d"
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.glob("*.md")
        if (p.is_file() or p.is_symlink()) and p.name.lower() != "readme.md" and not p.name.startswith(".")
    )


def fragment_pr(repo: Path, path: Path) -> str | None:
    """Le numéro ``(#NNNN)`` du commit qui a ajouté le fragment (sujet d'un squash-merge GitHub)."""
    rel = path.relative_to(repo).as_posix()
    subject = git(repo, "log", "--diff-filter=A", "--format=%s", "-1", "--", rel, check=False)
    match = PR_SUFFIX.search(subject)
    return match.group(1) if match else None


def with_pr_number(body: str, number: str) -> str:
    """Ajoute `` (#N)`` à la dernière ligne de chaque puce qui n'en porte pas."""
    lines = body.rstrip("\n").split("\n")
    ends: list[int] = []
    for index, line in enumerate(lines):
        if line.startswith("- "):
            ends.append(index)
        elif ends and line.strip() and line.startswith((" ", "\t")):
            ends[-1] = index
    for index in ends:
        if not PR_SUFFIX.search(lines[index]):
            lines[index] = f"{lines[index].rstrip()} (#{number})"
    return "\n".join(lines) + "\n"


def number_fragments(repo: Path, *, write: bool) -> list[str]:
    """Numérote les fragments ; renvoie les fragments restés sans numéro."""
    missing = []
    for path in fragments(repo):
        number = fragment_pr(repo, path)
        if number is None:
            missing.append(path.name)
            say(f"  ! changelog.d/{path.name} : aucun (#NNNN) trouvé dans le commit qui l'a ajouté")
            continue
        body = path.read_text(encoding="utf-8")
        updated = with_pr_number(body, number)
        if updated != body:
            say(f"  changelog.d/{path.name} : (#{number})")
            if write:
                path.write_text(updated, encoding="utf-8")
    return missing


# ── Budget de cérémonie ──────────────────────────────────────────────────────

_CAL_VERSION = re.compile(r'(?m)^(\s+kit_version:\s*")(\d+)\.(\d+)(")')
_CAL_DATE = re.compile(r'(?m)^(\s+as_of:\s*")([0-9-]+)(")')


def ceremony_lag(repo: Path, version: str) -> int | None:
    """Écart en versions mineures entre la calibration et ``version`` (``None`` si illisible)."""
    path = repo / CEREMONY_FILE
    if not path.is_file():
        return None
    match = _CAL_VERSION.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    major, minor, _ = parse_version(version)
    if int(match.group(2)) != major:
        return MAX_CALIBRATION_MINOR_LAG + 1
    return minor - int(match.group(3))


def redate_ceremony(repo: Path, version: str, today: str) -> None:
    path = repo / CEREMONY_FILE
    major, minor, _ = parse_version(version)
    text = _CAL_VERSION.sub(
        lambda m: f"{m.group(1)}{major}.{minor}{m.group(4)}", path.read_text(encoding="utf-8"), count=1
    )
    text = _CAL_DATE.sub(lambda m: f"{m.group(1)}{today}{m.group(3)}", text, count=1)
    path.write_text(text, encoding="utf-8")


# ── Roue vérifiée dans un venv vierge ────────────────────────────────────────


def wheel_check(repo: Path, version: str) -> None:
    """``uv build --wheel`` hors de l'arbre, venv neuf, ``grimoire --version`` et ``ceremony budget``."""
    if shutil.which("uv") is None:
        raise ReleaseError("uv introuvable : il construit la roue et le venv de vérification")
    with tempfile.TemporaryDirectory(prefix="grimoire-release-") as tmp:
        work = Path(tmp)
        run(["uv", "build", "--wheel", "--out-dir", str(work / "dist")], repo)
        wheels = sorted((work / "dist").glob("*.whl"))
        if len(wheels) != 1:
            raise ReleaseError(f"une roue attendue dans {work / 'dist'}, trouvé {len(wheels)}")
        venv = work / "venv"
        run(["uv", "venv", "--quiet", str(venv)], repo)
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        run(["uv", "pip", "install", "--quiet", "--python", str(python), str(wheels[0])], repo)
        grimoire = str(bindir / "grimoire")
        shown = run([grimoire, "--version"], work).stdout
        if version not in shown:
            raise ReleaseError(f"la roue annonce {shown.strip()!r}, pas {version}")
        say(f"  roue {wheels[0].name} : `grimoire --version` -> {shown.strip()}")
        run([grimoire, "ceremony", "budget", "--file", str(repo / CEREMONY_FILE)], repo)
        say("  `grimoire ceremony budget` : budget tenu sur la roue")


# ── Orchestration ────────────────────────────────────────────────────────────


def script(repo: Path, name: str, *args: str) -> list[str]:
    return [sys.executable, str(repo / "scripts" / name), *args]


def release(
    repo: Path,
    version: str,
    *,
    dry_run: bool = False,
    open_pr: bool = False,
    redate: bool = False,
    remote: str = "origin",
    base: str = "main",
    fetch: bool = True,
    today: str | None = None,
    check_wheel: Callable[[Path, str], None] = wheel_check,
    progress: list[str] | None = None,
) -> int:
    progress = progress if progress is not None else []
    today = today or datetime.date.today().isoformat()
    branch = f"release/{version}"
    prefix = "[dry-run] " if dry_run else ""

    parse_version(version)
    check_ref_names(remote, base)
    current = check_version_moves_forward(repo, version)
    check_clean_main(repo, remote, base, fetch=fetch)
    check_fragments_tracked(repo)
    check_branch_absent(repo, branch, remote)
    progress.append(STAGE_CHECKED)
    say(f"{prefix}{base} propre et à jour ; {current} -> {version}")

    lag = ceremony_lag(repo, version)
    stale = lag is not None and lag > MAX_CALIBRATION_MINOR_LAG
    if stale and not redate:
        raise ReleaseError(
            f"{CEREMONY_FILE} : calibration à {lag} versions mineures de {version} (au plus "
            f"{MAX_CALIBRATION_MINOR_LAG}) — la CI échouerait. Relancer avec --redate-ceremony : "
            "le corpus est rejoué sur la roue avant le commit."
        )
    if lag is not None and not stale:
        say(f"  {CEREMONY_FILE} : calibration à {lag} version(s) mineure(s), rien à redater")

    if dry_run:
        say(f"[dry-run] git switch -c {branch}")
        say("[dry-run] numéros de PR des fragments :")
        number_fragments(repo, write=False)
        say(f"[dry-run] version.txt et {VERSION_PY} -> {version}")
        say(
            run(
                script(repo, "changelog-assemble.py", version, "--repo", str(repo), "--date", today, "--dry-run"), repo
            ).stdout.strip()
        )
        if stale:
            say(f"[dry-run] {CEREMONY_FILE} redaté : kit_version {version.rsplit('.', 1)[0]}, as_of {today}")
        say("[dry-run] gen-kit-hashes.py, check-changelog-release.py, roue + venv vierge, commit")
        say("[dry-run] rien n'a été écrit")
        return 0

    git(repo, "switch", "--quiet", "-c", branch)
    progress.append(STAGE_BRANCH)
    say(f"branche {branch}")
    say("numéros de PR des fragments :")
    consumed = [p.relative_to(repo).as_posix() for p in fragments(repo)]
    missing = number_fragments(repo, write=True)
    if missing:
        say("  (check-changelog-release.py dira si ces entrées manquent à la section)")

    (repo / "version.txt").write_text(version + "\n", encoding="utf-8")
    say(run(script(repo, "changelog-assemble.py", version, "--repo", str(repo), "--date", today), repo).stdout.strip())
    version_py = repo / VERSION_PY
    version_py.write_text(
        re.sub(r"(?m)^__version__\s*=.*$", f'__version__ = "{version}"', version_py.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    if stale:
        redate_ceremony(repo, version, today)
        say(f"{CEREMONY_FILE} redaté ({version.rsplit('.', 1)[0]}, {today})")

    run(script(repo, "gen-kit-hashes.py"), repo)
    say("catalogue de digests régénéré")
    run(script(repo, "check-changelog-release.py"), repo)
    say("check-changelog-release.py : CHANGELOG conforme")

    untracked = git(repo, "ls-files", "--others", "--exclude-standard")
    if untracked:
        say(
            f"  ! la roue se construit depuis l'arbre de travail : {len(untracked.splitlines())} fichier(s) non "
            "suivi(s) peuvent y entrer sans être dans le commit"
        )
    check_wheel(repo, version)

    paths = ["CHANGELOG.md", "version.txt", VERSION_PY, HASHES, *consumed]
    if stale:
        paths.append(CEREMONY_FILE)
    present = [p for p in paths if (repo / p).exists() or p in consumed]
    git(repo, "add", "--", *present)
    git(repo, "commit", "--quiet", "-m", f"chore: release {version}")
    progress.append(STAGE_COMMITTED)
    say(f"commit « chore: release {version} » sur {branch}")

    pr_ref = "<numéro>"
    if open_pr:
        git(repo, "push", "--quiet", "-u", remote, branch)
        progress.append(STAGE_PUSHED)
        url = run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base,
                "--head",
                branch,
                "--title",
                f"chore: release {version}",
                "--body",
                pr_body(repo, version),
            ],
            repo,
        ).stdout.strip()
        say(f"PR ouverte : {url}")
        pr_ref = url.rsplit("/", 1)[-1] or pr_ref

    say("\nSuite (non exécutée par ce script) :")
    if not open_pr:
        say(f"  git push -u {remote} {branch}")
        say(f'  gh pr create --base {base} --head {branch} --title "chore: release {version}"')
    say(f"  gh pr merge {pr_ref} --squash        # après la CI verte")
    say(f"  git switch {base} && git pull --ff-only {remote} {base}")
    say(
        f'  git tag -a v{version} -m "Release {version}" && git push {remote} v{version}   # publish.yml prend le relais'
    )
    return 0


def pr_body(repo: Path, version: str) -> str:
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.find(f"## [{version}]")
    end = text.find("\n## [", start + 1)
    section = text[start : end if end != -1 else len(text)].split("\n", 1)[-1].strip() if start != -1 else ""
    return (
        f"Release {version}, préparée par `scripts/release.py`.\n\n## Entrées\n\n{section}\n\n"
        "## Vérifications faites avant d'ouvrir\n\n"
        "- `scripts/changelog-assemble.py` puis `scripts/check-changelog-release.py` : vert.\n"
        "- `scripts/gen-kit-hashes.py` : catalogue régénéré.\n"
        "- Roue construite et installée dans un environnement vierge : `grimoire --version` et "
        "`grimoire ceremony budget` verts.\n\n"
        f"## Après fusion\n\nTag annoté `v{version}` sur `main`.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="X.Y.Z (ou X.Y.Z-rcN), sans le préfixe v")
    parser.add_argument("--dry-run", action="store_true", help="vérifier et annoncer, sans rien écrire")
    parser.add_argument("--open-pr", action="store_true", help="pousser la branche et ouvrir la PR (gh)")
    parser.add_argument("--redate-ceremony", action="store_true", help="redater la calibration du budget de cérémonie")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base", default="main")
    parser.add_argument("--no-fetch", action="store_true", help="ne pas rafraîchir la référence distante")
    args = parser.parse_args(argv)
    version = args.version.removeprefix("v")
    progress: list[str] = []
    try:
        return release(
            args.repo.resolve(),
            version,
            dry_run=args.dry_run,
            open_pr=args.open_pr,
            redate=args.redate_ceremony,
            remote=args.remote,
            base=args.base,
            fetch=not args.no_fetch,
            progress=progress,
        )
    except ReleaseError as exc:
        print(f"::error::{exc}", file=sys.stderr)
    except Exception as exc:
        print(f"::error::erreur inattendue : {type(exc).__name__}: {exc}", file=sys.stderr)
    print(recovery_hint(progress[-1] if progress else None, version, args.base, args.remote), file=sys.stderr)
    return 1


def recovery_hint(stage: str | None, version: str, base: str, remote: str) -> str:
    """Ce qu'il reste à faire selon la dernière étape franchie."""
    branch = f"release/{version}"
    if stage in (None, STAGE_CHECKED):
        return "Rien n'a été écrit."
    if stage == STAGE_BRANCH:
        return (
            f"Rien n'est commité, la branche {branch} porte des modifications. Pour repartir : "
            f"git restore --staged --worktree . && git switch {base} && git branch -D {branch}"
        )
    if stage == STAGE_COMMITTED:
        return (
            f"Le commit « chore: release {version} » existe sur {branch}, rien n'est poussé. Reprendre à la main "
            f"(git push -u {remote} {branch}, puis gh pr create) ou abandonner : "
            f"git switch {base} && git branch -D {branch}"
        )
    return (
        f"{branch} est commitée et poussée sur {remote} ; seule l'ouverture de la PR a échoué. "
        f'La reprendre : gh pr create --base {base} --head {branch} --title "chore: release {version}". '
        "Ne pas supprimer la branche distante sans vérifier qu'aucune PR n'existe."
    )


if __name__ == "__main__":
    raise SystemExit(main())
