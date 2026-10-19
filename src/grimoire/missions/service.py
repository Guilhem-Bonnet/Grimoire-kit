"""Service des tâches — la logique que le CLI et le serveur MCP partagent.

``grimoire task`` (issue #137) portait seul l'enchaînement « machine à états,
puis gate de preuve, puis écriture au ledger ». Exposer les mêmes gestes aux
agents par MCP (issue #138) exigeait soit de le recopier, soit de le sortir du
CLI. Il est ici, et les deux surfaces l'appellent : un gate contourné par l'une
le serait par l'autre, donc il n'y a qu'un endroit où il peut l'être.

Deux règles :

- **Le gate précède l'écriture.** Un refus après l'append laisserait dans un
  journal qui ne se réécrit pas un événement que rien ne justifie.
- **Le board suit le ledger.** Chaque écriture reprojette
  ``_grimoire/standard/task-board.yaml`` quand le projet est enrôlé. C'est ce
  qui rend un claim visible au hook SessionStart sans qu'un humain ait à
  relancer ``task board export``.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError, GrimoireMissionError
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.core.standard_state import current_session, invalidate_cache
from grimoire.missions.admission import (
    QueuePlan,
    RouteRef,
    build_plan,
    load_capacity,
    queue_order,
    read_host,
    ready_since,
    release_capacity,
    wip_claim_guard,
)
from grimoire.missions.board import PRIORITIES, board_status_of, build_board, priority_of, write_board
from grimoire.missions.gates import GateRefusal, GateVerdict, check_transition
from grimoire.missions.intake import IntakeRequest, MissionIntakeService
from grimoire.missions.ledger import MissionLedger, ReapReport
from grimoire.missions.pause import PAUSE_LIFTED_CAUSE, PauseState, pause_claim_guard, read_pause
from grimoire.missions.recall import DEFAULT_TOKEN_BUDGET, TaskRecall, build_task_recall, consolidate_task_memory
from grimoire.missions.schemas import (
    DIRECTIVE_KINDS,
    ORIGIN_AGENT,
    ORIGIN_HUMAN,
    DependencyKind,
    MissionTask,
    RiskProfile,
    TaskCheck,
    TaskClaim,
    TaskDirective,
    TaskState,
)
from grimoire.missions.task_worktree import home_of_worktree
from grimoire.missions.waiting import (
    ABANDON,
    HUMAN_ANSWERED_EVENT,
    QUESTION_ASKED_EVENT,
    UNTIL_UNKNOWN,
    WAIT_REASONS,
    Question,
    ResumeHint,
    Waiting,
    attention_rank,
    new_question_id,
    parse_until,
)

_RISK_ORDER = (
    RiskProfile.LIGHT,
    RiskProfile.STANDARD,
    RiskProfile.STRICT,
    RiskProfile.SECURITY_CRITICAL,
    RiskProfile.RELEASE,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from grimoire.core.agentic_standard import StandardRuntimeArtifact
    from grimoire.memory.manager import MemoryManager

__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "TaskAdded",
    "TaskMove",
    "TaskNote",
    "TaskRefusedError",
    "TaskService",
    "WakeDecision",
]

#: Là où le ledger vit par défaut, relativement à la racine du projet.
DEFAULT_LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: Le board du standard, projection du ledger.
BOARD_RELPATH = STANDARD_DIR / "task-board.yaml"


class TaskRefusedError(GrimoireMissionError):
    """Le gate de preuve refuse la transition. Porte le verdict, pour qu'un
    appelant puisse le rendre tel quel — le refus nomme la preuve et le remède."""

    def __init__(self, task_id: str, verdict: GateVerdict) -> None:
        self.task_id = task_id
        self.verdict = verdict
        manque = "; ".join(str(r) for r in verdict.refusals)
        super().__init__(f"Gate « {verdict.transition_id} » refuse {task_id} : {manque}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": True,
            "task_id": self.task_id,
            "transition": self.verdict.transition_id,
            "strictness": self.verdict.strictness,
            "refusals": [
                {"evidence": r.evidence, "reason": r.reason, "remedy": r.remedy}
                for r in self.verdict.refusals
            ],
        }


@dataclass(frozen=True, slots=True)
class TaskMove:
    """Ce qu'une écriture a produit : la tâche après coup, d'où elle venait, ce
    que le gate a signalé sans bloquer, et le board reprojeté s'il l'a été."""

    task: MissionTask
    previous: TaskState
    verdict: GateVerdict
    board_path: Path | None

    @property
    def advisories(self) -> tuple[str, ...]:
        """Exigences non satisfaites qu'un profil permissif a laissé passer."""
        return tuple(str(r) for r in self.verdict.refusals)

    def to_dict(self) -> dict[str, Any]:
        from grimoire.missions.dispatch_advice import dispatch_advice

        data = self.task.to_dict()
        data["transition"] = f"{self.previous.value} → {self.task.status.value}"
        data["board"] = board_status_of(self.task.status)
        # #654 : `task_claim` (MCP et CLI) ne rendait ni la classe
        # de vérifiabilité ni le modèle qu'elle recommande — l'agent qui
        # réclame devait relire `task show` à part, ou deviner. Même calcul
        # que `grimoire task dispatch` (`missions.dispatch.start_tier_for`
        # part de la même classe), jamais une seconde classification.
        advice = dispatch_advice(self.task)
        data["verifiability"] = advice["verifiability"]
        # `None` sur V2 : aucun nom de modèle valide pour l'outil `Agent` (défaut
        # #662) — `model_tier` ("cheap"/"mid"/"session") reste toujours renseigné.
        data["recommended_model"] = advice["recommended_model"]
        data["model_tier"] = advice["model_tier"]
        # Même champ que TaskNote.to_dict() (issue #638 lot B) : un consommateur
        # qui rejoue cette réponse pour rafraîchir son propre affichage (le
        # cockpit réécrit son bloc de pilotage humain en place plutôt que de
        # refaire un aller-retour réseau après `cancel`/`claim`/`move`/`close`)
        # doit voir la même forme qu'après `prioritize`/`comment` — sans lui,
        # la priorité déclarée reste correcte mais la priorité *effective*
        # retombe sur son repli (medium), masquant une priorité critique tant
        # qu'aucun autre rafraîchissement complet n'est venu la corriger.
        data["effective_priority"] = priority_of(self.task)
        data["directives_pending"] = len(self.task.pending_directives)
        data["directives_unacknowledged"] = len(self.task.unacknowledged_directives)
        if self.advisories:
            data["advisories"] = list(self.advisories)
        if self.board_path is not None:
            data["board_path"] = str(self.board_path)
        return data


@dataclass(frozen=True, slots=True)
class TaskNote:
    """Ce qu'une écriture de pilotage a produit (issue #638) : la tâche après
    coup, le geste, la consigne créée s'il y en a une, et le board reprojeté."""

    task: MissionTask
    change: str
    directive: TaskDirective | None
    board_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        data = self.task.to_dict()
        data["change"] = self.change
        data["board"] = board_status_of(self.task.status)
        data["effective_priority"] = priority_of(self.task)
        data["directives_pending"] = len(self.task.pending_directives)
        data["directives_unacknowledged"] = len(self.task.unacknowledged_directives)
        if self.directive is not None:
            data["directive"] = self.directive.to_dict()
        if self.board_path is not None:
            data["board_path"] = str(self.board_path)
        return data


