"""Actions typées A et B du cockpit (ADR-008 D1–D4) — plan, apply, rollback, journal.

Une action est une entrée de :data:`CATALOGUE`, pas une commande : l'interface
envoie ``{action_id, paramètres}``, jamais un argv ni un chemin. Chaque entrée
déclare les cinq éléments de D1 (préconditions, plan, effets, vérification,
inverse) ; ``plan`` les calcule sans rien écrire hors journal, ``apply`` ne
rejoue que le plan dont l'identifiant ET l'empreinte sont ceux du journal et
dont l'état n'a pas bougé, ``rollback`` applique l'inverse consigné.

Catalogue :

* ``memory.compose`` (classe A) — appliquer un ``MemoryPlan`` à
  ``project-context.yaml`` ; le fichier d'avant est copié au journal et restauré
  par le rollback tant qu'il n'a pas été modifié depuis ;
* ``memory.slot`` (classe A) — attribuer ou retirer un emplacement mémoire du projet
  (configuration seulement, P41, cf. :mod:`grimoire.tools.workspace_slots`) ;
* ``memory.split`` (classe D) — séparer le projet d'un emplacement partagé par
  ``memory split`` (P28) : export d'abord, copie, bascule ; nettoyage différé ;
* ``stack.start`` / ``stack.stop`` (classe B) — ``docker compose -f <compose du
  kit> up -d|stop`` ; le compose est celui du gabarit du kit, copié dans le
  projet, jamais fourni par l'appelant ; jamais ``-v``/``--volumes``/``down`` ;
* ``stack.restart_policy`` (classe B) — ``docker update --restart``, relu par
  ``docker inspect`` ;
* ``pip.extras`` (classe C) — la commande à copier ; aucune route ``apply``.

Un conteneur dont le Compose appartient à un autre projet n'est jamais touché
depuis celui-ci ; arrêter une pile partagée exige une confirmation qui liste les
projets et les sessions vivantes touchés. Journal ajout seul, illisible ou non
inscriptible : pas d'effet (``_grimoire-runtime-output/actions/journal.jsonl``).
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.tools.workspace_write_guard import journal_event, state_fingerprint

if TYPE_CHECKING:
    from collections.abc import Callable

    from grimoire.tools.workspace_machine import MachineProbes

__all__ = [
    "CATALOGUE",
    "PLAN_TTL",
    "ActionEnv",
    "ActionSpec",
    "actions_catalogue",
    "actions_journal",
    "apply_action",
    "journal_path",
    "plan_action",
    "rollback_action",
]

SCHEMA_VERSION = "grimoire-actions/v1"
PLAN_TTL = timedelta(minutes=15)
_JOURNAL = ("_grimoire-runtime-output", "actions", "journal.jsonl")
_DOCKER_TIMEOUT = 120.0
#: Jamais dans un argv de classe B : une action n'efface aucune donnée.
_FORBIDDEN_ARGV = frozenset({"-v", "--volumes", "--volume", "down", "rm", "prune", "volume", "--force", "-f=-v"})
_POLICIES = ("unless-stopped", "no")
_LIVE_SESSION_STATES = ("active", "waiting")

#: Les piles du kit : id de catalogue → gabarit, fichier rendu dans le projet, services couverts.
STACKS: dict[str, dict[str, Any]] = {
    "qdrant": {
        "label": "Qdrant", "template": "docker-compose.memory.tpl.yml",
        "file": "docker-compose.memory.yml", "kinds": ("qdrant",),
    },
    "weaviate-neo4j": {
        "label": "Weaviate + Neo4j", "template": "docker-compose.memory-target.tpl.yml",
        "file": "docker-compose.memory-target.yml", "kinds": ("weaviate", "neo4j"),
    },
}


# ── Catalogue (D1) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionSpec:
    """Les cinq éléments de D1, lisibles sans rien exécuter."""

    id: str
    label: str
    klass: str
    preconditions: tuple[str, ...]
    plan: str
    effects: tuple[str, ...]
    verification: str
    inverse: str
    applicable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "class": self.klass,
            "preconditions": list(self.preconditions), "plan": self.plan, "effects": list(self.effects),
            "verification": self.verification, "inverse": self.inverse, "applicable": self.applicable,
        }


CATALOGUE: dict[str, ActionSpec] = {
    s.id: s for s in (
        ActionSpec(
            "memory.compose", "Composer la mémoire", "A",
            ("project-context.yaml présent et lisible", "au moins une clé du bloc memory: à changer"),
            "différence clé par clé du bloc memory: (MemoryPlan), sans écriture",
            ("clés du bloc memory: de project-context.yaml", "trace du backend quitté (_grimoire/_memory/migration)"),
            "project-context.yaml relu : chaque clé du plan a la valeur prévue",
            "restauration de project-context.yaml copié au journal (refusée s'il a été modifié depuis)",
        ),
        ActionSpec(
            "memory.slot", "Attribuer ou retirer un emplacement mémoire", "A",
            ("project-context.yaml présent et lisible", "type qdrant | weaviate | redis | neo4j",
             "attribution : adresse connue et nom valide ; retrait : un emplacement est attribué"),
            "différence clé par clé de memory:, avertissement de partage (« deviendra partagé avec N »), "
            "emplacement neuf proposé par défaut ; Redis : namespace dédié avant conteneur dédié",
            ("clés d'adresse et de nom de memory: dans project-context.yaml — aucun souvenir copié ni effacé",),
            "project-context.yaml relu : chaque clé du plan a la valeur prévue",
            "restauration de project-context.yaml copié au journal (refusée s'il a été modifié depuis)",
        ),
        ActionSpec(
            "memory.split", "Séparer le projet d'un emplacement partagé", "D",
            ("emplacement qdrant ou weaviate attribué", "Neo4j non séparable tant que P15 n'est pas livré",
             "au moins un souvenir du projet dans la source", "cible distincte de la source"),
            "aperçu `memory split` : souvenirs du projet, collection cible, souvenirs sans projet laissés dans la source",
            ("export JSON de toute la source (obligatoire, avant la copie)", "copie par upsert à identifiants conservés",
             "bascule de memory.collection_prefix — la source n'est ni modifiée ni supprimée"),
            "comptes et identifiants relus dans la cible ; configuration relue",
            "restauration de project-context.yaml ; la copie reste en place. "
            "Le nettoyage de la source est un geste séparé et différé, hors de cette action",
        ),
        ActionSpec(
            "stack.start", "Démarrer une pile mémoire", "B",
            ("Docker joignable", "pile du catalogue (qdrant | weaviate-neo4j)",
             "aucun conteneur de la pile ne dépend du Compose d'un autre projet"),
            "copie du compose du kit si absent, puis `docker compose -f <fichier> up -d`; pile existante réutilisée",
            ("compose du kit copié dans le projet (si absent)", "conteneurs de la pile démarrés"),
            "les conteneurs de la pile sont relus par docker inspect (running)",
            "`docker compose -f <fichier> stop` ; le compose copié par l'action est retiré",
        ),
        ActionSpec(
            "stack.stop", "Arrêter une pile mémoire", "B",
            ("Docker joignable", "pile du catalogue", "compose de la pile dans ce projet",
             "pile non empruntée à un autre projet",
             "pile partagée : confirmation listant projets et sessions vivantes touchés"),
            "`docker compose -f <fichier> stop` (jamais down, jamais -v) ; projets et sessions touchés listés",
            ("conteneurs de la pile arrêtés — volumes intacts",),
            "les conteneurs de la pile sont relus par docker inspect (arrêtés)",
            "`docker compose -f <fichier> up -d`",
        ),
        ActionSpec(
            "stack.restart_policy", "Redémarrer avec la machine", "B",
            ("Docker joignable", "service mémoire local nommé par son type", "conteneur non emprunté à un autre projet",
             "politique unless-stopped ou no"),
            "`docker update --restart=<politique> <conteneur>`",
            ("politique de redémarrage du conteneur",),
            "docker inspect relit HostConfig.RestartPolicy.Name",
            "`docker update --restart=<ancienne politique>`",
        ),
        ActionSpec(
            "pip.extras", "Installer les extras Python", "C",
            ("extras connus du kit",),
            "la commande à copier, avec l'installeur de l'environnement du projet",
            ("aucun : le cockpit n'exécute jamais pip/uv",),
            "sans objet — l'utilisateur lance la commande",
            "sans objet — pas d'effet à défaire",
            applicable=False,
        ),
    )
}


# ── Environnement injectable ───────────────────────────────────────────────────

def _default_run(argv: list[str], cwd: Path | None) -> tuple[int, str, str]:
    """``docker …`` sans shell, borné ; une panne devient un code et un motif."""
    try:
        done = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=_DOCKER_TIMEOUT, check=False,
        )
    except FileNotFoundError:
        return 127, "", "commande docker introuvable sur ce poste"
    except subprocess.TimeoutExpired:
        return 124, "", f"Docker n'a pas répondu en {_DOCKER_TIMEOUT:g} s"
    except OSError as exc:
        return 126, "", str(exc)
    return done.returncode, done.stdout or "", done.stderr or ""


def _default_sessions(project_root: Path) -> list[dict[str, Any]]:
    from grimoire.tools.workspace_sessions import sessions_view

    try:
        return list(sessions_view(project_root, portfolio=True)["sessions"])
    except Exception:
        return []


def _kit_memory_dir() -> Path:
    from grimoire.data import framework_path

    return framework_path() / "memory"


@dataclass
class ActionEnv:
    """Tout ce que les actions demandent au système, remplaçable en test."""

    run: Callable[[list[str], Path | None], tuple[int, str, str]] = _default_run
    probes: MachineProbes | None = None
    sessions: Callable[[Path], list[dict[str, Any]]] = _default_sessions
    memory_dir: Callable[[], Path] = _kit_memory_dir
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    build_memory_plan: Callable[..., Any] | None = None
    registry: Callable[[], list[dict[str, str]]] | None = None
    #: ``open_store(config, project_root, collection)`` → gestionnaire ; ``collection=None`` = la source.
    open_store: Callable[[Any, Path, str | None], Any] | None = None


def journal_path(project_root: Path) -> Path:
    return project_root.resolve().joinpath(*_JOURNAL)


def _check_argv(argv: list[str]) -> list[str]:
    """Dernier verrou avant tout processus : docker, et rien qui efface des données."""
    if not argv or argv[0] != "docker" or _FORBIDDEN_ARGV & set(argv[1:]):
        raise ValueError(f"commande refusée par le catalogue : {' '.join(argv)}")
    return argv


def _docker(env: ActionEnv, argv: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    return env.run(_check_argv(argv), cwd)


# ── Journal (D4) ────────────────────────────────────────────────────────────────


def _read_journal(project_root: Path) -> tuple[list[dict[str, Any]], str | None]:
    path = journal_path(project_root)
    if not path.exists():
        return [], None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"journal illisible ({path}) : {exc}"
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            return [], f"journal corrompu ({path}) : ligne illisible"
        if isinstance(row, dict):
            rows.append(row)
    return rows, None


def _log(project_root: Path, event: dict[str, Any]) -> str | None:
    """Ajoute un événement ; ``None`` si écrit, sinon la raison (fail-closed côté appelant)."""
    try:
        journal_event(journal_path(project_root), event)
    except OSError as exc:
        return f"journal non inscriptible ({journal_path(project_root)}) : {exc}"
    return None


def _refus(action: str, raison: str, **extra: Any) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "action": action, "ok": False, "raison": raison, **extra}


def actions_journal(project_root: Path, *, limit: int = 100) -> dict[str, Any]:
    """``GET data/actions/journal`` — les derniers événements, du plus récent au plus ancien."""
    rows, error = _read_journal(project_root)
    return {
        "schemaVersion": SCHEMA_VERSION, "journal": str(journal_path(project_root)), "error": error,
        "events": rows[-max(1, limit):][::-1], "count": len(rows),
    }


# ── Lecture de la machine ──────────────────────────────────────────────────────


def _within(path: str | None, root: Path) -> bool:
    if not path:
        return False
    target = Path(path).expanduser().resolve()
    base = root.resolve()
    return target == base or target.is_relative_to(base)


def _machine(project_root: Path, env: ActionEnv) -> dict[str, Any]:
    from grimoire.tools.workspace_machine import machine_view

    return machine_view(project_root, probes=env.probes)


def _stack_view(project_root: Path, env: ActionEnv, stack: str) -> dict[str, Any]:
    """Conteneurs, propriétaires, consommateurs et sessions vivantes d'une pile — mesure fraîche."""
    view = _machine(project_root, env)
    kinds = STACKS[stack]["kinds"]
    containers: list[dict[str, Any]] = []
    consumers: dict[str, str] = {}
    for service in view["services"]:
        if service["kind"] not in kinds:
            continue
        for consumer in service["consumers"]:
            consumers[consumer["slug"] or consumer["name"]] = consumer["name"]
        container = service["container"]
        if not container or service["host"] != "localhost":
            continue
        compose = container.get("compose") or {}
        mine = _within(compose.get("workingDir"), project_root)
        if compose and not mine:
            owner = (service.get("owner") or {}).get("name") or compose.get("project") or compose.get("workingDir")
            relation = "foreign"
        else:
            owner, relation = None, "mine" if compose else "unmanaged"
        containers.append({
            "kind": service["kind"], "name": container["name"], "status": container["status"],
            "restartPolicy": container["restartPolicy"], "relation": relation, "owner": owner,
            "state": service["state"],
        })
    slugs = set(consumers)
    live = [
        {"id": s["id"], "project": s["project"]["name"], "state": s["state"]}
        for s in env.sessions(project_root)
        if s["state"] in _LIVE_SESSION_STATES and (s["project"]["slug"] in slugs or s["project"]["name"] in slugs)
    ]
    return {
        "dockerReachable": view["docker"]["reachable"], "dockerReason": view["docker"]["reason"],
        "containers": sorted(containers, key=lambda c: c["name"]),
        "projects": sorted(consumers.values()), "sessions": sorted(live, key=lambda s: s["id"]),
    }


