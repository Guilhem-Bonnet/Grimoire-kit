"""La consommation des sessions lancées par l'orchestrateur, écrite au ledger (``quota.consumed``).

Avant ce module, l'admission lisait ``quota.consumed`` pour tenir un plafond ``quota.tokens_per_day`` et
aucun code du kit ne l'écrivait : sous un plafond fini la consommation du jour était « inconnue » et tout était
refusé par prudence. :func:`record_consumption` lit le journal de chaque session que le tick a lancée (worker,
relecteur, arbitre, session orchestrateur) et écrit ce qu'elle a consommé.

Règles :

* **Une session n'est comptée qu'une fois.** La clé est l'identifiant de la session (le condensé SHA-256 pour
  les rôles dont le ledger ne garde que le condensé). L'événement porte le *delta* depuis le dernier événement de
  la session et le total cumulé : rejouer le tick sans que le journal ait grandi n'écrit rien, et une session qui
  continue ne se compte pas deux fois.
* **Borne basse.** Le kit voit le journal de l'hôte, pas la facture : ``status`` vaut ``lower_bound``. Un coût
  inconnu pour un appel (modèle non tarifé) n'est jamais compté zéro : ``cost_status`` vaut alors ``lower_bound``
  ou ``unknown``.
* **Jamais de chiffre inventé, jamais de « complet » à tort.** Journal introuvable, illisible, trop gros, d'un hôte
  non lu, lancement sans identifiant, ou journal qui a changé sans avoir pu être relu à ce pas : un événement
  ``status: unknown`` avec une ``reason`` stable (sans grandeur qui varie), écrit une fois par changement d'état réel.
  Le dernier total connu reste la borne basse ; la consommation du jour est alors ``partial`` (voir
  :func:`~grimoire.missions.admission._consumed_today`), donc soumise à ``quota.on_unknown``. L'événement est remplacé
  par un événement connu dès que le journal est relu. Une session lancée depuis moins de :data:`GRACE` n'est pas
  déclarée inconnue faute de journal : il peut ne pas exister encore.
* **Un budget de lecture qui tourne.** Au plus ``max_sessions`` journaux sont lus par pas ; un journal dont la taille
  et la date n'ont pas changé depuis sa dernière lecture n'en consomme pas (le fichier d'état
  ``_grimoire-output/control/quota-reads.json`` n'est qu'un cache : perdu, tout est relu une fois). Les sessions à
  relire passent par ordre de dernière lecture, la plus ancienne d'abord : aucune n'est affamée.
* **Le jour d'un delta.** Un delta est daté de son écriture ; les appels dont l'horodatage précède minuit UTC
  sont comptés pour la veille (``day``). Limite restante : un appel sans horodatage lisible est compté pour le
  jour de la mesure, et l'ensemble d'une veille mesurée plusieurs jours après est daté de la veille.
* ``tokens`` est le total que ``grimoire task usage`` rapporte : entrée, sortie, cache lu et cache écrit.

Ce module n'a aucune voie d'écriture à la main : il n'écrit que ce que les journaux de session disent, et la
lecture (``queue rules``, cockpit) ne l'appelle pas.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write
from grimoire.core.regular_file import open_regular
from grimoire.missions.lease import parse_instant
from grimoire.missions.usage_rules import KIT_KEY_PREFIX, QUOTA_CONSUMED_EVENT, SESSION_EVENTS

if TYPE_CHECKING:
    from grimoire.missions.admission import QueuePlan
    from grimoire.missions.service import TaskService

__all__ = [
    "DEFERRAL_EVENT",
    "GIVE_UP_AFTER",
    "GRACE",
    "KIT_ORIGINS",
    "MAX_BYTES",
    "MAX_SESSIONS",
    "NO_ID_REASON",
    "OVERSIZE_REASON",
    "PENDING_REASON",
    "SESSION_EVENTS",
    "SETTLED_REASON",
    "SETTLE_AFTER",
    "STATE_RELPATH",
    "TIME_BUDGET_S",
    "ConsumedOutcome",
    "kit_call_refusal",
    "note_deferral",
    "record_consumption",
    "record_kit_call",
    "tracking",
]

GRACE = timedelta(minutes=10)
#: Au plus ce nombre de journaux lus par pas, et au plus cette taille chacun.
MAX_SESSIONS = 20
MAX_BYTES = 50 * 1024 * 1024
#: Une session connue, immobile et lancée il y a plus longtemps n'est plus même examinée.
_ANCIENT = timedelta(days=30)
#: Le cache des dernières lectures (taille, date, instant) : jamais une source de chiffre.
STATE_RELPATH = Path("_grimoire-output") / "control" / "quota-reads.json"
#: Au plus ce temps cumulé de lecture de journaux par pas : au-delà, le reste est reporté au tick suivant (la première
#: lecture d'un pas a toujours lieu, pour que le suivi avance) et la consommation du jour est dite partielle.
TIME_BUDGET_S = 20.0
#: Le cache est ignoré au-delà de cette taille.
STATE_MAX_BYTES = 4 * 1024 * 1024
#: Un journal trop gros, immobile (taille et date) depuis cette durée, cesse de peser : sa consommation reste inconnue
#: pour son jour, mais n'empêche plus l'admission des jours suivants.
SETTLE_AFTER = timedelta(hours=48)
#: Une session dont le journal reste introuvable n'est plus cherchée à chaque tick après cette durée.
GIVE_UP_AFTER = timedelta(hours=24)
MISSING_PREFIX = "journal de session introuvable"
SETTLED_REASON = "journal trop gros et immobile : la consommation au-delà du dernier total connu n'est pas mesurée"
DEFERRAL_EVENT = "quota.deferral_noted"
PENDING_REASON ="relecture en attente : le journal a changé et le budget de lecture du pas était épuisé"
OVERSIZE_REASON = "journal au-dessus du plafond de lecture : la consommation au-delà du dernier total connu n'est plus suivie"
NO_ID_REASON = "lancement sans identifiant ni condensé de session : consommation non attribuable"
_SAFE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_ACTOR = "tick"


@dataclass(frozen=True, slots=True)
class ConsumedOutcome:
    session: str
    role: str
    task_id: str
    status: str  # recorded | unknown | unchanged | skipped
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"session": self.session, "role": self.role, "task_id": self.task_id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class _Session:
    key: str
    role: str
    task_id: str
    model: str
    session_id: str
    cwd: str
    launched_at: str
    provider: str
    no_id: bool = False


def _sessions(events: list[Any]) -> dict[str, _Session]:
    found: dict[str, _Session] = {}
    for event in events:
        role = SESSION_EVENTS.get(event.event_type)
        if role is None:
            continue
        payload = event.payload
        session_id = str(payload.get("session_id") or "")
        key = session_id or str(payload.get("session_sha256") or "")
        if key.startswith(KIT_KEY_PREFIX):  # les clés « kit: » sont réservées au kit : jamais celle d'une session
            key = ""
        no_id = not key
        key = key or f"event:{event.id}"  # un lancement sans identité n'est pas compté zéro en silence
        if key in found:
            continue
        task = "" if role == "orchestrator" else event.entity_id
        found[key] = _Session(
            key, role, task, str(payload.get("model") or ""), session_id, str(payload.get("cwd") or ""),
            event.created_at, str(payload.get("provider") or ""), no_id,
        )
    return found


@dataclass(slots=True)
class _Seen:
    """Le dernier état écrit d'une session : ``known`` (total, coût) ou ``unknown`` (raison)."""

    total: int = 0
    cost: float = 0.0
    status: str = ""
    reason: str = ""
    #: Inconnu « en cours » : la session peut encore consommer (journal à relire ou trop gros).
    ongoing: bool = False


