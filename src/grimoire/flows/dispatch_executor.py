"""``DispatchExecutor`` — un ``NodeExecutor`` qui délègue à la cascade (#311).

``InteractiveNodeExecutor`` (#204) rend la main à l'hôte pour chaque node ;
``run_dispatch`` (#323) cascade déjà une tâche unique à travers les paliers de
fournisseurs selon sa classe de vérifiabilité. Ce module est le pont entre les
deux, exactement comme leurs docstrings respectives l'annonçaient : pour le
node courant, il crée ou retrouve une tâche de mission liée au run et au node,
en dérive la classe depuis les critères d'acceptation du node lui-même, puis
lance la cascade avec, pour seule vérification, la conformité du fichier que
l'ouvrier délégué doit écrire au contrat de sortie du node — la même fonction
que ``flow resume`` utilise pour un hôte humain.

Chaîne par classe (reprise telle quelle de la politique #311/#332) :

- **V0** : la cascade essaie ``cheap`` puis ``mid`` puis ``strong``. Un vert
  n'exige rien de plus.
- **V1** : la cascade démarre à ``mid`` ; un vert marque le node « à relire »
  (``run_dispatch`` bascule déjà la tâche en ``needs_verification``).
- **V2** : refusé avant tout appel, comme ``run_dispatch`` lui-même le fait —
  ce node revient à l'hôte. ``execute`` rend alors ``pending=True``, exactement
  le protocole qu'``InteractiveNodeExecutor`` utilise pour rendre la main :
  le moteur ne suspend rien de spécial, il attend un ``flow resume --result``.

Chaîne rouge (chaîne de paliers épuisée, ou aucun palier/fournisseur
disponible) : ``execute`` rend ``pending=False, output=None`` — le signal que
le pilote de run (voir plus bas) lit pour arrêter d'avancer et nommer le node
en faute, rapport de cascade à l'appui.
"""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError, GrimoireRuntimeError
from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint
from grimoire.flows.engine import FlowEngine, check_output_against_contract
from grimoire.flows.schemas import NodeContract, NodeExecutionResult, ResumeOutcome
from grimoire.missions.dispatch import DEFAULT_CALL_TIMEOUT_S, DispatchReport, run_dispatch
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService
from grimoire.missions.verifiability import Verifiability, classify

#: Une acceptance ``{"test": "..."}`` (issue #428) se traduit en une seule
#: invocation ``pytest`` de l'identifiant déclaré — même interpréteur que
#: celui qui fait tourner le kit, pour ne jamais dépendre d'un ``pytest`` du
#: PATH qui ne serait pas celui du projet.
_PYTEST_MODULE_INVOCATION = ("-m", "pytest")

__all__ = [
    "DEFAULT_DISPATCH_ACTOR",
    "DISPATCH_RESULTS_RELPATH",
    "DispatchExecutor",
    "FlowDispatchOutcome",
    "NodeDispatchOutcome",
    "node_dispatch_history",
    "resume_with_dispatch",
    "run_with_dispatch",
]

#: Où l'ouvrier délégué écrit sa sortie — un fichier par node, par run, jamais
#: partagé entre deux runs ni deux nodes : une relance ne peut pas lire la
#: sortie périmée d'une tentative précédente.
DISPATCH_RESULTS_RELPATH = Path("_grimoire-runtime-output/flows/dispatch-results")

DEFAULT_DISPATCH_ACTOR = "flow-dispatch"

#: Le chemin, dans la machine à états des tâches, pour amener une tâche
#: fraîchement créée (ou reprise depuis un état antérieur) jusqu'à ``running``
#: — seul état d'où ``run_dispatch`` peut faire transitionner un vert V1 vers
#: ``needs_verification`` (``_TASK_TRANSITIONS`` dans ``missions/ledger.py``).
_TO_RUNNING_PATH: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.PROPOSED: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.READY: (TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.CLAIMED: (TaskState.RUNNING,),
    TaskState.BLOCKED: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.FAILED: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.NEEDS_VERIFICATION: (TaskState.RUNNING,),
    TaskState.RUNNING: (),
}


def _task_id_for(run_id: str, node_id: str) -> str:
    return f"FLOW-{run_id}-{node_id}"


def _result_path(project_root: Path, run_id: str, node_id: str) -> Path:
    return project_root / DISPATCH_RESULTS_RELPATH / run_id / f"{node_id}.result.json"


