"""L'historique des versions d'un chantier de l'Atelier, en LECTURE SEULE (épique #1197).

Les versions d'un chantier sont les commits git qui ont touché ses fichiers de phase (brief, brainstorm, compréhension,
exigences, cahier des charges), plus, si l'arbre de travail diffère de ``HEAD``, une entrée « modifications non commitées ».

Routes (préfixe ``/api/workspace/``, branchées par :mod:`grimoire.tools.workspace_atelier`) :

- ``GET atelier/chantiers/<slug>/history?page=<n>`` : :func:`listing`, 50 versions par page ;
- ``GET atelier/chantiers/<slug>/history/<id>`` : :func:`detail`, le diff par lignes de chaque phase touchée.
  ``<id>`` est ``working`` (modifications non commitées) ou un identifiant de commit (7 à 40 hexadécimaux).

Garde-fous :

- git est lancé SANS shell (liste d'arguments), avec ``--literal-pathspecs``, ``-c core.quotepath=off``,
  ``-c core.fsmonitor=false`` (un dépôt ne fait exécuter aucun programme), sans paginateur, sans diff externe ni
  conversion de texte ; un délai maximal et une sortie bornée (le processus est tué au-delà) ;
- seules des commandes de lecture sont lancées (``rev-parse``, ``log``, ``ls-tree``, ``show``) ; aucun réseau ;
- **aucun argument ne vient du client** hormis l'identifiant de version : il est validé par expression régulière, puis
  retrouvé *dans la liste des commits du chantier* ; c'est le condensat complet de cette liste qui est passé à git, jamais
  la chaîne reçue (une option injectée n'atteint donc jamais git) ;
- les fichiers de phase viennent de :func:`workspace_atelier._phase_path` (chantier retrouvé dans la liste réelle, aucun
  lien symbolique, confinement), puis ``resolve()`` + ``is_relative_to`` ;
- projet hors dépôt, git absent, fichier non suivi, dépôt sans commit : réponse propre (liste vide + ``reason``), jamais d'erreur.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES

__all__ = ["PAGE_SIZE", "detail", "listing"]

PAGE_SIZE = 50
MAX_PAGE = 200
#: Le nombre de commits parcourus pour retrouver un identifiant : au-delà, la version est inconnue.
MAX_SCAN = 5000
GIT_TIMEOUT = 8.0
MAX_OUTPUT = 2_000_000
MAX_BLOB = 400_000
MAX_DIFF_LINES = 4000
MAX_ROWS = 2000
MAX_LINE_CHARS = 2000
CONTEXT = 3
WORKING = "working"
_ID = re.compile(r"^[0-9a-fA-F]{7,40}$")
_US, _RS = "\x1f", "\x1e"
REASONS = {
    "git-absent": "git n'est pas installé sur cette machine : l'historique n'est pas disponible.",
    "pas-de-depot": "Ce projet n'est pas dans un dépôt git : il n'y a pas d'historique à montrer.",
    "depot-sans-commit": "Le dépôt n'a encore aucun commit : l'historique commencera au premier.",
    "fichiers-non-suivis": "Les fichiers de ce chantier ne sont pas encore suivis par git : ajoute-les à un commit pour garder leur historique.",
    "aucune-version": "Aucun commit n'a encore touché ce chantier.",
    "git-illisible": "git n'a pas pu lire l'historique (délai dépassé ou dépôt illisible).",
}


@dataclass(frozen=True, slots=True)
class _Out:
    code: int
    data: bytes
    truncated: bool = False
    failed: str = ""  # "absent" (git introuvable) ou "delai"


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def _git(root: Path, args: list[str], *, limit: int = MAX_OUTPUT, timeout: float = GIT_TIMEOUT) -> _Out:
    """Lance ``git`` (lecture seule), sans shell, délai et sortie bornés. Ne lève jamais."""
    argv = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.quotepath=off", "-c", "core.fsmonitor=false", *args]
    try:
        proc = subprocess.Popen(  # liste d'arguments, aucun shell ; voir la docstring du module
            argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_env(),
        )
    except (FileNotFoundError, NotADirectoryError):
        return _Out(127, b"", failed="absent")
    except OSError:
        return _Out(126, b"", failed="delai")
    chunks: list[bytes] = []
    state = {"size": 0, "cut": False}

    def pump() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                return
            state["size"] += len(chunk)
            chunks.append(chunk)
            if state["size"] > limit:
                state["cut"] = True
                proc.kill()
                return

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    reader.join(timeout)
    timed_out = reader.is_alive()
    if timed_out:
        proc.kill()
        reader.join(2)
    proc.wait()
    if proc.stdout is not None:
        proc.stdout.close()
    data = b"".join(chunks)[:limit]
    return _Out(proc.returncode, data, truncated=bool(state["cut"]), failed="delai" if timed_out else "")


# ── Le chantier : ses fichiers de phase, retrouvés dans la liste réelle ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Phase:
    id: str
    title: str
    rel: str  # chemin relatif à la racine du projet, séparateurs « / »
    path: Path


def _phases(root: Path, slug: str) -> list[_Phase]:
    from grimoire.tools import workspace_atelier as wa

    slug = wa._slug(slug)
    base = root.resolve()
    out: list[_Phase] = []
    for phase in PHASES:
        try:
            path = wa._phase_path(root, slug, phase.filename)
        except FileNotFoundError:
            continue
        if not path.resolve().is_relative_to(base):
            continue
        out.append(_Phase(phase.id, phase.title, path.relative_to(root).as_posix(), path))
    if not out:
        raise FileNotFoundError(f"chantier inconnu : {slug!r}")
    return out


def _page(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return min(max(value, 1), MAX_PAGE)


def _text(blob: bytes) -> str | None:
    """Le texte d'un fichier, ou ``None`` s'il est binaire."""
    if b"\x00" in blob:
        return None
    return blob.decode("utf-8", errors="replace")


# ── Le dépôt : ce qu'on en sait, sans rien écrire ──────────────────────────────────────────────────────────


def _repo_state(root: Path) -> str:
    """``""`` si le projet est dans un dépôt qui a au moins un commit, sinon la raison."""
    inside = _git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside.failed == "absent":
        return "git-absent"
    if inside.failed:
        return "git-illisible"
    if inside.code != 0 or inside.data.strip() != b"true":
        return "pas-de-depot"
    head = _git(root, ["rev-parse", "--verify", "-q", "HEAD"])
    if head.failed:
        return "git-illisible"
    return "" if head.code == 0 else "depot-sans-commit"


def _tracked(root: Path, phases: list[_Phase]) -> set[str]:
    """Les fichiers de phase présents dans ``HEAD``."""
    done = _git(root, ["ls-tree", "-r", "--name-only", "HEAD", "--", *[p.rel for p in phases]])
    if done.code != 0:
        return set()
    return {line for line in done.data.decode("utf-8", errors="replace").splitlines() if line}


def _blob(root: Path, rev: str, rel: str) -> tuple[str | None, bool]:
    """Le contenu d'un fichier à une révision : ``(texte, tronqué)`` ; ``("", False)`` s'il n'existe pas à cette révision."""
    done = _git(root, ["show", f"{rev}:./{rel}"], limit=MAX_BLOB)
    if done.code != 0 and not done.truncated:
        return "", False
    return _text(done.data), done.truncated


