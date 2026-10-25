"""Lectures de l'intendant : git, ``gh`` et le Mission Ledger — injectables, en lecture seule.

Tout processus externe passe par un seul exécuteur :data:`grimoire.missions.launcher.Exec`
(``(argv, cwd, env) → ExecResult``), comme le lanceur et le janitor : les tests y branchent des
doubles qui enregistrent chaque appel, et prouvent qu'aucun n'écrit.

Ce que ces lectures ne peuvent pas mesurer (``gh`` absent, hors ligne, git trop ancien, référence
introuvable) est rendu comme :data:`NON_MESURE`, jamais deviné ni compté comme un « non ».
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.missions.launcher import Exec, ExecResult, default_exec
from grimoire.missions.lease import parse_instant
from grimoire.steward import OUTPUT_DIR

__all__ = [
    "CHANGELOG",
    "NON_MESURE",
    "BranchRef",
    "MergeProbe",
    "Probe",
    "Sources",
    "WorktreeEntry",
    "ahead_behind",
    "commit_on_target",
    "ensure_output_dir",
    "file_on_target",
    "gh_json",
    "hours_since",
    "last_commit_at",
    "list_branches",
    "list_worktrees",
    "merge_probe",
    "resolve_commit",
    "scratch_objects",
    "target_log",
]

NON_MESURE = "non mesuré"
#: Un conflit limité à ce fichier se résout mécaniquement (les entrées s'empilent sous [Unreleased]).
CHANGELOG = "CHANGELOG.md"

#: Sonde d'hôte : ``{session_id: "busy" | "idle"}``, ou ``None`` si indisponible (cf. ``missions.liveness``).
Probe = Callable[[], Mapping[str, str] | None]

_HEX = re.compile(r"[0-9a-f]{7,64}")
_SCRATCH = ".objets-git"


@dataclass
class Sources:
    """Où lire et comment : racine du projet, exécuteur, sonde d'hôte, horloge et cible."""

    root: Path
    exec_: Exec = default_exec
    probe: Probe | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    target: str = "origin/main"
    #: ``OWNER/REPO`` passé à ``gh -R`` ; vide = celui que ``gh`` déduit du remote.
    repo: str = ""

    def git(self, *args: str, env: Mapping[str, str] | None = None) -> ExecResult:
        return self.exec_(["git", "-C", str(self.root), *args], self.root, env)

    def gh(self, *args: str) -> ExecResult:
        argv = ["gh", *args]
        if self.repo:
            argv += ["-R", self.repo]
        return self.exec_(argv, self.root, None)


def ensure_output_dir(root: Path) -> Path:
    """Le dossier ``_grimoire-output/steward/``, avec un ``.gitignore`` qui l'ignore tout entier.

    Les rapports sont des instantanés : les laisser hors de git garde ``git status`` (et l'empreinte
    d'arbre du gate ``Stop``) inchangé par une commande en lecture seule, sans toucher au
    ``.gitignore`` du projet.
    """
    folder = root / OUTPUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    ignore = folder / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("# Instantanés de `grimoire steward` : jamais versionnés.\n*\n", encoding="utf-8")
    return folder


def gh_json(src: Sources, *args: str) -> tuple[Any, str]:
    """``(données, "")`` d'un ``gh … --json``, ou ``(None, raison)`` quand ``gh`` échoue ou rend autre chose."""
    done = src.gh(*args)
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()
        return None, f"gh : {reason[0] if reason else f'code {done.returncode}'}"
    try:
        return json.loads(done.stdout), ""
    except ValueError:
        return None, "gh : sortie JSON illisible"


def hours_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return round(max((now - moment).total_seconds(), 0.0) / 3600, 1)


# ── git : commits et fichiers de la cible ─────────────────────────────────────


def resolve_commit(src: Sources, rev: str) -> str | None:
    """Le SHA complet du commit *rev*, ou ``None`` s'il n'existe pas ici."""
    if not rev or rev.startswith("-"):
        return None
    done = src.git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    sha = done.stdout.strip()
    return sha if done.returncode == 0 and _HEX.fullmatch(sha) else None


def commit_on_target(src: Sources, target_sha: str, sha: str) -> tuple[bool, str, str]:
    """``(vrai, sha complet, "")`` si *sha* est un commit de la cible ; sinon ``(faux, …, raison)``."""
    if not _HEX.fullmatch(sha.lower()):
        return False, "", f"« {sha} » n'est pas un SHA hexadécimal (7 à 64 caractères)"
    full = resolve_commit(src, sha.lower())
    if full is None:
        return False, "", f"commit {sha} absent du dépôt local"
    done = src.git("merge-base", "--is-ancestor", full, target_sha)
    if done.returncode == 0:
        return True, full, ""
    if done.returncode == 1:
        return False, full, f"commit {full[:12]} présent mais pas sur la cible"
    return False, full, f"git merge-base : code {done.returncode}"


def file_on_target(src: Sources, target_sha: str, path: str, pattern: str = "") -> tuple[bool | None, str]:
    """``(présent, détail)`` : le chemin (et le motif s'il est donné) est-il sur la cible ? ``None`` = non mesuré."""
    exists = src.git("cat-file", "-e", f"{target_sha}:{path}")
    if exists.returncode != 0:
        return False, f"{path} absent de la cible"
    if not pattern:
        return True, f"{path} présent sur la cible"
    done = src.git("grep", "-q", "-F", "-e", pattern, target_sha, "--", path)
    if done.returncode == 0:
        return True, f"motif trouvé dans {path} sur la cible"
    if done.returncode == 1:
        return False, f"motif absent de {path} sur la cible"
    return None, f"git grep : code {done.returncode}"


