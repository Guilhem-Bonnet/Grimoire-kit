"""Ce que l'inspecteur de tâche du cockpit montre AVANT une transition (P11).

Trois lectures, aucune écriture :

- :func:`routing_fields` — la classe de vérifiabilité (V0/V1/V2), le modèle
  qu'elle recommande et son palier : le même calcul que ``grimoire task
  dispatch`` et ``task_claim`` (:mod:`grimoire.missions.dispatch_advice`),
  jamais une seconde classification ;
- :func:`gate_entry` — les conditions d'un passage (``check_transition``, le
  verdict même qui refuse une écriture), vertes ou rouges avec raison et remède ;
- :func:`last_gate_refusal` — le dernier ``task.gate.refused`` de la tâche, lu
  dans le TraceLedger où :meth:`TaskService._record_refusal` l'écrit.

Le cockpit affichait déjà les conditions du seul passage suivant ; un refus
passé (une clôture tentée par un agent, la veille) ne se lisait que dans la
timeline. :func:`gate_checks` rend chaque passage déclaré depuis la colonne
courante et marque, sur chaque condition, la date du dernier refus qui l'a
nommée — l'humain voit ce qui a déjà bloqué avant de rejouer le même geste.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["gate_checks", "gate_entry", "last_gate_refusal", "refused_target", "routing_fields"]


def routing_fields(task: Any) -> dict[str, Any]:
    """Classe de vérifiabilité, modèle recommandé (``None`` sur V2) et palier (jamais vide)."""
    from grimoire.missions.dispatch_advice import model_tier_for, recommended_model_for
    from grimoire.missions.verifiability import classify

    verifiability = classify(task)
    return {
        "verifiability_class": verifiability.value,
        "recommended_model": recommended_model_for(verifiability),
        "model_tier": model_tier_for(verifiability),
    }


def gate_entry(
    project_root: Path, task: Any, here: str, target: str, declared: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """Les conditions du passage ``here → target`` ; ``None`` si le verdict ne se lit pas.

    Chaque preuve exigée par le fichier de gates figure, verte (``ok``) ou rouge
    avec sa raison et son remède ; un refus qui nomme une preuve non déclarée
    (exigence implicite du gate) s'ajoute à la suite. Une transition non
    déclarée rend ``conditions: []`` : la vue n'invente rien.

    La clôture d'une tâche relue (``review → accepted``) exige en plus le reçu de
    revue, comme :meth:`TaskService.gate` — le verdict qui refuse vraiment
    l'écriture. Sans lui, le cockpit montrait un passage « prêt » que la
    clôture refusait ensuite.
    """
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.gates import GateVerdict, check_transition

    try:
        verdict = check_transition(project_root, task, here, target)
        if (here, target) == ("review", "accepted"):
            from grimoire.missions.review_receipt import check_receipt

            receipt = check_receipt(project_root, task)
            if receipt is not None:
                verdict = GateVerdict("review_receipt", "hard_fail", (receipt, *verdict.refusals))
    except (GrimoireError, OSError, ValueError):
        return None
    entry = declared.get((here, target)) or {}
    required = [n for n in (entry.get("required_evidence") or []) if isinstance(n, str)]
    refused = {r.evidence: r for r in verdict.refusals}
    names = required + [n for n in refused if n not in required]
    return {
        "to": target,
        "transition_id": verdict.transition_id,
        "strictness": verdict.strictness,
        "conditions": [
            {
                "gate": name,
                "ok": name not in refused,
                "reason": refused[name].reason if name in refused else "",
                "remedy": refused[name].remedy if name in refused else "",
            }
            for name in names
        ],
    }


def last_gate_refusal(project_root: Path, task_id: str) -> dict[str, Any] | None:
    """Le dernier refus de gate journalisé pour *task_id*, ou ``None``.

    Best-effort comme son écriture : un TraceLedger absent ou illisible n'est
    pas un refus, la vue dit alors seulement qu'aucun refus n'a été lu.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.missions.trace import TASK_GATE_RECIPE

    path = project_root / TRACES_DIR
    if not path.is_dir():
        return None
    try:
        from grimoire.traces.ledger import TraceLedger

        traces = [t for t in TraceLedger(path).list_traces(task_id=task_id) if t.recipe_id == TASK_GATE_RECIPE]
    except Exception:  # lecture d'observabilité : jamais au prix de l'inspecteur
        return None
    if not traces:
        return None
    last = max(traces, key=lambda t: t.started_at)
    return {
        "at": last.started_at,
        "transition_id": next((tag for tag in last.tags if tag != "task.gate"), ""),
        "to": refused_target(last.policy_verdicts),
        "missing": [pv.verdict_id for pv in last.policy_verdicts],
        "actor": last.agent_id or "",
    }


def refused_target(verdicts: Any) -> str:
    """La colonne visée par le refus, lue dans ``action_kind`` (``task.transition:<de>-><vers>``)."""
    from grimoire.missions.board import board_status_of
    from grimoire.missions.schemas import TaskState

    for verdict in verdicts:
        kind = str(getattr(verdict, "action_kind", "") or "")
        if kind.startswith("task.transition:") and "->" in kind:
            try:
                return board_status_of(TaskState(kind.rsplit("->", 1)[1]))
            except (ValueError, KeyError):
                return ""
    return ""


def gate_checks(
    project_root: Path, task: Any, here: str, targets: list[str], refusal: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Un :func:`gate_entry` par passage déclaré, chaque condition datée de son dernier refus.

    ``refused_at`` vaut l'horodatage du dernier ``task.gate.refused`` quand il
    portait sur ce même passage (même colonne visée) et nommait cette preuve ;
    sinon ``""``.
    """
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.gates import declared_transitions

    try:
        declared = declared_transitions(project_root)
    except GrimoireError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for target in targets:
        entry = gate_entry(project_root, task, here, target, declared)
        if entry is None:
            continue
        at = refusal["at"] if refusal is not None and refusal["to"] == target else ""
        missing = set(refusal["missing"]) if refusal is not None and at else set()
        for condition in entry["conditions"]:
            condition["refused_at"] = at if condition["gate"] in missing else ""
        out[target] = entry
    return out
