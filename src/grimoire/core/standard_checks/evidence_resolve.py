"""Résolution des preuves d'une plage git : ``grimoire evidence resolve <base>..<head>`` (D-13).

**Ancrage.** La plage est une plage de révisions git, et rien d'autre :

- le journal chaîné de W4-03 (``evidence-log.jsonl``) vit sous ``RUNS_DIR``, ignoré
  par git, propre à chaque copie de travail, et son ancre se réécrit avec lui
  (limite écrite dans :mod:`.evidence_chain`) : une « plage du journal » ne
  reposerait que sur elle-même ;
- les registres d'affirmations (``claim-ledger.md``) et ``test-run.json`` vivent
  sous ``EVIDENCE_DIR``, versionnés : un commit les fige avec le code qu'ils
  citent, sous des identifiants de contenu que personne ne réécrit sans changer
  le commit.

Les preuves d'une plage sont donc les jetons de la cellule Preuve (forme de W3-02 :
``chemin:ligne``, ``@sha256:``, ``test-run.json#<run_id>``…) des lignes de
registre qu'un commit de la plage a écrites ou modifiées (``git blame`` à
``head``). Chaque jeton est jugé contre les objets git, jamais contre l'arbre de
travail :

- ``resolved`` : la cible existe à ``head`` et correspond (lignes citées
  identiques à celles du commit qui a écrit l'affirmation, empreinte épinglée
  égale, run enregistré vert et toujours le même) ;
- ``stale`` : la cible existe à ``head`` mais ne correspond plus ;
- ``missing`` : la cible n'existe pas à ``head`` (fichier, ligne, nœud, run) ;
- ``unknown`` : rien dans git ne permet de trancher (mémoire, URL, texte libre,
  fichier absent au commit de l'affirmation, ``test-run.json`` illisible ou sans
  empreinte, erreur du résolveur).

Aucun statut n'est donné par défaut : un jeton que le code ne sait pas juger est
``unknown``, et seul ``resolved`` compte comme résolu. Le rapport dit toujours sur
quoi il repose (``anchor`` : commits de base et de tête, tête publiée ou non).
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows
from grimoire.core.standard_checks.claim_proof_resolve import (
    _HEAD_SPLIT_RE,
    _MEMORY_RE,
    _REF_RE,
    _SPLIT_RE,
    _TEST_RUN_RE,
    _TRIM,
    _URL_RE,
    _looks_like_path,
)
from grimoire.core.standard_checks.controls import acceptance_test_run_relpath
from grimoire.core.standard_generation import EVIDENCE_DIR

__all__ = ["STATUSES", "RangeError", "RangeProof", "RangeReport", "resolve_range"]

#: Statuts possibles d'une preuve ; seul ``resolved`` est résolu.
STATUSES = ("resolved", "stale", "missing", "unknown")

_LEDGER_NAME = "claim-ledger.md"
_GIT_REDIRECT_VARS = frozenset(
    {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX"}
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_PROVEN_RE = re.compile(r"^\s*prouv", re.IGNORECASE)


class RangeError(ValueError):
    """Plage illisible, révision inconnue ou dossier hors d'un dépôt git (usage : code 2)."""


@dataclass(frozen=True)
class RangeProof:
    """Verdict d'une preuve de la plage."""

    task_id: str
    claim: str
    ledger: str
    ledger_line: int
    claim_commit: str
    token: str
    kind: str  # path_line | test_run | memory | url | free_text | none | error
    status: str
    reason: str = ""
    fingerprint: str = ""


