"""``grimoire flow doctor`` — rejoue le journal d'un run et vérifie ses invariants (W1-03b).

Un run vit dans trois fichiers sans transaction commune : l'instance
(``instances.jsonl``), le journal (``run_events.jsonl`` + ``checkpoints.jsonl``)
et la méta de run (``flows/<run>.json``). Chaque écriture est atomique (W1-02),
rien ne garantit la cohérence ENTRE elles.

**Le journal d'événements fait autorité**, avec une exception qui est celle du
moteur : le dernier checkpoint de ``checkpoints.jsonl`` compte comme « node
franchi » (``FlowEngine._reconcile``). Le docteur dérive du journal ce que le
run devrait être (nodes franchis, node ouvert, statut) et compare le reste à
cette dérivation ; un checkpoint dont les événements manquent est une écriture
INTERROMPUE, pas un node encore ouvert : le docteur la signale et ne propose
que de rouler en avant (``flow resume``), jamais de ramener le statut en
arrière. Il est strictement en lecture seule : une réparation est
*proposée* (champ ``repair`` de chaque violation), jamais appliquée — la seule
écriture de réparation du moteur est la complétion d'un checkpoint interrompu
par ``resume``, signalée dans ``ResumeOutcome.repaired``.

Le module est pur : :func:`diagnose` ne lit aucun fichier. Il reçoit ce que
:meth:`grimoire.flows.engine.FlowEngine.doctor` a chargé, ce qui permet de
tester un journal altéré sans rien écrire sur disque.

Invariants vérifiés (un code par violation) :

- ``meta_missing`` / ``instance_missing`` / ``meta_order_invalid`` /
  ``meta_run_id_mismatch`` : les trois fichiers existent et se désignent ;
- ``workflow_started_missing`` : un node s'ouvre sans ``workflow.started`` ;
- ``multiple_open_nodes`` : un seul node ouvert à la fois ;
- ``step_skipped`` / ``step_unknown`` / ``step_reopened`` / ``step_duplicated`` : les nodes se
  franchissent dans l'ordre de la méta, chacun une fois ;
- ``step_completed_without_start`` : un node franchi sans avoir été ouvert ;
- ``checkpoint_event_missing`` / ``step_completed_missing`` : tout checkpoint a
  ses deux événements ;
- ``checkpoint_duplicate`` / ``checkpoint_order_mismatch`` : un checkpoint par
  node, ses ``completed_steps`` sont le préfixe de l'ordre ;
- ``checkpoint_refs_mismatch`` : les références de l'instance sont les
  checkpoints du fichier, dans l'ordre ;
- ``instance_ahead_of_journal`` / ``instance_behind_journal`` /
  ``status_conflict`` : le statut de l'instance est celui que le journal dérive ;
- ``completion_pending`` : tous les nodes sont franchis mais le run n'est pas
  terminé (fenêtre d'un crash avant ``complete``) ;
- ``current_node_mismatch`` : le node courant calculé par le moteur est celui
  que le journal désigne.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from grimoire.flows.schemas import FlowRunMeta
from grimoire.runtime.schemas import Checkpoint, RunEvent, RunEventType, WorkflowInstance, WorkflowStatus

__all__ = ["FlowDoctorReport", "FlowViolation", "diagnose"]

#: Ce que le docteur ne sait pas conclure — toujours rendu, jamais passé sous silence.
_BLUEPRINT_DRIFT_UNEVALUATED = (
    "blueprint : l'ordre de la méta n'est pas comparé au blueprint source "
    "(il peut avoir été édité légitimement depuis le démarrage du run)"
)

_RESUME = "relancer `grimoire flow resume` avec la dernière sortie envoyée : il complète l'écriture interrompue"

#: Réparation PROPOSÉE par code. Jamais exécutée par le docteur.
_REPAIRS: dict[str, str] = {
    "meta_missing": "relancer `grimoire flow run` : un run sans méta n'a jamais été rendu à l'hôte (crash pendant `run`)",
    "instance_missing": "restaurer instances.jsonl depuis une sauvegarde, ou abandonner ce run",
    "meta_order_invalid": "restaurer la méta du run depuis le blueprint (`grimoire flow run`) ; ne pas l'éditer à la main",
    "meta_run_id_mismatch": "renommer la méta d'après son run_id, ou restaurer la méta d'origine",
    "workflow_started_missing": "le journal a perdu `workflow.started` : restaurer run_events.jsonl ou relancer le run",
    "multiple_open_nodes": "restaurer run_events.jsonl : un événement `step.completed` a disparu ou a été inséré",
    "step_unknown": "restaurer la méta ou run_events.jsonl : le journal parle d'un node que le run ne connaît pas",
    "step_skipped": "restaurer run_events.jsonl ; si impossible, abandonner (`flow abort`) et relancer le run",
    "step_reopened": "restaurer run_events.jsonl : un node franchi ne se rouvre pas",
    "step_duplicated": "restaurer run_events.jsonl : un node ne se franchit qu'une fois",
    "step_completed_without_start": "restaurer run_events.jsonl : l'événement `step.started` a disparu",
    "checkpoint_event_missing": _RESUME,
    "step_completed_missing": _RESUME,
    "checkpoint_duplicate": "retirer la ligne en double de checkpoints.jsonl (le premier checkpoint du node fait foi)",
    "checkpoint_order_mismatch": "restaurer checkpoints.jsonl ; l'ordre des nodes vient de la méta",
    "checkpoint_refs_mismatch": _RESUME,
    "instance_ahead_of_journal": (
        "le journal fait autorité : ramener le statut de l'instance à celui que dérive le journal, "
        "ou restaurer les événements manquants"
    ),
    "instance_behind_journal": _RESUME,
    "status_conflict": "le journal fait autorité : aligner le statut de l'instance dessus",
    "completion_pending": "renvoyer la dernière sortie acceptée avec `grimoire flow resume` : le run se termine",
    "current_node_mismatch": "aligner le statut de l'instance sur le journal (voir les autres violations)",
}

# Rang d'avancement d'un statut, pour dire « en avance » ou « en retard » ; deux
# statuts de même rang sont un conflit, pas un décalage.
_RANK: dict[str, int] = {
    "created": 0,
    "running": 1,
    "blocked": 2,
    "paused": 2,
    "checkpointed": 2,
    "completed": 3,
    "verified": 4,
    "aborted": 5,
    "refused": 5,
}


@dataclass(frozen=True, slots=True)
class FlowViolation:
    """Un invariant rompu : son code, ce qu'on a vu, et la réparation proposée."""

    code: str
    detail: str
    node_id: str | None = None
    repair: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "node_id": self.node_id, "repair": self.repair}