def target_log(src: Sources, target_sha: str, *, limit: int = 5000) -> list[tuple[str, str, str]]:
    """``(sha, sujet, corps)`` des *limit* derniers commits de la cible ; ``[]`` si git échoue."""
    done = src.git("log", f"--max-count={limit}", "--format=%H%x1f%s%x1f%b%x1e", target_sha)
    if done.returncode != 0:
        return []
    out: list[tuple[str, str, str]] = []
    for record in done.stdout.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 3 and parts[0]:
            out.append((parts[0], parts[1], parts[2]))
    return out


def last_commit_at(src: Sources, rev: str) -> datetime | None:
    done = src.git("log", "-1", "--format=%cI", rev)
    return parse_instant(done.stdout.strip()) if done.returncode == 0 else None


# ── git : branches, worktrees, fusion par comparaison d'arbres ────────────────


@dataclass(frozen=True, slots=True)
class BranchRef:
    name: str  # ``feat/x`` (locale) ou ``origin/feat/x`` (distante)
    remote: bool
    sha: str
    committed_at: str
    subject: str


def list_branches(src: Sources) -> list[BranchRef] | None:
    """Branches locales et distantes (les références symboliques ``origin/HEAD`` exclues) ; ``None`` si git échoue."""
    fmt = "%(refname)%00%(objectname)%00%(committerdate:iso-strict)%00%(symref)%00%(subject)"
    done = src.git("for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes")
    if done.returncode != 0:
        return None
    refs: list[BranchRef] = []
    for line in done.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) != 5 or parts[3]:
            continue
        ref, sha, when, _symref, subject = parts
        if ref.startswith("refs/heads/"):
            refs.append(BranchRef(ref.removeprefix("refs/heads/"), False, sha, when, subject))
        elif ref.startswith("refs/remotes/"):
            refs.append(BranchRef(ref.removeprefix("refs/remotes/"), True, sha, when, subject))
    return sorted(refs, key=lambda r: (r.remote, r.name))


def ahead_behind(src: Sources, target_sha: str, sha: str) -> tuple[int, int] | None:
    """``(en avance, en retard)`` de *sha* par rapport à la cible ; ``None`` si git échoue."""
    done = src.git("rev-list", "--left-right", "--count", f"{target_sha}...{sha}")
    parts = done.stdout.split()
    if done.returncode != 0 or len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[1]), int(parts[0])


@contextmanager
def scratch_objects(src: Sources) -> Iterator[dict[str, str] | None]:
    """Un magasin d'objets git jetable sous ``_grimoire-output/steward/`` pour ``git merge-tree``.

    ``merge-tree --write-tree`` écrit les arbres qu'il calcule : redirigés ici
    (``GIT_OBJECT_DIRECTORY``, le vrai magasin en alternate, lu seulement), ils
    ne touchent jamais ``.git/objects`` du dépôt, et le dossier est retiré à la sortie.
    """
    done = src.git("rev-parse", "--git-path", "objects")
    raw = done.stdout.strip()
    if done.returncode != 0 or not raw:
        yield None
        return
    objects = Path(raw) if Path(raw).is_absolute() else src.root / raw
    scratch = ensure_output_dir(src.root) / _SCRATCH
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        yield {"GIT_OBJECT_DIRECTORY": str(scratch), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects.resolve())}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class MergeProbe:
    """Ce que donnerait la fusion de la branche dans la cible, calculé sans rien fusionner.

    ``status`` : ``propre``, ``conflit`` ou :data:`NON_MESURE`. ``same_tree`` : l'arbre fusionné
    est celui de la cible — la branche n'apporte rien de plus, même fusionnée par squash.
    """

    status: str
    same_tree: bool = False
    files: tuple[str, ...] = ()
    reason: str = ""

    @property
    def conflict_kind(self) -> str:
        if self.status != "conflit":
            return "aucun" if self.status == "propre" else NON_MESURE
        return "changelog_seul" if self.files == (CHANGELOG,) else "autre"


def merge_probe(src: Sources, target_sha: str, target_tree: str, sha: str, env: Mapping[str, str] | None) -> MergeProbe:
    """``git merge-tree --write-tree`` (git ≥ 2.38) de *sha* dans la cible, objets redirigés par *env*."""
    if env is None:
        return MergeProbe(NON_MESURE, reason="magasin d'objets temporaire indisponible")
    done = src.git("merge-tree", "--write-tree", "--name-only", "--no-messages", target_sha, sha, env=env)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode not in (0, 1) or not lines:
        first = (done.stderr or "").strip().splitlines()
        return MergeProbe(NON_MESURE, reason=f"git merge-tree : code {done.returncode}" + (f" ({first[0]})" if first else ""))
    tree, files = lines[0].strip(), tuple(sorted({line.strip() for line in lines[1:]}))
    if done.returncode == 1:
        return MergeProbe("conflit", files=files)
    return MergeProbe("propre", same_tree=tree == target_tree)


@dataclass(frozen=True, slots=True)
class WorktreeEntry:
    path: str
    head: str
    branch: str
    locked: bool
    prunable: bool


def list_worktrees(src: Sources) -> list[WorktreeEntry] | None:
    done = src.git("worktree", "list", "--porcelain")
    if done.returncode != 0:
        return None
    entries: list[WorktreeEntry] = []
    for block in done.stdout.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        entries.append(
            WorktreeEntry(
                path=fields["worktree"],
                head=fields.get("HEAD", ""),
                branch=fields.get("branch", "").removeprefix("refs/heads/"),
                locked="locked" in fields,
                prunable="prunable" in fields,
            )
        )
    return entries