def _verify_command(python: str, blueprint_path: Path, node_id: str, result_path: Path) -> str:
    """La commande ``--check`` : relit le même contrat, appelle la même fonction.

    ``python -m grimoire.flows.dispatch_executor --verify`` (voir ``main`` en
    bas de ce module) plutôt qu'une commande ``grimoire`` dédiée : le contrat
    de sortie n'a besoin de rien d'autre que ``check_output_against_contract``,
    déjà public sur ``grimoire.flows.engine`` — ajouter une commande CLI pour
    ce seul usage interne aurait exposé une surface que personne d'autre
    n'appelle jamais à la main.
    """
    return " ".join(
        shlex.quote(part)
        for part in (
            python,
            "-m",
            "grimoire.flows.dispatch_executor",
            "--verify",
            str(blueprint_path),
            node_id,
            str(result_path),
        )
    )


def _acceptance_checks(
    contract: NodeContract,
) -> tuple[tuple[str, ...], tuple[int, ...], tuple[str | None, ...], tuple[float | None, ...]]:
    """Les commandes de l'acceptance structurée d'un node — vides si aucune (issue #428).

    Rend quatre tuples alignés par position, prêts pour les paramètres
    ``check_expect_exits``/``check_expect_stdout_contains``/``check_timeouts``
    de :func:`grimoire.missions.dispatch.run_dispatch`. C'est ici, jamais côté
    ``run_dispatch``, que le fossé signalé par l'issue se referme : sans ces
    commandes en plus du contrôle d'enveloppe (:func:`_verify_command`), le
    gate ne vérifiait jamais que le texte de l'acceptance ; avec elles, une
    enveloppe conforme ne suffit plus si la commande déclarée échoue.

    ``AcceptanceRun.cwd`` (différent de ``"."``) est replié dans la commande
    elle-même (``cd <cwd> && <commande>``) : ``_run_checks`` exécute tout en
    ``shell=True`` dans la racine du projet, sans notion de cwd par check.
    Une ``AcceptanceEvidence`` de genre ``"path_exists"`` devient un ``test -e``
    POSIX ; de genre ``"test"``, une invocation ``pytest`` de l'identifiant
    déclaré, avec le même interpréteur que celui qui exécute le kit.
    """
    cmds: list[str] = []
    expect_exits: list[int] = []
    expect_stdout: list[str | None] = []
    timeouts: list[float | None] = []
    for run in contract.acceptance_runs:
        raw = run.raw if run.cwd in (".", "") else f"cd {shlex.quote(run.cwd)} && {run.raw}"
        cmds.append(raw)
        expect_exits.append(run.expect_exit)
        expect_stdout.append(run.expect_stdout_contains)
        timeouts.append(run.timeout_s)
    for evidence in contract.acceptance_evidence:
        if evidence.kind == "path_exists":
            cmds.append(f"test -e {shlex.quote(evidence.value)}")
        else:  # "test" — le seul autre genre que blueprint_loader produit
            pytest_argv = (sys.executable, *_PYTEST_MODULE_INVOCATION, evidence.value)
            cmds.append(" ".join(shlex.quote(part) for part in pytest_argv))
        expect_exits.append(0)
        expect_stdout.append(None)
        timeouts.append(None)
    return tuple(cmds), tuple(expect_exits), tuple(expect_stdout), tuple(timeouts)


def _ensure_running(service: TaskService, task: MissionTask, *, actor: str) -> MissionTask:
    """Amène *task* jusqu'à ``running``, seul état d'où la cascade peut fermer un V1.

    Chemin vide (``CLOSED``/``CANCELLED``) : la tâche est terminale, rendue
    telle quelle — l'appelant traite ce cas comme un node qui revient à
    l'hôte, il ne sait rien retenter dessus.
    """
    path = _TO_RUNNING_PATH.get(task.status, ())
    current = task
    for target in path:
        move = (
            service.claim(current.id, actor)
            if target is TaskState.CLAIMED
            else service.transition(
                current.id, target, actor, reason="grimoire flow --executor dispatch : préparation de la cascade"
            )
        )
        current = move.task
    return current


