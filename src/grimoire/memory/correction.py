"""Correction mémoire déléguée à un agent (P42).

Trois temps, et la mémoire n'est touchée qu'au dernier :

1. **Demander** (:func:`request_correction`) — « Demander une correction »
   ouvre une tâche (``TaskService.add``) avec sa description et un critère
   vérifiable *généré* depuis le résultat attendu (:data:`OUTCOMES`). L'état des
   entrées visées à cet instant est figé (empreintes) dans
   ``_grimoire-output/memory-corrections/<tâche>.json``.
2. **Proposer** (:func:`propose_correction`) — l'agent dépose sa correction comme
   proposition du mécanisme existant :mod:`grimoire.proposals` (type
   ``memory-correction``, accept/reject, #490). Il n'écrit jamais dans la
   mémoire : la charge utile reste dans le fichier de demande.
3. **Appliquer** (:func:`apply_correction`) — « Accepter » appelle
   ``MemoryManager`` ; le journal P14 porte ``proposed_by`` et ``applied_by``.

Une proposition dont une entrée visée a changé depuis la demande est
**périmée** (:func:`correction_status`) : l'application est refusée, et l'aperçu
compare la charge utile à l'état *actuel* des entrées.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.journal import fingerprint
from grimoire.memory.supersession import SUPERSEDED_KEY, is_retracted

__all__ = [
    "ARTIFACT_TYPE",
    "OUTCOMES",
    "acceptance_criterion",
    "apply_correction",
    "correction_status",
    "propose_correction",
    "request_correction",
]

ARTIFACT_TYPE = "memory-correction"
CORRECTS_KEY = "corrects"
SOURCE_KEY = "source"
_DIR = Path("_grimoire-output") / "memory-corrections"

#: Résultat attendu → libellé cockpit. Le critère généré en dépend.
OUTCOMES: dict[str, str] = {
    "replace": "Remplacer par une entrée corrigée",
    "merge": "Fusionner avec d'autres entrées",
    "attach_source": "Rattacher une source",
    "verify_decide": "Vérifier puis décider",
}

#: Les gestes qu'une proposition peut porter (« vérifier puis décider » choisit parmi eux).
_ACTIONS = ("replace", "merge", "attach_source", "retract")


def acceptance_criterion(outcome: str, entry_ids: list[str]) -> str:
    """Le critère vérifiable que la tâche porte, selon le résultat attendu."""
    if outcome not in OUTCOMES:
        raise ValueError(f"résultat attendu inconnu : {outcome!r} (attendu : {', '.join(OUTCOMES)})")
    ids = ", ".join(entry_ids)
    first = entry_ids[0]
    if outcome == "replace":
        return f"l'entrée {first} est remplacée par une entrée qui porte corrects: {first}"
    if outcome == "merge":
        return (
            f"les entrées {ids} sont remplacées par une seule entrée qui porte corrects: {ids}"
        )
    if outcome == "attach_source":
        return f"l'entrée {first} porte une source vérifiable (metadata.source), rattachée par un geste journalisé"
    return (
        f"l'entrée {first} est vérifiée, puis la décision (remplacer, rattacher une source, "
        "retirer) est appliquée et journalisée avec sa preuve"
    )


# ── Stockage de la demande ──────────────────────────────────────────────────


def _path(project_root: Path, task_id: str) -> Path:
    if not task_id or "/" in task_id or "\\" in task_id or task_id.startswith("."):
        raise ValueError(f"identifiant de tâche invalide : {task_id!r}")
    return project_root.resolve() / _DIR / f"{task_id}.json"


def _load(project_root: Path, task_id: str) -> dict[str, Any]:
    path = _path(project_root, task_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GrimoireMemoryError(f"demande de correction introuvable : {task_id}") from exc
    if not isinstance(data, dict):
        raise GrimoireMemoryError(f"demande de correction illisible : {task_id}")
    return data


def _save(project_root: Path, task_id: str, data: dict[str, Any]) -> None:
    path = _path(project_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _state(entry: Any) -> str:
    """Empreinte de l'état d'une entrée : texte, remplacement, retrait."""
    meta = getattr(entry, "metadata", None) or {}
    basis = json.dumps(
        [entry.text, str(meta.get(SUPERSEDED_KEY) or ""), bool(is_retracted(entry))], ensure_ascii=False
    )
    return fingerprint(basis)


def _open_manager(project_root: Path) -> Any:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    root = project_root.resolve()
    cfg = GrimoireConfig.from_yaml(root / "project-context.yaml")
    return MemoryManager.from_config(cfg, project_root=root)


# ── 1. Demander ─────────────────────────────────────────────────────────────


def request_correction(
    project_root: Path,
    *,
    entry_ids: list[str],
    outcome: str,
    description: str = "",
    actor: str = "workspace",
    manager: Any | None = None,
) -> dict[str, Any]:
    """Ouvrir la tâche de correction ; renvoie ``{task_id, criterion, ...}``."""
    from grimoire.missions.service import TaskService

    ids = [i.strip() for i in entry_ids if i and i.strip()]
    if not ids:
        raise ValueError("au moins une entrée à corriger est requise")
    if outcome == "merge" and len(ids) < 2:
        raise ValueError("fusionner demande au moins deux entrées")
    criterion = acceptance_criterion(outcome, ids)
    mgr = manager if manager is not None else _open_manager(project_root)
    base: dict[str, str] = {}
    for entry_id in ids:
        entry = mgr.recall(entry_id)
        if entry is None:
            raise ValueError(f"entrée introuvable : {entry_id}")
        base[entry_id] = _state(entry)

    title = f"Corriger la mémoire : {ids[0]}" + (f" (+{len(ids) - 1})" if len(ids) > 1 else "")
    body = (description or "").strip()
    full = (
        f"Résultat attendu : {OUTCOMES[outcome]}.\nEntrées : {', '.join(ids)}.\n"
        + (f"{body}\n" if body else "")
        + "Dépose ta correction par `grimoire memory correct propose <tâche> --action …` : une proposition "
        "à accepter ou refuser, sans écrire dans la mémoire."
    )
    added = TaskService(project_root.resolve()).add(
        title, (criterion,), description=full, actor=actor, origin="cockpit"
    )
    task_id = str(added.to_dict().get("task_id") or added.to_dict().get("id") or "")
    if not task_id:
        raise GrimoireMemoryError("la tâche de correction n'a pas d'identifiant")
    _save(
        project_root,
        task_id,
        {"task_id": task_id, "outcome": outcome, "entry_ids": ids, "base": base,
         "description": body, "requested_by": actor, "criterion": criterion},
    )
    return {"task_id": task_id, "criterion": criterion, "outcome": outcome, "entry_ids": ids}


# ── 2. Proposer ─────────────────────────────────────────────────────────────


def propose_correction(
    project_root: Path,
    task_id: str,
    *,
    action: str,
    text: str = "",
    source: str = "",
    reason: str = "",
    proposed_by: str = "",
) -> dict[str, Any]:
    """Déposer la correction comme proposition ``grimoire.proposals`` — aucune écriture mémoire."""
    from grimoire.proposals import create_manual_proposal

    request = _load(project_root, task_id)
    if action not in _ACTIONS:
        raise ValueError(f"geste inconnu : {action!r} (attendu : {', '.join(_ACTIONS)})")
    if action in ("replace", "merge") and not text.strip():
        raise ValueError("le texte corrigé est requis")
    if action == "attach_source" and not source.strip():
        raise ValueError("la source est requise")
    if action == "retract" and not reason.strip():
        raise ValueError("un retrait exige une raison")
    if action == "merge" and len(request["entry_ids"]) < 2:
        raise ValueError("fusionner demande au moins deux entrées")
    request["proposal"] = {
        "action": action, "text": text.strip(), "source": source.strip(),
        "reason": reason.strip(), "proposed_by": proposed_by.strip(),
    }
    slug = f"correction-{task_id.lower()}"
    request["proposal_slug"] = slug
    _save(project_root, task_id, request)
    proposal = create_manual_proposal(
        project_root,
        slug=slug,
        specialty=f"Corriger {', '.join(request['entry_ids'])} ({OUTCOMES.get(request['outcome'], action)})",
        artifact_type=ARTIFACT_TYPE,
        category="memoire",
        carrier_reason=f"proposé par {proposed_by or 'un agent'} — tâche {task_id}",
        artifact_ref=task_id,
    )
    return {"slug": proposal.slug, "task_id": task_id, "action": action}


# ── État : périmée ? aperçu ─────────────────────────────────────────────────


def correction_status(project_root: Path, task_id: str, *, manager: Any | None = None) -> dict[str, Any]:
    """``{stale, stale_ids, preview}`` — l'aperçu compare la charge utile à l'état *actuel*."""
    request = _load(project_root, task_id)
    mgr = manager if manager is not None else _open_manager(project_root)
    stale_ids: list[str] = []
    current: dict[str, str] = {}
    for entry_id in request["entry_ids"]:
        entry = mgr.recall(entry_id)
        current[entry_id] = entry.text if entry is not None else ""
        if entry is None or _state(entry) != request["base"].get(entry_id):
            stale_ids.append(entry_id)
    proposal = request.get("proposal") or {}
    return {
        "task_id": task_id,
        "stale": bool(stale_ids),
        "stale_ids": stale_ids,
        "preview": {
            "action": proposal.get("action", ""),
            "current": current,
            "proposed": proposal.get("text") or proposal.get("source") or proposal.get("reason", ""),
            "proposed_by": proposal.get("proposed_by", ""),
        },
    }


# ── 3. Appliquer ────────────────────────────────────────────────────────────


def apply_correction(
    project_root: Path,
    task_id: str,
    *,
    applied_by: str,
    manager: Any | None = None,
    open_manager: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Appliquer la proposition par ``MemoryManager`` ; refuse une proposition périmée."""
    request = _load(project_root, task_id)
    proposal = request.get("proposal")
    if not proposal:
        raise GrimoireMemoryError(f"aucune correction déposée pour {task_id}")
    mgr = manager if manager is not None else (open_manager or _open_manager)(project_root)
    status = correction_status(project_root, task_id, manager=mgr)
    if status["stale"]:
        raise GrimoireMemoryError(
            f"proposition périmée : {', '.join(status['stale_ids'])} a changé depuis la demande — "
            "relire l'état actuel et redemander"
        )
    ids: list[str] = request["entry_ids"]
    proposed_by = str(proposal.get("proposed_by") or "")
    action = str(proposal["action"])
    extra = {"proposed_by": proposed_by, "applied_by": applied_by, "correction_task": task_id}
    note = f"correction proposée par {proposed_by or 'un agent'}, appliquée par {applied_by}"
    ctx = {"actor": applied_by, "task_ref": task_id}

    if action in ("replace", "merge"):
        first = mgr.recall(ids[0])
        created = mgr.store(
            str(proposal["text"]),
            tags=tuple(getattr(first, "tags", ()) or ()),
            metadata={CORRECTS_KEY: ",".join(ids) if action == "merge" else ids[0]},
            reason=note,
            **ctx,
        )
        for entry_id in ids:
            mgr.supersede(entry_id, created.id, reason=note, journal_extra=extra, **ctx)
        result = {"action": action, "created": created.id, "replaced": ids}
    elif action == "attach_source":
        entry = mgr.recall(ids[0])
        if entry is None:
            raise GrimoireMemoryError(f"entrée introuvable : {ids[0]}")
        mgr.update(
            ids[0], metadata={**(entry.metadata or {}), SOURCE_KEY: str(proposal["source"])},
            reason=note, journal_extra=extra, **ctx,
        )
        result = {"action": action, "updated": ids[0]}
    else:
        mgr.retract(ids[0], reason=str(proposal.get("reason") or note), **ctx)
        result = {"action": action, "retracted": ids[0]}
    request["applied_by"] = applied_by
    _save(project_root, task_id, request)
    return result
