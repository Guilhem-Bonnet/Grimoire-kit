"""Export / import d'un run de flow (W2-04) : un fichier JSON, sans chemin absolu.

Le paquet porte la méta du run (chemin du blueprint relatif à la racine du
projet), l'instance du kernel avec ses événements et checkpoints, et le texte du
blueprint. À l'import, le texte doit être un blueprint valide (JSON, structure,
``id`` cohérent avec le run) et n'est posé que sous un nom ``*.blueprint.json``
dans le projet (sinon repli sous ``_grimoire-runtime-output/flows/blueprints/``) :
un paquet n'écrit jamais ``conftest.py`` ni autre fichier exécutable. Si le chemin
existe déjà, le blueprint local n'est jamais écrasé et doit COMPILER à l'identique
de celui du paquet (nodes, contrats, ordre) ; sinon l'import est refusé avant toute
écriture, en nommant l'écart.

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
from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, topo_order
from grimoire.flows.fingerprint import blueprint_divergence, relative_blueprint_path
from grimoire.flows.schemas import FlowRunMeta
from grimoire.tools.ext_manager import validate_blueprint_data

if TYPE_CHECKING:
    from grimoire.flows.engine import FlowEngine

__all__ = ["EXPORT_SCHEMA", "export_run", "import_run"]

EXPORT_SCHEMA = "grimoire.flow_export.v1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_FALLBACK_DIR = "_grimoire-runtime-output/flows/blueprints"
_BLUEPRINT_SUFFIX = ".blueprint.json"


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
    """Le chemin relatif où poser le blueprint : celui du paquet s'il reste sous la racine ET se nomme ``*.blueprint.json``, sinon un repli."""
    p = Path(rel)
    inside = not p.is_absolute() and (root / p).resolve().is_relative_to(root)
    if inside and p.name.endswith(_BLUEPRINT_SUFFIX) and p.name != _BLUEPRINT_SUFFIX:
        return p.as_posix()
    return f"{_FALLBACK_DIR}/{run_id}.blueprint.json"


def _packaged_blueprint(text: Any, meta: FlowRunMeta) -> dict[str, Any]:
    """Le blueprint du paquet décodé et validé, ou un refus nommé — avant toute écriture."""
    if not isinstance(text, str):
        raise GrimoireRuntimeError(f"paquet d'export refusé : le blueprint n'est pas un texte ({type(text).__name__})")
    try:
        text.encode("utf-8")  # une séquence orpheline ferait échouer l'écriture (et la frontière PyO3) après coup
    except UnicodeEncodeError as exc:
        raise GrimoireRuntimeError(f"paquet d'export refusé : le blueprint n'est pas encodable en UTF-8 ({exc})") from exc
    try:
        blueprint: Any = json.loads(text)
    except ValueError as exc:
        raise GrimoireRuntimeError(f"paquet d'export refusé : le texte du blueprint n'est pas du JSON ({exc})") from exc
    errors = validate_blueprint_data(blueprint)
    if errors:
        raise GrimoireRuntimeError("paquet d'export refusé : blueprint invalide — " + "; ".join(errors))
    if blueprint["id"] != meta.blueprint_id:
        raise GrimoireRuntimeError(
            f"paquet d'export refusé : le blueprint s'appelle {blueprint['id']!r}, le run attend {meta.blueprint_id!r}"
        )
    try:
        topo_order(blueprint)
    except (GrimoireRuntimeError, KeyError, TypeError, AttributeError) as exc:
        raise GrimoireRuntimeError(f"paquet d'export refusé : blueprint inexploitable ({exc!r})") from exc
    packaged: dict[str, Any] = blueprint
    return packaged


def _refuse_diverging_local(dest: Path, packaged: dict[str, Any], root: Path, rel: str) -> None:
    """Un blueprint local déjà présent doit compiler comme celui du paquet, sinon refus (rien n'est écrit)."""
    local = load_blueprint(dest, root)
    try:
        local_order, packaged_order = topo_order(local), topo_order(packaged)
        local_contracts = {n: c.to_dict() for n, c in build_node_contracts(local, root).items()}
        packaged_contracts = {n: c.to_dict() for n, c in build_node_contracts(packaged, root).items()}
    except (KeyError, TypeError, AttributeError) as exc:
        raise GrimoireRuntimeError(f"{rel} : blueprint local inexploitable ({exc!r}) ; rien n'a été écrit.") from exc
    why = blueprint_divergence(packaged_order, packaged_contracts, local_order, local_contracts)
    if why is not None:
        raise GrimoireRuntimeError(
            f"import refusé : le blueprint local {rel} diverge de celui du run exporté "
            f"(premier = paquet, second = local) — {why} ; rien n'a été écrit. "
            "Rétablir ce blueprint, ou l'importer dans un projet qui ne le porte pas."
        )


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
    packaged = _packaged_blueprint(text, meta)
    rel = _target(meta.blueprint_path, meta.run_id, root)
    dest = root / rel
    if dest.exists():
        _refuse_diverging_local(dest, packaged, root, rel)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dest, text)
    imported = FlowRunMeta.from_dict({**meta.to_dict(), "blueprint_path": rel})
    engine.kernel_import(kernel_data)
    engine.save_imported_meta(imported)
    return imported