@dataclass(frozen=True, slots=True)
class NodeDispatchOutcome:
    """Ce qu'un node a produit — la ligne que ``flow status`` affiche pour lui."""

    node_id: str
    task_id: str
    verifiability: str
    verdict: str  # "green" | "red" | "acceptance_unrunnable" | "refused_v2" | "host_unavailable"
    needs_review: bool
    provider: str | None
    attempts: int
    escalations: int
    cost_usd: float | None
    uncertainties: tuple[dict[str, Any], ...]
    #: « executed » / « unrunnable » / « judged » (issue #428, point 4) —
    #: voir ``_acceptance_status`` pour la règle exacte.
    acceptance_status: str = "judged"

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "verifiability": self.verifiability,
            "verdict": self.verdict,
            "needs_review": self.needs_review,
            "provider": self.provider,
            "attempts": self.attempts,
            "escalations": self.escalations,
            "cost_usd": self.cost_usd,
            "uncertainties": [dict(u) for u in self.uncertainties],
            "acceptance_status": self.acceptance_status,
        }


def _escalations(report: DispatchReport) -> int:
    tiers = [a.tier for a in report.attempts]
    return sum(1 for i in range(1, len(tiers)) if tiers[i] != tiers[i - 1])


def _acceptance_status(report: DispatchReport, *, has_structured_acceptance: bool) -> str:
    """« executed » / « unrunnable » / « judged » pour le node entier (issue #428).

    ``report.unrunnable`` prime toujours. Sinon, un node dont le contrat
    porte au moins une :class:`~grimoire.flows.schemas.AcceptanceRun` ou
    :class:`~grimoire.flows.schemas.AcceptanceEvidence` a vu sa véritable
    acceptance tourner (verte ou rouge) : « executed ». Un node purement
    textuel — qu'il soit V0 sans commande déclarée ou V1 — n'a ni exécution
    mécanique ni juge réel aujourd'hui : « judged » est le mode conservateur
    qui ne prétend jamais à une exécution qui n'a pas eu lieu (voir le
    docstring de ``missions.dispatch._acceptance_status_for_verdict`` pour la
    même règle côté ledger). Documenté comme limite connue, pas une garde
    silencieuse : un vert V1 continue de marquer le node à relire
    (``needs_review``), inchangé par ce correctif.
    """
    if report.unrunnable is not None:
        return "unrunnable"
    return "executed" if has_structured_acceptance else "judged"


def _node_outcome_from_report(
    node_id: str,
    task_id: str,
    verifiability: Verifiability,
    report: DispatchReport,
    *,
    has_structured_acceptance: bool,
) -> NodeDispatchOutcome:
    last = report.attempts[-1] if report.attempts else None
    known_costs = [a.cost_usd for a in report.attempts if a.cost_usd is not None]
    verdict = "acceptance_unrunnable" if report.unrunnable is not None else ("green" if report.succeeded else "red")
    return NodeDispatchOutcome(
        node_id=node_id,
        task_id=task_id,
        verifiability=verifiability.value,
        verdict=verdict,
        needs_review=report.succeeded and verifiability is Verifiability.V1,
        provider=last.provider if last else None,
        attempts=len(report.attempts),
        escalations=_escalations(report),
        cost_usd=sum(known_costs) if known_costs else None,
        uncertainties=tuple(u.to_dict() for u in report.uncertainties),
        acceptance_status=_acceptance_status(report, has_structured_acceptance=has_structured_acceptance),
    )


