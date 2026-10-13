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
- ``step_failed_without_start`` : un refus (``step.failed``) ne porte que sur le
  node ouvert ;
- ``step_started_missing`` : une méta de run suppose au moins un ``step.started``
  (le moteur l'écrit avant la méta) ;
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

__all__ = ["FlowDoctorReport", "FlowViolation", "checkpoint_interrupted", "diagnose", "resume_realigns_status"]

#: Ce que le docteur ne sait pas conclure — toujours rendu, jamais passé sous silence.
_BLUEPRINT_DRIFT_UNEVALUATED = (
    "blueprint : l'ordre de la méta n'est pas comparé au blueprint source "
    "(il peut avoir été édité légitimement depuis le démarrage du run)"
)

_RESUME = "relancer `grimoire flow resume` avec la dernière sortie envoyée : il complète l'écriture interrompue"

#: Instance RUNNING alors que le journal s'arrête sur `step.failed` (crash entre la transition
#: `advance_step` et son `step.started`) : `resume` ne rouvre pas un run déjà RUNNING, il juge la
#: sortie du node bloqué et réécrit le statut (checkpoint ou `fail_step`), sans rien « compléter ».
_RESUME_BLOCKED = (
    "relancer `grimoire flow resume` avec la sortie du node bloqué : il réécrit le statut de l'instance "
    "(rien à compléter, le statut RUNNING avait déjà rouvert le node)"
)

#: Ce que `resume` ne répare PAS : il ne complète que le DERNIER checkpoint d'un run en cours
#: (`FlowEngine._reconcile`). Un événement perdu ailleurs reste perdu quoi qu'on relance.
_RESTORE_EVENTS = (
    "restaurer run_events.jsonl depuis une sauvegarde : `grimoire flow resume` ne complète que le dernier "
    "checkpoint d'un run en cours, pas celui-ci"
)
_RESTORE_REFS = (
    "restaurer instances.jsonl depuis une sauvegarde (ou aligner `checkpoint_refs` sur checkpoints.jsonl) : "
    "`grimoire flow resume` ne ressoude que la référence du dernier checkpoint d'un run en cours"
)
_RESTORE_STATUS = (
    "le journal fait autorité : aligner le statut de l'instance dessus ; "
    "`grimoire flow resume` ne le ferait pas dans cet état"
)

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
    "step_failed_without_start": "restaurer run_events.jsonl : l'événement `step.started` du node refusé a disparu",
    "step_started_missing": "restaurer run_events.jsonl : l'événement `step.started` a disparu",
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


def _violation(code: str, detail: str, node_id: str | None = None, repair: str | None = None) -> FlowViolation:
    return FlowViolation(code=code, detail=detail, node_id=node_id, repair=_REPAIRS.get(code, "") if repair is None else repair)


def checkpoint_interrupted(
    instance: WorkflowInstance, checkpoints: Sequence[Checkpoint], events: Sequence[RunEvent]
) -> bool:
    """Vrai quand ``resume`` complèterait le dernier checkpoint de ce run.

    Source unique de la condition de ``FlowEngine._reconcile`` : le docteur ne
    propose ``flow resume`` que là où le moteur répare réellement, sans
    pouvoir dériver de lui. Seul le DERNIER checkpoint d'un run RUNNING ou
    CHECKPOINTED est complété ; un run terminé ou un checkpoint antérieur ne le
    sont jamais.
    """
    if instance.status not in (WorkflowStatus.RUNNING, WorkflowStatus.CHECKPOINTED) or not checkpoints:
        return False
    last = checkpoints[-1]
    if instance.status is WorkflowStatus.RUNNING:
        started = next(
            (e.payload.get("step_id") for e in reversed(events) if e.event_type is RunEventType.STEP_STARTED), None
        )
        return bool(started == last.step_id)
    saved = any(
        e.event_type is RunEventType.CHECKPOINT_SAVED and e.payload.get("checkpoint_id") == last.id for e in events
    )
    completed = any(e.event_type is RunEventType.STEP_COMPLETED and e.payload.get("step_id") == last.step_id for e in events)
    return last.id not in instance.checkpoint_refs or not (saved and completed)


def resume_realigns_status(actual: str, derived: str, *, interrupted: bool) -> str | None:
    """La réparation de statut que ``resume`` accomplit réellement dans cet état, sinon ``None``.

    Source unique de ce que le docteur annonce pour ``instance_behind_journal``,
    à confronter à ``FlowEngine.resume`` (la matrice des frontières le fait pour
    chacune des 31 écritures) :

    - checkpoint à moitié écrit (``interrupted``, voir
      :func:`checkpoint_interrupted`) et journal running/checkpointed :
      ``_reconcile`` rejoue le checkpoint, ce qui réécrit statut, références et
      événements ;
    - instance RUNNING alors que le journal dérive BLOCKED : ``resume`` saute
      ``advance_step`` (le statut est déjà RUNNING), juge la sortie du node
      bloqué, puis ``checkpoint`` ou ``fail_step`` réécrit le statut et son
      événement.
    """
    if interrupted and derived in ("running", "checkpointed"):
        return _RESUME
    if actual == "running" and derived == "blocked":
        return _RESUME_BLOCKED
    return None


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
            # Un refus se juge sur le node ouvert : `engine.run` écrit `step.started` avant tout
            # `fail_step`, et un crash ne produit jamais un `step.failed` hors de ce node.
            if step != open_node:
                opened = f"le node ouvert est {open_node}" if open_node is not None else "aucun node n'est ouvert"
                violations.append(_violation("step_failed_without_start", f"node {step} refusé alors que {opened}", step))
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


def _status_violation(actual: str, derived: str, *, interrupted: bool) -> FlowViolation | None:
    if actual == derived or (actual == "verified" and derived == "completed"):
        return None
    detail = f"statut de l'instance {actual!r}, le journal dérive {derived!r}"
    rank_actual, rank_derived = _RANK.get(actual, -1), _RANK.get(derived, -1)
    if rank_actual > rank_derived:
        return _violation("instance_ahead_of_journal", detail)
    if rank_actual < rank_derived:
        repair = resume_realigns_status(actual, derived, interrupted=interrupted) or _RESTORE_STATUS
        return _violation("instance_behind_journal", detail, repair=repair)
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
    # `engine.run` écrit `step.started` AVANT la méta (frontière 4 avant 5) : une méta sans aucun
    # `step.started`, sur un run que le journal dit en cours ou bloqué, a perdu cet événement.
    # Sans lui, le moteur retombe sur `order[0]` et le journal, vide de tout node, semble sain.
    if derived_status in ("running", "blocked") and not any(e.event_type is RunEventType.STEP_STARTED for e in events):
        violations.append(
            _violation("step_started_missing", f"statut dérivé {derived_status!r} mais aucun step.started dans le journal")
        )

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
    # `resume` ne complète que le dernier checkpoint d'un run en cours : la réparation
    # `_RESUME` n'est proposée que là (même condition que `_reconcile`), sinon le
    # journal reste altéré après un `resume` réussi — voire après la fin du run.
    resumable = checkpoint_interrupted(instance, checkpoints, events)
    last_checkpoint = checkpoints[-1] if checkpoints else None
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
        repair = _RESUME if resumable and chk is last_checkpoint else _RESTORE_EVENTS
        if chk.id not in saved_ids:
            violations.append(
                _violation(
                    "checkpoint_event_missing", f"checkpoint {chk.id} sans événement checkpoint.saved", chk.step_id, repair
                )
            )
        if chk.step_id not in completed_ids:
            violations.append(
                _violation(
                    "step_completed_missing", f"checkpoint {chk.id} sans événement step.completed", chk.step_id, repair
                )
            )
    expected_refs = [c.id for c in checkpoints]
    if list(instance.checkpoint_refs) != expected_refs:
        # `resume` ne rajoute que la référence du dernier checkpoint, en fin de liste.
        refs_repair = _RESUME if resumable and list(instance.checkpoint_refs) == expected_refs[:-1] else _RESTORE_REFS
        violations.append(
            _violation(
                "checkpoint_refs_mismatch",
                f"références de l'instance {list(instance.checkpoint_refs)} != checkpoints du fichier {expected_refs}",
                repair=refs_repair,
            )
        )

    # Statut : celui de l'instance contre celui que le journal dérive.
    status_violation = _status_violation(instance.status.value, derived_status, interrupted=resumable)
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
