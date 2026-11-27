"""Ce que la vue dit du moteur : qui parle, ce qui sort de la machine, pourquoi, et si la mascotte réfléchit (épique #1477, lot B2).

Le programme décide de ce qui est montré, jamais le moteur : une :class:`Presentation` est construite par la
fabrique des moteurs (code du programme) à partir de la décision de routage, puis nettoyée ici avant d'atteindre la
vue. Elle accompagne le gardien (:class:`~grimoire.core.cadrage_conversation_engine.Garde`) et non le moteur : un moteur
hostile ne peut donc rien y changer.

La réflexion (``reflexion``) est un marqueur EN MÉMOIRE, borné et daté : posé le temps d'un appel lent au modèle fort,
levé à la fin de l'appel (même en erreur) et périmé tout seul. Aucun fil de fond, rien d'écrit sur disque : une lecture
de la conversation qui arrive pendant le tour lent voit ``reflexion: true``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NamedTuple

__all__ = [
    "MAX_MARQUES",
    "Presentation",
    "Rendu",
    "UsageVue",
    "reflechit",
    "reflexion",
    "sans_modele",
    "vue_moteur",
]

MAX_MARQUES = 64
_MAX_TEXTE = 400
_LOCK = threading.Lock()
_MARQUES: dict[tuple[str, str], float] = {}


@dataclass(frozen=True, slots=True)
class UsageVue:
    """Le modèle retenu (ou l'absence de modèle) pour un usage, tel que la décision de routage l'a rendu."""

    usage: str
    id: str
    libelle: str
    local: bool
    sortie_de_texte: bool
    raison: str = ""
    #: ``{"key", "libelle", "raison"}`` : un modèle PROPOSÉ à l'humain, jamais utilisé seul ; ``None`` sinon.
    proposition: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Presentation:
    """Les modèles de la conversation, par usage ; ``cle`` : ce chantier, pour le marqueur de réflexion."""

    usages: tuple[UsageVue, ...]
    cle: tuple[str, str] | None = None


class Rendu(NamedTuple):
    """Ce que la fabrique rend : le moteur, et ce que la vue en dira (``presentation``)."""

    moteur: Any
    presentation: Presentation


SANS_MODELE_ID = "sans-modele"
SANS_MODELE_LIBELLE = "Questions guidées, sans modèle"


def sans_modele(raison: str = "", proposition: dict[str, str] | None = None, *, usage: str = "conversation") -> UsageVue:
    """Un usage rendu sans modèle : rien ne sort de la machine."""
    return UsageVue(usage, SANS_MODELE_ID, SANS_MODELE_LIBELLE, True, False, raison, proposition)


# ── Le marqueur de réflexion ────────────────────────────────────────────────


@contextmanager
def reflexion(cle: tuple[str, str] | None, duree_s: float) -> Iterator[None]:
    """Marque « la mascotte réfléchit » pour ce chantier le temps de l'appel ; levé à la sortie, en erreur aussi."""
    if cle is None:
        yield
        return
    now = time.monotonic()
    with _LOCK:
        for key in [k for k, until in _MARQUES.items() if until <= now]:
            del _MARQUES[key]
        if len(_MARQUES) < MAX_MARQUES or cle in _MARQUES:  # plein : pas de marque de plus, jamais d'erreur
            _MARQUES[cle] = now + max(1.0, duree_s)
    try:
        yield
    finally:
        with _LOCK:
            _MARQUES.pop(cle, None)


def reflechit(cle: tuple[str, str] | None) -> bool:
    """Un appel lent est-il en cours pour ce chantier (et pas périmé) ?"""
    if cle is None:
        return False
    with _LOCK:
        until = _MARQUES.get(cle)
    return until is not None and until > time.monotonic()


# ── La vue ──────────────────────────────────────────────────────────────────


def _texte(value: Any, limit: int = _MAX_TEXTE) -> str:
    if not isinstance(value, str):
        return ""
    clean = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _usage_public(u: UsageVue) -> dict[str, Any]:
    proposition = None
    if isinstance(u.proposition, dict):
        proposition = {k: _texte(u.proposition.get(k)) for k in ("key", "libelle", "raison")}
    return {
        "usage": _texte(u.usage, 40), "id": _texte(u.id, 40) or SANS_MODELE_ID, "libelle": _texte(u.libelle, 120),
        "local": u.local is True, "sortie_de_texte": u.sortie_de_texte is True, "raison": _texte(u.raison),
        "proposition": proposition,
    }


def vue_moteur(presentation: Presentation | None, moteur_id: str, libelle: str) -> dict[str, Any]:
    """``{id, libelle, local, sortie_de_texte, raison, proposition, reflexion, etat, par_usage}`` pour la vue.

    Le modèle qui parle est celui de l'usage « conversation » ; ``sortie_de_texte`` est vrai dès qu'UN usage actif envoie du
    texte hors de la machine (ce que la vue doit toujours dire), ``local`` seulement si tous les modèles actifs le sont.
    Sans présentation (moteur sans modèle posé à la main) : rien ne sort, aucune raison à dire.
    """
    if presentation is None or not presentation.usages:
        return {
            "id": _texte(moteur_id, 40) or SANS_MODELE_ID, "libelle": _texte(libelle, 120), "local": True,
            "sortie_de_texte": False, "raison": "", "proposition": None, "reflexion": False, "etat": "pret", "par_usage": [],
        }
    rows = [_usage_public(u) for u in presentation.usages]
    first = rows[0]
    out = any(r["sortie_de_texte"] for r in rows)
    thinking = reflechit(presentation.cle)
    ident = _texte(moteur_id, 40) or first["id"]
    speaker = next((r for r in rows if r["id"] == ident), first)
    return {
        "id": ident, "libelle": speaker["libelle"] or _texte(libelle, 120), "local": not out, "sortie_de_texte": out,
        "raison": first["raison"], "proposition": first["proposition"], "reflexion": thinking,
        "etat": "reflechit" if thinking else "pret", "par_usage": rows,
    }
