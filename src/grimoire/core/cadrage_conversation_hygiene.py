"""Hygiène des conversations de cadrage : sommeil, classement doux, réouverture (épique #1477, lot A).

Une conversation sans activité passe « en sommeil », puis « classée » au bout de délais réglables. Classée =
conservée, retrouvable, hors de la liste active ; JAMAIS supprimée automatiquement. Le statut se DÉDUIT de la date
de dernière activité et d'une horloge injectée : lire n'écrit rien. Classer et rouvrir sont des gestes explicites.
Au plus ``max_actives`` conversations sont proposées comme actives ; les autres restent retrouvables.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core import cadrage_conversation_store as store
from grimoire.core.cadrage_conversation import Conversation, ConversationError, parse_date

__all__ = ["Reglages", "classer", "lister", "maintenant", "rouvrir", "statut"]

BORNES_JOURS = (1, 365)
BORNES_ACTIVES = (1, 50)


def maintenant() -> datetime:
    """L'horloge par défaut ; les fonctions de ce module prennent ``now`` pour qu'un test injecte la sienne."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Reglages:
    """Les délais (en jours) et le plafond de conversations proposées comme actives."""

    sommeil_jours: int = 3
    classee_jours: int = 14
    max_actives: int = 5

    @classmethod
    def lire(cls, raw: Any) -> Reglages:
        """Réglages depuis un dictionnaire de configuration : tout ce qui est hors bornes ou mal typé retombe au défaut."""
        base = cls()
        if not isinstance(raw, dict):
            return base

        def pick(key: str, default: int, bornes: tuple[int, int]) -> int:
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not bornes[0] <= value <= bornes[1]:
                return default
            return value

        sommeil = pick("sommeil_jours", base.sommeil_jours, BORNES_JOURS)
        classee = max(pick("classee_jours", base.classee_jours, BORNES_JOURS), sommeil)
        return cls(sommeil, classee, pick("max_actives", base.max_actives, BORNES_ACTIVES))


def statut(conv: Conversation, now: datetime, reglages: Reglages) -> str:
    """``classee`` (explicite, ou sans activité depuis ``classee_jours``), ``en-sommeil`` ou ``active``."""
    if conv.classee_le:
        return "classee"
    age = now - parse_date(conv.active_le)
    if age >= timedelta(days=reglages.classee_jours):
        return "classee"
    return "en-sommeil" if age >= timedelta(days=reglages.sommeil_jours) else "active"


def classer(conv: Conversation, now: datetime) -> None:
    """Classe la conversation (conservée, hors de la liste active). L'appelant enregistre."""
    if conv.classee_le:
        raise ConversationError("cette conversation est déjà classée")
    conv.classee_le = now.isoformat(timespec="seconds")
    conv.rev += 1


def rouvrir(conv: Conversation, now: datetime) -> None:
    """Rouvre : plus classée, activité d'aujourd'hui. Une conversation terminée redevient un cadrage en cours."""
    conv.classee_le = ""
    conv.active_le = now.isoformat(timespec="seconds")
    if conv.mode == "termine":
        conv.mode, conv.document = "cadrage", ""
    conv.rev += 1


def lister(entries: Iterable[tuple[str, Path]], now: datetime, reglages: Reglages) -> dict[str, Any]:
    """Les conversations des chantiers donnés ``(slug, dossier)`` : actives (au plus N), autres, classées, en erreur.

    Lecture seule. Les dossiers viennent de la liste réelle des chantiers : rien ici ne construit un chemin depuis un
    nom reçu. Un fichier hors cadre donne une ligne ``erreur`` sans casser la liste.
    """
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for slug, directory in entries:
        try:
            conv = store.read(directory, slug)
        except ConversationError as exc:
            errors.append({"chantier": slug, "erreur": str(exc)})
            continue
        if conv is None:
            continue
        state = statut(conv, now, reglages)
        rows.append({
            "chantier": slug, "statut": state, "mode": conv.mode, "active_le": conv.active_le, "cree": conv.cree,
            "tours": len(conv.tours), "compris": len(conv.ranges()), "en_attente": len(conv.en_attente()),
            "classee_auto": state == "classee" and not conv.classee_le,
        })
    rows.sort(key=lambda r: parse_date(r["active_le"]), reverse=True)
    open_rows = [r for r in rows if r["statut"] != "classee"]
    return {
        "actives": open_rows[: reglages.max_actives], "autres": open_rows[reglages.max_actives:],
        "classees": [r for r in rows if r["statut"] == "classee"], "erreurs": errors,
        "reglages": {"sommeil_jours": reglages.sommeil_jours, "classee_jours": reglages.classee_jours,
                     "max_actives": reglages.max_actives},
    }
