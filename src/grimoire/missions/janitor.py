"""Le janitor : l'étape du tick qui nettoie une tâche arrivée à un état terminal (#876, lot 2, verrou 12).

Actif dès qu'un rôle lance des sessions (le rôle ``worker`` est activé, ou le ledger porte un lancement). Pour chaque
tâche ``closed``, ``cancelled`` ou ``failed`` qui a un worktree, une session lancée ou un créneau CI, dans cet ordre :

1. ``ci_slot`` — rend le créneau ``ci.runners`` (``capacity.released``, comme ``task.ci_finished``) ;
2. ``session`` — ``claude stop <id>`` sur la session détachée (la conversation est gardée) ;
3. ``worktree`` — ``git worktree remove`` **sans** ``--force`` : un arbre sale reste là, et l'échec est rapporté ;
4. ``branch_local`` et ``branch_remote`` — suppression de ``task/<id>``, **seulement si fusionnée** : ``gh pr view`` dit
   ``MERGED``, ou (aucune PR) le ledger a un ``task.merged`` pour cette tête qui est dans la cible. Une vérification
   qui échoue ou qui dit « pas fusionnée » ne supprime rien (échec fermé) ;
5. ``evidence`` — copie du dossier de preuve sous ``_grimoire-output/archive/evidence/<id>`` (jamais déplacé) ;
6. ``issue`` — derrière ``janitor.close_issue`` : ferme l'issue citée par ``Closes #N`` dans la description, fusionnée seulement ;
7. ``breaker`` — une tâche close remet le compteur du disjoncteur à zéro, une tâche en échec compte un échec de session.

**Idempotent et journalisé.** Chaque action écrit ``janitor.action`` (``done``, ``skipped``, ``refused``, ``failed``) au ledger
et n'est pas refaite une fois ``done``/``skipped`` ; quand plus rien ne reste à faire, ``janitor.swept`` clôt la tâche pour le
tick suivant. **Un échec ne bloque pas le tick** : l'action est rapportée (``failed``), rejouée au tick d'après, et les autres
tâches suivent leur chemin. Une branche qu'on ne peut pas dire fusionnée reste (``refused``) : pour une tâche ``closed`` le
janitor réessaie (la PR peut se fusionner plus tard), pour ``cancelled`` ou ``failed`` la branche est gardée définitivement.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.core.standard_generation import EVIDENCE_DIR
from grimoire.missions.admission import CAPACITY_RELEASED_EVENT, derive_view, load_capacity, release_capacity
from grimoire.missions.breaker import record_failure, record_success
from grimoire.missions.launcher import LAUNCHED_EVENT, Exec, default_exec, launched_sessions
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import MERGED_EVENT, TASK_BRANCH_PREFIX, WORKTREE_CREATED_EVENT, find_task_worktree

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.service import TaskService

__all__ = ["ACTION_EVENT", "ARCHIVE_DIR", "JANITOR_ACTOR", "SWEPT_EVENT", "Janitor", "JanitorOutcome", "janitor_active", "pending_cleanup"]

ACTION_EVENT = "janitor.action"
SWEPT_EVENT = "janitor.swept"
JANITOR_ACTOR = "janitor"
ARCHIVE_DIR = "_grimoire-output/archive/evidence"
_TERMINAL = (TaskState.CLOSED, TaskState.CANCELLED, TaskState.FAILED)
_FINAL = frozenset({"done", "skipped"})
_ISSUE_LINK = re.compile(r"\b(?:closes|closed|fixes|fixed|resolves|resolved)\s*:?\s*#(\d+)", re.IGNORECASE)
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_GONE = ("no such", "not found", "unknown session", "introuvable", "no session")


@dataclass(frozen=True, slots=True)
class JanitorOutcome:
    """Une action du janitor : ``done``, ``skipped`` (rien à faire), ``refused`` (garde fermée), ``failed`` ou ``planned`` (plan)."""

    task_id: str
    action: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail}


def janitor_active(service: TaskService, policy: OrchestratorPolicy | None) -> bool:
    """Vrai quand un rôle lance des sessions : le worker est activé, ou le ledger porte déjà un lancement (à nettoyer)."""
    if policy is not None and policy.worker.enabled:
        return True
    return bool(launched_sessions(service))


def _git(cwd: Path, *args: str, push: bool = False) -> subprocess.CompletedProcess[str]:
    """Le git durci du tick (:mod:`grimoire.core.safe_git`) : ni hooks, ni exécutable venu de la configuration du dépôt."""
    from grimoire.core.safe_git import PUSH_ARGS, run

    return run(args, cwd=cwd, extra_args=PUSH_ARGS if push else ())


def _after_terminal(events: list[Any]) -> list[Any]:
    """Les événements d'une tâche depuis son dernier passage à un état terminal (une réouverture remet tout à neuf)."""
    terminal = {s.value for s in _TERMINAL}
    last = -1
    for i, e in enumerate(events):
        if e.event_type == "task.transitioned" and e.payload.get("to_state") in terminal:
            last = i
    return events[last + 1:]