def _seen(events: list[Any]) -> dict[str, _Seen]:
    seen: dict[str, _Seen] = {}
    for event in events:
        if event.event_type != QUOTA_CONSUMED_EVENT:
            continue
        key = str(event.payload.get("session") or "")
        if not key:
            continue
        mine = seen.setdefault(key, _Seen())
        if event.payload.get("status") == "unknown":
            mine.status, mine.reason = "unknown", str(event.payload.get("reason") or "")
            mine.ongoing = bool(event.payload.get("ongoing"))
            continue
        total = event.payload.get("session_total_tokens")
        if isinstance(total, int) and not isinstance(total, bool):
            mine.total = max(mine.total, total)
            cost = event.payload.get("session_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                mine.cost = max(mine.cost, float(cost))
            mine.status, mine.reason, mine.ongoing = "known", "", False
    return seen


def tracking(events: list[Any]) -> dict[str, int]:
    """Les sessions suivies, d'après le ledger : combien, combien d'inconnues, combien en attente de relecture."""
    sessions = _sessions(events)
    seen = _seen(events)
    last = [seen.get(key, _Seen()) for key in sessions]
    return {
        "sessions": len(sessions),
        "known": sum(1 for s in last if s.status == "known"),
        "unknown": sum(1 for s in last if s.status == "unknown"),
        "pending_reread": sum(1 for s in last if s.status == "unknown" and s.reason == PENDING_REASON),
        "never_measured": sum(1 for s in last if not s.status),
    }


def _digest_of(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _locate(session: _Session, home: Path | None) -> Path | None:
    from grimoire.missions.usage_fold import locate_transcript
    from grimoire.tools.workspace_work import claude_projects_dir

    cwd = Path(session.cwd) if session.cwd else None
    if session.session_id:
        return locate_transcript(session.session_id, worktree=cwd, home=home)
    if cwd is None:
        return None
    folder = claude_projects_dir(home) / re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.glob("*.jsonl")):
        if _SAFE_RE.match(candidate.stem) and _digest_of(candidate.stem) == session.key:
            return candidate
    return None


def _measure(path: Path, root: Path, day_start: datetime) -> dict[str, Any]:
    from grimoire.providers.registry import read_model_pricing
    from grimoire.traces.transcript_usage import call_cost_usd, read_claude_transcript

    pricing_for = read_model_pricing(root).get
    read = read_claude_transcript(path)
    cost, unpriced, before, cost_before = 0.0, 0, 0, 0.0
    models: dict[str, int] = {}
    for call in read.calls:
        value = call_cost_usd(call, pricing_for(call.model) if call.model else None)
        if value is None:
            unpriced += 1
        else:
            cost += value
        stamp = parse_instant(call.timestamp) if call.timestamp else None
        if stamp is not None and stamp < day_start:  # consommé avant minuit UTC : pour la veille
            before += call.input_tokens + call.output_tokens + call.cache_read_tokens + call.cache_creation_tokens
            cost_before += value or 0.0
        if call.model:
            models[call.model] = models.get(call.model, 0) + 1
    totals = {
        "input_tokens": sum(c.input_tokens for c in read.calls),
        "output_tokens": sum(c.output_tokens for c in read.calls),
        "cache_read_tokens": sum(c.cache_read_tokens for c in read.calls),
        "cache_creation_tokens": sum(c.cache_creation_tokens for c in read.calls),
    }
    if not read.calls or unpriced == 0:
        cost_status = "exact"
    elif unpriced < len(read.calls):
        cost_status = "lower_bound"
    else:
        cost_status = "unknown"
    return {
        **totals, "total": sum(totals.values()), "calls": len(read.calls), "cost": cost, "cost_status": cost_status,
        "models": sorted(models, key=lambda m: -models[m]), "before_today": before, "cost_before_today": cost_before,
    }


def _load_state(root: Path) -> dict[str, dict[str, Any]]:
    """Le cache, lu comme fichier ordinaire seulement (ni lien, ni tube, ni périphérique), de taille bornée.

    Tout autre cas — absent, illisible, trop gros, mal formé — vaut « pas de cache » : tout est relu.
    """
    try:
        with open_regular(root / STATE_RELPATH) as handle:
            blob = handle.read(STATE_MAX_BYTES + 1)
        raw = json.loads(blob) if len(blob) <= STATE_MAX_BYTES else None
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):  # seuls taille, date et total comptent : jamais un chemin
            out[key] = {k: value[k] for k in ("size", "mtime_ns", "read_at", "total") if k in value}
    return out


