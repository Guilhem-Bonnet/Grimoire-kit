"""Import d'un plan JSON dans le Mission Ledger (P50, ``grimoire task import``).

Le plan nomme ses tâches par une **clé** propre (``key``) ; le ledger leur donne
sa propre clé (``GAO-...``). Le plan est validé en entier AVANT la première
écriture : un plan invalide n'écrit rien. L'import est idempotent — chaque tâche
créée porte ``extra.import = {plan, key}``, et une entrée déjà importée n'est
ni recréée ni modifiée (une divergence de titre ou de critères est signalée, jamais
écrasée) ; une entrée ``existing`` désigne une tâche déjà au ledger et est sautée.

Format::

    {"plan": "refonte-x",                    # identité du plan (défaut : nom du fichier)
     "mission": "MIS-...",                   # optionnel (sinon --mission, sinon la mission par défaut)
     "tasks": [
       {"key": "a", "title": "...", "acceptance": ["..."], "evidence": ["..."],
        "priority": "high", "depends_on": ["b"], "description": "...", "owner": "..."},
       {"key": "b", "existing": "GAO-deja-001"}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.board import PRIORITIES
from grimoire.missions.schemas import DependencyKind
from grimoire.missions.service import MAX_DESCRIPTION_CHARS, TaskService

__all__ = ["ImportReport", "ImportRow", "PlanError", "import_plan", "load_plan"]

_PLAN_FIELDS = frozenset({"plan", "mission", "tasks"})
_ENTRY_FIELDS = frozenset(
    {"key", "title", "acceptance", "evidence", "priority", "depends_on", "description", "owner", "existing"}
)


class PlanError(GrimoireMissionError):
    """Un plan refusé : toutes les anomalies d'un coup, une par ligne, rien d'écrit."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("plan refusé, rien n'a été écrit :\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class _Entry:
    key: str
    title: str = ""
    acceptance: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    priority: str = ""
    depends_on: tuple[str, ...] = ()
    description: str = ""
    owner: str = ""
    existing: str = ""


@dataclass(frozen=True)
class _Plan:
    plan_id: str
    mission: str
    entries: tuple[_Entry, ...]


@dataclass(frozen=True)
class ImportRow:
    """Une ligne de la table clé du plan → clé du ledger."""

    key: str
    task_id: str
    action: str  # created | would_create | already_imported | diverged | skipped_existing
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "task_id": self.task_id, "action": self.action, "note": self.note}


@dataclass
class ImportReport:
    plan_id: str
    mission: str
    dry_run: bool
    rows: list[ImportRow] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan_id, "mission": self.mission, "dry_run": self.dry_run,
            "tasks": [r.to_dict() for r in self.rows], "links": self.links,
        }


