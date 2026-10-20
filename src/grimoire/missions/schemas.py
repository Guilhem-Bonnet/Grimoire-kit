"""Mission and task dataclass schemas for the Grimoire Agent OS.

All identifiers follow the canonical formats defined in SCHEMAS-CONTRATS-cibles.md:
  Mission   → MIS-<slug>-<seq>
  Task      → GAO-<area>-<seq>
  Event     → evt-<ulid>
  Incident  → inc-<task>-<seq>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from grimoire.missions.questions import QuestionRecord
from grimoire.missions.waiting import Waiting

_log = logging.getLogger(__name__)

#: Durée par défaut avant qu'un claim n'expire de lui-même — assez pour une
#: session de travail (implémentation + tests + PR), pas assez pour qu'un
#: agent mort bloque un fichier indéfiniment.
DEFAULT_CLAIM_TTL_SECONDS = 4 * 60 * 60

#: Valeurs machine acceptées pour ``MissionTask.finition`` (lot 4.3, issue #561).
#: ``""`` = non déclaré. Sans accent : c'est une valeur machine, pas de la
#: prose — l'accent reste dans l'ADR et le CHANGELOG.
_FINITION_VALUES = frozenset({"", "maquette", "peaufine"})

#: Natures d'une consigne posée sur une tâche (lot B, issue #638). ``comment``
#: informe ; ``directive`` dirige — les deux sont livrées à la session au tour
#: suivant, seule la distinction change la formulation du rappel.
DIRECTIVE_KINDS: tuple[str, ...] = ("comment", "directive")
#: D'où vient une note, posé par le canal (jamais par l'appelant, issue #706) :
#: ``human`` (cockpit local, CLI interactive), ``agent`` (MCP, hooks, sous-agents),
#: ``unverified`` (événement antérieur au champ, ou terminal interactif sans
#: confirmation humaine : la provenance n'est pas établie).
ORIGIN_HUMAN = "human"
ORIGIN_AGENT = "agent"
ORIGIN_UNVERIFIED = "unverified"
DIRECTIVE_ORIGINS: tuple[str, ...] = (ORIGIN_HUMAN, ORIGIN_AGENT, ORIGIN_UNVERIFIED)


def _int_or_zero(value: Any) -> int:
    """Epoch lu d'un ledger : entier >= 0, sinon ``0`` (absent ou illisible, jamais d'exception).

    ``0`` n'est attribué à aucun claim posé par le ledger : un epoch illisible
    (texte, booléen, négatif) ne peut donc se réclamer d'aucun titulaire.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


class MissionState(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TaskState(StrEnum):
    PROPOSED = "proposed"
    READY = "ready"
    CLAIMED = "claimed"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_VERIFICATION = "needs_verification"
    FAILED = "failed"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    #: Une attente nommée (K6, issue #771) : ``MissionTask.waiting`` dit pourquoi
    #: (``human``, ``ci``, ``quota``, ``paused``), jusqu'à quand et, pour un humain,
    #: la question posée. Le claim est gardé (reprise sur place, même epoch) tant
    #: que personne n'a rendu le bail.
    WAITING = "waiting"
    #: Jamais écrit : ce que lit un lecteur plus ancien devant un état qu'il ne
    #: connaît pas (écrit par une version plus récente). La valeur brute reste
    #: dans ``MissionTask.raw_status`` ; aucune transition ne part de cet état
    #: ni n'y arrive. Épopée #705, lot K1.
    UNKNOWN = "unknown"


#: Les états qu'un opérateur peut viser (tout sauf le sentinelle de lecture).
TARGETABLE_TASK_STATES: tuple[TaskState, ...] = tuple(s for s in TaskState if s is not TaskState.UNKNOWN)


class TaskType(StrEnum):
    ANALYSIS = "analysis"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    TEST = "test"
    DOCUMENTATION = "documentation"
    MIGRATION = "migration"
    SECURITY = "security"
    OPERATION = "operation"
    CLEANUP = "cleanup"


class RiskProfile(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    STRICT = "strict"
    SECURITY_CRITICAL = "security_critical"
    RELEASE = "release"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"


class DependencyKind(StrEnum):
    BLOCKS = "blocks"
    RELATES = "relates"
    PARENT_CHILD = "parent_child"
    DISCOVERED_FROM = "discovered_from"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True, slots=True)
class TaskDependency:
    kind: DependencyKind
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "target": self.target}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskDependency:
        return cls(kind=DependencyKind(d["kind"]), target=d["target"])


