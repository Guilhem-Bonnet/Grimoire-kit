"""Le plafond de sessions lancées par jour d'un rôle, journalisé quand il refuse (relecteur et merger).

Le worker, l'arbitre et l'orchestrateur ont chacun leur ``sessions_per_day`` ; le relecteur et le merger n'en
avaient pas. Ce module porte la garde commune : le compte du jour (UTC) vient du ledger (aucun compteur à côté),
le refus est écrit une fois par rôle et par jour (``role.session_cap_reached``) et la ligne d'attention qui le
rend visible est relue de cet événement.

Sans plafond déclaré (``None``, le défaut), rien n'est refusé : le comportement d'un projet qui n'en pose pas
ne change pas. Le plafond ne tue jamais une session en cours : il ne refuse que le lancement suivant.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = ["SESSION_CAP_EVENT", "cap_attention", "cap_refusal", "launched_today"]

#: Un lancement refusé par le plafond de sessions du jour d'un rôle (``role``, ``limit``, ``count``, ``day``).
SESSION_CAP_EVENT = "role.session_cap_reached"


def _day(stamp: datetime) -> str:
    return stamp.astimezone(UTC).date().isoformat()


def launched_today(
    service: TaskService, kinds: Collection[str], now: datetime | None = None, *, not_launch: Callable[[Any], bool] | None = None
) -> int:
    """Les événements de *kinds* écrits aujourd'hui (UTC) : le compte des lancements d'un rôle.

    *not_launch* écarte ceux qui n'ont lancé aucune session (une escalade du merger, par exemple).
    """
    today = _day(now or datetime.now(UTC))
    return sum(
        1 for e in service.ledger.list_events()
        if e.event_type in kinds and e.created_at[:10] == today and not (not_launch is not None and not_launch(e))
    )


def cap_refusal(
    service: TaskService, role: str, limit: int | None, kinds: Collection[str], *,
    entity_id: str, actor: str, now: datetime | None = None, not_launch: Callable[[Any], bool] | None = None,
) -> str:
    """Le motif du refus (``""`` : lancer). Écrit ``role.session_cap_reached`` une fois par rôle et par jour."""
    if limit is None:
        return ""
    count = launched_today(service, kinds, now, not_launch=not_launch)
    if count < limit:
        return ""
    today = _day(now or datetime.now(UTC))
    already = any(
        e.event_type == SESSION_CAP_EVENT and e.payload.get("role") == role and e.payload.get("day") == today
        for e in service.ledger.list_events()
    )
    if not already:
        service.ledger.append_event(
            SESSION_CAP_EVENT, entity_id, "task", actor, {"role": role, "limit": limit, "count": count, "day": today}
        )
    return f"plafond de sessions du jour atteint ({count}/{limit}) : aucun lancement avant minuit UTC"


def cap_attention(service: TaskService, now: datetime | None = None) -> tuple[str, ...]:
    """Une ligne d'attention par rôle dont le plafond a refusé un lancement aujourd'hui (UTC)."""
    today = _day(now or datetime.now(UTC))
    seen: dict[str, str] = {}
    for event in service.ledger.list_events():
        if event.event_type == SESSION_CAP_EVENT and event.payload.get("day") == today:
            seen[str(event.payload.get("role"))] = (
                f"plafond de sessions {event.payload.get('role')} atteint "
                f"({event.payload.get('count')}/{event.payload.get('limit')}) : plus de lancement avant minuit UTC"
            )
    return tuple(seen.values())
