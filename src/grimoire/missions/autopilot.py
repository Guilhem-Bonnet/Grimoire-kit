"""Le pilote automatique : l'orchestrateur tranche aussi les cas bloqués, sauf une liste fermée réservée (P82).

Un interrupteur **par projet**, inscrit au journal d'autorité (``mandates.authority.jsonl``, chaîné par hash) :
activé ou coupé, par qui, par quel canal, quand, et **pour quel mandat**.

**Autorité** — le pilote ne s'active et ne se coupe que par le canal humain, comme le mandat :

- la CLI ``grimoire autonomy autopilot on|off`` : une demande émet un challenge, inscrite seulement attestée par un tour
  humain de la session qui l'a demandée (:meth:`AutopilotStore.request`), le tour étant consommé ; un agent n'en a pas ;
- le cockpit, derrière son jeton **et** la clé de contrôle (:meth:`AutopilotStore.set` avec ``origin="human"``).

Il exige un mandat actif et **lui est lié** : l'entrée « activé » nomme l'identifiant du mandat. Mandat révoqué, expiré,
remplacé ou journal illisible : le pilote est coupé (:meth:`AutopilotStore.state`), sans écrire. Un nouveau mandat ne
rallume rien.

**Liste fermée** (:data:`RESERVED`) — pilote activé, ces questions restent à l'humain : identifiants, secrets, dépense
payante, accès hors du dépôt, suppression de données utilisateur, octroi ou révocation du mandat, réglages du dépôt.
Elle est dans le code. La section ``autopilot`` de ``orchestration-policy.yaml`` peut seulement **ajouter** des
réservations (``autopilot.reserved``, une liste de catégories d'escalade ou de mots) ; toute autre clé, toute forme
inattendue rend la politique invalide et le pilote ne décide plus rien (échec fermé). Le classement lit la question, ses
options, sa raison et la tâche, sans accents ni casse ni caractères invisibles ; il préfère réserver à tort que décider
à tort — un faux positif coûte une question à l'humain, un faux négatif une décision que la liste devait lui garder.

**Comment il décide** (:func:`judge`) — d'abord l'option **recommandée** par la question (``task ask --recommended``,
champ ``recommended``) : si la réponse est cette option, elle porte la source ``recommended``. À défaut, ou en
divergeant, la réponse cite sa justification et déclare sa confiance (``high``, ``medium``) ; une confiance basse ou non
déclarée laisse la question à l'humain, avec la raison, et le pilote l'inscrit (:data:`DEFERRED_EVENT`) pour que le
cockpit la signale.

**Traçabilité** — chaque décision du pilote est un événement du ledger (:data:`DECIDED_EVENT`) : question, option,
justification, confiance, source de la recommandation, mandat.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError, GrimoireRuntimeError
from grimoire.missions.mandate import (
    ORCHESTRATION_FILE,
    AuthorityJournal,
    MandateRefusedError,
    MandateStore,
    _canon,
    task_categories,
)
from grimoire.missions.schemas import ORIGIN_HUMAN

if TYPE_CHECKING:
    from grimoire.flows.attestation import Attestation
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.waiting import Question

__all__ = [
    "AUTOPILOT_ENTRY",
    "CONFIDENCES",
    "DECIDED_EVENT",
    "DEFERRED_EVENT",
    "RESERVED",
    "AutopilotPolicy",
    "AutopilotState",
    "AutopilotStore",
    "Decision",
    "decided_payload",
    "judge",
    "last_deferral",
    "load_autopilot_policy",
    "reserved_reason",
]

AUTOPILOT_ENTRY = "autopilot"
_CHALLENGE = "autopilot_challenge"
#: Une décision du pilote, au ledger, sur la tâche.
DECIDED_EVENT = "autopilot.decided"
#: Une question que le pilote laisse à l'humain faute de confiance : le cockpit la signale.
DEFERRED_EVENT = "autopilot.deferred"
#: Les niveaux de confiance qu'une réponse déclare ; ``low`` (et le silence) laisse la question à l'humain.
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
_DECIDING = frozenset({"high", "medium"})
_MIN_JUSTIFICATION = 12
_TEXT_SIZE = 400
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿­]")
#: Lettres cyrilliques, grecques ou arméniennes : des homoglyphes de lettres latines déguisent un mot de la liste fermée.
_LOOKALIKE = re.compile(r"[\u0370-\u03ff\u0400-\u052f\u0531-\u058f]")
#: ``$HOME``, ``${X}``, ``%APPDATA%`` : un chemin que la question ne nomme pas en clair.
_ENV_PATH = re.compile(r"\$\{?\w+|%\w+%")
_PATH = re.compile(r"(?<![\w.])(?:~[\\/]?|[a-zA-Z]:[\\/]|/|\.\.[\\/])[^\s\"'`,;)\]]*")

#: La liste fermée : catégorie → motif (sur le texte plié : sans accents, ni casse). Dans le code, pas dans un fichier.
RESERVED: dict[str, re.Pattern[str]] = {
    "identifiants": re.compile(
        r"identifiant|credential|mot de passe|password|passwd|login|\bsso\b|\boauth\b|\btoken\b|jeton|\bapi[ _-]?key\b|"
        r"cle d'?api|cle ssh|ssh[ _-]?key|certificat|\bkeychain\b|trousseau"
    ),
    "secrets": re.compile(
        r"secret|cle privee|private key|\.env\b|\bvault\b|coffre|\bgpg\b|\bpgp\b|credentials\.json|id_rsa|\.pem\b"
    ),
    "depense": re.compile(
        r"payant|paye\b|payer|factur|billing|\bcout\b|\bcoute\b|depens|\bbanc\b|benchmark|\bcredits?\b|abonnement|"
        r"subscription|\bpaid\b|\bcost\b|\bbudget\b|\bquota payant|tarif|\bprix\b|\bprice\b"
    ),
    "donnees-utilisateur": re.compile(
        r"(?:supprim|effac|detrui|delet|purg|drop|wipe|truncate|rm -rf|ecras|overwrit)\w*"
        r"(?=[^.]{0,80}(?:donnee|data|base\b|database|compte|account|utilisateur|\buser|profil|\bbackup|sauvegarde))"
        r"|(?:donnee|data|base\b|database|compte|account|utilisateur|\buser|profil|sauvegarde)[^.]{0,80}"
        r"(?:supprim|effac|detrui|delet|purg|drop|wipe|truncate|ecras|overwrit)"
    ),
    "mandat": re.compile(
        r"\bmandat|\bmandate\b|autonomy|autonomie|pilote automatique|autopilot|politique d'?escalade|"
        r"escalation[ _-]?policy|orchestration-policy|cockpit-key|cle de controle|journal d'?autorite|authority"
    ),
    "reglages-depot": re.compile(
        r"reglages? du depot|repo(?:sitory)? settings|branch protection|protection de branche|settings\.json|"
        r"\.github/|workflow|webhook|visibilite|visibility|collaborat|deploy key|clef de deploiement|codeowners|"
        r"ruleset|rulesets|force[- ]push|\bpyproject\b|\.claude/|git hooks?"
    ),
}
#: La catégorie propre aux permissions : un accès hors du dépôt, ou sans chemin pour en juger.
_OUTSIDE_REPO = "hors-depot"


def _fold(text: object) -> str:
    """Le texte comparé : sans caractères invisibles, sans accents ni casse, espaces réduits."""
    cleaned = _INVISIBLE.sub("", str(text))
    folded = "".join(c for c in unicodedata.normalize("NFKD", cleaned) if not unicodedata.combining(c))
    return " ".join(folded.lower().split())


def _outside_repo(texts: list[str], project_root: Path) -> bool:
    """Vrai si un chemin cité sort du dépôt, ou si aucun n'est cité (une permission sans chemin ne se juge pas)."""
    if any(_ENV_PATH.search(text) for text in texts):
        return True
    root = project_root.resolve()
    found = [m.group(0).rstrip(".:") for text in texts for m in _PATH.finditer(text)]
    if not found:
        return True
    for raw in found:
        if raw.startswith(("~", "..")) or re.match(r"[a-zA-Z]:", raw):
            return True
        try:
            Path(raw).resolve().relative_to(root)
        except (ValueError, OSError):
            return True
    return False


