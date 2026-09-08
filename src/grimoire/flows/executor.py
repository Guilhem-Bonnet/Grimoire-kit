"""``NodeExecutor`` — le point où l'hôte, jamais le kit, exécute un node.

Doctrine #204 : le kit n'appelle aucun LLM. ``InteractiveNodeExecutor``, seule
implémentation livrée par ce lot, se contente d'imprimer le contrat autonome
du node courant et de rendre la main — c'est l'agent hôte (Claude Code,
Copilot, un humain) qui fait le travail, puis revient via
``grimoire flow resume --output``.

Le protocole existe pour qu'un exécuteur par dispatch (issue #323 puis #311)
se branche plus tard sans toucher au moteur : ``FlowEngine`` n'appelle jamais
rien d'autre que ``NodeExecutor.execute`` et ne connaît pas la différence
entre les deux implémentations.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any, Protocol, runtime_checkable

from grimoire.flows.schemas import NodeContract, NodeExecutionResult

__all__ = ["InteractiveNodeExecutor", "NodeExecutor"]


@runtime_checkable
class NodeExecutor(Protocol):
    """Contrat minimal : présenter (ou exécuter) un node, rendre un résultat."""

    def execute(self, contract: NodeContract, *, context_pack: dict[str, Any]) -> NodeExecutionResult:
        """Traite le node courant et dit si le moteur doit attendre l'hôte."""
        ...


class InteractiveNodeExecutor:
    """Exécuteur par défaut : imprime le contrat, rend la main à l'hôte.

    ``context_pack`` (le contexte transmis au node — sorties amont, pas le
    dépôt entier) n'est pas encore renseigné par le moteur dans ce lot : le
    paramètre existe dans le protocole pour que son introduction future
    (lot 2, #205 — un flow déclare des besoins) ne change pas la signature.
    """

    def __init__(self, *, json_output: bool = False, stream: IO[str] | None = None) -> None:
        self._json_output = json_output
        self._stream = stream or sys.stdout

    def execute(self, contract: NodeContract, *, context_pack: dict[str, Any]) -> NodeExecutionResult:
        if self._json_output:
            print(json.dumps(contract.to_dict(), indent=2, ensure_ascii=False), file=self._stream)
        else:
            print(contract.to_text(), file=self._stream)
        return NodeExecutionResult(pending=True)