@dataclass(frozen=True, slots=True)
class TaskClaim:
    actor_id: str
    host_id: str
    exclusive_files: tuple[str, ...] = ()
    #: ISO 8601 UTC. Chaîne vide = jamais expiré (claims historiques, tests).
    expires_at: str = ""
    #: La session d'hôte qui travaille sous ce claim (issue #638, lot A).
    #: L'agent qui réclame ne connaît pas son ``session_id`` — seul le hook le
    #: reçoit dans le payload d'hôte ; il le pose donc après coup, par un
    #: événement ``task.session_attached``, jamais en réécrivant le claim.
    #: ``""`` = aucune session rattachée (claim historique, ou pris hors hôte).
    session_id: str = ""
    #: L'identifiant d'hôte (:class:`grimoire.bridges.schemas.HostId`) de la
    #: session rattachée — distinct de ``host_id``, que l'agent déclare
    #: lui-même à la réclamation (« local », « mcp »…). C'est lui qui décide
    #: si une commande de reprise existe (``claude --resume``), jamais une
    #: valeur devinée.
    session_host: str = ""
    #: Version de l'hôte de la session rattachée (K3, issue #768), lue par le hook
    #: dans le journal de l'hôte — jamais devinée. Une preuve de fencing vaut pour
    #: la version sur laquelle elle a été capturée (``hosts.capabilities``) :
    #: ``""`` = version inconnue, donc hôte non fencé.
    session_host_version: str = ""
    #: Quand la session a *explicitement* déclaré ce claim sien (``grimoire task
    #: attach``, issue #695), ISO 8601 UTC du dernier tel rattachement. Les
    #: sous-agents héritent du ``session_id`` de leur parent : leurs claims s'y
    #: rattachent automatiquement ; seul ce marqueur dit laquelle des tâches
    #: d'une session est celle que l'opérateur a désignée. ``""`` = jamais.
    attached_explicit_at: str = ""
    #: Le sous-agent qui tient ce claim dans la session (issue #698) : l'``agent_id``
    #: que l'hôte met dans le payload des hooks tirés depuis un sous-agent (Claude
    #: Code : outil ``Agent``, champs ``agent_id``/``agent_type``). Les sous-agents
    #: héritent du ``session_id`` du parent et n'ont pas de ``GRIMOIRE_ACTOR`` propre ;
    #: seul le hook qui observe le ``task claim`` connaît cette identité et la pose,
    #: par le même événement ``task.session_attached``. ``""`` = claim de la boucle
    #: principale, ou pris hors d'un hôte qui nomme ses sous-agents.
    agent_id: str = ""
    #: Jeton de fencing du bail (K2, issue #767) : croît à chaque nouveau claim
    #: de la tâche, posé par le ledger sous son verrou — jamais par l'appelant.
    #: Un effet porteur d'un autre epoch vient d'un titulaire périmé et il est
    #: refusé. ``0`` = claim antérieur au fencing (aucun epoch à présenter).
    lease_epoch: int = 0

    @classmethod
    def new(
        cls,
        actor_id: str,
        host_id: str,
        exclusive_files: tuple[str, ...] = (),
        *,
        ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
        now: datetime | None = None,
    ) -> TaskClaim:
        """Le constructeur normal : un claim avec son expiration déjà calculée.

        Les trois points d'entrée qui créent un claim (`MissionLedger.claim_task`,
        `TaskService.claim`, `grimoire task claim`) passent par ici plutôt que par
        le constructeur brut, pour qu'aucun ne puisse oublier l'expiration.
        """
        moment = now or datetime.now(UTC)
        expires = moment + timedelta(seconds=ttl_seconds)
        return cls(
            actor_id=actor_id,
            host_id=host_id,
            exclusive_files=exclusive_files,
            expires_at=expires.isoformat(),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        """Un claim sans `expires_at` n'expire jamais — compat. des claims sans TTL.

        Un `expires_at` illisible (horodatage invalide) est traité comme
        *expiré* plutôt que comme non-expiré : un claim qu'on ne sait pas
        dater ne doit jamais bloquer un fichier indéfiniment ni silencieusement
        — l'anomalie est journalisée pour rester visible. Un ISO 8601 naïf
        (sans fuseau) est supposé UTC, comme tout ce que produit `TaskClaim.new`.
        """
        if not self.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            _log.warning("TaskClaim.expires_at invalide (%r) — claim traité comme expiré", self.expires_at)
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) >= expires

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "host_id": self.host_id,
            "exclusive_files": list(self.exclusive_files),
            "expires_at": self.expires_at,
            "session_id": self.session_id,
            "session_host": self.session_host,
            # Absent tant qu'aucune version n'est connue : sérialisation inchangée.
            **({"session_host_version": self.session_host_version} if self.session_host_version else {}),
            # Absent tant qu'aucun attach explicite : les sérialisations
            # existantes (board, cockpit, tests) ne changent pas.
            **({"attached_explicit_at": self.attached_explicit_at} if self.attached_explicit_at else {}),
            # Absent tant qu'aucun sous-agent n'est connu (issue #698).
            **({"agent_id": self.agent_id} if self.agent_id else {}),
            # Absent sur un claim d'avant le fencing : sa sérialisation ne change pas.
            **({"lease_epoch": self.lease_epoch} if self.lease_epoch else {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskClaim:
        return cls(
            actor_id=d["actor_id"],
            host_id=d["host_id"],
            exclusive_files=tuple(d.get("exclusive_files", [])),
            expires_at=d.get("expires_at", ""),
            session_id=str(d.get("session_id", "") or ""),
            session_host=str(d.get("session_host", "") or ""),
            session_host_version=str(d.get("session_host_version", "") or ""),
            attached_explicit_at=str(d.get("attached_explicit_at", "") or ""),
            agent_id=str(d.get("agent_id", "") or ""),
            lease_epoch=_int_or_zero(d.get("lease_epoch")),
        )

    def with_session(
        self,
        session_id: str,
        session_host: str = "",
        *,
        explicit_at: str = "",
        host_version: str = "",
        agent_id: str = "",
    ) -> TaskClaim:
        """Le même claim, rattaché à *session_id* — une valeur, jamais une mutation.

        *explicit_at* date un rattachement explicite (``task attach``) ; sans lui,
        un rattachement automatique conserve la date du dernier explicite.
        *host_version* (K3) : la version de l'hôte, ou celle déjà connue de la même
        session si l'événement n'en porte pas. *agent_id* (issue #698) : le
        sous-agent observé par le hook, ou celui déjà connu.
        """
        return TaskClaim(
            actor_id=self.actor_id,
            host_id=self.host_id,
            exclusive_files=self.exclusive_files,
            expires_at=self.expires_at,
            session_id=session_id,
            session_host=session_host,
            session_host_version=host_version or (self.session_host_version if session_id == self.session_id else ""),
            attached_explicit_at=explicit_at or self.attached_explicit_at,
            agent_id=agent_id or self.agent_id,
            lease_epoch=self.lease_epoch,
        )


@dataclass(frozen=True, slots=True)
class TaskCheck:
    """Une commande déclarée dont le code de sortie rend le verdict (issue #772, D9).

    C'est la seule chose qui autorise une tâche à être V0 : un critère écrit
    « les tests passent » n'exécute rien. ``cmd`` est lancée par
    ``grimoire task verify`` dans la racine du projet, comme le ``--check`` de
    ``task dispatch`` (même confiance : c'est l'auteur de la tâche qui l'écrit) ;
    ``expect_exit`` est le code attendu (0 par défaut) ; ``expect_stdout_contains``
    (sous-chaîne exigée de la sortie standard) et ``timeout_s`` (délai propre à la
    commande) reprennent ce que déclare une acceptance structurée de flow : sans
    eux, un exit 0 affichant « 3 FAILED » passerait pour un vert.
    """

    cmd: str
    expect_exit: int = 0
    expect_stdout_contains: str | None = None
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cmd, str) or not self.cmd.strip():
            raise ValueError("TaskCheck.cmd : commande vide")
        if isinstance(self.expect_exit, bool) or not isinstance(self.expect_exit, int):
            raise ValueError(f"TaskCheck.expect_exit : entier attendu, reçu {self.expect_exit!r}")
        if self.expect_stdout_contains is not None and not isinstance(self.expect_stdout_contains, str):
            raise ValueError(
                f"TaskCheck.expect_stdout_contains : chaîne attendue, reçu {self.expect_stdout_contains!r}"
            )
        if self.timeout_s is not None and (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not self.timeout_s > 0
        ):
            raise ValueError(f"TaskCheck.timeout_s : nombre strictement positif attendu, reçu {self.timeout_s!r}")

    def to_dict(self) -> dict[str, Any]:
        # Les champs optionnels ne sont écrits que renseignés : une ligne de
        # ledger antérieure garde sa forme et se relit à l'identique.
        data: dict[str, Any] = {"cmd": self.cmd, "expect_exit": self.expect_exit}
        if self.expect_stdout_contains is not None:
            data["expect_stdout_contains"] = self.expect_stdout_contains
        if self.timeout_s is not None:
            data["timeout_s"] = self.timeout_s
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskCheck:
        return cls(
            cmd=d["cmd"],
            expect_exit=d.get("expect_exit", 0),
            expect_stdout_contains=d.get("expect_stdout_contains"),
            timeout_s=d.get("timeout_s"),
        )


@dataclass(frozen=True, slots=True)
class TaskDirective:
    """Une consigne ou un commentaire posé par l'orchestrateur humain (issue #638).

    Journal append-only : une consigne ne se modifie jamais, elle se livre
    (``delivered_at`` — le hook l'a injectée dans la session) puis s'accuse
    (``acknowledged_at`` — l'agent a dit l'avoir lue). Chaque étape est un
    événement du ledger, jamais une réécriture de l'historique.
    """

    id: str
    author: str
    created_at: str
    text: str
    kind: str = "comment"
    delivered_at: str = ""
    acknowledged_at: str = ""
    origin: str = ORIGIN_AGENT
    """Le canal qui a posé la note. Seule l'origine ``human`` fait autorité ; le
    défaut est la plus prudente, et un événement sans champ est ``unverified``."""

    @property
    def is_human(self) -> bool:
        return self.origin == ORIGIN_HUMAN

    def __post_init__(self) -> None:
        if self.origin not in DIRECTIVE_ORIGINS:
            accepted = ", ".join(repr(v) for v in DIRECTIVE_ORIGINS)
            msg = f"origin invalide : {self.origin!r}. Valeurs acceptées : {accepted}."
            raise ValueError(msg)
        if self.kind not in DIRECTIVE_KINDS:
            accepted = ", ".join(repr(v) for v in DIRECTIVE_KINDS)
            msg = f"kind invalide : {self.kind!r}. Valeurs acceptées : {accepted}."
            raise ValueError(msg)

    @property
    def is_delivered(self) -> bool:
        return bool(self.delivered_at)

    @property
    def is_acknowledged(self) -> bool:
        return bool(self.acknowledged_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "created_at": self.created_at,
            "text": self.text,
            "kind": self.kind,
            "delivered_at": self.delivered_at,
            "acknowledged_at": self.acknowledged_at,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskDirective:
        return cls(
            id=d["id"],
            author=d.get("author", ""),
            created_at=d.get("created_at", ""),
            text=d.get("text", ""),
            kind=d.get("kind", "comment"),
            delivered_at=d.get("delivered_at", ""),
            acknowledged_at=d.get("acknowledged_at", ""),
            origin=d.get("origin", ORIGIN_UNVERIFIED),
        )


@dataclass(frozen=True, slots=True)
class Mission:
    id: str
    title: str
    status: MissionState
    origin: str
    created_at: str
    schema_version: str = "grimoire.mission.v1"
    description: str = ""
    risk_profile: RiskProfile = RiskProfile.STANDARD
    created_by: str = ""
    scope_repos: tuple[str, ...] = ()
    scope_surfaces: tuple[str, ...] = ()
    scope_packs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "origin": self.origin,
            "risk_profile": self.risk_profile.value,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "scope": {
                "repos": list(self.scope_repos),
                "surfaces": list(self.scope_surfaces),
                "packs": list(self.scope_packs),
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Mission:
        scope = d.get("scope", {})
        return cls(
            id=d["id"],
            title=d["title"],
            status=MissionState(d["status"]),
            origin=d["origin"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.mission.v1"),
            description=d.get("description", ""),
            risk_profile=RiskProfile(d.get("risk_profile", "standard")),
            created_by=d.get("created_by", ""),
            scope_repos=tuple(scope.get("repos", [])),
            scope_surfaces=tuple(scope.get("surfaces", [])),
            scope_packs=tuple(scope.get("packs", [])),
        )


@dataclass(frozen=True, slots=True)
class MissionTask:
    id: str
    mission_id: str
    title: str
    status: TaskState
    type: TaskType
    risk_profile: RiskProfile
    acceptance: tuple[str, ...]
    created_at: str
    schema_version: str = "grimoire.mission_task.v1"
    description: str = ""
    surface: str = ""
    owner: str = ""
    claim: TaskClaim | None = None
    dependencies: tuple[TaskDependency, ...] = ()
    guardrails: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()
    #: Terrain du lot 4.3 (issue #561) : aucune interface ne le lit encore.
    finition: str = ""
    #: Priorité déclarée côté board (ADR-007, issue #580/#587) — ``""`` =
    #: dérivée de ``risk_profile`` par ``board.py``, jamais fixée ici. Sans ce
    #: champ, `task_unification.py` n'avait nulle part où ranger la priorité
    #: d'un board scaffolded ; la migration retombait donc toujours sur le
    #: défaut dérivé du `risk_profile` par défaut (medium), quelle que soit la
    #: priorité réelle saisie à la main.
    priority: str = ""
    #: Rôles agent déclarés côté board (ADR-007) — ``()`` = dérivé de `type`
    #: par ``board.py`` (une seule valeur). Un board peut lister plusieurs
    #: rôles (« orchestrator », « reviewer », ...) qu'aucun `TaskType` unique
    #: ne représente ; ce champ les porte tels quels.
    agent_roles: tuple[str, ...] = ()
    #: Référence de remédiation explicite (ADR-007) — ``board.py`` n'en déduit
    #: une par défaut que pour une carte à l'état « blocked ». Une tâche
    #: migrée dans un autre état (ex. « accepted ») qui en portait une
    #: explicitement la perdait silencieusement sans ce champ.
    remediation_ref: str = ""
    #: Champs de board que ce schéma ne modélise pas encore (ADR-007) —
    #: reportés tels quels d'une projection à l'autre. Seul filet qui empêche
    #: `migrate_standard_tasks` de jeter silencieusement une clé inconnue
    #: (ex. ``labels``) au lieu de simplement ne pas encore savoir l'exploiter.
    extra: dict[str, Any] = field(default_factory=dict)
    #: Consignes et commentaires de l'orchestrateur humain (issue #638, lot B),
    #: dans l'ordre d'ajout. Alimenté par ``task.directive_added`` et mis à jour
    #: par ``task.directive_delivered`` / ``task.directive_acknowledged`` au
    #: rejeu du ledger — jamais posé à la création.
    directives: tuple[TaskDirective, ...] = ()
    #: Risques acceptés en l'état (W2-10) : un ``accept_as_is`` de checkpoint de
    #: flow lié à cette tâche. Alimenté au rejeu par ``task.risk_accepted``,
    #: jamais posé à la création. ``classify`` le lit : c'est ce qui fait de la
    #: remontée V0 -> V1 un fait de la tâche, vu par toute surface qui décide,
    #: et non un affichage propre à une commande.
    accepted_risks: tuple[dict[str, Any], ...] = ()
    #: Valeur brute de ``status`` quand ce lecteur ne la connaît pas
    #: (``status is TaskState.UNKNOWN``) ; ``""`` sinon. Réécrite telle quelle
    #: par ``to_dict`` : relire puis réécrire ne perd pas l'état d'origine.
    raw_status: str = ""
    #: L'attente nommée quand ``status is TaskState.WAITING`` (K6) ; ``None`` sinon.
    #: Posée par la transition vers ``waiting``, effacée par toute autre.
    waiting: Waiting | None = None

    @property
    def is_unknown_state(self) -> bool:
        return self.status is TaskState.UNKNOWN

    @property
    def status_label(self) -> str:
        """L'état à afficher : la valeur brute inconnue, jamais un état voisin."""
        if self.status is TaskState.UNKNOWN:
            return f"inconnu ({self.raw_status})" if self.raw_status else "inconnu"
        return self.status.value

    #: Commandes déclarées dont le code de sortie rend le verdict (issue #772).
    #: Sans elles, ``classify`` ne rend jamais V0 : un vocabulaire mécanique
    #: sans commande à lancer n'est qu'une promesse.
    checks: tuple[TaskCheck, ...] = ()
    #: Profondeur de test exigée à la fermeture (W6-01) : ``floor``, ``standard``
    #: ou ``strict`` ; ``""`` = aucune exigence. Gardée telle quelle, jamais
    #: validée à la lecture : une valeur inconnue (écrite par une version plus
    #: récente) se relit, et c'est le gate qui la refuse.
    test_depth: str = ""
    #: Les questions posées sur la tâche et leurs réponses (W6-07, issue #564), dans l'ordre. Rejouées
    #: depuis ``task.question_asked``, ``task.human_answered`` et ``task.question_resolved`` — jamais
    #: posées à la création. Le statut se lit par :mod:`grimoire.missions.questions`.
    questions: tuple[QuestionRecord, ...] = ()

    @property
    def pending_directives(self) -> tuple[TaskDirective, ...]:
        """Celles que la session n'a pas encore reçues."""
        return tuple(d for d in self.directives if not d.is_delivered)

    @property
    def unacknowledged_directives(self) -> tuple[TaskDirective, ...]:
        """Celles dont aucun agent n'a accusé réception — livrées ou non."""
        return tuple(d for d in self.directives if not d.is_acknowledged)

    def __post_init__(self) -> None:
        if self.finition not in _FINITION_VALUES:
            accepted = ", ".join(repr(v) for v in sorted(_FINITION_VALUES))
            msg = f"finition invalide : {self.finition!r}. Valeurs acceptées : {accepted}."
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "title": self.title,
            "description": self.description,
            "status": self.raw_status if self.status is TaskState.UNKNOWN and self.raw_status else self.status.value,
            "type": self.type.value,
            "risk_profile": self.risk_profile.value,
            "surface": self.surface,
            "owner": self.owner,
            "acceptance": list(self.acceptance),
            "guardrails": list(self.guardrails),
            "expected_evidence": list(self.expected_evidence),
            "dependencies": [dep.to_dict() for dep in self.dependencies],
            "created_at": self.created_at,
        }
        if self.claim is not None:
            d["claim"] = self.claim.to_dict()
        if self.finition:
            d["finition"] = self.finition
        if self.priority:
            d["priority"] = self.priority
        if self.agent_roles:
            d["agent_roles"] = list(self.agent_roles)
        if self.remediation_ref:
            d["remediation_ref"] = self.remediation_ref
        if self.extra:
            d["extra"] = dict(self.extra)
        if self.directives:
            d["directives"] = [directive.to_dict() for directive in self.directives]
        if self.accepted_risks:
            d["accepted_risks"] = [dict(risk) for risk in self.accepted_risks]
        if self.checks:
            d["checks"] = [check.to_dict() for check in self.checks]
        if self.waiting is not None:
            d["waiting"] = self.waiting.to_dict()
        if self.test_depth:
            d["test_depth"] = self.test_depth
        if self.questions:
            d["questions"] = [record.to_dict() for record in self.questions]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MissionTask:
        claim = TaskClaim.from_dict(d["claim"]) if d.get("claim") else None
        raw_status = str(d["status"])
        try:
            status = TaskState(raw_status)
        except ValueError:
            # État écrit par une version plus récente : on le garde tel quel,
            # affiché « inconnu » et figé, au lieu de rendre le ledger illisible.
            status = TaskState.UNKNOWN
        return cls(
            id=d["id"],
            mission_id=d["mission_id"],
            title=d["title"],
            status=status,
            type=TaskType(d["type"]),
            risk_profile=RiskProfile(d["risk_profile"]),
            acceptance=tuple(d.get("acceptance", [])),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.mission_task.v1"),
            description=d.get("description", ""),
            surface=d.get("surface", ""),
            owner=d.get("owner", ""),
            claim=claim,
            dependencies=tuple(TaskDependency.from_dict(dep) for dep in d.get("dependencies", [])),
            guardrails=tuple(d.get("guardrails", [])),
            expected_evidence=tuple(d.get("expected_evidence", [])),
            finition=d.get("finition", ""),
            priority=d.get("priority", ""),
            agent_roles=tuple(d.get("agent_roles", [])),
            remediation_ref=d.get("remediation_ref", ""),
            extra=dict(d.get("extra", {})),
            directives=tuple(TaskDirective.from_dict(item) for item in d.get("directives", [])),
            accepted_risks=tuple(dict(risk) for risk in d.get("accepted_risks", [])),
            raw_status=raw_status if status is TaskState.UNKNOWN else "",
            checks=tuple(TaskCheck.from_dict(c) for c in d.get("checks", [])),
            waiting=Waiting.from_dict(d["waiting"]) if isinstance(d.get("waiting"), dict) else None,
            test_depth=str(d.get("test_depth", "") or ""),
            questions=tuple(QuestionRecord.from_dict(q) for q in d.get("questions") or () if isinstance(q, dict)),
        )


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    id: str
    event_type: str
    entity_id: str
    entity_kind: str
    actor_id: str
    created_at: str
    schema_version: str = "grimoire.ledger_event.v1"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LedgerEvent:
        return cls(
            id=d["id"],
            event_type=d["event_type"],
            entity_id=d["entity_id"],
            entity_kind=d["entity_kind"],
            actor_id=d["actor_id"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.ledger_event.v1"),
            payload=d.get("payload", {}),
        )


@dataclass(frozen=True, slots=True)
class Incident:
    id: str
    mission_id: str
    task_id: str
    kind: str
    severity: IncidentSeverity
    status: IncidentStatus
    summary: str
    created_at: str
    schema_version: str = "grimoire.incident.v1"
    workflow_instance_id: str = ""
    causes: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "workflow_instance_id": self.workflow_instance_id,
            "kind": self.kind,
            "severity": self.severity.value,
            "status": self.status.value,
            "summary": self.summary,
            "causes": list(self.causes),
            "recommended_actions": list(self.recommended_actions),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Incident:
        return cls(
            id=d["id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            kind=d["kind"],
            severity=IncidentSeverity(d["severity"]),
            status=IncidentStatus(d["status"]),
            summary=d["summary"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.incident.v1"),
            workflow_instance_id=d.get("workflow_instance_id", ""),
            causes=tuple(d.get("causes", [])),
            recommended_actions=tuple(d.get("recommended_actions", [])),
        )