@dataclass
class RangeReport:
    """Rapport d'une plage : l'ancrage, puis une ligne par preuve."""

    range: str
    base: str | None
    head: str
    commits: int
    published: bool | None
    proofs: list[RangeProof] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(STATUSES, 0)
        for proof in self.proofs:
            out[proof.status] += 1
        return out

    @property
    def verdict(self) -> str:
        """``empty`` (aucune preuve), ``resolved`` (toutes résolues) ou ``unresolved``."""
        if not self.proofs:
            return "empty"
        return "resolved" if all(p.status == "resolved" for p in self.proofs) else "unresolved"

    def to_dict(self) -> dict[str, object]:
        return {
            "range": self.range,
            "anchor": {
                "kind": "git",
                "base": self.base,
                "head": self.head,
                "commits": self.commits,
                "head_published": self.published,
                "basis": "claim-ledger rows written in the range, judged against git objects at head",
            },
            "verdict": self.verdict,
            "summary": {**self.counts(), "total": len(self.proofs)},
            "proofs": [asdict(p) for p in self.proofs],
        }


# ── git ──────────────────────────────────────────────────────────────────────


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECT_VARS}


class _Git:
    def __init__(self, top: Path) -> None:
        self.top = top

    def run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.top), *args], capture_output=True, timeout=120, check=False, env=_env()
        )

    def text(self, *args: str) -> str:
        done = self.run(*args)
        if done.returncode != 0:
            raise RangeError(f"git {' '.join(args)}: {done.stderr.decode('utf-8', 'replace').strip()}")
        return done.stdout.decode("utf-8", "replace")

    def rev(self, ref: str) -> str | None:
        done = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return done.stdout.decode().strip() if done.returncode == 0 else None

    def blob(self, rev: str, path: str) -> bytes | None:
        """Contenu du fichier *path* (relatif au dépôt) au commit *rev* ; ``None`` s'il n'y est pas un fichier."""
        kind = self.run("cat-file", "-t", f"{rev}:{path}")
        if kind.returncode != 0 or kind.stdout.strip() != b"blob":
            return None
        done = self.run("cat-file", "blob", f"{rev}:{path}")
        return done.stdout if done.returncode == 0 else None

    def blame(self, rev: str, path: str) -> dict[int, str]:
        """Numéro de ligne (1-based) -> commit qui l'a écrite, à *rev*."""
        out: dict[int, str] = {}
        current: tuple[str, int] | None = None
        for line in self.text("blame", "--line-porcelain", rev, "--", path).splitlines():
            if line.startswith("\t"):
                continue
            parts = line.split(" ")
            if len(parts) >= 3 and _SHA_RE.match(parts[0]) and parts[2].isdigit():
                current = (parts[0], int(parts[2]))
                out[current[1]] = current[0]
        return out


def _open(root: Path) -> tuple[_Git, str]:
    probe = _Git(root)
    done = probe.run("rev-parse", "--show-toplevel", "--show-prefix")
    if done.returncode != 0:
        raise RangeError(f"not a git repository: {root}")
    lines = done.stdout.decode("utf-8", "replace").splitlines()
    top, prefix = Path(lines[0]), (lines[1] if len(lines) > 1 else "")
    return _Git(top), prefix


def _parse_range(git: _Git, spec: str) -> tuple[str | None, str]:
    if "..." in spec:
        raise RangeError(f"symmetric range not supported: {spec} (use <base>..<head>)")
    if ".." in spec:
        left, right = spec.split("..", 1)
        base, head = git.rev(left or "HEAD"), git.rev(right or "HEAD")
        if base is None or head is None:
            raise RangeError(f"unknown revision in range: {spec}")
        return base, head
    head = git.rev(spec)
    if head is None:
        raise RangeError(f"unknown revision: {spec}")
    return git.rev(f"{head}^"), head


def _published(git: _Git, head: str) -> bool | None:
    remotes = git.run("for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes")
    if remotes.returncode != 0 or not remotes.stdout.strip():
        return None
    contains = git.run("for-each-ref", "--count=1", "--format=%(refname)", "--contains", head, "refs/remotes")
    return bool(contains.stdout.strip()) if contains.returncode == 0 else None


# ── preuves ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Ctx:
    git: _Git
    prefix: str
    head: str
    claim_commit: str
    task_id: str


