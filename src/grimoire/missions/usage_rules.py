"""Les règles d'usage activables de ``capacity.yaml`` (``usage_rules:``), appliquées à l'admission seulement.

Une règle dit : « quand cette mesure franchit ce seuil, fais cela ».

.. code-block:: yaml

    usage_rules:
      - {id: jour-gros, scope: day, metric: tokens, threshold: 2000000, action: warn, enabled: true}
      - {id: tache-chere, scope: task, metric: usd, threshold: 5, action: hold}
      - {id: trop-de-sessions, scope: project, metric: sessions, threshold: 200, action: pause}

* ``scope`` : ``task`` (une tâche, tous ses lancements), ``project`` (depuis le début), ``day`` (aujourd'hui, UTC).
  La portée « session » est déjà couverte par les politiques ``per_session`` : pas ici.
* ``metric`` : ``tokens`` (total que ``grimoire task usage`` rapporte), ``usd`` (coût connu), ``sessions`` (lancées).
* ``action`` : ``warn`` (une ligne dans la liste d'attention), ``hold`` (le lancement est différé tant que la règle est
  franchie : ``Defer(usage_rule)``, une ligne d'attention dit laquelle et comment lever), ``pause`` (pose la pause
  existante en mode ``drain`` avec la règle pour motif ; elle ne se lève pas d'elle-même, sauf portée ``day`` au
  changement de jour). Une règle ne tue jamais une session en cours : elle ne parle qu'au prochain lancement.

La consommation vient de ``quota.consumed`` (:mod:`.quota_usage`) : un minimum. Une règle franchie sur le connu l'est
pour de bon ; une règle sous son seuil sur une consommation partiellement inconnue le dit (``complete: false``). Le coût
inconnu n'est jamais compté zéro.

Une règle invalide ne plante rien : elle est signalée (``issues``) et ignorée. Le fichier illisible, lui, reste fermé :
c'est ``capacity.errors['*']``, que l'admission lit déjà. Personne ne relève un plafond ni ne désactive une règle depuis
le kit : seul l'humain édite ``capacity.yaml``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.lease import parse_instant

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "ACTIONS",
    "HOLD_EVENT",
    "METRICS",
    "ONGOING_WINDOW",
    "PAUSE_ORIGIN",
    "QUOTA_CONSUMED_EVENT",
    "SCOPES",
    "SESSION_EVENTS",
    "RuleStatus",
    "UsageRule",
    "apply_effects",
    "describe",
    "evaluate",
    "event_day",
    "lift_day_pauses",
    "parse_rules",
    "rule_dict",
]

#: Des tokens consommés : la seule source de la consommation (écrite par :mod:`.quota_usage`).
QUOTA_CONSUMED_EVENT = "quota.consumed"
#: Événement de lancement -> rôle. Valeurs de ``launcher``, ``reviewer``, ``arbiter``, ``orchestrator_session``
#: (un test l'affirme), littérales ici pour ne pas importer les modules du tick.
SESSION_EVENTS: dict[str, str] = {
    "task.session_launched": "worker",
    "task.review_launched": "reviewer",
    "task.arbitration_launched": "arbiter",
    "orchestrator.session_launched": "orchestrator",
}
#: Un lancement différé par une règle ``hold`` (une fois par règle, tâche et jour).
HOLD_EVENT = "usage_rule.hold"
SCOPES = ("task", "project", "day")
METRICS = ("tokens", "usd", "sessions")
ACTIONS = ("warn", "pause", "hold")
_KEYS = frozenset({"id", "scope", "metric", "threshold", "action", "enabled"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ACTOR = "usage-rule"
_PAUSE_ACTOR_PREFIX = "usage-rule:"
#: L'origine que le drapeau d'une pause posée par une règle porte : ce n'est ni ``human`` ni ``agent`` (qu'un canal
#: d'appel pose), seule :func:`apply_effects` l'écrit et seule :func:`lift_day_pauses` la reconnaît. ``--actor`` n'y change rien.
PAUSE_ORIGIN = "usage-rule"
_CAPACITY_FILE = "_grimoire/standard/capacity.yaml"


@dataclass(frozen=True, slots=True)
class UsageRule:
    id: str
    scope: str
    metric: str
    threshold: float
    action: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RuleStatus:
    """Une règle face à la consommation : ``scope_key`` est la tâche d'une règle de portée ``task``, sinon ``""``."""

    rule: UsageRule
    value: float
    complete: bool
    crossed: bool
    scope_key: str = ""

    @property
    def state(self) -> str:
        if not self.rule.enabled:
            return "désactivée"
        if self.crossed:
            return "franchie"
        return "sous le seuil" if self.complete else "sous le seuil (consommation partiellement inconnue)"