def _confined(path: Path | None, home: Path | None) -> Path | None:
    """*path* s'il reste sous le dossier des journaux de l'hôte (liens résolus), sinon ``None``."""
    from grimoire.tools.workspace_work import claude_projects_dir

    if path is None:
        return None
    try:
        return path if path.resolve().is_relative_to(claude_projects_dir(home).resolve()) else None
    except OSError:
        return None


def _save_state(root: Path, state: dict[str, dict[str, Any]]) -> None:
    try:
        atomic_write(root / STATE_RELPATH, json.dumps(state, sort_keys=True))
    except OSError:  # un cache perdu coûte une relecture, jamais un chiffre
        return


def record_consumption(
    service: TaskService, *, home: Path | None = None, now: datetime | None = None,
    max_sessions: int = MAX_SESSIONS, max_bytes: int = MAX_BYTES, time_budget: float = TIME_BUDGET_S,
) -> list[ConsumedOutcome]:
    """Écrit ``quota.consumed`` pour chaque session lancée dont le journal a grandi (idempotent, borné, tournant)."""
    ledger = service.ledger
    events = ledger.list_events()
    sessions = _sessions(events)
    seen = _seen(events)
    moment = now or datetime.now(UTC)
    root = service.home_root
    state = _load_state(root)
    before_state = json.dumps(state, sort_keys=True)
    outcomes: list[ConsumedOutcome] = []
    kept: set[str] = set()
    pending: list[tuple[str, str, _Session, Path, tuple[int, int]]] = []

    def unknown(session: _Session, reason: str, label: str = "unknown", *, ongoing: bool = False) -> None:
        """Un seul événement par changement d'état réel : le même état ne s'écrit pas deux fois.

        *ongoing* : la session peut encore consommer (journal à relire ou trop gros) ; l'inconnu pèse alors, sans limite
        de durée, jusqu'à ce que le journal soit relu ou reste immobile (un événement ``ongoing: false`` l'acte).
        """
        mine = seen.get(session.key, _Seen())
        if mine.status == "unknown" and mine.reason == reason and mine.ongoing == ongoing:
            outcomes.append(ConsumedOutcome(session.key[:12], session.role, session.task_id, label if label != "unknown" else "unchanged", reason))
            return
        ledger.append_event(
            QUOTA_CONSUMED_EVENT, session.task_id or session.role, "task", _ACTOR,
            {"session": session.key, "role": session.role, "task_id": session.task_id, "model": session.model,
             "status": "unknown", "reason": reason, "tokens": None, "cost_usd": None, "cost_status": "unknown",
             "ongoing": ongoing},
        )
        seen[session.key] = _Seen(mine.total, mine.cost, "unknown", reason, ongoing)
        outcomes.append(ConsumedOutcome(session.key[:12], session.role, session.task_id, label, reason))

    for key, session in sessions.items():
        mine = seen.get(key, _Seen())
        launched = parse_instant(session.launched_at)
        entry = state.get(key, {})
        if session.no_id:
            unknown(session, NO_ID_REASON)
            continue
        if mine.status == "known" and entry and launched is not None and moment - launched > _ANCIENT:
            continue  # connue, immobile, vieille de plus de 30 jours : plus même examinée (et élaguée du cache)
        kept.add(key)
        age = None if launched is None else moment - launched
        if mine.status == "unknown" and mine.reason.startswith(MISSING_PREFIX) and age is not None and age > GIVE_UP_AFTER:
            outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged", "journal introuvable, plus cherché"))
            continue  # restée inconnue pour son jour, mais plus recherchée à chaque tick
        # Le chemin n'est JAMAIS lu du cache : toujours recalculé, et confiné au dossier des journaux de l'hôte.
        path = _confined(_locate(session, home), home)
        if path is None:
            if mine.status == "unknown" and mine.ongoing:  # le journal a disparu : plus rien ne peut grossir
                unknown(session, SETTLED_REASON)
                continue
            young = age is not None and age < GRACE
            if young or mine.status == "known":
                outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged", "journal absent"))
                continue
            host = f" ({session.provider})" if session.provider else ""
            unknown(session, f"{MISSING_PREFIX} ou d'un hôte non lu{host}")
            continue
        try:
            info = path.stat()
        except OSError as exc:
            unknown(session, f"journal illisible ({type(exc).__name__})")
            continue
        stamp = (info.st_size, info.st_mtime_ns)
        # Le cache ne dispense de lire que si son stamp est celui du fichier réel ET le total qu'il a vu est celui du
        # ledger, pour une session que le ledger dit connue (ou trop grosse) : un cache faux donne une relecture.
        trusted = mine.status == "known" or (mine.status == "unknown" and mine.reason in (OVERSIZE_REASON, SETTLED_REASON))
        if trusted and entry.get("size") == stamp[0] and entry.get("mtime_ns") == stamp[1] and entry.get("total") == mine.total:
            if mine.ongoing and moment - datetime.fromtimestamp(info.st_mtime, UTC) >= SETTLE_AFTER:
                unknown(session, SETTLED_REASON)  # immobile depuis la fenêtre : cesse de peser, un seul événement
            else:
                outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged"))
            continue  # rien n'a changé depuis la dernière lecture : aucun budget
        if stamp[0] > max_bytes:
            unknown(session, OVERSIZE_REASON, ongoing=True)  # la décision suit l'état du fichier, pas des réécritures
            state[key] = {"size": stamp[0], "mtime_ns": stamp[1], "read_at": moment.isoformat(), "total": mine.total}
            continue
        pending.append((str(entry.get("read_at") or ""), key, session, path, stamp))

    pending.sort(key=lambda item: item[0])  # la dernière lecture la plus ancienne d'abord (« jamais » en tête)
    day_start = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    deadline = time.monotonic() + time_budget
    for index, (_read_at, key, session, path, stamp) in enumerate(pending):
        if index >= max_sessions or (index > 0 and time.monotonic() > deadline):
            unknown(session, PENDING_REASON, "skipped", ongoing=True)
            continue
        mine = seen.get(key, _Seen())
        try:
            measure = _measure(path, root, day_start)
        except OSError as exc:
            unknown(session, f"journal illisible ({type(exc).__name__})")
            continue
        total = int(measure["total"])
        state[key] = {"size": stamp[0], "mtime_ns": stamp[1], "read_at": moment.isoformat(), "total": max(total, mine.total)}
        if total <= mine.total and mine.status != "unknown":
            outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged"))
            continue
        _record(service, session, measure, mine, total, day_start)
        outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "recorded", f"+{max(total - mine.total, 0)} tokens"))
        seen[key] = _Seen(max(total, mine.total), measure["cost"], "known", "")
    state = {key: value for key, value in state.items() if key in kept}  # les sessions sorties du suivi quittent le cache
    if json.dumps(state, sort_keys=True) != before_state:
        _save_state(root, state)
    return outcomes


