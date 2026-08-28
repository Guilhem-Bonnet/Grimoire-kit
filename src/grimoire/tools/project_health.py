"""Santé d'un projet gouverné — alignement kit, flows, activité réelle.

Ce que le portefeuille doit pouvoir dire d'un projet sans mentir : sur quelle
version du kit il est aligné, quels flows il porte, et s'il se passe quelque
chose dedans en ce moment.

Les trois réponses viennent de faits sur disque, jamais d'une estimation :

* **alignement** — par digest de contenu (``kit_hashes``). Un projet ne note
  nulle part la version qui l'a généré ; en revanche chaque fichier que le kit
  possède est reconnaissable, et le catalogue dit de quelle version il date.
* **flows** — les blueprints réellement présents sous ``_grimoire/blueprints``.
* **activité** — l'horodatage le plus frais des journaux d'événements du
  projet, plus les tâches que son board déclare en cours. Rien ici ne prétend
  qu'un processus tourne : on rapporte la dernière trace écrite et ce que le
  projet dit de lui-même. Un indicateur « en cours » déduit d'autre chose
  serait une invention de plus.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core import layout
from grimoire.core.kit_hashes import load_catalog, shipped_by_kit
from grimoire.tools.blueprint_telemetry import event_files

_yaml = YAML(typ="safe")

#: Fenêtre au-delà de laquelle un projet n'est plus considéré comme actif.
#: Quinze minutes : assez pour couvrir une pause de frappe, trop court pour
#: laisser croire qu'une session d'hier est en cours.
ACTIVE_WINDOW_MINUTES = 15

#: Emplacements possibles du board gouverné, dans l'ordre de priorité.
TASK_BOARD_CANDIDATES = (
    Path("_grimoire") / "standard" / "task-board.yaml",
    Path("_grimoire") / "_config" / "standard" / "task-board.yaml",
)

#: Statuts qui décrivent un travail engagé — ce que « où il en est » veut dire
#: au niveau du board.
IN_FLIGHT_STATUSES = ("in_progress", "review", "blocked")


def _installed_kit_version() -> str:
    from grimoire.__version__ import __version__

    return str(__version__)


def _version_key(version: str) -> tuple[int, ...]:
    """Ordre de version tolérant : ce qui n'est pas numérique passe en dernier."""
    parts: list[int] = []
    for chunk in str(version).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


@lru_cache(maxsize=1)
def _newest_version_by_path() -> dict[str, str]:
    """Dernière version connue du kit pour chaque chemin logique.

    Le catalogue est indexé par digest : un même fichier y figure une fois par
    révision de son contenu. La version d'une entrée dit donc quand *ce
    contenu-là* a été publié, pas si c'est le contenu courant — un fichier
    inchangé depuis 3.32.0 est parfaitement à jour dans un kit 3.34.2.

    Comparer à la version installée, comme le faisait la première version de ce
    module, déclarait « en retard » les 37 fichiers d'un projet qu'on venait
    tout juste de mettre à jour. La bonne question n'est pas « de quand date ce
    fichier » mais « le kit en connaît-il une révision plus récente ».
    """
    newest: dict[str, str] = {}
    for entry in load_catalog().values():
        path = str(entry.get("path", ""))
        version = str(entry.get("version", ""))
        if not path:
            continue
        if path not in newest or _version_key(version) > _version_key(newest[path]):
            newest[path] = version
    return newest


def kit_alignment(project_root: Path) -> dict[str, Any]:
    """Le contenu de ce projet est-il la dernière révision que le kit connaît ?

    Un projet n'enregistre pas la version qui l'a généré. Le catalogue de
    digests, lui, reconnaît chaque contenu que le kit a publié : un fichier est
    en retard quand le catalogue porte une révision plus récente du *même
    chemin*. Les fichiers inconnus du catalogue sont des écritures du projet,
    pas un retard — les compter comme tels transformerait chaque
    personnalisation en alerte.
    """
    installed = _installed_kit_version()
    kit_dir = layout.kit_dir(project_root)
    newest = _newest_version_by_path()
    catalog_available = bool(newest)

    versions: dict[str, int] = {}
    behind: list[str] = []
    own = 0
    total = 0
    if kit_dir.is_dir():
        for path in sorted(kit_dir.rglob("*")):
            if not path.is_file():
                continue
            total += 1
            entry = shipped_by_kit(path)
            if entry is None:
                own += 1
                continue
            version = str(entry.get("version", ""))
            versions[version] = versions.get(version, 0) + 1
            latest = newest.get(str(entry.get("path", "")), version)
            if _version_key(version) < _version_key(latest):
                behind.append(str(path.relative_to(project_root)))

    aligned = max(versions, key=_version_key) if versions else None
    return {
        "installed": installed,
        "aligned": aligned,
        "upToDate": bool(versions) and not behind,
        "behind": len(behind),
        "behindFiles": behind[:20],
        "projectOwned": own,
        "tracked": total,
        # Sans catalogue, on ne reconnaît rien : le dire évite d'afficher
        # « 0 fichier en retard » comme si c'était un diagnostic.
        "catalogAvailable": catalog_available,
        "scaffolded": kit_dir.is_dir(),
    }