def _foreign_reason(containers: list[dict[str, Any]], project_root: Path) -> str | None:
    foreign = [c for c in containers if c["relation"] == "foreign"]
    if not foreign:
        return None
    names = ", ".join(f"« {c['name']} » ({c['owner']})" for c in foreign)
    return (
        f"pile empruntée : {names} appartient au Compose d'un autre projet que {project_root.name} — "
        "agissez depuis le projet propriétaire, jamais depuis l'emprunteur"
    )


# ── Plan : forme commune ───────────────────────────────────────────────────────


@dataclass
class _Plan:
    action: str
    params: dict[str, Any]
    blockers: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    needs_confirmation: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            {"action": self.action, "params": self.params, "blockers": self.blockers,
             "effects": self.effects, "commands": self.commands, "facts": self.facts},
            sort_keys=True, default=str,
        )
        return state_fingerprint(body.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "params": self.params, "blockers": self.blockers, "effects": self.effects,
            "commands": [" ".join(c) for c in self.commands], "facts": self.facts,
            "needsConfirmation": self.needs_confirmation, "notes": self.notes,
        }


def _stack_param(params: dict[str, Any], plan: _Plan) -> str | None:
    stack = str(params.get("stack") or "")
    if stack not in STACKS:
        plan.blockers.append(f"pile inconnue « {stack} » — attendu : {', '.join(STACKS)}")
        return None
    return stack


