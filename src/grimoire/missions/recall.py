"""Rappel de tâche — ce que la mémoire du projet sait avant un claim (#141).

L'aval existait déjà : ``memory/projections.py`` projette missions et tâches en
documents vectoriels, câblés sur ``grimoire memory vector sync-tasks`` et
``grimoire memory graph sync-tasks``. Ce qui manquait était l'amont — rien ne
rappelait quoi que ce soit à personne au moment où une tâche est réclamée.

Deux gestes symétriques :

``build_task_recall``
    Lu au claim (et par ``grimoire task recall`` / l'outil MCP ``task_recall``
    à tout moment) : l'historique propre de la tâche, ses voisines — liées
    explicitement, de la même mission, ou au titre proche — et ce que la
    mémoire du projet a consolidé sur des sujets voisins. Borné en tokens :
    c'est un rappel, pas un rapport.

``consolidate_task_memory``
    Écrit à la clôture ou au blocage — les seuls moments où la roadmap
    (``planning/memory-os-roadmap.md``, étape 6) autorise une écriture
    durable : *« Ne pas stocker chaque micro-action comme connaissance
    durable. Promouvoir seulement décisions, preuves, blocages, patterns et
    erreurs répétées. »* Rien n'est écrit sur un ``move`` ordinaire.

Aucune des deux fonctions n'exige de backend mémoire configuré : sans lui, le
rappel reste utile depuis le seul Mission Ledger (historique propre, tâches
voisines, causes d'arrêt) — la mémoire n'ajoute qu'une couche, elle n'est
jamais la condition de la boucle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import IncidentStatus, MissionTask, TaskState

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry
    from grimoire.memory.manager import MemoryManager

__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "DEFAULT_TOKEN_BUDGET",
    "MemoryHit",
    "SiblingRecall",
    "TaskRecall",
    "build_task_recall",
    "consolidate_task_memory",
]

DEFAULT_LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: ~4 caractères par token — même convention que
#: ``MemoryManager._trim_to_budget``. Un rappel est un paragraphe qu'une
#: session lit avant de commencer, pas un rapport qu'elle dépouille.
DEFAULT_TOKEN_BUDGET = 400

#: Types typés (voir ``grimoire.memory.manager.MEMORY_TYPES``) que le rappel
#: interroge — les deux seuls que :func:`consolidate_task_memory` écrit.
_RECALL_MEMORY_TYPES: tuple[str, ...] = ("decisions", "failures")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into", "your",
    "une", "un", "des", "les", "dans", "pour", "avec", "sur", "aux", "que",
    "qui", "sans", "est", "son", "ses", "par", "plus", "tout", "aussi",
    "leur", "cette", "vers", "sous", "afin", "ainsi",
})
_WORD_RE = re.compile(r"[a-zà-öø-ÿ0-9]+")


@dataclass(frozen=True, slots=True)
class SiblingRecall:
    """Une tâche voisine, et ce qui a expliqué son arrêt si elle s'est arrêtée."""

    task_id: str
    title: str
    relation: str
    status: str
    causes: tuple[str, ...] = ()
    resolution: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "relation": self.relation,
            "status": self.status,
            "causes": list(self.causes),
            "resolution": self.resolution,
        }


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """Une entrée de mémoire consolidée, retrouvée par le rappel."""

    id: str
    type: str
    text: str
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "text": self.text, "tags": list(self.tags)}


@dataclass(frozen=True, slots=True)
class TaskRecall:
    """Le rappel d'une tâche : son propre passé, ses voisines, la mémoire consolidée."""

    task_id: str
    task: MissionTask | None
    own_history: tuple[str, ...] = ()
    siblings: tuple[SiblingRecall, ...] = ()
    memory_hits: tuple[MemoryHit, ...] = ()
    text: str = ""
    token_budget: int = DEFAULT_TOKEN_BUDGET

    @property
    def is_empty(self) -> bool:
        """Aucune tâche à ce nom — absence de sujet, pas absence de souvenir."""
        return self.task is None

    @property
    def has_content(self) -> bool:
        """Le rappel a quelque chose à dire — c'est le test de parcimonie de l'UI."""
        return bool(self.own_history or self.siblings or self.memory_hits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task.to_dict() if self.task is not None else None,
            "own_history": list(self.own_history),
            "siblings": [s.to_dict() for s in self.siblings],
            "memory_hits": [h.to_dict() for h in self.memory_hits],
            "text": self.text,
            "token_budget": self.token_budget,
            "has_content": self.has_content,
        }


