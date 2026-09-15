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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Miroir du chemin que ``cmd_upgrade_flow.py``/``project_update.py``
#: passent à ``FlowEngine(flows_root=...)`` — jamais réimporté depuis là pour
#: éviter de charger le moteur entier juste pour une lecture.
_FLOWS_RELPATH = Path("_grimoire-runtime-output") / "flows"


def _flows_dir(project_root: Path) -> Path:
    return project_root / _FLOWS_RELPATH


def list_flow_runs(
    project_root: Path, *, blueprint_id: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Les runs connus de ce projet, les plus récents d'abord.

    Une lecture tolérante : un fichier de métadonnées absent, corrompu ou
    incomplet est ignoré plutôt que de faire échouer toute la liste — un run
    en cours d'écriture au moment précis de la lecture ne doit pas casser
    l'affichage des autres. ``blueprint_id`` filtre (ex. ``"project-upgrade"``) ;
    omis, tous les runs de flow du projet.
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
    return runs[:limit]
