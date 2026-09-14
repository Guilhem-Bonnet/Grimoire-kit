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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError, GrimoireRuntimeError
from grimoire.flows import pilot
from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, resolve_composite_ref
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
    # "green" | "red" | "acceptance_unrunnable" | "cost_capped" | "refused_v2" |
    # "host_unavailable" — un node "composite" (issue #206) n'ajoute aucune
    # nouvelle valeur : "green"/"red"/"cost_capped" portent le même sens pour
    # le sous-flow entier que pour un dispatch simple.
    verdict: str
    needs_review: bool
    provider: str | None
    attempts: int
    escalations: int
    cost_usd: float | None
    uncertainties: tuple[dict[str, Any], ...]
    #: « executed » / « unrunnable » / « judged » (issue #428, point 4) —
    #: voir ``_acceptance_status`` pour la règle exacte. « composite »
    #: (issue #206) : ce node n'a exécuté aucune commande lui-même, son
    #: acceptance EST la complétion du sous-flow qu'il a lancé.
    acceptance_status: str = "judged"
    #: Posé quand un V0 sans acceptance structurée a été rétrogradé en V1
    #: (issue #428, suite) — ``None`` sinon (V0 structuré, V1 déclaré, V2).
    verifiability_warning: str | None = None
    #: Le ``run_id`` du sous-flow lancé par ce node (issue #206) — ``None``
    #: pour un node qui n'est pas ``kind: "composite"``. Sa propre preuve
    #: (par node) vit dans le Mission Ledger de CE run, pas dans celui du
    #: parent : ``grimoire flow status <child_run_id>`` la montre.
    child_run_id: str | None = None

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
            "verifiability_warning": self.verifiability_warning,
            "child_run_id": self.child_run_id,
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
    verifiability_warning: str | None = None,
) -> NodeDispatchOutcome:
    last = report.attempts[-1] if report.attempts else None
    known_costs = [a.cost_usd for a in report.attempts if a.cost_usd is not None]
    if report.unrunnable is not None:
        verdict = "acceptance_unrunnable"
    elif report.cost_capped is not None:
        # Le pilote (issue #209) a arrêté l'escalade : distinct d'un "red"
        # ordinaire — la cascade n'a pas épuisé la chaîne, elle a renoncé
        # au palier suivant que le plafond de coût interdisait.
        verdict = "cost_capped"
    else:
        verdict = "green" if report.succeeded else "red"
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
        verifiability_warning=verifiability_warning,
    )


