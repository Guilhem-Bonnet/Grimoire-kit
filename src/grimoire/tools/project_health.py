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

#: Où vivent les blueprints d'un projet. Défini ici, module feuille, pour que
#: le serveur et la santé ne puissent pas diverger sur l'emplacement.
BLUEPRINTS_RELPATH = Path("_grimoire") / "blueprints"

#: Emplacements possibles du board gouverné, dans l'ordre de priorité.
TASK_BOARD_CANDIDATES = (
    Path("_grimoire") / "standard" / "task-board.yaml",
    Path("_grimoire") / "_config" / "standard" / "task-board.yaml",
)

#: Statuts qui décrivent un travail engagé — ce que « où il en est » veut dire
#: au niveau du board.
IN_FLIGHT_STATUSES = ("in_progress", "review", "blocked")

#: Journaux qui témoignent d'une activité, en plus de la télémétrie du runtime.
#: Travailler dans l'atelier — installer une extension, compiler un blueprint —
#: est une activité sur le projet ; ne pas la compter afficherait « aucune
#: trace » sur un projet qu'on est justement en train de manipuler.
EXTRA_EVENT_SOURCES = (
    ("atelier", Path("_grimoire-runtime-output") / "hook-runtime" / "serve-mutations.jsonl"),
)

#: Où le noyau d'exécution persiste ses instances, événements et checkpoints.
#: Voir ``grimoire.runtime.kernel.RuntimeKernel``.
RUNTIME_RELPATH = Path("_grimoire-runtime-output") / "runtime"

#: Statuts d'instance qui décrivent un travail encore en vol. Les autres sont
#: terminaux : les compter comme « en cours » afficherait comme actives des
#: exécutions finies il y a des semaines.
LIVE_RUN_STATUSES = frozenset({"created", "running", "checkpointed", "paused", "blocked"})

#: Un run tué n'écrit jamais son statut terminal : il reste « running » pour
#: toujours. Constaté en produisant un vrai run puis en interrompant le
#: processus. Au-delà de cette fenêtre sans le moindre signal, l'exécution est
#: rapportée comme sans nouvelles plutôt que comptée comme active.
RUN_SILENT_AFTER_MINUTES = 60

#: On ne veut que la fin de chaque journal. Les lire en entier coûterait, sur
#: cette machine, 14 Mo pour une seule ligne utile — et ces fichiers ne font
#: que grossir. 64 Kio couvrent largement les dernières entrées.
_TAIL_BYTES = 64 * 1024


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
    base = project_root / BLUEPRINTS_RELPATH
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
    sources = list(event_files(project_root))
    sources += [
        (name, project_root / rel)
        for name, rel in EXTRA_EVENT_SOURCES
        if (project_root / rel).is_file()
    ]
    for name, path in sources:
        lines = _tail_lines(path)
        for line in reversed(lines):
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


def _tail_lines(path: Path) -> list[str]:
    """Dernières lignes d'un journal, sans le charger en entier.

    La première ligne lue peut être tronquée par la découpe en octets : on
    l'écarte, sauf quand tout le fichier tient dans la fenêtre.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > _TAIL_BYTES:
                f.seek(size - _TAIL_BYTES)
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]
    return lines


def _read_jsonl(path: Path, *, tail: bool = True) -> list[dict[str, Any]]:
    """Objets d'un JSONL, lignes illisibles écartées.

    ``tail`` borne la lecture à la fin du fichier — bon pour un journal
    append-only, faux pour un fichier réécrit en entier à chaque sauvegarde,
    où tronquer ferait disparaître des enregistrements.
    """
    if tail:
        lines = _tail_lines(path)
    else:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def runs(project_root: Path, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Exécutions encore en vol, et où elles en sont.

    Le noyau d'exécution tient déjà ce registre — instances, événements,
    checkpoints. Rien ne le remontait, d'où l'impression qu'« est-ce que ça
    tourne » n'avait pas de réponse dans le kit : elle existait, elle n'était
    simplement lue par personne.

    « Où il se trouve » est littéral ici : le dernier checkpoint d'une instance
    nomme l'étape courante et ce qui reste à faire.
    """
    root = project_root / RUNTIME_RELPATH
    # `instances.jsonl` est réécrit intégralement à chaque sauvegarde : le lire
    # par la fin en perdrait. Les journaux append-only, eux, restent bornés.
    instances = _read_jsonl(root / "instances.jsonl", tail=False)
    if not instances:
        return []

    # Le fichier d'instances est réécrit en entier à chaque sauvegarde : la
    # dernière occurrence d'un id est l'état courant.
    latest: dict[str, dict[str, Any]] = {}
    for entry in instances:
        instance_id = str(entry.get("id", ""))
        if instance_id:
            latest[instance_id] = entry

    checkpoints: dict[str, dict[str, Any]] = {}
    for chk in _read_jsonl(root / "checkpoints.jsonl"):
        instance_id = str(chk.get("workflow_instance_id", ""))
        if not instance_id:
            continue
        seen = checkpoints.get(instance_id)
        if seen is None or str(chk.get("created_at", "")) >= str(seen.get("created_at", "")):
            checkpoints[instance_id] = chk

    now = now or datetime.now(UTC)
    live = []
    for instance_id, entry in latest.items():
        if str(entry.get("status", "")) not in LIVE_RUN_STATUSES:
            continue
        chk = checkpoints.get(instance_id, {})
        raw_state = chk.get("state")
        state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
        signal = _parse_stamp(chk.get("created_at")) or _parse_stamp(entry.get("created_at"))
        silent_for = None if signal is None else (now - signal).total_seconds() / 60.0
        live.append({
            "silentForMinutes": None if silent_for is None else round(silent_for, 1),
            "silent": bool(silent_for is not None and silent_for > RUN_SILENT_AFTER_MINUTES),
            "id": instance_id,
            "runId": str(entry.get("run_id", "")),
            "recipe": str(entry.get("recipe_id", "")),
            "mission": str(entry.get("mission_id", "")),
            "task": str(entry.get("task_id", "")),
            "status": str(entry.get("status", "")),
            "startedAt": str(entry.get("created_at", "")) or None,
            "step": str(chk.get("step_id", "")) or None,
            "completedSteps": len(state.get("completed_steps") or []),
            "pendingSteps": len(state.get("pending_steps") or []),
            "checkpointAt": str(chk.get("created_at", "")) or None,
        })
    live.sort(key=lambda r: str(r.get("checkpointAt") or r.get("startedAt") or ""), reverse=True)
    return live


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
    live_runs = runs(project_root, now=now)
    return {
        "runs": live_runs,
        # Seul un run qui donne encore signe de vie compte comme en cours : un
        # processus tué laisse son instance en « running » indéfiniment.
        "running": any(not r["silent"] for r in live_runs),
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
