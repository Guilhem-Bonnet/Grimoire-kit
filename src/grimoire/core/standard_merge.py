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
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.fsio import atomic_write
from grimoire.core.standard_generation import STANDARD_DIR

#: Dernier gabarit synchronisé, par nom de fichier : la base de la fusion.
BASE_DIR = STANDARD_DIR / ".generated-base"

_MISSING = object()


@dataclass(slots=True)
class MergeOutcome:
    """Ce que la fusion a fait, et ce que le projet doit savoir."""

    changed: bool = False
    notices: list[str] = field(default_factory=list)
    #: Conflits sur un champ de sûreté : le projet l'emporte, mais ce n'est pas anodin.
    conflicts: list[str] = field(default_factory=list)


class _Notes(list[str]):
    """Messages de fusion, avec à part les conflits sur des champs de sûreté."""

    def __init__(self) -> None:
        super().__init__()
        self.conflicts: list[str] = []


#: Champs qui gouvernent les verdicts de sûreté du standard.
_SAFETY_KEYS = frozenset({"risk", "permissions", "out_of_scope", "out_of_scope_reason"})


#: Champs des serveurs MCP lus par les vérificateurs.
_MCP_SAFETY_KEYS = frozenset({"owner", "scopes", "timeout_s", "logging"})


def _is_safety_field(where: str, key: str) -> bool:
    path = f"{where}{key}"
    return (
        key in _SAFETY_KEYS
        or path == "error_capture.policy"
        or path.startswith("allowed.")  # vocabulaire qui sert à juger le registre
        or (where.startswith("mcp_servers[") and key in _MCP_SAFETY_KEYS)
    )