@dataclass(frozen=True, slots=True)
class _ChildRun:
    """Ce qu'un run enfant a produit — la brique commune à ``composite`` et aux six autres genres (#206/#207)."""

    child_run_id: str
    status: str  # "finished" | "blocked" | "waiting_host" | "cost_capped"
    cost_usd: float | None
    faults: tuple[str, ...]
    host_reason: str | None
    attempts: int
    escalations: int
    #: La sortie soumise pour le dernier node de l'enfant, quand
    #: ``status == "finished"`` — ``None`` sinon. Un genre y lit une clé
    #: annexe (``novelty_key`` pour ``loop-until-dry``) que le contrat de
    #: pins ignore déjà.
    output: dict[str, Any] | None = None


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
        engine: FlowEngine | None = None,
        extra_context: str = "",
    ) -> None:
        self._project_root = project_root
        self._blueprint_path = blueprint_path
        self._service = service or TaskService(project_root)
        self._max_tier = max_tier
        self._call_timeout = call_timeout
        self._actor = actor
        #: Texte ajouté à la description de chaque tâche que CETTE instance
        #: crée (issue #207) — le moyen par lequel un genre à plusieurs
        #: tentatives (``verify-panel``, ``judge``) transmet l'angle imposé
        #: d'une tentative à l'ouvrier qui l'exécute : une instance par
        #: tentative, chacune avec son propre ``extra_context``. Vide :
        #: comportement inchangé (aucun texte ajouté).
        self._extra_context = extra_context
        #: L'engine parent (issue #206) — nécessaire pour lancer le sous-flow
        #: d'un node ``kind: "composite"`` (:meth:`FlowEngine.child_engine`).
        #: ``None`` : comportement inchangé pour tout run sans node composite ;
        #: un node composite rencontré sans engine est un refus interne nommé
        #: (voir :meth:`_execute_composite`), jamais un crash.
        self._engine = engine
        #: Agent (issue #373) au nom duquel tous les nodes de ce run sont
        #: dispatchés — le run entier a un seul exécuteur, donc une seule
        #: identité d'agent ; son ``context`` déclaré s'ajoute au contrat de
        #: chaque node, rien de plus. ``None`` : comportement inchangé
        #: (aucun contexte d'agent ajouté).
        self._agent = agent
        #: Politique du pilote (issue #209) — chargée une fois pour tout le
        #: run, comme le reste de la configuration de cette instance.
        self._pilot_policy = pilot.load_pilot_policy(project_root)
        self.last_result: NodeExecutionResult | None = None
        self.node_outcomes: dict[str, NodeDispatchOutcome] = {}
        self.host_node: str | None = None
        self.host_reason: str | None = None
        self.blocked_node: str | None = None
        self.blocked_report: DispatchReport | None = None

    def execute(self, contract: NodeContract, *, context_pack: dict[str, Any]) -> NodeExecutionResult:
        # Issues #206/#207 : un node de genre ne passe jamais par la cascade
        # ordinaire — son "acceptance" est la complétion d'un ou plusieurs
        # sous-flows (ou, pour "checkpoint", une décision d'hôte), pas le
        # verdict d'une commande unique. Branché avant toute création de
        # tâche : ces nodes n'ont pas de tâche de dispatch à eux-mêmes (sauf
        # "checkpoint", qui EST sa propre tâche — voir son docstring).
        genre_method = _GENRE_EXECUTORS.get(contract.kind)
        if genre_method is not None:
            return genre_method(self, contract, context_pack)

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
        # Un V0 sans acceptance exécutable est le fossé exact de l'issue #428 :
        # le texte seul aurait suffi à fermer le node sur la foi de l'ouvrier.
        # `contract.verifiability_warning` (posé au chargement du blueprint,
        # `blueprint_loader._verifiability_warning`) porte exactement ce
        # diagnostic quand et seulement quand ce cas se présente — la même
        # règle qu'ailleurs (« un faux V0 est pire qu'un faux V2 ») rétrograde
        # ce node en V1 : jamais fermé sur la seule enveloppe, toujours à
        # relire. Appliqué ici, avant `run_dispatch`, pour que le palier de
        # départ et la transition finale du ledger le voient aussi — pas
        # seulement le libellé affiché après coup.
        if verifiability is Verifiability.V0 and contract.verifiability_warning is not None:
            verifiability = Verifiability.V1

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
        # Le pilote (issue #209) décide du palier de départ et du plafond de
        # coût avant cet appel — jamais après. `self._max_tier` (le
        # `--max-tier` explicite de la CLI) reste prioritaire sur le plafond
        # d'escalade de la politique : un opérateur qui le donne pour CE run
        # sait mieux que la politique par défaut du projet.
        pilot_decision = pilot.decide(verifiability, policy=self._pilot_policy)
        effective_max_tier = self._max_tier if self._max_tier is not None else pilot_decision.max_tier
        report = run_dispatch(
            self._service,
            task_id,
            checks=checks,
            check_expect_exits=(0, *acceptance_expects),
            check_expect_stdout_contains=(None, *acceptance_stdout),
            check_timeouts=(None, *acceptance_timeouts),
            acceptance_declared=contract.has_structured_acceptance,
            verifiability_override=verifiability,
            verifiability_warning=contract.verifiability_warning,
            start_tier=pilot_decision.start_tier,
            max_tier=effective_max_tier,
            max_cost_usd=pilot_decision.max_cost_usd,
            call_timeout=self._call_timeout,
            actor=self._actor,
            agent=self._agent,
            project_root=self._project_root,
            # Clé de série pour le pass^k (issue #442) : le node de blueprint,
            # pas `task_id` — `_task_id_for` l'encode avec `run_id`, qui change
            # à chaque rejeu du même blueprint. Sans ceci, deux runs du même
            # node compteraient comme deux séries à une seule observation
            # chacune, jamais comme une série rejouée.
            replay_key=f"{blueprint_id}:{node_id}",
        )
        self.node_outcomes[node_id] = _node_outcome_from_report(
            node_id,
            task_id,
            verifiability,
            report,
            has_structured_acceptance=contract.has_structured_acceptance,
            verifiability_warning=contract.verifiability_warning,
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

    def _execute_plain(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """Le dispatch ordinaire — un node, une tâche, une cascade (#311/#323).

        Extrait tel quel de l'ancien corps de :meth:`execute` (issue #207) :
        les genres de node (``checkpoint``, ``judge``) le réutilisent pour
        leur propre phase de décision d'hôte, sur le même node, une seconde
        fois avec un ``extra_context`` différent — jamais une seconde
        implémentation de la cascade.
        """
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
        # Un V0 sans acceptance exécutable est le fossé exact de l'issue #428 :
        # le texte seul aurait suffi à fermer le node sur la foi de l'ouvrier.
        # `contract.verifiability_warning` (posé au chargement du blueprint,
        # `blueprint_loader._verifiability_warning`) porte exactement ce
        # diagnostic quand et seulement quand ce cas se présente — la même
        # règle qu'ailleurs (« un faux V0 est pire qu'un faux V2 ») rétrograde
        # ce node en V1 : jamais fermé sur la seule enveloppe, toujours à
        # relire. Appliqué ici, avant `run_dispatch`, pour que le palier de
        # départ et la transition finale du ledger le voient aussi — pas
        # seulement le libellé affiché après coup.
        if verifiability is Verifiability.V0 and contract.verifiability_warning is not None:
            verifiability = Verifiability.V1

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
        # Le pilote (issue #209) décide du palier de départ et du plafond de
        # coût avant cet appel — jamais après. `self._max_tier` (le
        # `--max-tier` explicite de la CLI) reste prioritaire sur le plafond
        # d'escalade de la politique : un opérateur qui le donne pour CE run
        # sait mieux que la politique par défaut du projet.
        pilot_decision = pilot.decide(verifiability, policy=self._pilot_policy)
        effective_max_tier = self._max_tier if self._max_tier is not None else pilot_decision.max_tier
        report = run_dispatch(
            self._service,
            task_id,
            checks=checks,
            check_expect_exits=(0, *acceptance_expects),
            check_expect_stdout_contains=(None, *acceptance_stdout),
            check_timeouts=(None, *acceptance_timeouts),
            acceptance_declared=contract.has_structured_acceptance,
            verifiability_override=verifiability,
            verifiability_warning=contract.verifiability_warning,
            start_tier=pilot_decision.start_tier,
            max_tier=effective_max_tier,
            max_cost_usd=pilot_decision.max_cost_usd,
            call_timeout=self._call_timeout,
            actor=self._actor,
            agent=self._agent,
            project_root=self._project_root,
            # Clé de série pour le pass^k (issue #442) : le node de blueprint,
            # pas `task_id` — `_task_id_for` l'encode avec `run_id`, qui change
            # à chaque rejeu du même blueprint. Sans ceci, deux runs du même
            # node compteraient comme deux séries à une seule observation
            # chacune, jamais comme une série rejouée.
            replay_key=f"{blueprint_id}:{node_id}",
        )
        self.node_outcomes[node_id] = _node_outcome_from_report(
            node_id,
            task_id,
            verifiability,
            report,
            has_structured_acceptance=contract.has_structured_acceptance,
            verifiability_warning=contract.verifiability_warning,
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

    def _launch_child(
        self,
        ref: str,
        *,
        node_id: str,
        run_id: str,
        blueprint_id: str,
        suffix: str,
        max_cost_usd: float | None,
        extra_context: str = "",
    ) -> _ChildRun:
        """Lance *ref* comme un run enfant complet, le pilote jusqu'à sa fin (issues #206/#207).

        Cœur partagé de ``composite`` et des six autres genres qui lancent un
        sous-flow (fanout, verify-panel, loop-until-dry, judge, budget,
        replay-diff) — seule la politique d'agrégation diffère d'un genre à
        l'autre, jamais ce mécanisme de lancement. ``max_cost_usd`` s'applique
        au run enfant lancé ICI seul : pour un genre à plusieurs enfants
        (verify-panel, judge, budget...), c'est l'appelant qui doit vérifier
        le cumul *avant* chaque appel — passer ``None`` ici et faire ce calcul
        soi-même (voir chaque ``_execute_<genre>``).
        """
        if self._engine is None:
            raise GrimoireRuntimeError(
                f"node={node_id} : genre exécuté sans engine parent (usage interne invalide, "
                "DispatchExecutor doit être construit avec engine=...)"
            )
        try:
            sub_path = resolve_composite_ref(ref, project_root=self._project_root, blueprint_dir=self._blueprint_path.parent)
        except GrimoireRuntimeError as exc:
            # Ne devrait pas arriver pour composite/fanout/.../replay-diff :
            # déjà validé au chargement du blueprint parent
            # (`blueprint_loader.validate_flow_composition`). Pour `budget`,
            # dont les refs vivent dans une liste de config plutôt que dans
            # `ref`, la même validation au chargement s'applique déjà aussi
            # (`_genre_refs`). Refus défensif nommé, jamais une exception crue.
            raise GrimoireRuntimeError(f"node={node_id} : {exc}") from exc

        child_engine = self._engine.child_engine()
        child_executor = DispatchExecutor(
            project_root=self._project_root,
            blueprint_path=sub_path,
            max_tier=self._max_tier,
            call_timeout=self._call_timeout,
            actor=self._actor,
            agent=self._agent,
            engine=child_engine,
            extra_context=extra_context,
        )
        tag = f"-{suffix}" if suffix else ""
        # Le contrat du premier node de l'enfant n'est jamais réutilisé ici :
        # `_drive_composite_child` pilote sur `executor.last_result`/
        # `ResumeOutcome`, jamais sur un `NodeContract` porté d'un tour à
        # l'autre (contrairement à `_drive`, qui doit le présenter à l'hôte).
        wfi, _ = child_engine.run(
            sub_path,
            executor=child_executor,
            mission_id=f"MIS-flow-{blueprint_id}-{node_id}{tag}",
            task_id=f"FLOW-{run_id}-{node_id}{tag}",
            parent_run_id=run_id,
            parent_node_id=node_id,
        )
        child_run_id = wfi.id
        first_result = child_executor.last_result or NodeExecutionResult(pending=True)
        status, faults, cost, host_reason, last_output = _drive_composite_child(
            child_engine, child_run_id, child_executor, first_result, max_cost_usd=max_cost_usd
        )
        return _ChildRun(
            child_run_id=child_run_id,
            status=status,
            cost_usd=cost,
            faults=faults,
            host_reason=host_reason,
            attempts=len(child_executor.node_outcomes),
            escalations=sum(o.escalations for o in child_executor.node_outcomes.values()),
            output=last_output,
        )

    def _record_genre_outcome(
        self,
        node_id: str,
        run_id: str,
        *,
        verifiability_label: str,
        verdict: str,
        cost_usd: float | None,
        attempts: int,
        escalations: int,
        child_run_id: str | None = None,
    ) -> None:
        self.node_outcomes[node_id] = NodeDispatchOutcome(
            node_id=node_id,
            task_id=f"FLOW-{run_id}-{node_id}",
            verifiability=verifiability_label,
            verdict=verdict,
            needs_review=False,
            provider=None,
            attempts=attempts,
            escalations=escalations,
            cost_usd=cost_usd,
            uncertainties=(),
            acceptance_status=verifiability_label,
            child_run_id=child_run_id,
        )

    def _execute_composite(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """Lance le sous-flow d'un node ``kind: "composite"`` comme son propre run (issue #206).

        Pas de tâche de dispatch pour CE node : sa preuve est le run enfant
        entier (son propre Mission Ledger, sa propre entrée dans
        ``TraceLedger.dispatch_outcome_stats().by_flow`` sous l'id du
        sous-blueprint — voir #473, aucun code neuf requis pour que ``flow
        list --require-measure`` couvre un sous-flow, il est mesuré comme
        n'importe quel flow dispatché). Trois issues, jamais une quatrième :
        terminé (vert), bloqué (rouge), ou suspendu à l'hôte (V2 dans
        l'enfant) — voir :meth:`_launch_child`.

        Le plafond de coût du pilote (issue #209) s'applique ici au **total**
        du sous-flow, pas à une seule tentative : vérifié entre chaque node
        de l'enfant (:func:`_drive_composite_child`), jamais après un node
        déjà vert (même garantie que ``run_dispatch`` pour un node simple).
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")

        child = self._launch_child(
            contract.ref,
            node_id=node_id,
            run_id=run_id,
            blueprint_id=blueprint_id,
            suffix="",
            max_cost_usd=self._pilot_policy.max_cost_usd_per_node,
        )

        if child.status == "waiting_host":
            self.host_node = node_id
            self.host_reason = (
                f"sous-flow {child.child_run_id} (node {node_id}) suspendu : "
                f"{child.host_reason or 'nœud du sous-flow en attente de l’hôte'}"
            )
            self._record_genre_outcome(
                node_id,
                run_id,
                verifiability_label="composite",
                verdict="waiting_host",
                cost_usd=child.cost_usd,
                attempts=child.attempts,
                escalations=child.escalations,
                child_run_id=child.child_run_id,
            )
            result = NodeExecutionResult(
                pending=True,
                extra={"dispatch_refused": "composite_waiting_host", "node_id": node_id, "child_run_id": child.child_run_id},
            )
            self.last_result = result
            return result

        verdict = {"finished": "green", "blocked": "red", "cost_capped": "cost_capped"}[child.status]
        self._record_genre_outcome(
            node_id,
            run_id,
            verifiability_label="composite",
            verdict=verdict,
            cost_usd=child.cost_usd,
            attempts=child.attempts,
            escalations=child.escalations,
            child_run_id=child.child_run_id,
        )
        if verdict != "green":
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False,
                output=None,
                extra={
                    "composite_child_run_id": child.child_run_id,
                    "composite_status": child.status,
                    "composite_faults": list(child.faults),
                },
            )
            self.last_result = result
            return result

        output = {
            "pins": {pin.pin_id: {"contract": pin.contract, "child_run_id": child.child_run_id} for pin in contract.outputs}
        }
        result = NodeExecutionResult(
            pending=False,
            output=output,
            extra={"composite_child_run_id": child.child_run_id, "composite_status": child.status},
        )
        self.last_result = result
        return result

    def _execute_fanout(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "fanout"`` (issue #207) : un node produit N éléments, N sous-flows instanciés.

        Deux phases, jamais confondues : (1) le node lui-même est dispatché
        normalement (:meth:`_execute_plain`) — son acceptance/verdict décide
        de fermer *ce* palier, et sa sortie doit porter, en plus de ses pins
        déclarées, une clé ``fanout_items`` (liste) que ce genre seul lit ;
        (2) une fois ``fanout_items`` connue, un sous-flow enfant est lancé
        par élément (:meth:`_launch_child`, ``ref`` = celui du node). N est
        borné par ``pilot.max_fanout_n`` (issue #209/#207) — **absent**, le
        fan-out est refusé : jamais une politique manquante ne vaut un
        plafond infini implicite.

        Vert seulement si tous les N enfants finissent verts : un fan-out
        n'est jamais « majoritairement » recollé, comme un ``verify-panel``
        n'est jamais vert sur une minorité — recoller un résultat partiel
        comme s'il était le tout serait l'invention que #207 refuse.
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")

        phase1 = self._execute_plain(contract, context_pack)
        if phase1.pending or phase1.output is None:
            return phase1  # V2/host, ou rouge : le fan-out hérite tel quel.

        items = phase1.output.get("fanout_items")
        if not isinstance(items, list) or not items:
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False,
                output=None,
                extra={"fanout_error": "sortie sans 'fanout_items' (liste non vide attendue)", "node_id": node_id},
            )
            self.last_result = result
            return result

        max_n = self._pilot_policy.max_fanout_n
        if max_n is None or len(items) > max_n:
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False,
                output=None,
                extra={
                    "fanout_error": (
                        f"{len(items)} éléments demandés, plafond du pilote "
                        f"(pilot.max_fanout_n) = {max_n!r} — un plafond absent ou dépassé refuse le fan-out"
                    ),
                    "node_id": node_id,
                },
            )
            self.last_result = result
            return result

        children: list[_ChildRun] = []
        total_cost = 0.0
        cap = self._pilot_policy.max_cost_usd_per_node
        cost_capped = False
        for i in range(len(items)):
            if cap is not None and total_cost > cap:
                cost_capped = True
                break
            child = self._launch_child(
                contract.ref,
                node_id=node_id,
                run_id=run_id,
                blueprint_id=blueprint_id,
                suffix=f"fanout{i}",
                max_cost_usd=None,
                extra_context=f"Élément de fan-out {i + 1}/{len(items)}. [genre_attempt_index={i}]",
            )
            children.append(child)
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"fanout : élément {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="fanout", verdict="waiting_host",
                    cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                    escalations=sum(c.escalations for c in children),
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "fanout_waiting_host", "node_id": node_id})
                self.last_result = result
                return result

        attempts = sum(c.attempts for c in children)
        escalations = sum(c.escalations for c in children)
        all_green = len(children) == len(items) and all(c.status == "finished" for c in children)
        verdict = "cost_capped" if cost_capped else ("green" if all_green else "red")
        self._record_genre_outcome(
            node_id, run_id, verifiability_label="fanout", verdict=verdict, cost_usd=total_cost,
            attempts=attempts, escalations=escalations,
        )
        fanout_results = [
            {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
            for i, c in enumerate(children)
        ]
        if verdict != "green":
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None, extra={"fanout_results": fanout_results, "fanout_status": verdict}
            )
            self.last_result = result
            return result
        output = dict(phase1.output)
        output["fanout_results"] = fanout_results
        result = NodeExecutionResult(pending=False, output=output, extra={"fanout_status": verdict})
        self.last_result = result
        return result

    def _execute_verify_panel(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "verify-panel"`` (issue #207) : k vérificateurs indépendants, majorité requise.

        k et les angles imposés (un par vérificateur, distincts) sont validés
        au chargement (:func:`grimoire.flows.blueprint_loader._validate_genre_config`).
        Coût = k tentatives, **toujours** — jamais une cascade qui s'arrête au
        premier consensus apparent (l'issue le nomme explicitement : « jamais
        un vert sur 1/k »). Un plafond de coût qui empêche de compléter les k
        tentatives referme le node en ``cost_capped``, jamais sur un vote
        partiel : un panel incomplet n'a pas d'avis.
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        vp = self._node_config(node_id).get("verifyPanel") or {}
        k = int(vp["k"])
        angles = list(vp["angles"])

        children: list[_ChildRun] = []
        total_cost = 0.0
        cap = self._pilot_policy.max_cost_usd_per_node
        for i in range(k):
            if cap is not None and total_cost > cap:
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="verify-panel", verdict="cost_capped",
                    cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                    escalations=sum(c.escalations for c in children),
                )
                self.blocked_node = node_id
                result = NodeExecutionResult(
                    pending=False, output=None,
                    extra={"verify_panel_error": f"plafond dépassé après {len(children)}/{k} vérificateurs"},
                )
                self.last_result = result
                return result
            child = self._launch_child(
                contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"verifier{i}",
                max_cost_usd=None,
                extra_context=(
                    f"Angle de vérification imposé ({i + 1}/{k}) : {angles[i]}\n"
                    "Instruction : cherche activement à réfuter, ne confirme jamais par défaut.\n"
                    f"[genre_attempt_index={i}]"
                ),
            )
            children.append(child)
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"verify-panel : vérificateur {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="verify-panel", verdict="waiting_host",
                    cost_usd=total_cost, attempts=sum(c.attempts for c in children),
                    escalations=sum(c.escalations for c in children),
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "verify_panel_waiting_host"})
                self.last_result = result
                return result

        greens = sum(1 for c in children if c.status == "finished")
        majority = greens > k / 2
        verdict = "green" if majority else "red"
        attempts = sum(c.attempts for c in children)
        escalations = sum(c.escalations for c in children)
        self._record_genre_outcome(
            node_id, run_id, verifiability_label="verify-panel", verdict=verdict, cost_usd=total_cost,
            attempts=attempts, escalations=escalations,
        )
        panel_results = [
            {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
            for i, c in enumerate(children)
        ]
        if verdict != "green":
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None,
                extra={"verify_panel_results": panel_results, "verify_panel_greens": greens, "verify_panel_k": k},
            )
            self.last_result = result
            return result
        output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
        output["verify_panel_results"] = panel_results
        output["verify_panel_greens"] = greens
        result = NodeExecutionResult(pending=False, output=output)
        self.last_result = result
        return result

    def _execute_loop_until_dry(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "loop-until-dry"`` (issue #207) : relance jusqu'à k tours sans nouveauté.

        Chaque tour doit porter, dans sa sortie, une clé ``novelty_key``
        (chaîne) — la déduplication porte contre **tout le vu** depuis le
        début (un ``set`` cumulatif sur tous les tours), jamais seulement
        contre le dernier tour retenu, comme l'issue le nomme explicitement.
        ``maxRounds`` (validé au chargement) est une borne dure : atteinte
        sans jamais sécher, le node ferme quand même — documenté comme
        distinct d'un séchage réel via ``stop_reason``.
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        lud = self._node_config(node_id).get("loopUntilDry") or {}
        max_rounds = int(lud["maxRounds"])

        seen: set[str] = set()
        rounds: list[dict[str, Any]] = []
        total_cost = 0.0
        cap = self._pilot_policy.max_cost_usd_per_node
        stop_reason = "max_rounds"
        for i in range(max_rounds):
            if cap is not None and total_cost > cap:
                stop_reason = "cost_capped"
                break
            child = self._launch_child(
                contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"round{i}",
                max_cost_usd=None, extra_context=f"Tour {i + 1}/{max_rounds}. [genre_attempt_index={i}]",
            )
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"loop-until-dry : tour {i} (sous-flow {child.child_run_id}) suspendu à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="loop-until-dry", verdict="waiting_host",
                    cost_usd=total_cost, attempts=child.attempts, escalations=child.escalations,
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "loop_until_dry_waiting_host"})
                self.last_result = result
                return result
            if child.status != "finished":
                rounds.append({"index": i, "child_run_id": child.child_run_id, "status": child.status, "novel": False})
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="loop-until-dry", verdict="red",
                    cost_usd=total_cost, attempts=child.attempts, escalations=child.escalations,
                )
                self.blocked_node = node_id
                result = NodeExecutionResult(pending=False, output=None, extra={"loop_until_dry_rounds": rounds})
                self.last_result = result
                return result
            raw_key = child.output.get("novelty_key") if isinstance(child.output, dict) else None
            novelty_key = raw_key if isinstance(raw_key, str) and raw_key else f"round-{i}"
            novel = novelty_key not in seen
            seen.add(novelty_key)
            rounds.append(
                {"index": i, "child_run_id": child.child_run_id, "status": child.status, "novel": novel, "novelty_key": novelty_key}
            )
            if not novel:
                stop_reason = "dry"
                break

        verdict = "cost_capped" if stop_reason == "cost_capped" else "green"
        self._record_genre_outcome(
            node_id, run_id, verifiability_label="loop-until-dry", verdict=verdict, cost_usd=total_cost,
            attempts=len(rounds), escalations=0,
        )
        output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
        output["loop_until_dry_rounds"] = rounds
        output["loop_until_dry_stop_reason"] = stop_reason
        if verdict != "green":
            self.blocked_node = node_id
            result = NodeExecutionResult(pending=False, output=None, extra={"loop_until_dry_stop_reason": stop_reason})
            self.last_result = result
            return result
        result = NodeExecutionResult(pending=False, output=output)
        self.last_result = result
        return result

    def _execute_judge(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "judge"`` (issue #207) : N tentatives sous angles imposés, un juge choisit.

        Les N tentatives sont des sous-flows (:meth:`_launch_child`, comme
        ``verify-panel``) ; le juge, lui, n'en est pas un — c'est le node lui
        décrivant un vocabulaire de revue (V1, « un humain choisit ») dispatché
        une seconde fois via :meth:`_execute_plain`, avec le résumé des N
        tentatives injecté dans le contexte de la tâche. La sortie du juge
        doit porter ``judge_winner`` (index 0..N-1) : sans lui, refus nommé.
        La trace des N tentatives (leurs ``child_run_id``) survit dans la
        sortie du node ET dans le Mission Ledger de chaque enfant.
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        j = self._node_config(node_id).get("judge") or {}
        n = int(j["n"])
        angles = list(j["angles"])

        candidates: list[_ChildRun] = []
        total_cost = 0.0
        cap = self._pilot_policy.max_cost_usd_per_node
        for i in range(n):
            if cap is not None and total_cost > cap:
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="judge", verdict="cost_capped", cost_usd=total_cost,
                    attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
                )
                self.blocked_node = node_id
                result = NodeExecutionResult(
                    pending=False, output=None,
                    extra={"judge_error": f"plafond dépassé après {len(candidates)}/{n} tentatives"},
                )
                self.last_result = result
                return result
            child = self._launch_child(
                contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"candidate{i}",
                max_cost_usd=None,
                extra_context=f"Angle imposé ({i + 1}/{n}) : {angles[i]} [genre_attempt_index={i}]",
            )
            candidates.append(child)
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"judge : tentative {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="judge", verdict="waiting_host", cost_usd=total_cost,
                    attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "judge_waiting_host"})
                self.last_result = result
                return result

        if not all(c.status == "finished" for c in candidates):
            self._record_genre_outcome(
                node_id, run_id, verifiability_label="judge", verdict="red", cost_usd=total_cost,
                attempts=sum(c.attempts for c in candidates), escalations=sum(c.escalations for c in candidates),
            )
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None,
                extra={"judge_error": "une ou plusieurs tentatives n'ont pas fini vertes — rien à départager"},
            )
            self.last_result = result
            return result

        trace = [
            {"index": i, "child_run_id": c.child_run_id, "status": c.status, "cost_usd": c.cost_usd}
            for i, c in enumerate(candidates)
        ]
        summary = "\n".join(f"  [{row['index']}] run {row['child_run_id']} — coût {row['cost_usd']}" for row in trace)
        saved_context = self._extra_context
        self._extra_context = (
            f"{saved_context}\n\nTentatives à départager (indices 0..{n - 1}) :\n{summary}\n\n"
            "Réponds avec 'judge_winner' (l'index du gagnant) dans l'objet de sortie."
        ).strip()
        try:
            judge_result = self._execute_plain(contract, context_pack)
        finally:
            self._extra_context = saved_context

        if judge_result.pending or judge_result.output is None:
            # V2/host, ou rouge sur la phase de jugement elle-même : hérité
            # tel quel — les N tentatives restent dans le Mission Ledger de
            # leurs propres runs, rien n'est perdu même si le juge coince.
            return judge_result

        winner = judge_result.output.get("judge_winner")
        if not isinstance(winner, int) or isinstance(winner, bool) or not (0 <= winner < n):
            self.blocked_node = node_id
            result = NodeExecutionResult(
                pending=False, output=None,
                extra={"judge_error": f"'judge_winner' absent ou hors bornes (reçu {winner!r}, attendu 0..{n - 1})"},
            )
            self.last_result = result
            return result

        existing = self.node_outcomes.get(node_id)
        judge_cost = existing.cost_usd if existing else None
        self._record_genre_outcome(
            node_id, run_id, verifiability_label="judge",
            verdict="green" if existing is None or existing.verdict == "green" else existing.verdict,
            cost_usd=total_cost + (judge_cost or 0.0),
            attempts=sum(c.attempts for c in candidates) + (existing.attempts if existing else 0),
            escalations=sum(c.escalations for c in candidates) + (existing.escalations if existing else 0),
        )
        output = dict(judge_result.output)
        output["judge_trace"] = trace
        output["judge_winner"] = winner
        result = NodeExecutionResult(pending=False, output=output)
        self.last_result = result
        return result

    def _execute_checkpoint(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "checkpoint"`` (issue #207) : approuver, refuser, amender — jamais auto-décidé.

        Toujours ``pending=True``, quelle que soit la cascade : un
        checkpoint est une décision d'hôte par construction, jamais
        rubber-stampée par un palier bon marché. La décision arrive via
        ``flow resume --result`` (forme interactive, même run) ; le contrat
        exact — ``checkpoint_decision`` ``approve``/``reject``/``amend`` et le
        motif — est vérifié par :func:`grimoire.flows.engine._checkpoint_faults`,
        pas ici : un refus (``reject``) bloque le node en nommant le motif
        dans le graphe de décision (l'événement ``STEP_FAILED`` du kernel),
        une reprise ultérieure rouvre exactement ce même node.
        """
        node_id = contract.node_id
        self.host_node = node_id
        self.host_reason = "checkpoint (issue #207) : décision d'hôte obligatoire — jamais auto-décidée par la cascade"
        result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "checkpoint", "node_id": node_id})
        self.last_result = result
        return result

    def _execute_budget(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "budget"`` (issue #207) : plafond déclaré, passes optionnelles abandonnées avant dépassement.

        ``config.budget.passes`` (validé au chargement) est une liste de
        refs ; la première est **obligatoire** (son échec fait échouer tout
        le node) ; chacune des suivantes est **optionnelle** — lancée
        seulement si le coût cumulé après les précédentes reste sous
        ``config.budget.maxCostUsd`` ; sinon, abandonnée (jamais tentée),
        sans faire échouer le node. Différent de ``pilot.max_cost_usd_per_node``
        : ce plafond-ci est déclaré par le blueprint, propre à ce node.
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        b = self._node_config(node_id).get("budget") or {}
        max_cost_usd = float(b["maxCostUsd"])
        passes = list(b["passes"])

        rows: list[dict[str, Any]] = []
        total_cost = 0.0
        for i, ref in enumerate(passes):
            mandatory = i == 0
            if not mandatory and total_cost >= max_cost_usd:
                rows.append({"index": i, "ref": ref, "launched": False})
                continue
            child = self._launch_child(
                ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"pass{i}", max_cost_usd=None,
            )
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"budget : passe {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="budget", verdict="waiting_host", cost_usd=total_cost,
                    attempts=child.attempts, escalations=child.escalations,
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "budget_waiting_host"})
                self.last_result = result
                return result
            rows.append(
                {"index": i, "ref": ref, "launched": True, "child_run_id": child.child_run_id, "status": child.status, "cost_usd": child.cost_usd}
            )
            if mandatory and child.status != "finished":
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="budget", verdict="red", cost_usd=total_cost,
                    attempts=child.attempts, escalations=child.escalations,
                )
                self.blocked_node = node_id
                result = NodeExecutionResult(pending=False, output=None, extra={"budget_passes": rows})
                self.last_result = result
                return result

        self._record_genre_outcome(
            node_id, run_id, verifiability_label="budget", verdict="green", cost_usd=total_cost,
            attempts=sum(1 for r in rows if r.get("launched")), escalations=0,
        )
        output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
        output["budget_passes"] = rows
        result = NodeExecutionResult(pending=False, output=output)
        self.last_result = result
        return result

    def _execute_replay_diff(self, contract: NodeContract, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """``kind: "replay-diff"`` (issue #207) : rejoue le même flow sur la même entrée, compare les traces.

        Deux runs enfants du même ``ref``, sans variation d'un à l'autre
        (aucun ``extra_context`` différenciant) : la divergence éventuelle
        est le signal utile, jamais un défaut qui ferait échouer le node —
        ``replay_diverged`` est exposé dans la sortie, le verdict reste vert
        tant que les deux réplications finissent (peu importe si elles
        divergent l'une de l'autre).
        """
        node_id = contract.node_id
        run_id = str(context_pack.get("run_id") or "no-run")
        blueprint_id = str(context_pack.get("blueprint_id") or "flow")
        cap = self._pilot_policy.max_cost_usd_per_node

        children: list[_ChildRun] = []
        total_cost = 0.0
        for i in range(2):
            if cap is not None and total_cost > cap:
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="replay-diff", verdict="cost_capped", cost_usd=total_cost,
                    attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
                )
                self.blocked_node = node_id
                result = NodeExecutionResult(pending=False, output=None, extra={"replay_diff_error": "plafond dépassé avant la seconde relecture"})
                self.last_result = result
                return result
            child = self._launch_child(
                contract.ref, node_id=node_id, run_id=run_id, blueprint_id=blueprint_id, suffix=f"replay{i}", max_cost_usd=None,
            )
            children.append(child)
            total_cost += child.cost_usd or 0.0
            if child.status == "waiting_host":
                self.host_node = node_id
                self.host_reason = f"replay-diff : relecture {i} (sous-flow {child.child_run_id}) suspendue à l'hôte"
                self._record_genre_outcome(
                    node_id, run_id, verifiability_label="replay-diff", verdict="waiting_host", cost_usd=total_cost,
                    attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
                )
                result = NodeExecutionResult(pending=True, extra={"dispatch_refused": "replay_diff_waiting_host"})
                self.last_result = result
                return result

        traces: list[tuple[str, tuple[str, ...]]] = []
        for c in children:
            status = self._engine.status(c.child_run_id, include_contract=False) if self._engine else None
            traces.append((status.status, status.completed_nodes) if status is not None else (c.status, ()))
        diverged = traces[0] != traces[1]
        both_finished = all(c.status == "finished" for c in children)
        verdict = "green" if both_finished else "red"
        self._record_genre_outcome(
            node_id, run_id, verifiability_label="replay-diff", verdict=verdict, cost_usd=total_cost,
            attempts=sum(c.attempts for c in children), escalations=sum(c.escalations for c in children),
        )
        rows = [{"index": i, "child_run_id": c.child_run_id, "status": c.status} for i, c in enumerate(children)]
        if verdict != "green":
            self.blocked_node = node_id
            result = NodeExecutionResult(pending=False, output=None, extra={"replay_diff_children": rows})
            self.last_result = result
            return result
        output: dict[str, Any] = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
        output["replay_diff_children"] = rows
        output["replay_diverged"] = diverged
        result = NodeExecutionResult(pending=False, output=output)
        self.last_result = result
        return result

    def _node_config(self, node_id: str) -> dict[str, Any]:
        """Le ``config`` du node courant, relu depuis le blueprint (issue #207).

        Les genres n'ont pas leur config sur ``NodeContract`` (qui ne porte
        que ce que tout node partage) : elle est déjà validée au chargement
        (:func:`grimoire.flows.blueprint_loader._validate_genre_config`), il
        suffit de la relire dans le blueprint source, une fois par appel —
        même coût qu'une relecture de contrat, déjà fait ailleurs dans ce
        module (aucune E/S supplémentaire notable, le fichier est petit et
        déjà en cache disque après le premier chargement du run).
        """
        blueprint = load_blueprint(self._blueprint_path, self._project_root)
        for node in blueprint.get("nodes", []):
            if node.get("id") == node_id:
                return dict(node.get("config") or {})
        return {}

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
        if self._extra_context:
            # Issue #207 : l'angle imposé d'une tentative de verify-panel/judge
            # — une instance de DispatchExecutor par tentative, chacune avec
            # son propre extra_context, jamais une chaîne globale au run.
            description = f"{description}\n\n{self._extra_context}"
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


#: Table de dispatch par ``kind`` (issues #206/#207) — un genre de plus n'est
#: jamais un ``if/elif`` de plus dans :meth:`DispatchExecutor.execute`, juste
#: une entrée ici. Toutes les valeurs de
#: :data:`grimoire.flows.blueprint_loader.GENRE_KINDS` doivent y figurer —
#: leur absence serait un genre validé au chargement mais jamais exécutable,
#: un fossé que ``tests/unit/test_flows_genres.py`` couvre.
_GENRE_EXECUTORS: dict[str, Callable[[DispatchExecutor, NodeContract, dict[str, Any]], NodeExecutionResult]] = {
    "composite": DispatchExecutor._execute_composite,
    "fanout": DispatchExecutor._execute_fanout,
    "verify-panel": DispatchExecutor._execute_verify_panel,
    "loop-until-dry": DispatchExecutor._execute_loop_until_dry,
    "judge": DispatchExecutor._execute_judge,
    "checkpoint": DispatchExecutor._execute_checkpoint,
    "budget": DispatchExecutor._execute_budget,
    "replay-diff": DispatchExecutor._execute_replay_diff,
}


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


def _total_cost(executor: DispatchExecutor) -> float | None:
    known = [o.cost_usd for o in executor.node_outcomes.values() if o.cost_usd is not None]
    return sum(known) if known else None


def _drive_composite_child(
    engine: FlowEngine,
    run_id: str,
    executor: DispatchExecutor,
    result: NodeExecutionResult,
    *,
    max_cost_usd: float | None,
) -> tuple[str, tuple[str, ...], float | None, str | None, dict[str, Any] | None]:
    """Enchaîne les nodes du sous-flow d'un node composite (issue #206) — variante coût-plafonné de :func:`_drive`.

    Même boucle que :func:`_drive`, avec une différence : le plafond de coût
    du pilote (issue #209) est vérifié à chaque tour, **avant** de faire
    avancer l'enfant d'un node de plus — jamais après un node déjà vert
    (même garantie que ``run_dispatch`` applique à un dispatch simple). Un
    dépassement abandonne le run enfant (``FlowEngine.abort``, motif nommé)
    plutôt que de le laisser suspendu à mi-chemin sans qu'aucun futur
    ``resume`` ne le débloque.

    Rend ``(status, faults, total_cost, host_reason, last_output)`` où
    ``status`` vaut ``"finished"``, ``"blocked"``, ``"waiting_host"`` ou
    ``"cost_capped"``. ``last_output`` (issue #207) est la sortie soumise
    pour le DERNIER node exécuté de l'enfant quand ``status == "finished"``
    (``None`` sinon) — un genre à plusieurs tentatives (``loop-until-dry``)
    y lit une clé annexe (``novelty_key``) que ``check_output_against_contract``
    ignore déjà (il ne regarde jamais que ``"pins"``).
    """
    while True:
        if result.pending:
            return "waiting_host", (), _total_cost(executor), executor.host_reason, None
        if result.output is None:
            return "blocked", (), _total_cost(executor), None, None
        total = _total_cost(executor)
        if max_cost_usd is not None and total is not None and total > max_cost_usd:
            engine.abort(
                run_id,
                reason=f"plafond du pilote dépassé pour le sous-flow ({total} USD > {max_cost_usd} USD, issue #206/#209)",
            )
            return "cost_capped", (), total, None, None
        last_output = result.output
        outcome: ResumeOutcome = engine.resume(run_id, output=result.output, executor=executor)
        if not outcome.ok:
            return "blocked", outcome.faults, _total_cost(executor), None, None
        if outcome.finished:
            return "finished", (), _total_cost(executor), None, last_output
        assert outcome.contract is not None  # non fini : le moteur a rouvert le node suivant
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
        project_root=project_root,
        blueprint_path=blueprint_path,
        max_tier=max_tier,
        call_timeout=call_timeout,
        engine=engine,
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
        engine=engine,
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
                "verifiability_warning": last.get("verifiability_warning"),
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
