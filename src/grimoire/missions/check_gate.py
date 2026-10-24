"""Commande de vérification approuvée par empreinte, reçu d'exécution (W3-03).

Constat : ``_run_checks`` (:mod:`grimoire.missions.dispatch`) lance en
``shell=True`` toute commande que lui passent ``grimoire task verify``
(``task.checks``, écrites au ledger) et le dispatch de flow (acceptances d'un
blueprint). Rien ne dit qui a lu cette commande avant qu'elle ne tourne : un
agent qui réécrit ``task.checks`` ou le blueprint fait exécuter ce qu'il veut,
ou remplace la vérification par ``true`` et obtient un vert.

Ce module ferme le trou en profil ``governed`` et ``production`` (hors
enrôlement ou en ``starter``, le comportement historique reste, comme pour les
autres gardes de profil) :

- une commande ne tourne que si son empreinte (:func:`spec_sha256` : la
  commande exacte, au caractère près, et ce qu'elle doit rendre —
  ``expect_exit``, ``expect_stdout_contains``) figure dans le journal
  d'approbations ``check-approvals.authority.jsonl`` ; un octet modifié, une
  attente affaiblie ou ``true`` à la place changent l'empreinte, donc
  l'approbation ne vaut plus ;
- l'approbation vaut pour la tâche qui l'a reçue (``task_id`` du journal) : celle
  d'une tâche A ne vaut jamais pour une tâche B qui porte la même commande, et
  un lanceur sans tâche (``task_id`` vide) ne compte aucune approbation ;
- sans approbation, aucun sous-processus : le lot entier est refusé
  (``unapproved``) et un challenge W2-01 est émis pour la session, lié à
  l'ensemble exact des empreintes montrées (``approve-check:<tâche>:<sha>``) :
  un tour humain consommé pour approuver ``pytest`` n'approuve pas ``true`` ;
- ``grimoire task approve-check`` écrit ``{sha256, actor, ts}`` et classe
  l'acteur avec le mécanisme de W2-01 (:func:`grimoire.flows.attestation.attest`) :
  depuis une session d'hôte, un agent ou ``GRIMOIRE_UNATTENDED=1``, l'approbation
  exige un tour humain de la session postérieur au challenge ; hors session ni
  marqueur (terminal nu), l'approbation n'est que ``unattested`` et ne compte
  qu'avec ``allow_unattested`` explicite (``--allow-unattested``), journalisé ;
- les caractères de contrôle bidirectionnel et de largeur nulle (U+202E,
  U+200B…) sont refusés : une commande dont l'affichage ment ne s'approuve pas ;
- chaque exécution laisse un reçu ``{command_sha256, tree_fingerprint, exit}``
  (``check-receipts.authority.jsonl``) ; avec ``reuse=True`` (la clôture), un
  vert dont l'arbre est identique n'est pas relancé.

Les deux journaux portent le suffixe ``.authority.jsonl`` : la garde d'écriture
de :mod:`grimoire.hosts.decisions.authority_guard` les protège des Write, Edit
et redirections d'un agent.

Limite écrite (principe 7) : un agent qui exécute du code arbitraire peut
contourner une garde de chemin ; la détection de « session d'hôte » ou d'agent
lit l'environnement du processus appelant, donc un agent qui retire ses
variables se classe terminal nu : il n'obtient alors qu'une approbation
``unattested``, refusée sauf ``--allow-unattested`` (un opt-in visible dans sa
ligne de commande et dans le journal, pas une preuve d'humain) ; le reçu ne
protège pas d'une commande non déterministe (même arbre, autre résultat).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import atomic_write, locked
from grimoire.missions.dispatch import CheckResult, _run_checks

if TYPE_CHECKING:
    from grimoire.flows.attestation import Attestation
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "APPROVALS_RELPATH",
    "RECEIPTS_RELPATH",
    "UNAPPROVED",
    "ApprovalOutcome",
    "ApprovalRefusedError",
    "Blocked",
    "CheckGate",
    "CheckSpec",
    "UnapprovedChecksError",
    "approve_checks",
    "approve_checks_under_mandate",
    "command_sha256",
    "enforced",
    "hidden_characters",
    "spec_sha256",
    "specs_of_task",
    "visible",
]

RUNS_RELPATH = Path("_grimoire-output") / ".runs"
APPROVALS_RELPATH = RUNS_RELPATH / "check-approvals.authority.jsonl"
RECEIPTS_RELPATH = RUNS_RELPATH / "check-receipts.authority.jsonl"
#: Les flows partagent leur dossier de challenges et leur registre de tours consommés.
FLOWS_RELPATH = Path("_grimoire-runtime-output") / "flows"
#: Le « run » du journal de challenges d'approbation (un seul pour le projet).
_JOURNAL_RUN = "check-approval"

#: Le verdict d'un lot refusé faute d'approbation.
UNAPPROVED = "unapproved"
ENFORCED_PROFILES = frozenset({"governed", "production"})

#: Au-delà, le journal des reçus est réduit à ses dernières lignes.
_RECEIPTS_MAX_LINES = 1000
_RECEIPTS_KEEP_LINES = 500

#: Caractères qui font mentir l'affichage d'une commande (assemblés par code
#: point : aucun n'est écrit littéralement dans cette source).
_HIDDEN_RANGES: tuple[tuple[int, int], ...] = (
    (0x200B, 0x200F),  # largeur nulle, marques gauche-droite
    (0x202A, 0x202E),  # embeddings et overrides bidirectionnels (U+202E)
    (0x2060, 0x2064),  # word joiner et opérateurs invisibles
    (0x2066, 0x2069),  # isolats bidirectionnels
    (0x061C, 0x061C),  # marque de lettre arabe
    (0xFEFF, 0xFEFF),  # BOM / espace insécable de largeur nulle
)


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """Ce qu'on approuve : la commande ET ce qu'elle doit rendre."""

    cmd: str
    expect_exit: int = 0
    expect_stdout_contains: str | None = None
    timeout_s: float | None = None

    @property
    def sha256(self) -> str:
        return spec_sha256(self.cmd, self.expect_exit, self.expect_stdout_contains)


@dataclass(frozen=True, slots=True)
class Blocked:
    """Une commande qui ne peut pas tourner, et pourquoi."""

    index: int
    command_sha256: str
    reason: str
    display: str


class UnapprovedChecksError(Exception):
    """Au moins une commande n'est pas approuvée : rien n'a été lancé."""

    def __init__(self, task_id: str, blocked: Sequence[Blocked]) -> None:
        self.task_id = task_id
        self.blocked = tuple(blocked)
        super().__init__(
            f"{UNAPPROVED} : {len(self.blocked)} commande(s) non approuvée(s) pour {task_id or 'le lot'}, aucune lancée"
        )


class ApprovalRefusedError(Exception):
    """``approve-check`` refuse : ``code`` vaut ``hidden_characters`` ou ``awaiting_human``."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    approved: tuple[str, ...]
    attestation: str
    attestation_reason: str
    actor: str


