"""Plan de mise en place du Memory OS — surface de ``grimoire memory up``.

``grimoire init`` détecte un backend vectoriel et écrit ``memory.backend``,
mais s'arrête là : ``neo4j_uri``, ``knowledge_graph``, ``memory_graph``,
``code_graph``, ``task_memory`` et ``redis_url`` restent commentés dans
``project-context.tpl.yaml`` et rien ne les décommente. Un projet neuf a donc
la couche vectorielle et rien d'autre, et il faut éditer le YAML à la main
pour obtenir la stack complète.

Ce module calcule le plan qui comble ce trou, en une règle : **on n'active
que ce qui répond**. Écrire ``memory_graph: neo4j`` alors que Neo4j est éteint
produirait une config qui échoue silencieusement au runtime — exactement le
mode de panne que l'observabilité vient de rendre visible. Un service
injoignable est signalé, pas activé.

Le plan est calculé sans effet de bord ; ``apply_memory_plan`` est la seule
fonction qui écrit, et elle passe par ruamel pour préserver commentaires et
mise en forme du fichier existant.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MEMORY_SETUP_SCHEMA_VERSION = "grimoire-memory-setup/v1"

#: Budget de sonde — identique à ``cmd_up`` : jamais bloquant.
_SOCKET_TIMEOUT = 0.5

_DEFAULT_URLS = {
    "weaviate": "http://localhost:8080",
    "qdrant": "http://localhost:6333",
    "neo4j": "bolt://localhost:7687",
    "redis": "redis://localhost:6379/0",
    "ollama": "http://localhost:11434",
}
_DEFAULT_PORTS = {"weaviate": 8080, "qdrant": 6333, "neo4j": 7687, "redis": 6379, "ollama": 11434}

#: Extra pip requis par service, et les modules dont **au moins un** prouve son
#: installation. Les extras vectoriels acceptent deux moteurs d'embedding :
#: fastembed (tiré par les extras) et sentence-transformers (repli historique,
#: utilisé s'il est déjà présent). Ne tester qu'un seul des deux ferait
#: déclarer l'extra absent sur une installation parfaitement valide, et
#: memory up retomberait en lexical sans raison.
_EXTRA_MODULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "weaviate": ("weaviate", ("fastembed", "sentence_transformers")),
    "qdrant": ("qdrant", ("qdrant_client",)),
    "neo4j": ("neo4j", ("neo4j",)),
    "redis": ("redis", ("redis",)),
    "ollama": ("ollama", ("ollama",)),
}

#: Commande de démarrage proposée quand un service manque.
_START_COMMANDS = {
    "weaviate": "docker compose -f docker-compose.memory-target.yml up -d",
    "neo4j": "docker compose -f docker-compose.memory-target.yml up -d",
    "qdrant": "docker compose -f docker-compose.memory.yml up -d",
    "redis": "docker run -d --name grimoire-redis -p 6379:6379 redis:7-alpine",
    "ollama": "ollama serve",
}

PROFILES: tuple[str, ...] = ("lexical", "vector", "full")


@dataclass(frozen=True, slots=True)
class ServiceProbe:
    """Disponibilité d'un service mémoire sur cette machine."""

    id: str
    url: str
    reachable: bool
    extra: str
    extra_installed: bool

    @property
    def usable(self) -> bool:
        """Utilisable seulement si le service répond *et* que l'extra est là."""
        return self.reachable and self.extra_installed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "reachable": self.reachable,
            "extra": self.extra,
            "extraInstalled": self.extra_installed,
            "usable": self.usable,
        }


@dataclass
class MemoryPlan:
    """Ce que ``memory up`` ferait, et pourquoi."""

    profile: str
    project_root: Path
    services: dict[str, ServiceProbe]
    config: dict[str, Any] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": MEMORY_SETUP_SCHEMA_VERSION,
            "profile": self.profile,
            "projectRoot": str(self.project_root),
            "services": {k: v.to_dict() for k, v in self.services.items()},
            "config": dict(self.config),
            "changes": list(self.changes),
            "warnings": list(self.warnings),
            "nextSteps": list(self.next_steps),
            "hasChanges": self.has_changes,
        }