def note_deferral(service: TaskService, plan: QueuePlan, *, apply: bool, attention: list[str]) -> None:
    """Dit, une fois par jour et par cause, que la consommation inconnue ou partielle diffère les lancements autonomes."""
    cause = ""
    for deferred in plan.deferred:
        if deferred.reason == "unknown" and deferred.detail.startswith("consommation du jour"):
            cause = "partial" if "partiellement" in deferred.detail else "unknown"
            break
    if not cause:
        return
    day = plan.now.astimezone(UTC).date().isoformat()
    for event in service.ledger.list_events():
        if event.event_type == DEFERRAL_EVENT and event.payload.get("day") == day and event.payload.get("cause") == cause:
            return
    waiting = max(plan.view.tokens.unknown_sessions, 1)
    what = "partiellement inconnue" if cause == "partial" else "inconnue"
    attention.append(
        f"quota : lancements différés, consommation {what}, {waiting} session(s) à relire. Pour en sortir : les prochains "
        "ticks relisent les journaux ; sinon un humain règle `quota.on_unknown: count_known` dans capacity.yaml "
        "(le connu seul compte), ou relève le plafond."
    )
    if apply:
        service.ledger.append_event(DEFERRAL_EVENT, "project", "task", _ACTOR, {"day": day, "cause": cause})


