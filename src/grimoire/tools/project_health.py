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
import subprocess
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

#: `git rev-list --count` sur un dépôt local est de l'ordre de la milliseconde ;
#: cette borne n'existe que pour qu'une route de statut ne bloque jamais sur un
#: dépôt distant mal configuré (un remote lent ne devrait pas être consulté ici
#: de toute façon — la commande ne touche que l'historique local).
_GIT_TIMEOUT_S = 5.0


def _installed_kit_version() -> str:
    from grimoire.__version__ import __version__

    return str(__version__)


#: Chemins relatifs où un environnement Python propre du projet range son
#: binaire ``grimoire`` — le seul indice qu'on prend sans jamais deviner à
#: partir de ``$PATH``, qui désignerait le process servant le cockpit plutôt
#: que « l'outil du projet » (issue #510 point 4c).
_PROJECT_TOOL_CANDIDATES = (
    Path(".venv") / "bin" / "grimoire",
    Path(".venv") / "Scripts" / "grimoire.exe",
)

#: Un `.venv` cassé ou un binaire qui bloque ne doit jamais faire attendre une
#: route de statut — même logique que ``_GIT_TIMEOUT_S`` plus bas.
_TOOL_VERSION_TIMEOUT_S = 2.0


def _declared_project_tool(project_root: Path) -> Path | None:
    """Le binaire ``grimoire`` que CE projet déclare comme le sien, s'il y en a un.

    Deux sources, dans l'ordre : un ``.venv/bin/grimoire`` à la racine (un
    environnement propre du projet), sinon un champ ``tool:`` explicite au
    premier niveau de ``project-context.yaml`` (chemin vers le binaire,
    absolu ou relatif à la racine du projet). Rien d'autre — deviner via
    ``$PATH`` ou ``sys.executable`` désignerait le process qui répond ici, pas
    l'outil qui gouverne réellement le projet au quotidien.
    """
    for rel in _PROJECT_TOOL_CANDIDATES:
        candidate = project_root / rel
        if candidate.is_file():
            return candidate

    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return None
    try:
        data = _yaml.load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, YAMLError):
        return None
    tool = data.get("tool") if isinstance(data, dict) else None
    if not isinstance(tool, str) or not tool.strip():
        return None
    candidate = Path(tool.strip())
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate if candidate.is_file() else None


def project_tool_version(project_root: Path) -> dict[str, Any]:
    """Version de l'outil que CE projet déclare gouverner ses opérations.

    Distinct de :func:`_installed_kit_version` : celle-ci répond pour le
    process qui exécute ce code — souvent le serveur cockpit, dans un venv
    différent de celui qui pilote le projet au jour le jour. Un projet géré
    par un pipx séparé peut être en retard sans qu'aucun des deux autres
    chiffres ne le montre (issue #510 point 4). Ne lève jamais : un binaire
    absent, cassé ou muet rend simplement ``version: None`` — « inconnu » côté
    interface, jamais une panne de la route de statut.
    """
    binary = _declared_project_tool(project_root)
    if binary is None:
        return {"version": None, "source": None}

    try:
        proc = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TOOL_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"version": None, "source": str(binary)}
    if proc.returncode != 0:
        return {"version": None, "source": str(binary)}

    # `grimoire --version` imprime p.ex. « grimoire-kit 3.50.2 » : on ne garde
    # que le dernier jeton qui ressemble à un numéro de version.
    tokens = proc.stdout.strip().split()
    version = next((t for t in reversed(tokens) if t and t[0].isdigit()), None)
    return {"version": version, "source": str(binary)}


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
    tool = project_tool_version(project_root)
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
        # L'outil que CE projet déclare comme le sien (`.venv/bin/grimoire`
        # ou `tool:`), distinct du process qui répond ici — `None` quand le
        # projet ne déclare rien ou que la version n'a pas pu être lue
        # (issue #510 point 4c).
        "projectTool": tool["version"],
    }


def tool_version_gap(project_root: Path) -> dict[str, Any] | None:
    """Le CLI qui exécute ce code est-il en retard sur le kit aligné du projet ?

    Distinct de ``projectTool`` (l'outil que le projet *déclare*) : cette
    comparaison porte sur l'outil qui répond maintenant, quel qu'il soit — le
    cas réel de l'issue #510 point 4 est un pipx 3.50.1 lancé à la main sur un
    projet mis à niveau entretemps par un cockpit 3.50.2, sans qu'aucun check
    existant ne le dise. ``None`` quand le projet n'a pas de version alignée
    connue (pas encore scaffolded, ou catalogue indisponible) : rien à
    comparer, pas un écart annoncé contre du vide.
    """
    alignment = kit_alignment(project_root)
    aligned = alignment["aligned"]
    if not aligned:
        return None
    installed = str(alignment["installed"])
    return {
        "installed": installed,
        "aligned": str(aligned),
        "outdated": _version_key(installed) < _version_key(aligned),
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


def commits_total(project_root: Path) -> int | None:
    """Nombre de commits sur ``HEAD``, ou ``None`` hors dépôt git ou sans historique.

    Corrige la revue §4.1 : le portefeuille lisait ``p.commits`` quand la donnée
    réelle qui existait ailleurs s'appelait ``commits_total`` — un nom qui ne
    correspondait à rien plutôt qu'une valeur fausse. Ici, la donnée est
    calculée pour de vrai (``git rev-list --count``), sous ce nom, et ``None``
    quand elle ne peut pas l'être : un dépôt sans ``.git`` ou sans commit n'a
    pas zéro commit, il n'a pas de réponse.
    """
    if not (project_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return int(text) if text.isdigit() else None


def ci_status(project_root: Path) -> str:
    """Statut CI du projet — ``"unknown"`` tant qu'aucune source locale ne le mesure.

    Corrige la revue §4.1 : le portefeuille traitait un statut inconnu comme un
    échec silencieux (``isFail(undefined)``) au lieu de le nommer. Ce module ne
    lit ni le réseau ni ``gh`` — une route de statut appelée à chaque rendu du
    portefeuille ne doit pas dépendre d'un appel API externe ni d'une
    authentification locale. Elle rend donc honnêtement ``"unknown"``, que
    l'interface affiche en gris sous le mot « inconnue » plutôt qu'une couleur
    inventée. Le jour où une sonde CI locale existe (webhook mis en cache,
    fichier de statut écrit par la CI elle-même), c'est ici qu'elle se branche
    — la forme de la réponse ne change pas pour les appelants.
    """
    return "unknown"


def project_health(project_root: Path) -> dict[str, Any]:
    """Vue unique consommée par l'atelier et par le portefeuille."""
    root = project_root.resolve()
    return {
        "projectRoot": str(root),
        "kit": kit_alignment(root),
        "flows": flows(root),
        "activity": activity(root),
        # Corrections dues par la revue §4.1 : les trois champs qu'un portefeuille
        # honnête doit pouvoir rendre sans les inventer. `antifragile` reste
        # `None` — aucun module de ce dépôt ne calcule ce score aujourd'hui —
        # et `antifragile_note` porte le texte que l'interface affiche à sa
        # place, pour qu'un score jamais mesuré ne se lise jamais comme zéro.
        "commits_total": commits_total(root),
        "ci_status": ci_status(root),
        "antifragile": None,
        "antifragile_note": "pas encore mesurée",
        # Ce module ne lit jamais de jeu de données de démonstration : la
        # donnée qu'il rend est toujours celle du projet servi.
        "demo": False,
    }
