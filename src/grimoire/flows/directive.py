"""Directive d'un run de flow : ce que l'hôte doit faire ensuite, avec une identité stable (W2-03).

Quatre ``kind``, pas davantage : ``execute`` (produire la sortie du node),
``checkpoint`` (décision d'hôte attendue), ``blocked`` (le run ne peut pas
avancer sans correction ; ``reason`` dit pourquoi) et ``done``.

``directive_id`` lie un ``resume`` à la directive que l'hôte a REÇUE : sans lui,
``resume`` déduit le node courant et valide la sortie contre lui, même si
l'hôte répondait à la directive d'avant. Il vaut
``sha256(run_id, node_id, tentative, empreinte du blueprint)`` : stable entre
deux lectures, différent dès qu'un refus ouvre une nouvelle tentative ou que le
contrat compilé change. Pur : ce module ne lit ni n'écrit rien.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DIRECTIVE_KINDS",
    "Directive",
    "blueprint_digest",
    "directive_id_for",
]

#: Exactement quatre kinds (W2-03) : un test en fige la liste.
DIRECTIVE_KINDS: tuple[str, ...] = ("execute", "checkpoint", "blocked", "done")


def blueprint_digest(contracts: Iterable[dict[str, Any]]) -> str:
    """Empreinte sha256 des contrats compilés, dans l'ordre reçu."""
    canonical = json.dumps(list(contracts), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def directive_id_for(run_id: str, node_id: str, attempt: int, digest: str) -> str:
    """``sha256(run_id, node_id, tentative, empreinte du blueprint)`` — séparateur NUL, sans ambiguïté."""
    raw = "\x00".join((run_id, node_id, str(attempt), digest))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Directive:
    """La prochaine étape d'un run, telle que l'hôte doit y répondre."""

    kind: str
    run_id: str
    node_id: str | None
    directive_id: str | None
    attempt: int = 0
    #: Seulement pour ``blocked`` : ``contract_violation``, ``aborted``, ``refused``…
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "directive_id": self.directive_id,
            "attempt": self.attempt,
            "reason": self.reason,
        }
