"""Fusion à trois voies du registre d'outils, pour ``grimoire up``.

``tool-registry.yaml`` est un gabarit que le kit livre **et** un registre que le
projet complète (serveurs MCP ``out_of_scope``, ``error_capture``, outils
ajoutés). Le manifeste de génération ne sait dire que « édité ou pas » : un
fichier édité était donc figé tel quel, et les nouveautés du kit n'arrivaient
jamais ; un fichier jugé intact était réécrit en entier.

Ici, un fichier que le projet a touché est fusionné avec le gabarit courant en
s'appuyant sur le gabarit de la dernière synchronisation (la « base », rangée
dans :data:`BASE_DIR`) :

- une valeur que le projet a changée par rapport à la base est conservée ;
- une valeur restée celle de la base suit le nouveau gabarit ;
- une clé ou un élément (identifié par ``id``) nouveau dans le gabarit arrive ;
- une clé ou un élément supprimé par le projet reste supprimé ;
- un élément que le kit ne livre plus est conservé et signalé (jamais supprimé),
  comme pour les orphelins d'agents.

Sans base (projet antérieur à ce mécanisme), seules les clés manquantes sont
ajoutées : rien d'autre n'est déduit. Un fichier illisible ou d'un autre schéma
n'est jamais touché : il est copié à côté et signalé.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.standard_generation import STANDARD_DIR

#: Dernier gabarit synchronisé, par nom de fichier : la base de la fusion.
BASE_DIR = STANDARD_DIR / ".generated-base"

_MISSING = object()


@dataclass(slots=True)
class MergeOutcome:
    """Ce que la fusion a fait, et ce que le projet doit savoir."""

    changed: bool = False
    notices: list[str] = field(default_factory=list)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _loads(text: str) -> Any:
    return _yaml().load(text)


def base_path(root: Path, destination: str | Path) -> Path:
    return root / BASE_DIR / Path(destination).name


def record_base(root: Path, destination: str | Path, rendered: str) -> None:
    """Retient le gabarit rendu comme base de la prochaine fusion (best-effort)."""
    path = base_path(root, destination)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    except OSError:
        pass


def _id_list(value: Any) -> bool:
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(i, dict) and i.get("id") is not None for i in value)
    )


def _merge_list(proj: list[Any], base: Any, tmpl: list[Any], where: str, notes: list[str]) -> bool:
    if not isinstance(base, list):
        return False  # sans base on ne sait pas distinguer « nouveau » de « retiré »
    changed = False
    base_by_id = {i["id"]: i for i in base if isinstance(i, dict) and "id" in i}
    proj_by_id = {i["id"]: i for i in proj if isinstance(i, dict) and "id" in i}
    tmpl_ids = set()
    for item in tmpl:
        tid = item["id"]
        tmpl_ids.add(tid)
        if tid in proj_by_id:
            changed |= _merge_map(proj_by_id[tid], base_by_id.get(tid), item, f"{where}[{tid}].", notes)
        elif tid not in base_by_id:
            proj.append(item)
            notes.append(f"ajout kit : {where}[{tid}]")
            changed = True
    for bid in base_by_id:
        if bid not in tmpl_ids and bid in proj_by_id:
            notes.append(f"{where}[{bid}] n'est plus livré par le kit : conservé")
    return changed


def _merge_map(proj: Any, base: Any, tmpl: dict[str, Any], where: str, notes: list[str]) -> bool:
    base = base if isinstance(base, dict) else None
    changed = False
    for key, tv in tmpl.items():
        if key not in proj:
            if base is None or key not in base:
                proj[key] = tv
                notes.append(f"ajout kit : {where}{key}")
                changed = True
            continue
        pv = proj[key]
        bv = base.get(key, _MISSING) if base is not None else _MISSING
        if isinstance(pv, dict) and isinstance(tv, dict):
            changed |= _merge_map(pv, bv, tv, f"{where}{key}.", notes)
        elif _id_list(pv) and _id_list(tv):
            changed |= _merge_list(pv, bv, tv, f"{where}{key}", notes)
        elif bv is not _MISSING and pv == bv and pv != tv:
            proj[key] = tv
            notes.append(f"mise à jour kit : {where}{key}")
            changed = True
    if base is not None:
        for key in base:
            if key not in tmpl and key in proj:
                notes.append(f"{where}{key} n'est plus livré par le kit : conservé")
    return changed


def _backup_unreadable(path: Path) -> str:
    """Copie à côté un fichier qu'on ne sait pas lire, une fois par contenu."""
    try:
        data = path.read_bytes()
        for old in path.parent.glob(f"{path.name}.unreadable-*"):
            if old.read_bytes() == data:
                return old.name
        target = path.with_name(f"{path.name}.unreadable-{datetime.now(UTC):%Y%m%dT%H%M%S}")
        target.write_bytes(data)
        return target.name
    except OSError:
        return "(sauvegarde impossible)"


def merge_project_registry(
    root: Path, destination: str | Path, rendered: str, *, dry_run: bool = False,
) -> MergeOutcome:
    """Fusionne le registre du projet avec le gabarit *rendered* ; voir le module."""
    path = root / destination
    outcome = MergeOutcome()
    try:
        tmpl = _loads(rendered)
        raw = path.read_text(encoding="utf-8")
        proj = _loads(raw)
    except (OSError, UnicodeDecodeError, YAMLError):
        proj = None
    if not isinstance(proj, dict) or not isinstance(tmpl, dict) or proj.get("$schema") != tmpl.get("$schema"):
        saved = "" if dry_run else f" ; copie : {_backup_unreadable(path)}"
        outcome.notices.append(
            f"{Path(destination).name} illisible ou de schéma inattendu : laissé tel quel, "
            f"non fusionné{saved}. Corriger le fichier, puis relancer `grimoire up`."
        )
        return outcome
    base_file = base_path(root, destination)
    try:
        base = _loads(base_file.read_text(encoding="utf-8")) if base_file.is_file() else None
    except (OSError, UnicodeDecodeError, YAMLError):
        base = None
    outcome.changed = _merge_map(proj, base, tmpl, "", outcome.notices)
    if dry_run:
        return outcome
    if outcome.changed:
        out = io.StringIO()
        _yaml().dump(proj, out)
        path.write_text(out.getvalue(), encoding="utf-8")
    record_base(root, destination, rendered)
    return outcome


def sync_registry(
    root: Path, destination: str | Path, rendered: str, action: str,
    refresh: bool, dry_run: bool, result: Any,
) -> bool:
    """Branche la fusion sur ``setup_standard_profile`` ; True si l'artefact est traité.

    *action* est la décision de :func:`grimoire.core.standard_generation.decide`.
    Un fichier que le projet a touché (``keep``) est fusionné lors d'une mise à
    jour (*refresh*) ; sinon l'appelant écrit ou saute comme avant, et la base
    de fusion suit ce qui vient d'être écrit ou adopté.
    """
    if action == "keep" and refresh:
        merged = merge_project_registry(root, destination, rendered, dry_run=dry_run)
        result.notices.extend(merged.notices)
        (result.written if merged.changed else result.skipped).append(Path(destination))
        return True
    if action in {"adopt", "write"} and not dry_run:
        record_base(root, destination, rendered)
    return False