def _compose_argv(stack: str, verb: list[str]) -> list[str]:
    return ["docker", "compose", "-f", STACKS[stack]["file"], *verb]


# ── memory.compose (A) ─────────────────────────────────────────────────────────


def _memory_plan(project_root: Path, env: ActionEnv, params: dict[str, Any]) -> _Plan:
    from grimoire.tools import memory_setup

    plan = _Plan("memory.compose", {"profile": str(params.get("profile") or "complet")})
    config = project_root / "project-context.yaml"
    if not config.is_file():
        plan.blockers.append("project-context.yaml absent — lancez d'abord `grimoire init`")
        return plan
    try:
        built = (env.build_memory_plan or memory_setup.build_memory_plan)(project_root, profile=plan.params["profile"])
    except ValueError as exc:
        plan.blockers.append(str(exc))
        return plan
    plan.blockers.extend(w for w in built.warnings if w.startswith(("Aucun project-context", "Config illisible")))
    if not built.changes and not plan.blockers:
        plan.blockers.append("rien à changer : le bloc memory: est déjà celui du profil")
    plan.effects = [f"memory.{c['key']}: {c['old']!r} → {c['new']!r}" for c in built.changes]
    plan.facts = {
        "configSha": state_fingerprint(config.read_bytes()),
        "changes": built.changes, "profile": built.profile,
    }
    plan.notes = [*built.warnings, *built.notes, *built.next_steps]
    return plan