def _plain(value: Any) -> Any:
    """Valeur YAML en types natifs, pour l'affichage."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _ids(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(i, dict) and i.get("id") is not None for i in value)


def _diff(proj: Any, tmpl: Any, where: str, out: list[str]) -> None:
    """Liste ce que le projet a posé et que le gabarit du kit ne reprend pas."""
    if isinstance(proj, dict) and isinstance(tmpl, dict):
        for key in proj:
            if key in tmpl:
                _diff(proj[key], tmpl[key], f"{where}{key}.", out)
            else:
                out.append(f"{where}{key} : projet = {_plain(proj[key])!r} -> kit = (absent)")
        for key in tmpl:
            if key not in proj:
                out.append(f"{where}{key} : projet = (absent) -> kit = {_plain(tmpl[key])!r}")
    elif _ids(proj) and _ids(tmpl) and (proj or tmpl):
        pmap = {i["id"]: i for i in proj}
        tmap = {i["id"]: i for i in tmpl}
        for tid, item in pmap.items():
            if tid in tmap:
                _diff(item, tmap[tid], f"{where.rstrip('.')}[{tid}].", out)
            else:
                out.append(f"{where.rstrip('.')}[{tid}] : projet = {_plain(item)!r} -> kit = (absent)")
        for tid, item in tmap.items():
            if tid not in pmap:
                out.append(f"{where.rstrip('.')}[{tid}] : projet = (absent) -> kit = {_plain(item)!r}")
    elif proj != tmpl:
        out.append(f"{where.rstrip('.')} : projet = {_plain(proj)!r} -> kit = {_plain(tmpl)!r}")


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


def _merge_list(proj: list[Any], base: Any, tmpl: list[Any], where: str, notes: _Notes) -> bool:
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


def _merge_map(proj: Any, base: Any, tmpl: dict[str, Any], where: str, notes: _Notes) -> bool:
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
        elif bv is not _MISSING and pv != bv and tv != bv and pv != tv:
            if _is_safety_field(where, key):
                notes.conflicts.append(f"{where}{key} : projet = {_plain(pv)!r}, kit = {_plain(tv)!r}")
            notes.append(f"conflit sur {where}{key} : valeur du projet conservée")
    if base is not None:
        for key in base:
            if key not in tmpl and key in proj:
                notes.append(f"{where}{key} n'est plus livré par le kit : conservé")
    return changed


def _backup_unreadable(path: Path, tag: str = "unreadable") -> str:
    """Copie à côté un fichier, horodatée, une fois par contenu."""
    try:
        data = path.read_bytes()
        for old in path.parent.glob(f"{path.name}.{tag}-*"):
            if not old.is_symlink() and old.is_file() and old.read_bytes() == data:
                return old.name
        stamp = f"{path.name}.{tag}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
        # Nom libre d'abord (``lstat`` : fichier, lien ou autre chose, tout nom
        # pris est écarté, jamais suivi), puis écriture atomique de ``fsio``.
        # ``os.replace`` remplace l'entrée du dossier sans suivre un lien posé au
        # nom cible : rien n'est écrit hors du projet. Limite connue : entre la
        # vérification et l'écriture, un fichier créé au même nom serait remplacé ;
        # la vraie création exclusive demanderait une fonction dans ``fsio``.
        for attempt in range(8):
            name = stamp if attempt == 0 else f"{stamp}-{secrets.token_hex(3)}"
            try:
                os.lstat(path.with_name(name))
            except FileNotFoundError:
                atomic_write(path.with_name(name), data)
                return name
        return "(sauvegarde impossible)"
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
    notes = _Notes()
    outcome.changed = _merge_map(proj, base, tmpl, "", notes)
    outcome.notices.extend(notes)
    outcome.conflicts.extend(notes.conflicts)
    if dry_run:
        return outcome
    if outcome.changed:
        out = io.StringIO()
        _yaml().dump(proj, out)
        path.write_text(out.getvalue(), encoding="utf-8")
    record_base(root, destination, rendered)
    return outcome


def _force_report(root: Path, destination: str | Path, rendered: str, dry_run: bool) -> tuple[list[str], bool]:
    """Sauvegarde le registre existant et dit ce que le gabarit va remplacer.

    Renvoie (messages, écrasement permis) : sans sauvegarde réussie, il ne l'est pas.
    """
    path = root / destination
    name = Path(destination).name
    verb = "serait sauvegardé" if dry_run else "sauvegardé"
    saved = "" if dry_run else _backup_unreadable(path, "backup")
    backup_failed = saved == "(sauvegarde impossible)"
    where = f"{path.parent.relative_to(root).as_posix()}/{saved}" if saved else "(à côté du registre)"
    try:
        tmpl = YAML(typ="safe").load(rendered)
        proj = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError):
        proj = None
    lost: list[str] = []
    unreadable = not isinstance(proj, dict) or not isinstance(tmpl, dict) or proj.get("$schema") != tmpl.get("$schema")
    if not unreadable:
        _diff(proj, tmpl, "", lost)
    lines = [f"  - {line}" for line in lost]
    if backup_failed:
        head = f"--force : sauvegarde impossible : registre laissé en place, rien écrasé ({name})"
        return [head, *(["réglages du projet qui auraient été perdus :", *lines] if lines else [])], False
    if unreadable:
        return [f"--force : {name} illisible ou de schéma inattendu, {verb} dans {where} puis remplacé par le gabarit du kit"], True
    if not lost:
        return [], True
    return [f"--force : {name} remplacé par le gabarit du kit ({verb} dans {where}) ; réglages du projet non repris :", *lines], True


def sync_registry(
    root: Path, destination: str | Path, rendered: str, action: str,
    refresh: bool, dry_run: bool, result: Any, *, force: bool = False,
) -> bool:
    """Branche la fusion sur ``setup_standard_profile`` ; True si l'artefact est traité.

    *action* est la décision de :func:`grimoire.core.standard_generation.decide`.
    Un fichier que le projet a touché (``keep``) est fusionné lors d'une mise à
    jour (*refresh*) ; sinon l'appelant écrit ou saute comme avant, et la base
    de fusion suit ce qui vient d'être écrit ou adopté.
    """
    if force and action == "write" and (root / destination).is_file():
        # ``--force`` : le kit reprend l'autorité, mais jamais en silence. Le
        # registre du projet est copié à côté et ce qui n'est pas repris est listé.
        notices, may_overwrite = _force_report(root, destination, rendered, dry_run)
        result.notices.extend(notices)
        if not may_overwrite:
            result.skipped.append(Path(destination))
            return True  # sans sauvegarde, le registre reste en place
        if not dry_run:
            record_base(root, destination, rendered)
        return False  # l'appelant écrit le gabarit
    if action == "keep" and refresh:
        merged = merge_project_registry(root, destination, rendered, dry_run=dry_run)
        result.notices.extend(merged.notices)
        result.conflicts.extend(merged.conflicts)
        (result.written if merged.changed else result.skipped).append(Path(destination))
        return True
    if action in {"adopt", "write"} and not dry_run:
        record_base(root, destination, rendered)
    return False
