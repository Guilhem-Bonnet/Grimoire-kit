"""Identifiants et fichiers du ledger : helpers extraits de :mod:`grimoire.missions.ledger` (#1162).

Le module historique approchait son plafond de taille (ratchet R2) ; ces fonctions pures — slug, horodatage,
identifiant, confinement du journal et verrou inter-processus — n'ont besoin ni de l'état rejoué ni de la classe
:class:`~grimoire.missions.ledger.MissionLedger`. Elles sont déplacées ici sans changer leur contrat.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import locked

__all__ = [
    "confined_ledger_file",
    "ledger_file_lock",
    "new_id",
    "now_iso",
    "slug",
]


def slug(title: str, limit: int) -> str:
    """Fragment d'identifiant sûr dans un chemin.

    Le slug naïf recopiait le titre : « Ajouter /health » donnait
    ``GAO-ajouter-/hea-001``, un identifiant que ``normalize_task_id`` refuse et
    qui, employé tel quel, ouvrait un chemin de plus d'un segment. Les
    identifiants du ledger servent de nom de dossier aux artefacts du standard :
    ils ne peuvent contenir que ce que ce contrat autorise.
    """
    kept = [c if c.isascii() and (c.isalnum() or c in "_.-") else "-" for c in title.lower()]
    slug_text = re.sub(r"-{2,}", "-", "".join(kept))[:limit].strip("-._")
    return slug_text or "task"


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


#: Les seuls journaux que le ledger écrit sous sa racine.
_LEDGER_FILES = frozenset({"events.jsonl", "incidents.jsonl"})


def confined_ledger_file(path: Path) -> Path:
    """Résout *path* et refuse tout ce qui n'est pas un journal du ledger sous sa racine.

    La racine vient du CLI (``--ledger-root``) : on n'écrit que ``events.jsonl``
    et ``incidents.jsonl``, et le chemin résolu (liens symboliques compris) doit
    rester directement sous le dossier résolu de la racine.
    """
    if path.name not in _LEDGER_FILES:
        raise GrimoireMissionError(f"Fichier de ledger non autorisé : {path.name!r}")
    base = path.parent.resolve()
    resolved = (base / path.name).resolve()
    if resolved.parent != base or not resolved.is_relative_to(base):
        raise GrimoireMissionError(f"Fichier de ledger hors de sa racine : {path.name!r}")
    return resolved


@contextmanager
def ledger_file_lock(target_path: Path) -> Iterator[None]:
    """Verrou exclusif inter-processus sur *target_path* — :func:`grimoire.core.fsio.locked`.

    ``_atomic_append`` ajoute une ligne en fin de fichier (``open('ab')``, un
    seul ``write``, ``fsync``) ; le verrou sérialise les écrivains (deux
    requêtes HTTP du cockpit servies par des threads séparés, typiquement) :
    la réparation d'une fin tronquée et l'empreinte « avant / après » de
    l'ajout ne valent que pour un seul écrivain à la fois, et
    :meth:`MissionLedger.transition_task` relit et valide sous ce même
    verrou : un compare-and-set (issue #710). Les lecteurs n'en ont pas
    besoin : une dernière ligne sans ``\\n`` (ajout en cours) n'est pas lue.

    Un verrou impossible à prendre (délai dépassé, système de fichiers sans
    ``flock``) lève :class:`GrimoireMissionError` au lieu d'écrire sans
    exclusion : deux titulaires valent pire qu'une écriture à refaire. Un
    ledger partagé par un système de fichiers réseau n'est pas supporté.
    """
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(locked(target_path))
        except (TimeoutError, OSError) as exc:
            raise GrimoireMissionError(
                f"Écriture refusée : aucun verrou inter-processus n'a pu être pris sur {target_path.name} ({exc}) "
                "— sans lui, deux écritures simultanées pourraient réussir toutes les deux. "
                "Réessaie ; si cela persiste, le système de fichiers ne supporte pas le verrouillage."
            ) from exc
        yield
