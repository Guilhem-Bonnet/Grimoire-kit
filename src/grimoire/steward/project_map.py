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
    NON_MESURE,
    BranchRef,
    MergeProbe,
    Sources,
    ahead_behind,
    gh_json,
    hours_since,
    list_branches,
    list_worktrees,
    merge_probe,
    resolve_commit,
    scratch_objects,
    target_log,
)

__all__ = [
    "DEFAULT_INACTIVITY_HOURS",
    "LedgerView",
    "build_map",
    "ci_state",
    "find_element",
    "issue_refs",
    "owners_of_branch",
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


def _id_pattern(task_id: str) -> re.Pattern[str]:
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
    from grimoire.tools.workspace_portfolio import _sessions_index

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
            journals = _sessions_index(service.home_root)
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
        pattern = _id_pattern(task_id)
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


@dataclass
class _Ctx:
    src: Sources
    view: LedgerView | None
    target_sha: str = ""
    target_tree: str = ""
    env: Mapping[str, str] | None = None
    signals: list[dict[str, Any]] = field(default_factory=list)
    probes: dict[str, MergeProbe] = field(default_factory=dict)
    #: Arbres des derniers commits de la cible : une branche dont l'arbre en fait partie y a été fusionnée.
    target_trees: frozenset[str] = frozenset()
    #: Tête d'une PR fusionnée → son numéro (``gh pr list --state merged``) ; vide si ``gh`` ne répond pas.
    merged_heads: dict[str, int] = field(default_factory=dict)

    def probe(self, sha: str) -> MergeProbe:
        if sha not in self.probes:
            self.probes[sha] = merge_probe(self.src, self.target_sha, self.target_tree, sha, self.env)
        return self.probes[sha]

    def signal(self, kind: str, ref: str, detail: str, **extra: Any) -> None:
        self.signals.append({"type": kind, "ref": ref, "detail": detail, **extra})


def _fusion(ctx: _Ctx, sha: str) -> tuple[dict[str, Any], dict[str, Any], tuple[int, int] | None]:
    """``(fusion, conflit, (avance, retard))`` d'un commit contre la cible."""
    counts = ahead_behind(ctx.src, ctx.target_sha, sha)
    if counts is None:
        return {"etat": NON_MESURE, "par": ""}, {"etat": NON_MESURE, "fichiers": []}, None
    none = {"etat": "aucun", "fichiers": []}
    if counts[0] == 0:
        return {"etat": "fusionnee", "par": "ancetre"}, none, counts
    tree = ctx.src.git("rev-parse", f"{sha}^{{tree}}").stdout.strip()
    if tree and tree in ctx.target_trees:
        # Fusion squash (ou rebase) d'une branche à jour : un commit de la cible porte exactement son arbre.
        return {"etat": "fusionnee", "par": "arbre"}, none, counts
    if sha in ctx.merged_heads:
        return {"etat": "fusionnee", "par": f"pr #{ctx.merged_heads[sha]}"}, none, counts
    probe = ctx.probe(sha)
    conflict = {"etat": probe.conflict_kind, "fichiers": list(probe.files)}
    if probe.status == NON_MESURE:
        return {"etat": NON_MESURE, "par": "", "raison": probe.reason}, conflict, counts
    if probe.same_tree:
        # La fusion ne changerait rien à la cible : tout son contenu y est déjà.
        return {"etat": "fusionnee", "par": "fusion_sans_apport"}, conflict, counts
    return {"etat": "non_fusionnee", "par": ""}, conflict, counts


def _branch_entry(ctx: _Ctx, ref: BranchRef) -> dict[str, Any]:
    fusion, conflict, counts = _fusion(ctx, ref.sha)
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
    }
    if orphan:
        ctx.signal("branche_orpheline", entry["ref"], f"branche de tâche sans tâche vivante ({task_id})", tache=task_id)
    if fusion["etat"] == "fusionnee" and fusion["par"] != "ancetre":
        ctx.signal("branche_fusionnee_sans_etre_ancetre", entry["ref"], f"contenu déjà sur la cible ({fusion['par']})")
    if conflict["etat"] in ("changelog_seul", "autre") and not ref.remote:
        ctx.signal(f"conflit_{conflict['etat']}", entry["ref"], ", ".join(conflict["fichiers"]))
    return entry


def _section_git(ctx: _Ctx, include_remote: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    branches: dict[str, Any] = {"mesure": False, "raison": "cible introuvable", "elements": []}
    worktrees: dict[str, Any] = {"mesure": False, "raison": "git worktree list a échoué", "elements": []}
    entries = list_worktrees(ctx.src)
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
                }
                for w in sorted(entries, key=lambda w: w.path)
            ],
        }
        for w in worktrees["elements"]:
            if w["elagable"]:
                ctx.signal("worktree_elagable", w["ref"], "dossier du worktree absent (git worktree prune le retirerait)")
    if not ctx.target_sha:
        return branches, worktrees
    refs = list_branches(ctx.src)
    if refs is None:
        branches["raison"] = "git for-each-ref a échoué"
        return branches, worktrees
    # Les distantes du seul remote de la cible (``origin`` pour ``origin/main``) : un remote amont étranger
    # (historique sans ancêtre commun) n'est pas du travail de ce projet.
    remote = ctx.src.target.split("/", 1)[0] + "/" if "/" in ctx.src.target else ""
    keep = [
        r
        for r in refs
        if r.name != ctx.src.target and (not r.remote or (include_remote and remote and r.name.startswith(remote)))
    ]
    branches = {"mesure": True, "distantes_incluses": include_remote, "elements": [_branch_entry(ctx, r) for r in keep]}
    return branches, worktrees


