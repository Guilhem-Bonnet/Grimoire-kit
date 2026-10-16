"""Export / import d'un run de flow (W2-04) : un fichier JSON, sans chemin absolu.

Le paquet porte la méta du run (chemin du blueprint relatif à la racine du
projet), l'instance du kernel avec ses événements et checkpoints, et le texte du
blueprint. À l'import, le blueprint n'est écrit que si son chemin n'existe pas
déjà : s'il existe et diffère, il n'est PAS écrasé et le constat
``blueprint_drift`` du run dit ce qui ne colle plus.

Limites : les blueprints des sous-flows (``composite``) ne sont pas embarqués ;
le Mission Ledger (historique de dispatch) n'est pas transporté.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.fsio import atomic_write
from grimoire.flows.fingerprint import relative_blueprint_path
from grimoire.flows.schemas import FlowRunMeta

if TYPE_CHECKING:
    from grimoire.flows.engine import FlowEngine

__all__ = ["EXPORT_SCHEMA", "export_run", "import_run"]

EXPORT_SCHEMA = "grimoire.flow_export.v1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_FALLBACK_DIR = "_grimoire-runtime-output/flows/blueprints"


def export_run(engine: FlowEngine, run_id: str, project_root: Path) -> dict[str, Any]:
    """Le paquet exportable du run — lecture seule."""
    meta = engine.run_meta(run_id)
    blueprint = engine.blueprint_file(run_id)
    try:
        text = blueprint.read_text(encoding="utf-8")
    except OSError as exc:
        raise GrimoireRuntimeError(f"run {run_id} : blueprint illisible ({blueprint}) : {exc}") from exc
    portable = relative_blueprint_path(blueprint, project_root)
    return {
        "schema": EXPORT_SCHEMA,
        "meta": {**meta.to_dict(), "blueprint_path": portable},
        "blueprint": {"path": portable, "text": text},
        "kernel": engine.kernel_export(run_id),
    }


def _target(rel: str, run_id: str, root: Path) -> str:
    """Le chemin relatif où poser le blueprint : celui du paquet s'il reste sous la racine, sinon un repli."""
    p = Path(rel)
    inside = not p.is_absolute() and (root / p).resolve().is_relative_to(root)
    return p.as_posix() if inside else f"{_FALLBACK_DIR}/{run_id}.blueprint.json"


def import_run(engine: FlowEngine, bundle: dict[str, Any], project_root: Path) -> FlowRunMeta:
    """Recrée le run dans ce projet. Refuse un paquet inconnu ou un ``run_id`` déjà présent."""
    if not isinstance(bundle, dict) or bundle.get("schema") != EXPORT_SCHEMA:
        raise GrimoireRuntimeError(f"paquet d'export inconnu (schéma attendu : {EXPORT_SCHEMA})")
    try:
        meta = FlowRunMeta.from_dict(bundle["meta"])
        text = bundle["blueprint"]["text"]
        kernel_data = bundle["kernel"]
        json.dumps(kernel_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise GrimoireRuntimeError(f"paquet d'export illisible : {exc!r}") from exc
    if not _SAFE_ID.fullmatch(meta.run_id) or kernel_data.get("instance", {}).get("id") != meta.run_id:
        raise GrimoireRuntimeError("paquet d'export refusé : identifiant de run invalide ou incohérent avec l'instance")
    root = project_root.resolve()
    if meta.run_id in engine.list_run_ids() or engine.has_instance(meta.run_id):
        raise GrimoireRuntimeError(f"run {meta.run_id} existe déjà dans ce projet ; rien n'a été écrit.")
    rel = _target(meta.blueprint_path, meta.run_id, root)
    dest = root / rel
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dest, text)
    imported = FlowRunMeta.from_dict({**meta.to_dict(), "blueprint_path": rel})
    engine.kernel_import(kernel_data)
    engine.save_imported_meta(imported)
    return imported