def _memory_apply(project_root: Path, env: ActionEnv, plan: _Plan, run_id: str) -> dict[str, Any]:
    from grimoire.memory.migration import PREVIOUS_BACKEND_BREADCRUMB
    from grimoire.tools import memory_setup

    config = project_root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")
    breadcrumb = project_root / PREVIOUS_BACKEND_BREADCRUMB
    crumb_before = breadcrumb.read_text(encoding="utf-8") if breadcrumb.is_file() else None
    built = (env.build_memory_plan or memory_setup.build_memory_plan)(project_root, profile=plan.params["profile"])
    written = memory_setup.apply_memory_plan(built)
    after = config.read_bytes()
    return {
        "effects": [f"memory.{k} écrit" for k in written],
        "inverse": {
            "kind": "restore_file", "path": "project-context.yaml", "content": before,
            "afterSha": state_fingerprint(after),
            "breadcrumb": {"path": str(PREVIOUS_BACKEND_BREADCRUMB), "content": crumb_before},
        },
        "verification": _memory_verify(config, plan.facts["changes"]),
    }


def _memory_verify(config: Path, changes: list[dict[str, Any]]) -> dict[str, Any]:
    from grimoire.tools.memory_setup import _raw_memory_block

    block = _raw_memory_block(config)
    wrong = [c["key"] for c in changes if block.get(c["key"]) != c["new"]]
    return {"ok": not wrong, "detail": "bloc memory: relu conforme au plan" if not wrong else f"clés non conformes : {wrong}"}


def _memory_rollback(project_root: Path, inverse: dict[str, Any]) -> dict[str, Any]:
    config = project_root / inverse["path"]
    if state_fingerprint(config.read_bytes()) != inverse["afterSha"]:
        return {"ok": False, "detail": "project-context.yaml a été modifié depuis l'apply : restauration refusée"}
    config.write_text(inverse["content"], encoding="utf-8")
    crumb = inverse.get("breadcrumb") or {}
    if crumb.get("path") and not crumb.get("keep"):
        target = project_root / crumb["path"]
        if crumb.get("content") is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(crumb["content"], encoding="utf-8")
    ok = config.read_text(encoding="utf-8") == inverse["content"]
    return {"ok": ok, "detail": "project-context.yaml restauré" if ok else "restauration non conforme"}


# ── memory.slot (A) et memory.split (D) — P41 ──────────────────────────────────


def _registry(env: ActionEnv) -> list[dict[str, str]]:
    if env.registry is not None:
        return env.registry()
    from grimoire.tools.project_registry import load_registry

    return load_registry()


def _default_open_store(cfg: Any, root: Path, collection: str | None) -> Any:
    from dataclasses import replace

    from grimoire.memory.manager import MemoryManager

    if collection is not None:
        mem = replace(cfg.memory, collection_prefix=collection)
        if mem.backend == "weaviate-server":
            mem = replace(mem, weaviate_collection=collection)
        cfg = replace(cfg, memory=mem)
    return MemoryManager.from_config(cfg, project_root=root)


def _slot_plan(project_root: Path, env: ActionEnv, params: dict[str, Any]) -> _Plan:
    from grimoire.tools import workspace_slots as slots

    plan = _Plan("memory.slot", {k: v for k, v in params.items() if k in ("op", "kind", "slot", "name", "location", "redis_mode")})
    config = project_root / "project-context.yaml"
    if not config.is_file():
        plan.blockers.append("project-context.yaml absent — lancez d'abord `grimoire init`")
        return plan
    changes, blockers, warnings, facts = slots.assign_changes(config, params, _registry(env))
    plan.blockers.extend(blockers)
    plan.notes = warnings
    plan.effects = [f"memory.{c['key']}: {c['old']!r} → {c['new']!r}" for c in changes]
    plan.facts = {"configSha": state_fingerprint(config.read_bytes()), "changes": changes, **facts}
    return plan


def _slot_apply(project_root: Path, plan: _Plan) -> dict[str, Any]:
    from grimoire.memory.migration import PREVIOUS_BACKEND_BREADCRUMB
    from grimoire.tools import workspace_slots as slots

    config = project_root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")
    written = slots.write_changes(config, plan.facts["changes"])
    return {
        "effects": [f"memory.{k} écrit" for k in written],
        "inverse": {
            "kind": "restore_file", "path": "project-context.yaml", "content": before,
            "afterSha": state_fingerprint(config.read_bytes()),
            "breadcrumb": {"path": str(PREVIOUS_BACKEND_BREADCRUMB), "content": None, "keep": True},
        },
        "verification": slots.verify_changes(config, plan.facts["changes"]),
    }


def _split_context(project_root: Path, env: ActionEnv, kind: str) -> tuple[Any, Any, str, list[Any]]:
    """``(config, plan P28, collection source, entrées de la source)`` — lecture seule."""
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.split import plan_split
    from grimoire.memory.supersession import INCLUDE_ALL

    config_path = project_root / "project-context.yaml"
    cfg = GrimoireConfig.from_yaml(config_path)
    source = cfg.memory.weaviate_collection or cfg.memory.collection_prefix if kind == "weaviate" else cfg.memory.collection_prefix
    store = (env.open_store or _default_open_store)(cfg, project_root, None)
    entries = store.get_all(**INCLUDE_ALL)
    plan = plan_split(entries, source_collection=source, configs={cfg.project.name: config_path}, only=[cfg.project.name])
    return cfg, plan, source, entries