@dataclass(frozen=True, slots=True)
class FlowDoctorReport:
    """Verdict du docteur sur un run. ``ok`` n'est vrai que sans aucune violation."""

    run_id: str
    ok: bool
    violations: tuple[FlowViolation, ...] = ()
    #: Contrôles que le docteur n'a pas pu ou voulu évaluer — jamais un « ok » implicite.
    unevaluated: tuple[str, ...] = ()
    #: Le journal d'événements fait autorité ; écrit dans le rapport pour que le lecteur le sache.
    authority: str = "events"
    #: Ce que le docteur a lu, pour qu'un « ok » ne se lise pas comme « rien vu ».
    checked: dict[str, int] | None = None

    @property
    def repairs(self) -> tuple[str, ...]:
        """Les réparations proposées, sans doublon, dans l'ordre des violations."""
        seen: dict[str, None] = {}
        for violation in self.violations:
            if violation.repair:
                seen.setdefault(violation.repair, None)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "authority": self.authority,
            "violations": [v.to_dict() for v in self.violations],
            "repairs": list(self.repairs),
            "unevaluated": list(self.unevaluated),
            "checked": dict(self.checked or {}),
        }


def _violation(code: str, detail: str, node_id: str | None = None) -> FlowViolation:
    return FlowViolation(code=code, detail=detail, node_id=node_id, repair=_REPAIRS.get(code, ""))


