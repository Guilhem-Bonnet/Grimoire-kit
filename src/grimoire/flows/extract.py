"""``flow extract`` — un flow s'extrait d'un run, il ne se dessine pas (transversale T2, issue #210).

Le moteur enregistre déjà la séquence exécutée d'un run : ``FlowRunMeta``
(ordre topologique, blueprint source) côté ``RuntimeKernel``/``FlowEngine``,
et — pour tout node passé par ``DispatchExecutor`` — la commande réellement
exécutée et son verdict, dans le Mission Ledger (``node_dispatch_history``).
Ce module joint les deux pour produire un blueprint brouillon, dans le même
format ``.blueprint.json`` que le Studio et le moteur savent déjà lire :
créer un flow devient éditer quelque chose qui a déjà fonctionné.

Règle d'or (issue #210) : **rien de ce qui n'a pas été observé n'est
inventé.** Un node dont l'acceptance a réellement tourné (au moins un check
en dehors de l'enveloppe interne du gate) voit son acceptance déclarée
remplacée par ce qui a réellement tourné — jamais par une inférence depuis
la prose de l'auteur. Un node jamais dispatché (V2, jamais atteint, ou
exécuté par un hôte interactif sans passer par la cascade) garde son
acceptance d'origine verbatim, et son statut d'extraction le dit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.execution_needs import resolve_execution_needs
from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.dispatch_executor import node_dispatch_history
from grimoire.flows.engine import FlowEngine
from grimoire.missions.service import TaskService
from grimoire.missions.verifiability import classify

__all__ = ["ExtractedNode", "extract_blueprint"]

#: Marqueur du check d'enveloppe interne que ``DispatchExecutor`` ajoute
#: devant toute acceptance déclarée (``_verify_command``, ``flows.dispatch_executor``) —
#: jamais une commande que l'auteur du blueprint a déclarée, jamais réinjectée
#: dans le brouillon.
_ENVELOPE_CHECK_MARKER = "grimoire.flows.dispatch_executor"


@dataclass(frozen=True, slots=True)
class ExtractedNode:
    """Ce que l'extraction a observé pour un node — traçabilité du brouillon."""

    node_id: str
    status: str  # "completed" | "blocked" | "host_pending" | "not_reached"
    verifiability: str | None  # "V0" / "V1" / "V2", None si jamais classé (pas de tâche créée)
    observed_commands: tuple[str, ...]  # commandes réellement exécutées, vides si aucune
    needs_inferred: tuple[str, ...]  # sous-ensemble d'observed_commands reconnu comme besoin résolu
    note: str | None = None


def _task_verifiability(project_root: Path, run_id: str, node_id: str) -> str | None:
    """La classe de vérifiabilité observée pour ce node, ou ``None`` sans tâche.

    Une tâche existe dès que ``DispatchExecutor`` a été invoqué sur ce node —
    y compris pour un node refusé V2 avant tout dispatch (``_find_or_create_task``
    tourne avant la classification). Un node jamais présenté à
    ``DispatchExecutor`` (exécution interactive, ou node jamais atteint) n'a
    pas de tâche : ``None``, jamais une classe devinée.
    """
    task = TaskService(project_root).ledger.get_task(f"FLOW-{run_id}-{node_id}")
    if task is None:
        return None
    return classify(task).value