def command_sha256(cmd: str) -> str:
    """sha256 de la commande exacte — un octet de différence, une empreinte différente."""
    return hashlib.sha256(cmd.encode("utf-8", "surrogatepass")).hexdigest()


def spec_sha256(cmd: str, expect_exit: int = 0, expect_stdout_contains: str | None = None) -> str:
    """Empreinte de ``(commande, code attendu, sous-chaîne attendue)`` : affaiblir l'attente invalide aussi."""
    payload = json.dumps([cmd, expect_exit, expect_stdout_contains], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def hidden_characters(cmd: str) -> tuple[str, ...]:
    """Les caractères de contrôle bidirectionnel ou de largeur nulle de *cmd* (``U+202E``…), sans doublon."""
    found: list[str] = []
    for char in cmd:
        code = ord(char)
        if any(low <= code <= high for low, high in _HIDDEN_RANGES):
            label = f"U+{code:04X}"
            if label not in found:
                found.append(label)
    return tuple(found)


def visible(cmd: str) -> str:
    """*cmd* tel qu'il doit s'afficher à qui approuve : chaque caractère caché devient ``<U+XXXX>``."""
    out: list[str] = []
    for char in cmd:
        code = ord(char)
        hidden = any(low <= code <= high for low, high in _HIDDEN_RANGES)
        out.append(f"<U+{code:04X}>" if hidden else char)
    return "".join(out)


def enforced(project_root: Path) -> bool:
    """``True`` en profil governed ou production d'un projet enrôlé ; un profil illisible vaut ``True`` (fail-closed)."""
    from grimoire.core.standard_state import active_profile_id, is_standard_enrolled

    root = project_root.resolve()
    try:
        return is_standard_enrolled(root) and active_profile_id(root, write_cache=False) in ENFORCED_PROFILES
    except Exception:  # une garde qui ne sait pas lire son profil refuse
        return True


def specs_of_task(task: MissionTask) -> tuple[CheckSpec, ...]:
    return tuple(CheckSpec(c.cmd, c.expect_exit, c.expect_stdout_contains, c.timeout_s) for c in task.checks)


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []  # silent-ok — journal absent ou illisible : rien d'approuvé, donc fail-closed
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue  # silent-ok — ligne corrompue : elle n'approuve rien
        if isinstance(data, dict):
            out.append(data)
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _approved_specs(project_root: Path, task_id: str) -> frozenset[str]:
    """Les empreintes approuvées POUR *task_id* (jamais celles d'une autre tâche).

    Une approbation compte si un humain l'a attestée ou si elle porte
    ``allow_unattested`` (opt-in explicite d'un terminal nu). Un *task_id* vide
    ne compte rien : toute approbation écrite nomme sa tâche.
    """
    if not task_id:
        return frozenset()
    return frozenset(
        str(e.get("spec_sha256", ""))
        for e in _read(project_root / APPROVALS_RELPATH)
        if e.get("type") == "approval"
        and e.get("task_id") == task_id
        and (
            e.get("attestation") == "attested"
            or e.get("allow_unattested") is True
            or (str(e.get("attestation", "")).startswith("granted:mandate:") and bool(e.get("mandate_id")))
        )
    )


def _journal(project_root: Path) -> Any:
    from grimoire.flows.attestation import CheckpointJournal

    return CheckpointJournal(project_root / FLOWS_RELPATH, _JOURNAL_RUN)


def _set_sha256(specs: Sequence[CheckSpec]) -> str:
    """Empreinte de l'ensemble des empreintes de *specs* (ordre et doublons sans effet)."""
    return hashlib.sha256("\n".join(sorted({s.sha256 for s in specs})).encode("ascii")).hexdigest()


def _node(task_id: str, specs: Sequence[CheckSpec]) -> str:
    """Le node du challenge : la tâche ET l'ensemble exact des commandes montrées à l'humain."""
    return f"approve-check:{task_id}:{_set_sha256(specs)}"


def _needs_human(env: Mapping[str, str] | None) -> bool:
    """Une session d'hôte, un marqueur d'agent ou une exécution déclarée sans humain : l'approbation s'atteste."""
    from grimoire.core.standard_state import agent_marker, current_session
    from grimoire.hosts.decisions.human_turns import is_unattended

    return bool(current_session(env)[0] or agent_marker(env) or is_unattended(env))


def approve_checks(
    project_root: Path,
    task_id: str,
    specs: Sequence[CheckSpec],
    *,
    actor: str,
    env: Mapping[str, str] | None = None,
    allow_unattested: bool = False,
) -> ApprovalOutcome:
    """Inscrit l'approbation de *specs* ; l'acteur est classé par W2-01 (un tour humain consommé par appel).

    Le challenge est celui de ``(task_id, specs)`` exactement : un tour humain
    obtenu pour d'autres commandes n'atteste pas celles-ci. Une approbation que
    rien n'atteste (terminal nu) est refusée (``unattested``) sauf
    *allow_unattested*, qui la journalise comme telle et la fait compter.
    """
    from grimoire.flows.attestation import attest

    root = project_root.resolve()
    for spec in specs:
        found = hidden_characters(spec.cmd)
        if found:
            raise ApprovalRefusedError(
                "hidden_characters",
                f"commande refusée ({', '.join(found)}) : caractère de contrôle bidirectionnel ou de largeur nulle, "
                f"l'affichage ne dit pas ce qui s'exécute — {visible(spec.cmd)}",
            )
    journal = _journal(root)
    node = _node(task_id, specs)
    challenge = journal.open_challenge(node)
    attestation: Attestation = attest(root, challenge, env, journal.consumed_turns())
    if not attestation.attested and _needs_human(env):
        if challenge is None:
            journal.issue_challenge(node, env)
        raise ApprovalRefusedError(
            "awaiting_human",
            f"awaiting_human : {attestation.reason} — {attestation.explain()}. Une approbation depuis une session "
            "d'hôte ou un agent exige un tour humain de cette session postérieur au challenge : "
            "dis « approuvé » dans la conversation, puis relance cette commande.",
        )
    if not attestation.attested and not allow_unattested:
        raise ApprovalRefusedError(
            "unattested",
            f"unattested : {attestation.reason} — {attestation.explain()}. Aucun tour humain n'atteste cette "
            "approbation en profil governed/production : depuis un terminal nu, relance avec --allow-unattested "
            "(journalisé comme non attesté ; ce n'est pas une preuve d'humain).",
        )
    journal.record_decision(node, "approve", "approve-check", attestation)
    _write_approvals(
        root, task_id, specs, actor, session_id=attestation.session_id, attestation=attestation.status,
        attestation_reason=attestation.reason, allow_unattested=bool(allow_unattested and not attestation.attested),
    )
    return ApprovalOutcome(tuple(s.sha256 for s in specs), attestation.status, attestation.reason, actor)


def _write_approvals(
    root: Path, task_id: str, specs: Sequence[CheckSpec], actor: str, *, session_id: str, attestation: str,
    attestation_reason: str, allow_unattested: bool, extra: Mapping[str, Any] | None = None,
) -> None:
    approvals = root / APPROVALS_RELPATH
    approvals.parent.mkdir(parents=True, exist_ok=True)
    stamp = _now()
    lines = [
        json.dumps(
            {
                "type": "approval",
                "task_id": task_id,
                "spec_sha256": spec.sha256,
                "command_sha256": command_sha256(spec.cmd),
                "expect_exit": spec.expect_exit,
                "actor": actor,
                "session_id": session_id,
                "attestation": attestation,
                "attestation_reason": attestation_reason,
                "allow_unattested": allow_unattested,
                "ts": stamp,
                **(extra or {}),
            },
            ensure_ascii=True,
        )
        for spec in specs
    ]
    with locked(approvals), approvals.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def approve_checks_under_mandate(
    project_root: Path, task: MissionTask, *, actor: str, provenance: Mapping[str, Any] | None
) -> ApprovalOutcome:
    """Approuve les ``checks`` déclarés de *task* sous mandat (#876, verrou 9).

    Le geste ``approve-check`` doit être couvert (:func:`grimoire.missions.mandate.require_authority`) ;
    puis les checks de la tâche doivent être ceux scellés à sa création : un check modifié depuis n'est pas
    approuvable sous mandat, et l'écart ouvre l'événement « disjoncteur » (``MandateStore.breaker_events``)
    que le lot 2 consomme. Les caractères cachés sont refusés comme pour un humain.
    """
    from grimoire.missions.mandate import MandateStore, require_authority
    from grimoire.missions.schemas import ORIGIN_ORCHESTRATOR

    root = project_root.resolve()
    specs = specs_of_task(task)
    if not specs:
        raise ApprovalRefusedError("no_checks", f"{task.id} ne déclare aucune commande : rien à approuver")
    for spec in specs:
        found = hidden_characters(spec.cmd)
        if found:
            raise ApprovalRefusedError(
                "hidden_characters", f"commande refusée ({', '.join(found)}) — {visible(spec.cmd)}"
            )
    authority = require_authority(
        root, "approve-check", task, ORIGIN_ORCHESTRATOR, what=f"Approbation refusée pour {task.id}", provenance=provenance
    )
    assert authority is not None
    try:
        MandateStore(root).verify_seal(task.id, task.checks)
    except GrimoireMissionError as exc:
        raise ApprovalRefusedError("seal", str(exc)) from exc
    status = f"granted:mandate:{authority.mandate_id}"
    _write_approvals(
        root, task.id, specs, authority.actor_label, session_id="", attestation=status, attestation_reason="mandat",
        allow_unattested=False, extra={"mandate_id": authority.mandate_id, "provenance": dict(authority.provenance)},
    )
    return ApprovalOutcome(tuple(s.sha256 for s in specs), status, "mandat", authority.actor_label)


class CheckGate:
    """Le lanceur de commandes de vérification d'un projet : approbation, reçu, réutilisation.

    ``run`` a la signature de :func:`grimoire.missions.dispatch._run_checks`
    (``run_dispatch(check_runner=gate.run)``). Hors profil governed/production
    il lui est strictement équivalent. *trusted* : les commandes que le kit a
    lui-même assemblées (le contrôle d'enveloppe d'un node), qui n'ont pas à
    être approuvées. *reuse* : une commande verte dont l'arbre est identique à
    celui d'un reçu n'est pas relancée (la clôture, pas la cascade).
    """

    def __init__(
        self, project_root: Path, task_id: str = "", *, trusted: Sequence[str] = (), reuse: bool = False
    ) -> None:
        self.root = project_root.resolve()
        self.task_id = task_id
        self.trusted = frozenset(trusted)
        self.reuse = reuse
        self.enforced = enforced(self.root)
        #: Un reçu par commande du dernier ``run`` (``None`` si elle n'a pas tourné : non approuvée ou de confiance).
        self.receipts: tuple[dict[str, Any] | None, ...] = ()

    def blocked(self, specs: Sequence[CheckSpec]) -> tuple[Blocked, ...]:
        """Les commandes qui ne peuvent pas tourner (vide hors profil appliqué)."""
        if not self.enforced:
            return ()
        approved = _approved_specs(self.root, self.task_id)
        out: list[Blocked] = []
        for index, spec in enumerate(specs):
            if spec.cmd in self.trusted:
                continue
            found = hidden_characters(spec.cmd)
            reason = f"caractères cachés {', '.join(found)}" if found else ("" if spec.sha256 in approved else "non approuvée")
            if reason:
                out.append(Blocked(index, command_sha256(spec.cmd), reason, visible(spec.cmd)))
        return tuple(out)

    def preflight(self, specs: Sequence[CheckSpec]) -> tuple[Blocked, ...]:
        """:meth:`blocked`, plus le challenge W2-01 qui permettra d'attester l'approbation (au mieux)."""
        blocked = self.blocked(specs)
        shown = [specs[b.index] for b in blocked if "caractères" not in b.reason]
        if shown:
            # Sans challenge, l'approbation depuis une session sera refusée : le refus nomme le remède.
            # Un challenge par ensemble que `approve-check` peut approuver : les commandes refusées,
            # et le lot entier (ce que l'approbation par défaut d'une tâche inscrit).
            sets = [shown]
            whole = [s for s in specs if s.cmd not in self.trusted]
            if _set_sha256(whole) != _set_sha256(shown):
                sets.append(whole)
            with contextlib.suppress(OSError, ValueError):
                for chosen in sets:
                    _journal(self.root).issue_challenge(_node(self.task_id, chosen))
        return blocked

    def run(
        self,
        checks: tuple[str, ...],
        *,
        project_root: Path | None = None,
        expect_exits: tuple[int, ...] = (),
        expect_stdout_contains: tuple[str | None, ...] = (),
        timeouts: tuple[float | None, ...] = (),
    ) -> tuple[CheckResult, ...]:
        root = (project_root or self.root).resolve()
        if not self.enforced:
            self.receipts = tuple(None for _ in checks)
            return _run_checks(
                checks,
                project_root=root,
                expect_exits=expect_exits,
                expect_stdout_contains=expect_stdout_contains,
                timeouts=timeouts,
            )
        specs = [
            CheckSpec(
                cmd,
                expect_exits[i] if i < len(expect_exits) else 0,
                expect_stdout_contains[i] if i < len(expect_stdout_contains) else None,
                timeouts[i] if i < len(timeouts) else None,
            )
            for i, cmd in enumerate(checks)
        ]
        blocked = self.preflight(specs)
        if blocked:
            self.receipts = tuple(None for _ in checks)
            reasons = {b.index: b.reason for b in blocked}
            return tuple(
                CheckResult(
                    cmd=spec.cmd,
                    ok=False,
                    exit_code=None,
                    unrunnable_reason=(
                        f"{UNAPPROVED} : {reasons[i]}" if i in reasons else f"{UNAPPROVED} : non lancée, une autre commande du lot est refusée"
                    ),
                )
                for i, spec in enumerate(specs)
            )
        results: list[CheckResult] = []
        receipts: list[dict[str, Any] | None] = []
        for spec in specs:
            result, receipt = self._run_one(spec, root)
            results.append(result)
            receipts.append(receipt)
        self.receipts = tuple(receipts)
        return tuple(results)

    def _run_one(self, spec: CheckSpec, root: Path) -> tuple[CheckResult, dict[str, Any] | None]:
        if spec.cmd in self.trusted:
            (result,) = _run_checks(
                (spec.cmd,),
                project_root=root,
                expect_exits=(spec.expect_exit,),
                expect_stdout_contains=(spec.expect_stdout_contains,),
                timeouts=(spec.timeout_s,),
            )
            return result, None
        from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

        fingerprint = compute_tree_fingerprint(root)
        if self.reuse:
            prior = self._find_receipt(spec.sha256, fingerprint)
            if prior is not None:
                code = prior.get("exit")
                reused = CheckResult(
                    cmd=spec.cmd,
                    ok=True,
                    exit_code=code if isinstance(code, int) else None,
                    output_excerpt=str(prior.get("output_excerpt", "")),
                )
                return reused, {**prior, "reused": True}
        (result,) = _run_checks(
            (spec.cmd,),
            project_root=root,
            expect_exits=(spec.expect_exit,),
            expect_stdout_contains=(spec.expect_stdout_contains,),
            timeouts=(spec.timeout_s,),
        )
        receipt: dict[str, Any] = {
            "type": "receipt",
            "task_id": self.task_id,
            "command_sha256": command_sha256(spec.cmd),
            "spec_sha256": spec.sha256,
            "tree_fingerprint": fingerprint,
            "exit": result.exit_code,
            "ok": result.ok,
            "output_excerpt": result.output_excerpt,
            "ts": _now(),
        }
        self._store_receipt(receipt)
        return result, {**receipt, "reused": False}

    def _find_receipt(self, spec_sha: str, fingerprint: str) -> dict[str, Any] | None:
        for entry in reversed(_read(self.root / RECEIPTS_RELPATH)):
            if (
                entry.get("type") == "receipt"
                and entry.get("spec_sha256") == spec_sha
                and entry.get("tree_fingerprint") == fingerprint
                and entry.get("ok") is True
            ):
                return dict(entry)
        return None

    def _store_receipt(self, receipt: Mapping[str, Any]) -> None:
        path = self.root / RECEIPTS_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path):
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
            lines.append(json.dumps(dict(receipt), ensure_ascii=True))
            if len(lines) > _RECEIPTS_MAX_LINES:
                lines = lines[-_RECEIPTS_KEEP_LINES:]
            atomic_write(path, "\n".join(lines) + "\n")
