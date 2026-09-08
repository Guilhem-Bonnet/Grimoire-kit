"""Types du moteur de flows : le contrat d'un node, et les métadonnées d'un run.

Deux familles bien séparées, à dessein :

- :class:`NodeContract` est **dérivé** du blueprint à chaque lecture (``run``,
  ``resume``, ``status``) — il n'est jamais persisté tel quel. Le persister
  aurait dupliqué le blueprint sur disque et l'aurait figé au moment du
  ``run`` : une correction du fichier source entre deux étapes ne se serait
  jamais vue.
- :class:`FlowRunMeta` **est** persistée, une fois, à la création du run. Elle
  ne porte que ce qu'aucune source déjà présente sur disque ne peut redonner :
  quel fichier blueprint charger et dans quel ordre topologique. Tout le
  reste (node courant, étapes faites, dernier refus) se recalcule depuis les
  checkpoints et le journal d'événements du ``RuntimeKernel`` — jamais d'un
  compteur parallèle qui pourrait diverger après un crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FlowRunMeta",
    "NodeContract",
    "PinRef",
]


@dataclass(frozen=True, slots=True)
class PinRef:
    """Une pin de node, réduite à ce qu'un exécuteur doit en savoir."""

    pin_id: str
    contract: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.pin_id, "contract": self.contract}


@dataclass(frozen=True, slots=True)
class NodeContract:
    """Ce qu'un node offre à son exécuteur : entrées, sortie, outils, preuve.

    C'est l'inversion visée par #201 rendue concrète : au lieu du blueprint
    entier aplati en prompt, l'hôte reçoit ce contrat borné pour un seul node.
    """

    node_id: str
    kind: str
    label: str
    description: str
    inputs: tuple[PinRef, ...]
    outputs: tuple[PinRef, ...]
    tool_boundary: tuple[str, ...]
    acceptance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "tool_boundary": list(self.tool_boundary),
            "acceptance": list(self.acceptance),
        }

    def to_text(self) -> str:
        """Rendu humain autonome : tout ce qu'un exécuteur doit lire, rien de plus."""
        lines = [f"Node « {self.node_id} » ({self.kind}) — {self.label or self.node_id}"]
        if self.description:
            lines.append(f"  {self.description}")
        if self.inputs:
            lines.append("Entrées :")
            lines.extend(f"  - {p.pin_id} : {p.contract}" for p in self.inputs)
        else:
            lines.append("Entrées : aucune pin d'entrée")
        if self.outputs:
            lines.append("Sortie attendue :")
            lines.extend(f"  - {p.pin_id} : {p.contract}" for p in self.outputs)
        else:
            lines.append("Sortie attendue : aucune pin de sortie déclarée")
        if self.tool_boundary:
            lines.append("Frontière d'outils :")
            lines.extend(f"  - {t}" for t in self.tool_boundary)
        else:
            lines.append("Frontière d'outils : aucune (raisonnement seul)")
        lines.append("Critères d'acceptation :")
        lines.extend(f"  - {a}" for a in self.acceptance)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class FlowRunMeta:
    """Métadonnées immuables d'un run, écrites une fois par ``flow run``."""

    run_id: str
    blueprint_id: str
    blueprint_path: str
    order: tuple[str, ...]
    created_at: str
    schema_version: str = "grimoire.flow_run_meta.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_path": self.blueprint_path,
            "order": list(self.order),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FlowRunMeta:
        return cls(
            run_id=d["run_id"],
            blueprint_id=d["blueprint_id"],
            blueprint_path=d["blueprint_path"],
            order=tuple(d.get("order", [])),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.flow_run_meta.v1"),
        )


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """Ce que ``resume`` a produit : à avancer, suspendu, ou terminé."""

    ok: bool
    finished: bool
    node_id: str | None
    faults: tuple[str, ...] = ()
    contract: NodeContract | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "finished": self.finished,
            "node_id": self.node_id,
            "faults": list(self.faults),
            "contract": self.contract.to_dict() if self.contract else None,
        }


@dataclass(frozen=True, slots=True)
class FlowStatusView:
    """Vue de lecture d'un run — jamais de mutation, tout est dérivé."""

    run_id: str
    blueprint_id: str
    status: str
    current_node: str | None
    completed_nodes: tuple[str, ...]
    pending_nodes: tuple[str, ...]
    last_refusal: dict[str, Any] | None
    contract: NodeContract | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "blueprint_id": self.blueprint_id,
            "status": self.status,
            "current_node": self.current_node,
            "completed_nodes": list(self.completed_nodes),
            "pending_nodes": list(self.pending_nodes),
            "last_refusal": self.last_refusal,
            "contract": self.contract.to_dict() if self.contract else None,
        }


@dataclass(frozen=True, slots=True)
class NodeExecutionResult:
    """Résultat d'un ``NodeExecutor.execute`` — ce que le moteur fait ensuite.

    ``pending=True`` (l'implémentation interactive, par défaut) : le contrat a
    été présenté, la main revient à l'hôte, qui reviendra via
    ``flow resume --output``. ``pending=False`` : l'exécuteur a lui-même
    produit la sortie du node (cas d'un exécuteur par dispatch, #323 puis
    #311) et le moteur vérifie/avance dans le même appel, sans attendre un
    second aller-retour CLI.
    """

    pending: bool
    output: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