def _pr_entry(ctx: _Ctx, pr: dict[str, Any], hours: int, branches: dict[str, dict[str, Any]]) -> dict[str, Any]:
    number = int(pr.get("number") or 0)
    head = str(pr.get("headRefName") or "")
    updated = parse_instant(pr.get("updatedAt"))
    owners = owners_of_branch(ctx.view, head, str(pr.get("title") or ""), number)
    alive, sessions = session_alive(ctx.view, owners)
    idle = hours_since(updated, ctx.src.now)
    conflict: dict[str, Any] = {"etat": NON_MESURE, "fichiers": [], "source": ""}
    head_sha = str(pr.get("headRefOid") or "")
    local = branches.get(f"branche:origin/{head}") or branches.get(f"branche:{head}")
    sha = head_sha if head_sha and resolve_commit(ctx.src, head_sha) else str((local or {}).get("sha") or "")
    if ctx.target_sha and sha:
        probe = ctx.probe(sha)
        conflict = {"etat": probe.conflict_kind, "fichiers": list(probe.files), "source": "git merge-tree local"}
    elif str(pr.get("mergeable") or "") == "CONFLICTING":
        conflict = {"etat": "conflit_non_detaille", "fichiers": [], "source": "gh mergeable"}
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
            f"aucune activité depuis {idle} h, aucune session vivante",
            heures=idle,
        )
    if conflict["etat"] in ("changelog_seul", "autre"):
        ctx.signal(f"conflit_{conflict['etat']}", entry["ref"], ", ".join(conflict["fichiers"]))
    return entry


def _section_github(ctx: _Ctx, hours: int, branches: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_ref = {b["ref"]: b for b in branches.get("elements", [])}
    prs: dict[str, Any] = {"mesure": False, "elements": []}
    data, reason = gh_json(ctx.src, "pr", "list", "--state", "open", "--limit", "200", "--json", _PR_FIELDS)
    if isinstance(data, list):
        items = sorted((p for p in data if isinstance(p, dict)), key=lambda p: int(p.get("number") or 0))
        prs = {"mesure": True, "elements": [_pr_entry(ctx, p, hours, by_ref) for p in items]}
    else:
        prs["raison"] = reason or "gh : réponse inattendue"
    issues: dict[str, Any] = {"mesure": False, "elements": []}
    data, reason = gh_json(ctx.src, "issue", "list", "--state", "open", "--limit", "500", "--json", _ISSUE_FIELDS)
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
    else:
        issues["raison"] = reason or "gh : réponse inattendue"
    return prs, issues


def _linked_states(ctx: _Ctx, numbers: set[int], open_numbers: set[int] | None) -> dict[int, dict[str, Any]]:
    """État des issues et PR liées (``gh issue view`` rend aussi une PR) ; une ouverte connue n'est pas relue."""
    states: dict[int, dict[str, Any]] = {}
    for number in sorted(numbers):
        if open_numbers is not None and number in open_numbers:
            states[number] = {"etat": "OPEN"}
            continue
        data, reason = gh_json(ctx.src, "issue", "view", str(number), "--json", "number,state,closedAt,title")
        states[number] = (
            {"etat": str(data.get("state") or ""), "fermee_le": data.get("closedAt"), "titre": data.get("title") or ""}
            if isinstance(data, dict)
            else {"etat": NON_MESURE, "raison": reason}
        )
    return states


def _task_signals(ctx: _Ctx, tasks: list[dict[str, Any]], issues: dict[str, Any]) -> None:
    open_tasks = [t for t in tasks if t["ouverte"]]
    open_numbers = {int(i["numero"]) for i in issues["elements"]} if issues["mesure"] else None
    linked = {n for t in open_tasks for n in t["liens"]["issues"]}
    gh_ok = issues["mesure"]
    states = _linked_states(ctx, linked, open_numbers) if gh_ok else {}
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
    log = target_log(ctx.src, ctx.target_sha)
    for task in open_tasks:
        pattern = _id_pattern(task["id"])
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
    ctx = _Ctx(src=src, view=view)
    target_sha = resolve_commit(src, src.target)
    if target_sha:
        ctx.target_sha = target_sha
        tree = src.git("rev-parse", f"{target_sha}^{{tree}}")
        ctx.target_tree = tree.stdout.strip() if tree.returncode == 0 else ""
        trees = src.git("log", "--max-count=5000", "--format=%T", target_sha)
        ctx.target_trees = frozenset(trees.stdout.split()) if trees.returncode == 0 else frozenset()
        merged, _reason = gh_json(src, "pr", "list", "--state", "merged", "--limit", "1000", "--json", "number,headRefOid")
        if isinstance(merged, list):
            ctx.merged_heads = {
                str(p["headRefOid"]): int(p["number"]) for p in merged if isinstance(p, dict) and p.get("headRefOid")
            }
    tasks = [_task_entry(t, view) for t in sorted(view.tasks.values(), key=lambda t: t.id)] if view else []
    with scratch_objects(src) as env:
        ctx.env = env if ctx.target_tree else None
        branches, worktrees = _section_git(ctx, include_remote)
        prs, issues = _section_github(ctx, inactivity_hours, branches)
    _task_signals(ctx, tasks, issues)
    payload: dict[str, Any] = {
        "$schema": MAP_SCHEMA,
        "genere_le": src.now.isoformat(),
        "projet": str(src.root),
        "cible": {"ref": src.target, "sha": target_sha or NON_MESURE},
        "parametres": {"inactivite_heures": inactivity_hours, "distantes_incluses": include_remote},
        "taches": {"mesure": view is not None, "elements": tasks, **({"raison": ledger_reason} if view is None else {})},
        "branches": branches,
        "worktrees": worktrees,
        "prs": prs,
        "issues": issues,
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
