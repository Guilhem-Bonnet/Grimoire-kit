"""La carte du projet (``grimoire steward map``) : déterministe, sans LLM, schéma ``grimoire-steward-map/v1``.

Elle rassemble ce qu'un humain ou un agent doit voir pour dire ce qui reste réellement à faire :

- les tâches du Mission Ledger (état, critères, dernière activité, claim, session et sa vivacité, liens) ;
- les branches et worktrees : fusionnée dans la cible **par comparaison d'arbres** (une fusion squash
  ne laisse pas la branche ancêtre de la cible ; on calcule la fusion avec ``git merge-tree`` et on
  compare l'arbre obtenu à celui de la cible), avance/retard, dernier commit, orpheline ;
- les PR et issues ouvertes (``gh``) ;
- des **signaux** calculés, jamais des verdicts : PR inactive dont la session n'est plus vivante,
  tâche ouverte dont l'issue liée est fermée, tâche ouverte citée par un commit de la cible (un
  identifiant homonyme peut tromper : c'est un indice à vérifier), conflit limité au CHANGELOG.

La vivacité est celle du kit (:func:`grimoire.missions.liveness.liveness`, quatre valeurs) : « inconnu »
n'est pas « morte », mais ce n'est pas non plus une session vivante.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from grimoire.missions.lease import parse_instant
from grimoire.missions.liveness import INACTIF, VIVANT, claimed_at, liveness
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.steward import MAP_SCHEMA
from grimoire.steward.sources import (
    LOG_LIMIT,
    NON_MESURE,
    PARTIELLE,
    BranchRef,
    MergeProbe,
    Sources,
    ahead_behind,
    commit_on_target,
    gh_json,
    hours_since,
    list_branches,
    list_worktrees,
    merge_probe,
    resolve_commit,
    resolve_sha,
    scratch_objects,
    target_log,
)

__all__ = [
    "DEFAULT_INACTIVITY_HOURS",
    "ISSUE_LIMIT",
    "MERGED_LIMIT",
    "PR_LIMIT",
    "LedgerView",
    "MapContext",
    "build_map",
    "ci_state",
    "find_element",
    "id_pattern",
    "issue_refs",
    "owners_of_branch",
    "partial",
    "read_ledger",
    "session_alive",
    "summarize",
]

#: Au-delà, une PR sans activité dont la session n'est plus vivante est signalée.
DEFAULT_INACTIVITY_HOURS = 48
_CLOSED = frozenset({TaskState.CLOSED, TaskState.CANCELLED})
_DONE = _CLOSED | {TaskState.FAILED}
_HELD = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING})
_ISSUE_REF = re.compile(r"(?<![\w/&#])#(\d{1,6})\b|/(?:issues|pull)/(\d{1,6})\b")
_PR_FIELDS = (
    "number,title,headRefName,headRefOid,baseRefName,author,createdAt,updatedAt,"
    "isDraft,mergeable,mergeStateStatus,statusCheckRollup,url"
)
_ISSUE_FIELDS = "number,title,labels,updatedAt,url"


def issue_refs(*texts: str) -> list[int]:
    """Numéros ``#N`` (ou URL ``/issues/N``, ``/pull/N``) cités, triés et sans doublon."""
    found: set[int] = set()
    for text in texts:
        for match in _ISSUE_REF.finditer(text or ""):
            found.add(int(match.group(1) or match.group(2)))
    return sorted(found)


def id_pattern(task_id: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(task_id)}(?![\w-])")


# ── Ledger ────────────────────────────────────────────────────────────────────