def _split_plan(project_root: Path, env: ActionEnv, params: dict[str, Any]) -> _Plan:
    from grimoire.memory.split import SplitError
    from grimoire.tools import workspace_slots as slots

    kind = str(params.get("kind") or "qdrant")
    plan = _Plan("memory.split", {"kind": kind})
    config = project_root / "project-context.yaml"
    if not config.is_file():
        plan.blockers.append("project-context.yaml absent — lancez d'abord `grimoire init`")
        return plan
    plan.blockers.extend(slots.split_blockers(config, kind))
    if plan.blockers:
        plan.facts = {"separable": False, "kind": kind}
        return plan
    try:
        cfg, split, source, entries = _split_context(project_root, env, kind)
    except SplitError as exc:
        plan.blockers.append(str(exc))
        return plan
    except Exception as exc:
        plan.blockers.append(f"source illisible : {exc}")
        return plan
    mine = next((g for g in split.groups if g.project == cfg.project.name), None)
    if mine is None:
        plan.blockers.append(f"aucun souvenir de « {cfg.project.name} » (project_name) dans « {source} » : rien à séparer")
        return plan
    if mine.collection == source:
        plan.blockers.append("la cible désigne la même collection que la source")
    others = sorted({str(e.metadata.get("project_name", "")) for e in entries} - {cfg.project.name, ""})
    plan.effects = [
        f"export JSON de la source entière ({split.total} souvenir(s)) dans _grimoire/_memory/migration/",
        f"copie de {mine.count} souvenir(s) vers « {mine.collection} », identifiants conservés",
        f"memory.collection_prefix: {source!r} → {mine.collection!r}",
        f"la source « {source} » n'est ni modifiée ni supprimée",
    ]
    plan.notes = ["le nettoyage de la source est un geste séparé et différé : cette action ne supprime rien"]
    if split.unassigned:
        plan.notes.append(f"{len(split.unassigned)} souvenir(s) sans project_name restent dans la source et ne seront plus vus d'ici")
    plan.facts = {
        "separable": True, "kind": kind, "source": source, "target": mine.collection, "count": mine.count,
        "total": split.total, "unassigned": len(split.unassigned), "stayWith": others,
        "configSha": state_fingerprint(config.read_bytes()), "ids": sorted(mine.ids),
        "touches": {"projects": others, "sessions": []},
    }
    plan.needs_confirmation = True  # classe D : jamais sans accord explicite
    return plan


def _split_apply(project_root: Path, env: ActionEnv, plan: _Plan) -> dict[str, Any]:
    import time

    from grimoire.memory.migration import PREVIOUS_BACKEND_BREADCRUMB
    from grimoire.memory.split import SplitError, apply_split
    from grimoire.tools import workspace_slots as slots

    kind = plan.params["kind"]
    config = project_root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")
    cfg, split, _source, entries = _split_context(project_root, env, kind)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    export_path = project_root / "_grimoire" / "_memory" / "migration" / f"split-{stamp}.json"
    opener = env.open_store or _default_open_store
    try:
        report = apply_split(
            split, entries, export_path=export_path,
            open_target=lambda collection: opener(cfg, project_root, collection),
            render_config=lambda path, collection: slots.render_split_config(path, collection, kind),
        )
    except SplitError as exc:
        return {"ok": False, "raison": str(exc)}
    wrong = [p for p, ok in report.verified.items() if not ok]
    relu = slots.current_slot(config, kind).name
    ok = not wrong and relu == plan.facts["target"] and export_path.is_file()
    return {
        "effects": [
            f"export : {export_path}", f"copiés et vérifiés : {sum(report.copied.values())}",
            f"configuration repointée vers « {plan.facts['target']} »", "source intacte — nettoyage différé",
        ],
        "inverse": {
            "kind": "restore_file", "path": "project-context.yaml", "content": before,
            "afterSha": state_fingerprint(config.read_bytes()),
            "breadcrumb": {"path": str(PREVIOUS_BACKEND_BREADCRUMB), "content": None, "keep": True},
        },
        "verification": {
            "ok": ok,
            "detail": "comptes, identifiants, export et configuration relus" if ok else f"écart : {wrong or 'configuration/export'}",
            "export": str(export_path),
        },
    }


# ── stack.start / stack.stop (B) ───────────────────────────────────────────────


def _stack_plan(project_root: Path, env: ActionEnv, params: dict[str, Any], *, stopping: bool) -> _Plan:
    plan = _Plan("stack.stop" if stopping else "stack.start", {"stack": str(params.get("stack") or "")})
    stack = _stack_param(params, plan)
    if stack is None:
        return plan
    view = _stack_view(project_root, env, stack)
    if not view["dockerReachable"]:
        plan.blockers.append(view["dockerReason"] or "Docker injoignable")
        return plan
    foreign = _foreign_reason(view["containers"], project_root)
    if foreign:
        plan.blockers.append(foreign)
        return plan
    compose = project_root / STACKS[stack]["file"]
    running = [c for c in view["containers"] if c["status"] in ("running", "restarting")]
    plan.facts = {
        "containers": [(c["name"], c["status"]) for c in view["containers"]],
        "composeExists": compose.is_file(),
        "composeSha": state_fingerprint(compose.read_bytes()) if compose.is_file() else None,
    }
    if stopping:
        if not compose.is_file():
            plan.blockers.append(f"{STACKS[stack]['file']} absent de ce projet : rien à arrêter d'ici")
        elif not running:
            plan.blockers.append("la pile n'est pas démarrée")
        plan.commands = [_compose_argv(stack, ["stop"])]
        plan.effects = [f"arrêt de {c['name']}" for c in running] + ["volumes conservés"]
        plan.needs_confirmation = len(view["projects"]) > 1 or bool(view["sessions"])
        plan.facts["touches"] = {"projects": view["projects"], "sessions": view["sessions"]}
        return plan
    needs_up = not running or len(running) < len(STACKS[stack]["kinds"])
    if running and not needs_up:
        plan.notes.append("pile déjà démarrée : réutilisée, rien n'est lancé")
        plan.facts["reuse"] = True
        return plan
    if not compose.is_file():
        plan.effects.append(f"copie du compose du kit → {STACKS[stack]['file']}")
    plan.commands = [_compose_argv(stack, ["up", "-d"])]
    plan.effects.append(f"démarrage de la pile {STACKS[stack]['label']}")
    plan.notes.append("peut tirer l'image (`docker pull` implicite) si absente de la machine : effet réseau")
    return plan


