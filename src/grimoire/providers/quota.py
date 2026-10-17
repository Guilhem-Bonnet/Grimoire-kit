"""Lecture des signaux de forfait d'un fournisseur : quota épuisé, surcharge, fenêtre.

Trois situations que le refroidissement unique (``state.record_failure``) mêlait :

* **quota** : le forfait ou la limite est épuisé. Le fournisseur n'est pas
  « occupé », il est fermé jusqu'à une date, parfois connue (``resets at 3pm``,
  ``Retry-After``, ``resetsAt`` du flux ``stream-json``), parfois non. Une date
  illisible se rend ``unknown`` : on ne devine pas, le fournisseur reste exclu
  jusqu'à un ``providers probe``.
* **overloaded** (HTTP 529) : la plateforme est saturée, pas le forfait. C'est un
  refroidissement court qui double, jamais une suspension de forfait.
* **rate_limit** : un 429 sans date ni mot de forfait, refroidissement classique.

Ce que l'on sait du flux de l'hôte Claude Code (``tests/fixtures/hosts/claude-code/
03-quota-signal.json``) : une ligne ``rate_limit_event`` porte ``status``,
``resetsAt`` en secondes epoch et ``unifiedWindows.*.utilization``. Elle n'a été
vue qu'à l'état ``allowed``. Le payload d'un forfait épuisé n'a pas été
capturé : tout statut autre que ``allowed``/``allowed_warning`` est donc traité
comme un refus, et une date absente ou passée donne ``unknown`` au lieu d'être
présumée.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

#: Statuts de ``rate_limit_event`` qui ne signalent aucun refus (vus ou
#: raisonnablement attendus). Tout autre statut est un refus.
_BENIGN_STATUSES = frozenset({"allowed", "allowed_warning"})

#: Mots d'un forfait épuisé (au sens large) : la date, si elle existe, décide
#: de la suspension ; sans date, la suspension est ``unknown``.
_QUOTA_MARKERS = ("quota", "usage limit", "limit reached", "out of credits", "plan limit")
#: Mots d'une limite de débit : refroidissement, sauf si une date est lue.
_RATE_MARKERS = ("429", "rate limit", "rate_limit", "too many requests")
_OVERLOAD_MARKERS = ("529", "overloaded")

_MAX_SUSPENSION = timedelta(days=366)

_RESETS_AT = re.compile(
    r"resets?\s*(?:at|on)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)
_RESETS_IN = re.compile(r"resets?\s+in\s+(\d+)\s*(minute|hour|day)s?\b", re.IGNORECASE)
_RETRY_AFTER = re.compile(r"retry-after\s*[:=]\s*([^\r\n]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class QuotaSignal:
    """Ce qu'une sortie de fournisseur dit de sa disponibilité.

    ``kind`` : ``quota`` | ``overloaded`` | ``rate_limit``. ``until`` et
    ``source`` (``resets_at`` | ``retry_after`` | ``unknown``) ne valent que pour
    ``quota`` et ``rate_limit`` ; ``source == "unknown"`` implique ``until is
    None``. ``model_hint`` : fragment de nom de modèle quand le flux borne la
    limite à un modèle (``seven_day_opus`` → ``opus``).
    """

    kind: str
    until: datetime | None = None
    source: str = "unknown"
    model_hint: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaObservation:
    """Une lecture de fenêtre de forfait : un **minorant** de la consommation."""

    window: str
    utilization: float
    resets_at: datetime | None
    observed_at: datetime


def _epoch(value: Any, *, now: datetime) -> datetime | None:
    """Date d'un ``resetsAt`` en secondes epoch ; ``None`` si illisible ou passée."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if seconds > 10**11:  # millisecondes : tolérance, la fixture est en secondes
        seconds /= 1000.0
    try:
        moment = datetime.fromtimestamp(seconds, tz=now.tzinfo)
    except (OverflowError, OSError, ValueError):
        return None
    return moment if moment > now else None


def _rate_limit_events(stdout: str) -> tuple[list[dict[str, Any]], str]:
    """Les objets ``rate_limit_event`` du flux, et le texte sans ces lignes."""
    events: list[dict[str, Any]] = []
    kept: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and "rate_limit_event" in stripped:
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("type") == "rate_limit_event":
                events.append(parsed)
                continue
        kept.append(line)
    return events, "\n".join(kept)


