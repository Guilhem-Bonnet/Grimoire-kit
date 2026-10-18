"""Checkpoint humain par vague, lié à l'empreinte du diff fusionné (W5-05, PAR-6).

Avant ce module, rien ne séparait deux vagues d'un plan : les tâches de la
vague 1 se fusionnaient (:mod:`grimoire.missions.task_worktree`), puis celles de
la vague 2 se réclamaient, sans qu'un humain ait vu ce qui venait d'entrer dans
la cible. Ici une vague a un checkpoint :

- **l'empreinte** est celle du contenu fusionné : pour chaque tâche de la vague,
  l'arbre de sa base épinglée, l'arbre de sa tête et l'arbre du commit de merge
  vérifié (``task.merged``). Un arbre git détermine son diff ; deux contenus
  identiques donnent la même empreinte, quel que soit l'historique. Tant qu'une
  tâche de la vague n'est pas fusionnée, il n'y a pas d'empreinte : la vague est
  ``incomplete`` et ne peut pas être validée ;
- **la décision** passe par l'attestation de W2-01
  (:mod:`grimoire.flows.attestation`) : un tour humain de la session qui a
  présenté le checkpoint, postérieur au challenge. Le challenge porte
  l'empreinte ; une vague dont le contenu a changé depuis ne se valide pas avec
  l'ancien challenge, et une décision qui cite une autre empreinte que l'actuelle
  est refusée. Une décision non attestée est refusée (le checkpoint est
  destructif au sens de W2-01 : il ouvre la suite) ;
- **la caducité** est recalculée à chaque lecture : une tâche re-fusionnée avec
  un autre contenu, ou un commit de merge qui n'est plus dans la cible, rend
  la vague ``stale``, jamais ``approved`` ;
- **le verrou** : une vague déclarée requise (:func:`require`) ferme le claim de
  toute tâche d'une vague postérieure de la mission tant qu'elle n'est pas
  ``approved`` (:func:`claim_refusal`, appelé par ``TaskService.transition``).

Limites écrites : l'attestation rend la falsification plus coûteuse, elle ne la
rend pas impossible (voir :data:`grimoire.hosts.decisions.authority_guard.LIMIT_NOTE`) ;
``TaskService.transition`` applique le verrou à toute cible ``claimed`` (CLI, MCP,
routes du cockpit), mais une écriture directe dans le ledger (``MissionLedger.claim_task`` :
import du board, rejeu task-flow) le contourne ; le checkpoint ne vaut que pour les tâches fusionnées
par ``task merge`` (un ``task.merged`` au ledger).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.flows.attestation import ATTESTED, GRANTED_PREFIX, CheckpointJournal, judge_checkpoint
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import MERGED_EVENT, _git, _is_ancestor
from grimoire.missions.waves import wave_levels

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "WaveDecision",
    "WaveState",
    "claim_blocked",
    "claim_refusal",
    "decide",
    "report",
    "request",
    "require",
    "wave_state",
]

_FLOWS_RELPATH = Path("_grimoire-runtime-output/flows")
_REQUIRE = "require"
APPROVED = "approved"
_SETTLED = frozenset({TaskState.CLOSED, TaskState.CANCELLED})


@dataclass(frozen=True, slots=True)
class WaveState:
    """Où en est le checkpoint d'une vague."""

    mission_id: str
    wave: int
    tasks: tuple[str, ...]
    #: ``incomplete`` | ``unrequested`` | ``awaiting_human`` | ``approved`` | ``rejected`` | ``stale``
    status: str
    fingerprint: str = ""
    reason: str = ""
    required: bool = False
    unmerged: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id, "wave": self.wave, "tasks": list(self.tasks), "status": self.status,
            "fingerprint": self.fingerprint, "reason": self.reason, "required": self.required,
            "unmerged": list(self.unmerged),
        }


@dataclass(frozen=True, slots=True)
class WaveDecision:
    """L'issue d'une décision : ``ok`` seulement si elle est inscrite au journal."""

    ok: bool
    status: str
    reason: str
    state: WaveState

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "reason": self.reason, "state": self.state.to_dict()}


def _lifts(attestation: object) -> bool:
    """Une décision lève le checkpoint si elle est attestée (W2-01) ou prise sous un octroi valide (``granted:<id>``, W5-06)."""
    return attestation == ATTESTED or (isinstance(attestation, str) and attestation.startswith(GRANTED_PREFIX))


def _grant_lookup(service: TaskService, mission_id: str, node: str) -> tuple[str, str] | None:
    """L'octroi d'autonomie qui couvre ce checkpoint : ``run:waves-<mission>`` (ou ``/wave-<n>``), comme un node de flow."""
    from grimoire.flows.autonomy import GrantJournal, refusal_note

    state = GrantJournal(service.home_root / _FLOWS_RELPATH, service.home_root).for_checkpoint(f"waves-{mission_id}", node)
    if state is None:
        return None
    return state.grant_id, "" if state.active else refusal_note(state)