def _record(service: TaskService, session: _Session, measure: dict[str, Any], mine: _Seen, total: int, day_start: datetime) -> None:
    """Écrit le delta de la session ; la part consommée avant minuit UTC est datée de la veille."""
    delta = max(total - mine.total, 0)
    cost_known = measure["cost_status"] != "unknown"
    delta_cost = max(measure["cost"] - mine.cost, 0.0) if cost_known else None
    old = max(0, min(delta, int(measure["before_today"]) - mine.total))
    yesterday = (day_start - timedelta(days=1)).date().isoformat()
    parts = [(old, yesterday, mine.total + old)] if old else []
    parts.append((delta - old, "", total))
    for tokens, day, cumulative in parts:
        if tokens == 0 and old:
            continue
        share = None if delta_cost is None else (delta_cost * tokens / delta if delta else 0.0)
        service.ledger.append_event(
            QUOTA_CONSUMED_EVENT, session.task_id or session.role, "task", _ACTOR,
            {"session": session.key, "role": session.role, "task_id": session.task_id,
             "model": (measure["models"] or [session.model])[0], "models": measure["models"],
             "status": "lower_bound", "tokens": tokens, "session_total_tokens": cumulative,
             "input_tokens": measure["input_tokens"], "output_tokens": measure["output_tokens"],
             "cache_read_tokens": measure["cache_read_tokens"], "cache_creation_tokens": measure["cache_creation_tokens"],
             "calls": measure["calls"], "cost_usd": share, "session_cost_usd": measure["cost"] if cost_known else None,
             "cost_status": measure["cost_status"], **({"day": day} if day else {})},
        )