@dataclass(frozen=True, slots=True)
class AutopilotPolicy:
    """Ce que le projet ajoute à la liste fermée ; il ne peut rien en retirer. *error* : invalide, le pilote ne décide rien."""

    extra: tuple[str, ...] = ()
    error: str = ""


def load_autopilot_policy(project_root: Path) -> AutopilotPolicy:
    """La section ``autopilot`` de ``orchestration-policy.yaml`` : seule la clé ``reserved`` (liste de textes) existe."""
    path = project_root / ORCHESTRATION_FILE
    if not path.is_file():
        return AutopilotPolicy()
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # une politique illisible ne se lit pas à moitié
        return AutopilotPolicy(error=f"{ORCHESTRATION_FILE.as_posix()} illisible : {type(exc).__name__}")
    if not isinstance(data, Mapping) or "autopilot" not in data:
        return AutopilotPolicy()
    section = data["autopilot"]
    if section is None:
        return AutopilotPolicy()
    if not isinstance(section, Mapping):
        return AutopilotPolicy(error="`autopilot` doit être une table")
    unknown = sorted(str(k) for k in section if k != "reserved")
    if unknown:
        return AutopilotPolicy(
            error=f"`autopilot` : clé inconnue {', '.join(unknown)} — seule `reserved` existe (elle ajoute des "
            "réservations, jamais n'en retire)"
        )
    raw = section.get("reserved", [])
    if not isinstance(raw, list) or not all(isinstance(r, str) and _fold(r) for r in raw):
        return AutopilotPolicy(error="`autopilot.reserved` doit être une liste de textes non vides")
    return AutopilotPolicy(tuple(dict.fromkeys(_fold(r) for r in raw)))