@dataclass(frozen=True, slots=True)
class TaskAdded:
    """Ce qu'une ouverture de tâche a produit (issue #638, lot A) : la tâche,
    la mission qui la porte — créée à l'occasion ou non — et le board reprojeté."""

    task: MissionTask
    mission_id: str
    mission_created: bool
    board_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        data = self.task.to_dict()
        data["board"] = board_status_of(self.task.status)
        data["mission_created"] = self.mission_created
        if self.board_path is not None:
            data["board_path"] = str(self.board_path)
        return data


#: Titre de la mission ouverte d'office quand le ledger n'en a aucune.
DEFAULT_MISSION_TITLE = "Travaux courants"


@dataclass(frozen=True, slots=True)
class WakeDecision:
    """Ce que le tick fait d'une tâche en attente : ``wake`` (rendue), ``keep`` (elle attend encore)."""

    task_id: str
    reason: str
    action: str
    cause: str
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "reason": self.reason, "action": self.action,
            "cause": self.cause, "written": self.written,
        }


def _claim_guards(root: Path) -> Callable[[Mapping[str, MissionTask], MissionTask], str]:
    """Les gardes que le ledger appelle sous son verrou : la pause d'abord (K11), puis le WIP (K10)."""
    pause, wip = pause_claim_guard(root), wip_claim_guard(root)

    def guard(tasks: Mapping[str, MissionTask], task: MissionTask) -> str:
        return pause(tasks, task) or wip(tasks, task)

    return guard