def flows(project_root: Path) -> list[dict[str, Any]]:
    """Blueprints réellement présents dans le projet, avec leur taille."""
    base = project_root / "_grimoire" / "blueprints"
    found: list[dict[str, Any]] = []
    if not base.is_dir():
        return found
    for path in sorted(base.glob("*.blueprint.json")):
        try:
            bp = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta = bp.get("meta") if isinstance(bp.get("meta"), dict) else {}
        found.append(
            {
                "id": str(bp.get("id", path.stem.replace(".blueprint", ""))),
                "name": str(bp.get("name") or meta.get("name") or ""),
                "nodes": len(bp.get("nodes", []) or []),
                "edges": len(bp.get("edges", []) or []),
                "validated": bool(meta.get("validated")),
                "compiledAt": meta.get("compiledAt"),
            }
        )
    return found


def _latest_event(project_root: Path) -> dict[str, Any] | None:
    """Événement le plus récent, tous journaux du projet confondus."""
    best: dict[str, Any] | None = None
    for name, path in event_files(project_root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-200:]):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            stamp = entry.get("ts") or entry.get("timestamp")
            parsed = _parse_stamp(stamp)
            if parsed is None:
                continue
            if best is None or parsed > best["at"]:
                best = {
                    "at": parsed,
                    "source": name,
                    "label": str(
                        entry.get("action")
                        or entry.get("event")
                        or entry.get("reason")
                        or entry.get("kind")
                        or name
                    )[:80],
                }
            break  # la dernière ligne horodatée du flux suffit
    return best


def _parse_stamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _in_flight_tasks(project_root: Path) -> list[dict[str, str]]:
    """Ce que le board du projet déclare engagé — sa position, dite par lui."""
    source = next(
        (project_root / rel for rel in TASK_BOARD_CANDIDATES if (project_root / rel).is_file()),
        None,
    )
    if source is None:
        return []
    try:
        data = _yaml.load(source.read_text(encoding="utf-8")) or {}
    except (OSError, YAMLError):
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    return [
        {
            "id": str(t.get("task_id", "")),
            "title": str(t.get("title", "")),
            "status": str(t.get("status", "")),
        }
        for t in tasks
        if isinstance(t, dict) and str(t.get("status", "")) in IN_FLIGHT_STATUSES
    ]


def activity(project_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Dernière trace écrite par le projet, et ce que son board dit en cours.

    ``active`` ne prétend pas qu'un processus tourne : il dit qu'une trace a été
    écrite dans la fenêtre récente. La nuance est le sujet même de ce module.
    """
    now = now or datetime.now(UTC)
    latest = _latest_event(project_root)
    in_flight = _in_flight_tasks(project_root)
    age_minutes = None
    if latest is not None:
        age_minutes = max(0.0, (now - latest["at"]).total_seconds() / 60.0)
    return {
        "lastEventAt": latest["at"].isoformat() if latest else None,
        "lastEventSource": latest["source"] if latest else None,
        "lastEventLabel": latest["label"] if latest else None,
        "ageMinutes": round(age_minutes, 1) if age_minutes is not None else None,
        "active": bool(age_minutes is not None and age_minutes <= ACTIVE_WINDOW_MINUTES),
        "activeWindowMinutes": ACTIVE_WINDOW_MINUTES,
        "inFlight": in_flight,
    }


def project_health(project_root: Path) -> dict[str, Any]:
    """Vue unique consommée par l'atelier et par le portefeuille."""
    root = project_root.resolve()
    return {
        "projectRoot": str(root),
        "kit": kit_alignment(root),
        "flows": flows(root),
        "activity": activity(root),
    }
