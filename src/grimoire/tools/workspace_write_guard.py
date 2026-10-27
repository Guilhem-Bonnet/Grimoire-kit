"""Garde d'écriture commune du cockpit et de l'atelier — ADR-008 D3.

Spécification. Toute route d'écriture (``POST``/``PUT``) des deux serveurs
locaux passe par :func:`guard_write`, qui rend un refus ``(code, message)``
ou ``None`` :

* **Jeton** — la requête porte ``X-Grimoire-Session``, le jeton de processus
  servi dans chaque page HTML (``request_guard.inject_session_token``) ;
  absent ou faux, refus 403 avec la marche à suivre (recharger la page).
* **Origine** — ``Host`` loopback, ``Origin`` égal à l'origine servie,
  ``Sec-Fetch-Site`` non étranger et corps ``application/json`` : la règle de
  ``request_guard.check_mutation``, inchangée ; s'y ajoute le pair réseau
  (``peer``) qui doit être la loopback.
* **Journal** — chaque écriture, acceptée OU refusée, ajoute une ligne JSONL
  (horodatage UTC, route, méthode, verdict, code et raison du refus) au
  journal de la garde, en ajout seul — jamais de réécriture. Cockpit :
  ``registry_home()/write-guard.jsonl`` (:func:`cockpit_journal_path`) ;
  atelier : ``_grimoire-runtime-output/cockpit/write-guard.jsonl`` du projet.
* **Fail-closed** — un journal non inscriptible refuse l'écriture (503) avec
  un message actionnable ; un refus reste un refus même si sa trace échoue.

Point d'extension (apply des actions typées, ADR-008 D3/D4) : les routes
``/api/workspace/actions/<id>/apply|rollback`` passeront par
:func:`guard_write` comme toute écriture, puis consigneront leurs événements
(``plan``, ``apply_started``, ``effects``…) via :func:`journal_event` dans
leur propre journal ajout seul ; :func:`state_fingerprint` fournit
l'empreinte d'état que ``apply`` comparera à celle du plan avant tout effet.
L'apply lui-même n'est pas dans ce module.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from grimoire.tools.project_registry import registry_home
from grimoire.tools.request_guard import LOOPBACK_NAMES, SESSION_HEADER, check_mutation

if TYPE_CHECKING:
    from collections.abc import Mapping
    from email.message import Message
    from pathlib import Path

    _Headers = Message[str, str]

__all__ = [
    "Refusal",
    "cockpit_journal_path",
    "guard_write",
    "journal_event",
    "state_fingerprint",
]

Refusal = tuple[int, str]

#: Le refus « jeton » doit dire quoi faire, pas seulement dire non.
_TOKEN_HINT = (
    " — rechargez la page du cockpit : le jeton est servi avec elle "
    f"(en-tête {SESSION_HEADER})"
)


def cockpit_journal_path() -> Path:
    """Journal de la garde du cockpit multi-projets, dans son dossier runtime."""
    return registry_home() / "write-guard.jsonl"


def state_fingerprint(content: bytes) -> str:
    """Empreinte d'état (SHA-256) pour le contrat plan→apply d'ADR-008.

    ``apply`` recalcule l'empreinte du contenu cible et refuse si elle ne
    correspond plus à celle enregistrée au plan : l'état a changé entre-temps.
    """
    return hashlib.sha256(content).hexdigest()


def journal_event(journal: Path, event: Mapping[str, object]) -> None:
    """Ajoute une ligne JSONL horodatée au ``journal`` — ajout seul.

    Lève ``OSError`` si le journal n'est pas inscriptible : au point
    d'extension apply (ADR-008 D4), pas de journal, pas d'effet.
    """
    line = json.dumps({"ts": datetime.now(UTC).isoformat(), **event}, ensure_ascii=False)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def guard_write(
    headers: _Headers,
    port: int,
    *,
    method: str,
    route: str,
    journal: Path,
    with_body: bool,
    peer: str | None = None,
) -> Refusal | None:
    """Le verdict de la garde commune pour UNE écriture, journalisé.

    ``peer`` est l'adresse du client quand le serveur la connaît (cockpit) ;
    ``None`` laisse ce contrôle au serveur appelant. ``route`` est le chemin
    sans sa query string ; ``journal`` le fichier JSONL de la garde.
    """
    refused: Refusal | None
    if peer is not None and peer not in LOOPBACK_NAMES:
        refused = (403, "cockpit local only")
    else:
        refused = check_mutation(headers, port, with_body=with_body)
    if refused is not None and refused[1] == "jeton de session requis":
        refused = (refused[0], refused[1] + _TOKEN_HINT)
    event: dict[str, object] = {
        "route": route,
        "method": method,
        "verdict": "refusée" if refused else "acceptée",
    }
    if refused is not None:
        event["code"], event["reason"] = refused
    try:
        journal_event(journal, event)
    except OSError as exc:
        if refused is None:
            # Fail-closed : une écriture qui ne laisse pas de trace n'a pas lieu.
            return 503, (
                f"journal de la garde non inscriptible ({journal}) : écriture refusée"
                f" — rendez ce fichier inscriptible puis réessayez ({exc})"
            )
    return refused