def rule_dict(rule: UsageRule) -> dict[str, Any]:
    return {
        "id": rule.id, "scope": rule.scope, "metric": rule.metric, "threshold": rule.threshold, "action": rule.action,
        "enabled": rule.enabled,
    }


def _threshold(value: Any, metric: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"threshold doit être un nombre fini strictement positif, pas {value!r}")
    if metric != "usd" and (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"threshold de {metric} doit être un entier, pas {value!r}")
    return float(value)


def _one(entry: Any, seen: set[str]) -> UsageRule:
    if not isinstance(entry, Mapping):
        raise ValueError(f"une règle est une table, pas {entry!r}")
    unknown = sorted(str(k) for k in entry if k not in _KEYS)
    if unknown:
        raise ValueError(f"clé inconnue {', '.join(unknown)} (admises : {', '.join(sorted(_KEYS))})")
    rid = entry.get("id")
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        raise ValueError(f"id doit être un identifiant (lettres, chiffres, . _ -), pas {rid!r}")
    if rid in seen:
        raise ValueError(f"id {rid!r} déjà pris par une règle plus haute")
    for name, allowed in (("scope", SCOPES), ("metric", METRICS), ("action", ACTIONS)):
        if entry.get(name) not in allowed:
            raise ValueError(f"{name} doit valoir {' | '.join(allowed)}, pas {entry.get(name)!r}")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"enabled doit être un booléen, pas {enabled!r}")
    return UsageRule(rid, entry["scope"], entry["metric"], _threshold(entry.get("threshold"), entry["metric"]), entry["action"], enabled)


def parse_rules(raw: Any) -> tuple[tuple[UsageRule, ...], tuple[str, ...]]:
    """``(règles valides, problèmes)`` : une règle invalide est signalée et ignorée, les autres tiennent."""
    if raw is None:
        return (), ()
    if not isinstance(raw, list):
        return (), (f"usage_rules doit être une liste de règles, pas {raw!r} : toutes ignorées",)
    rules: list[UsageRule] = []
    issues: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, 1):
        try:
            rule = _one(entry, seen)
        except ValueError as exc:
            name = entry.get("id") if isinstance(entry, Mapping) else None
            issues.append(f"usage_rules[{index}]{f' ({name})' if isinstance(name, str) else ''} ignorée : {exc}")
            continue
        seen.add(rule.id)
        rules.append(rule)
    return tuple(rules), tuple(issues)


# ── Mesure ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Record:
    day: str
    task: str
    key: str
    kind: str  # consumed | unknown | launch
    tokens: int
    cost: float | None


def event_day(payload: Mapping[str, Any], written: str) -> str:
    """Le jour (UTC) auquel un ``quota.consumed`` se compte : son ``day`` (consommé avant minuit), sinon son écriture."""
    day = payload.get("day")
    return day if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else written


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


#: Un « inconnu en cours » (journal à relire, trop gros) compte pour le jour courant tant qu'il date de moins de cela.
ONGOING_WINDOW = timedelta(hours=48)


def unknown_counts_today(payload: Mapping[str, Any], stamp: datetime | None, now: datetime) -> bool:
    """Un ``quota.consumed`` inconnu pèse sur *now* : écrit aujourd'hui, ou « en cours » et récent."""
    if stamp is None:
        return False
    if stamp.astimezone(UTC).date() == now.astimezone(UTC).date():
        return True
    return bool(payload.get("ongoing")) and now - stamp <= ONGOING_WINDOW


def _records(events: Iterable[Any], now: datetime) -> list[_Record]:
    out: list[_Record] = []
    today = now.astimezone(UTC).date().isoformat()
    for event in events:
        payload = event.payload
        day = str(event.created_at)[:10]
        if event.event_type == QUOTA_CONSUMED_EVENT:
            key = str(payload.get("session") or event.id)
            task = str(payload.get("task_id") or "")
            if payload.get("status") == "unknown":
                if unknown_counts_today(payload, parse_instant(str(event.created_at)), now):
                    day = today
                out.append(_Record(day, task, key, "unknown", 0, None))
                continue
            day = event_day(payload, day)
            tokens = payload.get("tokens")
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                out.append(_Record(day, task, key, "consumed", tokens, _num(payload.get("cost_usd"))))
        elif event.event_type in SESSION_EVENTS:
            key = str(payload.get("session_id") or payload.get("session_sha256") or event.id)
            task = "" if SESSION_EVENTS[event.event_type] == "orchestrator" else str(event.entity_id)
            out.append(_Record(day, task, key, "launch", 0, None))
    return out