def _lines(data: bytes) -> list[str]:
    return data.decode("utf-8", "replace").splitlines()


def _fingerprint(chunk: str) -> str:
    return "sha256:" + hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def _resolve_path_line(ctx: _Ctx, token: str, path: str) -> tuple[str, str, str]:
    match = _REF_RE.match(token)
    if match is None:
        return "missing", f"unreadable locator after {path} (expected path:line, path#L12, path::test)", ""
    rel = posixpath.normpath(path)
    if rel == ".." or rel.startswith(("/", "../")):
        return "missing", "path escapes the project root", ""
    short = ctx.head[:12]
    now = ctx.git.blob(ctx.head, ctx.prefix + rel)
    if now is None:
        return "missing", f"{rel} is not a file at {short}", ""
    lines = _lines(now)
    if match["start"]:
        start = int(match["start"])
        end = int(match["end"]) if match["end"] else start
        if start < 1 or end < start or end > len(lines):
            span = f"{start}-{end}" if end != start else str(start)
            return "missing", f"line {span} outside {rel} at {short} ({len(lines)} lines)", ""
        cited, where = "\n".join(lines[start - 1 : end]), f"lines {start}-{end}"
    else:
        start = end = 0
        cited, where = "\n".join(lines), "file"
    if match["node"]:
        name = match["node"].rsplit("::", 1)[-1].split("[", 1)[0]
        if name and name not in "\n".join(lines):
            return "missing", f"test node {name} not found in {rel} at {short}", ""
    if match["sha"] and not hashlib.sha256(now).hexdigest().startswith(match["sha"].lower()):
        return "stale", f"sha256 of {rel} at {short} does not match the pinned hash", ""
    then = ctx.git.blob(ctx.claim_commit, ctx.prefix + rel)
    if then is None:
        return "unknown", f"{rel} did not exist at the claim commit {ctx.claim_commit[:12]}: no content to compare", ""
    before = _lines(then)
    cited_then = "\n".join(before[start - 1 : end]) if start else "\n".join(before)
    if cited_then != cited:
        return "stale", f"{where} of {rel} changed between the claim commit {ctx.claim_commit[:12]} and {short}", ""
    return "resolved", f"{where} of {rel} unchanged since the claim commit {ctx.claim_commit[:12]}", _fingerprint(cited)


def _resolve_test_run(ctx: _Ctx, match: re.Match[str]) -> tuple[str, str, str]:
    canonical = acceptance_test_run_relpath(ctx.task_id)
    given = match["dir"].rstrip("/")
    if given and posixpath.normpath(given) != canonical.parent.as_posix():
        return "missing", f"only the test-run.json recorded for this task counts ({canonical.as_posix()})", ""
    data = ctx.git.blob(ctx.head, ctx.prefix + canonical.as_posix())
    if data is None:
        return "missing", f"{canonical.as_posix()} is not committed at {ctx.head[:12]}", ""
    try:
        run = json.loads(data.decode("utf-8"))
    except ValueError:
        return "unknown", f"{canonical.as_posix()} is unreadable at {ctx.head[:12]}", ""
    if not isinstance(run, dict) or not run.get("tree_fingerprint"):
        return "unknown", "test-run.json has no tree_fingerprint: not written by record_acceptance_test_run", ""
    if run.get("run_id") != match["run"]:
        return "stale", f"run {match['run']} replaced by run {run.get('run_id')!r} at {ctx.head[:12]}", ""
    if run.get("ok") is not True:
        return "stale", f"run {match['run']} is not green (ok={run.get('ok')!r})", ""
    return "resolved", f"run {match['run']} recorded green at {ctx.head[:12]}", f"tree:{run['tree_fingerprint']}"