def _title_tokens(title: str) -> set[str]:
    words = _WORD_RE.findall(title.lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def _own_history(ledger: MissionLedger, task_id: str) -> list[str]:
    """Ce que cette tâche, elle-même, a déjà traversé — avant ce claim-ci."""
    lines: list[str] = []
    for event in ledger.list_events(task_id):
        if event.event_type != "task.transitioned":
            continue
        to_state = str(event.payload.get("to_state", ""))
        if to_state not in (TaskState.FAILED.value, TaskState.BLOCKED.value):
            continue
        reason = str(event.payload.get("reason") or "")
        line = f"tentative précédente → {to_state}"
        if reason:
            line += f" : {reason}"
        lines.append(line)
    for incident in ledger.list_incidents(task_id):
        if incident.status is IncidentStatus.OPEN:
            continue  # un incident encore ouvert appartient au présent, pas au rappel
        causes = "; ".join(incident.causes)
        marker = "résolu" if incident.status is IncidentStatus.RESOLVED else "accepté"
        detail = causes or incident.summary
        lines.append(f"incident {incident.kind} ({marker})" + (f" — {detail}" if detail else ""))
    return lines


def _find_siblings(ledger: MissionLedger, task: MissionTask, *, limit: int) -> list[MissionTask]:
    """Les tâches voisines : liées explicitement, ou dont le titre recoupe le sien.

    Un lien déclaré (``task link``) compte toujours. Sans lien, il faut au
    moins deux mots-clés partagés — ou un seul dans la même mission — pour
    éviter qu'un titre générique ("Corriger le bug") ne fasse voisiner la
    moitié du ledger.
    """
    tokens = _title_tokens(task.title)
    linked = {dep.target for dep in task.dependencies}
    scored: list[tuple[int, bool, MissionTask]] = []
    for other in ledger.list_tasks():
        if other.id == task.id:
            continue
        explicit = other.id in linked
        overlap = len(tokens & _title_tokens(other.title))
        same_mission = other.mission_id == task.mission_id
        if not explicit and overlap < 2 and not (same_mission and overlap >= 1):
            continue
        scored.append((overlap, explicit, other))
    scored.sort(key=lambda s: (not s[1], -s[0]))
    return [s[2] for s in scored[:limit]]


def _sibling_causes(ledger: MissionLedger, other: MissionTask) -> list[str]:
    """Ce qui a arrêté *other*, qu'elle le soit encore ou qu'elle s'en soit relevée.

    « Une jumelle qui a échoué précédemment » (#141) ne redevient pas muette
    parce qu'elle a depuis été rouverte : la cause reste instructive même une
    fois la carte revenue à ``ready``. On regarde d'abord les incidents, plus
    riches ; à défaut, tout passage historique par ``blocked``/``failed``.
    """
    causes: list[str] = []
    for incident in ledger.list_incidents(other.id):
        detail = "; ".join(incident.causes) or incident.summary
        if detail:
            causes.append(detail)
    if causes:
        return causes
    for event in ledger.list_events(other.id):
        if event.event_type != "task.transitioned":
            continue
        if event.payload.get("to_state") not in (TaskState.BLOCKED.value, TaskState.FAILED.value):
            continue
        reason = str(event.payload.get("reason") or "")
        if reason:
            causes.append(reason)
    return causes


def _sibling_recall(task: MissionTask, other: MissionTask, causes: list[str]) -> SiblingRecall:
    linked = {dep.target for dep in task.dependencies}
    if other.id in linked:
        relation = "lien déclaré"
    elif other.mission_id == task.mission_id:
        relation = "même mission"
    else:
        relation = "titre proche"
    resolution = "clôturée" if other.status is TaskState.CLOSED else ""
    return SiblingRecall(
        task_id=other.id, title=other.title, relation=relation,
        status=other.status.value, causes=tuple(causes), resolution=resolution,
    )


def _memory_hits(
    memory_manager: MemoryManager | None,
    task: MissionTask,
    siblings: tuple[SiblingRecall, ...],
    *,
    limit: int,
) -> list[MemoryHit]:
    """Ce que la mémoire consolidée sait de ce sujet — au-delà des seules voisines du ledger.

    Best-effort : un backend absent, non configuré, ou en erreur rend un
    rappel amputé de cette couche, jamais un rappel qui plante.
    """
    if memory_manager is None or limit <= 0:
        return []
    query = " ".join([task.title, *(s.title for s in siblings)])[:300].strip()
    if not query:
        return []
    hits: list[MemoryHit] = []
    seen: set[str] = set()
    for type_ in _RECALL_MEMORY_TYPES:
        try:
            entries = memory_manager.recall_typed(query, type_=type_, limit=limit)
        except Exception:  # noqa: S112 — la mémoire est une couche optionnelle du rappel
            continue
        for entry in entries:
            if not entry.id or entry.id in seen:
                continue
            seen.add(entry.id)
            hits.append(MemoryHit(id=entry.id, type=type_, text=entry.text, tags=tuple(entry.tags)))
    return hits[:limit]


def _trim_to_budget(text: str, token_budget: int) -> str:
    char_budget = max(token_budget, 1) * 4
    if len(text) <= char_budget:
        return text
    trimmed = text[:char_budget]
    cut = trimmed.rfind("\n")
    if cut > 0:
        trimmed = trimmed[:cut]
    return trimmed + f"\n  … (rappel tronqué à ~{token_budget} tokens)"


def _render_text(
    task_id: str,
    own_history: tuple[str, ...],
    siblings: tuple[SiblingRecall, ...],
    memory_hits: tuple[MemoryHit, ...],
    token_budget: int,
) -> str:
    if not own_history and not siblings and not memory_hits:
        return (
            f"[Grimoire — rappel de tâche] {task_id} : rien en mémoire — "
            "première fois que ce sujet est travaillé."
        )
    lines = [f"[Grimoire — rappel de tâche] {task_id} :"]
    if own_history:
        lines.append("Historique de cette tâche :")
        lines.extend(f"  - {h}" for h in own_history)
    if siblings:
        lines.append("Tâches voisines :")
        for sibling in siblings:
            head = f"  - {sibling.task_id} ({sibling.relation}, {sibling.status})"
            if sibling.resolution:
                head += f" — {sibling.resolution}"
            lines.append(head)
            lines.extend(f"      cause : {cause}" for cause in sibling.causes)
    if memory_hits:
        lines.append("Mémoire du projet :")
        lines.extend(f"  - [{hit.type}] {hit.text}" for hit in memory_hits)
    return _trim_to_budget("\n".join(lines), token_budget)


def build_task_recall(
    project_root: Path,
    task_id: str,
    *,
    ledger_root: Path = DEFAULT_LEDGER_RELPATH,
    memory_manager: MemoryManager | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    sibling_limit: int = 5,
    memory_limit: int = 5,
) -> TaskRecall:
    """Assemble le rappel de *task_id* — le ledger toujours, la mémoire si configurée.

    Une tâche inconnue, ou un projet sans ledger, rend un rappel honnêtement
    vide (``task is None``) plutôt qu'une erreur : c'est la même convention
    que :func:`grimoire.missions.trace.build_task_timeline`.
    """
    root = project_root.resolve()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    empty = TaskRecall(task_id=task_id, task=None, token_budget=token_budget)
    if not (ledger_path / "events.jsonl").is_file():
        return empty
    ledger = MissionLedger(ledger_path)
    task = ledger.get_task(task_id)
    if task is None:
        return empty

    own_history = tuple(_own_history(ledger, task_id))
    sibling_tasks = _find_siblings(ledger, task, limit=sibling_limit)
    siblings = tuple(
        _sibling_recall(task, other, _sibling_causes(ledger, other)) for other in sibling_tasks
    )
    memory_hits = tuple(_memory_hits(memory_manager, task, siblings, limit=memory_limit))
    text = _render_text(task_id, own_history, siblings, memory_hits, token_budget)
    return TaskRecall(
        task_id=task_id, task=task, own_history=own_history, siblings=siblings,
        memory_hits=memory_hits, text=text, token_budget=token_budget,
    )


def _closure_summary(task: MissionTask, own_history: list[str]) -> str:
    lines = [f"Tâche {task.id} clôturée : {task.title}."]
    if task.acceptance:
        lines.append("Critères : " + "; ".join(task.acceptance) + ".")
    if own_history:
        lines.append("Avant d'y arriver : " + " | ".join(own_history) + ".")
    return " ".join(lines)


def _blocker_summary(task: MissionTask, target: TaskState, reason: str) -> str:
    verb = "bloquée" if target is TaskState.BLOCKED else "échouée"
    text = f"Tâche {task.id} ({task.title}) {verb}"
    return f"{text} : {reason}." if reason else f"{text}."


def consolidate_task_memory(
    memory_manager: MemoryManager | None,
    ledger: MissionLedger,
    task: MissionTask,
    target: TaskState,
    *,
    actor: str,
    reason: str = "",
) -> MemoryEntry | None:
    """Écrit ce qu'une clôture ou un blocage enseigne — et rien d'autre.

    Les garde-fous de la roadmap sont la forme de cette fonction, pas un
    commentaire à côté : elle n'a pas de branche pour ``ready``, ``running``,
    ou tout autre mouvement de travail ordinaire. ``remember`` est idempotent
    sur (agent, texte) — reclôturer ou rebloquer sur le même motif n'écrit pas
    de doublon. Best-effort : une mémoire indisponible n'annule jamais la
    transition déjà actée au ledger.
    """
    if memory_manager is None:
        return None
    if target is TaskState.CLOSED:
        text = _closure_summary(task, _own_history(ledger, task.id))
        type_ = "decisions"
    elif target in (TaskState.BLOCKED, TaskState.FAILED):
        text = _blocker_summary(task, target, reason)
        type_ = "failures"
    else:
        return None
    try:
        return memory_manager.remember(type_, actor, text, tags=(task.id, task.mission_id, "task-recall"))
    except Exception:  # consolidation best-effort, jamais au prix de la transition déjà actée
        return None