def _derive_from_events(
    events: Sequence[RunEvent], order: Sequence[str]
) -> tuple[list[str], str | None, str, list[FlowViolation]]:
    """Rejoue les événements : (nodes franchis, node ouvert, statut dérivé, violations)."""
    violations: list[FlowViolation] = []
    done: list[str] = []
    open_node: str | None = None
    started_seen = False
    status = "created"
    for event in events:
        kind = event.event_type
        step = event.payload.get("step_id")
        if kind is RunEventType.WORKFLOW_STARTED:
            started_seen = True
            status = "running"
        elif kind is RunEventType.STEP_STARTED:
            status = "running"
            if not started_seen:
                violations.append(_violation("workflow_started_missing", f"node {step} ouvert sans workflow.started", step))
                started_seen = True  # une seule fois, pas une violation par node
            if step not in order:
                violations.append(_violation("step_unknown", f"node {step!r} absent de l'ordre de la méta", step))
            elif step in done:
                violations.append(_violation("step_reopened", f"node {step} rouvert après avoir été franchi", step))
            elif open_node is not None and open_node != step:
                violations.append(
                    _violation("multiple_open_nodes", f"node {step} ouvert alors que {open_node} ne s'est pas fermé", step)
                )
            elif step in order and len(done) < len(order) and step != order[len(done)]:
                violations.append(
                    _violation("step_skipped", f"node {step} ouvert alors que {order[len(done)]} était attendu", step)
                )
            open_node = step
        elif kind is RunEventType.STEP_FAILED:
            status = "blocked"
        elif kind is RunEventType.CHECKPOINT_SAVED:
            status = "checkpointed"
        elif kind is RunEventType.STEP_COMPLETED:
            status = "checkpointed"
            if step in done:
                violations.append(_violation("step_duplicated", f"node {step} franchi plusieurs fois", step))
            elif step not in order or len(done) >= len(order) or step != order[len(done)]:
                expected = order[len(done)] if len(done) < len(order) else "(aucun)"
                violations.append(_violation("step_skipped", f"node {step} franchi alors que {expected} était attendu", step))
                if step is not None:
                    done.append(step)
            else:
                if open_node != step:
                    violations.append(
                        _violation("step_completed_without_start", f"node {step} franchi sans avoir été ouvert", step)
                    )
                done.append(step)
            if open_node == step:
                open_node = None
        elif kind is RunEventType.WORKFLOW_COMPLETED:
            status = "completed"
        elif kind is RunEventType.WORKFLOW_ABORTED:
            status = "aborted"
        elif kind is RunEventType.WORKFLOW_REFUSED:
            status = "refused"
    return done, open_node, status, violations


def _status_violation(actual: str, derived: str) -> FlowViolation | None:
    if actual == derived or (actual == "verified" and derived == "completed"):
        return None
    detail = f"statut de l'instance {actual!r}, le journal dérive {derived!r}"
    rank_actual, rank_derived = _RANK.get(actual, -1), _RANK.get(derived, -1)
    if rank_actual > rank_derived:
        return _violation("instance_ahead_of_journal", detail)
    if rank_actual < rank_derived:
        return _violation("instance_behind_journal", detail)
    return _violation("status_conflict", detail)


