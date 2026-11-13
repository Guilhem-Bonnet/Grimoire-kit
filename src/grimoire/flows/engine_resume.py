"""Les étapes de ``FlowEngine._resume_core`` — une sortie soumise, jugée puis rangée.

``_resume_core`` (:mod:`grimoire.flows.engine`) garde le fil : charger le run,
réconcilier une écriture interrompue, résoudre le node courant, rouvrir le
node. Les cinq étapes qu'il appelle, dans cet ordre, vivent ici pour que
``engine.py`` reste sous le seuil du cliquet de taille (R2) :

- :func:`resume_terminal` — le run est déjà terminal ;
- :func:`resume_all_checkpointed` — tous les nodes sont checkpointés, seul
  ``complete`` manque ;
- :func:`resume_judge` — défauts de contrat et décision d'hôte d'un checkpoint ;
- :func:`resume_refuse` — la sortie est refusée ;
- :func:`resume_accept` — la sortie est acceptée.

Chaque étape reçoit le moteur en premier argument et ne fait que ce que le
bloc d'origine faisait, dans le même ordre.

Ce module n'importe pas :mod:`grimoire.flows.engine` à son chargement (seul
le typage le nomme) : c'est le moteur qui l'importe, jamais l'inverse. Les
aides du moteur dont une étape a besoin (empreintes, ``_refusal``, pont Rust)
sont importées dans le corps de l'étape, à l'appel — elles restent définies à
un seul endroit, et un test qui remplace ``grimoire.flows.engine._rust_core``
remplace bien ce que l'étape utilise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.schemas import ResumeOutcome
from grimoire.runtime.schemas import RunEventType, WorkflowStatus

if TYPE_CHECKING:
    from grimoire.flows.attestation import CheckpointGate
    from grimoire.flows.engine import FlowEngine
    from grimoire.flows.executor import NodeExecutor
    from grimoire.flows.schemas import FlowRunMeta, NodeContract
    from grimoire.runtime.schemas import ExecutionContext, WorkflowInstance

__all__ = [
    "resume_accept",
    "resume_all_checkpointed",
    "resume_judge",
    "resume_refuse",
    "resume_terminal",
]


def resume_terminal(
    engine: FlowEngine, run_id: str, wfi: WorkflowInstance, meta: FlowRunMeta, output: dict[str, Any],
    allow_replay: bool,
) -> ResumeOutcome:
    """Run déjà terminal : renvoyer la dernière sortie acceptée est un rejeu, tout autre appel est refusé."""
    from grimoire.flows.engine import _output_digest

    if allow_replay and wfi.status in (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED):
        # Le dernier node a été accepté puis la réponse s'est perdue :
        # le même renvoi sur un run terminé réussi est un rejeu.
        digest = _output_digest(output)
        crossed = engine._crossed_node_for_digest(wfi, digest) if digest is not None else None
        if crossed is not None:
            repaired: tuple[str, ...] = ()
            if not any(e.event_type is RunEventType.WORKFLOW_COMPLETED for e in engine._kernel.get_run_events(wfi.id)):
                # Le statut COMPLETED est écrit avant son événement : un
                # crash entre les deux laisse le journal sans clôture.
                engine._kernel.complete(wfi.id, engine._ctx_for(wfi))
                repaired = ("événement workflow.completed manquant rétabli",)
            return ResumeOutcome(
                ok=True,
                finished=True,
                node_id=meta.order[-1],
                replay=True,
                replay_of=crossed,
                repaired=repaired,
            )
    raise GrimoireRuntimeError(f"run {run_id} est {wfi.status.value}, rien à reprendre")


def resume_all_checkpointed(
    engine: FlowEngine, run_id: str, wfi: WorkflowInstance, ctx: ExecutionContext, digest: str | None,
    allow_replay: bool, repaired_notes: tuple[str, ...],
) -> ResumeOutcome | None:
    """Run CHECKPOINTED sans node courant : le clôt sur sa dernière sortie acceptée ; ``None`` s'il reste des nodes."""
    from grimoire.flows.engine import _checkpoint_digest, _refusal

    last_checkpoint = engine._kernel.list_checkpoints(wfi.id)[-1:]
    if last_checkpoint and not last_checkpoint[0].state.pending_steps:
        # Tous les nodes sont checkpointés ; seul ``complete`` n'a pas eu
        # lieu. Terminer sur la foi de la dernière sortie ACCEPTÉE : la
        # renvoyer suffit, toute autre est refusée (jamais de clôture
        # sur une sortie que ce run n'a pas vue).
        last = last_checkpoint[0]
        if allow_replay and digest is not None and digest == _checkpoint_digest(last):
            engine._kernel.complete(wfi.id, ctx)
            return ResumeOutcome(
                ok=True,
                finished=True,
                node_id=last.step_id,
                replay=True,
                replay_of=last.step_id,
                repaired=(*repaired_notes, "run terminé : tous les nodes étaient déjà checkpointés"),
            )
        raise _refusal(
            f"run {run_id} : tous les nodes sont déjà validés (dernier : {last.step_id}) mais le run n'est pas "
            f"terminé ; renvoyer la sortie acceptée du node {last.step_id} pour le terminer, "
            "ou `grimoire flow doctor` / `grimoire flow abort`",
            run_id,
            repaired_notes,
        )
    return None