def _measure(rule: UsageRule, records: list[_Record]) -> tuple[float, bool]:
    """``(valeur connue, complète ?)`` de la mesure de *rule* sur *records*."""
    if rule.metric == "sessions":
        return float(len({r.key for r in records if r.kind == "launch"})), True
    last: dict[str, str] = {}
    for r in records:  # chronologique : le dernier état d'une session fait foi (un inconnu récent défait un connu ancien)
        if r.kind in ("consumed", "unknown"):
            last[r.key] = r.kind
    unknown = {key for key, kind in last.items() if kind == "unknown"}
    consumed = [r for r in records if r.kind == "consumed"]
    if rule.metric == "tokens":
        return float(sum(r.tokens for r in consumed)), not unknown
    costs = [r.cost for r in consumed]
    return sum(c for c in costs if c is not None), not unknown and all(c is not None for c in costs)


def evaluate(rules: Iterable[UsageRule], events: Iterable[Any], now: datetime) -> tuple[RuleStatus, ...]:
    """Chaque règle face à la consommation. Une règle désactivée n'est pas mesurée (état ``désactivée``)."""
    records = _records(events, now)
    today = now.astimezone(UTC).date().isoformat()
    out: list[RuleStatus] = []
    for rule in rules:
        if not rule.enabled:
            out.append(RuleStatus(rule, 0.0, True, False))
            continue
        if rule.scope == "task":
            tasks = sorted({r.task for r in records if r.task})
            statuses = [(task, [r for r in records if r.task == task]) for task in tasks] or [("", [])]  # la règle se lit même sans tâche
        else:
            scoped = records if rule.scope == "project" else [r for r in records if r.day == today]
            statuses = [("", scoped)]
        for key, subset in statuses:
            value, complete = _measure(rule, subset)
            out.append(RuleStatus(rule, value, complete, value >= rule.threshold, key))
    return tuple(out)


def consumption_today(events: Iterable[Any], now: datetime) -> dict[str, Any]:
    """La consommation du jour (UTC), en borne basse : ``complete`` dit si tout est connu (jamais un inconnu compté zéro)."""
    today = now.astimezone(UTC).date().isoformat()
    records = [r for r in _records(events, now) if r.day == today]
    out: dict[str, Any] = {"day": today}
    for metric in METRICS:
        value, complete = _measure(UsageRule("-", "day", metric, 1.0, "warn"), records)
        out[metric] = value if metric == "usd" else int(value)
        out[f"{metric}_complete"] = complete
    return out


def rules_report(
    rules: Iterable[UsageRule], issues: Iterable[str], events: Iterable[Any], now: datetime
) -> dict[str, Any]:
    """Ce que ``grimoire queue rules --json`` et la fiche Capacité du cockpit montrent : lecture seule."""
    from grimoire.missions.quota_usage import tracking

    events = list(events)
    return {
        "consumption_today": consumption_today(events, now),
        "tracking": tracking(events),
        "rules": [
            {**rule_dict(s.rule), "state": s.state, "value": s.value, "complete": s.complete, "crossed": s.crossed,
             "scope_key": s.scope_key}
            for s in evaluate(rules, events, now)
        ],
        "issues": list(issues),
    }


def _num_text(value: float, metric: str) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if metric == "usd" else str(int(value))


def describe(status: RuleStatus) -> str:
    rule = status.rule
    where = f"{rule.scope}{f' {status.scope_key}' if status.scope_key else ''}"
    unit = " USD" if rule.metric == "usd" else ""
    return (
        f"règle d'usage {rule.id} ({rule.action}) {status.state} : {rule.metric} {_num_text(status.value, rule.metric)}{unit}"
        f" / seuil {_num_text(rule.threshold, rule.metric)}{unit} (portée {where}, borne basse)"
    )


# ── Effets du tick ─────────────────────────────────────────────────────────────