def _disk(path: Path) -> tuple[str | None, bool]:
    try:
        with path.open("rb") as handle:
            blob = handle.read(MAX_BLOB + 1)
    except OSError:
        return "", False
    return _text(blob[:MAX_BLOB]), len(blob) > MAX_BLOB


# ── Le diff par lignes ───────────────────────────────────────────────────────────────────────────────────


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"


def line_diff(old: str, new: str, budget: int = MAX_ROWS) -> dict[str, Any]:
    """Le diff par lignes entre deux textes : lignes ajoutées, retirées, contexte, trous (``gap``).

    Rend ``{"added", "removed", "rows", "truncated"}`` ; ``rows`` ne dépasse jamais *budget* lignes. Les compteurs sont
    ceux du diff entier (calculé sur les ``MAX_DIFF_LINES`` premières lignes de chaque côté).
    """
    old_lines = old.splitlines()[:MAX_DIFF_LINES]
    new_lines = new.splitlines()[:MAX_DIFF_LINES]
    cut = len(old.splitlines()) > MAX_DIFF_LINES or len(new.splitlines()) > MAX_DIFF_LINES
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    rows: list[dict[str, Any]] = []
    truncated = cut
    if added or removed:
        previous_end = 0
        for group in matcher.get_grouped_opcodes(CONTEXT):
            skipped = group[0][1] - previous_end
            if rows and skipped > 0:
                rows.append({"t": "gap", "skipped": skipped})
            for kind, i1, i2, j1, j2 in group:
                if kind == "equal":
                    rows.extend({"t": "ctx", "old": i + 1, "new": j + 1, "text": _clip(old_lines[i])}
                                for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
                    continue
                rows.extend({"t": "del", "old": i + 1, "new": None, "text": _clip(old_lines[i])} for i in range(i1, i2))
                rows.extend({"t": "add", "old": None, "new": j + 1, "text": _clip(new_lines[j])} for j in range(j1, j2))
            previous_end = group[-1][2]
            if len(rows) > budget:
                break
    if len(rows) > budget:
        rows = rows[:budget]
        truncated = True
    return {"added": added, "removed": removed, "rows": rows, "truncated": truncated}


# ── Liste ────────────────────────────────────────────────────────────────────────────────────────────────


def _empty(reason: str, page: int = 1) -> dict[str, Any]:
    return {"available": reason in ("", "depot-sans-commit", "fichiers-non-suivis", "aucune-version"), "reason": reason,
            "reason_text": REASONS.get(reason, ""), "versions": [], "page": page, "page_size": PAGE_SIZE, "has_more": False}


def _title_of(phases: list[_Phase], ids: set[str]) -> list[dict[str, str]]:
    return [{"id": p.id, "title": p.title} for p in phases if p.id in ids]


def _parse_log(text: str, phases: list[_Phase]) -> list[dict[str, Any]]:
    by_rel = {p.rel: p.id for p in phases}
    out: list[dict[str, Any]] = []
    for record in text.split(_RS):
        if not record.strip():
            continue
        head, _, body = record.partition("\n")
        fields = head.split(_US, 3)
        if len(fields) < 4:
            continue
        sha, date, author, subject = fields
        touched: set[str] = set()
        added = removed = 0
        for line in body.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[2] not in by_rel:
                continue
            touched.add(by_rel[parts[2]])
            added += int(parts[0]) if parts[0].isdigit() else 0
            removed += int(parts[1]) if parts[1].isdigit() else 0
        out.append({
            "id": sha, "short": sha[:7], "kind": "commit", "date": date, "author": author, "subject": subject[:200],
            "phases": _title_of(phases, touched), "added": added, "removed": removed,
        })
    return out


def _working(root: Path, phases: list[_Phase], tracked: set[str]) -> tuple[dict[str, Any] | None, list[str]]:
    """L'entrée « modifications non commitées » (phases suivies qui diffèrent de ``HEAD``) et les phases non suivies."""
    touched: set[str] = set()
    added = removed = 0
    untracked: list[str] = []
    for phase in phases:
        if phase.rel not in tracked:
            if phase.path.is_file():
                untracked.append(phase.id)
            continue
        old, old_cut = _blob(root, "HEAD", phase.rel)
        new, new_cut = _disk(phase.path)
        if old is None or new is None or old_cut or new_cut:
            continue
        diff = line_diff(old, new, 0)
        if diff["added"] or diff["removed"]:
            touched.add(phase.id)
            added += diff["added"]
            removed += diff["removed"]
    if not touched:
        return None, untracked
    entry = {"id": WORKING, "short": "", "kind": "working", "date": None, "author": "", "subject": "Modifications non commitées",
             "phases": _title_of(phases, touched), "added": added, "removed": removed}
    return entry, untracked


def listing(root: Path, slug: str, page: str = "1") -> dict[str, Any]:
    """Les versions d'un chantier, plus récente d'abord, par pages de :data:`PAGE_SIZE`. Lecture seule, ne lève pas (hors chantier inconnu)."""
    phases = _phases(root, slug)
    number = _page(page)
    reason = _repo_state(root)
    if reason:
        return _empty(reason, number)
    rels = [p.rel for p in phases]
    done = _git(root, [
        "log", "--no-merges", "--no-renames", "--relative", "--numstat", f"--format={_RS}%H{_US}%aI{_US}%an{_US}%s",
        "-n", str(PAGE_SIZE + 1), f"--skip={(number - 1) * PAGE_SIZE}", "--", *rels,
    ])
    if done.failed or done.code != 0:
        return _empty("git-illisible", number)
    commits = _parse_log(done.data.decode("utf-8", errors="replace"), phases)
    has_more = len(commits) > PAGE_SIZE
    versions = commits[:PAGE_SIZE]
    untracked: list[str] = []
    if number == 1:
        entry, untracked = _working(root, phases, _tracked(root, phases))
        if entry is not None:
            versions.insert(0, entry)
    out = _empty("", number)
    out.update({"versions": versions, "has_more": has_more, "untracked": untracked})
    if not versions and number == 1:
        out["reason"] = "fichiers-non-suivis" if untracked else "aucune-version"
        out["reason_text"] = REASONS[out["reason"]]
    return out


# ── Détail ───────────────────────────────────────────────────────────────────────────────────────────────


def _resolve(root: Path, phases: list[_Phase], raw: str) -> str:
    """Le condensat complet d'un commit du chantier, retrouvé dans la liste de ses commits ; refuse le reste."""
    if raw == WORKING:
        return WORKING
    if not _ID.match(raw):
        raise ValueError("identifiant de version invalide")
    wanted = raw.lower()
    done = _git(root, ["log", "--no-merges", "--format=%H", "-n", str(MAX_SCAN), "--", *[p.rel for p in phases]])
    if done.failed or done.code != 0:
        raise FileNotFoundError("version introuvable")
    known = [line for line in done.data.decode("ascii", errors="replace").split() if re.fullmatch(r"[0-9a-f]{40}", line)]
    found = [h for h in known if h.startswith(wanted)]
    if len(found) != 1:
        raise FileNotFoundError("version introuvable")
    return found[0]


def _meta(root: Path, sha: str) -> dict[str, Any]:
    done = _git(root, ["show", "-s", f"--format=%aI{_US}%an{_US}%s", sha])
    fields = done.data.decode("utf-8", errors="replace").strip().split(_US, 2) if done.code == 0 else []
    date, author, subject = [*fields, "", "", ""][:3]
    return {"date": date, "author": author, "subject": subject[:200]}


def _phase_diff(phase: _Phase, old: str | None, new: str | None, cut: bool, budget: int) -> dict[str, Any] | None:
    base: dict[str, Any] = {"id": phase.id, "title": phase.title, "added": 0, "removed": 0, "rows": [], "truncated": False, "note": ""}
    if old is None or new is None:
        return {**base, "note": "fichier binaire : aucun diff par lignes."}
    if cut:
        return {**base, "truncated": True, "note": "fichier trop volumineux : diff non calculé."}
    if old == new:
        return None
    diff = line_diff(old, new, budget)
    return {**base, **diff}


def detail(root: Path, slug: str, version: str) -> dict[str, Any]:
    """Le diff, phase par phase, entre une version et la précédente (ou ``HEAD`` pour les modifications non commitées)."""
    phases = _phases(root, slug)
    reason = _repo_state(root)
    if reason:
        raise FileNotFoundError(REASONS.get(reason, "version introuvable"))
    sha = _resolve(root, phases, version)
    out: dict[str, Any] = {"id": sha, "short": sha[:7], "kind": "commit", "date": None, "author": "", "subject": "", "phases": [],
                           "truncated": False}
    if sha == WORKING:
        out.update({"short": "", "kind": "working", "subject": "Modifications non commitées"})
        tracked = _tracked(root, phases)
    else:
        out.update(_meta(root, sha))
        tracked = set()
    budget = MAX_ROWS
    for phase in phases:
        if sha == WORKING:
            if phase.rel not in tracked:
                continue
            old, old_cut = _blob(root, "HEAD", phase.rel)
            new, new_cut = _disk(phase.path)
        else:
            old, old_cut = _blob(root, f"{sha}^", phase.rel)
            new, new_cut = _blob(root, sha, phase.rel)
        shown = _phase_diff(phase, old, new, old_cut or new_cut, max(budget, 0))
        if shown is None:
            continue
        budget -= len(shown["rows"])
        out["truncated"] = out["truncated"] or bool(shown["truncated"]) or (budget <= 0 and bool(shown["rows"]))
        out["phases"].append(shown)
    out["added"] = sum(p["added"] for p in out["phases"])
    out["removed"] = sum(p["removed"] for p in out["phases"])
    return out
