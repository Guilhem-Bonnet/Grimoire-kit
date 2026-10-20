"""Les décisions attendues d'un humain : les lister, les notifier, repérer celles qui restent implicites (K6, issue #771).

Une décision attendue n'est jamais laissée dans le fil de discussion (§2.7) :
c'est une tâche ``waiting(human)`` qui porte une question. Ce module en tire
trois lectures, toutes dérivées du ledger — aucun compteur à côté :

- :func:`pending_decisions` — ce qui attend un humain, la plus ancienne en tête ;
- :func:`notify_pending` — la commande ``notify.command`` lancée **une fois par
  question** (``task.question_notified``), au mieux : un échec se voit dans
  ``grimoire status`` et ne bloque rien ;
- :func:`lint_decisions` — les commentaires et notes de progression qui posent
  une question de fusion ou de permission sans avoir été posée par ``task ask``.
  Un **indicateur heuristique**, pas une preuve.

La commande de notification est celle du propriétaire du projet : même confiance
que ``--check`` de ``task dispatch``. Elle n'est jamais passée à un shell (``shlex``,
``shell=False``) : le texte de la question n'est donc jamais interprété.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.lease import parse_instant
from grimoire.missions.questions import group_settled, open_in_group
from grimoire.missions.schemas import TaskState
from grimoire.missions.waiting import (
    PROGRESS_NOTED_EVENT,
    QUESTION_ASKED_EVENT,
    QUESTION_NOTIFIED_EVENT,
    DecisionLint,
    Question,
    lint_decision_text,
)

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "MAX_NOTIFY_FAILURES",
    "NOTIFY_ENV",
    "NOTIFY_TIMEOUT_SECONDS",
    "NotifyOutcome",
    "PendingDecision",
    "decisions_summary",
    "lint_decisions",
    "load_notify_command",
    "notify_pending",
    "pending_decisions",
]

#: Surcharge d'environnement de ``options.notify.command`` (testable, et propre à un poste).
NOTIFY_ENV = "GRIMOIRE_NOTIFY_COMMAND"
#: Au-delà, la commande est tuée : une notification ne retient jamais le tick.
NOTIFY_TIMEOUT_SECONDS = 10.0
#: Après autant d'échecs pour une même question, on ne réessaie plus (l'échec reste visible dans ``status``).
MAX_NOTIFY_FAILURES = 3


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """Une question qui attend un humain, avec la tâche qui la porte.

    ``question`` est la tête du lot (son identifiant clé la notification : une par lot) ; ``questions``
    (W6-07) les questions du lot encore sans réponse, dans l'ordre — une seule question pour K6.
    """

    task_id: str
    title: str
    question: Question
    since: str
    questions: tuple[Question, ...] = ()

    def age_seconds(self, now: datetime) -> float:
        moment = parse_instant(self.since or self.question.asked_at)
        return max((now - moment).total_seconds(), 0.0) if moment is not None else 0.0

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id, "title": self.title, "since": self.since,
            "question": self.question.to_dict(),
            "questions": [q.to_dict() for q in self.questions or (self.question,)],
        }
        if now is not None:
            data["age_seconds"] = int(self.age_seconds(now))
        return data


def pending_decisions(ledger: MissionLedger) -> list[PendingDecision]:
    """Les tâches ``waiting(human)`` dont le lot de questions n'est pas tranché, la plus ancienne d'abord.

    Un lot dont toutes les bloquantes ont leur réponse humaine n'attend plus personne : le réveil
    (``answer`` ou le tick) le rend, même si une non bloquante reste ouverte.
    """
    out: list[PendingDecision] = []
    for task in ledger.list_tasks():
        waiting = task.waiting
        if task.status is not TaskState.WAITING or waiting is None or waiting.question is None:
            continue
        if not waiting.is_human or group_settled(task):
            continue
        out.append(PendingDecision(
            task.id, task.title, waiting.question, waiting.since or waiting.question.asked_at,
            tuple(r.question for r in open_in_group(task)),
        ))
    out.sort(key=lambda d: (parse_instant(d.since) or datetime.max.replace(tzinfo=UTC), d.task_id))
    return out


def _notified(ledger: MissionLedger, task_id: str, question_id: str) -> tuple[bool, int]:
    """``(déjà notifiée avec succès, nombre d'échecs)`` pour cette question."""
    done, failures = False, 0
    for e in ledger.list_events(task_id):
        if e.event_type != QUESTION_NOTIFIED_EVENT or e.payload.get("question_id") != question_id:
            continue
        if e.payload.get("ok") is True:
            done = True
        else:
            failures += 1
    return done, failures


def load_notify_command(project_root: Path) -> str:
    """``notify.command`` : ``$GRIMOIRE_NOTIFY_COMMAND``, sinon ``options.notify.command`` du profil du standard.

    Un profil illisible vaut « pas de commande » (la notification est au mieux), jamais une exception.
    """
    env = os.environ.get(NOTIFY_ENV, "").strip()
    if env:
        return env
    try:
        from ruamel.yaml import YAML

        from grimoire.core.standard_generation import STANDARD_PROFILE_FILE

        path = project_root.resolve() / STANDARD_PROFILE_FILE
        if not path.is_file():
            return ""
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    options = data.get("options") if isinstance(data, dict) else None
    notify = options.get("notify") if isinstance(options, dict) else None
    command = notify.get("command") if isinstance(notify, dict) else None
    return command.strip() if isinstance(command, str) else ""


@dataclass(frozen=True, slots=True)
class NotifyOutcome:
    """Ce que la notification d'une question a donné : ``sent``, ``failed``, ``skipped`` (déjà fait) ou ``gave_up``."""

    task_id: str
    question_id: str
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"task_id": self.task_id, "question_id": self.question_id, "status": self.status, "error": self.error}


