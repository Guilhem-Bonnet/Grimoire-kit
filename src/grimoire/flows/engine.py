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

import hashlib
import json
import logging
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.fsio import atomic_write, locked
from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, ready_nodes, topo_order
from grimoire.flows.directive import Directive, blueprint_digest, directive_id_for
from grimoire.flows.doctor import FlowDoctorReport, checkpoint_interrupted, diagnose
from grimoire.flows.executor import InteractiveNodeExecutor, NodeExecutor
from grimoire.flows.fingerprint import (
    DRIFT_KIND,
    contract_digest,
    drift_of,
    migration_refusal,
    relative_blueprint_path,
    resolve_blueprint_path,
)
from grimoire.flows.schemas import FlowRunMeta, FlowStatusView, NodeContract, ResumeOutcome
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import Checkpoint, ExecutionContext, RunEventType, WorkflowInstance, WorkflowStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grimoire.flows.attestation import CheckpointJournal
    from grimoire.flows.autonomy import GrantJournal

__all__ = [
    "FlowEngine",
    "check_output_against_contract",
    "checkpoint_decision_faults",
    "log_repairs",
    "rust_backend_available",
]

_LOG = logging.getLogger(__name__)


def log_repairs(run_id: str, repaired: tuple[str, ...]) -> None:
    """Journalise (WARNING) ce que ``resume`` a réparé — pour les pilotes qui ne rendent pas ``repaired``.

    ``resume`` complète l'écriture qu'un crash a interrompue même avec
    ``allow_replay=False``. Les pilotes automatiques qui n'ont pas de rapport où
    la loger (``genres``, ``upgrade-flow``) l'appellent pour qu'une réparation
    ne soit jamais silencieuse (W1-03b-S1).
    """
    for note in repaired:
        _LOG.warning("flow %s : réparé — %s", run_id, note)

def _refusal(message: str, run_id: str, repaired: tuple[str, ...]) -> GrimoireRuntimeError:
    """L'erreur de ``resume`` qui dit aussi ce qu'il a réparé avant de refuser (W1-03b-SK2-02).

    ``resume`` complète l'écriture interrompue AVANT de juger la sortie : si la
    sortie est ensuite refusée, aucun ``ResumeOutcome`` ne porte la réparation,
    alors qu'elle est écrite. Elle figure donc dans le message et dans le journal
    de logs ; sans réparation, le message est rendu tel quel.
    """
    if not repaired:
        return GrimoireRuntimeError(message)
    log_repairs(run_id, repaired)
    return GrimoireRuntimeError(f"{message} (réparé avant ce refus : {' ; '.join(repaired)})")