def resume_judge(
    engine: FlowEngine, run_id: str, wfi: WorkflowInstance, blueprint: dict[str, Any], contract: NodeContract,
    current_id: str, output: dict[str, Any], digest: str | None,
) -> tuple[list[str], tuple[str, ...], CheckpointGate | None]:
    """Les défauts de la sortie soumise, ceux du seul contrat, et le classement de la décision d'un checkpoint."""
    from grimoire.flows.engine import check_output_against_contract, checkpoint_decision_faults

    faults = check_output_against_contract(contract, output)
    #: Le gate du node : une sortie refusée par son contrat révoque les octrois qui couvrent le run (W5-06).
    contract_faults = tuple(faults)
    if contract.kind == "checkpoint":
        if wfi.status is WorkflowStatus.PAUSED and (digest is None or engine._crossed_node_for_digest(wfi, digest) is None):
            # K11 : pause levée, checkpoint jamais présenté : le challenge précède le jugement, sinon la décision
            # courante serait refusée en `no_challenge` alors que personne n'a pu y répondre. (Pas pour un rejeu.)
            engine._present(run_id, contract, blueprint)
        # Issue #207 : la décision d'hôte ("approve"/"reject"/"amend")
        # n'est pas une pin — jamais franchie côté Rust
        # (`check_output_against_contract` ci-dessus n'en sait toujours
        # rien) — vérifiée ici, Python-only, au même endroit que le
        # reste de la précondition de resume().
        faults = [*faults, *checkpoint_decision_faults(output)]
        from grimoire.flows.attestation import (
            DEFAULT_MAX_AMENDMENTS,
            destructive_checkpoints,
            judge_checkpoint,
            max_amendments,
        )

        gate = judge_checkpoint(
            engine._authority(run_id),
            engine._project_root,
            current_id,
            output,
            destructive=current_id in destructive_checkpoints(blueprint),
            cap=max_amendments(blueprint).get(current_id, DEFAULT_MAX_AMENDMENTS),
            plan=engine._projected_plan(blueprint, current_id),
            grant=lambda: engine._grant_lookup(run_id, current_id),
        )
        if gate is not None and gate.refusal:
            faults.append(gate.refusal)
    else:
        gate = None
    return faults, contract_faults, gate


def resume_refuse(
    engine: FlowEngine, run_id: str, wfi: WorkflowInstance, ctx: ExecutionContext, blueprint: dict[str, Any],
    contract: NodeContract, current_id: str, faults: list[str], contract_faults: tuple[str, ...],
    gate: CheckpointGate | None, repaired_notes: tuple[str, ...], attestation: dict[str, Any] | None,
) -> ResumeOutcome:
    """Sortie refusée : bloque le node courant, révoque les octrois, consigne la décision et présente de nouveau le node."""
    wfi = engine._kernel.fail_step(wfi.id, ctx, step_id=current_id, reason="; ".join(faults))
    if contract_faults:
        engine._grants().revoke_on_red_gate(run_id, current_id, contract_faults)
    if gate is not None and (gate.decision in ("reject", "amend") or gate.drifted):
        # Un refus, un amendement ou une dérive de plan (W2-11) est une
        # décision : il consomme son challenge, la présentation suivante
        # en émet un neuf (sur le plan courant, pour une dérive).
        engine._authority(run_id).record_decision(
            current_id,
            "plan_drifted" if gate.drifted else gate.decision,
            gate.refusal if gate.drifted else gate.reason,
            gate.attestation,
        )
        if gate.attestation.attested:
            engine._settle_linked_question(wfi, run_id, current_id, gate.decision)
    if gate is not None and gate.refusal and gate.refusal.startswith("awaiting_human"):
        engine._ask_linked_task(wfi, run_id, current_id, gate.attestation.explain())
    engine._present(run_id, contract, blueprint)
    return ResumeOutcome(
        ok=False,
        finished=False,
        node_id=current_id,
        faults=tuple(faults),
        contract=contract,
        repaired=repaired_notes,
        attestation=attestation,
    )