def _hint_from_type(rate_limit_type: Any) -> str | None:
    if isinstance(rate_limit_type, str):
        for prefix in ("seven_day_", "weekly_"):
            if rate_limit_type.startswith(prefix) and len(rate_limit_type) > len(prefix):
                return rate_limit_type[len(prefix) :].lower()
    return None


def _from_events(events: list[dict[str, Any]], *, now: datetime) -> QuotaSignal | None:
    for event in events:
        info = event.get("rate_limit_info")
        if not isinstance(info, dict):
            continue
        status = info.get("status")
        if isinstance(status, str) and status in _BENIGN_STATUSES:
            continue
        until = _epoch(info.get("resetsAt"), now=now)
        return QuotaSignal(
            "quota",
            until,
            "resets_at" if until is not None else "unknown",
            _hint_from_type(info.get("rateLimitType")),
        )
    return None


def parse_resets_at(text: str, *, now: datetime) -> datetime | None:
    """« resets at 3pm » → la prochaine occurrence de 15:00 dans le fuseau de *now*."""
    relative = _RESETS_IN.search(text)
    if relative:
        unit = {"minute": 60, "hour": 3600, "day": 86400}[relative.group(2).lower()]
        return now + timedelta(seconds=int(relative.group(1)) * unit)
    match = _RESETS_AT.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    meridiem = (match.group(3) or "").lower()
    if not meridiem and match.group(2) is None:
        return None  # « resets 30 » : ni heure du jour ni minutes, on ne devine pas
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now else moment + timedelta(days=1)


def parse_retry_after(text: str, *, now: datetime) -> datetime | None:
    """``Retry-After: 120`` (secondes) ou une date HTTP ; ``None`` si illisible."""
    match = _RETRY_AFTER.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    digits = re.match(r"\d+", raw)
    if digits and digits.group(0) == raw.split()[0]:
        return now + timedelta(seconds=min(int(digits.group(0)), int(_MAX_SUSPENSION.total_seconds())))
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return moment if moment > now else None


def classify_failure(stdout: str, stderr: str, *, now: datetime) -> QuotaSignal | None:
    """Lit stdout/stderr d'un appel ; ``None`` quand rien n'évoque un refus.

    Les lignes ``rate_limit_event`` du flux sont jugées sur leur ``status`` et
    retirées du texte avant la recherche de mots : le nom de l'événement
    contient lui-même ``rate_limit`` et faisait passer pour limité un appel
    pourtant ``allowed``.
    """
    events, remaining_stdout = _rate_limit_events(stdout)
    from_stream = _from_events(events, now=now)
    if from_stream is not None:
        return from_stream

    text = f"{remaining_stdout}\n{stderr}"
    lowered = text.lower()
    quota = any(marker in lowered for marker in _QUOTA_MARKERS)
    rate = any(marker in lowered for marker in _RATE_MARKERS)
    overloaded = any(marker in lowered for marker in _OVERLOAD_MARKERS)
    if not (quota or rate or overloaded):
        return None
    if overloaded and not (quota or rate):
        return QuotaSignal("overloaded")

    dated = [
        (moment, source)
        for moment, source in (
            (parse_resets_at(text, now=now), "resets_at"),
            (parse_retry_after(text, now=now), "retry_after"),
        )
        if moment is not None
    ]
    if dated:
        until, source = max(dated, key=lambda item: item[0])
        return QuotaSignal("quota" if quota else "rate_limit", until, source)
    if quota:
        return QuotaSignal("quota", None, "unknown")
    return QuotaSignal("rate_limit")


def observe_window(stdout: str, *, now: datetime) -> QuotaObservation | None:
    """Fenêtre de forfait la plus entamée lue dans le flux ; ``None`` sans lecture positive.

    Une utilisation de 0 n'apprend rien (un minorant nul), elle n'est pas
    retenue : ``providers quota`` rend ``unknown``, jamais 0.
    """
    events, _ = _rate_limit_events(stdout)
    best: QuotaObservation | None = None
    for event in events:
        info = event.get("rate_limit_info")
        windows = info.get("unifiedWindows") if isinstance(info, dict) else None
        if not isinstance(windows, dict):
            continue
        for name, window in windows.items():
            if not isinstance(window, dict):
                continue
            used = window.get("utilization")
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 < used <= 1.0:
                continue
            if best is None or used > best.utilization:
                best = QuotaObservation(str(name), float(used), _epoch(window.get("resetsAt"), now=now), now)
    return best