class TaskService:
    """Lire, réclamer et déplacer les tâches d'un projet, gates compris."""

    def __init__(
        self,
        project_root: Path,
        ledger_root: Path = DEFAULT_LEDGER_RELPATH,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        #: La racine du projet qui porte le ledger et le board : la même, sauf dans un
        #: worktree de tâche (W5-02), où une racine relative du ledger se résout contre
        #: le projet d'origine — sinon le worktree ouvrirait un second ledger.
        self.home_root = self.project_root
        home = None if ledger_root.is_absolute() else home_of_worktree(self.project_root)
        if home is not None:
            self.home_root = home[0]
            ledger_root = home[1] if ledger_root == DEFAULT_LEDGER_RELPATH else self.home_root / ledger_root
        self.ledger_root = ledger_root if ledger_root.is_absolute() else self.project_root / ledger_root
        self._ledger: MissionLedger | None = None
        # ``None`` explicite (mémoire injectée sans backend) et « pas encore
        # résolue » sont deux états différents : sans ce booléon, un manager
        # None passé au constructeur serait re-résolu depuis la config à
        # chaque appel.
        self._memory: MemoryManager | None = memory_manager
        self._memory_resolved = memory_manager is not None
        #: K12 : faux pour le service du plan à blanc (``orchestrate tick --plan``), qui rejoue le tick sur une
        #: copie du ledger — ni board ni mémoire du projet ne doivent alors bouger.
        self.writes_projections = True

    @property
    def ledger(self) -> MissionLedger:
        if self._ledger is None:
            self._ledger = MissionLedger(self.ledger_root)
            self._ledger.claim_guard = _claim_guards(self.home_root)  # K10 WIP + K11 pause, sous le verrou
        return self._ledger

    def refresh(self) -> None:
        """Oublie le cache du ledger : la lecture suivante relit le disque (les écritures d'un autre processus y sont)."""
        self.ledger._invalidate()

    @property
    def has_ledger(self) -> bool:
        return (self.ledger_root / "events.jsonl").is_file()

    @property
    def memory(self) -> MemoryManager | None:
        """Le manager mémoire du projet, résolu au premier accès — ``None`` sans config.

        Un projet sans ``project-context.yaml`` n'a pas de mémoire à consulter :
        le rappel et la consolidation se dégradent alors à ce que le ledger
        seul sait, plutôt que d'échouer.
        """
        if not self._memory_resolved:
            self._memory = self._load_memory()
            self._memory_resolved = True
        return self._memory

    def _load_memory(self) -> MemoryManager | None:
        config_path = self.project_root / "project-context.yaml"
        if not config_path.is_file():
            return None
        try:
            from grimoire.core.config import GrimoireConfig
            from grimoire.memory.manager import MemoryManager as _MemoryManager

            config = GrimoireConfig.from_yaml(config_path)
            return _MemoryManager.from_config(config, project_root=self.project_root)
        except Exception:  # la mémoire est une couche optionnelle du service
            return None

    # ── Lecture ────────────────────────────────────────────────────────────

    def require(self, task_id: str) -> MissionTask:
        task = self.ledger.get_task(task_id)
        if task is None:
            raise GrimoireMissionError(f"Tâche inconnue : {task_id}")
        return task

    def list_tasks(self, mission_id: str | None = None, status: str | None = None) -> list[MissionTask]:
        tasks = self.ledger.list_tasks(mission_id)
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        # K6 : une décision qui attend un humain passe en tête ; le reste garde l'ordre du ledger (tri stable).
        return sorted(tasks, key=attention_rank)

    def list_ready(self, mission_id: str | None = None) -> list[MissionTask]:
        """Les tâches qu'un agent peut réclamer, dans l'ordre de la file (K10) : priorité, vieillissement, ancienneté."""
        ready = self.list_tasks(mission_id, TaskState.READY.value)
        capacity = load_capacity(self.home_root)
        since = ready_since(self.ledger)
        return [e.task for e in queue_order(ready, since, now=datetime.now(UTC), aging_hours=capacity.aging_hours)]

    def queue_plan(
        self,
        *,
        now: datetime | None = None,
        autonomous: bool | None = None,
        probe_host: bool = True,
        routes: bool = True,
        paused: bool = False,
        pause: PauseState | None = None,
    ) -> QueuePlan:
        """Ce que l'admission déciderait maintenant, sans rien écrire (``grimoire queue plan``, K10).

        La sonde de l'hôte ne tourne que si le pool ``host`` est déclaré. Les routes viennent du
        routeur (K9), sans événement ``task.routed`` : l'admission les lit, ne les change pas.
        La pause (K11) est lue dans les drapeaux du projet et de la machine, sauf *pause* donnée.
        """
        capacity = load_capacity(self.home_root)
        host = read_host() if probe_host and capacity.host is not None else None
        unattended = os.environ.get("GRIMOIRE_UNATTENDED", "").strip() == "1" if autonomous is None else autonomous
        return build_plan(
            self.ledger, capacity, now=now or datetime.now(UTC), host=host,
            route_of=self._route_ref if routes else None, paused=paused, autonomous=unattended,
            pause=pause if pause is not None else read_pause(self.home_root),
        )

    def _route_ref(self, task: MissionTask) -> RouteRef:
        from grimoire.missions.route_dispatch import record_route
        from grimoire.missions.verifiability import classify

        routing = record_route(self, task, classify(task, commands_declared=bool(task.checks)), actor="queue", write=False)
        decision = routing.decision
        if routing.policy_invalid or decision is None:
            return RouteRef("refuse")
        if routing.mode == "active" and decision.outcome == "defer":
            return RouteRef("defer", defer_until=decision.defer_until or "")
        if decision.outcome != "route":
            return RouteRef("session")
        return RouteRef("route", decision.provider or "", decision.model or "")

    def release_capacity(self, task_id: str, pool: str, actor: str, reason: str = "") -> None:
        """Un humain rend le créneau *pool* d'une tâche dont la fin des checks n'est pas observable (``capacity.released``)."""
        self.require(task_id)
        release_capacity(self.ledger, task_id, pool, actor=actor, reason=reason)

    def gate(self, task: MissionTask, target: TaskState) -> GateVerdict:
        """Le verdict du gate pour cette transition, sans rien écrire.

        La clôture d'une tâche relue (``needs_verification → closed``) exige en
        plus le reçu de revue (W3-01, :mod:`grimoire.missions.review_receipt`),
        quel que soit le profil : sans reçu frais, le refus est bloquant.
        """
        from_board, to_board = board_status_of(task.status), board_status_of(target)
        if self.home_root == self.project_root:
            verdict = check_transition(self.project_root, task, from_board, to_board)
        else:
            # Worktree de tâche (W5-02) : la transition s'écrit au ledger du projet d'origine,
            # donc ce sont ses déclarations de gate qui l'opposent ; les preuves restent lues ici.
            verdict = check_transition(
                self.project_root, task, from_board, to_board, declarations_root=self.home_root
            )
        if task.status is TaskState.NEEDS_VERIFICATION and target is TaskState.CLOSED:
            from grimoire.missions.review_receipt import check_receipt

            refusal = check_receipt(self.project_root, task)
            if refusal is not None:
                return GateVerdict("review_receipt", "hard_fail", (refusal, *verdict.refusals))
        return verdict

    # ── Écriture ───────────────────────────────────────────────────────────

    def add(
        self,
        title: str,
        acceptance: tuple[str, ...],
        *,
        mission_id: str = "",
        owner: str = "",
        expected_evidence: tuple[str, ...] = (),
        actor: str = "cli",
        origin: str = "cli",
        ready: bool = False,
        checks: tuple[TaskCheck, ...] = (),
        files: tuple[str, ...] = (),
        test_depth: str = "",
    ) -> TaskAdded:
        """Ouvre une tâche — le geste que ``grimoire task add`` et ``task_add`` (MCP) partagent.

        Sans *mission_id*, la première mission du ledger porte la tâche ; s'il
        n'y en a aucune, une mission « Travaux courants » est ouverte. Le
        critère d'acceptation reste exigé par le ledger lui-même, pas ici : un
        critère blanc est retiré avant l'appel pour que ``("  ",)`` soit
        refusé au même titre que ``()``. Avec *ready*, la tâche passe
        ``proposed → ready`` par :meth:`transition`, donc par le gate
        ``proposed_to_ready`` — en profil gouverné, il exige un ``owner``.
        *test_depth* (W6-01) : ``floor``, ``standard`` ou ``strict`` ; le gate de
        fermeture refuse tant que le dernier verdict ne l'atteint pas.
        """
        if test_depth:
            from grimoire.missions.ceremony import DepthLevel

            if test_depth not in {d.value for d in DepthLevel}:
                accepted = ", ".join(d.value for d in DepthLevel)
                raise GrimoireMissionError(f"test_depth inconnue : {test_depth!r} (attendu : {accepted})")
        acceptance = tuple(item.strip() for item in acceptance if item and item.strip())
        expected_evidence = tuple(item.strip() for item in expected_evidence if item and item.strip())
        ledger = self.ledger
        created_mission = False
        if not mission_id:
            missions = ledger.list_missions()
            if missions:
                mission_id = missions[0].id
            else:
                mission_id = ledger.create_mission(title=DEFAULT_MISSION_TITLE, origin=origin, created_by=actor).id
                created_mission = True
        # Le risque se pose à la création, par l'intake déterministe (W1-11) ; seules
        # les hausses sont retenues : jamais de light automatique, jamais de baisse.
        proposed = MissionIntakeService().analyze(IntakeRequest(raw_text=title)).risk_profile
        risk = RiskProfile(proposed)
        if _RISK_ORDER.index(risk) < _RISK_ORDER.index(RiskProfile.STANDARD):
            risk = RiskProfile.STANDARD
        task = ledger.create_task(
            mission_id, title, acceptance=acceptance, owner=owner, expected_evidence=expected_evidence,
            risk_profile=risk, checks=checks, test_depth=test_depth,
            extra={"files": [f for f in (x.strip() for x in files) if f]} if any(x.strip() for x in files) else None,
        )
        if ready:
            task = self.transition(task.id, TaskState.READY, actor).task
        return TaskAdded(task=task, mission_id=mission_id, mission_created=created_mission, board_path=self.project_board())

    def link(self, task_id: str, depends_on: str, kind: DependencyKind = DependencyKind.BLOCKS, *, actor: str = "cli") -> MissionTask:
        """« *task_id* dépend de *depends_on* » ; un lien qui ferme un cycle ``blocks`` est refusé (W5-01)."""
        task = self.ledger.link_tasks(task_id, depends_on, kind, actor_id=actor)
        self.project_board()
        return task

    def waves(self, mission_id: str | None = None) -> dict[str, Any]:
        """Les vagues du plan, ``file_overlaps`` compris (:func:`grimoire.missions.waves.compute_waves`)."""
        from grimoire.missions.wave_checkpoint import claim_blocked, report
        from grimoire.missions.waves import compute_waves

        everything = self.ledger.list_tasks()
        if mission_id is None:
            return compute_waves(everything)
        # Les dépendances se résolvent contre tout le ledger, comme au claim : seul le rapport est filtré.
        plan = compute_waves(everything, only=[t.id for t in everything if t.mission_id == mission_id])
        plan["checkpoints"] = report(self, mission_id)  # W5-05 : les vagues closes n'y figurent plus, leur checkpoint si
        plan["claim_blocked"] = claim_blocked(self, mission_id)  # {tâche: motif} que le claim opposerait
        return plan

    def attach_session(
        self,
        task_id: str,
        session_id: str,
        *,
        session_host: str = "",
        actor: str = "hook",
        explicit: bool = False,
        host_version: str = "",
    ) -> MissionTask:
        """Rattache la session d'hôte courante au claim de *task_id* (issue #638).

        Même règle que :meth:`MissionLedger.attach_session` — un événement de
        plus, jamais une réécriture, jamais le vol d'un claim déjà rattaché à
        une autre session — puis, pour un rattachement explicite, le journal de
        la session note l'epoch du claim (:meth:`_note_session_lease`) ; enfin le board est reprojeté pour que le cockpit
        voie la session sans attendre un autre geste.
        """
        task = self.ledger.attach_session(
            task_id, session_id, session_host=session_host, actor_id=actor, explicit=explicit,
            host_version=host_version,
        )
        if explicit:
            # `task attach` (K3-S1-02) rattache une session à un claim qui peut être d'un autre
            # epoch que celui qu'elle avait : sans cette note le journal garde l'ancien et le
            # frein refuse la session pour toujours. Un geste explicite vaut reprise du bail.
            self._note_session_lease(task, session_id)
        self.project_board()
        return task

    def attach_current_session(self, task_id: str, *, actor: str, env: Mapping[str, str] | None = None) -> str:
        """Rattache la session que l'environnement désigne au claim de *task_id* (issue #680).

        Pour un claim lancé depuis la session (CLI dans Bash, outil MCP) : le
        hook ne le verra qu'au tour suivant, et pas du tout s'il y a plusieurs
        claims. Rend le ``session_id`` rattaché, ``""`` quand l'environnement
        n'en désigne pas ou que le claim appartient déjà à une autre session —
        jamais une erreur : le claim, lui, a réussi.
        """
        session_id, session_host = current_session(env)
        if not session_id:
            return ""
        from grimoire.hosts.host_version import VERSION_ENV, valid_version

        version = valid_version(str((os.environ if env is None else env).get(VERSION_ENV, "")))
        try:
            task = self.attach_session(task_id, session_id, session_host=session_host, actor=actor, host_version=version)
        except GrimoireMissionError:
            return ""
        self._note_session_lease(task, session_id)
        return session_id

    def _note_session_lease(self, task: MissionTask, session_id: str) -> None:
        """Le journal de la session apprend l'epoch qu'elle vient de réclamer (K3) : c'est ce que le frein compare.

        Un claim fait depuis la session (CLI, MCP) est un geste explicite de la
        session elle-même : il remplace l'epoch noté, y compris celui d'un bail perdu
        qu'elle reprend. Best-effort : un journal illisible laisse la session sans frein.
        """
        if task.claim is None or task.claim.session_id != session_id:
            return
        with contextlib.suppress(Exception):
            from grimoire.policies.session_state import note_session_task

            note_session_task(
                self.project_root, session_id, task.id, now_iso=datetime.now(UTC).isoformat(),
                lease_epoch=task.claim.lease_epoch,
            )

    def claim(
        self, task_id: str, actor: str, host: str = "local", files: tuple[str, ...] = ()
    ) -> TaskMove:
        """ready → claimed, si le gate `ready_to_in_progress` l'accorde et si aucun
        autre claim actif ne réserve déjà l'un de *files*."""
        # Le verrou de vague vit dans `transition` (cible CLAIMED) : la CLI, le MCP et le cockpit y passent tous.
        claim = TaskClaim.new(actor_id=actor, host_id=host, exclusive_files=files)
        return self.transition(task_id, TaskState.CLAIMED, actor, claim=claim)

    def renew(self, task_id: str, actor: str, *, epoch: int | None = None) -> MissionTask:
        """Renouvelle le bail de *task_id* (K2) — fichier de bail, et plafond dur s'il approche.

        Sans *epoch*, seul l'acteur du claim renouvelle, à l'epoch courant ; un
        autre acteur doit présenter l'epoch qu'il tient. Un epoch périmé est refusé.
        """
        task = self.require(task_id)
        if epoch is None:
            if task.claim is None or task.claim.actor_id != actor:
                holder = task.claim.actor_id if task.claim is not None else "personne"
                raise GrimoireMissionError(
                    f"Renouvellement refusé pour {task_id} : le claim est tenu par {holder}, pas par {actor} "
                    "— présente l'epoch que tu tiens (--epoch)"
                )
            epoch = task.claim.lease_epoch
        return self.ledger.renew_lease(task_id, epoch, actor_id=actor)

    def note_progress(self, task_id: str, text: str, actor: str) -> MissionTask:
        """Pose une note de progression sur une tâche tenue (:meth:`MissionLedger.note_progress`)."""
        self.require(task_id)
        self.ledger.note_progress(task_id, text, actor_id=actor)
        return self.require(task_id)

    def reap(self, *, apply: bool = True, actor: str = "tick", now: datetime | None = None) -> ReapReport:
        """Récolte des baux perdus (:meth:`MissionLedger.reap`), quota lu dans l'état des fournisseurs.

        K3 (issue #768) : un hôte est fencé selon la table ``hosts.capabilities``
        — par hôte *et* par version, depuis les seules fixtures de K0 — et non plus
        selon une déclaration de l'appelant. Tout autre hôte, ou une version
        inconnue, donne ``resume_unfenced`` : la question est posée, rien n'est relancé.

        K12 : un worktree de tâche en avance sur la remote (commits non poussés, arbre modifié, état
        illisible) donne ``resume_branch`` — le travail local est à l'humain, pas au prochain repreneur.

        K12-SC2-02 : les git de ``unpushed_work`` (jusqu'à 120 s) se jouent **avant** le verrou du journal,
        sur les baux perdus à cet instant, et sont gardés par (tâche, epoch). Sous le verrou, un bail dont
        l'epoch a changé depuis n'est pas jugé à ce tick (:class:`UnjudgedClaimError`) ; le suivant le jugera.
        """
        from grimoire.hosts.capabilities import claim_host_is_fenced
        from grimoire.missions.ledger_rules import UnjudgedClaimError
        from grimoire.missions.task_worktree import unpushed_work

        self.refresh()
        held = {(v.task_id, v.epoch): unpushed_work(self, v.task_id) for v in self.ledger.expired_claims(now)}

        def branch_hold(task: MissionTask) -> str:
            key = (task.id, task.claim.lease_epoch if task.claim is not None else -1)
            if key not in held:
                raise UnjudgedClaimError(task.id)
            return held[key]

        report = self.ledger.reap(
            quota_blocked=self._quota_blocked,
            quota_until=self._quota_until,
            actor_id=actor,
            apply=apply,
            now=now,
            is_fenced=lambda view: claim_host_is_fenced(view.host, view.host_version),
            branch_hold=branch_hold,
        )
        if any(d.written and d.action == "ready" for d in report.decisions):
            self.project_board()
        return report

    # ── Attente nommée (K6, issue #771) ────────────────────────────────────

    def _held_epoch(self, task: MissionTask, actor: str, epoch: int | None) -> int | None:
        """L'epoch qui signe l'effet : celui présenté, sinon celui du claim de *actor* ; ``None`` pour un tiers (tick, flow)."""
        if epoch is not None:
            return epoch
        claim = task.claim
        if claim is not None and claim.actor_id == actor and claim.lease_epoch > 0:
            return claim.lease_epoch
        return None

    def _hint_of(self, task: MissionTask, *, observe_head: bool = False) -> ResumeHint | None:
        """De quoi reprendre la session ; avec *observe_head* (point de reprise d'une pause, K11), la branche et le HEAD du worktree."""
        from grimoire.missions.session_link import session_fields

        fields = session_fields(task)
        branch, head = self._observed_head() if observe_head else ("", "")
        if not fields["session_id"] and not head:
            return None
        return ResumeHint(
            session_id=fields["session_id"], host=fields["host"], branch=branch, observed_head=head,
            resume_command=fields["resume_command"],
        )

    def _observed_head(self) -> tuple[str, str]:
        """``(branche, HEAD)`` du worktree de ce service ; ``("", "")`` hors d'un dépôt git ou si git ne répond pas."""
        import subprocess

        def git(*args: str) -> str:
            try:
                done = subprocess.run(  # git, arguments fixes, dossier du projet
                    ["git", *args], cwd=self.project_root, capture_output=True, text=True, encoding="utf-8",
                    timeout=10, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return ""
            return done.stdout.strip() if done.returncode == 0 else ""

        return git("rev-parse", "--abbrev-ref", "HEAD"), git("rev-parse", "HEAD")

    def wait(
        self, task_id: str, reason: str, actor: str, *, until: str = "", note: str = "", epoch: int | None = None
    ) -> TaskMove:
        """``claimed|running → waiting(ci|quota|paused)`` — l'agent se gare avec une raison nommée.

        ``human`` passe par :meth:`ask` : une décision attendue porte toujours une question.
        *until* : une date ISO 8601, ``unknown`` (défaut pour ``quota`` et ``paused``) ou rien
        pour ``ci`` (la fin des checks est observée par le tick).
        """
        if reason not in WAIT_REASONS or reason == "human":
            raise ValueError(
                f"raison d'attente inconnue pour `task wait` : {reason!r} — parmi "
                f"{', '.join(r for r in WAIT_REASONS if r != 'human')} ; une attente humaine se pose par `task ask`"
            )
        task = self.require(task_id)
        moment = datetime.now(UTC).isoformat()
        waiting = Waiting(
            reason, until or (UNTIL_UNKNOWN if reason in ("quota", "paused") else ""), None,
            self._hint_of(task, observe_head=reason == "paused"), moment,
        )
        if problem := waiting.problem():
            raise ValueError(problem)
        return self.transition(
            task_id, TaskState.WAITING, actor, note or f"en attente ({reason})",
            extra_payload={"waiting": waiting.to_dict()}, lease_epoch=self._held_epoch(task, actor, epoch),
        )

    def ask(
        self,
        task_id: str,
        text: str,
        actor: str,
        *,
        kind: str = "choice",
        options: tuple[str, ...] = (),
        origin: str = "agent",
        epoch: int | None = None,
        question_id: str = "",
    ) -> TaskMove:
        """``claimed|running → waiting(human)`` avec sa question, tracée par ``task.question_asked``.

        La tâche se gare ; la question remonte au cockpit (« Décisions en attente »), à
        ``grimoire status`` et à la notification. Seul le canal humain y répond
        (:meth:`answer`) : aucune échéance ne décide à la place de l'humain.
        """
        task = self.require(task_id)
        moment = datetime.now(UTC).isoformat()
        question = Question(question_id or new_question_id(), kind, text.strip(), tuple(options), origin, moment)
        waiting = Waiting("human", "", question, self._hint_of(task), moment)
        if problem := waiting.problem():
            raise ValueError(problem)
        move = self.transition(
            task_id, TaskState.WAITING, actor, f"question : {question.text}",
            extra_payload={"waiting": waiting.to_dict()}, lease_epoch=self._held_epoch(task, actor, epoch),
        )
        self.ledger.append_event(
            QUESTION_ASKED_EVENT, task_id, "task", actor,
            {"task_id": task_id, "reason": "human", "question": question.to_dict(), "actor_id": actor},
        )
        return move

    def resume(self, task_id: str, actor: str, *, epoch: int | None = None) -> TaskMove:
        """``waiting → running`` — la même session, au même epoch : reprise sur place.

        Un bail rendu entre-temps (réponse reçue, tick) refuse : la tâche est ``ready``,
        on la réclame de nouveau (``task claim``).
        """
        task = self.require(task_id)
        signed = self._held_epoch(task, actor, epoch)
        if signed is None:
            raise GrimoireMissionError(
                f"Reprise refusée pour {task_id} : aucun epoch à présenter — `--epoch` (celui du claim de {actor})"
            )
        return self.transition(task_id, TaskState.RUNNING, actor, "reprise sur place", lease_epoch=signed)

    def answer(
        self, task_id: str, text: str, actor: str, *, origin: str, question_id: str = "",
        attested_by_flow: bool = False,
    ) -> TaskMove:
        """Répond à la question en attente — **canal humain seulement** — puis réveille la tâche.

        *origin* est posé par le canal d'appel (cockpit, CLI interactive), jamais par un
        champ que l'appelant remplit : un agent ne répond pas à sa propre attente
        (``test_agent_cannot_answer_own_await``). La réponse est écrite d'abord
        (``task.human_answered``, qui remet le compteur de tentatives à zéro) ; le réveil
        suit, et le tick le rejoue si le processus s'interrompt entre les deux. La réponse
        est aussi remise à la prochaine session de la tâche comme consigne humaine.
        ``abandonner`` (quand c'est une option) annule la tâche au lieu de la rendre.

        Une question posée par un checkpoint de flow (``origin == "flow"``) ne se tranche pas ici : la
        décision doit être attestée dans le flux (W2-01), et c'est le flux qui répond, avec
        *attested_by_flow*. Répondre à sa place réveillerait la tâche sans rien décider.
        """
        if origin != ORIGIN_HUMAN:
            raise GrimoireMissionError(
                f"Réponse refusée pour {task_id} : origine {origin!r}. Seul le canal humain (cockpit, CLI interactive) "
                "répond à une question — un agent ne tranche pas ce qu'il a demandé"
            )
        task = self.require(task_id)
        waiting = task.waiting
        if task.status is not TaskState.WAITING or waiting is None or waiting.question is None:
            raise GrimoireMissionError(f"{task_id} n'attend aucune réponse (état {task.status.value})")
        question = waiting.question
        if question_id and question_id != question.id:
            raise GrimoireMissionError(
                f"Question {question_id} inconnue pour {task_id} : la question en attente est {question.id}"
            )
        if question.origin == "flow" and not attested_by_flow:
            raise GrimoireMissionError(
                f"Réponse refusée pour {task_id} : la question {question.id} vient d'un checkpoint de flow, dont la "
                "décision s'atteste dans le flux (un tour humain de la session qui l'a présenté, puis la même "
                "décision renvoyée). Répondre ici réveillerait la tâche sans décider le checkpoint"
            )
        answer = text.strip()
        if not answer:
            raise ValueError("`text` requis : une réponse vide ne tranche rien")
        self.ledger.append_event(
            HUMAN_ANSWERED_EVENT, task_id, "task", actor,
            {"task_id": task_id, "question_id": question.id, "answer": answer, "origin": origin, "actor_id": actor},
        )
        self.ledger.add_directive(
            task_id, f"Réponse à « {question.text} » : {answer}", actor_id=actor, kind="directive", origin=origin
        )
        return self._settle_answer(task, question, answer, actor, in_place=attested_by_flow)

    def _settle_answer(
        self, task: MissionTask, question: Question, answer: str, actor: str, *, in_place: bool = False
    ) -> TaskMove:
        if answer.lower() == ABANDON and ABANDON in (o.lower() for o in question.options):
            return self.cancel(task.id, f"abandon sur réponse humaine à {question.id}", actor, force=True)
        woken = None
        if in_place and task.claim is not None and not task.claim.is_expired():
            # K6-S2-02 : le run lié continue chez son titulaire — la décision attestée le reprend sur
            # place (même epoch), sans rendre le bail qu'un autre worker pourrait prendre.
            with contextlib.suppress(GrimoireMissionError):
                woken = self.ledger.resume_attested(
                    task.id, actor_id=actor, reason=f"décision attestée à {question.id}"
                )
        if woken is None:
            woken = self.ledger.wake(
                task.id, actor_id=actor, reason=f"réponse humaine à {question.id}", cause="answered"
            )
        return TaskMove(
            task=woken, previous=TaskState.WAITING, verdict=GateVerdict("", "", ()), board_path=self.project_board()
        )

    def release_flow_question(self, task_id: str, question_ids: Collection[str], actor: str, *, cause: str) -> bool:
        """Lève la question de checkpoint d'un run qui ne la tranchera plus (K6-S2-01), sans fabriquer de réponse.

        Un run abandonné ne présentera plus son checkpoint : la question ne peut plus être attestée dans le
        flux, et le canal humain la refuse (``origin == flow``). La tâche se réveille donc en ``ready`` avec
        *cause* tracée sur la transition, aucune ``task.human_answered`` n'étant écrite. Une autre question
        (posée par ``task ask``, ou par un autre run) n'est pas touchée.
        """
        task = self.require(task_id)
        waiting = task.waiting
        if task.status is not TaskState.WAITING or waiting is None or waiting.question is None:
            return False
        if waiting.question.origin != "flow" or waiting.question.id not in question_ids:
            return False
        self.ledger.wake(task_id, actor_id=actor, reason=f"question {waiting.question.id} levée ({cause})", cause=cause)
        self.project_board()
        return True

    def wake_due(
        self, *, apply: bool = True, now: datetime | None = None, pause: PauseState | None = None
    ) -> list[WakeDecision]:
        """La part « attente » du tick : rend les tâches dont l'attente est levée — jamais une ``waiting(human)`` au minuteur.

        - ``human`` : seulement si une réponse humaine est écrite (réveil interrompu rejoué) ;
        - ``ci``, ``quota`` : à l'échéance ``until`` ; ``unknown`` ou absente, la tâche attend (une sonde
          explicite est l'affaire d'un lot suivant) ;
        - ``paused`` (K11) : tant qu'un drapeau de pause la couvre, elle attend. Une fois la pause levée, elle est
          rendue ``ready`` (échéance ``unknown`` ou absente : tout de suite ; datée : à l'échéance), **dans l'ordre
          de la file** (priorité, ancienneté d'avant son claim) et sans perdre cette ancienneté.

        *pause* : les drapeaux déjà lus par l'appelant (le tick) ; sinon ils sont relus ici.
        """
        moment = now or datetime.now(UTC)
        state = pause if pause is not None else read_pause(self.home_root)
        planned: list[tuple[MissionTask, Waiting, str, str]] = []
        for task in self.ledger.list_tasks():
            waiting = task.waiting
            if task.status is not TaskState.WAITING or waiting is None:
                continue
            action, cause = "keep", "attend"
            if waiting.reason == "human":
                answered = self._answer_of(task, waiting)
                if answered is not None:
                    action, cause = "wake", "answered"
            elif waiting.reason == "paused" and state.blocks_admission(task.id):
                cause = f"pause en cours ({state.mode_for(task.id)})"
            else:
                due = parse_until(waiting.until)
                if waiting.reason == "paused" and due is None and waiting.until in ("", UNTIL_UNKNOWN):
                    action, cause = "wake", PAUSE_LIFTED_CAUSE
                elif due is not None and moment >= due:
                    action, cause = "wake", f"échéance {waiting.reason} atteinte"
            planned.append((task, waiting, action, cause))
        written: set[str] = set()
        if apply:
            for task, waiting, action, cause in self._write_order(planned):
                if action != "wake":
                    continue
                if waiting.reason == "human" and waiting.question is not None:
                    self._settle_answer(task, waiting.question, self._answer_of(task, waiting) or "", "tick")
                else:
                    self.ledger.wake(task.id, actor_id="tick", cause=cause)
                written.add(task.id)
            if written:
                self.project_board()
        return [WakeDecision(t.id, w.reason, a, c, t.id in written) for t, w, a, c in planned]

    def _write_order(
        self, planned: list[tuple[MissionTask, Waiting, str, str]]
    ) -> list[tuple[MissionTask, Waiting, str, str]]:
        """Les réveils d'une pause levée passent après les autres, dans l'ordre de la file (K11)."""
        lifted = [item for item in planned if item[2] == "wake" and item[3] == PAUSE_LIFTED_CAUSE]
        if len(lifted) < 2:
            return planned
        capacity = load_capacity(self.home_root)
        ranked = queue_order(
            [t for t, _, _, _ in lifted], ready_since(self.ledger), now=datetime.now(UTC), aging_hours=capacity.aging_hours
        )
        position = {e.task.id: i for i, e in enumerate(ranked)}
        rest = [item for item in planned if item not in lifted]
        return [*rest, *sorted(lifted, key=lambda item: position[item[0].id])]

    def _answer_of(self, task: MissionTask, waiting: Waiting) -> str | None:
        if waiting.question is None:
            return None
        for event in reversed(self.ledger.list_events(task.id)):
            if (
                event.event_type == HUMAN_ANSWERED_EVENT
                and event.payload.get("question_id") == waiting.question.id
                and event.payload.get("origin") == ORIGIN_HUMAN
            ):
                return str(event.payload.get("answer", ""))
        return None

    def pending_decisions(self) -> list[Any]:
        from grimoire.missions.decisions import pending_decisions

        return pending_decisions(self.ledger)

    def _quota_blocked(self, task: MissionTask) -> bool:
        """La dernière route de *task* bute sur un quota : verdict ``rate_limit``, ou fournisseur refroidi pour ce motif."""
        dispatched = [e for e in self.ledger.list_events(task.id) if e.event_type == "task.dispatched"]
        if not dispatched:
            return False
        last = dispatched[-1].payload
        if last.get("verdict") == "rate_limit":
            return True
        from grimoire.providers.state import load_state

        entry = load_state(self.project_root).get(str(last.get("provider", "")))
        return (
            entry is not None
            and entry.last_failure == "rate_limit"
            and entry.is_cooling_down(now=datetime.now(UTC))
        )

    def _quota_until(self, task: MissionTask) -> datetime | None:
        """Quand le fournisseur de la dernière route de *task* rouvre (K6) ; ``None`` si l'état ne le dit pas.

        La suspension ou le refroidissement encore actifs, le plus lointain : c'est l'échéance
        de ``waiting(quota)`` que le tick (:meth:`wake_due`) lève. Un état sans date (rien
        d'actif) laisse la récolte retomber sur son délai de reprise.
        """
        dispatched = [e for e in self.ledger.list_events(task.id) if e.event_type == "task.dispatched"]
        if not dispatched:
            return None
        from grimoire.providers.state import load_state

        provider = str(dispatched[-1].payload.get("provider", ""))
        entry = load_state(self.project_root).get(provider)
        if entry is None:
            return None
        now = datetime.now(UTC)
        dates = [entry.cooldown_until] if entry.is_cooling_down(now=now) else []
        if entry.is_suspended(now=now) and entry.suspension is not None:
            dates.append(entry.suspension.until)
        known = [d for d in dates if d is not None]
        return max(known) if known else None

    def transition(
        self,
        task_id: str,
        target: TaskState,
        actor: str,
        reason: str = "",
        *,
        claim: TaskClaim | None = None,
        extra_payload: dict[str, Any] | None = None,
        lease_epoch: int | None = None,
    ) -> TaskMove:
        """Déplace une tâche : machine à états, verrou de fichiers, gate, puis ledger, puis board.

        *lease_epoch* (K2/K6) signe l'effet d'un titulaire : un epoch qui n'est plus celui du bail est refusé.

        Lève :class:`TaskRefusedError` sur un verrou de fichiers ou un gate bloquant,
        :class:`GrimoireMissionError` sur une tâche inconnue ou une transition que la
        machine à états refuse. Dans tous les cas, rien n'a été écrit.
        """
        task = self.require(task_id)
        if target is TaskState.CLAIMED:
            # W5-05 : point de passage commun de `claim`, de `task claim`, de `task_update move` et de la route
            # workspace `move` — un checkpoint de vague requis ferme le claim quel que soit le chemin.
            from grimoire.missions.wave_checkpoint import claim_refusal

            refusal = claim_refusal(self, task_id)
            if refusal:
                raise GrimoireMissionError(f"Claim refusé pour {task_id} : {refusal}")
        if claim is not None:
            self._check_exclusive_files(task, target, claim)
        verdict = self.gate(task, target)
        if verdict.blocked:
            self._record_refusal(task, target, verdict, actor)
            raise TaskRefusedError(task_id, verdict)
        # `expected_from` : le gate a jugé `task.status` ; si l'état a bougé
        # depuis (autre session, autre processus), le ledger refuse sous son
        # verrou au lieu d'écrire une transition jugée sur un état périmé (#710).
        moved = self.ledger.transition_task(
            task_id,
            target,
            actor_id=actor,
            reason=reason,
            claim=claim,
            extra_payload=extra_payload,
            expected_from=task.status,
            lease_epoch=lease_epoch,
        )
        self._consolidate(moved, target, actor=actor, reason=reason)
        return TaskMove(task=moved, previous=task.status, verdict=verdict, board_path=self.project_board())

    # ── Pilotage humain (issue #638, lot B) ────────────────────────────────
    #
    # L'orchestrateur humain agit depuis le cockpit, le CLI ou le MCP — les
    # trois appellent ces quatre gestes, jamais le ledger directement (ADR-007).

    def prioritize(self, task_id: str, priority: str, actor: str, reason: str = "") -> TaskNote:
        """Change la priorité déclarée d'une tâche ; l'échelle est :data:`PRIORITIES`."""
        if priority not in PRIORITIES:
            raise ValueError(f"priorité inconnue : {priority!r} — parmi {', '.join(PRIORITIES)}")
        self.require(task_id)
        task = self.ledger.prioritize_task(task_id, priority, actor_id=actor, reason=reason)
        return TaskNote(task=task, change=f"priority → {priority}", directive=None, board_path=self.project_board())

    def comment(
        self, task_id: str, text: str, actor: str, kind: str = "comment", *, origin: str = ORIGIN_AGENT
    ) -> TaskNote:
        """Pose un commentaire ou une consigne — livrée à la session au tour suivant.

        *origin* est posé par le canal d'appel (cockpit et CLI interactive :
        ``human`` ; MCP, hooks, sous-agents : ``agent``), jamais par un champ
        que l'appelant remplit (issue #706). Seule l'origine ``human`` peut
        poser une consigne ; le reste est rendu à la session comme note non fiable.
        """
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("`text` requis : une consigne vide n'a rien à dire")
        if kind not in DIRECTIVE_KINDS:
            raise ValueError(f"kind inconnu : {kind!r} — parmi {', '.join(DIRECTIVE_KINDS)}")
        self.require(task_id)
        directive = self.ledger.add_directive(task_id, cleaned, actor_id=actor, kind=kind, origin=origin)
        task = self.require(task_id)
        return TaskNote(task=task, change=f"{kind} ajouté", directive=directive, board_path=self.project_board())

    def acknowledge(self, task_id: str, directive_id: str, actor: str) -> TaskNote:
        """L'agent dit avoir lu la consigne ; horodaté, jamais effacé."""
        task = self.require(task_id)
        if not any(d.id == directive_id for d in task.directives):
            raise ValueError(f"consigne inconnue sur {task_id} : {directive_id!r}")
        updated = self.ledger.acknowledge_directive(task_id, directive_id, actor_id=actor)
        directive = next(d for d in updated.directives if d.id == directive_id)
        return TaskNote(task=updated, change="ack", directive=directive, board_path=self.project_board())

    def deliver_pending_directives(self, task_id: str, *, actor: str = "hook") -> tuple[TaskDirective, ...]:
        """Ce que le hook injecte — les consignes non livrées, marquées livrées dans le même geste.

        Rend un tuple vide sans rien écrire quand il n'y a rien à livrer :
        appelé à chaque ``UserPromptSubmit``, il ne doit coûter qu'une lecture.
        """
        task = self.ledger.get_task(task_id)
        if task is None:
            return ()
        pending = task.pending_directives
        if not pending:
            return ()
        self.ledger.mark_directives_delivered(task_id, tuple(d.id for d in pending), actor_id=actor)
        return pending

    def cancel(self, task_id: str, reason: str, actor: str, *, force: bool = False) -> TaskMove:
        """Annule une tâche : la raison est obligatoire, et une tâche qu'une autre
        session tient (claim actif d'un autre acteur) exige *force* explicite.

        Les deux refus sont des :class:`TaskRefusedError`, comme un gate rouge :
        le CLI, le MCP et le cockpit les rendent déjà en nommant le remède.
        """
        task = self.require(task_id)
        cleaned = reason.strip()
        if not cleaned:
            refusal = GateRefusal(
                evidence="reason",
                reason="annuler sans raison n'est pas permis — la carte doit dire pourquoi elle s'arrête",
                remedy="donner une raison (--reason, champ `reason`)",
            )
            verdict = GateVerdict(transition_id="cancel", strictness="hard_fail", refusals=(refusal,))
            self._record_refusal(task, TaskState.CANCELLED, verdict, actor)
            raise TaskRefusedError(task_id, verdict)
        held = task.claim
        held_by_other = (
            task.status in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING)  # waiting garde le claim
            and held is not None
            and not held.is_expired()
            and held.actor_id != actor
        )
        if held_by_other and not force:
            assert held is not None
            session = str(getattr(held, "session_id", "") or "")
            who = f"{held.actor_id}" + (f" (session {session})" if session else "")
            refusal = GateRefusal(
                evidence="claim",
                reason=f"tâche {task.status.value}, réclamée par {who} jusqu'à {held.expires_at or 'sans expiration'}",
                remedy="laisser la session conclure, ou forcer explicitement (--force) avec la raison",
            )
            verdict = GateVerdict(transition_id="cancel", strictness="hard_fail", refusals=(refusal,))
            self._record_refusal(task, TaskState.CANCELLED, verdict, actor)
            raise TaskRefusedError(task_id, verdict)
        return self.transition(
            task_id, TaskState.CANCELLED, actor, cleaned,
            extra_payload={"forced": bool(force and held_by_other), "cancelled_by": actor},
        )

    def _check_exclusive_files(self, task: MissionTask, target: TaskState, claim: TaskClaim) -> None:
        """Refuse un claim dont un fichier exclusif est déjà réservé ailleurs.

        Une autre tâche CLAIMED ou RUNNING (« in_progress » au board) dont le
        claim n'a pas expiré et réserve l'un des mêmes fichiers bloque celui-ci —
        deux agents n'écrivent jamais le même fichier en même temps. Un claim
        expiré ne bloque plus personne : le temps a suffi, sans geste humain.
        """
        if not claim.exclusive_files:
            return
        requested = set(claim.exclusive_files)
        for other in self.ledger.list_tasks():
            if other.id == task.id or other.status not in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING):
                continue
            held = other.claim
            # Une tâche en attente (K6) garde ses fichiers : elle reprend sur place, son bail n'est plus jugé.
            parked = other.status is TaskState.WAITING
            expired = held.is_expired() if held is not None and not parked else False
            if held is None or not held.exclusive_files or expired:
                continue
            overlap = requested & set(held.exclusive_files)
            if not overlap:
                continue
            fichier = min(overlap)
            if parked:
                # Pas la date d'expiration du claim (périmée, sans effet ici) : ce qui rend les fichiers.
                due = other.waiting.until if other.waiting is not None else ""
                reason, remedy = (
                    f"réservé par {other.id} ({held.actor_id}), en attente"
                    + (f" ({other.waiting.reason})" if other.waiting is not None else "")
                    + (f" jusqu'à {due}" if due and due != "unknown" else ""),
                    "répondre à sa question (`grimoire task answer`) ou attendre son réveil (`grimoire task wake`), "
                    "ou réclamer une autre tâche",
                )
            else:
                reason = f"réservé par {other.id} ({held.actor_id}) jusqu'à {held.expires_at or 'sans expiration'}"
                remedy = "attendre l'expiration du claim ou réclamer une autre tâche"
            refusal = GateRefusal(evidence=fichier, reason=reason, remedy=remedy)
            verdict = GateVerdict(transition_id="exclusive_files", strictness="hard_fail", refusals=(refusal,))
            self._record_refusal(task, target, verdict, claim.actor_id)
            raise TaskRefusedError(task.id, verdict)

    def _consolidate(self, task: MissionTask, target: TaskState, *, actor: str, reason: str) -> None:
        """Clôture ou blocage : ce que la roadmap Memory OS autorise à écrire.

        Rien d'autre n'appelle :func:`consolidate_task_memory` — c'est le seul
        point d'écriture, comme :meth:`project_board` l'est pour le board.
        Best-effort : la transition est déjà au ledger, qui est la source ;
        une mémoire indisponible n'y change rien.
        """
        if target not in (TaskState.CLOSED, TaskState.BLOCKED, TaskState.FAILED) or not self.writes_projections:
            return
        # best-effort : la transition est déjà au ledger, qui est la source ;
        # une mémoire indisponible n'y change rien.
        with contextlib.suppress(Exception):
            consolidate_task_memory(self.memory, self.ledger, task, target, actor=actor, reason=reason)

    def _record_refusal(self, task: MissionTask, target: TaskState, verdict: GateVerdict, actor: str) -> None:
        """Journaliser un gate rouge dans le TraceLedger — le journal d'observabilité, pas la source.

        Le Mission Ledger ne reçoit rien : un refus n'est pas un changement
        d'état. Mais `grimoire task trace` doit pouvoir montrer *pourquoi* une
        tâche n'a pas avancé, et c'est ici que le gateway de hooks écrit déjà
        ses refus. Best-effort : un journal inaccessible ne bloque pas le refus.
        """
        try:
            from grimoire.core.standard_generation import TRACES_DIR
            from grimoire.traces.ledger import TraceLedger
            from grimoire.traces.schemas import TraceOutcome

            TraceLedger(self.project_root / TRACES_DIR).record(
                run_id=f"task-gate-{task.id}",
                workflow_instance_id="",
                mission_id=task.mission_id,
                task_id=task.id,
                recipe_id="grimoire.task-gate",
                outcome=TraceOutcome.FAILURE,
                started_at=datetime.now(UTC).isoformat(),
                agent_id=actor,
                policy_verdicts=[
                    {
                        "verdict_id": refusal.evidence,
                        "action_kind": f"task.transition:{task.status.value}->{target.value}",
                        "verdict": "block",
                    }
                    for refusal in verdict.refusals
                ],
                error_count=len(verdict.refusals),
                tags=["task.gate", verdict.transition_id],
            )
        except Exception:  # noqa: S110 — observabilité : jamais au prix du refus lui-même
            pass

    def project_board(self) -> Path | None:
        """Reprojette le board du standard depuis le ledger, si le projet est enrôlé.

        Un projet sans ``_grimoire/standard/`` n'a pas de board à tenir à jour ;
        on n'en crée pas un pour lui. L'échec d'écriture n'annule pas la
        transition — elle est déjà au ledger, qui est la source — mais il ne
        doit pas non plus passer pour un succès : on rend ``None``.
        """
        if not self.writes_projections or not (self.home_root / STANDARD_DIR).is_dir():
            return None
        dest = self.home_root / BOARD_RELPATH
        try:
            write_board(dest, build_board(self.ledger, project=self.home_root.name))
        except (OSError, GrimoireError):
            return None
        # Le hook lit la tâche ``in_progress`` du board via un cache dérivé,
        # invalidé par empreinte fichier (grimoire.core.standard_state) ; sans
        # cet appel, la reprojection ne serait visible qu'au hook *suivant*.
        invalidate_cache(self.home_root)
        return dest

    # ── Contexte ───────────────────────────────────────────────────────────

    def context(self, task_id: str) -> StandardRuntimeArtifact:
        """Le context bundle d'une tâche réelle — une tâche inconnue est refusée avant tout calcul."""
        from grimoire.core.agentic_standard import build_context_bundle

        self.require(task_id)
        return build_context_bundle(self.project_root, task_id=task_id)

    def recall(self, task_id: str, *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> TaskRecall:
        """Ce que le projet sait de *task_id* et de ses voisines — le même rappel partout.

        Une tâche inconnue est refusée avant tout calcul, comme :meth:`context`.
        """
        self.require(task_id)
        return build_task_recall(
            self.project_root, task_id,
            ledger_root=self.ledger_root, memory_manager=self.memory, token_budget=token_budget,
        )
