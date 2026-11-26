"""La traçabilité d'une exigence, de bout en bout (#1342, #1197) : première brique d'une spec vivante.

Le kit savait aller de l'exigence à la tâche (``cadrage approve``, ``task add --requirement``, ``cadrage recette``) ;
rien ne remontait du code vers l'exigence, et rien ne disait qu'une exigence avait changé après sa validation. Ce
module construit, **en lecture seule**, le graphe ::

    exigence -> validation MOA -> tâches -> commits / PR -> fichiers touchés -> preuves / tests -> recette

Rien n'est stocké : tout est dérivé de ce que le kit écrit déjà — le cadrage (:mod:`grimoire.core.cadrage_requirements`),
la vue de recette (:mod:`.moa_acceptance`, qui lit le journal d'autorité et le ledger) et git. Aucune écriture, aucune
autorité nouvelle : une trace ne valide, ne recette et ne ferme rien.

**Le lien tâche -> code** vient des événements du ledger : ``task.worktree_created`` (branche et base),
``task.merged`` (base, tête, SHA de fusion — par le merger du kit) et ``task.merged_externally`` (PR, SHA de fusion —
fusion hors merger). Les commits et fichiers sont relus dans git à partir de ces SHA ; un SHA absent du dépôt local est
dit (``notes``), jamais deviné. Une tâche qui n'a ni fusion ni branche vivante n'a pas de fichiers.

**La dérive** : la validation scelle l'**empreinte** du texte validé (:func:`~.requirement_gate.fingerprint`) et son
titre, pas le texte entier. Une exigence dont aucune validation ne porte l'empreinte actuelle, alors qu'une validation
antérieure existe, est « modifiée depuis la validation ». On sait *que* le texte a changé, et si la formulation (le
titre) a changé ; pas *quel* critère : le texte validé n'est pas conservé. Rien n'est ajouté au chemin de validation.

**Limites écrites** : les preuves viennent du ledger et du registre de preuves, que la MOE écrit (étiquette
:data:`~.moa_acceptance.PROOF_SOURCE`) ; un fichier est « touché » par une tâche, pas forcément « servi » au sens
fonctionnel ; une PR fusionnée en rebase (plusieurs commits, un seul SHA noté) ne remonte que son dernier commit.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from grimoire.core.cadrage import DEFAULT_CHANTIER
from grimoire.core.cadrage_requirements import read_requirements
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import moa_acceptance as moa
from grimoire.missions import requirement_gate as rg

if TYPE_CHECKING:
    from collections.abc import Iterable

    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "SCHEMA",
    "CadrageTrace",
    "CommitRef",
    "Drift",
    "FileTrace",
    "Gap",
    "GitReader",
    "RepoGit",
    "RequirementTrace",
    "TaskTrace",
    "build_trace",
    "normalize_file",
]

SCHEMA = "grimoire-cadrage-trace/v1"
#: Les trous : une exigence validée sans tâche, une tâche livrée sans preuve, une exigence sans critère.
NO_TASK, NO_PROOF, NO_CRITERION = "validated-without-task", "delivered-without-proof", "requirement-without-criterion"
GAP_LABELS = {
    NO_TASK: "exigence validée sans tâche",
    NO_PROOF: "tâche livrée sans preuve",
    NO_CRITERION: "exigence sans critère",
}
STAGE_LABELS = {
    "proposed": "proposée",
    "validated": "validée",
    "in_progress": "en cours",
    "delivered": "livrée",
    "accepted": "recette prononcée",
    "rework": "recette prononcée, reprise à faire",
}
_WORKTREE_EVENT = "task.worktree_created"
_MERGED_EVENT = "task.merged"
_EXTERNAL_EVENT = "task.merged_externally"
_ACTIVE = frozenset({"claimed", "running", "waiting", "blocked", "needs_verification"})
_ACCEPTED = frozenset({moa.ACCEPTED, moa.ACCEPTED_RESERVE, moa.ACCEPTED_WIP})
_REWORK = frozenset({moa.REJECTED, moa.FOLLOW_UP_MISSING})
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
_TEST_SUFFIXES = ("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
_SHA_LEN = 12


# ── git, en lecture seule ───────────────────────────────────────────────────


class GitReader(Protocol):
    """Ce que la trace demande à git : un inconnu se rend vide (``""``, ``[]``, ``None``), jamais par une exception."""

    def resolve(self, ref: str) -> str: ...

    def commits(self, base: str, head: str) -> list[tuple[str, str]]: ...

    def files(self, base: str, head: str) -> list[str] | None: ...

    def parents(self, sha: str) -> list[str]: ...

    def subject(self, sha: str) -> str: ...


class RepoGit:
    """Le dépôt de *root* : ``git rev-parse`` / ``rev-list`` / ``diff --name-only``, sans jamais écrire."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _run(self, *args: str) -> str | None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k == "GIT_SSH_COMMAND"}
        try:
            done = subprocess.run(
                ["git", "-C", str(self._root), "--no-pager", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def resolve(self, ref: str) -> str:
        if not ref or ref.startswith("-"):
            return ""
        out = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return out.strip() if out else ""

    def commits(self, base: str, head: str) -> list[tuple[str, str]]:
        out = self._run("log", "--format=%H%x09%s", "--no-merges", f"{base}..{head}")
        rows = [line.partition("\t") for line in (out or "").splitlines() if line.strip()]
        return [(sha, subject) for sha, _, subject in rows]

    def files(self, base: str, head: str) -> list[str] | None:
        out = self._run("diff", "--name-only", "--no-renames", "-z", base, head)
        return None if out is None else sorted(p for p in out.split("\0") if p)

    def parents(self, sha: str) -> list[str]:
        out = self._run("rev-list", "--parents", "-n", "1", sha)
        return (out or "").split()[1:]

    def subject(self, sha: str) -> str:
        return (self._run("log", "-n", "1", "--format=%s", sha) or "").strip()


# ── les types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommitRef:
    sha: str
    subject: str

    def to_dict(self) -> dict[str, str]:
        return {"sha": self.sha, "subject": self.subject}


@dataclass(frozen=True, slots=True)
class Gap:
    """Un trou de la chaîne : ce qui manque, et les tâches concernées (s'il y en a)."""

    kind: str
    detail: str
    tasks: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return GAP_LABELS[self.kind]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "detail": self.detail, "tasks": list(self.tasks)}


@dataclass(frozen=True, slots=True)
class Drift:
    """Une exigence modifiée depuis sa validation : ce que la validation a gardé (titre, date, MOA) et ce qui est concerné."""

    validated_title: str
    current_title: str
    validated_at: str
    moa: str
    tasks: tuple[str, ...] = ()
    files: tuple[str, ...] = ()

    @property
    def title_changed(self) -> bool:
        return self.validated_title.strip() != self.current_title.strip()

    @property
    def what(self) -> str:
        return (
            "formulation"
            if self.title_changed
            else "critères, priorité ou périmètre (le texte validé n'est pas conservé)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": "modifiée depuis la validation",
            "what": self.what,
            "title_changed": self.title_changed,
            "validated_title": self.validated_title,
            "current_title": self.current_title,
            "validated_at": self.validated_at,
            "validated_by": self.moa,
            "tasks": list(self.tasks),
            "files": list(self.files),
        }


@dataclass(slots=True)
class TaskTrace:
    """Une tâche qui sert l'exigence : sa branche, sa PR, ses commits, ses fichiers, ses preuves."""

    id: str
    title: str
    status: str
    delivered: bool
    branch: str = ""
    base_sha: str = ""
    head_sha: str = ""
    merged_sha: str = ""
    pr: int | None = None
    commits: list[CommitRef] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    verdict: str = ""
    evidence_pack: str = ""
    merged: str = ""
    checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def tests(self) -> list[str]:
        return [f for f in self.files if _is_test(f)]

    @property
    def has_proof(self) -> bool:
        return bool(self.verdict or self.evidence_pack or self.merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "delivered": self.delivered,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "merged_sha": self.merged_sha,
            "pr": self.pr,
            "commits": [c.to_dict() for c in self.commits],
            "files": list(self.files),
            "tests": self.tests,
            "proof": {
                "verdict": self.verdict,
                "evidence_pack": self.evidence_pack,
                "merged": self.merged,
                "checks": list(self.checks),
                "source": moa.PROOF_SOURCE if self.has_proof else "",
            },
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class RequirementTrace:
    key: str
    title: str
    priority: str
    criteria: tuple[str, ...]
    stage: str
    state: str
    validated: bool
    validated_by: str = ""
    validated_at: str = ""
    reason: str = ""
    verdict: dict[str, Any] | None = None
    tasks: list[TaskTrace] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    drift: Drift | None = None

    @property
    def stage_label(self) -> str:
        return STAGE_LABELS[self.stage]

    @property
    def files(self) -> list[str]:
        return sorted({f for t in self.tasks for f in t.files})

    @property
    def tests(self) -> list[str]:
        return sorted({f for t in self.tasks for f in t.tests})

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.key,
            "title": self.title,
            "priority": self.priority,
            "criteria": list(self.criteria),
            "stage": self.stage,
            "stage_label": self.stage_label,
            "state": self.state,
            "state_label": moa.STATE_LABELS[self.state],
            "validation": {
                "validated": self.validated,
                "by": self.validated_by,
                "at": self.validated_at,
                "reason": self.reason,
            },
            "tasks": [t.to_dict() for t in self.tasks],
            "files": self.files,
            "tests": self.tests,
            "recette": self.verdict,
            "gaps": [g.to_dict() for g in self.gaps],
            "drift": self.drift.to_dict() if self.drift else None,
        }


@dataclass(frozen=True, slots=True)
class FileTrace:
    """Le sens inverse : les exigences servies par un fichier, via les tâches qui l'ont touché."""

    path: str
    requirements: tuple[dict[str, Any], ...]

    @property
    def served(self) -> bool:
        return bool(self.requirements)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "file": self.path, "served": self.served, "requirements": list(self.requirements)}