def _observed_acceptance(
    checks: tuple[dict[str, Any], ...], command_to_need: dict[str, str]
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Les checks réellement exécutés (hors enveloppe interne) → acceptance structurée.

    Rend ``(entries, needs_inferred)`` : chaque commande devient ``{"run_need":
    ...}`` si elle correspond, caractère pour caractère, à la commande qu'un
    besoin du catalogue résout *pour ce projet, maintenant* — sinon
    ``{"run": ...}`` verbatim. Jamais l'inverse : une commande qui ne
    correspond à aucun besoin résolu reste une commande en dur, pas une
    supposition.
    """
    entries: list[dict[str, Any]] = []
    needs_inferred: list[str] = []
    for check in checks:
        cmd = str(check.get("cmd", ""))
        if not cmd or _ENVELOPE_CHECK_MARKER in cmd:
            continue
        need_id = command_to_need.get(cmd)
        entry: dict[str, Any]
        if need_id is not None:
            entry = {"run_need": need_id}
            needs_inferred.append(need_id)
        else:
            entry = {"run": cmd}
        exit_code = check.get("exit_code")
        if check.get("ok") and isinstance(exit_code, int) and exit_code != 0:
            # Le check a été déclaré vert sur ce code de sortie précis (un
            # ``expect_exit`` non nul) — l'omettre referait du brouillon un
            # nœud qui refuse au rejeu ce que l'original acceptait.
            entry["expect_exit"] = exit_code
        entries.append(entry)
    return tuple(entries), tuple(needs_inferred)


def extract_blueprint(engine: FlowEngine, run_id: str, project_root: Path) -> tuple[dict[str, Any], tuple[ExtractedNode, ...]]:
    """Le blueprint brouillon extrait de *run_id*, et la trace d'extraction par node.

    Rend un dict déjà dans la forme ``.blueprint.json`` (même
    ``blueprintVersion``/``id``/``nodes``/``edges`` que le format existant) —
    aucun nouveau format, conformément à la doctrine de #201 : « il ne
    propose pas de nouveau format ». Le second élément du tuple est la preuve
    de ce qui a été observé par node, pour que la CLI (ou un futur rapport de
    run) puisse l'afficher sans redemander au Mission Ledger.
    """
    meta = engine.run_meta(run_id)
    original = load_blueprint(Path(meta.blueprint_path))
    status = engine.status(run_id, include_contract=False)
    nodes_by_id = {node["id"]: node for node in original["nodes"]}
    completed = set(status.completed_nodes)
    current = status.current_node

    history = {row["node_id"]: row for row in node_dispatch_history(project_root, run_id, list(meta.order))}
    resolved_needs = resolve_execution_needs(project_root)
    command_to_need = {r.command: need_id for need_id, r in resolved_needs.items() if r.command}

    draft_nodes: list[dict[str, Any]] = []
    extraction_trace: list[ExtractedNode] = []
    for node_id in meta.order:
        node = dict(nodes_by_id[node_id])  # copie : ne mute jamais le blueprint source
        row = history.get(node_id)
        verifiability = _task_verifiability(project_root, run_id, node_id)

        if node_id in completed:
            node_status = "completed"
            note = None
        elif node_id == current:
            if status.last_refusal is not None and status.last_refusal.get("node_id") == node_id:
                node_status, note = "blocked", str(status.last_refusal.get("reason") or "")
            else:
                node_status, note = "host_pending", None
        else:
            # Couvre à la fois ``pending_nodes`` (run encore vivant) et le node
            # jamais atteint d'un run mort en route (ABORTED/REFUSED) : dans
            # les deux cas, aucune tâche de dispatch n'a pu être créée pour ce
            # node — ``not_reached`` est la seule description honnête.
            node_status, note = "not_reached", None

        observed_commands: tuple[str, ...] = ()
        needs_inferred: tuple[str, ...] = ()
        if node_status == "completed" and row is not None and row.get("checks"):
            entries, needs_inferred = _observed_acceptance(tuple(row["checks"]), command_to_need)
            if entries:
                node["acceptance"] = list(entries)
                observed_commands = tuple(e.get("run") or f"run_need:{e.get('run_need')}" for e in entries)
            # Aucun check observé hors enveloppe (ex. node purement interactif
            # dispatché mais sans acceptance structurée) : l'acceptance
            # d'origine survit telle quelle, jamais remplacée par du vide.
        node["extraction"] = {"status": node_status, **({"note": note} if note else {})}
        draft_nodes.append(node)
        extraction_trace.append(
            ExtractedNode(
                node_id=node_id,
                status=node_status,
                verifiability=verifiability,
                observed_commands=observed_commands,
                needs_inferred=needs_inferred,
                note=note,
            )
        )

    draft_id = f"{run_id.lower().removeprefix('wfi-')}-extract"
    draft: dict[str, Any] = {
        "blueprintVersion": 1,
        "id": draft_id,
        "name": f"Extrait du run {run_id}",
        "description": f"Brouillon extrait de {meta.blueprint_id} (run {run_id}) — texte d'abord, éditable au Studio.",
        "nodes": draft_nodes,
        "edges": list(original.get("edges", [])),
        "extractedFrom": {
            "run_id": run_id,
            "blueprint_id": meta.blueprint_id,
            "blueprint_path": meta.blueprint_path,
            "run_status": status.status,
            "extracted_at": datetime.now(tz=UTC).isoformat(),
        },
    }
    if "catalogRef" in original:
        draft["catalogRef"] = original["catalogRef"]
    return draft, tuple(extraction_trace)