def _hold_lift(status: RuleStatus) -> str:
    rule = status.rule
    base = f"Pour lever : un humain relève le seuil de {rule.id} ou y met `enabled: false` dans {_CAPACITY_FILE} (aucun agent ne le fait)"
    return f"{base} ; la portée jour se lève d'elle-même à minuit UTC." if rule.scope == "day" else f"{base}."


def apply_effects(
    service: TaskService, statuses: Iterable[RuleStatus], *, apply: bool, now: datetime, attention: list[str],
    task_ids: Iterable[str] = (),
) -> None:
    """Les effets des règles franchies : lignes d'attention, événement ``hold``, pause ``drain`` (apply seulement).

    *task_ids* : les tâches que ce tick aurait lancées ; une règle de portée ``task`` ne parle que d'elles.
    Une pause déjà posée (humaine ou autre) n'est jamais écrasée : la règle se contente de le dire.
    """
    from grimoire.missions.pause import DRAIN, SCOPE_PROJECT, PauseError, home_root, read_pause, write_pause

    wanted = set(task_ids)
    today = now.astimezone(UTC).date().isoformat()
    posed: set[str] = set()
    journaled = {
        (e.payload.get("rule"), e.payload.get("task_id"), e.payload.get("day"))
        for e in service.ledger.list_events() if e.event_type == HOLD_EVENT
    }
    for status in statuses:
        rule = status.rule
        if not rule.enabled or not status.crossed or (rule.scope == "task" and status.scope_key not in wanted):
            continue
        line = describe(status)
        if rule.action == "warn":
            attention.append(line)
        elif rule.action == "hold":
            attention.append(f"{line} : lancement différé. {_hold_lift(status)}")
            mark = (rule.id, status.scope_key, today)
            if apply and mark not in journaled:
                journaled.add(mark)
                service.ledger.append_event(
                    HOLD_EVENT, status.scope_key or "project", "task", _ACTOR,
                    {"rule": rule.id, "scope": rule.scope, "metric": rule.metric, "value": status.value,
                     "threshold": rule.threshold, "task_id": status.scope_key, "day": today},
                )
        elif rule.action == "pause":
            if f"{rule.id}:{status.scope_key}" in posed:
                continue
            posed.add(f"{rule.id}:{status.scope_key}")
            root = home_root(service.home_root)
            tasks = (status.scope_key,) if rule.scope == "task" else ()  # une règle de tâche ne met pas le projet en pause
            if read_pause(root).flags:
                attention.append(f"{line} : une pause est déjà posée, elle n'est pas écrasée (la règle signale seulement)")
            elif not apply:
                attention.append(f"{line} : une pause drain serait posée")
            else:
                try:
                    write_pause(
                        root, mode=DRAIN, scope=SCOPE_PROJECT, tasks=tasks,
                        actor=f"{_PAUSE_ACTOR_PREFIX}{rule.scope}:{rule.id}", reason=line, origin=PAUSE_ORIGIN, now=now,
                    )
                except PauseError as exc:
                    attention.append(f"{line} : pause non posée ({exc})")
                    continue
                lift = "`grimoire orchestrate resume` (geste humain)"
                tail = "levée d'elle-même au changement de jour (UTC), ou par " if rule.scope == "day" else "levée par "
                attention.append(f"{line} : pause drain posée ({tail}{lift})")


def lift_day_pauses(project_root: Path, now: datetime) -> bool:
    """Lève la pause qu'une règle de portée ``day`` a posée un jour précédent ; ``True`` si elle l'a levée.

    Seule une pause d'origine :data:`PAUSE_ORIGIN` ET d'acteur ``usage-rule:day:<id>`` est concernée : l'acteur seul
    (une option ``--actor`` libre) ne suffit pas. Une pause humaine, ou posée par une règle de portée ``project`` ou
    ``task``, ne se lève jamais d'elle-même.
    """
    from grimoire.missions.pause import SCOPE_PROJECT, clear_pause, home_root, project_pause_path, read_flag

    root = home_root(project_root)
    flag = read_flag(project_pause_path(root), SCOPE_PROJECT)
    if (
        flag is None or flag.unreadable or flag.tasks or flag.origin != PAUSE_ORIGIN
        or not flag.actor.startswith(f"{_PAUSE_ACTOR_PREFIX}day:")
    ):
        return False
    since = flag.since[:10]
    if not since or since >= now.astimezone(UTC).date().isoformat():
        return False
    clear_pause(root, scope=SCOPE_PROJECT)
    return True


def next_midnight(now: datetime) -> str:
    moment = now.astimezone(UTC)
    return (moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