try:
    import grimoire_flows_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_flows_core`` module is importable.

    Purely informational (used by tests and diagnostics) — every call site
    below decides its own backend fresh via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend this module's Rust-optional functions should use.

    Own copy of the precedent set by ``grimoire.hosts.collect``/
    ``grimoire.hosts.surface`` (issue #354): two independent modules backed
    by one crate (here, :mod:`grimoire.runtime.kernel` has its own copy)
    each read their shared env var independently rather than cross-importing
    a private module-level variable. Reads ``GRIMOIRE_FLOWS_BACKEND`` fresh
    every call so tests can flip it with ``monkeypatch.setenv``.
    """
    override = os.environ.get("GRIMOIRE_FLOWS_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireRuntimeError(
                "GRIMOIRE_FLOWS_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_flows_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-flows-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireRuntimeError(f"GRIMOIRE_FLOWS_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


#: Miroir **corrige** de la table originale : ``REFUSED`` (terminal côté
#: ``RuntimeKernel`` — aucune transition sortante dans ``_WF_TRANSITIONS``,
#: écrit par ``mediate_tool`` quand les plafonds MAST (B11) sont atteints)
#: en était absent. Sans ce correctif, ``resume()`` sur un run REFUSED
#: levait bien une erreur, mais celle générique de
#: ``RuntimeKernel.advance_step`` plutôt que le message nommé ci-dessous, et
#: ``status()`` renvoyait un ``current_node`` périmé avec des
#: ``pending_nodes`` non vides — comme si un run mort continuait de
#: progresser. Trouvé en portant cette table vers ``grimoire_flows_core``
#: (issue #354) : son miroir Rust, ``is_flow_terminal``, est un ``match``
#: exhaustif sur les 9 variantes de ``WorkflowStatus`` — omettre une valeur
#: terminale n'y compile pas silencieusement, ce qu'une comparaison
#: Python contre un tuple permet sans broncher. Voir le docstring de
#: ``rust/grimoire-flows-core/src/lib.rs`` et
#: ``tests/unit/test_flows_rust_parity.py``.
_TERMINAL_STATUSES = (
    WorkflowStatus.COMPLETED,
    WorkflowStatus.VERIFIED,
    WorkflowStatus.ABORTED,
    WorkflowStatus.REFUSED,
)


def _is_flow_terminal(status: WorkflowStatus) -> bool:
    """Un run dans un de ces statuts n'a plus rien à reprendre.

    Delegates to ``grimoire_flows_core.is_flow_terminal_status`` (issue
    #354) when the Rust backend is active — see :func:`_use_rust_backend`.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return bool(_rust_core.is_flow_terminal_status(status.value))
    return status in _TERMINAL_STATUSES


def _flow_question_id(run_id: str, node_id: str) -> str:
    """L'identifiant stable de la question qu'un checkpoint pose à l'humain sur la tâche liée (K6)."""
    return f"q-flow-{run_id}-{node_id}"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def check_output_against_contract(contract: NodeContract, output: dict[str, Any]) -> list[str]:
    """Vérifie une sortie d'hôte contre les pins de sortie d'un node.

    Forme attendue : ``{"pins": {"<pin-id>": {"contract": "<nom>", ...}}}``.
    Chaque défaut nomme le node ET la pin — jamais un échec muet — pour que
    le flow suspendu dise exactement quoi corriger.

    Delegates to ``grimoire_flows_core.check_output_against_contract`` (issue
    #354) when the Rust backend is active — see :func:`_use_rust_backend`.
    The pure-Python path below is the reference implementation and is what
    runs when the compiled module is absent.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        outputs = [(pin.pin_id, pin.contract) for pin in contract.outputs]
        return list(_rust_core.check_output_against_contract(contract.node_id, outputs, output))
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


#: Préfixe de la référence qui range, dans ``Checkpoint.evidence_refs``,
#: l'empreinte de la sortie acceptée pour un node (W1-03).
_OUTPUT_DIGEST_PREFIX = "output-sha256:"


def _output_digest(output: Any) -> str | None:
    """Empreinte sha256 canonique d'une sortie d'hôte, ou ``None`` si elle n'est pas sérialisable.

    Forme canonique : JSON à clés triées, sans espace, **ASCII pur**
    (``ensure_ascii=True``). Deux sorties égales donnent la même empreinte quel
    que soit l'ordre des clés, et une chaîne portant un substitut isolé ne
    peut pas faire échouer l'encodage UTF-8 strict (frontière PyO3 comprise).
    Pure Python sur les deux backends : la décision de rejeu ne dépend jamais
    de ``grimoire_flows_core``.
    """
    try:
        canonical = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _checkpoint_digest(chk: Checkpoint) -> str | None:
    """L'empreinte de sortie rangée dans ce checkpoint (W1-03), ou ``None`` s'il n'en porte pas."""
    for ref in chk.evidence_refs:
        if ref.startswith(_OUTPUT_DIGEST_PREFIX):
            return ref[len(_OUTPUT_DIGEST_PREFIX) :]
    return None


_CHECKPOINT_DECISIONS = ("approve", "reject", "amend", "accept_as_is")


def checkpoint_decision_faults(output: dict[str, Any]) -> list[str]:
    """Valide la décision d'un node ``kind: "checkpoint"`` (issue #207) — Python-only, jamais côté Rust.

    En plus de ses pins (vérifiées par :func:`check_output_against_contract`,
    inchangée), la sortie d'un checkpoint doit porter ``checkpoint_decision``
    parmi ``"approve"``/``"reject"``/``"amend"`` — absente ou invalide, un
    refus nommé, jamais une décision devinée. ``"reject"`` est lui-même un
    défaut (le motif, ``checkpoint_reason``, finit dans l'événement
    ``STEP_FAILED`` du kernel via le même mécanisme que tout autre défaut —
    « le motif dans le graphe de décision » que l'issue demande) : la reprise
    suivante rouvre exactement ce même node, jamais le suivant.

    Ne juge que la *valeur*. Qui a décidé — ``attested`` ou ``unattested`` —
    est jugé par :func:`grimoire.flows.attestation.judge_checkpoint` (W2-01),
    qui persiste aussi la décision et son motif (``amend`` compris) dans le
    journal d'autorité du run, relu par :meth:`FlowEngine.status`.
    """
    decision = output.get("checkpoint_decision")
    if decision not in _CHECKPOINT_DECISIONS:
        return [
            f"checkpoint : 'checkpoint_decision' doit être l'un de {_CHECKPOINT_DECISIONS} (reçu {decision!r})"
        ]
    if decision == "reject":
        reason = output.get("checkpoint_reason") or "(aucun motif fourni)"
        return [f"checkpoint rejeté : {reason}"]
    # ``amend`` et ``accept_as_is`` (W2-10) sont jugés par ``judge_checkpoint`` :
    # ils dépendent du journal du run (compteur d'amendements), pas de la sortie.
    return []


class FlowEngine:
    """Coordonne blueprint, kernel et exécuteur pour un ou plusieurs runs."""

    def __init__(
        self,
        *,
        kernel_root: Path,
        flows_root: Path,
        actor_id: str = "cli",
        host_id: str = "local",
        project_root: Path = Path(),
    ) -> None:
        #: Conservé à côté de ``self._kernel`` (qui n'expose pas sa racine) —
        #: uniquement pour :meth:`child_engine` (issue #206), qui doit pouvoir
        #: reconstruire un engine partageant le même kernel/répertoire de
        #: flows qu'un node ``kind: "composite"`` lance un sous-run.
        self._kernel_root = kernel_root
        self._kernel = RuntimeKernel(kernel_root)
        self._flows_root = flows_root
        self._flows_root.mkdir(parents=True, exist_ok=True)
        self._actor_id = actor_id
        self._host_id = host_id
        #: Racine du projet exécuté (issue #205) — nécessaire pour résoudre une
        #: acceptance ``run_need`` au chargement du blueprint. Défaut au
        #: répertoire courant : sans effet sur un blueprint qui n'en déclare
        #: aucune, ce qui couvre tout le corpus antérieur à cette issue.
        self._project_root = project_root

    def child_engine(self) -> FlowEngine:
        """Un nouvel engine, même kernel/flows_root/projet (issue #206).

        Un node ``kind: "composite"`` (voir ``flows.dispatch_executor``)
        lance son sous-flow comme un run à part entière — un ``run_id``
        distinct, ses propres checkpoints — mais visible par le même
        ``RuntimeKernel`` et le même registre de métadonnées de run que le
        parent : ``grimoire flow run`` (sans argument) et ``grimoire flow
        status`` sur l'id de l'enfant fonctionnent sans configuration
        supplémentaire, exactement comme sur un run racine.
        """
        return FlowEngine(
            kernel_root=self._kernel_root,
            flows_root=self._flows_root,
            actor_id=self._actor_id,
            host_id=self._host_id,
            project_root=self._project_root,
        )

    # ── Persistance des métadonnées de run ──────────────────────────────────

    def _meta_path(self, run_id: str) -> Path:
        return self._flows_root / f"{run_id}.json"

    def _save_meta(self, meta: FlowRunMeta) -> None:
        path = self._meta_path(meta.run_id)
        with locked(path):
            atomic_write(path, json.dumps(meta.to_dict(), indent=2, ensure_ascii=False) + "\n")

    def _load_meta(self, run_id: str) -> FlowRunMeta:
        path = self._meta_path(run_id)
        if not path.is_file():
            raise GrimoireRuntimeError(f"run introuvable : {run_id}")
        return FlowRunMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_run_ids(self) -> list[str]:
        return sorted(p.stem for p in self._flows_root.glob("*.json"))

    def knows_run(self, run_id: str) -> bool:
        """Le kernel a-t-il encore l'instance de ce run ? Une métadonnée orpheline (kernel nettoyé) répond non."""
        return self._kernel.get_instance(run_id) is not None

    def _blueprint_file(self, meta: FlowRunMeta) -> Path:
        """Le fichier blueprint du run : le chemin stocké est relatif à la racine du projet (W2-04)."""
        return resolve_blueprint_path(meta.blueprint_path, self._project_root)

    def blueprint_file(self, run_id: str) -> Path:
        """Le fichier blueprint (résolu) d'un run — pour les appelants qui rechargent le blueprint."""
        return self._blueprint_file(self._load_meta(run_id))

    def _drift_state(self, meta: FlowRunMeta, wfi: WorkflowInstance) -> tuple[dict[str, Any] | None, list[str]]:
        """``(constat de dérive ou None, nodes franchis)``. Rien à comparer : ``(None, [])``.

        Un run terminé, ou lancé avant W2-04 (pas d'empreinte), n'est pas jugé :
        ``FlowStatusView.blueprint_sha256`` ``None`` le dit.
        """
        if meta.blueprint_sha256 is None or _is_flow_terminal(wfi.status):
            return None, []
        order = list(meta.order)
        current = self._current_node(wfi, order)
        crossed = order if current is None else (order[: order.index(current)] if current in order else [])
        blueprint = load_blueprint(self._blueprint_file(meta), self._project_root)
        contracts = build_node_contracts(blueprint, self._project_root)
        drift = drift_of(
            stored_order=order,
            stored_digests=meta.node_digests,
            crossed=crossed,
            new_order=topo_order(blueprint),
            new_contracts={n: c.to_dict() for n, c in contracts.items()},
        )
        return drift, crossed

    def blueprint_drift(self, run_id: str) -> dict[str, Any] | None:
        """Le constat ``blueprint_drift`` du run, ou ``None`` (sain, terminé ou non vérifié). Lecture seule."""
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        return self._drift_state(meta, wfi)[0]

    def kernel_export(self, run_id: str) -> dict[str, Any]:
        """Instance + événements + checkpoints du run (voir ``flows.transfer``)."""
        return self._kernel.export_instance(run_id)

    def kernel_import(self, data: dict[str, Any]) -> None:
        self._kernel.import_instance(data)

    def has_instance(self, run_id: str) -> bool:
        return self._kernel.get_instance(run_id) is not None

    def save_imported_meta(self, meta: FlowRunMeta) -> None:
        """Écrit la méta d'un run importé — ``flows.transfer`` seul."""
        self._save_meta(meta)

    def migrate(self, run_id: str) -> tuple[FlowRunMeta, FlowRunMeta]:
        """Adopte le blueprint actuel (ordre, contrats, empreintes) si les nodes franchis en forment un préfixe identique.

        Refuse, sans rien écrire, sinon. Seul chemin qui réécrit ``order`` et les
        empreintes d'un run. Un run antérieur à W2-04 (sans empreinte) y gagne la sienne.
        Rend ``(ancienne méta, nouvelle méta)``.
        """
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        if _is_flow_terminal(wfi.status):
            raise GrimoireRuntimeError(f"run {run_id} est {wfi.status.value} : rien à migrer")
        order = list(meta.order)
        current = self._current_node(wfi, order)
        crossed = order if current is None else (order[: order.index(current)] if current in order else [])
        blueprint = load_blueprint(self._blueprint_file(meta), self._project_root)
        contracts = build_node_contracts(blueprint, self._project_root)
        new_order = topo_order(blueprint)
        why = migration_refusal(crossed=crossed, current=current, new_order=new_order, new_contracts=contracts)
        if why is not None:
            raise GrimoireRuntimeError(f"run {run_id} : migration refusée — {why} ; rien n'a été écrit.")
        new_meta = replace(
            meta,
            order=tuple(new_order),
            blueprint_sha256=self._digest_of(contracts, new_order),
            node_digests={n: contract_digest(contracts[n].to_dict()) for n in new_order if n in contracts},
        )
        self._save_meta(new_meta)
        return meta, new_meta

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
        """Le node courant, recalculé depuis le kernel — jamais un compteur à côté.

        La lecture des journaux d'événements/checkpoints (E/S) reste toujours
        ici, sous les deux backends — seule la *décision* qui en dérive (quel
        node est "courant" pour ce statut) délègue à
        ``grimoire_flows_core.current_node`` (issue #354) quand le backend
        Rust est actif ; voir :func:`_use_rust_backend`.
        """
        if _is_flow_terminal(wfi.status):
            return None

        last_step_failed: str | None = None
        last_step_started: str | None = None
        has_checkpoint = False
        checkpoint_pending_head: str | None = None

        if wfi.status is WorkflowStatus.BLOCKED:
            # W1-03b : le statut BLOCKED est écrit AVANT l'événement STEP_FAILED.
            # Un STEP_STARTED plus récent que le dernier STEP_FAILED (ou sans
            # aucun STEP_FAILED) signe un crash entre les deux : le node bloqué
            # est alors celui qu'on venait d'ouvrir, jamais le premier du run.
            for event in reversed(self._kernel.get_run_events(wfi.id)):
                if event.event_type in (RunEventType.STEP_FAILED, RunEventType.STEP_STARTED):
                    last_step_failed = event.payload.get("step_id")
                    break
        elif wfi.status is WorkflowStatus.CHECKPOINTED:
            checkpoints = self._kernel.list_checkpoints(wfi.id)
            if checkpoints:
                has_checkpoint = True
                pending = checkpoints[-1].state.pending_steps
                checkpoint_pending_head = pending[0] if pending else None
        else:
            # RUNNING (ou CREATED, avant le tout premier advance_step ; ou PAUSED,
            # posé par le kernel à la frontière d'un node, K11 — voir le docstring
            # de rust/grimoire-flows-core/src/lib.rs).
            for event in reversed(self._kernel.get_run_events(wfi.id)):
                if event.event_type is RunEventType.STEP_STARTED:
                    last_step_started = event.payload.get("step_id")
                    break
            if wfi.status in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED) and last_step_started is not None:
                # W1-03b : le statut RUNNING est écrit AVANT STEP_STARTED (et
                # avant le statut CHECKPOINTED d'un checkpoint déjà écrit). Si le
                # dernier node ouvert est déjà dans le dernier checkpoint, il est
                # franchi : le courant est le suivant, pas lui — sinon la reprise
                # le rejouerait.
                checkpoints = self._kernel.list_checkpoints(wfi.id)
                if checkpoints and last_step_started in checkpoints[-1].state.completed_steps:
                    pending = checkpoints[-1].state.pending_steps
                    if not pending:
                        return None  # tout est checkpointé : il ne reste qu'à terminer
                    last_step_started = pending[0]

        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return _rust_core.current_node(  # type: ignore[no-any-return]
                wfi.status.value, order, last_step_failed, has_checkpoint, checkpoint_pending_head, last_step_started
            )

        if wfi.status is WorkflowStatus.BLOCKED:
            return last_step_failed if last_step_failed is not None else (order[0] if order else None)
        if wfi.status is WorkflowStatus.CHECKPOINTED:
            if has_checkpoint:
                return checkpoint_pending_head
            return order[0] if order else None
        return last_step_started if last_step_started is not None else (order[0] if order else None)

    def _last_checkpoint_completed_steps(self, wfi: WorkflowInstance) -> tuple[str, ...]:
        """``completed_steps`` du dernier checkpoint connu, ou ``()`` si aucun n'existe.

        Seule source de vérité pour ``completed_nodes`` sur un run terminé
        hors succès (ABORTED/REFUSED) dans :meth:`status` (issue #414) : un
        run mort avant son premier ``resume()`` n'a jamais checkpointé quoi
        que ce soit, donc rien n'est réellement fait.
        """
        checkpoints = self._kernel.list_checkpoints(wfi.id)
        if not checkpoints:
            return ()
        return tuple(checkpoints[-1].state.completed_steps)

    def _last_refusal(self, wfi: WorkflowInstance) -> dict[str, Any] | None:
        for event in reversed(self._kernel.get_run_events(wfi.id)):
            if event.event_type is RunEventType.STEP_FAILED:
                return {
                    "node_id": event.payload.get("step_id"),
                    "reason": event.payload.get("reason"),
                    "at": event.created_at,
                }
        return None

    def _reconcile(
        self, wfi: WorkflowInstance, ctx: ExecutionContext
    ) -> tuple[WorkflowInstance, Checkpoint | None]:
        """Complète le checkpoint qu'un crash a laissé à moitié écrit (W1-03b).

        Un checkpoint s'écrit en cinq temps sans transaction commune (fichier
        de checkpoints, statut, références, ``CHECKPOINT_SAVED``,
        ``STEP_COMPLETED``). Le fichier de checkpoints est écrit en premier : sa
        dernière ligne fait autorité sur « ce node est franchi ». Deux états
        signent une écriture interrompue :

        - RUNNING alors que le dernier node ouvert est déjà le dernier
          checkpoint (statut CHECKPOINTED jamais écrit, ou RUNNING réécrit par
          ``advance_step``/``complete`` avant l'événement qui le suit) ;
        - CHECKPOINTED alors que les références de l'instance ou l'un des deux
          événements manquent.

        La réparation rejoue ``checkpoint`` avec les données DU checkpoint déjà
        écrit — idempotent côté kernel, aucune décision nouvelle. Rend
        l'instance relue et le checkpoint complété, ou ``(wfi, None)`` si rien
        n'était interrompu.
        """
        if wfi.status not in (WorkflowStatus.RUNNING, WorkflowStatus.CHECKPOINTED):
            return wfi, None
        checkpoints = self._kernel.list_checkpoints(wfi.id)
        if not checkpoints:
            return wfi, None
        last = checkpoints[-1]
        interrupted = checkpoint_interrupted(wfi, checkpoints, self._kernel.get_run_events(wfi.id))
        if not interrupted:
            return wfi, None
        wfi, healed = self._kernel.checkpoint(
            wfi.id,
            ctx,
            step_id=last.step_id,
            completed_steps=list(last.state.completed_steps),
            pending_steps=list(last.state.pending_steps),
            evidence_refs=list(last.evidence_refs),
        )
        return wfi, healed

    # ── Autorité humaine sur les checkpoints (W2-01) ────────────────────────

    def _record_accepted_risk(
        self, wfi: WorkflowInstance, run_id: str, node_id: str, risk: Mapping[str, Any]
    ) -> None:
        """Pose ``task.risk_accepted`` sur la tâche du ledger liée au run, si elle existe (W2-10).

        Ne crée ni ledger ni tâche : un run sans tâche de ledger (``FLOW-<id>``
        par défaut) garde le risque dans le journal du run, relu par
        ``flow status``. Le ledger le rejoue dans ``MissionTask.accepted_risks`` et
        ``classify`` remonte la tâche au moins en V1 pour toute surface.
        """
        from grimoire.missions.ledger import RISK_ACCEPTED_EVENT, MissionLedger
        from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

        root = Path(self._project_root) / DEFAULT_LEDGER_RELPATH
        if not (root / "events.jsonl").is_file():
            return
        ledger = MissionLedger(root)
        if ledger.get_task(wfi.task_id) is None:
            return
        ledger.append_event(
            RISK_ACCEPTED_EVENT,
            wfi.task_id,
            "task",
            self._actor_id,
            {"run_id": run_id, "node_id": node_id, **risk},
        )

    def _linked_service(self, wfi: WorkflowInstance) -> Any:
        """Le ``TaskService`` du projet si le run a une tâche de ledger liée (K6), sinon ``None``.

        Même règle que :meth:`_record_accepted_risk` : ni ledger ni tâche n'est créé pour un run
        qui n'en a pas ; il garde sa question dans le journal du run (``flow status``).
        """
        from grimoire.missions.service import DEFAULT_LEDGER_RELPATH, TaskService

        root = Path(self._project_root) / DEFAULT_LEDGER_RELPATH
        if not (root / "events.jsonl").is_file():
            return None
        service = TaskService(Path(self._project_root))
        return service if service.ledger.get_task(wfi.task_id) is not None else None

    def _ask_linked_task(self, wfi: WorkflowInstance, run_id: str, node_id: str, why: str) -> None:
        """Un checkpoint destructif sans décision attestée (W2-01) devient une question tracée sur la tâche liée (K6).

        La tâche tenue se gare en ``waiting(human)`` ; une tâche qui n'est pas tenue reçoit
        seulement l'événement ``task.question_asked``. L'identifiant de la question est celui du
        checkpoint : un refus répété ne la repose pas. Meilleur effort : le flux n'en dépend pas.
        """
        import contextlib

        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.schemas import TaskState
        from grimoire.missions.waiting import QUESTION_ASKED_EVENT, Question

        service = self._linked_service(wfi)
        if service is None:
            return
        qid = _flow_question_id(run_id, node_id)
        text = (
            f"Checkpoint destructif « {node_id} » du run {run_id} : la décision attend un humain "
            f"(tour humain de la session qui l'a présenté, ou jeton cockpit) — {why}"
        )
        with contextlib.suppress(GrimoireError, OSError, ValueError):
            task = service.require(wfi.task_id)
            if task.status in (TaskState.CLAIMED, TaskState.RUNNING):
                service.ask(
                    wfi.task_id, text, self._actor_id, kind="permission",
                    options=("approve", "reject", "amend"), origin="flow", question_id=qid,
                )
            elif not any(
                e.event_type == QUESTION_ASKED_EVENT and e.payload.get("question", {}).get("id") == qid
                for e in service.ledger.list_events(wfi.task_id)
            ):
                question = Question(qid, "permission", text, ("approve", "reject", "amend"), "flow")
                service.ledger.append_event(
                    QUESTION_ASKED_EVENT, wfi.task_id, "task", self._actor_id,
                    {"task_id": wfi.task_id, "reason": "human", "question": question.to_dict(),
                     "actor_id": self._actor_id},
                )

    def _settle_linked_question(self, wfi: WorkflowInstance, run_id: str, node_id: str, decision: str) -> None:
        """La décision d'un checkpoint est attestée humaine : elle répond à la question de la tâche liée (K6).

        L'attestation est la preuve d'autorité (un tour humain, W2-01) : c'est elle, et non le
        champ ``actor``, qui fait de ceci une réponse du canal humain.
        """
        import contextlib

        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState

        service = self._linked_service(wfi)
        if service is None:
            return
        qid = _flow_question_id(run_id, node_id)
        with contextlib.suppress(GrimoireError, OSError, ValueError):
            task = service.require(wfi.task_id)
            waiting = task.waiting
            if task.status is TaskState.WAITING and waiting is not None and waiting.question is not None \
                    and waiting.question.id == qid:
                service.answer(
                    wfi.task_id, f"décision de checkpoint attestée : {decision}", self._actor_id,
                    origin=ORIGIN_HUMAN, question_id=qid, attested_by_flow=True,
                )

    def _release_linked_question(self, run_id: str, order: list[str], wfi: WorkflowInstance) -> None:
        """Un run abandonné ne tranchera plus sa question de checkpoint : la tâche liée qu'elle garait se réveille (K6-S2-01).

        Meilleur effort, comme :meth:`_ask_linked_task` : l'abandon du run ne dépend pas du ledger.
        """
        import contextlib

        from grimoire.core.exceptions import GrimoireError

        service = self._linked_service(wfi)
        if service is None:
            return
        with contextlib.suppress(GrimoireError, OSError, ValueError):
            service.release_flow_question(
                wfi.task_id, {_flow_question_id(run_id, node) for node in order}, self._actor_id, cause="flow_aborted"
            )

    def _authority(self, run_id: str) -> CheckpointJournal:
        from grimoire.flows.attestation import CheckpointJournal

        return CheckpointJournal(self._flows_root, run_id)

    def _projected_plan(self, blueprint: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
        """Le plan projeté maintenant pour *node_id* (W2-11), ``None`` si le node n'en déclare pas."""
        from grimoire.flows.attestation import plan_providers, project_plan

        provider = plan_providers(blueprint).get(node_id)
        return None if provider is None else project_plan(provider, self._project_root)

    def _grants(self) -> GrantJournal:
        from grimoire.flows.autonomy import GrantJournal

        return GrantJournal(self._flows_root, Path(self._project_root))

    def _grant_lookup(self, run_id: str, node_id: str) -> tuple[str, str] | None:
        """L'octroi d'autonomie qui couvre ce checkpoint (W5-06) — ``(grant_id, texte de révocation)``."""
        from grimoire.flows.autonomy import refusal_note

        state = self._grants().for_checkpoint(run_id, node_id)
        if state is None:
            return None
        return state.grant_id, "" if state.active else refusal_note(state)

    def _hold_in_force(self, wfi: WorkflowInstance) -> str:
        """Le motif d'un ``hold`` qui couvre ce run (K11), ``""`` sinon.

        Seul ``hold`` pose un run à la frontière de node : ``drain`` laisse les agents en cours finir leur tâche,
        et un drapeau illisible vaut ``drain``. Les drapeaux sont relus à chaque frontière (deux lectures de fichier).
        """
        from grimoire.missions.pause import HOLD, home_root, read_pause

        state = read_pause(home_root(Path(self._project_root)))
        if state.mode_for(wfi.task_id) != HOLD:
            return ""
        return "pause hold en vigueur (" + "; ".join(f.label for f in state.applying(wfi.task_id)) + ")"

    def _present(self, run_id: str, contract: NodeContract | None, blueprint: Mapping[str, Any]) -> None:
        """Un checkpoint présenté émet son challenge, lié à la session courante (rien pour un autre kind).

        Le challenge porte le plan projeté s'il est déclaré (W2-11) : c'est
        contre lui que la décision sera comparée.
        """
        if contract is not None and contract.kind == "checkpoint":
            self._authority(run_id).issue_challenge(
                contract.node_id, plan=self._projected_plan(blueprint, contract.node_id)
            )

    # ── Commandes ────────────────────────────────────────────────────────────

    def run(
        self,
        blueprint_path: Path,
        *,
        executor: NodeExecutor | None = None,
        mission_id: str = "",
        task_id: str = "",
        parent_run_id: str | None = None,
        parent_node_id: str | None = None,
        preview: bool = False,
    ) -> tuple[WorkflowInstance, NodeContract]:
        """Démarre un run : premier node ouvert, son contrat présenté.

        ``parent_run_id``/``parent_node_id`` (issue #206) : posés par
        ``flows.dispatch_executor`` quand ce run est le sous-flow d'un node
        ``kind: "composite"`` — ``None``/``None`` pour tout run racine, les
        deux sont toujours posés ou absents ensemble.

        ``preview`` marque un aperçu (``upgrade-flow run --dry-run``) dans la
        métadonnée du run : :func:`grimoire.tools.flow_runs.list_flow_runs` ne
        le compte pas parmi les runs de flow.
        """
        blueprint = load_blueprint(blueprint_path, self._project_root)
        order = topo_order(blueprint)
        if not order:
            raise GrimoireRuntimeError(f"{blueprint_path} : aucun node à exécuter")
        contracts = build_node_contracts(blueprint, self._project_root)
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
        wfi = self._kernel.create_instance(ctx, recipe_id=blueprint_id, inputs_ref=relative_blueprint_path(blueprint_path, self._project_root))
        ctx = replace(ctx, workflow_instance_id=wfi.id)
        wfi = self._kernel.start(wfi.id, ctx)
        wfi = self._kernel.advance_step(wfi.id, ctx, step_id=order[0])

        self._save_meta(
            FlowRunMeta(
                run_id=wfi.id,
                blueprint_id=blueprint_id,
                blueprint_path=relative_blueprint_path(blueprint_path, self._project_root),
                order=tuple(order),
                created_at=_now_iso(),
                parent_run_id=parent_run_id,
                parent_node_id=parent_node_id,
                blueprint_sha256=self._digest_of(contracts, order),
                node_digests={n: contract_digest(contracts[n].to_dict()) for n in order if n in contracts},
                preview=preview,
            )
        )

        contract = contracts[order[0]]
        self._present(wfi.id, contract, blueprint)
        (executor or InteractiveNodeExecutor()).execute(contract, context_pack=self._context_pack(wfi, blueprint_id))
        return wfi, contract

    def _crossed_node_for_digest(self, wfi: WorkflowInstance, digest: str) -> str | None:
        """Le node déjà franchi dont la sortie acceptée a cette empreinte, sinon ``None``.

        Lue dans les checkpoints du run (écrits avant tout changement de
        statut) : un crash après le checkpoint ne perd donc pas l'empreinte.
        """
        ref = _OUTPUT_DIGEST_PREFIX + digest
        for chk in self._kernel.list_checkpoints(wfi.id):
            if ref in chk.evidence_refs:
                return chk.step_id
        return None

    def _attempt(self, run_id: str, node_id: str) -> int:
        """Numéro de tentative du node : refus déjà journalisés pour lui + 1."""
        failed = sum(
            1
            for e in self._kernel.get_run_events(run_id)
            if e.event_type is RunEventType.STEP_FAILED and e.payload.get("step_id") == node_id
        )
        return failed + 1

    def _digest_of(self, contracts: dict[str, NodeContract], order: list[str]) -> str:
        return blueprint_digest(contracts[n].to_dict() for n in order if n in contracts)

    def directive(self, run_id: str) -> Directive:
        """La directive courante du run — lecture seule, stable entre deux appels (W2-03)."""
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        if wfi.status in (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED):
            return Directive(kind="done", run_id=run_id, node_id=None, directive_id=None)
        if _is_flow_terminal(wfi.status):
            return Directive(
                kind="blocked", run_id=run_id, node_id=None, directive_id=None, reason=wfi.status.value
            )
        order = list(meta.order)
        node_id = self._current_node(wfi, order)
        if node_id is None:
            if not order:
                return Directive(kind="done", run_id=run_id, node_id=None, directive_id=None)
            # Tous les nodes sont checkpointés mais le statut n'est pas COMPLETED
            # (crash entre le dernier checkpoint et ``complete``) : le run n'est
            # PAS terminé, donc jamais ``done`` — l'hôte qui s'arrêterait là le
            # laisserait en l'état. La directive reste celle du dernier node ;
            # renvoyer sa sortie acceptée, avec ce directive_id, termine le run.
            node_id = order[-1]
        if self._drift_state(meta, wfi)[0] is not None:
            # Pas de directive_id : aucune réponse n'est attendue tant que le blueprint a dérivé.
            return Directive("blocked", run_id, node_id, None, self._attempt(run_id, node_id), DRIFT_KIND)
        blueprint = load_blueprint(self._blueprint_file(meta), self._project_root)
        contracts = build_node_contracts(blueprint, self._project_root)
        attempt = self._attempt(run_id, node_id)
        did = directive_id_for(run_id, node_id, attempt, self._digest_of(contracts, order))
        if wfi.status is WorkflowStatus.BLOCKED:
            # Un checkpoint destructif sans décision attestée (W2-01) est un
            # ``blocked`` comme un autre (quatre kinds seulement) : seule la
            # raison dit que l'issue est humaine, pas une correction de sortie.
            refusal = self._last_refusal(wfi)
            awaiting = (
                refusal is not None
                and refusal["node_id"] == node_id
                and str(refusal["reason"] or "").startswith("awaiting_human")
            )
            return Directive("blocked", run_id, node_id, did, attempt, "awaiting_human" if awaiting else "contract_violation")
        if wfi.status is WorkflowStatus.PAUSED and self._hold_in_force(wfi):
            # Pas de directive_id : aucune réponse n'est attendue tant que la pause dure.
            return Directive("blocked", run_id, node_id, None, attempt, "paused")
        contract = contracts.get(node_id)
        kind = "checkpoint" if contract is not None and contract.kind == "checkpoint" else "execute"
        if wfi.status is WorkflowStatus.PAUSED and kind == "checkpoint":
            # K11 : la frontière a posé le run sans présenter ce checkpoint (donc sans challenge) ; la session qui
            # reprend le reçoit ici, avant le tour humain qui doit y répondre. Idempotent pour une même session.
            self._present(run_id, contract, blueprint)
        return Directive(kind, run_id, node_id, did, attempt)

    def _directive_refusal(self, run_id: str, supplied: str, output: dict[str, Any], allow_replay: bool) -> None:
        """Lève une faute nommée, SANS écrire, si ``supplied`` n'est pas la directive courante.

        Exception : le rejeu (W1-03) d'une sortie déjà acceptée pour un node
        franchi, tant que ``supplied`` est bien la directive de CE node — un hôte
        qui n'a pas reçu la réponse renvoie sa requête telle quelle.
        """
        current = self.directive(run_id)
        if current.directive_id == supplied:
            return
        if allow_replay:
            # Y compris quand la directive courante est ``done`` : le rejeu du
            # dernier node d'un run terminé est admis par ``_resume_core``.
            wfi = self._kernel.get_instance(run_id)
            digest = _output_digest(output)
            crossed = self._crossed_node_for_digest(wfi, digest) if wfi is not None and digest is not None else None
            if crossed is not None:
                meta = self._load_meta(run_id)
                contracts = build_node_contracts(
                    load_blueprint(self._blueprint_file(meta), self._project_root), self._project_root
                )
                bp = self._digest_of(contracts, list(meta.order))
                if any(
                    directive_id_for(run_id, crossed, n, bp) == supplied
                    for n in range(1, self._attempt(run_id, crossed) + 1)
                ):
                    return
        raise GrimoireRuntimeError(
            f"run {run_id} : directive_id {supplied[:12]}… périmé ou étranger — la directive courante vise "
            f"{'le node « ' + current.node_id + ' »' if current.node_id else 'aucun node'} "
            f"({current.kind}, directive_id={current.directive_id}) ; rien n'a été écrit. "
            "Relire `grimoire flow status` avant de répondre."
        )

    def resume(
        self,
        run_id: str,
        *,
        output: dict[str, Any],
        executor: NodeExecutor | None = None,
        allow_replay: bool = True,
        directive_id: str | None = None,
    ) -> ResumeOutcome:
        """``_resume_core`` + la directive qui suit (W2-03).

        ``directive_id`` (optionnel, rétrocompatible) : la directive à laquelle
        l'hôte répond. Une autre que la courante est refusée avant toute écriture.
        Le résultat porte toujours la directive suivante (``directive_id``).
        """
        drift = self.blueprint_drift(run_id)
        if drift is not None:
            raise GrimoireRuntimeError(
                f"run {run_id} : {DRIFT_KIND} — le blueprint ne colle plus au run "
                f"(modifiés : {drift['changed_nodes'] or 'aucun'} ; retirés : {drift['removed_nodes'] or 'aucun'} ; "
                f"ajoutés, non adoptés : {drift['added_nodes'] or 'aucun'} ; "
                f"ordre changé : {'oui' if drift['reordered'] else 'non'}) ; diff d'ordre : "
                f"{' '.join(drift['order_diff']) or '(identique)'} ; rien n'a été écrit. "
                "Rétablir le blueprint, ou `grimoire flow migrate` si les nodes franchis forment un préfixe identique "
                "et que le node courant les suit."
            )
        if directive_id is not None:
            self._directive_refusal(run_id, directive_id, output, allow_replay)
        outcome = self._resume_core(run_id, output=output, executor=executor, allow_replay=allow_replay)
        return replace(outcome, directive=self.directive(run_id))

    def _resume_core(
        self,
        run_id: str,
        *,
        output: dict[str, Any],
        executor: NodeExecutor | None = None,
        allow_replay: bool = True,
    ) -> ResumeOutcome:
        """Vérifie la sortie du node courant, avance ou suspend le run.

        Idempotent (W1-03) : une sortie qui viole le contrat du node courant
        mais qui est **identique** (empreinte sha256 canonique) à celle déjà
        acceptée pour un node franchi est un rejeu — un hôte qui n'a pas reçu
        la réponse et renvoie la même requête. Le résultat est ``ok=True,
        replay=True`` et **rien n'est écrit** : ni ``STEP_STARTED``, ni
        ``STEP_FAILED``, ni checkpoint. Une sortie valide pour le node courant
        avance toujours normalement, même identique à une sortie antérieure
        (deux nodes sans pin de sortie acceptent la même sortie vide) ; une
        sortie invalide et inconnue reste refusée.

        ``allow_replay=False`` rend la sémantique antérieure (refus) aux
        pilotes automatiques qui produisent eux-mêmes la sortie du node
        courant : pour eux un rejeu ne ferait pas avancer le run.
        """
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        if _is_flow_terminal(wfi.status):
            if allow_replay and wfi.status in (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED):
                # Le dernier node a été accepté puis la réponse s'est perdue :
                # le même renvoi sur un run terminé réussi est un rejeu.
                digest = _output_digest(output)
                crossed = self._crossed_node_for_digest(wfi, digest) if digest is not None else None
                if crossed is not None:
                    repaired: tuple[str, ...] = ()
                    if not any(
                        e.event_type is RunEventType.WORKFLOW_COMPLETED for e in self._kernel.get_run_events(wfi.id)
                    ):
                        # Le statut COMPLETED est écrit avant son événement : un
                        # crash entre les deux laisse le journal sans clôture.
                        self._kernel.complete(wfi.id, self._ctx_for(wfi))
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

        order = list(meta.order)
        blueprint = load_blueprint(self._blueprint_file(meta), self._project_root)
        contracts = build_node_contracts(blueprint, self._project_root)
        missing = [n for n in order if n not in contracts]
        if missing:
            # W2-04-S4 : le run avance sur l'ordre figé ; un node que le blueprint n'a plus
            # ne s'ouvre pas (``contracts[next_id]`` levait un KeyError après le checkpoint).
            raise GrimoireRuntimeError(
                f"run {run_id} : les nodes {missing} n'existent plus dans le blueprint "
                f"{self._blueprint_file(meta)} ; rien n'a été écrit. "
                "Rétablir le blueprint, ou `grimoire flow migrate` si le node courant suit les nodes franchis."
            )
        ctx = self._ctx_for(wfi)
        digest = _output_digest(output)
        # W1-03b : avant toute décision, complète le checkpoint qu'un crash a
        # laissé à moitié écrit — la suite lit alors un état cohérent.
        wfi, healed = self._reconcile(wfi, ctx)
        repaired_notes: tuple[str, ...] = (
            (f"checkpoint {healed.step_id} : écriture interrompue complétée (statut, références, événements)",)
            if healed is not None
            else ()
        )
        if healed is not None and allow_replay and digest is not None and digest == _checkpoint_digest(healed):
            # L'hôte renvoie l'appel que le crash a interrompu : c'est un rejeu
            # de CE node, que la sortie soit ou non valide pour le suivant.
            if not healed.state.pending_steps:
                self._kernel.complete(wfi.id, ctx)
                return ResumeOutcome(
                    ok=True,
                    finished=True,
                    node_id=healed.step_id,
                    replay=True,
                    replay_of=healed.step_id,
                    repaired=(*repaired_notes, "run terminé : tous les nodes étaient déjà checkpointés"),
                )
            following = healed.state.pending_steps[0]
            return ResumeOutcome(
                ok=True,
                finished=False,
                node_id=following,
                contract=contracts.get(following),
                replay=True,
                replay_of=healed.step_id,
                repaired=repaired_notes,
            )

        current_id = self._current_node(wfi, order)
        if current_id is None and wfi.status is WorkflowStatus.CHECKPOINTED:
            last_checkpoint = self._kernel.list_checkpoints(wfi.id)[-1:]
            if last_checkpoint and not last_checkpoint[0].state.pending_steps:
                # Tous les nodes sont checkpointés ; seul ``complete`` n'a pas eu
                # lieu. Terminer sur la foi de la dernière sortie ACCEPTÉE : la
                # renvoyer suffit, toute autre est refusée (jamais de clôture
                # sur une sortie que ce run n'a pas vue).
                last = last_checkpoint[0]
                if allow_replay and digest is not None and digest == _checkpoint_digest(last):
                    self._kernel.complete(wfi.id, ctx)
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
        if current_id is None or current_id not in contracts:
            raise _refusal(
                f"run {run_id} : aucun node courant résoluble (status={wfi.status.value})", run_id, repaired_notes
            )

        if wfi.status is WorkflowStatus.PAUSED and (held := self._hold_in_force(wfi)):
            raise _refusal(
                f"run {run_id} : {held} ; rien n'a été écrit. Reprendre après `grimoire orchestrate resume`.",
                run_id,
                repaired_notes,
            )
        contract = contracts[current_id]
        faults = check_output_against_contract(contract, output)
        #: Le gate du node : une sortie refusée par son contrat révoque les octrois qui couvrent le run (W5-06).
        contract_faults = tuple(faults)
        if contract.kind == "checkpoint":
            if wfi.status is WorkflowStatus.PAUSED and (digest is None or self._crossed_node_for_digest(wfi, digest) is None):
                # K11 : pause levée, checkpoint jamais présenté : le challenge précède le jugement, sinon la décision
                # courante serait refusée en `no_challenge` alors que personne n'a pu y répondre. (Pas pour un rejeu.)
                self._present(run_id, contract, blueprint)
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
                self._authority(run_id),
                self._project_root,
                current_id,
                output,
                destructive=current_id in destructive_checkpoints(blueprint),
                cap=max_amendments(blueprint).get(current_id, DEFAULT_MAX_AMENDMENTS),
                plan=self._projected_plan(blueprint, current_id),
                grant=lambda: self._grant_lookup(run_id, current_id),
            )
            if gate is not None and gate.refusal:
                faults.append(gate.refusal)
        else:
            gate = None
        attestation = gate.attestation.to_dict() if gate is not None else None
        warnings = (gate.warning,) if gate is not None and gate.warning else ()
        if faults and allow_replay and digest is not None:
            # Vérification pure, avant toute écriture : un rejeu ne doit ni
            # rouvrir le node courant (STEP_STARTED) ni le bloquer.
            crossed = self._crossed_node_for_digest(wfi, digest)
            if crossed is not None:
                return ResumeOutcome(
                    ok=True,
                    finished=False,
                    node_id=current_id,
                    contract=contract,
                    replay=True,
                    replay_of=crossed,
                    repaired=repaired_notes,
                )
        resumed_from_pause = wfi.status is WorkflowStatus.PAUSED
        if resumed_from_pause:
            # K11 : la pause est levée (sinon on a refusé plus haut) ; le challenge d'un checkpoint a été émis
            # plus haut, pour la session qui reprend — celle qui a posé la pause n'est plus forcément là.
            wfi = self._kernel.unpause(wfi.id, ctx)
        if wfi.status is not WorkflowStatus.RUNNING or resumed_from_pause:
            # (Le docteur s'appuie sur ce test : un run déjà RUNNING n'est pas rouvert,
            # voir `doctor.resume_realigns_status`.)
            # CHECKPOINTED (crash avant le advance_step du node suivant) ou
            # BLOCKED (retentative après correction de l'hôte) : les deux
            # ramènent au même point, ouvrir l'exécution du node courant.
            wfi = self._kernel.advance_step(wfi.id, ctx, step_id=current_id)
        if faults:
            wfi = self._kernel.fail_step(wfi.id, ctx, step_id=current_id, reason="; ".join(faults))
            if contract_faults:
                self._grants().revoke_on_red_gate(run_id, current_id, contract_faults)
            if gate is not None and (gate.decision in ("reject", "amend") or gate.drifted):
                # Un refus, un amendement ou une dérive de plan (W2-11) est une
                # décision : il consomme son challenge, la présentation suivante
                # en émet un neuf (sur le plan courant, pour une dérive).
                self._authority(run_id).record_decision(
                    current_id,
                    "plan_drifted" if gate.drifted else gate.decision,
                    gate.refusal if gate.drifted else gate.reason,
                    gate.attestation,
                )
                if gate.attestation.attested:
                    self._settle_linked_question(wfi, run_id, current_id, gate.decision)
            if gate is not None and gate.refusal and gate.refusal.startswith("awaiting_human"):
                self._ask_linked_task(wfi, run_id, current_id, gate.attestation.explain())
            self._present(run_id, contract, blueprint)
            return ResumeOutcome(
                ok=False,
                finished=False,
                node_id=current_id,
                faults=tuple(faults),
                contract=contract,
                repaired=repaired_notes,
                attestation=attestation,
            )

        # Décision "termine / avance vers quel node" déléguée à
        # grimoire_flows_core.resume_outcome (issue #354) quand le backend
        # Rust est actif : elle nomme explicitement le cas "current_id
        # absent de order" (devrait ne jamais arriver — contracts et order
        # viennent du même blueprint) plutôt que de laisser `order.index`
        # lever un ValueError anonyme, comme le ferait la référence Python
        # ci-dessous si jamais cet invariant était violé.
        if _use_rust_backend():
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
        self._record_accepted_contract(meta, contracts, order, current_id)
        wfi, _chk = self._kernel.checkpoint(
            wfi.id,
            ctx,
            step_id=current_id,
            completed_steps=completed,
            pending_steps=pending,
            evidence_refs=[_OUTPUT_DIGEST_PREFIX + digest] if digest is not None else None,
        )
        if gate is not None:
            self._authority(run_id).record_decision(
                current_id, gate.decision, gate.reason, gate.attestation, gate.accepted_risk
            )
            if gate.accepted_risk is not None:
                self._record_accepted_risk(wfi, run_id, current_id, gate.accepted_risk)
            if gate.attestation.attested:
                self._settle_linked_question(wfi, run_id, current_id, gate.decision)
        if finished:
            self._kernel.complete(wfi.id, ctx)
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
        held = self._hold_in_force(wfi)
        if held:
            # K11 : la frontière de node est le seul endroit où un run se pose sans rien casser — le node vient
            # d'être franchi, le suivant n'est ni présenté, ni exécuté (un exécuteur par dispatch lancerait un agent).
            self._kernel.pause(wfi.id, ctx, reason=held)
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
        self._present(run_id, next_contract, blueprint)
        # Le node suivant n'est pas encore ouvert (pas de advance_step ici) :
        # un crash juste après cet appel laisse le kernel en CHECKPOINTED, et
        # le prochain resume() rouvrira ce même node suivant en premier —
        # jamais de node sauté, jamais de node rejoué deux fois.
        (executor or InteractiveNodeExecutor()).execute(
            next_contract, context_pack=self._context_pack(wfi, meta.blueprint_id)
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

    def _record_accepted_contract(
        self, meta: FlowRunMeta, contracts: dict[str, NodeContract], order: list[str], node_id: str
    ) -> None:
        """W2-04-S1 : le contrat sous lequel le node est franchi devient son empreinte.

        Une retouche du node courant est acceptée (voir ``fingerprint``) ; sans ce
        ré-enregistrement le node franchi garderait l'empreinte d'avant la retouche
        et le franchissement suivant serait refusé en ``blueprint_drift``. Écrit AVANT
        le checkpoint : un crash entre les deux laisse un node encore courant, jamais
        un node franchi à l'empreinte périmée. Un run sans empreinte (antérieur à
        W2-04) reste « non vérifié ». ``blueprint_sha256`` est celui du blueprint vu
        à ce franchissement.
        """
        if meta.blueprint_sha256 is None:
            return
        digests = {**meta.node_digests, node_id: contract_digest(contracts[node_id].to_dict())}
        sha = self._digest_of(contracts, order)
        if digests != dict(meta.node_digests) or sha != meta.blueprint_sha256:
            self._save_meta(replace(meta, blueprint_sha256=sha, node_digests=digests))

    def status(self, run_id: str, *, include_contract: bool = True) -> FlowStatusView:
        """Vue de lecture seule — ne mute jamais le kernel."""
        meta = self._load_meta(run_id)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        order = list(meta.order)
        current_id = self._current_node(wfi, order)

        # Découpage completed/pending délégué à grimoire_flows_core.status_slices
        # (issue #354) quand le backend Rust est actif — même verdict que la
        # référence Python ci-dessous. Corrigé pour l'issue #414 : un run
        # ABORTED/REFUSED sans node courant résoluble n'affiche plus `order`
        # en entier comme si tout était fait — voir
        # _last_checkpoint_completed_steps et le docstring de
        # rust/grimoire-flows-core/src/lib.rs.
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            checkpoint_completed = list(self._last_checkpoint_completed_steps(wfi))
            completed_list, pending_list = _rust_core.status_slices(
                order, current_id, wfi.status.value, checkpoint_completed
            )
        elif current_id is not None:
            if current_id in order:
                idx = order.index(current_id)
                completed_list = order[:idx]
                pending_list = order[idx + 1 :]
            else:
                # Défensif, hors périmètre de l'issue #414 : ne devrait
                # jamais arriver (order et current_id viennent du même run).
                completed_list = list(order)
                pending_list = []
        elif wfi.status in (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED):
            completed_list = list(order)
            pending_list = []
        else:
            # ABORTED/REFUSED (ou tout autre statut dégénéré sans node
            # courant résoluble) : la seule source de vérité est ce qui a
            # réellement été checkpointé, jamais une position déduite.
            completed_list = list(self._last_checkpoint_completed_steps(wfi))
            pending_list = []

        contract = None
        if include_contract and current_id is not None and not (wfi.status is WorkflowStatus.PAUSED and self._hold_in_force(wfi)):
            # K11 : un run posé par un hold ne sert pas le contrat du node suivant, qu'aucun appelant ne doit exécuter.
            contract = build_node_contracts(
                load_blueprint(self._blueprint_file(meta), self._project_root), self._project_root
            ).get(current_id)
        return FlowStatusView(
            run_id=wfi.id,
            blueprint_id=meta.blueprint_id,
            status=wfi.status.value,
            current_node=current_id,
            completed_nodes=tuple(completed_list),
            pending_nodes=tuple(pending_list),
            last_refusal=self._last_refusal(wfi),
            contract=contract,
            parent_run_id=meta.parent_run_id,
            directive=self.directive(run_id) if include_contract else None,
            checkpoint_decisions=self._authority(run_id).decisions(),
            blueprint_drift=self._drift_state(meta, wfi)[0] if include_contract else None,
            blueprint_sha256=meta.blueprint_sha256,
            **(self._questions_receipt(wfi) if include_contract else {}),
        )

    def _questions_receipt(self, wfi: WorkflowInstance) -> dict[str, int]:
        """``questions_asked`` / ``question_interruptions`` de la tâche liée au run (lot 4.6), ``{}`` sans tâche liée.

        Lecture seule, meilleur effort : le statut d'un run ne dépend pas du ledger. Une question
        déduite par l'agent n'est pas « posée » ; un lot de blocages posés ensemble est une seule interruption.
        """
        import contextlib

        from grimoire.core.exceptions import GrimoireError
        from grimoire.missions.questions import questions_summary

        with contextlib.suppress(GrimoireError, OSError, ValueError):
            service = self._linked_service(wfi)
            task = service.ledger.get_task(wfi.task_id) if service is not None else None
            if task is not None:
                summary = questions_summary(task)
                return {
                    "questions_asked": summary["asked_to_human"],
                    "question_interruptions": summary["interruptions"],
                }
        return {}

    def next_wave(self, run_id: str) -> list[str]:
        """Les nodes prêts ensemble (W5-04) : non faits, tous leurs prédécesseurs du DAG faits.

        Lecture seule. Un run terminé, abandonné ou refusé n'a pas de liste. Le
        moteur n'ouvre toujours qu'un node à la fois : cette liste dit ce qui
        peut s'exécuter en parallèle, elle n'en lance rien. Un node d'une vague
        plus tardive y figure dès que ses propres prédécesseurs sont faits
        (a -> b avec c indépendant : après a, b et c).
        """
        view = self.status(run_id, include_contract=False)
        wfi = self._kernel.get_instance(run_id)
        if wfi is None or _is_flow_terminal(wfi.status):
            return []
        meta = self._load_meta(run_id)
        return ready_nodes(load_blueprint(self._blueprint_file(meta), self._project_root), view.completed_nodes)

    def doctor(self, run_id: str) -> FlowDoctorReport:
        """Rejoue le journal d'un run et vérifie ses invariants — lecture seule (W1-03b).

        Le journal d'événements fait autorité ; toute réparation est proposée
        dans le rapport, jamais appliquée. Lève si ni la méta ni l'instance du
        run n'existent (rien à diagnostiquer).
        """
        path = self._meta_path(run_id)
        meta: FlowRunMeta | None = None
        meta_unreadable = ""
        if path.is_file():
            try:
                meta = FlowRunMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                meta_unreadable = f"méta illisible ({exc})"
        wfi = self._kernel.get_instance(run_id)
        if meta is None and wfi is None and not meta_unreadable:
            raise GrimoireRuntimeError(f"run introuvable : {run_id}")
        current = self._current_node(wfi, list(meta.order)) if meta is not None and wfi is not None else None
        report = diagnose(
            run_id=run_id,
            meta=meta,
            instance=wfi,
            events=self._kernel.get_run_events(run_id),
            checkpoints=self._kernel.list_checkpoints(run_id),
            current_node=current,
        )
        if meta_unreadable:
            return replace(report, unevaluated=(*report.unevaluated, meta_unreadable))
        return report

    def list_runs(self) -> list[FlowStatusView]:
        return [self.status(run_id, include_contract=False) for run_id in self.list_run_ids()]

    def abort(self, run_id: str, *, reason: str = "") -> WorkflowInstance:
        self._load_meta(run_id)  # 404 franc si le run n'existe pas côté flows
        wfi = self._kernel.get_instance(run_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"run introuvable côté kernel : {run_id}")
        aborted = self._kernel.abort(wfi.id, self._ctx_for(wfi), reason=reason)
        self._release_linked_question(run_id, list(self._load_meta(run_id).order), wfi)
        return aborted