def _window(service: TaskService, task_id: str) -> list[Any]:
    return _after_terminal(service.ledger.events_for(task_id))


def _candidates(service: TaskService) -> list[str]:
    """Les tâches terminales à nettoyer : un lancement, un worktree noté ou un créneau CI, et pas encore ``swept``."""
    terminal = [t for t in service.list_tasks() if t.status in _TERMINAL]
    if not terminal:
        return []
    view = derive_view(service.ledger, load_capacity(service.home_root), now=_now())
    by_task: dict[str, list[Any]] = {}
    for event in service.ledger.list_events():
        by_task.setdefault(event.entity_id, []).append(event)
    found: list[str] = []
    for task in terminal:
        events = by_task.get(task.id, [])
        known = (
            task.id in view.ci_occupants
            or any(e.event_type in (LAUNCHED_EVENT, WORKTREE_CREATED_EVENT) for e in events)
        )
        if known and not any(e.event_type == SWEPT_EVENT for e in _after_terminal(events)):
            found.append(task.id)
    return found


def _now() -> datetime:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def pending_cleanup(service: TaskService) -> tuple[JanitorOutcome, ...]:
    """Le plan à blanc : les tâches que le janitor nettoierait, sans rien toucher."""
    return tuple(JanitorOutcome(tid, "sweep", "planned", "nettoyage à faire") for tid in _candidates(service))