@dataclass(slots=True)
class CadrageTrace:
    chantier: str
    requirements: list[RequirementTrace] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def gaps(self) -> list[tuple[str, Gap]]:
        return [(r.key, g) for r in self.requirements for g in r.gaps]

    @property
    def drifted(self) -> list[str]:
        return [r.key for r in self.requirements if r.drift is not None]

    def only(self, key: str) -> CadrageTrace:
        return CadrageTrace(self.chantier, [r for r in self.requirements if r.key == key], list(self.notes))

    def for_file(self, path: str) -> FileTrace:
        rows = []
        for req in self.requirements:
            via = [t for t in req.tasks if path in t.files]
            if via:
                rows.append(
                    {
                        "requirement": req.key,
                        "title": req.title,
                        "stage": req.stage,
                        "stage_label": STAGE_LABELS[req.stage],
                        "drift": req.drift is not None,
                        "tasks": [
                            {"id": t.id, "status": t.status, "pr": t.pr, "commits": [c.sha for c in t.commits]}
                            for t in via
                        ],
                    }
                )
        return FileTrace(path, tuple(rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "chantier": self.chantier,
            "requirements": [r.to_dict() for r in self.requirements],
            "summary": {
                "requirements": len(self.requirements),
                "gaps": len(self.gaps),
                "modified_since_validation": self.drifted,
            },
            "notes": list(self.notes),
        }


# ── construction ────────────────────────────────────────────────────────────


def _is_test(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name
    return (
        bool(_TEST_DIRS & set(pure.parts[:-1]))
        or name.startswith("test_")
        or name.endswith(_TEST_SUFFIXES)
        or name == "conftest.py"
    )


def normalize_file(root: Path, raw: str) -> str:
    """Le chemin relatif au projet, en ``/`` (un chemin absolu sous *root* est relativisé ; ``./`` retiré)."""
    text = raw.strip().replace("\\", "/")
    candidate = Path(text)
    if candidate.is_absolute():
        for base in (root, root.resolve()):
            try:
                text = candidate.relative_to(base).as_posix()
                break
            except ValueError:
                continue
    return str(PurePosixPath(text)) if text else ""


def _last(events: Iterable[LedgerEvent], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for event in events:
        if event.event_type == kind:
            found = dict(event.payload)
    return found


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _short(sha: str) -> str:
    return sha[:_SHA_LEN]


def _fill_code(trace: TaskTrace, events: list[LedgerEvent], git: GitReader) -> None:
    """Branche, SHA, PR, commits et fichiers d'une tâche, depuis ses événements puis git. Un inconnu est noté."""
    created = _last(events, _WORKTREE_EVENT)
    merged = _last(events, _MERGED_EVENT)
    external = _last(events, _EXTERNAL_EVENT)
    trace.branch = str(external.get("branch") or created.get("branch") or "")
    trace.pr = _int(external.get("pr"))
    base = str(merged.get("base_sha") or created.get("base_sha") or "")
    head = str(merged.get("head_sha") or external.get("head_sha") or "")
    merged_sha = str(merged.get("merged_sha") or external.get("merged_sha") or "")
    trace.merged_sha = _short(merged_sha)
    if merged and base and head:
        _range(trace, git, base, head)
    elif external and merged_sha:
        _merge_commit(trace, git, merged_sha)
    elif created and trace.branch and base:
        tip = git.resolve(f"refs/heads/{trace.branch}")
        if tip and tip != git.resolve(base):
            _range(trace, git, base, tip)
        elif not tip:
            trace.notes.append(f"branche {trace.branch} absente du dépôt local : aucun commit retrouvé")
    elif trace.delivered:
        trace.notes.append("aucune fusion ni branche notée au ledger : commits et fichiers inconnus")
    if trace.delivered and not trace.files and not trace.notes:
        trace.notes.append("aucun fichier retrouvé pour cette tâche livrée")


def _range(trace: TaskTrace, git: GitReader, base: str, head: str) -> None:
    trace.base_sha, trace.head_sha = _short(base), _short(head)
    files = git.files(base, head)
    if files is None:
        trace.notes.append(f"SHA {_short(base)}..{_short(head)} absents du dépôt local : fichiers inconnus")
        return
    trace.files = files
    trace.commits = [CommitRef(_short(sha), rg.safe_text(subject)) for sha, subject in git.commits(base, head)]


def _merge_commit(trace: TaskTrace, git: GitReader, merged: str) -> None:
    """Fusion hors merger : le SHA de fusion (squash ou merge commit) ; ses fichiers sont ceux du diff au 1er parent."""
    parents = git.parents(merged)
    if not parents:
        trace.notes.append(f"SHA de fusion {_short(merged)} absent du dépôt local : fichiers inconnus")
        return
    files = git.files(parents[0], merged)
    if files is None:
        trace.notes.append(f"SHA de fusion {_short(merged)} illisible : fichiers inconnus")
        return
    trace.files = files
    trace.head_sha = _short(parents[1]) if len(parents) > 1 else _short(merged)
    if len(parents) > 1:
        trace.commits = [CommitRef(_short(s), rg.safe_text(sub)) for s, sub in git.commits(parents[0], parents[1])]
    else:
        trace.commits = [CommitRef(_short(merged), rg.safe_text(git.subject(merged)))]


def _stage(view: moa.RequirementView) -> str:
    if not view.validated:
        return "proposed"
    if view.state in _ACCEPTED:
        return "accepted"
    if view.state in _REWORK:
        return "rework"
    if view.state == moa.DELIVERED:
        return "delivered"
    return "in_progress" if any(t.status in _ACTIVE for t in view.tasks) else "validated"


def _drift(
    entries: list[dict[str, Any]], project: str, key: str, view: moa.RequirementView, trace: RequirementTrace
) -> Drift | None:
    """Une validation scellée pour cette exigence existe, mais aucune pour son texte actuel."""
    if view.validated or not view.fingerprint:
        return None
    sealed = [
        e
        for e in entries
        if e.get("type") == rg.VALIDATED_ENTRY and e.get("project") == project and e.get("requirement") == key
    ]
    if not sealed or any(e.get("fingerprint") == view.fingerprint for e in sealed):
        return None
    last = sealed[-1]
    return Drift(
        rg.safe_text(last.get("title") or ""),
        view.title,
        rg.safe_text(last.get("ts") or ""),
        rg.safe_text(last.get("moa") or ""),
        tuple(t.id for t in trace.tasks),
        tuple(trace.files),
    )


def _gaps(trace: RequirementTrace, view: moa.RequirementView) -> list[Gap]:
    gaps: list[Gap] = []
    if not view.criteria:
        gaps.append(Gap(NO_CRITERION, f"{trace.key} n'a aucun critère d'acceptation dans le cadrage"))
    if trace.validated and not trace.tasks:
        gaps.append(
            Gap(NO_TASK, f"{trace.key} est validée par la MOA mais aucune tâche ne la sert (`task add --requirement`)")
        )
    bare = tuple(t.id for t in trace.tasks if t.delivered and not t.has_proof)
    if bare:
        gaps.append(
            Gap(NO_PROOF, "livrée sans verdict, pack de preuve ni PR fusionnée au ledger : " + ", ".join(bare), bare)
        )
    return gaps


def build_trace(
    root: Path,
    service: TaskService,
    chantier: str | None = None,
    *,
    git: GitReader | None = None,
    journal: AuthorityJournal | None = None,
) -> CadrageTrace:
    """La trace de chaque exigence planifiée du chantier. Lecture seule : ledger, journal d'autorité, cadrage et git."""
    from grimoire.missions.mandate import AuthorityJournal, JournalError

    chantier = None if chantier == DEFAULT_CHANTIER else chantier
    project = rg.origin_root(root)
    reader = git if git is not None else RepoGit(project)
    out = CadrageTrace(chantier or DEFAULT_CHANTIER)
    found = read_requirements(project, chantier)
    if not found.identified:
        out.notes.append("aucune exigence identifiée dans le cadrage (convention `REQ-NNN`) : rien à tracer")
        return out
    entries: list[dict[str, Any]] = []
    try:
        entries = (journal or AuthorityJournal()).read()
    except JournalError as exc:
        out.notes.append(f"journal d'autorité illisible ({exc}) : validations et dérive inconnues")
    moa_name = rg.gate_mode(project).moa
    tasks = {t.id: t for t in (service.list_tasks() if service.has_ledger else [])}
    for req in found.requirements:
        if not req.planned:
            continue
        key = rg.normalize_key(req.id, chantier)
        view = moa.requirement_view(project, key, service=service, journal=journal)
        entry = rg.validation_in(entries, str(project), key, view.fingerprint, moa_name)[0] if view.validated else None
        trace = RequirementTrace(
            key,
            view.title,
            view.priority,
            view.criteria,
            _stage(view),
            view.state,
            view.validated,
            str((entry or {}).get("moa") or ""),
            rg.safe_text((entry or {}).get("ts") or ""),
            view.reason,
            view.verdict,
        )
        for proof in view.tasks:
            task = tasks.get(proof.id)
            row = TaskTrace(
                proof.id,
                proof.title,
                proof.status,
                proof.delivered,
                verdict=proof.verdict,
                evidence_pack=proof.pack,
                merged=proof.merged,
                checks=_checks(task),
            )
            try:
                events = service.ledger.events_for(proof.id)
            except GrimoireMissionError as exc:
                events = []
                row.notes.append(f"événements illisibles : {exc}")
            _fill_code(row, events, reader)
            trace.tasks.append(row)
        trace.gaps = _gaps(trace, view)
        trace.drift = _drift(entries, str(project), key, view, trace)
        out.requirements.append(trace)
    return out


def _checks(task: MissionTask | None) -> list[str]:
    return [rg.safe_text(c.cmd) for c in task.checks] if task is not None else []