# ── Les appels de modèle du kit lui-même (hors sessions) ───────────────────────

#: Les origines que le kit peut inscrire : une liste fermée, choisie ici (jamais une valeur venue d'un appelant externe).
KIT_ORIGINS = ("atelier",)
_KIT_CALL_ID = re.compile(r"(?!.*\.\.)[A-Za-z0-9._:-]{1,128}")
_KIT_SLUG = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: Lettres, chiffres, ``-``, ``_``, ``.`` isolé, ``:`` ; ni ``/`` ni suite ``..`` ; 64 caractères au plus.
_KIT_MODEL = re.compile(r"(?!.*\.\.)[A-Za-z0-9._:-]{1,64}")
#: Au-delà, un total de jetons déclaré pour un seul appel n'est pas plausible : refusé, jamais borné en silence.
MAX_KIT_TOKENS = 10_000_000


def _kit_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_KIT_TOKENS else None


def record_kit_call(
    root: Path, *, origin: str, call_id: str, model: str, input_tokens: int, output_tokens: int, chantier: str = "",
) -> str:
    """Inscrit la consommation CONNUE d'un appel de modèle que le kit a lui-même facturé (``quota.consumed``).

    Appelée par le code du kit après une réponse facturée, jamais par un outil MCP ni une commande : ``origin`` est
    l'une de :data:`KIT_ORIGINS`, les jetons sont ceux que la réponse déclare. Idempotente : la clé est
    ``kit:<origine>:<call_id>``, rejouer le même appel (même ``call_id``) n'écrit rien ; le contrôle d'existence et
    l'écriture se font sous le verrou du ledger, et la lecture ne compte une clé du kit qu'une fois. Le
    ``call_id`` est celui de l'APPEL local (jamais le seul identifiant d'une réponse distante).

    Rend l'issue : ``recorded``, ``duplicate`` (déjà inscrit), ``refused`` (valeur hors forme), ``no_ledger`` (projet
    sans ledger de missions) ou ``error`` (l'écriture a échoué : l'appelant le dit). Rien d'autre n'est écrit.
    La consommation entre dans la fiche Capacité et les règles d'usage sans jamais rendre « connue » une journée dont
    des sessions sont sans mesure ; le compteur local du client reste distinct et en vigueur.
    """
    from grimoire.missions.ledger_files import ledger_file_lock
    from grimoire.missions.service import TaskService

    tokens_in, tokens_out = _kit_count(input_tokens), _kit_count(output_tokens)
    if (
        origin not in KIT_ORIGINS or tokens_in is None or tokens_out is None
        or not isinstance(call_id, str) or not _KIT_CALL_ID.fullmatch(call_id)
        or not isinstance(model, str) or not _KIT_MODEL.fullmatch(model)
    ):
        return "refused"
    slug = chantier if isinstance(chantier, str) and _KIT_SLUG.fullmatch(chantier) else ""
    try:
        service = TaskService(root)
        if not service.has_ledger:
            return "no_ledger"
        key = f"{KIT_KEY_PREFIX}{origin}:{call_id}"
        from grimoire.providers.registry import read_model_pricing
        from grimoire.traces.transcript_usage import UsageCall, call_cost_usd

        cost = call_cost_usd(
            UsageCall(call_id, model, tokens_in, tokens_out, 0, 0, 0, 0, "", "", ""), read_model_pricing(root).get(model)
        )
        ledger = service.ledger
        payload = {
            "session": key, "role": origin, "task_id": "", "chantier": slug, "model": model, "status": "lower_bound",
            "tokens": tokens_in + tokens_out, "session_total_tokens": tokens_in + tokens_out,
            "input_tokens": tokens_in, "output_tokens": tokens_out, "calls": 1, "cost_usd": cost,
            "session_cost_usd": cost, "cost_status": "exact" if cost is not None else "unknown",
        }
        with ledger_file_lock(ledger._events_path):  # le contrôle et l'écriture sous le même verrou que `append_event`
            ledger._invalidate()
            ledger._load()
            if any(e.event_type == QUOTA_CONSUMED_EVENT and e.payload.get("session") == key for e in ledger.list_events()):
                return "duplicate"
            ledger._append_event(QUOTA_CONSUMED_EVENT, f"kit-{origin}", "task", _ACTOR, payload, lock_held=True)
    except Exception:  # inscrire ne doit jamais faire échouer le geste déjà facturé : l'appelant le dit (``error``)
        return "error"
    return "recorded"