def resume_accept(
    engine: FlowEngine, run_id: str, wfi: WorkflowInstance, ctx: ExecutionContext, meta: FlowRunMeta,
    blueprint: dict[str, Any], contracts: dict[str, NodeContract], order: list[str], current_id: str,
    digest: str | None, gate: CheckpointGate | None, executor: NodeExecutor | None,
    repaired_notes: tuple[str, ...], attestation: dict[str, Any] | None, warnings: tuple[str, ...],
) -> ResumeOutcome:
    """Sortie acceptée : checkpointe le node, puis termine le run, le pose en pause (hold) ou ouvre le node suivant."""
    from grimoire.flows.engine import _OUTPUT_DIGEST_PREFIX, _refusal, _use_rust_backend, rust_core_module

    # Décision "termine / avance vers quel node" déléguée à
    # grimoire_flows_core.resume_outcome (issue #354) quand le backend
    # Rust est actif : elle nomme explicitement le cas "current_id
    # absent de order" (devrait ne jamais arriver — contracts et order
    # viennent du même blueprint) plutôt que de laisser `order.index`
    # lever un ValueError anonyme, comme le ferait la référence Python
    # ci-dessous si jamais cet invariant était violé.
    if _use_rust_backend():
        _rust_core = rust_core_module()
        assert _rust_core is not None  # guarded by _use_rust_backend
        try:
            _ok, finished, next_id, _faults = _rust_core.resume_outcome(order, current_id, [])
        except ValueError as exc:
            raise _refusal(str(exc), run_id, repaired_notes) from exc
    else:
        idx0 = order.index(current_id)
        finished = idx0 == len(order) - 1
        next_id = None if finished else order[idx0 + 1]

    idx = order.index(current_id)
    completed = order[: idx + 1]
    pending = order[idx + 1 :]
    engine._record_accepted_contract(meta, contracts, order, current_id)
    wfi, _chk = engine._kernel.checkpoint(
        wfi.id,
        ctx,
        step_id=current_id,
        completed_steps=completed,
        pending_steps=pending,
        evidence_refs=[_OUTPUT_DIGEST_PREFIX + digest] if digest is not None else None,
    )
    if gate is not None:
        engine._authority(run_id).record_decision(
            current_id, gate.decision, gate.reason, gate.attestation, gate.accepted_risk
        )
        if gate.accepted_risk is not None:
            engine._record_accepted_risk(wfi, run_id, current_id, gate.accepted_risk)
        if gate.attestation.attested:
            engine._settle_linked_question(wfi, run_id, current_id, gate.decision)
    if finished:
        engine._kernel.complete(wfi.id, ctx)
        return ResumeOutcome(
            ok=True,
            finished=True,
            node_id=current_id,
            contract=None,
            repaired=repaired_notes,
            attestation=attestation,
            warnings=warnings,
        )

    assert next_id is not None  # finished est False => resume_outcome a nommé le node suivant
    next_contract = contracts[next_id]
    held = engine._hold_in_force(wfi)
    if held:
        # K11 : la frontière de node est le seul endroit où un run se pose sans rien casser — le node vient
        # d'être franchi, le suivant n'est ni présenté, ni exécuté (un exécuteur par dispatch lancerait un agent).
        engine._kernel.pause(wfi.id, ctx, reason=held)
        return ResumeOutcome(
            ok=True,
            finished=False,
            node_id=next_id,
            contract=None,
            repaired=repaired_notes,
            attestation=attestation,
            warnings=(*warnings, held),
            paused=True,
        )
    engine._present(run_id, next_contract, blueprint)
    # Le node suivant n'est pas encore ouvert (pas de advance_step ici) :
    # un crash juste après cet appel laisse le kernel en CHECKPOINTED, et
    # le prochain resume() rouvrira ce même node suivant en premier —
    # jamais de node sauté, jamais de node rejoué deux fois.
    (executor or InteractiveNodeExecutor()).execute(
        next_contract, context_pack=engine._context_pack(wfi, meta.blueprint_id)
    )
    return ResumeOutcome(
        ok=True,
        finished=False,
        node_id=next_id,
        contract=next_contract,
        repaired=repaired_notes,
        attestation=attestation,
        warnings=warnings,
    )
