"""``FlowEngine`` — pilote le ``RuntimeKernel`` un node à la fois.

Un run garde deux sources de vérité, jamais une troisième :

- Le ``RuntimeKernel`` (checkpoints + journal d'événements, déjà append-only
  et rejouable) dit **où en est** le run : statut, node courant, dernier
  refus. ``_current_node`` ci-dessous ne fait que le lire, jamais un compteur
  à côté qui pourrait diverger après un crash — c'est exactement ce que le
  scénario de reprise du prototype #204 vérifiait déjà sur le kernel nu.
- :class:`~grimoire.flows.schemas.FlowRunMeta`, écrite une fois par ``run()``,
  dit **quoi** exécuter : le fichier blueprint et l'ordre topologique des
  nodes. Rien dans ce fichier ne change après sa création — il n'y a donc
  rien à faire diverger.

``resume()`` est le seul point qui mute l'état : il ramène le node courant en
RUNNING s'il ne l'est pas déjà (CHECKPOINTED entre deux nodes, ou BLOCKED
après un refus corrigé), vérifie la sortie soumise contre le contrat du node,
puis checkpointe et avance — ou bloque en nommant le node et la pin fautifs.
``status()`` ne mute jamais rien : un hôte qui relit l'état après un crash ne
risque pas de le faire progresser par erreur.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, topo_order
from grimoire.flows.executor import InteractiveNodeExecutor, NodeExecutor
from grimoire.flows.schemas import FlowRunMeta, FlowStatusView, NodeContract, ResumeOutcome
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext, RunEventType, WorkflowInstance, WorkflowStatus

__all__ = ["FlowEngine", "check_output_against_contract"]

_TERMINAL_STATUSES = (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED, WorkflowStatus.ABORTED)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def check_output_against_contract(contract: NodeContract, output: dict[str, Any]) -> list[str]:
    """Vérifie une sortie d'hôte contre les pins de sortie d'un node.

    Forme attendue : ``{"pins": {"<pin-id>": {"contract": "<nom>", ...}}}``.
    Chaque défaut nomme le node ET la pin — jamais un échec muet — pour que
    le flow suspendu dise exactement quoi corriger.
    """
    if not isinstance(output, dict) or not isinstance(output.get("pins"), dict):
        return [f"node={contract.node_id} : sortie sans objet 'pins' (forme attendue : {{'pins': {{...}}}})"]
    pins_out: dict[str, Any] = output["pins"]
    faults: list[str] = []
    for pin in contract.outputs:
        entry = pins_out.get(pin.pin_id)
        if not isinstance(entry, dict):
            faults.append(f"node={contract.node_id} pin={pin.pin_id} : absente de la sortie soumise")
            continue
        produced = entry.get("contract")
        if produced != pin.contract:
            faults.append(
                f"node={contract.node_id} pin={pin.pin_id} : contrat produit {produced!r} != attendu {pin.contract!r}"
            )
    return faults


class FlowEngine:
    """Coordonne blueprint, kernel et exécuteur pour un ou plusieurs runs."""

    def __init__(
        self,
        *,
        kernel_root: Path,
        flows_root: Path,
        actor_id: str = "cli",
        host_id: str = "local",
    ) -> None:
        self._kernel = RuntimeKernel(kernel_root)
        self._flows_root = flows_root
        self._flows_root.mkdir(parents=True, exist_ok=True)
        self._actor_id = actor_id
        self._host_id = host_id

    # ── Persistance des métadonnées de run ──────────────────────────────────

    def _meta_path(self, run_id: str) -> Path:
        return self._flows_root / f"{run_id}.json"

    def _save_meta(self, meta: FlowRunMeta) -> None:
        self._meta_path(meta.run_id).write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def _load_meta(self, run_id: str) -> FlowRunMeta:
        path = self._meta_path(run_id)
        if not path.is_file():
            raise GrimoireRuntimeError(f"run introuvable : {run_id}")
        return FlowRunMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_run_ids(self) -> list[str]:
        return sorted(p.stem for p in self._flows_root.glob("*.json"))

    def run_meta(self, run_id: str) -> FlowRunMeta:
        """Accès public aux métadonnées d'un run — utile à un exécuteur qui doit

        recharger le blueprint hors du cycle ``run``/``resume`` (l'exécuteur par
        dispatch, #311, en a besoin pour reprendre un run sans que l'hôte lui
        redonne le chemin du fichier)."""
        return self._load_meta(run_id)

    # ── Dérivation d'état depuis le kernel ──────────────────────────────────

    def _ctx_for(self, wfi: WorkflowInstance) -> ExecutionContext:
        return ExecutionContext(
            run_id=wfi.run_id,
            mission_id=wfi.mission_id,
            task_id=wfi.task_id,
            workflow_instance_id=wfi.id,
            actor_id=self._actor_id,
            host_id=self._host_id,
            risk_profile="standard",
        )

    def _context_pack(self, wfi: WorkflowInstance, blueprint_id: str) -> dict[str, Any]:
        """Le peu qu'un exécuteur non interactif doit savoir pour lier son travail au run.

        ``InteractiveNodeExecutor`` l'ignore entièrement (il n'imprime que le
        contrat) : ces clés n'existent que pour un exécuteur qui doit, comme
        celui par dispatch (#311), nommer une tâche de mission stable d'un
        appel à l'autre du même run sans que l'hôte le lui répète.
        """
        return {"run_id": wfi.id, "blueprint_id": blueprint_id, "mission_id": wfi.mission_id}

    def _current_node(self, wfi: WorkflowInstance, order: list[str]) -> str | None:
        """Le node courant, recalculé depuis le kernel — jamais un compteur à côté."""
        if wfi.status in _TERMINAL_STATUSES:
            return None
        if wfi.status is WorkflowStatus.BLOCKED:
            for event in reversed(self._kernel.get_run_events(wfi.id)):
                if event.event_type is RunEventType.STEP_FAILED:
                    return event.payload.get("step_id")
            return order[0] if order else None
        if wfi.status is WorkflowStatus.CHECKPOINTED:
            checkpoints = self._kernel.list_checkpoints(wfi.id)
            if checkpoints:
                pending = checkpoints[-1].state.pending_steps
                return pending[0] if pending else None
            return order[0] if order else None
        # RUNNING (ou CREATED, avant le tout premier advance_step)
        for event in reversed(self._kernel.get_run_events(wfi.id)):
            if event.event_type is RunEventType.STEP_STARTED:
                return event.payload.get("step_id")
        return order[0] if order else None

    def _last_refusal(self, wfi: WorkflowInstance) -> dict[str, Any] | None:
        for event in reversed(self._kernel.get_run_events(wfi.id)):
            if event.event_type is RunEventType.STEP_FAILED:
                return {
                    "node_id": event.payload.get("step_id"),
                    "reason": event.payload.get("reason"),
                    "at": event.created_at,
                }
        return None

    # ── Commandes ────────────────────────────────────────────────────────────

    def run(
        self,
        blueprint_path: Path,
        *,
        executor: NodeExecutor | None = None,
        mission_id: str = "",
        task_id: str = "",
    ) -> tuple[WorkflowInstance, NodeContract]:
        """Démarre un run : premier node ouvert, son contrat présenté."""
        blueprint = load_blueprint(blueprint_path)
        order = topo_order(blueprint)
        if not order:
            raise GrimoireRuntimeError(f"{blueprint_path} : aucun node à exécuter")
        contracts = build_node_contracts(blueprint)
        blueprint_id = blueprint["id"]

        ctx = ExecutionContext(
            run_id=f"RUN-{uuid.uuid4().hex[:12]}",
            mission_id=mission_id or f"MIS-flow-{blueprint_id}",
            task_id=task_id or f"FLOW-{blueprint_id}",
            workflow_instance_id="",
            actor_id=self._actor_id,
            host_id=self._host_id,
            risk_profile="standard",
        )
        wfi = self._kernel.create_instance(ctx, recipe_id=blueprint_id)
        ctx = replace(ctx, workflow_instance_id=wfi.id)
        wfi = self._kernel.start(wfi.id, ctx)
        wfi = self._kernel.advance_step(wfi.id, ctx, step_id=order[0])

        self._save_meta(
            FlowRunMeta(
                run_id=wfi.id,
                blueprint_id=blueprint_id,
                blueprint_path=str(blueprint_path),
                order=tuple(order),
                created_at=_now_iso(),
            )
        )

        contract = contracts[order[0]]
        (executor or InteractiveNodeExecutor()).execute(contract, context_pack=self._context_pack(wfi, blueprint_id))
        return wfi, contract

    def resume(self, run_id: str, *, output: dict[str, Any], executor: NodeExecutor | None = None) -> ResumeOutcome:
        """Vérifie la sortie du node courant, avance ou suspend le run."""
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        if wfi.status in _TERMINAL_STATUSES:
            raise GrimoireRuntimeError(f"run {run_id} est {wfi.status.value}, rien à reprendre")

        order = list(meta.order)
        contracts = build_node_contracts(load_blueprint(Path(meta.blueprint_path)))
        current_id = self._current_node(wfi, order)
        if current_id is None or current_id not in contracts:
            raise GrimoireRuntimeError(f"run {run_id} : aucun node courant résoluble (status={wfi.status.value})")

        ctx = self._ctx_for(wfi)
        if wfi.status is not WorkflowStatus.RUNNING:
            # CHECKPOINTED (crash avant le advance_step du node suivant) ou
            # BLOCKED (retentative après correction de l'hôte) : les deux
            # ramènent au même point, ouvrir l'exécution du node courant.
            wfi = self._kernel.advance_step(wfi.id, ctx, step_id=current_id)

        contract = contracts[current_id]
        faults = check_output_against_contract(contract, output)
        if faults:
            wfi = self._kernel.fail_step(wfi.id, ctx, step_id=current_id, reason="; ".join(faults))
            return ResumeOutcome(ok=False, finished=False, node_id=current_id, faults=tuple(faults), contract=contract)

        idx = order.index(current_id)
        completed = order[: idx + 1]
        pending = order[idx + 1 :]
        wfi, _chk = self._kernel.checkpoint(
            wfi.id,
            ctx,
            step_id=current_id,
            completed_steps=completed,
            pending_steps=pending,
        )
        if not pending:
            self._kernel.complete(wfi.id, ctx)
            return ResumeOutcome(ok=True, finished=True, node_id=current_id, contract=None)

        next_id = pending[0]
        next_contract = contracts[next_id]
        # Le node suivant n'est pas encore ouvert (pas de advance_step ici) :
        # un crash juste après cet appel laisse le kernel en CHECKPOINTED, et
        # le prochain resume() rouvrira ce même node suivant en premier —
        # jamais de node sauté, jamais de node rejoué deux fois.
        (executor or InteractiveNodeExecutor()).execute(
            next_contract, context_pack=self._context_pack(wfi, meta.blueprint_id)
        )
        return ResumeOutcome(ok=True, finished=False, node_id=next_id, contract=next_contract)

    def status(self, run_id: str, *, include_contract: bool = True) -> FlowStatusView:
        """Vue de lecture seule — ne mute jamais le kernel."""
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        order = list(meta.order)
        current_id = self._current_node(wfi, order)
        idx = order.index(current_id) if current_id in order else len(order)
        contract = None
        if include_contract and current_id is not None:
            contract = build_node_contracts(load_blueprint(Path(meta.blueprint_path))).get(current_id)
        return FlowStatusView(
            run_id=wfi.id,
            blueprint_id=meta.blueprint_id,
            status=wfi.status.value,
            current_node=current_id,
            completed_nodes=tuple(order[:idx]),
            pending_nodes=tuple(order[idx + 1 :]) if current_id else (),
            last_refusal=self._last_refusal(wfi),
            contract=contract,
        )

    def list_runs(self) -> list[FlowStatusView]:
        return [self.status(run_id, include_contract=False) for run_id in self.list_run_ids()]

    def abort(self, run_id: str, *, reason: str = "") -> WorkflowInstance:
        self._load_meta(run_id)  # 404 franc si le run n'existe pas côté flows
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        return self._kernel.abort(wfi.id, self._ctx_for(wfi), reason=reason)