def _strings(value: Any, label: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{label} : une liste de textes est attendue")
        return ()
    return tuple(v.strip() for v in value if v.strip())


def load_plan(path: Path) -> _Plan:
    """Lit et valide le fichier ; lève :class:`PlanError` (toutes les anomalies) sinon."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanError([f"fichier illisible : {path} ({exc.strerror or exc})"]) from exc
    except ValueError as exc:
        raise PlanError([f"JSON invalide dans {path.name} : {exc}"]) from exc
    return parse_plan(raw, default_id=path.stem)


def parse_plan(raw: Any, *, default_id: str = "plan") -> _Plan:
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise PlanError(["le plan doit être un objet JSON {\"tasks\": [...]}"])
    for unknown in sorted(set(raw) - _PLAN_FIELDS):
        problems.append(f"champ inconnu au plan : {unknown!r}")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise PlanError([*problems, "« tasks » doit être une liste non vide"])
    entries: list[_Entry] = []
    seen: set[str] = set()
    for index, item in enumerate(tasks, 1):
        where = f"tâche n°{index}"
        if not isinstance(item, dict):
            problems.append(f"{where} : un objet est attendu")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{where} : « key » manquante")
            continue
        key = key.strip()
        where = f"tâche « {key} »"
        if key in seen:
            problems.append(f"{where} : clé en double dans le plan")
        seen.add(key)
        for unknown in sorted(set(item) - _ENTRY_FIELDS):
            problems.append(f"{where} : champ inconnu {unknown!r}")
        entries.append(_parse_entry(key, where, item, problems))
    known = {e.key for e in entries}
    for entry in entries:
        for dep in entry.depends_on:
            if dep == entry.key:
                problems.append(f"tâche « {entry.key} » : dépend d'elle-même")
            elif dep not in known:
                problems.append(f"tâche « {entry.key} » : dépend de « {dep} », absente du plan")
    problems.extend(_cycles(entries))
    if problems:
        raise PlanError(problems)
    plan_id = str(raw.get("plan") or default_id).strip() or default_id
    return _Plan(plan_id=plan_id, mission=str(raw.get("mission") or "").strip(), entries=tuple(entries))


def _parse_entry(key: str, where: str, item: dict[str, Any], problems: list[str]) -> _Entry:
    depends = _strings(item.get("depends_on"), f"{where}, depends_on", problems)
    existing = item.get("existing", "")
    if not isinstance(existing, str):
        problems.append(f"{where} : « existing » doit être l'identifiant d'une tâche du ledger")
        existing = ""
    if existing.strip():
        extra = [n for n in ("title", "acceptance", "evidence", "priority", "description", "owner") if item.get(n)]
        if extra or depends:
            problems.append(f"{where} : une entrée « existing » ne porte que sa clé (reçu : {', '.join([*extra, *(['depends_on'] if depends else [])])})")
        return _Entry(key=key, existing=existing.strip())
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"{where} : « title » manquant")
        title = ""
    acceptance = _strings(item.get("acceptance"), f"{where}, acceptance", problems)
    if not acceptance:
        problems.append(f"{where} : au moins un critère d'acceptation (« acceptance »)")
    priority = item.get("priority", "")
    if priority and priority not in PRIORITIES:
        problems.append(f"{where} : priorité {priority!r} inconnue — parmi {', '.join(PRIORITIES)}")
        priority = ""
    description = item.get("description", "")
    if not isinstance(description, str):
        problems.append(f"{where} : « description » doit être un texte")
        description = ""
    elif len(description.strip()) > MAX_DESCRIPTION_CHARS:
        problems.append(f"{where} : description trop longue ({len(description.strip())} > {MAX_DESCRIPTION_CHARS})")
    owner = item.get("owner", "")
    if not isinstance(owner, str):
        problems.append(f"{where} : « owner » doit être un texte")
        owner = ""
    return _Entry(
        key=key, title=title.strip(), acceptance=acceptance,
        evidence=_strings(item.get("evidence"), f"{where}, evidence", problems), priority=str(priority or ""),
        depends_on=depends, description=description, owner=owner.strip(),
    )


def _cycles(entries: list[_Entry]) -> list[str]:
    """Un cycle de dépendances du plan, refusé avant d'écrire (le ledger le refuserait en cours de route)."""
    graph = {e.key: [d for d in e.depends_on if d != e.key] for e in entries}
    state: dict[str, int] = {}
    problems: list[str] = []

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2 or node not in graph:
            return
        if state.get(node) == 1:
            problems.append("cycle de dépendances : " + " → ".join([*path[path.index(node):], node]))
            return
        state[node] = 1
        for dep in graph[node]:
            visit(dep, [*path, node])
        state[node] = 2

    for key in graph:
        visit(key, [])
    return problems[:1]


def import_plan(service: TaskService, plan: _Plan, *, mission: str = "", actor: str = "cli", dry_run: bool = False) -> ImportReport:
    """Crée les tâches du plan et leurs liens ; ``dry_run`` calcule le même rapport sans écrire."""
    ledger = service.ledger
    target = mission or plan.mission
    if target and ledger.get_mission(target) is None:
        raise PlanError([f"mission inconnue : {target} (`grimoire mission open` en ouvre une)"])
    tasks = {t.id: t for t in ledger.list_tasks()}
    imported = {
        (str(t.extra["import"].get("plan")), str(t.extra["import"].get("key"))): t.id
        for t in tasks.values() if isinstance(t.extra.get("import"), dict)
    }
    problems = [
        f"tâche « {e.key} » : {e.existing} n'existe pas dans le ledger"
        for e in plan.entries if e.existing and e.existing not in tasks
    ]
    if problems:
        raise PlanError(problems)

    report = ImportReport(plan_id=plan.plan_id, mission=target, dry_run=dry_run)
    ids: dict[str, str] = {}
    for entry in plan.entries:
        if entry.existing:
            ids[entry.key] = entry.existing
            report.rows.append(ImportRow(entry.key, entry.existing, "skipped_existing", "déjà au ledger, non modifiée"))
            continue
        prior = imported.get((plan.plan_id, entry.key))
        if prior is not None:
            ids[entry.key] = prior
            old = tasks[prior]
            diverged = old.title != entry.title or tuple(old.acceptance) != entry.acceptance
            report.rows.append(ImportRow(
                entry.key, prior, "diverged" if diverged else "already_imported",
                "titre ou critères différents du plan, tâche non modifiée" if diverged else "déjà importée",
            ))
            continue
        if dry_run:
            ids[entry.key] = ""
            report.rows.append(ImportRow(entry.key, "", "would_create"))
            continue
        added = service.add(
            entry.title, entry.acceptance, mission_id=target, owner=entry.owner, expected_evidence=entry.evidence,
            actor=actor, description=entry.description, priority=entry.priority,
            extra={"import": {"plan": plan.plan_id, "key": entry.key}},
        )
        target = target or added.mission_id  # les tâches suivantes rejoignent la mission de la première
        ids[entry.key] = added.task.id
        report.rows.append(ImportRow(entry.key, added.task.id, "created"))
    report.mission = target
    for entry in plan.entries:
        for dep in entry.depends_on:
            link = {"task": entry.key, "depends_on": dep}
            if not dry_run:
                before = ledger.get_task(ids[entry.key])
                had = before is not None and any(d.target == ids[dep] for d in before.dependencies)
                service.link(ids[entry.key], ids[dep], DependencyKind.BLOCKS, actor=actor)
                link["state"] = "already_linked" if had else "linked"
                link["task_id"], link["depends_on_id"] = ids[entry.key], ids[dep]
            report.links.append(link)
    return report