@dataclass
class LedgerView:
    tasks: dict[str, MissionTask] = field(default_factory=dict)
    last_activity: dict[str, str] = field(default_factory=dict)
    #: task_id → ``{"id", "hote", "vivacite": {...}, "journal"}`` ; absent = aucune session connue.
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def is_live(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        return task is not None and task.status not in _DONE


def read_ledger(src: Sources) -> tuple[LedgerView | None, str]:
    """Le ledger en lecture seule (aucun instantané écrit, aucun dossier créé) ; ``(None, raison)`` sinon."""
    from grimoire.core.standard_state import suspended_state_writes
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.service import TaskService
    from grimoire.tools.workspace_portfolio import sessions_index

    service = TaskService(src.root)  # ne touche pas le disque : seul l'accès à ``.ledger`` ouvrirait le journal
    if not service.has_ledger:
        return None, "aucun Mission Ledger (events.jsonl absent)"
    view = LedgerView()
    try:
        with suspended_state_writes():
            ledger = MissionLedger(service.ledger_root)
            for task in ledger.list_tasks():
                view.tasks[task.id] = task
            for event in ledger.list_events():
                if event.entity_id in view.tasks and event.created_at > view.last_activity.get(event.entity_id, ""):
                    view.last_activity[event.entity_id] = event.created_at
            journals = sessions_index(service.home_root)
            probed: Mapping[str, str] | None = None
            probe_done = False
            for task in view.tasks.values():
                view.last_activity.setdefault(task.id, task.created_at)
                session_id, journal = _session_of(task, journals)
                if not session_id:
                    continue
                if src.probe is not None and not probe_done:
                    probed, probe_done = src.probe(), True  # une fois, seulement s'il y a une session à juger
                held = task.status in _HELD
                state = liveness(
                    now=src.now,
                    lease=ledger.lease_file(task.id) if held else None,
                    journal_at=parse_instant((journal or {}).get("updated_at")),
                    ended=bool(journal and journal.get("ended")),
                    probe_status=(probed or {}).get(session_id),
                    claimed_at=claimed_at(ledger, task.id) if held else None,
                )
                view.sessions[task.id] = {
                    "id": session_id,
                    "hote": (task.claim.session_host if task.claim else "") or (journal or {}).get("host") or "",
                    "vivacite": state.to_dict(),
                    "journal": (journal or {}).get("journal"),
                }
    except Exception as exc:  # un journal corrompu se dit, il ne fait pas tomber la carte
        return None, f"ledger illisible : {exc}"
    return view, ""


def _session_of(task: MissionTask, journals: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    """La session du claim d'abord, sinon le journal le plus récent qui nomme la tâche (même règle que le cockpit)."""
    session_id = task.claim.session_id if task.claim else ""
    if session_id:
        return session_id, journals.get(session_id)
    candidates = [j for j in journals.values() if j.get("task_id") == task.id]
    if not candidates:
        return "", None
    journal = max(candidates, key=lambda j: str(j.get("updated_at") or ""))
    return str(journal["session_id"]), journal


def session_alive(view: LedgerView | None, task_ids: list[str]) -> tuple[bool, list[dict[str, Any]]]:
    """Une des sessions des tâches *task_ids* est-elle vivante (``vivant`` ou ``inactif``) ? Avec le détail."""
    detail: list[dict[str, Any]] = []
    alive = False
    for task_id in task_ids:
        session = (view.sessions if view else {}).get(task_id)
        if session is None:
            detail.append({"tache": task_id, "session": None, "vivacite": "aucune session connue"})
            continue
        state = session["vivacite"]["state"]
        alive = alive or state in (VIVANT, INACTIF)
        detail.append({"tache": task_id, "session": session["id"], "vivacite": state})
    return alive, detail


def owners_of_branch(view: LedgerView | None, branch: str, title: str = "", number: int | None = None) -> list[str]:
    """Tâches qui portent une branche ou une PR : ``task/<id>``, identifiant cité dans la branche ou le titre, ou ``#N`` lié."""
    if view is None:
        return []
    name = branch.split("/", 1)[1] if branch.startswith("origin/") else branch
    owners = []
    for task_id, task in view.tasks.items():
        pattern = id_pattern(task_id)
        by_name = name == f"task/{task_id}" or pattern.search(name) or (title and pattern.search(title))
        if by_name or (number is not None and number in issue_refs(task.title, task.description)):
            owners.append(task_id)
    return sorted(owners)


def _task_entry(task: MissionTask, view: LedgerView) -> dict[str, Any]:
    claim = task.claim
    return {
        "ref": f"tache:{task.id}",
        "id": task.id,
        "titre": task.title,
        "etat": task.status_label,
        "ouverte": task.status not in _CLOSED,
        "criteres": list(task.acceptance),
        "derniere_activite": view.last_activity.get(task.id, task.created_at),
        "claim": None
        if claim is None
        else {
            "acteur": claim.actor_id,
            "hote": claim.host_id,
            "session": claim.session_id,
            "expire_le": claim.expires_at,
        },
        "session": view.sessions.get(task.id),
        "liens": {
            "issues": issue_refs(task.title, task.description, *task.acceptance, *task.expected_evidence),
            "dependances": [d.to_dict() for d in task.dependencies],
        },
    }


# ── GitHub ────────────────────────────────────────────────────────────────────


def ci_state(rollup: Any) -> str:
    """``succes``, ``echec``, ``en_cours`` ou ``aucune`` depuis ``statusCheckRollup``."""
    if not isinstance(rollup, list) or not rollup:
        return "aucune"
    states: set[str] = set()
    for check in rollup:
        if not isinstance(check, dict):
            continue
        if check.get("__typename") == "StatusContext":
            value = str(check.get("state") or "").upper()
            states.add({"SUCCESS": "ok", "PENDING": "run", "EXPECTED": "run"}.get(value, "ko"))
        elif str(check.get("status") or "").upper() != "COMPLETED":
            states.add("run")
        else:
            value = str(check.get("conclusion") or "").upper()
            states.add("ok" if value in ("SUCCESS", "SKIPPED", "NEUTRAL") else "ko")
    if "ko" in states:
        return "echec"
    return "en_cours" if "run" in states else "succes"


# ── Assemblage ────────────────────────────────────────────────────────────────

#: Limites des listes ``gh`` : atteinte, la liste est « partielle » et son absence ne prouve rien.
PR_LIMIT = 200
ISSUE_LIMIT = 500
MERGED_LIMIT = 1000


def partial(items: list[Any], limit: int) -> bool:
    return len(items) >= limit


@dataclass
class MapContext:
    """Ce que la carte et la validation partagent : la cible, ses arbres, les PR fusionnées, les fusions calculées.

    :meth:`fusion` est la seule définition de « fusionnée » : la carte l'affiche, la validation
    l'exige pour qu'une branche soit ``livree``.
    """

    src: Sources
    view: LedgerView | None
    target_sha: str = ""
    target_tree: str = ""
    env: Mapping[str, str] | None = None
    signals: list[dict[str, Any]] = field(default_factory=list)
    probes: dict[str, MergeProbe] = field(default_factory=dict)
    #: Arbres des derniers commits de la cible (hors son arbre courant) → commit qui le porte.
    older_trees: dict[str, str] = field(default_factory=dict)
    #: Tête → PR fusionnée (``number``, ``baseRefName``, ``mergeCommit``) ; vide si ``gh`` ne répond pas.
    merged: dict[str, dict[str, Any]] = field(default_factory=dict)
    merged_ok: dict[str, int | None] = field(default_factory=dict)
    #: Ce qui a été lu jusqu'à sa limite : l'absence n'y prouve rien.
    truncations: list[str] = field(default_factory=list)

    def prepare(self) -> None:
        """Résout la cible, ses arbres et les PR fusionnées. Sans cible, tout ce qui en dépend reste non mesuré."""
        target_sha = resolve_commit(self.src, self.src.target)
        if not target_sha:
            return
        self.target_sha = target_sha
        tree = self.src.git("rev-parse", f"{target_sha}^{{tree}}")
        self.target_tree = tree.stdout.strip() if tree.returncode == 0 else ""
        trees = self.src.git("log", f"--max-count={LOG_LIMIT}", "--format=%T %H", target_sha)
        if trees.returncode == 0:
            pairs = [line.split() for line in trees.stdout.splitlines() if len(line.split()) == 2]
            for tree_id, commit in reversed(pairs):  # le plus récent gagne
                self.older_trees[tree_id] = commit
            self.older_trees.pop(self.target_tree, None)
            if partial(pairs, LOG_LIMIT):
                self.truncations.append(f"arbres de la cible : {LOG_LIMIT} derniers commits seulement")
        fields = "number,headRefOid,baseRefName,mergeCommit"
        data, _reason = gh_json(self.src, "pr", "list", "--state", "merged", "--limit", str(MERGED_LIMIT), "--json", fields)
        if isinstance(data, list):
            for pr in data:
                if isinstance(pr, dict) and pr.get("headRefOid"):
                    self.merged[str(pr["headRefOid"])] = pr
            if partial(data, MERGED_LIMIT):
                self.truncations.append(f"PR fusionnées : {MERGED_LIMIT} dernières seulement (une fusion plus ancienne n'est pas vue)")

    def probe(self, sha: str) -> MergeProbe:
        if sha not in self.probes:
            self.probes[sha] = merge_probe(self.src, self.target_sha, self.target_tree, sha, self.env)
        return self.probes[sha]

    def merged_pr(self, sha: str) -> int | None:
        """Le numéro de la PR fusionnée **dans la cible** dont *sha* est la tête — base = cible et commit de fusion sur la cible."""
        if sha not in self.merged_ok:
            pr = self.merged.get(sha)
            number: int | None = None
            if pr is not None and str(pr.get("baseRefName") or "") == self.src.target_branch:
                merge = pr.get("mergeCommit") if isinstance(pr.get("mergeCommit"), dict) else {}
                oid = str((merge or {}).get("oid") or "")
                if oid and commit_on_target(self.src, self.target_sha, oid)[0]:
                    number = int(pr.get("number") or 0) or None
            self.merged_ok[sha] = number
        return self.merged_ok[sha]

    def fusion(self, sha: str) -> tuple[dict[str, Any], dict[str, Any], tuple[int, int] | None]:
        """``(fusion, conflit, (avance, retard))`` d'un commit contre la cible.

        ``fusion.etat`` : ``vide`` (aucun commit d'avance : rien à fusionner), ``fusionnee`` (``par`` :
        ``pr #N`` fusionnée dans la cible avec cette tête, ou ``fusion_sans_apport`` : la fusion ne
        changerait rien à la cible), ``etat_anterieur`` (son arbre est celui d'un commit **ancien** de
        la cible : la fusionner reviendrait en arrière, ce n'est pas une preuve de livraison),
        ``non_fusionnee`` ou :data:`NON_MESURE`.
        """
        none = {"etat": "aucun", "fichiers": []}
        counts = ahead_behind(self.src, self.target_sha, sha) if self.target_sha else None
        if counts is None:
            return {"etat": NON_MESURE, "par": ""}, {"etat": NON_MESURE, "fichiers": []}, None
        if counts[0] == 0:
            return {"etat": "vide", "par": "aucun commit d'avance"}, none, counts
        number = self.merged_pr(sha)
        if number is not None:
            return {"etat": "fusionnee", "par": f"pr #{number}"}, none, counts
        probe = self.probe(sha)
        conflict = {"etat": probe.conflict_kind, "fichiers": list(probe.files)}
        if probe.status == NON_MESURE:
            return {"etat": NON_MESURE, "par": "", "raison": probe.reason}, conflict, counts
        if probe.same_tree:
            return {"etat": "fusionnee", "par": "fusion_sans_apport"}, conflict, counts
        tree = self.src.git("rev-parse", f"{sha}^{{tree}}").stdout.strip()
        if tree in self.older_trees:
            older = self.older_trees[tree]
            return {"etat": "etat_anterieur", "par": f"revient à l'état de {older[:12]}"}, conflict, counts
        return {"etat": "non_fusionnee", "par": ""}, conflict, counts

    def signal(self, kind: str, ref: str, detail: str, **extra: Any) -> None:
        self.signals.append({"type": kind, "ref": ref, "detail": detail, **extra})


def _branch_entry(ctx: MapContext, ref: BranchRef, dirty: dict[str, bool | None]) -> dict[str, Any]:
    fusion, conflict, counts = ctx.fusion(ref.sha)
    name = ref.name.split("/", 1)[1] if ref.remote and "/" in ref.name else ref.name
    task_id = name.removeprefix("task/") if name.startswith("task/") else ""
    orphan = bool(task_id) and ctx.view is not None and not ctx.view.is_live(task_id)
    entry: dict[str, Any] = {
        "ref": f"branche:{ref.name}",
        "nom": ref.name,
        "distante": ref.remote,
        "sha": ref.sha,
        "dernier_commit": {"date": ref.committed_at, "sujet": ref.subject},
        "en_avance": counts[0] if counts else NON_MESURE,
        "en_retard": counts[1] if counts else NON_MESURE,
        "fusion": fusion,
        "conflit": conflict,
        "tache": task_id or None,
        "orpheline": orphan if ctx.view is not None or not task_id else NON_MESURE,
        "worktree_sale": None if ref.remote else dirty.get(ref.name, False),
    }
    if orphan:
        ctx.signal("branche_orpheline", entry["ref"], f"branche de tâche sans tâche vivante ({task_id})", tache=task_id)
    if fusion["etat"] == "fusionnee":
        ctx.signal("branche_fusionnee_sans_etre_ancetre", entry["ref"], f"contenu déjà sur la cible ({fusion['par']})")
    if fusion["etat"] == "etat_anterieur":
        ctx.signal("branche_etat_anterieur", entry["ref"], f"la fusionner reviendrait en arrière ({fusion['par']})")
    if conflict["etat"] in ("changelog_seul", "autre") and not ref.remote:
        ctx.signal(f"conflit_{conflict['etat']}", entry["ref"], ", ".join(conflict["fichiers"]))
    return entry


def _section_git(ctx: MapContext, include_remote: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    branches: dict[str, Any] = {"mesure": False, "raison": "cible introuvable", "elements": []}
    worktrees: dict[str, Any] = {"mesure": False, "raison": "git worktree list a échoué", "elements": []}
    entries = list_worktrees(ctx.src)
    dirty: dict[str, bool | None] = {}
    if entries is not None:
        worktrees = {
            "mesure": True,
            "elements": [
                {
                    "ref": f"worktree:{w.path}",
                    "chemin": w.path,
                    "branche": w.branch or None,
                    "head": w.head,
                    "verrouille": w.locked,
                    "elagable": w.prunable,
                    "sale": w.dirty if w.dirty is not None else NON_MESURE,
                }
                for w in sorted(entries, key=lambda w: w.path)
            ],
        }
        for w in entries:
            if w.branch:
                dirty[w.branch] = True if dirty.get(w.branch) else w.dirty
        for w in worktrees["elements"]:
            if w["elagable"]:
                ctx.signal("worktree_elagable", w["ref"], "dossier du worktree absent (git worktree prune le retirerait)")
            if w["sale"] is True:
                ctx.signal(
                    "worktree_sale",
                    w["ref"],
                    "fichiers modifiés ou non suivis : du travail non commité",
                    branche=f"branche:{w['branche']}" if w["branche"] else None,
                )
    if not ctx.target_sha:
        return branches, worktrees
    refs = list_branches(ctx.src)
    if refs is None:
        branches["raison"] = "git for-each-ref a échoué"
        return branches, worktrees
    # Les distantes du seul remote de la cible (``origin`` pour ``origin/main``) : un remote amont étranger
    # (historique sans ancêtre commun) n'est pas du travail de ce projet.
    remote = f"{ctx.src.remote}/" if ctx.src.remote else ""
    keep = [
        r
        for r in refs
        if r.name != ctx.src.target and (not r.remote or (include_remote and remote and r.name.startswith(remote)))
    ]
    branches = {
        "mesure": True,
        "distantes_incluses": include_remote,
        "elements": [_branch_entry(ctx, r, dirty) for r in keep],
    }
    return branches, worktrees


def _pr_entry(ctx: MapContext, pr: dict[str, Any], hours: int) -> dict[str, Any]:
    number = int(pr.get("number") or 0)
    head = str(pr.get("headRefName") or "")
    updated = parse_instant(pr.get("updatedAt"))
    owners = owners_of_branch(ctx.view, head, str(pr.get("title") or ""), number)
    alive, sessions = session_alive(ctx.view, owners)
    idle = hours_since(updated, ctx.src.now)
    head_sha = str(pr.get("headRefOid") or "")
    local_head = resolve_sha(ctx.src, head_sha) if head_sha else None
    conflict: dict[str, Any]
    if ctx.target_sha and local_head:
        probe = ctx.probe(local_head)
        conflict = {"etat": probe.conflict_kind, "fichiers": list(probe.files), "sha": local_head, "source": "git merge-tree local"}
    else:
        reason = "tête de la PR absente du dépôt local (git fetch)" if ctx.target_sha else "cible introuvable"
        conflict = {"etat": NON_MESURE, "fichiers": [], "sha": head_sha or None, "raison": reason}
        if str(pr.get("mergeable") or "") == "CONFLICTING":
            conflict["gh_mergeable"] = "CONFLICTING"
    author = pr.get("author") if isinstance(pr.get("author"), dict) else {}
    entry: dict[str, Any] = {
        "ref": f"pr:{number}",
        "numero": number,
        "titre": pr.get("title") or "",
        "url": pr.get("url") or "",
        "auteur": (author or {}).get("login") or "",
        "branche": head,
        "base": pr.get("baseRefName") or "",
        "brouillon": bool(pr.get("isDraft")),
        "ci": ci_state(pr.get("statusCheckRollup")),
        "mergeable": pr.get("mergeable") or NON_MESURE,
        "merge_state": pr.get("mergeStateStatus") or NON_MESURE,
        "derniere_activite": pr.get("updatedAt") or NON_MESURE,
        "heures_sans_activite": idle if idle is not None else NON_MESURE,
        "taches": owners,
        "sessions": sessions,
        "session_vivante": alive,
        "conflit": conflict,
    }
    if idle is not None and idle >= hours and not alive:
        ctx.signal(
            "pr_inactive_sans_session_vivante",
            entry["ref"],
            f"aucune activité depuis {idle} h, aucune session vivante (un indice : « inconnu » n'est pas « morte »)",
            heures=idle,
        )
    if conflict["etat"] in ("changelog_seul", "autre"):
        ctx.signal(f"conflit_{conflict['etat']}", entry["ref"], ", ".join(conflict["fichiers"]))
    return entry


def _section_github(ctx: MapContext, hours: int) -> tuple[dict[str, Any], dict[str, Any]]:
    prs: dict[str, Any] = {"mesure": False, "elements": []}
    data, reason = gh_json(ctx.src, "pr", "list", "--state", "open", "--limit", str(PR_LIMIT), "--json", _PR_FIELDS)
    if isinstance(data, list):
        items = sorted((p for p in data if isinstance(p, dict)), key=lambda p: int(p.get("number") or 0))
        prs = {"mesure": True, "elements": [_pr_entry(ctx, p, hours) for p in items]}
        if partial(data, PR_LIMIT):
            prs.update(mesure=PARTIELLE, raison=f"{PR_LIMIT} PR lues, la limite : d'autres peuvent manquer")
    else:
        prs["raison"] = reason or "gh : réponse inattendue"
    issues: dict[str, Any] = {"mesure": False, "elements": []}
    data, reason = gh_json(ctx.src, "issue", "list", "--state", "open", "--limit", str(ISSUE_LIMIT), "--json", _ISSUE_FIELDS)
    if isinstance(data, list):
        issues = {
            "mesure": True,
            "elements": [
                {
                    "ref": f"issue:{i.get('number')}",
                    "numero": i.get("number"),
                    "titre": i.get("title") or "",
                    "labels": sorted(str(lb.get("name")) for lb in i.get("labels") or [] if isinstance(lb, dict)),
                    "derniere_activite": i.get("updatedAt") or NON_MESURE,
                    "url": i.get("url") or "",
                }
                for i in sorted((i for i in data if isinstance(i, dict)), key=lambda i: int(i.get("number") or 0))
            ],
        }
        if partial(data, ISSUE_LIMIT):
            issues.update(mesure=PARTIELLE, raison=f"{ISSUE_LIMIT} issues lues, la limite : d'autres peuvent manquer")
    else:
        issues["raison"] = reason or "gh : réponse inattendue"
    return prs, issues


def _linked_states(ctx: MapContext, numbers: set[int], open_numbers: set[int] | None) -> dict[int, dict[str, Any]]:
    """État des issues et PR liées (``gh issue view`` rend aussi une PR) ; une ouverte connue n'est pas relue."""
    states: dict[int, dict[str, Any]] = {}
    for number in sorted(numbers):
        if open_numbers is not None and number in open_numbers:
            states[number] = {"etat": "OPEN"}
            continue
        data, reason = gh_json(ctx.src, "issue", "view", str(number), "--json", "number,state,stateReason,closedAt,title")
        states[number] = (
            {
                "etat": str(data.get("state") or ""),
                "raison_fermeture": data.get("stateReason") or "",
                "fermee_le": data.get("closedAt"),
                "titre": data.get("title") or "",
            }
            if isinstance(data, dict)
            else {"etat": NON_MESURE, "raison": reason}
        )
    return states


def _task_signals(ctx: MapContext, tasks: list[dict[str, Any]], issues: dict[str, Any]) -> None:
    open_tasks = [t for t in tasks if t["ouverte"]]
    open_numbers = {int(i["numero"]) for i in issues["elements"]} if issues["mesure"] else None
    linked = {n for t in open_tasks for n in t["liens"]["issues"]}
    states = _linked_states(ctx, linked, open_numbers) if issues["mesure"] else {}
    for task in open_tasks:
        task["liens"]["etats"] = {str(n): states.get(n, {"etat": NON_MESURE}) for n in task["liens"]["issues"]}
        for n in task["liens"]["issues"]:
            if states.get(n, {}).get("etat") in ("CLOSED", "MERGED"):
                ctx.signal(
                    "tache_ouverte_issue_fermee",
                    task["ref"],
                    f"#{n} est {'fusionnée' if states[n]['etat'] == 'MERGED' else 'fermée'}",
                    numero=n,
                )
    if not ctx.target_sha:
        return
    log, truncated = target_log(ctx.src, ctx.target_sha)
    if truncated:
        ctx.truncations.append(f"citations d'identifiants : {LOG_LIMIT} derniers commits de la cible seulement")
    for task in open_tasks:
        pattern = id_pattern(task["id"])
        hits = [{"sha": sha, "sujet": subject} for sha, subject, body in log if pattern.search(subject) or pattern.search(body)]
        task["commits_citant"] = hits[:20]
        if hits:
            ctx.signal(
                "tache_ouverte_citee_par_commit",
                task["ref"],
                f"{len(hits)} commit(s) de la cible citent l'identifiant — vérifier le code, un identifiant homonyme trompe",
                commits=[h["sha"][:12] for h in hits[:5]],
            )


def build_map(
    src: Sources,
    *,
    inactivity_hours: int = DEFAULT_INACTIVITY_HOURS,
    include_remote: bool = True,
) -> dict[str, Any]:
    """La carte du projet. Aucune écriture hors du magasin d'objets temporaire (retiré à la sortie)."""
    view, ledger_reason = read_ledger(src)
    ctx = MapContext(src=src, view=view)
    ctx.prepare()
    tasks = [_task_entry(t, view) for t in sorted(view.tasks.values(), key=lambda t: t.id)] if view else []
    with scratch_objects(src) as env:
        ctx.env = env if ctx.target_tree else None
        branches, worktrees = _section_git(ctx, include_remote)
        prs, issues = _section_github(ctx, inactivity_hours)
    _task_signals(ctx, tasks, issues)
    payload: dict[str, Any] = {
        "$schema": MAP_SCHEMA,
        "genere_le": src.now.isoformat(),
        "projet": str(src.root),
        "cible": {"ref": src.target, "sha": ctx.target_sha or NON_MESURE},
        "parametres": {"inactivite_heures": inactivity_hours, "distantes_incluses": include_remote},
        "taches": {"mesure": view is not None, "elements": tasks, **({"raison": ledger_reason} if view is None else {})},
        "branches": branches,
        "worktrees": worktrees,
        "prs": prs,
        "issues": issues,
        "troncatures": ctx.truncations,
        "signaux": sorted(ctx.signals, key=lambda s: (s["type"], s["ref"])),
    }
    payload["resume"] = summarize(payload)
    return payload


def summarize(payload: Mapping[str, Any]) -> dict[str, Any]:
    def count(section: str) -> int | str:
        part = payload.get(section) or {}
        return len(part.get("elements") or []) if part.get("mesure") else NON_MESURE

    tasks = (payload.get("taches") or {}).get("elements") or []
    by_type: dict[str, int] = {}
    for signal in payload.get("signaux") or []:
        by_type[signal["type"]] = by_type.get(signal["type"], 0) + 1
    return {
        "taches": count("taches"),
        "taches_ouvertes": sum(1 for t in tasks if t.get("ouverte")) if (payload.get("taches") or {}).get("mesure") else NON_MESURE,
        "branches": count("branches"),
        "worktrees": count("worktrees"),
        "prs": count("prs"),
        "issues": count("issues"),
        "signaux": dict(sorted(by_type.items())),
    }


def find_element(payload: Mapping[str, Any], ref: str) -> dict[str, Any] | None:
    for section in ("taches", "branches", "worktrees", "prs", "issues"):
        for element in (payload.get(section) or {}).get("elements") or []:
            if element.get("ref") == ref:
                return dict(element)
    return None