def _journal(service: TaskService, mission_id: str) -> CheckpointJournal:
    return CheckpointJournal(service.home_root / _FLOWS_RELPATH, f"waves-{mission_id}")


def _node(wave: int) -> str:
    return f"wave-{wave}"


def _tree(repo: Path, rev: str) -> str:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{tree}}", check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def _fingerprint(service: TaskService, tasks: tuple[str, ...]) -> tuple[str, str, tuple[str, ...]]:
    """``(empreinte, motif, non fusionnées)`` : l'empreinte est vide si la vague n'est pas (ou plus) intègre."""
    repo = service.home_root
    parts: list[dict[str, str]] = []
    unmerged: list[str] = []
    for tid in tasks:
        merged = [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT]
        if not merged:
            unmerged.append(tid)
            continue
        p = merged[-1].payload
        base, head, sha, into = (str(p.get(k, "")) for k in ("base_sha", "head_sha", "merged_sha", "into"))
        if not (base and head and sha and into):
            return "", f"{tid} : reçu task.merged incomplet", ()
        if not _is_ancestor(repo, sha, f"refs/heads/{into}"):
            return "", f"{tid} : le commit fusionné {sha[:12]} n'est plus dans {into}", ()
        trees = (_tree(repo, base), _tree(repo, head), _tree(repo, sha))
        if not all(trees):
            return "", f"{tid} : objet git introuvable (base, tête ou merge)", ()
        parts.append({"task": tid, "base_tree": trees[0], "head_tree": trees[1], "merged_tree": trees[2]})
    if unmerged:
        return "", f"{len(unmerged)} tâche(s) pas encore fusionnée(s) : {', '.join(unmerged)}", tuple(unmerged)
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), "", ()


def wave_members(service: TaskService, mission_id: str, wave: int) -> tuple[str, ...]:
    levels = wave_levels(service.ledger.list_tasks())
    by_id = {t.id: t for t in service.ledger.list_tasks()}
    return tuple(sorted(t for t, lv in levels.items() if lv == wave and by_id[t].mission_id == mission_id))


def _decided(entries: list[dict[str, Any]], node: str) -> dict[str, Any] | None:
    last = [e for e in entries if e.get("type") == "decision" and e.get("node_id") == node and e.get("decision") in ("approve", "reject")]
    return last[-1] if last else None


def wave_state(service: TaskService, mission_id: str, wave: int) -> WaveState:
    """L'état du checkpoint de la vague *wave*, recalculé depuis le ledger, git et le journal."""
    tasks = wave_members(service, mission_id, wave)
    journal = _journal(service, mission_id)
    entries = journal.entries()
    node = _node(wave)
    required = any(e.get("type") == _REQUIRE and e.get("node_id") == node for e in entries)

    def state(status: str, fp: str = "", reason: str = "", unmerged: tuple[str, ...] = ()) -> WaveState:
        return WaveState(mission_id, wave, tasks, status, fp, reason, required, unmerged)

    if not tasks:
        return state("incomplete", reason=f"aucune tâche en vague {wave} dans {mission_id}")
    fp, why, unmerged = _fingerprint(service, tasks)
    decision = _decided(entries, node)
    if not fp:
        if unmerged:
            return state("incomplete", reason=why, unmerged=unmerged)
        return state("stale", reason=why)  # une décision a pu exister : plus rien ne la couvre
    if decision is not None:
        if decision.get("fingerprint") != fp:
            return state("stale", fp, "le contenu fusionné a changé depuis la décision : à revalider")
        if decision.get("decision") == "approve" and _lifts(decision.get("attestation")):
            return state(APPROVED, fp)
        if decision.get("decision") == "reject":
            return state("rejected", fp, str(decision.get("reason") or ""))
    challenge = journal.open_challenge(node)
    if challenge is not None and challenge.get("fingerprint") == fp:
        return state("awaiting_human", fp)
    return state("unrequested", fp)


def request(service: TaskService, mission_id: str, wave: int) -> WaveState:
    """Présente le checkpoint : émet le challenge lié à l'empreinte actuelle (sans effet si la vague n'est pas complète)."""
    current = wave_state(service, mission_id, wave)
    if current.status in ("incomplete", "stale") and not current.fingerprint:
        return current
    if current.status == APPROVED:
        return current
    _journal(service, mission_id).issue_challenge(_node(wave), bound={"fingerprint": current.fingerprint})
    return wave_state(service, mission_id, wave)


def require(service: TaskService, mission_id: str, wave: int, *, actor: str = "cli") -> WaveState:
    """Déclare le checkpoint de la vague requis : les vagues suivantes ne se réclament plus sans lui."""
    journal = _journal(service, mission_id)
    if not any(e.get("type") == _REQUIRE and e.get("node_id") == _node(wave) for e in journal.entries()):
        journal._append({"type": _REQUIRE, "node_id": _node(wave), "mission_id": mission_id, "actor": actor})
    return wave_state(service, mission_id, wave)