def diagnose(
    *,
    run_id: str,
    meta: FlowRunMeta | None,
    instance: WorkflowInstance | None,
    events: Sequence[RunEvent],
    checkpoints: Sequence[Checkpoint],
    current_node: str | None,
) -> FlowDoctorReport:
    """Vérifie les invariants d'un run. Pur : ne lit ni n'écrit aucun fichier.

    ``current_node`` est le node courant que le moteur calcule pour ce run
    (:meth:`FlowEngine._current_node`) ; ``None`` quand le run est terminal ou
    entièrement franchi.
    """
    violations: list[FlowViolation] = []
    unevaluated: list[str] = [_BLUEPRINT_DRIFT_UNEVALUATED]
    checked = {"events": len(events), "checkpoints": len(checkpoints), "nodes": len(meta.order) if meta else 0}

    if meta is None:
        violations.append(_violation("meta_missing", f"aucune méta de run pour {run_id}"))
    if instance is None:
        violations.append(_violation("instance_missing", f"aucune instance pour {run_id} dans le kernel"))
    if meta is None or instance is None:
        unevaluated.append("journal : non rejoué (méta ou instance absente)")
        return FlowDoctorReport(
            run_id=run_id, ok=False, violations=tuple(violations), unevaluated=tuple(unevaluated), checked=checked
        )

    order = list(meta.order)
    if meta.run_id != run_id:
        violations.append(_violation("meta_run_id_mismatch", f"la méta désigne {meta.run_id!r}, pas {run_id!r}"))
    if not order or len(set(order)) != len(order):
        violations.append(_violation("meta_order_invalid", f"ordre de la méta vide ou avec doublon : {order}"))

    done, open_node, derived_status, replay_violations = _derive_from_events(events, order)
    violations.extend(replay_violations)

    real = [c for c in checkpoints if c.step_id != "refused"]
    # Le fichier de checkpoints est écrit AVANT le statut et les événements : un
    # dernier checkpoint dont le node est encore « ouvert » dans le journal est
    # un node franchi dont l'écriture a été interrompue (W1-03b-S2). Le moteur le
    # lit ainsi (`_current_node`, `_reconcile`) ; le docteur aussi, sinon il
    # désignerait ce node comme courant et proposerait de revenir en arrière.
    # Les violations de l'écriture interrompue (`checkpoint_event_missing`,
    # `step_completed_missing`, `checkpoint_refs_mismatch`) proposent déjà `resume`.
    if (
        real
        and open_node is not None
        and real[-1].step_id == open_node
        and len(done) < len(order)
        and order[len(done)] == open_node
        and derived_status in ("running", "checkpointed")
    ):
        done = [*done, open_node]
        open_node = None
        derived_status = "checkpointed"

    # Checkpoints : un par node, préfixe de l'ordre, avec leurs deux événements.
    saved_ids = {
        e.payload.get("checkpoint_id") for e in events if e.event_type is RunEventType.CHECKPOINT_SAVED
    }
    completed_ids = {e.payload.get("step_id") for e in events if e.event_type is RunEventType.STEP_COMPLETED}
    seen_steps: set[str] = set()
    for chk in real:
        if chk.step_id in seen_steps:
            violations.append(_violation("checkpoint_duplicate", f"deux checkpoints pour le node {chk.step_id}", chk.step_id))
            continue
        seen_steps.add(chk.step_id)
        if chk.step_id in order:
            idx = order.index(chk.step_id)
            if list(chk.state.completed_steps) != order[: idx + 1] or list(chk.state.pending_steps) != order[idx + 1 :]:
                violations.append(
                    _violation(
                        "checkpoint_order_mismatch",
                        f"checkpoint {chk.step_id} : completed={list(chk.state.completed_steps)} "
                        f"pending={list(chk.state.pending_steps)} ne suit pas l'ordre {order}",
                        chk.step_id,
                    )
                )
        if chk.id not in saved_ids:
            violations.append(_violation("checkpoint_event_missing", f"checkpoint {chk.id} sans événement checkpoint.saved", chk.step_id))
        if chk.step_id not in completed_ids:
            violations.append(_violation("step_completed_missing", f"checkpoint {chk.id} sans événement step.completed", chk.step_id))
    expected_refs = [c.id for c in checkpoints]
    if list(instance.checkpoint_refs) != expected_refs:
        violations.append(
            _violation(
                "checkpoint_refs_mismatch",
                f"références de l'instance {list(instance.checkpoint_refs)} != checkpoints du fichier {expected_refs}",
            )
        )

    # Statut : celui de l'instance contre celui que le journal dérive.
    status_violation = _status_violation(instance.status.value, derived_status)
    if status_violation is not None:
        violations.append(status_violation)

    terminal = instance.status in (
        WorkflowStatus.COMPLETED,
        WorkflowStatus.VERIFIED,
        WorkflowStatus.ABORTED,
        WorkflowStatus.REFUSED,
    )
    if not terminal and order and len(done) == len(order) and open_node is None:
        violations.append(_violation("completion_pending", f"les {len(order)} nodes sont franchis mais le run n'est pas terminé"))

    # Node courant : celui du moteur contre celui que le journal désigne.
    if not terminal and derived_status not in ("completed", "aborted", "refused"):
        expected_current = open_node if open_node is not None else (order[len(done)] if len(done) < len(order) else None)
        if current_node != expected_current:
            violations.append(
                _violation(
                    "current_node_mismatch",
                    f"le moteur désigne {current_node!r}, le journal désigne {expected_current!r}",
                    expected_current,
                )
            )

    return FlowDoctorReport(
        run_id=run_id,
        ok=not violations,
        violations=tuple(violations),
        unevaluated=tuple(unevaluated),
        checked=checked,
    )