def reserved_reason(
    question: Question, task: MissionTask, project_root: Path, policy: AutopilotPolicy | None = None
) -> str:
    """Pourquoi *question* reste à l'humain pilote activé (``""`` : le pilote peut la trancher).

    La liste fermée d'abord, puis les ajouts de la politique. Une politique invalide réserve tout.
    """
    policy = load_autopilot_policy(project_root) if policy is None else policy
    if policy.error:
        return f"politique du pilote invalide ({policy.error}) : tout reste à l'humain"
    texts = [question.text, question.why, *question.options, task.title]
    folded = _fold(" ".join(texts))
    if _LOOKALIKE.search(folded):
        return "catégorie réservée à l'humain, même pilote activé : texte aux lettres non latines (homoglyphes possibles)"
    for category, pattern in RESERVED.items():
        if pattern.search(folded):
            return f"catégorie réservée à l'humain, même pilote activé : {category}"
    if question.kind == "permission" and _outside_repo([question.text, question.why], project_root):
        return f"catégorie réservée à l'humain, même pilote activé : {_OUTSIDE_REPO} (permission hors du dépôt, ou sans chemin)"
    categories = task_categories(task)
    for extra in policy.extra:
        if extra in categories or extra in folded:
            return f"réservé par la politique du projet (`autopilot.reserved` : {extra})"
    return ""


# ── Décision ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Decision:
    """Ce que le pilote a décidé : l'option, d'où elle vient, avec quelle confiance et pourquoi."""

    answer: str
    source: str  # ``recommended`` | ``judgment``
    confidence: str
    justification: str

    def to_dict(self) -> dict[str, str]:
        return {
            "answer": self.answer[:_TEXT_SIZE],
            "source": self.source,
            "confidence": self.confidence,
            "justification": self.justification[:_TEXT_SIZE],
        }


def judge(question: Question, answer: str, *, justification: str = "", confidence: str = "") -> Decision:
    """La décision du pilote sur *question* ; lève :class:`GrimoireMissionError` si elle reste à l'humain.

    Une réponse égale à l'option recommandée (casse et espaces mis à part) est la recommandation appliquée. Toute autre
    réponse est un jugement : elle cite sa justification et déclare une confiance ``high`` ou ``medium``.
    """
    text = " ".join(answer.split())
    recommended = " ".join(question.recommended.split())
    if recommended and text.lower() == recommended.lower():
        return Decision(text, "recommended", "high", " ".join((justification or question.why).split()))
    level = confidence.strip().lower()
    why = " ".join(justification.split())
    if level not in _DECIDING:
        shown = level or "non déclarée"
        raise GrimoireMissionError(
            f"pilote automatique : confiance {shown} — la question reste à l'humain "
            f"({'confiance basse' if level == 'low' else 'déclarer --confidence high|medium'})"
        )
    if len(why) < _MIN_JUSTIFICATION:
        raise GrimoireMissionError(
            "pilote automatique : une décision cite sa justification (--why, au moins une phrase) — la question reste "
            "à l'humain"
        )
    return Decision(text, "judgment", level, why)