def _tcp_reachable(url: str, default_port: int, *, timeout: float = _SOCKET_TIMEOUT) -> bool:
    """Vrai si un connect TCP aboutit dans *timeout* secondes."""
    raw = url if "//" in url else f"//{url}"
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _module_installed(names: tuple[str, ...]) -> bool:
    """Vrai dès qu'un des *names* est importable."""
    for name in names:
        try:
            if find_spec(name) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def probe_services(urls: dict[str, str] | None = None) -> dict[str, ServiceProbe]:
    """Sonde chaque service mémoire connu sur cette machine. Ne lève jamais."""
    resolved = {**_DEFAULT_URLS, **(urls or {})}
    probes: dict[str, ServiceProbe] = {}
    for service, (extra, modules) in _EXTRA_MODULES.items():
        url = resolved[service]
        probes[service] = ServiceProbe(
            id=service,
            url=url,
            reachable=_tcp_reachable(url, _DEFAULT_PORTS[service]),
            extra=extra,
            extra_installed=_module_installed(modules),
        )
    return probes


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    return cleaned or "grimoire"


def _camel(text: str) -> str:
    return "".join(part.capitalize() for part in _slug(text).split("_") if part) or "Grimoire"


def build_memory_plan(
    project_root: Path,
    *,
    profile: str = "full",
    services: dict[str, ServiceProbe] | None = None,
) -> MemoryPlan:
    """Calcule le bloc ``memory:`` cible pour *profile*, borné au réellement disponible.

    Aucun effet de bord : la fonction lit la config existante et retourne le
    delta. Un service déclaré par le profil mais injoignable descend en
    avertissement au lieu d'être écrit — une config qui ment coûte plus cher
    qu'une config incomplète.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    if profile not in PROFILES:
        raise ValueError(f"Unknown memory profile '{profile}', expected one of: {list(PROFILES)}")

    probes = services if services is not None else probe_services()
    plan = MemoryPlan(profile=profile, project_root=project_root, services=probes)

    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        plan.warnings.append("Aucun project-context.yaml — lancez d'abord `grimoire init`.")
        return plan

    try:
        cfg = GrimoireConfig.from_yaml(config_path)
    except (GrimoireConfigError, OSError) as exc:
        plan.warnings.append(f"Config illisible : {exc}")
        return plan

    project_name = cfg.project.name or project_root.name
    prefix = cfg.memory.collection_prefix
    if not prefix or prefix == "grimoire":
        prefix = _slug(project_name)

    target = _target_config(profile, probes, prefix=prefix, project_name=project_name, plan=plan)
    plan.config = target
    plan.changes = _diff_config(cfg.memory, target, _raw_memory_block(config_path))
    plan.next_steps = _next_steps(profile, probes, plan)
    return plan


def _target_config(
    profile: str,
    probes: dict[str, ServiceProbe],
    *,
    prefix: str,
    project_name: str,
    plan: MemoryPlan,
) -> dict[str, Any]:
    """Bloc ``memory:`` visé, réduit à ce que la machine peut réellement servir."""
    target: dict[str, Any] = {"collection_prefix": prefix}

    if profile == "lexical":
        target["backend"] = "lexical"
        target["vector_database"] = False
        target["retrieval_mode"] = "lexical"
        return target

    # ── Couche vectorielle ──
    weaviate, qdrant = probes["weaviate"], probes["qdrant"]
    if weaviate.usable:
        target["backend"] = "weaviate-server"
        target["weaviate_url"] = weaviate.url
        target["weaviate_collection"] = f"{_camel(project_name)}Memory"
        target["embedding_model"] = "sentence-transformers/all-MiniLM-L6-v2"
    elif qdrant.usable:
        target["backend"] = "qdrant-server"
        target["qdrant_url"] = qdrant.url
        target["embedding_model"] = "sentence-transformers/all-MiniLM-L6-v2"
    else:
        target["backend"] = "lexical"
        plan.warnings.append(
            "Aucun backend vectoriel utilisable — repli sur `lexical` (BM25, zéro dépendance)."
        )
        _warn_unusable(plan, weaviate)
        _warn_unusable(plan, qdrant)

    if profile == "vector":
        return target

    # ── Couches graphe (profil full) ──
    neo4j = probes["neo4j"]
    if neo4j.usable:
        target["neo4j_uri"] = neo4j.url
        target["neo4j_user"] = "neo4j"
        # Nom de variable d'environnement, pas un secret.
        target["neo4j_password_env"] = "GRIMOIRE_NEO4J_PASSWORD"  # noqa: S105
        target["neo4j_database"] = "neo4j"
        target["knowledge_graph"] = "neo4j"
        target["memory_graph"] = "neo4j"
        target["code_graph"] = "neo4j"
        target["task_memory"] = "neo4j"
    else:
        plan.warnings.append(
            "Neo4j indisponible — les couches graphe, code et tâches restent hors config."
        )
        _warn_unusable(plan, neo4j)

    # ── Couche chaude (profil full) ──
    redis = probes["redis"]
    if redis.usable:
        target["short_term_backend"] = "redis"
        target["redis_url"] = redis.url
    else:
        plan.warnings.append("Redis indisponible — mémoire chaude laissée sur `sqlite`.")
        _warn_unusable(plan, redis)

    return target


def _warn_unusable(plan: MemoryPlan, probe: ServiceProbe) -> None:
    """Distingue « service éteint » de « extra pip manquant » : le remède diffère."""
    if probe.usable:
        return
    if not probe.reachable:
        cmd = _START_COMMANDS.get(probe.id, "")
        plan.warnings.append(
            f"  {probe.id} : injoignable sur {probe.url}" + (f" → {cmd}" if cmd else "")
        )
    elif not probe.extra_installed:
        plan.warnings.append(
            f"  {probe.id} : service en ligne mais extra absent → "
            f'pip install "grimoire-kit[{probe.extra}]"'
        )


def _raw_memory_block(config_path: Path) -> dict[str, Any]:
    """Bloc ``memory:`` tel qu'écrit dans le fichier, sans les valeurs par défaut."""
    try:
        from ruamel.yaml import YAML

        yaml = YAML()
        with config_path.open(encoding="utf-8") as fh:
            data = yaml.load(fh)
    except Exception:  # fichier illisible : traité comme vide, jamais fatal
        return {}
    block = data.get("memory") if isinstance(data, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def _diff_config(current: Any, target: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Delta entre le fichier et la cible, clé par clé.

    La comparaison porte sur ce qui est **écrit dans le fichier**, pas sur les
    valeurs par défaut de la dataclass. Une clé absente est un ajout même
    quand sa valeur cible égale le défaut : ``neo4j_password_env`` vaut déjà
    ``GRIMOIRE_NEO4J_PASSWORD`` par défaut, mais tant qu'elle n'est pas dans
    le fichier, rien ne dit à l'opérateur quelle variable exporter.
    """
    changes: list[dict[str, Any]] = []
    for key, new_value in sorted(target.items()):
        if key in raw:
            if raw[key] == new_value:
                continue
            changes.append({"key": key, "old": raw[key], "new": new_value, "absent": False})
            continue
        default = getattr(current, key, None)
        changes.append({"key": key, "old": default, "new": new_value, "absent": True})
    return changes


def _next_steps(profile: str, probes: dict[str, ServiceProbe], plan: MemoryPlan) -> list[str]:
    """Ce qu'il reste à faire après l'écriture — dans l'ordre d'exécution."""
    steps: list[str] = []
    missing_extras = sorted({p.extra for p in probes.values() if p.reachable and not p.extra_installed})
    if missing_extras:
        steps.append(f'pip install "grimoire-kit[{",".join(missing_extras)}]"')

    target = plan.config
    if target.get("neo4j_password_env"):
        steps.append(f"export {target['neo4j_password_env']}=<password>  # requis, sinon écritures graphe muettes")

    if target.get("backend", "").endswith("-server") or target.get("neo4j_uri"):
        steps.append("grimoire memory status  # vérifier les 7 couches et la parité")
    if target.get("neo4j_uri"):
        steps.extend([
            "grimoire memory graph sync-code",
            "grimoire memory graph sync-tasks",
            "grimoire memory vector sync-code --granularity file,symbol,method,test,contract",
            "grimoire memory vector sync-tasks",
            "grimoire memory gate",
        ])
    return steps


def apply_memory_plan(plan: MemoryPlan) -> list[str]:
    """Écrit le bloc ``memory:`` du plan dans ``project-context.yaml``.

    Passe par ruamel pour préserver commentaires, ordre et mise en forme du
    fichier existant. Retourne la liste des clés effectivement écrites.
    """
    if not plan.has_changes:
        return []

    from ruamel.yaml import YAML

    config_path = plan.project_root / "project-context.yaml"
    yaml = YAML()
    yaml.preserve_quotes = True
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)

    if not isinstance(data, dict):
        raise ValueError("project-context.yaml ne contient pas un mapping racine")
    if "memory" not in data or not isinstance(data.get("memory"), dict):
        data["memory"] = {}

    written = []
    for change in plan.changes:
        data["memory"][change["key"]] = change["new"]
        written.append(str(change["key"]))

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return written