def _stack_apply(project_root: Path, env: ActionEnv, plan: _Plan, *, stopping: bool) -> dict[str, Any]:
    stack = plan.params["stack"]
    compose = project_root / STACKS[stack]["file"]
    if not stopping and plan.facts.get("reuse"):
        return {"effects": ["pile réutilisée"], "inverse": {"kind": "none"},
                "verification": _stack_verify(project_root, env, stack, running=True)}
    created = False
    if not stopping and not compose.is_file():
        template = env.memory_dir() / STACKS[stack]["template"]
        compose.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        created = True
    code, out, err = _docker(env, plan.commands[0], project_root)
    if code != 0:
        if created:
            compose.unlink(missing_ok=True)
        detail = (err or out).strip().splitlines()
        return {"ok": False, "raison": f"docker compose a échoué : {detail[0] if detail else f'code {code}'}"}
    inverse = (
        {"kind": "start_stack", "stack": stack}
        if stopping else {"kind": "stop_stack", "stack": stack, "removeFile": created}
    )
    return {
        "effects": [" ".join(plan.commands[0])] + (["compose du kit copié"] if created else []),
        "inverse": inverse,
        "verification": _stack_verify(project_root, env, stack, running=not stopping),
    }


def _stack_verify(project_root: Path, env: ActionEnv, stack: str, *, running: bool) -> dict[str, Any]:
    view = _stack_view(project_root, env, stack)
    mine = [c for c in view["containers"] if c["relation"] != "foreign"]
    is_up = [c["status"] in ("running", "restarting") for c in mine]
    ok = bool(mine) and (all(is_up) if running else not any(is_up))
    want = "démarrés" if running else "arrêtés"
    return {"ok": ok, "detail": f"conteneurs {want}" if ok else f"conteneurs relus : {[(c['name'], c['status']) for c in mine]}"}


def _stack_rollback(project_root: Path, env: ActionEnv, inverse: dict[str, Any]) -> dict[str, Any]:
    kind = inverse.get("kind")
    if kind == "none":
        return {"ok": True, "detail": "rien à défaire (pile réutilisée)"}
    stack = inverse["stack"]
    view = _stack_view(project_root, env, stack)
    foreign = _foreign_reason(view["containers"], project_root)
    if foreign:
        return {"ok": False, "detail": foreign}
    verb = ["up", "-d"] if kind == "start_stack" else ["stop"]
    code, out, err = _docker(env, _compose_argv(stack, verb), project_root)
    if code != 0:
        return {"ok": False, "detail": f"docker compose a échoué : {(err or out).strip()[:200]}"}
    if inverse.get("removeFile"):
        (project_root / STACKS[stack]["file"]).unlink(missing_ok=True)
    check = _stack_verify(project_root, env, stack, running=kind == "start_stack")
    return {"ok": check["ok"], "detail": check["detail"]}


# ── stack.restart_policy (B) ───────────────────────────────────────────────────


def _restart_plan(project_root: Path, env: ActionEnv, params: dict[str, Any]) -> _Plan:
    kind = str(params.get("service") or "")
    policy = str(params.get("policy") or "")
    plan = _Plan("stack.restart_policy", {"service": kind, "policy": policy})
    if policy not in _POLICIES:
        plan.blockers.append(f"politique inconnue « {policy} » — attendu : {', '.join(_POLICIES)}")
    stack = next((s for s, spec in STACKS.items() if kind in spec["kinds"]), None)
    if stack is None:
        plan.blockers.append(f"service inconnu « {kind} » — attendu : qdrant, weaviate ou neo4j")
    if plan.blockers:
        return plan
    view = _stack_view(project_root, env, str(stack))
    if not view["dockerReachable"]:
        plan.blockers.append(view["dockerReason"] or "Docker injoignable")
        return plan
    target = next((c for c in view["containers"] if c["kind"] == kind), None)
    if target is None:
        plan.blockers.append(f"aucun conteneur local pour {kind} : démarrez d'abord la pile")
        return plan
    if target["relation"] == "foreign":
        plan.blockers.append(_foreign_reason([target], project_root) or "conteneur emprunté")
        return plan
    plan.facts = {"container": target["name"], "current": target["restartPolicy"]}
    if target["restartPolicy"] == policy:
        plan.blockers.append(f"« {target['name']} » a déjà la politique {policy}")
    plan.commands = [["docker", "update", f"--restart={policy}", target["name"]]]
    plan.effects = [f"politique de {target['name']} : {target['restartPolicy']} → {policy}"]
    plan.notes.append("le fichier compose n'est pas réécrit : `up -d` rétablirait la politique du compose")
    return plan