class DispatchExecutor:
    """Un ``NodeExecutor`` (voir ``flows.executor``) qui délègue à ``run_dispatch``.

    Une instance vit pour la durée d'un seul appel CLI (``flow run`` ou
    ``flow resume`` avec ``--executor dispatch``) : ``node_outcomes`` et
    ``last_result`` accumulent au fil des nodes traités dans cet appel, le
    pilote de run ci-dessous s'en sert pour composer le rapport final.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        blueprint_path: Path,
        service: TaskService | None = None,
        max_tier: str | None = None,
        call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
        actor: str = DEFAULT_DISPATCH_ACTOR,
        agent: str | None = None,
    ) -> None:
        self._project_root = project_root
        self._blueprint_path = blueprint_path
        self._service = service or TaskService(project_root)
        self._max_tier = max_tier
        self._call_timeout = call_timeout
        self._actor = actor
        #: Agent (issue #373) au nom duquel tous les nodes de ce run sont
        #: dispatchés — le run entier a un seul exécuteur, donc une seule
        #: identité d'agent ; son ``context`` déclaré s'ajoute au contrat de
        #: chaque node, rien de plus. ``None`` : comportement inchangé
        #: (aucun contexte d'agent ajouté).
        self._agent = agent
        self.last_result: NodeExecutionResult | None = None
        self.node_outcomes: dict[str, NodeDispatchOutcome] = {}
        self.host_node: str | None = None
        self.host_reason: str | None = None
        self.blocked_node: str | None = None
        self.blocked_report: DispatchReport | None = None

    def execute(self, contract: NodeContract, *, context_pack: dict[str, Any]) -> NodeExecutionResult:
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        mission_id = str(context_pack.get("mission_id") or f"MIS-flow-{blueprint_id}")
        task_id = _task_id_for(run_id, node_id)
        result_path = _result_path(self._project_root, run_id, node_id)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.unlink(missing_ok=True)  # jamais de lecture d'une tentative précédente périmée

        task = self._find_or_create_task(
            mission_id=mission_id, task_id=task_id, contract=contract, result_path=result_path
        )
        verifiability = classify(task)

        if verifiability is Verifiability.V2:
            self.host_node = node_id
            self.host_reason = "classe de vérifiabilité V2 : aucun critère mécanique ni revue reconnue"
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "v2", "node_id": node_id})
            self.last_result = result
            return result

        try:
            task = _ensure_running(self._service, task, actor=self._actor)
        except (TaskRefusedError, GrimoireMissionError) as exc:
            self.host_node = node_id
            self.host_reason = f"tâche {task_id} non préparable pour la cascade : {exc}"
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "not_runnable", "node_id": node_id})
            self.last_result = result
            return result

        if task.status is not TaskState.RUNNING:
            # Tâche terminale (closed/cancelled) retrouvée d'un run antérieur :
            # rien à retenter dessus, ce node revient à l'hôte.
            self.host_node = node_id
            self.host_reason = f"tâche {task_id} terminale ({task.status.value}) : ce node revient à l'hôte"
            result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "terminal_task", "node_id": node_id})
            self.last_result = result
            return result

        # Le check d'enveloppe (conformité du fichier de sortie au contrat de
        # pins) ne suffit plus seul (issue #428) : l'acceptance structurée du
        # node — si le blueprint en déclare une — s'ajoute ici, exécutée par
        # le même gate, dans le même appel de cascade. Sans elle, un texte
        # d'acceptance conforme n'engageait jamais aucune commande réelle.
        acceptance_cmds, acceptance_expects, acceptance_stdout, acceptance_timeouts = _acceptance_checks(contract)
        checks = (_verify_command(sys.executable, self._blueprint_path, node_id, result_path), *acceptance_cmds)
        report = run_dispatch(
            self._service,
            task_id,
            checks=checks,
            check_expect_exits=(0, *acceptance_expects),
            check_expect_stdout_contains=(None, *acceptance_stdout),
            check_timeouts=(None, *acceptance_timeouts),
            acceptance_declared=contract.has_structured_acceptance,
            max_tier=self._max_tier,
            call_timeout=self._call_timeout,
            actor=self._actor,
            agent=self._agent,
            project_root=self._project_root,
        )
        self.node_outcomes[node_id] = _node_outcome_from_report(
            node_id, task_id, verifiability, report, has_structured_acceptance=contract.has_structured_acceptance
        )

        if not report.succeeded:
            self.blocked_node = node_id
            self.blocked_report = report
            result = NodeExecutionResult(pending=False, output=None, extra={"dispatch_report": report.to_dict()})
            self.last_result = result
            return result

        output: dict[str, Any] = {"pins": {}}
        if result_path.is_file():
            try:
                output = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                output = {"pins": {}}
        result = NodeExecutionResult(pending=False, output=output, extra={"dispatch_report": report.to_dict()})
        self.last_result = result
        return result

    def _find_or_create_task(
        self, *, mission_id: str, task_id: str, contract: NodeContract, result_path: Path
    ) -> MissionTask:
        ledger = self._service.ledger
        existing = ledger.get_task(task_id)
        if existing is not None:
            return existing
        if ledger.get_mission(mission_id) is None:
            ledger.create_mission(f"Flow dispatch — {contract.node_id}", origin="flow-dispatch", mission_id=mission_id)
        pins_desc = (
            "\n".join(f"  - {p.pin_id} : {p.contract}" for p in contract.outputs)
            if contract.outputs
            else "  (aucune pin de sortie — un objet 'pins' vide suffit)"
        )
        description = (
            f"{contract.description or contract.label or contract.node_id}\n\n"
            f"Écris ta sortie dans le fichier {result_path} : un objet JSON "
            '{"pins": {"<id-pin>": {"contract": "<nom-du-contrat>"}}}, une entrée '
            f"par pin de sortie :\n{pins_desc}"
        )
        guardrails = tuple(f"Outil autorisé : {tool}" for tool in contract.tool_boundary)
        acceptance = contract.acceptance or (f"node {contract.node_id} : aucun critère d'acceptation déclaré",)
        return ledger.create_task(
            mission_id,
            contract.label or contract.node_id,
            acceptance=acceptance,
            description=description,
            guardrails=guardrails,
            task_id=task_id,
        )


@dataclass(frozen=True, slots=True)
class FlowDispatchOutcome:
    """Le rapport d'un ``flow run``/``flow resume --executor dispatch`` (#311).

    ``status`` vaut ``"finished"`` (tous les nodes verts, run terminé),
    ``"waiting_host"`` (V2, ou tâche non préparable — le node nommé revient à
    ``flow resume`` comme en mode interactif), ou ``"blocked"`` (chaîne
    épuisée sur ce node, ou sortie non conforme malgré un vert de cascade).
    """

    run_id: str
    status: str
    node_id: str | None
    nodes: tuple[NodeDispatchOutcome, ...]
    faults: tuple[str, ...] = ()
    host_reason: str | None = None
    contract: NodeContract | None = None

    @property
    def total_cost_usd(self) -> float | None:
        known = [n.cost_usd for n in self.nodes if n.cost_usd is not None]
        return sum(known) if known else None

    @property
    def escalations(self) -> int:
        return sum(n.escalations for n in self.nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "node_id": self.node_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "faults": list(self.faults),
            "host_reason": self.host_reason,
            "total_cost_usd": self.total_cost_usd,
            "escalations": self.escalations,
            "contract": self.contract.to_dict() if self.contract else None,
        }


def _outcome(
    executor: DispatchExecutor,
    run_id: str,
    *,
    status: str,
    node_id: str | None,
    faults: tuple[str, ...] = (),
    contract: NodeContract | None = None,
) -> FlowDispatchOutcome:
    return FlowDispatchOutcome(
        run_id=run_id,
        status=status,
        node_id=node_id,
        nodes=tuple(executor.node_outcomes.values()),
        faults=faults,
        host_reason=executor.host_reason,
        contract=contract,
    )


def _drive(
    engine: FlowEngine, run_id: str, executor: DispatchExecutor, contract: NodeContract, result: NodeExecutionResult
) -> FlowDispatchOutcome:
    """Enchaîne les nodes tant que la cascade est verte — cœur du pilotage #311.

    ``engine.resume`` a déjà appelé ``executor.execute`` sur le node suivant
    avant de rendre la main ici (voir ``FlowEngine.resume``) : chaque tour de
    boucle ne fait qu'interpréter ``executor.last_result``, jamais un second
    appel à l'exécuteur.
    """
    while True:
        if result.pending:
            return _outcome(executor, run_id, status="waiting_host", node_id=contract.node_id, contract=contract)
        if result.output is None:
            return _outcome(executor, run_id, status="blocked", node_id=contract.node_id, contract=contract)
        outcome: ResumeOutcome = engine.resume(run_id, output=result.output, executor=executor)
        if not outcome.ok:
            return _outcome(executor, run_id, status="blocked", node_id=outcome.node_id, faults=outcome.faults)
        if outcome.finished:
            return _outcome(executor, run_id, status="finished", node_id=outcome.node_id)
        assert outcome.contract is not None  # non fini : le moteur a rouvert le node suivant
        contract = outcome.contract
        result = executor.last_result or NodeExecutionResult(pending=True)


def run_with_dispatch(
    engine: FlowEngine,
    blueprint_path: Path,
    *,
    project_root: Path,
    mission_id: str = "",
    task_id: str = "",
    max_tier: str | None = None,
    call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
) -> FlowDispatchOutcome:
    """``flow run <blueprint> --executor dispatch`` : démarre puis enchaîne."""
    executor = DispatchExecutor(
        project_root=project_root, blueprint_path=blueprint_path, max_tier=max_tier, call_timeout=call_timeout
    )
    wfi, contract = engine.run(blueprint_path, executor=executor, mission_id=mission_id, task_id=task_id)
    result = executor.last_result or NodeExecutionResult(pending=True)
    return _drive(engine, wfi.id, executor, contract, result)


def resume_with_dispatch(
    engine: FlowEngine,
    run_id: str,
    *,
    project_root: Path,
    max_tier: str | None = None,
    call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
) -> FlowDispatchOutcome:
    """``flow resume <run-id> --executor dispatch`` : reprend le node courant.

    Sans ``--result`` : le node courant (rouvert par ``flow status`` — même
    node qu'un crash aurait laissé en CHECKPOINTED/BLOCKED) est retenté par la
    cascade, pas relu depuis un fichier que l'hôte n'a pas produit.
    """
    meta = engine.run_meta(run_id)
    status = engine.status(run_id, include_contract=True)
    if status.contract is None:
        raise GrimoireRuntimeError(f"run {run_id} : rien à reprendre (status={status.status})")
    executor = DispatchExecutor(
        project_root=project_root,
        blueprint_path=Path(meta.blueprint_path),
        max_tier=max_tier,
        call_timeout=call_timeout,
    )
    result = executor.execute(
        status.contract, context_pack={"run_id": run_id, "blueprint_id": meta.blueprint_id, "mission_id": ""}
    )
    return _drive(engine, run_id, executor, status.contract, result)


def node_dispatch_history(project_root: Path, run_id: str, node_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Ce que ``flow status`` affiche par node — lu du ledger, pas d'une instance.

    Une :class:`DispatchExecutor` ne survit pas à l'appel CLI qui l'a créée ;
    ``flow status`` tourne dans un tout autre process, parfois bien après le
    dispatch. La seule source qui survit est le Mission Ledger : chaque
    tentative de ``run_dispatch`` y a laissé un événement ``task.dispatched``
    (voir ``missions/dispatch.py``), et l'état courant de la tâche dit si le
    node est passé « à relire » (``needs_verification``).
    """
    service = TaskService(project_root)
    rows: list[dict[str, Any]] = []
    for node_id in node_ids:
        task_id = _task_id_for(run_id, node_id)
        task = service.ledger.get_task(task_id)
        if task is None:
            continue
        events = [e for e in service.ledger.events_for(task_id) if e.event_type == "task.dispatched"]
        if not events:
            continue
        last = events[-1].payload
        rows.append(
            {
                "node_id": node_id,
                "task_id": task_id,
                "task_status": task.status.value,
                "needs_review": task.status is TaskState.NEEDS_VERIFICATION,
                "attempts": len(events),
                "provider": last.get("provider"),
                "tier": last.get("tier"),
                "verdict": last.get("verdict"),
                "cost_usd": last.get("cost_usd"),
                "uncertainties": last.get("uncertainties", []),
                # « executed »/« unrunnable »/« judged » (issue #428) — absent
                # (``None``) sur un événement écrit avant ce correctif.
                "acceptance_status": last.get("acceptance_status"),
                "checks": last.get("checks", []),
            }
        )
    return rows


def _cli_verify(argv: list[str]) -> int:
    blueprint_path, node_id, result_path = Path(argv[0]), argv[1], Path(argv[2])
    contracts = build_node_contracts(load_blueprint(blueprint_path))
    contract = contracts.get(node_id)
    if contract is None:
        print(f"node introuvable dans le blueprint : {node_id}", file=sys.stderr)
        return 1
    try:
        output = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{result_path} illisible : {exc}", file=sys.stderr)
        return 1
    faults = check_output_against_contract(contract, output)
    for fault in faults:
        print(fault, file=sys.stderr)
    return 0 if not faults else 1


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée ``python -m grimoire.flows.dispatch_executor --verify ...``.

    Le seul appelant est la commande ``--check`` que :func:`_verify_command`
    construit — jamais un humain au clavier. C'est pourquoi ce n'est pas une
    commande ``grimoire`` : elle n'a rien à offrir hors de ce contexte précis.
    """
    args = argv if argv is not None else sys.argv[1:]
    if len(args) == 4 and args[0] == "--verify":
        return _cli_verify(args[1:])
    print(
        "usage : python -m grimoire.flows.dispatch_executor --verify <blueprint> <node_id> <result_path>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
