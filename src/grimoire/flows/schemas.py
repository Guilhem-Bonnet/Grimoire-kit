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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.flows.directive import Directive

__all__ = [
    "AcceptanceEvidence",
    "AcceptanceRun",
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
class AcceptanceRun:
    """Une acceptance structurée déclarant une commande dont le code de sortie fait le verdict (issue #428).

    Rétrocompatible avec l'acceptance textuelle : une entrée ``{"run": "..."}``
    du blueprint (voir ``blueprint_loader._parse_acceptance_entry``) produit à
    la fois ce contrôle exécutable et une chaîne d'acceptance équivalente,
    pour que ``verifiability.classify`` continue de reconnaître un critère
    mécanique sans jamais avoir à exécuter quoi que ce soit lui-même.
    """

    argv: tuple[str, ...]
    raw: str
    expect_exit: int = 0
    cwd: str = "."
    timeout_s: float = 120.0
    expect_stdout_contains: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "raw": self.raw,
            "expect_exit": self.expect_exit,
            "cwd": self.cwd,
            "timeout_s": self.timeout_s,
            "expect_stdout_contains": self.expect_stdout_contains,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceEvidence:
    """Une preuve mécanique déclarée sans commande libre (issue #428).

    ``kind`` vaut ``"path_exists"`` (un fichier doit exister, relatif à la
    racine du projet) ou ``"test"`` (un test nommé doit passer — traduit par
    le gate en une invocation ``pytest`` du seul identifiant déclaré).
    """

    kind: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class NodeContract:
    """Ce qu'un node offre à son exécuteur : entrées, sortie, outils, preuve.

    C'est l'inversion visée par #201 rendue concrète : au lieu du blueprint
    entier aplati en prompt, l'hôte reçoit ce contrat borné pour un seul node.

    ``acceptance_runs``/``acceptance_evidence`` (issue #428) portent la forme
    structurée et exécutable de l'acceptance, quand le blueprint la déclare —
    ``acceptance`` (texte) reste la seule source que
    ``grimoire.missions.verifiability.classify`` sait lire, et continue à
    exister même pour un node purement structuré (voir
    ``blueprint_loader._parse_acceptance_entry``, qui dérive toujours le
    texte depuis la forme structurée).

    ``verifiability_warning`` (issue #428, suite) : posé au chargement du
    blueprint (``blueprint_loader.build_node_contracts``) quand le texte de
    ``acceptance`` seul classerait ce node V0 mais qu'aucune commande
    exécutable n'est déclarée — un faux V0 est pire qu'un vrai V1
    (``verifiability.py``), donc un tel node est traité comme V1 par
    ``flows.dispatch_executor`` (jamais fermé sur la seule foi de l'ouvrier),
    et ce champ porte le message nommé qui explique pourquoi. ``None`` dans
    tous les autres cas (V0 structuré, V1, V2).
    """

    node_id: str
    kind: str
    label: str
    description: str
    inputs: tuple[PinRef, ...]
    outputs: tuple[PinRef, ...]
    tool_boundary: tuple[str, ...]
    acceptance: tuple[str, ...]
    acceptance_runs: tuple[AcceptanceRun, ...] = ()
    acceptance_evidence: tuple[AcceptanceEvidence, ...] = ()
    verifiability_warning: str | None = None
    #: La ``ref`` brute du node (issue #206) — vide pour les kinds qui n'en
    #: portent pas d'utile à l'exécuteur. Un ``DispatchExecutor`` qui reçoit un
    #: node ``kind == "composite"`` en a besoin pour résoudre le sous-flow à
    #: lancer (:func:`grimoire.flows.blueprint_loader.resolve_composite_ref`) ;
    #: aucun autre champ de ce contrat ne porte cette information.
    ref: str = ""

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
            "acceptance_runs": [r.to_dict() for r in self.acceptance_runs],
            "acceptance_evidence": [e.to_dict() for e in self.acceptance_evidence],
            "verifiability_warning": self.verifiability_warning,
            "ref": self.ref,
        }

    @property
    def has_structured_acceptance(self) -> bool:
        """Vrai si le gate a de quoi exécuter réellement l'acceptance (issue #428)."""
        return bool(self.acceptance_runs or self.acceptance_evidence)

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
    #: Lien vers le run parent (issue #206) — ``None`` pour un run racine,
    #: renseigné une fois à la création pour un run lancé par un node
    #: ``kind: "composite"``. Comme le reste de cette classe : écrit une
    #: fois, jamais recalculé.
    parent_run_id: str | None = None
    #: L'id du node composite du run parent qui a lancé ce run — ``None`` ssi
    #: ``parent_run_id`` l'est aussi (les deux sont posés ou absents ensemble).
    parent_node_id: str | None = None
    #: W2-04 — sha256 des contrats compilés (dans ``order``) au lancement, et le
    #: sha256 de chaque contrat. ``None``/vide pour un run antérieur : sa dérive
    #: est alors « non vérifiée », jamais déclarée absente. Seul ``flow migrate``
    #: réécrit ces champs et ``order`` (acte explicite de l'utilisateur).
    blueprint_sha256: str | None = None
    node_digests: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_path": self.blueprint_path,
            "order": list(self.order),
            "created_at": self.created_at,
            "parent_run_id": self.parent_run_id,
            "parent_node_id": self.parent_node_id,
            "blueprint_sha256": self.blueprint_sha256,
            "node_digests": dict(self.node_digests),
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
            parent_run_id=d.get("parent_run_id"),
            parent_node_id=d.get("parent_node_id"),
            blueprint_sha256=d.get("blueprint_sha256"),
            node_digests=dict(d.get("node_digests") or {}),
        )


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """Ce que ``resume`` a produit : à avancer, suspendu, ou terminé."""

    ok: bool
    finished: bool
    node_id: str | None
    faults: tuple[str, ...] = ()
    contract: NodeContract | None = None
    #: ``True`` quand la sortie soumise rejoue celle déjà acceptée pour un
    #: node franchi (``replay_of``) : ``ok`` reste vrai, rien n'a été écrit,
    #: ``node_id``/``contract`` désignent toujours ce que le run attend.
    replay: bool = False
    replay_of: str | None = None
    #: Ce que cette reprise a complété d'une écriture qu'un crash avait
    #: interrompue (W1-03b) — vide dans le cas normal. Une réparation n'est
    #: jamais silencieuse.
    repaired: tuple[str, ...] = ()
    #: La directive qui suit cette reprise (W2-03) : ce à quoi l'hôte répondra
    #: ensuite, avec son ``directive_id`` à renvoyer à ``resume``.
    directive: Directive | None = None
    #: Classement de la décision d'un checkpoint (W2-01) —
    #: :meth:`grimoire.flows.attestation.Attestation.to_dict`, ``None`` hors
    #: checkpoint ou sur un rejeu.
    attestation: dict[str, Any] | None = None
    #: Avertissements nommés : décision ``unattested`` acceptée sur un
    #: checkpoint non destructif (W2-01, décision 2).
    warnings: tuple[str, ...] = ()
    #: ``True`` quand le node vient d'être accepté mais qu'un ``hold`` (K11) a posé le run en ``PAUSED`` à la
    #: frontière : ``node_id`` nomme le node suivant, qui n'est ni présenté ni exécuté, ``contract`` est vide.
    #: Un pilote qui enchaîne les nodes s'arrête là.
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "directive": self.directive.to_dict() if self.directive else None,
            "ok": self.ok,
            "finished": self.finished,
            "node_id": self.node_id,
            "faults": list(self.faults),
            "contract": self.contract.to_dict() if self.contract else None,
            "replay": self.replay,
            "replay_of": self.replay_of,
            "repaired": list(self.repaired),
            "attestation": self.attestation,
            "warnings": list(self.warnings),
            "paused": self.paused,
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
    #: ``FlowRunMeta.parent_run_id`` (issue #206) — ``None`` pour un run
    #: racine. Recopié ici pour que ``flow status``/``flow run`` (sans
    #: argument, la liste des runs) puissent l'afficher sans redemander la
    #: méta directement.
    parent_run_id: str | None = None
    #: La directive courante (W2-03), ``None`` quand la vue est une simple ligne de liste.
    directive: Directive | None = None
    #: Décisions de checkpoint journalisées (W2-01) : node, décision, motif
    #: (celui d'un ``amend`` compris), attestation et son motif, horodatage.
    checkpoint_decisions: tuple[dict[str, Any], ...] = ()
    #: W2-04 — le constat ``blueprint_drift`` (nodes franchis modifiés, retirés ou
    #: réordonnés depuis le lancement) ou ``None``. ``blueprint_sha256`` ``None``
    #: dit « non vérifié » (run antérieur) : ``None`` ici ne veut alors pas dire « sain ».
    blueprint_drift: dict[str, Any] | None = None
    blueprint_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "directive": self.directive.to_dict() if self.directive else None,
            "blueprint_sha256": self.blueprint_sha256,
            "blueprint_drift": self.blueprint_drift,
            "run_id": self.run_id,
            "blueprint_id": self.blueprint_id,
            "status": self.status,
            "current_node": self.current_node,
            "completed_nodes": list(self.completed_nodes),
            "pending_nodes": list(self.pending_nodes),
            "last_refusal": self.last_refusal,
            "contract": self.contract.to_dict() if self.contract else None,
            "parent_run_id": self.parent_run_id,
            "checkpoint_decisions": [dict(d) for d in self.checkpoint_decisions],
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