def _restart_read(env: ActionEnv, name: str) -> str | None:
    code, out, _ = _docker(env, ["docker", "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", name])
    return out.strip() if code == 0 else None


def _restart_apply(project_root: Path, env: ActionEnv, plan: _Plan) -> dict[str, Any]:
    name, policy = plan.facts["container"], plan.params["policy"]
    code, out, err = _docker(env, plan.commands[0], project_root)
    if code != 0:
        return {"ok": False, "raison": f"docker update a échoué : {(err or out).strip()[:200]}"}
    read = _restart_read(env, name)
    return {
        "effects": [" ".join(plan.commands[0])],
        "inverse": {"kind": "restart_policy", "service": plan.params["service"], "container": name,
                    "policy": plan.facts["current"]},
        "verification": {"ok": read == policy, "detail": f"docker inspect relit « {read} »"},
    }


def _restart_rollback(project_root: Path, env: ActionEnv, inverse: dict[str, Any]) -> dict[str, Any]:
    fresh = _restart_plan(project_root, env, {"service": inverse["service"], "policy": "no" if inverse["policy"] != "no" else "unless-stopped"})
    if any("empruntée" in b for b in fresh.blockers):
        return {"ok": False, "detail": fresh.blockers[0]}
    code, out, err = _docker(env, ["docker", "update", f"--restart={inverse['policy']}", inverse["container"]], project_root)
    if code != 0:
        return {"ok": False, "detail": f"docker update a échoué : {(err or out).strip()[:200]}"}
    read = _restart_read(env, inverse["container"])
    return {"ok": read == inverse["policy"], "detail": f"docker inspect relit « {read} »"}


# ── pip.extras (C) ─────────────────────────────────────────────────────────────


def _extras_plan(params: dict[str, Any]) -> _Plan:
    from grimoire.tools.memory_setup import _EXTRA_MODULES, extras_install_hint

    plan = _Plan("pip.extras", {"extras": [str(e) for e in params.get("extras") or []]})
    known = sorted({extra for extra, _ in _EXTRA_MODULES.values()})
    unknown = [e for e in plan.params["extras"] if e not in known]
    if not plan.params["extras"] or unknown:
        plan.blockers.append(f"extras inconnus ou absents {unknown or ''} — attendu parmi : {', '.join(known)}")
        return plan
    plan.commands = []
    plan.notes = [extras_install_hint(plan.params["extras"]), "à copier et lancer vous-même : le cockpit n'exécute jamais pip/uv"]
    plan.effects = []
    return plan


# ── Routes : catalogue, plan, apply, rollback ──────────────────────────────────