class Janitor:
    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec, claude_bin: str = "claude",
        remote: str = "origin",
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin
        self._remote = remote
        self._repo = service.home_root

    def sweep(self) -> tuple[JanitorOutcome, ...]:
        """Nettoie chaque tâche terminale ; une exception d'une action devient un ``failed`` rapporté, jamais une panne du tick."""
        outcomes: list[JanitorOutcome] = []
        for tid in _candidates(self._service):
            outcomes.extend(self._sweep_task(tid))
        return tuple(outcomes)

    # -- une tâche -----------------------------------------------------------

    def _sweep_task(self, tid: str) -> list[JanitorOutcome]:
        task = self._service.require(tid)
        done_before = {
            str(e.payload.get("action")) for e in _window(self._service, tid)
            if e.event_type == ACTION_EVENT and e.payload.get("status") in _FINAL
        }
        merged: list[tuple[str, str, str]] = []  # preuve de fusion, calculée une fois si une action en a besoin

        def proof() -> tuple[str, str, str]:
            if not merged:
                merged.append(self._merged_proof(tid))
            return merged[0]

        steps = (
            ("ci_slot", self._ci_slot),
            ("session", self._session),
            ("worktree", self._worktree),
            ("branch_local", lambda t: self._branch_local(t, proof())),
            ("branch_remote", lambda t: self._branch_remote(t, proof())),
            ("evidence", self._evidence),
            ("issue", lambda t: self._issue(t, proof())),
            ("breaker", self._breaker),
        )
        outcomes: list[JanitorOutcome] = []
        open_work = False
        for action, step in steps:
            if action in done_before:
                continue
            try:
                status, detail = step(tid)
            except Exception as exc:
                status, detail = "failed", f"{type(exc).__name__}: {exc}"
            outcomes.append(JanitorOutcome(tid, action, status, detail))
            self._journal(tid, action, status, detail)
            if status in ("failed", "refused") and not (status == "refused" and task.status is not TaskState.CLOSED):
                open_work = True
        if not open_work:
            self._service.ledger.append_event(SWEPT_EVENT, tid, "task", JANITOR_ACTOR, {"status": task.status.value})
        return outcomes

    def _journal(self, tid: str, action: str, status: str, detail: str) -> None:
        """Une ligne au ledger ; un échec ou un refus identique au précédent n'est pas réécrit à chaque tick."""
        if status in ("failed", "refused"):
            previous = [e for e in _window(self._service, tid) if e.event_type == ACTION_EVENT and e.payload.get("action") == action]
            if previous and previous[-1].payload.get("status") == status and previous[-1].payload.get("detail") == detail:
                return
        self._service.ledger.append_event(
            ACTION_EVENT, tid, "task", JANITOR_ACTOR, {"action": action, "status": status, "detail": detail}
        )

    # -- actions -------------------------------------------------------------

    def _ci_slot(self, tid: str) -> tuple[str, str]:
        service = self._service
        view = derive_view(service.ledger, load_capacity(service.home_root), now=_now())
        if tid not in view.ci_occupants:
            return "skipped", "aucun créneau CI tenu"
        release_capacity(service.ledger, tid, "ci", actor=JANITOR_ACTOR, reason=f"tâche {service.require(tid).status.value}")
        return "done", f"créneau CI rendu ({CAPACITY_RELEASED_EVENT})"

    def _session(self, tid: str) -> tuple[str, str]:
        launch = launched_sessions(self._service).get(tid)
        if not launch or not launch.get("session"):
            return "skipped", "aucune session lancée"
        sid = str(launch["session"])
        result = self._exec([self._claude, "stop", sid], self._repo, None)
        if result.returncode == 0:
            return "done", f"session {sid} arrêtée"
        text = (result.stderr + result.stdout).lower()
        # 124, 126, 127 : délai, non exécutable, binaire absent — la session n'est pas dite absente, elle est peut-être vivante.
        if result.returncode not in (124, 126, 127) and any(word in text for word in _GONE):
            return "done", f"session {sid} déjà absente"
        return "failed", f"claude stop {sid} a échoué ({result.returncode}) : {(result.stderr or result.stdout).strip()[:200]}"

    def _worktree(self, tid: str) -> tuple[str, str]:
        wt = find_task_worktree(self._service, tid)
        if wt is None or not wt.path.is_dir():
            if wt is not None:
                _git(self._repo, "worktree", "prune")
            return "skipped", "aucun worktree à retirer"
        done = _git(self._repo, "worktree", "remove", str(wt.path))  # sans --force : un arbre sale reste
        if done.returncode != 0:
            return "failed", f"git worktree remove refusé : {(done.stderr or done.stdout).strip()[:200]}"
        return "done", f"worktree {wt.path} retiré"

    def _branch(self, tid: str) -> str:
        """La branche que le lanceur (ou ``task_worktree``) a créée, notée au ledger ; jamais un ``task/<id>`` qu'on n'a pas posé."""
        wt = find_task_worktree(self._service, tid)
        if wt is None or wt.branch != TASK_BRANCH_PREFIX + tid:
            return ""
        return wt.branch

    def _merged_proof(self, tid: str) -> tuple[str, str, str]:
        """``("merged"|"unmerged"|"unknown", détail, sha)`` : ``gh pr view`` d'abord, le reçu de fusion locale à défaut de PR.

        *sha* est la tête de branche que la fusion couvre (``headRefOid`` de la PR) : une branche qui a avancé depuis
        n'est pas couverte, la supprimer perdrait des commits.
        """
        branch = self._branch(tid)
        if not branch:
            return "unmerged", "branche non créée par le lanceur", ""
        result = self._exec(["gh", "pr", "view", branch, "--json", "state,number,headRefOid"], self._repo, None)
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                state = str(data["state"]).upper()
            except (ValueError, KeyError, TypeError):
                return "unknown", "réponse de gh pr view illisible", ""
            if state != "MERGED":
                return "unmerged", f"PR #{data.get('number', '?')} à l'état {state}", ""
            oid = str(data.get("headRefOid") or "")
            if not _SHA.fullmatch(oid):
                return "unknown", "gh pr view ne donne pas la tête fusionnée (headRefOid)", ""
            return "merged", f"PR #{data.get('number', '?')} fusionnée à {oid[:12]}", oid
        if "no pull requests found" not in result.stderr.lower():
            return "unknown", f"gh pr view a échoué ({result.returncode}) : {result.stderr.strip()[:200]}", ""
        wt = find_task_worktree(self._service, tid)
        head = _git(self._repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}").stdout.strip()
        if wt is not None and head:
            receipt = any(
                e.payload.get("head_sha") == head for e in self._service.ledger.events_for(tid) if e.event_type == MERGED_EVENT
            )
            contained = _git(self._repo, "merge-base", "--is-ancestor", head, f"refs/heads/{wt.into}").returncode == 0
            if receipt and contained:
                return "merged", f"fusion locale attestée (task.merged), {branch} contenue dans {wt.into}", head
        return "unmerged", "aucune PR et aucune fusion locale attestée", ""

    def _guard(self, proof: tuple[str, str, str]) -> tuple[str, str] | None:
        status, detail, _sha = proof
        if status == "merged":
            return None
        return ("refused", f"branche conservée : {detail}") if status == "unmerged" else ("failed", f"fusion non vérifiée, branche conservée : {detail}")

    def _branch_local(self, tid: str, proof: tuple[str, str, str]) -> tuple[str, str]:
        branch = self._branch(tid)
        if not branch:
            return "skipped", "aucune branche posée par le lanceur"
        head = _git(self._repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").stdout.strip()
        if not head:
            return "skipped", f"{branch} n'existe pas en local"
        if (refused := self._guard(proof)) is not None:
            return refused
        # La tête locale peut être un ancêtre de la tête fusionnée : le merger réaligne la branche distante sans toucher
        # au worktree du worker (lot 4) ; rien de local n'est alors perdu.
        if head != proof[2] and _git(self._repo, "merge-base", "--is-ancestor", head, proof[2]).returncode != 0:
            return "refused", f"branche conservée : {branch} est à {head[:12]}, la fusion couvre {proof[2][:12]} (commits postérieurs)"
        done = _git(self._repo, "branch", "-D", branch)
        if done.returncode != 0:
            return "failed", f"git branch -D {branch} refusé : {(done.stderr or done.stdout).strip()[:200]}"
        return "done", f"{branch} supprimée en local ({proof[1]})"

    def _branch_remote(self, tid: str, proof: tuple[str, str, str]) -> tuple[str, str]:
        branch = self._branch(tid)
        if not branch:
            return "skipped", "aucune branche posée par le lanceur"
        remotes = _git(self._repo, "remote").stdout.split()
        if self._remote not in remotes:
            return "skipped", f"aucun remote {self._remote}"
        listing = _git(self._repo, "ls-remote", "--heads", self._remote, f"refs/heads/{branch}")
        if listing.returncode != 0:
            return "failed", f"git ls-remote {self._remote} a échoué : {listing.stderr.strip()[:200]}"
        if not listing.stdout.strip():
            return "skipped", f"{branch} n'existe pas sur {self._remote}"
        if (refused := self._guard(proof)) is not None:
            return refused
        remote_head = listing.stdout.split()[0]
        if remote_head != proof[2]:
            return "refused", f"branche conservée : {branch} est à {remote_head[:12]} sur {self._remote}, la fusion couvre {proof[2][:12]} (commits postérieurs)"
        # Le bail ferme la fenêtre entre le contrôle et la suppression : un push intermédiaire fait refuser la suppression.
        done = _git(self._repo, "push", "--no-verify", self._remote, f"--force-with-lease=refs/heads/{branch}:{proof[2]}", "--delete", branch, push=True)
        if done.returncode != 0:
            return "failed", f"git push --delete {branch} refusé : {(done.stderr or done.stdout).strip()[:200]}"
        return "done", f"{branch} supprimée sur {self._remote} ({proof[1]})"

    def _evidence(self, tid: str) -> tuple[str, str]:
        source = self._repo / EVIDENCE_DIR / tid
        if not source.is_dir():
            return "skipped", "aucun dossier de preuve"
        target = self._repo / ARCHIVE_DIR / tid
        if target.exists():
            return "skipped", f"preuve déjà archivée ({target})"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        return "done", f"preuve copiée sous {ARCHIVE_DIR}/{tid}"

    def _issue(self, tid: str, proof: tuple[str, str, str]) -> tuple[str, str]:
        if not self._policy.close_issue:
            return "skipped", "janitor.close_issue désactivé"
        match = _ISSUE_LINK.search(self._service.require(tid).description or "")
        if match is None:
            return "skipped", "aucune issue citée (Closes #N) dans la description"
        if (refused := self._guard(proof)) is not None:
            return refused
        number = match.group(1)
        result = self._exec(["gh", "issue", "close", number, "--comment", f"Tâche {tid} fusionnée et nettoyée."], self._repo, None)
        if result.returncode != 0:
            return "failed", f"gh issue close #{number} a échoué ({result.returncode}) : {result.stderr.strip()[:200]}"
        return "done", f"issue #{number} fermée"

    def _breaker(self, tid: str) -> tuple[str, str]:
        status = self._service.require(tid).status
        root = self._service.home_root
        if status is TaskState.CLOSED:
            record_success(root)
            return "done", "tâche close : compteur du disjoncteur remis à zéro"
        if status is TaskState.FAILED and tid in launched_sessions(self._service):
            state = record_failure(root, self._policy.breaker_threshold, f"{tid} : session échouée")
            return "done", f"session échouée comptée au disjoncteur ({state.failures}{', ouvert' if state.open else ''})"
        return "skipped", "rien à compter"