def _resolve_token(ctx: _Ctx, root: Path, token: str) -> tuple[str, str, str, str] | None:
    """(kind, status, reason, fingerprint), ou ``None`` pour un jeton de texte libre."""
    if _URL_RE.match(token):
        return "url", "unknown", "URL not fetched (no network): nothing in git to compare", ""
    if match := _TEST_RUN_RE.match(token):
        return ("test_run", *_resolve_test_run(ctx, match))
    if _MEMORY_RE.match(token):
        return "memory", "unknown", "memory references are not versioned: nothing in git to compare", ""
    head = _HEAD_SPLIT_RE.split(token, maxsplit=1)[0]
    if _looks_like_path(root, head) or ctx.git.blob(ctx.head, ctx.prefix + head) is not None:
        return ("path_line", *_resolve_path_line(ctx, token, head))
    return None


def _cell_proofs(ctx: _Ctx, root: Path, cell: str) -> list[tuple[str, str, str, str, str]]:
    out: list[tuple[str, str, str, str, str]] = []
    free: list[str] = []
    for raw in _SPLIT_RE.split(cell.strip()):
        token = raw.strip(_TRIM) if not raw.lower().startswith("http") else raw.strip()
        if not token:
            continue
        try:
            verdict = _resolve_token(ctx, root, token)
        except Exception as exc:  # fail-closed : un résolveur qui casse ne prouve rien
            out.append((token, "error", "unknown", f"resolver failed: {type(exc).__name__}: {exc}", ""))
            continue
        if verdict is None:
            free.append(token)
        else:
            out.append((token, *verdict))
    if free:
        out.append((" ".join(free), "free_text", "unknown", "free text: not a reference git can check", ""))
    return out


def _ledgers(git: _Git, prefix: str, base: str | None, head: str) -> list[str]:
    scope = prefix + EVIDENCE_DIR.as_posix()
    if base is None:
        names = git.text("ls-tree", "-r", "--name-only", "--full-name", head, "--", scope).splitlines()
    else:
        names = git.text("diff", "--name-only", "--no-renames", base, head, "--", scope).splitlines()
    depth = len(Path(scope).parts) + 2
    keep = [n for n in names if n.endswith("/" + _LEDGER_NAME) and len(Path(n).parts) == depth]
    return sorted(n for n in keep if git.blob(head, n) is not None)


def resolve_range(root: Path, spec: str) -> RangeReport:
    """Juge chaque preuve écrite dans la plage *spec* contre les objets git de sa tête. Lève :class:`RangeError`."""
    git, prefix = _open(root)
    base, head = _parse_range(git, spec)
    revs = git.text("rev-list", f"{base}..{head}" if base else head).split()
    commits = set(revs)
    report = RangeReport(spec, base, head, len(revs), _published(git, head))
    if not commits:
        return report
    for ledger in _ledgers(git, prefix, base, head):
        task_id = Path(ledger).parent.name
        blame = git.blame(head, ledger)
        text = (git.blob(head, ledger) or b"").decode("utf-8", "replace")
        numbers: dict[str, deque[int]] = defaultdict(deque)
        for number, line in enumerate(text.splitlines(), 1):
            numbers[line].append(number)
        scan = scan_claim_rows(text)
        for line, cells in sorted(scan.evaluated + scan.unevaluated_cells, key=lambda row: numbers[row[0]][0]):
            number = numbers[line].popleft()
            commit = blame.get(number, "")
            if commit not in commits:
                continue
            ctx = _Ctx(git, prefix, head, commit, task_id)
            rel_ledger = ledger[len(prefix) :]
            if not cells[3]:
                if len(cells) > 4 and _PROVEN_RE.match(cells[4]):
                    report.proofs.append(
                        RangeProof(task_id, cells[0], rel_ledger, number, commit, "", "none", "missing",
                                   "claim marked proven without any proof")
                    )
                continue
            for token, kind, status, reason, fingerprint in _cell_proofs(ctx, root, cells[3]):
                report.proofs.append(
                    RangeProof(task_id, cells[0], rel_ledger, number, commit, token, kind, status, reason, fingerprint)
                )
    return report