def actions_catalogue(project_root: Path) -> dict[str, Any]:
    """``GET data/actions`` — le catalogue ; chaque spécification se lit sans rien exécuter."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "actions": [spec.to_dict() for spec in CATALOGUE.values()],
        "stacks": {k: {"label": v["label"], "file": v["file"], "kinds": list(v["kinds"])} for k, v in STACKS.items()},
        "policies": list(_POLICIES),
        "journal": str(journal_path(project_root)),
    }


def _build_plan(project_root: Path, env: ActionEnv, action: str, params: dict[str, Any]) -> _Plan:
    if action == "memory.compose":
        return _memory_plan(project_root, env, params)
    if action == "memory.slot":
        return _slot_plan(project_root, env, params)
    if action == "memory.split":
        return _split_plan(project_root, env, params)
    if action in ("stack.start", "stack.stop"):
        return _stack_plan(project_root, env, params, stopping=action == "stack.stop")
    if action == "stack.restart_policy":
        return _restart_plan(project_root, env, params)
    return _extras_plan(params)


def plan_action(project_root: Path, action: str, body: dict[str, Any], *, env: ActionEnv | None = None) -> dict[str, Any]:
    """``POST data/actions/<id>/plan`` — préconditions, plan, empreinte ; seul le journal est écrit."""
    env = env or ActionEnv()
    spec = CATALOGUE.get(action)
    if spec is None:
        raise FileNotFoundError(f"action inconnue : {action}")
    raw = body.get("params")
    params: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {k: v for k, v in body.items() if k != "params"}
    plan = _build_plan(project_root, env, action, params)
    run_id = f"act-{uuid.uuid4().hex[:12]}"
    now = env.now()
    out = {
        "schemaVersion": SCHEMA_VERSION, "ok": not plan.blockers, "plan_id": run_id,
        "empreinte": plan.fingerprint, "expire": (now + PLAN_TTL).isoformat(),
        "class": spec.klass, "applicable": spec.applicable, "blockers": plan.blockers, "plan": plan.to_dict(),
        "spec": spec.to_dict(),
    }
    if not spec.applicable:
        return out
    error = _log(project_root, {
        "event": "plan", "run_id": run_id, "action": action, "class": spec.klass,
        "project": project_root.name, "empreinte": out["empreinte"], "expire": out["expire"],
        "params": plan.params, "blockers": plan.blockers,
    })
    if error:
        return _refus(action, error)
    return out


def _find_plan(rows: list[dict[str, Any]], run_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    plan = next((r for r in rows if r.get("event") == "plan" and r.get("run_id") == run_id), None)
    return plan, [r for r in rows if r.get("run_id") == run_id and r is not plan]


def apply_action(
    project_root: Path, action: str, body: dict[str, Any], *, env: ActionEnv | None = None,
) -> dict[str, Any]:
    """``POST data/actions/<id>/apply`` — exécute le plan consigné, et seulement lui."""
    env = env or ActionEnv()
    spec = CATALOGUE.get(action)
    if spec is None:
        raise FileNotFoundError(f"action inconnue : {action}")
    if not spec.applicable:
        raise FileNotFoundError(f"{action} : classe {spec.klass}, pas de route apply")
    run_id, empreinte = str(body.get("plan_id") or ""), str(body.get("empreinte") or "")
    if not run_id or not empreinte:
        return _refus(action, "plan_id et empreinte requis : demandez d'abord un plan")
    rows, error = _read_journal(project_root)
    if error:
        return _refus(action, f"{error} : pas de journal, pas d'effet")
    recorded, later = _find_plan(rows, run_id)
    if recorded is None or recorded.get("action") != action:
        return _refus(action, "aucun plan correspondant au journal — demandez un plan")
    if any(r.get("event") == "apply_started" for r in later):
        return _refus(action, "ce plan a déjà été appliqué — demandez un nouveau plan")
    if empreinte != recorded.get("empreinte"):
        return _refus(action, "empreinte différente de celle du plan consigné")
    if datetime.fromisoformat(str(recorded["expire"])) < env.now():
        return _refus(action, "plan expiré — demandez un nouveau plan")
    plan = _build_plan(project_root, env, action, dict(recorded.get("params") or {}))
    if plan.fingerprint != empreinte:
        return _refus(action, "l'état a changé depuis le plan — demandez un plan à jour", empreinte=plan.fingerprint)
    if plan.blockers:
        return _refus(action, f"préconditions fausses : {'; '.join(plan.blockers)}")
    if plan.needs_confirmation and body.get("confirm") is not True:
        # Avant le journal : le plan n'est pas consommé, on le rejoue avec `confirm: true`.
        return _refus(
            action, "geste engageant (pile partagée, sessions vivantes ou classe D) : confirmation requise (confirm: true)",
            touches=plan.facts["touches"],
        )
    error = _log(project_root, {"event": "apply_started", "run_id": run_id, "action": action, "empreinte": empreinte})
    if error:
        return _refus(action, f"{error} : pas de journal, pas d'effet")
    base = {"event": "", "run_id": run_id, "action": action, "class": spec.klass, "project": project_root.name}
    try:
        if action == "memory.compose":
            result = _memory_apply(project_root, env, plan, run_id)
        elif action == "memory.slot":
            result = _slot_apply(project_root, plan)
        elif action == "memory.split":
            result = _split_apply(project_root, env, plan)
        elif action == "stack.restart_policy":
            result = _restart_apply(project_root, env, plan)
        else:
            result = _stack_apply(project_root, env, plan, stopping=action == "stack.stop")
    except (OSError, ValueError) as exc:
        result = {"ok": False, "raison": str(exc)}
    if result.get("ok") is False:
        _log(project_root, {**base, "event": "failed", "raison": result["raison"]})
        return _refus(action, result["raison"], run_id=run_id, **{k: v for k, v in result.items() if k == "touches"})
    _log(project_root, {**base, "event": "effects", "effects": result["effects"], "inverse": result["inverse"]})
    verification = result["verification"]
    _log(project_root, {**base, "event": "verification", **verification})
    status = "applied" if verification["ok"] else "failed"
    _log(project_root, {**base, "event": status})
    return {
        "schemaVersion": SCHEMA_VERSION, "action": action, "ok": verification["ok"], "statut": status,
        "run_id": run_id, "effects": result["effects"], "verification": verification,
        "rollback": result["inverse"].get("kind") != "none",
        "raison": "" if verification["ok"] else f"vérification échouée : {verification['detail']} — le retour arrière reste proposé",
    }


def rollback_action(
    project_root: Path, action: str, body: dict[str, Any], *, env: ActionEnv | None = None,
) -> dict[str, Any]:
    """``POST data/actions/<id>/rollback`` — l'inverse consigné d'une exécution, puis sa vérification."""
    env = env or ActionEnv()
    spec = CATALOGUE.get(action)
    if spec is None or not spec.applicable:
        raise FileNotFoundError(f"action inconnue ou sans rollback : {action}")
    run_id = str(body.get("run_id") or "")
    rows, error = _read_journal(project_root)
    if error:
        return _refus(action, f"{error} : pas de journal, pas d'effet")
    _, run = _find_plan(rows, run_id)
    effects = next((r for r in run if r.get("event") == "effects" and r.get("action") == action), None)
    if effects is None:
        return _refus(action, "aucune exécution consignée avec cet identifiant")
    if any(r.get("event") == "rollback_applied" for r in run):
        return _refus(action, "cette exécution a déjà été annulée")
    error = _log(project_root, {"event": "rollback_started", "run_id": run_id, "action": action})
    if error:
        return _refus(action, f"{error} : pas de journal, pas d'effet")
    inverse = effects["inverse"]
    kind = inverse.get("kind")
    try:
        if kind == "restore_file":
            result = _memory_rollback(project_root, inverse)
        elif kind == "restart_policy":
            result = _restart_rollback(project_root, env, inverse)
        else:
            result = _stack_rollback(project_root, env, inverse)
    except (OSError, ValueError) as exc:
        result = {"ok": False, "detail": str(exc)}
    if result["ok"]:
        _log(project_root, {"event": "rollback_applied", "run_id": run_id, "action": action, "detail": result["detail"]})
    else:
        _log(project_root, {"event": "failed", "run_id": run_id, "action": action, "raison": result["detail"]})
    return {
        "schemaVersion": SCHEMA_VERSION, "action": action, "ok": result["ok"], "run_id": run_id,
        "statut": "rollback_applied" if result["ok"] else "failed", "detail": result["detail"],
        "raison": "" if result["ok"] else result["detail"],
    }