def _run(command: str, decision: PendingDecision) -> str:
    """Lance la commande ; ``""`` si elle a réussi, sinon ce qui a échoué. Jamais d'exception."""
    title = f"Grimoire : décision attendue sur {decision.task_id}"
    message = decision.question.text
    try:
        argv = [part.replace("{title}", title).replace("{message}", message) for part in shlex.split(command)]
    except ValueError as exc:
        return f"commande illisible : {exc}"
    if not argv:
        return "commande vide"
    env = {
        **os.environ,
        "GRIMOIRE_NOTIFY_TASK": decision.task_id,
        "GRIMOIRE_NOTIFY_QUESTION_ID": decision.question.id,
        "GRIMOIRE_NOTIFY_KIND": decision.question.kind,
        "GRIMOIRE_NOTIFY_TITLE": title,
        "GRIMOIRE_NOTIFY_MESSAGE": message,
    }
    try:
        done = subprocess.run(  # commande du propriétaire du projet, jamais un shell
            argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=NOTIFY_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"délai dépassé ({NOTIFY_TIMEOUT_SECONDS:g} s)"
    except (OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        return f"code de sortie {done.returncode} : {(done.stderr or done.stdout).strip()[:200]}"
    return ""


def notify_pending(
    ledger: MissionLedger, project_root: Path, *, command: str | None = None, apply: bool = True,
    actor: str = "tick",
) -> list[NotifyOutcome]:
    """Notifie chaque question en attente **une fois** (``task.question_notified``), au mieux.

    Garantie au moins une fois : la question n'est marquée notifiée qu'après le
    succès de la commande ; un échec s'écrit (``ok: false``) et la prochaine
    passe réessaie, jusqu'à :data:`MAX_NOTIFY_FAILURES`. Sans commande déclarée,
    rien n'est tenté et rien n'est écrit. *apply* faux : ce qui serait notifié,
    sans lancer la commande.
    """
    cmd = load_notify_command(project_root) if command is None else command.strip()
    if not cmd:
        return []
    outcomes: list[NotifyOutcome] = []
    for decision in pending_decisions(ledger):
        qid = decision.question.id
        done, failures = _notified(ledger, decision.task_id, qid)
        if done:
            outcomes.append(NotifyOutcome(decision.task_id, qid, "skipped"))
            continue
        if failures >= MAX_NOTIFY_FAILURES:
            outcomes.append(NotifyOutcome(decision.task_id, qid, "gave_up"))
            continue
        if not apply:
            outcomes.append(NotifyOutcome(decision.task_id, qid, "pending"))
            continue
        error = _run(cmd, decision)
        ledger.append_event(
            QUESTION_NOTIFIED_EVENT, decision.task_id, "task", actor,
            {"task_id": decision.task_id, "question_id": qid, "ok": not error, "error": error},
        )
        outcomes.append(NotifyOutcome(decision.task_id, qid, "failed" if error else "sent", error))
    return outcomes


def notify_failures(ledger: MissionLedger) -> list[dict[str, str]]:
    """Les questions en attente dont la dernière notification a échoué — ce que ``status`` montre."""
    out: list[dict[str, str]] = []
    for decision in pending_decisions(ledger):
        done, failures = _notified(ledger, decision.task_id, decision.question.id)
        if done or not failures:
            continue
        last = [
            e for e in ledger.list_events(decision.task_id)
            if e.event_type == QUESTION_NOTIFIED_EVENT and e.payload.get("question_id") == decision.question.id
        ][-1]
        out.append({
            "task_id": decision.task_id, "question_id": decision.question.id,
            "error": str(last.payload.get("error", "")), "failures": str(failures),
        })
    return out


def _age_text(seconds: float) -> str:
    if seconds < 90:
        return "moins d'une minute" if seconds < 60 else "1 min"
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} min"
    hours = int(minutes // 60)
    return f"{hours} h" if hours < 48 else f"{hours // 24} j"


def decisions_summary(project_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Le résumé que ``grimoire status`` et SessionStart affichent ; vide sans ledger, jamais une exception.

    ``count`` décisions en attente, ``oldest`` la plus ancienne (tâche et âge), ``line`` la phrase
    prête à afficher (vide s'il n'y en a aucune), ``notify_failures`` les notifications échouées.
    """
    from grimoire.core.standard_state import LEDGER_RELPATH

    empty: dict[str, Any] = {"count": 0, "oldest": None, "line": "", "notify_failures": []}
    events = project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
    if not events.is_file():
        return empty
    try:
        from grimoire.missions.ledger import MissionLedger

        ledger = MissionLedger(events.parent)
        pending = pending_decisions(ledger)
        failures = notify_failures(ledger)
    except Exception:  # frontière de commande et de hook : un ledger illisible ne casse pas l'affichage
        return empty
    moment = now or datetime.now(UTC)
    if not pending:
        return {**empty, "notify_failures": failures}
    oldest = pending[0]
    age = _age_text(oldest.age_seconds(moment))
    noun = "décision" if len(pending) == 1 else "décisions"
    line = f"{len(pending)} {noun} en attente, plus ancienne : {age} ({oldest.task_id})"
    return {
        "count": len(pending),
        "oldest": {"task_id": oldest.task_id, "age": age, "age_seconds": int(oldest.age_seconds(moment))},
        "line": line,
        "notify_failures": failures,
    }


def lint_decisions(ledger: MissionLedger, task_id: str | None = None) -> list[DecisionLint]:
    """Les commentaires et notes de progression qui posent une décision sans ``task.question_asked`` associé.

    Un texte avec un point d'interrogation et un mot de décision (fusion, merge, permission,
    autoriser, valider) est « associé » si la même tâche a posé une question **à ou après** ce
    texte. Heuristique : elle rate les formulations hors vocabulaire et signale des faux positifs.
    """
    findings: list[DecisionLint] = []
    tasks: list[MissionTask] = [t for t in ledger.list_tasks() if task_id in (None, t.id)]
    for task in tasks:
        events = ledger.list_events(task.id)
        asked = [t for t in (parse_instant(e.created_at) for e in events if e.event_type == QUESTION_ASKED_EVENT) if t]
        candidates: list[tuple[str, str, str]] = [
            ("comment", d.text, d.created_at) for d in task.directives
        ]
        candidates += [
            ("progress", str(e.payload.get("note", "")), e.created_at)
            for e in events if e.event_type == PROGRESS_NOTED_EVENT
        ]
        for source, text, at in candidates:
            if not lint_decision_text(text):
                continue
            written = parse_instant(at)
            if written is not None and any(a >= written for a in asked):
                continue
            findings.append(DecisionLint(task.id, source, text, at))
    return findings
