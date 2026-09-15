"""Lister les runs de flow d'un projet — sans le moteur, juste leurs métadonnées.

Issue #506 (PR B) : après un `grimoire upgrade-flow run`, l'espace Observer
affichait « Aucune trace : le TraceLedger de ce projet est vide » — vrai pour
le TraceLedger (dispatch/agent-miss, ``_grimoire-runtime-output/hook-runtime/
events.jsonl``), mais trompeur : un flow a bien tourné, ailleurs
(``FlowEngine`` écrit une :class:`~grimoire.flows.schemas.FlowRunMeta` par
run sous ``_grimoire-runtime-output/flows/<run_id>.json``, un mécanisme
entièrement distinct du TraceLedger). Ce module lit ces métadonnées
directement — jamais via :class:`~grimoire.flows.engine.FlowEngine`, qui
ouvre aussi un ``RuntimeKernel`` et crée des répertoires : une lecture
d'affichage n'a besoin ni de l'un ni de l'autre.

Issue #510/#513 (restes) : le badge Piloter « checkpoint destructif en
attente » ne doit pas être un état purement client (perdu à la première
navigation Flotte → Projet), mais dériver du run réel. `FlowRunMeta` seule
ne porte que le *quoi* (blueprint, ordre) — jamais le *où en est-il*, qui
vit côté ``RuntimeKernel``. :func:`list_flow_runs` pose donc `status` et
`currentNode` sur chaque run qu'elle rend, en n'interrogeant le moteur que
pour ceux déjà retenus après tri/troncature — jamais pour la liste entière,
qui resterait le cas dégénéré que le docstring ci-dessus met en garde.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Miroir du chemin que ``cmd_upgrade_flow.py``/``project_update.py``
#: passent à ``FlowEngine(flows_root=...)`` — jamais réimporté depuis là pour
#: éviter de charger le moteur entier juste pour une lecture.
_FLOWS_RELPATH = Path("_grimoire-runtime-output") / "flows"

#: Même chemin que ``FlowEngine(kernel_root=...)`` reçoit ailleurs
#: (``cmd_flow.py``, ``cmd_upgrade_flow.py``) — la seule source du statut
#: live (`status`/`currentNode`) qu'une métadonnée de run ne porte pas.
_KERNEL_RELPATH = Path("_grimoire-runtime-output") / "runtime"


def _flows_dir(project_root: Path) -> Path:
    return project_root / _FLOWS_RELPATH


def _attach_live_status(project_root: Path, runs: list[dict[str, Any]]) -> None:
    """Pose `status`/`currentNode` sur chaque run déjà retenu, depuis le kernel.

    Un run dont le kernel n'a plus trace (nettoyage externe), ou dont la
    métadonnée est incomplète (fixture de test qui n'écrit que `run_id`/
    `blueprint_id`/`created_at` — le contrat minimal que le reste de ce
    module tolère déjà), garde les deux champs à ``None`` — jamais une
    exception qui casserait toute la liste pour un seul run illisible.
    `Exception` largement plutôt que `GrimoireRuntimeError` seul : une
    métadonnée sans `blueprint_path` lève un `KeyError` bien avant que le
    moteur n'ait la moindre chance de refuser proprement.
    """
    if not runs:
        return
    from grimoire.flows.engine import FlowEngine

    engine = FlowEngine(
        kernel_root=project_root / _KERNEL_RELPATH,
        flows_root=_flows_dir(project_root),
        project_root=project_root,
    )
    for run in runs:
        try:
            view = engine.status(str(run["runId"]), include_contract=False)
            status, current_node = view.status, view.current_node
        except Exception:  # lecture annexe : ne doit jamais masquer la liste des runs
            status, current_node = None, None
        run["status"] = status
        run["currentNode"] = current_node


def list_flow_runs(
    project_root: Path, *, blueprint_id: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Les runs connus de ce projet, les plus récents d'abord.

    Une lecture tolérante : un fichier de métadonnées absent, corrompu ou
    incomplet est ignoré plutôt que de faire échouer toute la liste — un run
    en cours d'écriture au moment précis de la lecture ne doit pas casser
    l'affichage des autres. ``blueprint_id`` filtre (ex. ``"project-upgrade"``) ;
    omis, tous les runs de flow du projet.

    Chaque run rendu porte aussi `status` (``WorkflowStatus.value`` — ex.
    ``"checkpointed"``) et `currentNode` (le node courant, ou ``None``),
    lus depuis le kernel (issue #510/#513) : c'est ce qu'un badge « checkpoint
    en attente » a besoin de savoir pour survivre à une navigation, plutôt
    que de rester un état client perdu au premier rendu suivant.
    """
    directory = _flows_dir(project_root)
    if not directory.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        run_bp = str(data.get("blueprint_id", ""))
        if blueprint_id is not None and run_bp != blueprint_id:
            continue
        runs.append({
            "runId": str(data.get("run_id", path.stem)),
            "blueprintId": run_bp,
            "createdAt": data.get("created_at"),
        })
    runs.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
    sliced = runs[:limit]
    _attach_live_status(project_root, sliced)
    return sliced