def kit_call_refusal(root: Path, *, now: datetime | None = None) -> str:
    """Le motif pour lequel un geste modèle du kit est refusé (``""`` : il peut partir). Lecture seule, échec fermé.

    Lit ce que l'admission lit (:func:`~grimoire.missions.admission.load_capacity` et le ledger) : une règle d'usage de
    portée ``project`` ou ``day`` franchie dont l'action est ``hold`` ou ``pause`` refuse le geste. ``warn`` ne retient
    rien ; une règle de portée ``task`` ne vise que des tâches, pas un geste d'Atelier. ``capacity.yaml`` illisible, ou
    le ledger illisible alors que des règles existent : refusé (échec fermé).
    """
    from grimoire.missions.admission import load_capacity
    from grimoire.missions.service import TaskService
    from grimoire.missions.usage_rules import describe, evaluate

    try:
        capacity = load_capacity(root)
        if capacity.errors.get("*"):
            return "capacity.yaml illisible : geste du modèle refusé (échec fermé), un humain le répare"
        if not capacity.usage_rules:
            return ""
        service = TaskService(root)
        events = service.ledger.list_events() if service.has_ledger else []
        statuses = evaluate(capacity.usage_rules, events, now or datetime.now(UTC))
    except Exception:
        return "règles d'usage illisibles : geste du modèle refusé (échec fermé)"
    for status in statuses:
        rule = status.rule
        if rule.enabled and status.crossed and rule.action in ("hold", "pause") and rule.scope != "task":
            return (
                f"{describe(status)} : geste du modèle refusé. Pour lever : un humain relève le seuil ou met "
                "`enabled: false` sur cette règle dans capacity.yaml (la portée jour se lève à minuit UTC)."
            )
    return ""