# ── État ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AutopilotState:
    """Le pilote du projet, réévalué maintenant : actif, ou la raison pour laquelle il ne l'est pas."""

    active: bool
    reason: str = ""
    mandate_id: str = ""
    by: str = ""
    via: str = ""
    since: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "mandate_id": self.mandate_id,
            "by": self.by,
            "via": self.via,
            "since": self.since,
        }


class AutopilotStore:
    """Le pilote d'un projet, lu et écrit dans le journal d'autorité (comme :class:`MandateStore`)."""

    def __init__(self, project_root: Path, journal: AuthorityJournal | None = None) -> None:
        self.mandates = MandateStore(project_root, journal)
        self.root = self.mandates.root
        self.project = self.mandates.project
        self.journal = self.mandates.journal

    def state(self, now: datetime | None = None) -> AutopilotState:
        """Actif seulement si la dernière entrée du projet l'active **pour le mandat actif**. Échec fermé."""
        try:
            mandate = self.mandates.state(now)
            entries = self.journal.read()
        except Exception as exc:  # JournalError ou tout autre : un journal qu'on ne lit pas n'active rien
            return AutopilotState(False, f"journal d'autorité illisible ({type(exc).__name__})")
        mine = [e for e in entries if e.get("type") == AUTOPILOT_ENTRY and e.get("project") == self.project]
        if not mine or mine[-1].get("state") != "on":
            return AutopilotState(False, "coupé")
        last = mine[-1]
        base = {"by": str(last.get("by") or ""), "via": str(last.get("via") or ""), "since": str(last.get("ts") or "")}
        if not mandate.active or mandate.mandate is None:
            return AutopilotState(
                False,
                f"mandat inactif ({mandate.reason or 'aucun'}) : le pilote est coupé",
                mandate_id=str(last.get("mandate_id") or ""),
                **base,
            )
        if last.get("mandate_id") != mandate.mandate.id:
            return AutopilotState(
                False, "activé pour un autre mandat : à réactiver", mandate_id=str(last.get("mandate_id") or ""), **base
            )
        if last.get("by") != ORIGIN_HUMAN:
            return AutopilotState(
                False, "activation sans origine humaine : ignorée", mandate_id=str(last.get("mandate_id") or ""), **base
            )
        return AutopilotState(True, "", mandate_id=mandate.mandate.id, **base)

    def history(self) -> list[dict[str, Any]]:
        """Les activations et coupures du projet, de la plus ancienne à la plus récente."""
        return [e for e in self.journal.read() if e.get("type") == AUTOPILOT_ENTRY and e.get("project") == self.project]

    def set(
        self,
        on: bool,
        *,
        origin: str,
        via: str,
        attestation: Attestation | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Inscrit l'activation ou la coupure. *origin* vient du canal (cockpit derrière sa clé, tour humain attesté)."""
        if origin != ORIGIN_HUMAN:
            raise MandateRefusedError(
                "human_required",
                f"pilote automatique refusé : origine {origin!r}. Seul l'humain l'active ou le coupe — pas "
                "l'orchestrateur, pas un agent",
            )
        moment = now or datetime.now(UTC)
        mandate = self.mandates.state(moment)
        if on and (not mandate.active or mandate.mandate is None):
            raise MandateRefusedError(
                mandate.reason or "no_mandate",
                f"pilote automatique refusé : il exige un mandat actif ({mandate.reason or 'aucun mandat'})",
            )
        if on and load_autopilot_policy(self.root).error:
            raise MandateRefusedError(
                "policy_invalid", f"pilote automatique refusé : {load_autopilot_policy(self.root).error}"
            )
        return self.journal.append(
            {
                "type": AUTOPILOT_ENTRY,
                "project": self.project,
                "state": "on" if on else "off",
                "mandate_id": mandate.mandate.id if mandate.mandate is not None else "",
                "by": ORIGIN_HUMAN,
                "via": via,
                "attestation": attestation.status if attestation else "",
                "session_id": attestation.session_id if attestation else "",
                "ts": moment.isoformat(),
            }
        )

    def request(
        self,
        on: bool,
        env: Mapping[str, str] | None = None,
        now: datetime | None = None,
        *,
        attest_from: Path | None = None,
    ) -> tuple[dict[str, Any] | None, Attestation]:
        """Voie CLI : inscrit si la demande est attestée par un tour humain de cette session ; sinon émet son challenge.

        Même mécanisme que ``autonomy mandate grant`` : le challenge est celui de cette demande, de ce mandat et de cette
        session ; un refus ne le consomme pas ; un tour humain ne sert qu'une décision (registre commun des tours).
        """
        from grimoire.core.standard_state import current_session
        from grimoire.flows.attestation import FLOWS_RELPATH, CheckpointJournal, attest_grant, turns_source

        source = turns_source(attest_from) if attest_from is not None else None
        entries = self.journal.read()
        session_id, _host = current_session(env)
        mandate = self.mandates.state(now)
        digest = hashlib.sha256(
            _canon(
                {
                    "autopilot": "on" if on else "off",
                    "project": self.project,
                    "mandate": mandate.mandate.id if mandate.mandate is not None else "",
                }
            ).encode("ascii")
        ).hexdigest()
        used = {e.get("challenge_id") for e in entries if e.get("type") == AUTOPILOT_ENTRY}
        challenge = next(
            (
                e
                for e in reversed(entries)
                if e.get("type") == _CHALLENGE
                and e.get("request_sha256") == digest
                and e.get("project") == self.project
                and e.get("session_id", "") == session_id
            ),
            None,
        )
        if challenge is not None and challenge.get("challenge_id") in used:
            challenge = None
        if challenge is None:
            challenge = self.journal.append(
                {
                    "type": _CHALLENGE,
                    "challenge_id": f"ACH-{uuid.uuid4().hex[:12]}",
                    "request_sha256": digest,
                    "project": self.project,
                    "session_id": session_id,
                    "ts": (now or datetime.now(UTC)).isoformat(),
                }
            )
        turns = CheckpointJournal(self.root / FLOWS_RELPATH, "autonomy-grants")
        attestation, registry = attest_grant(self.root, turns, challenge, env, source)
        if not attestation.attested:
            return None, attestation
        decision_id = f"APL-{uuid.uuid4().hex[:12]}"
        if registry is turns:
            turns.consume_turn(decision_id, attestation)
        else:
            try:
                registry.consume_turn(decision_id, attestation, for_project=self.project)
            except OSError as exc:
                raise GrimoireRuntimeError(
                    f"--attest-from : le tour n'a pas pu être consommé dans {registry.consumed_path} "
                    f"({type(exc).__name__}) ; rien d'inscrit"
                ) from exc
        entry = self.set(on, origin=ORIGIN_HUMAN, via="attestation", attestation=attestation, now=now)
        with contextlib.suppress(Exception):  # le challenge servi ne se rejoue pas (le tour est déjà consommé)
            self.journal.append(
                {"type": f"{_CHALLENGE}_used", "challenge_id": challenge["challenge_id"], "ts": entry["ts"]}
            )
        return {**entry, "challenge_id": challenge["challenge_id"]}, attestation


def decided_payload(
    task_id: str, question: Question, decision: Decision, gesture: str, mandate_id: str
) -> dict[str, Any]:
    """Le corps de :data:`DECIDED_EVENT` : tout ce qu'il faut pour relire, et défaire, la décision."""
    return {
        "task_id": task_id,
        "question_id": question.id,
        "question": question.text[:_TEXT_SIZE],
        "kind": question.kind,
        "option": decision.answer[:_TEXT_SIZE],
        "justification": decision.justification[:_TEXT_SIZE],
        "confidence": decision.confidence,
        "recommendation_source": decision.source,
        "recommended": question.recommended[:_TEXT_SIZE],
        "gesture": gesture,
        "mandate_id": mandate_id,
        "reversible": True,  # « abandonner » range la tâche en failed : `task move --to ready` la rouvre
    }


def last_deferral(events: list[LedgerEvent], question_id: str) -> str:
    """La raison pour laquelle le pilote a laissé *question_id* à l'humain (``""`` : jamais)."""
    for event in reversed(events):
        if event.event_type == DEFERRED_EVENT and event.payload.get("question_id") == question_id:
            return str(event.payload.get("reason") or "")
    return ""