def decide(
    service: TaskService, mission_id: str, wave: int, decision: str, fingerprint: str, reason: str = ""
) -> WaveDecision:
    """Inscrit la décision humaine sur *wave*, pour l'empreinte *fingerprint* que l'humain a vue.

    Refusée si la vague n'est pas complète, si *fingerprint* n'est pas l'empreinte actuelle,
    si aucun challenge ne porte cette empreinte, ou si la décision n'est pas attestée.
    """
    current = wave_state(service, mission_id, wave)

    def refuse(status: str, why: str) -> WaveDecision:
        return WaveDecision(False, status, why, current)

    if decision not in ("approve", "reject"):
        return refuse("invalid", f"décision {decision!r} : 'approve' ou 'reject'")
    if not current.fingerprint:
        return refuse(current.status, current.reason or "vague sans empreinte")
    if fingerprint != current.fingerprint:
        return refuse("stale", f"empreinte vue {fingerprint[:12] or '(vide)'} != empreinte actuelle {current.fingerprint[:12]} : "
                      "le contenu fusionné a changé, relire puis redécider")
    journal = _journal(service, mission_id)
    node = _node(wave)
    challenge = journal.open_challenge(node)
    if challenge is None or challenge.get("fingerprint") != current.fingerprint:
        return refuse("unrequested", "aucun challenge pour cette empreinte : présenter le checkpoint (request) d'abord")
    gate = judge_checkpoint(
        journal, service.home_root, node, {"checkpoint_decision": decision, "checkpoint_reason": reason},
        destructive=True,
        grant=lambda: _grant_lookup(service, mission_id, node),
    )
    if gate is None:  # pragma: no cover - décision déjà validée plus haut
        return refuse("invalid", "décision invalide")
    if gate.refusal:
        return refuse("awaiting_human", gate.refusal)
    journal.record_decision(node, decision, reason, gate.attestation, bound={"fingerprint": current.fingerprint})
    return WaveDecision(True, wave_state(service, mission_id, wave).status, reason, wave_state(service, mission_id, wave))


def report(service: TaskService, mission_id: str) -> list[dict[str, Any]]:
    """Le checkpoint de chaque vague de la mission : requis, décidé, ou présenté. Vide si le journal n'existe pas."""
    journal = _journal(service, mission_id)
    if not journal.path.is_file():
        return []
    waves = sorted({int(str(e["node_id"]).split("-")[1]) for e in journal.entries() if str(e.get("node_id", "")).startswith("wave-")})
    return [wave_state(service, mission_id, n).to_dict() for n in waves]


def _refusal(service: TaskService, mission_id: str, entries: list[dict[str, Any]], level: int) -> str:
    for entry in entries:
        if entry.get("type") != _REQUIRE:
            continue
        wave = int(str(entry["node_id"]).split("-")[1])
        if wave < level:
            st = wave_state(service, mission_id, wave)
            if st.status != APPROVED:
                return (f"checkpoint de la vague {wave} requis et {st.status}"
                        + (f" ({st.reason})" if st.reason else "") + " : grimoire task wave-checkpoint")
    return ""


def claim_refusal(service: TaskService, task_id: str) -> str:
    """Le motif de refus du claim de *task_id* si une vague requise antérieure n'est pas ``approved`` ; vide sinon.

    Appelé par ``TaskService.transition`` pour toute cible ``claimed`` : la CLI, le MCP
    (``task_claim`` comme ``task_update move``) et les routes du cockpit passent par là.
    """
    task = service.require(task_id)
    journal = _journal(service, task.mission_id)
    if not journal.path.is_file():
        return ""
    level = wave_levels(service.ledger.list_tasks()).get(task_id)
    if level is None:
        return ""
    return _refusal(service, task.mission_id, journal.entries(), level)


def claim_blocked(service: TaskService, mission_id: str) -> dict[str, str]:
    """``{tâche: motif}`` des tâches non terminées de la mission dont le claim est fermé par un checkpoint requis.

    Ce que le MCP et le cockpit affichent à côté des vagues : le motif exact que le claim opposerait.
    """
    journal = _journal(service, mission_id)
    if not journal.path.is_file():
        return {}
    entries = journal.entries()
    tasks = service.ledger.list_tasks()
    levels = wave_levels(tasks)
    blocked: dict[str, str] = {}
    for task in tasks:
        level = levels.get(task.id)
        if task.mission_id != mission_id or level is None or task.status in _SETTLED:
            continue
        reason = _refusal(service, mission_id, entries, level)
        if reason:
            blocked[task.id] = reason
    return dict(sorted(blocked.items()))
