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

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from grimoire.memory import profiles as memory_profiles

if TYPE_CHECKING:
    from grimoire.core.config import MemoryConfig

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

#: Canonical composition ids — the same vocabulary as :mod:`grimoire.memory.profiles`
#: and the ``layer_profile`` schema field: lexical | standard | graphe | complet.
#: The earlier ``lexical | vector | full`` names some scripts still pass are
#: resolved through :data:`grimoire.memory.profiles.ALIASES` (#527).
PROFILES: tuple[str, ...] = memory_profiles.PROFILE_ORDER


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
    #: Informational call-outs that are not warnings — e.g. the Redis key
    #: namespace a shared instance will use (#527).
    notes: list[str] = field(default_factory=list)

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
            "notes": list(self.notes),
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


def docker_daemon_reachable(*, timeout: float = 2.0) -> bool:
    """Whether a Docker CLI exists *and* its daemon actually answers.

    ``shutil.which("docker")`` alone (the check ``grimoire init``'s wizard has
    used since #552) only proves the client binary is on ``PATH`` — a Docker
    Desktop that quit, a rootless daemon that never started, or a CLI shim
    left behind by an uninstall all pass it while unable to run anything.
    ``docker info`` is the cheapest round-trip that actually talks to the
    daemon; a machine without Docker at all must never pay its process-spawn
    cost, hence the early return.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _ollama_usable(*, timeout: float = _SOCKET_TIMEOUT) -> bool:
    """Whether a local Ollama both answers and has its python extra installed.

    The single live check :func:`local_embedding_available` needs when it was
    not handed a pre-computed probe set — a direct TCP probe of Ollama alone,
    never the other four memory services :func:`probe_services` covers
    (Weaviate/Qdrant/Neo4j/Redis). ``doctor``/``status`` (via
    :func:`grimoire.core.project_capabilities.memory_upgrade_target`) call
    :func:`local_embedding_available` with no probes on every run — a full
    :func:`probe_services` there would add up to a few seconds of socket
    timeouts to check a fact only Ollama's reachability can change (#619
    review).
    """
    if not _module_installed(_EXTRA_MODULES["ollama"][1]):
        return False
    return _tcp_reachable(_DEFAULT_URLS["ollama"], _DEFAULT_PORTS["ollama"], timeout=timeout)


def local_embedding_available(probes: dict[str, ServiceProbe] | None = None) -> bool:
    """Whether this machine can compute embeddings without any server.

    True when ``fastembed`` or ``sentence-transformers`` is importable — both
    embed locally, no network round-trip once the model is cached — or when a
    local Ollama already answers with its extra installed. This is the gate
    every vector-backed profile (``standard``/``graphe``/``complet``) needs:
    even the server-backed ones (Weaviate, Qdrant) embed client-side through
    :mod:`grimoire.memory.embedding`, so a docker daemon with nothing to embed
    with can still only serve ``lexical``.

    ``probes`` lets a caller that already ran :func:`probe_services` this
    command (e.g. :func:`recommend_profile`) reuse that result instead of
    probing twice. Left ``None`` — the ``doctor``/``status`` path, which never
    needs the other four services — this checks Ollama alone
    (:func:`_ollama_usable`), never the full service set.
    """
    if _module_installed(("fastembed",)) or _module_installed(("sentence_transformers",)):
        return True
    if probes is not None:
        ollama = probes.get("ollama")
        return bool(ollama and ollama.usable)
    return _ollama_usable()


def recommend_profile(
    probes: dict[str, ServiceProbe] | None = None,
    *,
    docker_ready: bool | None = None,
) -> tuple[str, str]:
    """The richest memory profile this machine can serve, and why.

    Order, per the 2026-09-18 onboarding decision: no local embedding
    capability at all means every vector-backed profile would build a store
    it can never fill — ``lexical`` is the honest floor, named as such rather
    than left to look like an arbitrary default. With embedding capacity,
    ``complet`` is offered whenever Docker can actually start the missing
    services (:func:`start_memory_stack`); short of that, ``standard`` still
    gets real semantic search through the embedded ``qdrant-local`` backend
    (:func:`build_memory_plan`) — no server, no container, no consent needed.
    """
    resolved = probes if probes is not None else probe_services()
    if not local_embedding_available(resolved):
        return "lexical", "no local embedding capability (fastembed/sentence-transformers not installed, no Ollama reachable)"
    ready = docker_daemon_reachable() if docker_ready is None else docker_ready
    if ready:
        return "complet", "Docker available — the most complete profile this machine can serve"
    return "standard", "local vector embeddings (fastembed/sentence-transformers), no Docker or server needed"


def unreached_configured_services(memory: MemoryConfig) -> list[str]:
    """Services *memory* (a ``GrimoireConfig.memory`` section) declares that
    do not actually answer right now.

    A project can be scaffolded for ``complet``/``graphe`` (backend pinned to
    ``weaviate-server``, ``knowledge_graph: neo4j``…) before its containers
    were ever started — the express and interactive Memory steps in
    ``grimoire init`` write that aspirational config without starting
    anything unless consent was given (2026-09-18 arbitrage). This is the
    gap ``grimoire doctor`` reports as *pile mémoire non démarrée*: never a
    diagnostic on whether the config is well-formed, only on whether the
    services it names are reachable this instant.

    Returns service ids (``"weaviate"``, ``"qdrant"``, ``"neo4j"``,
    ``"redis"``), empty when nothing configured is missing.
    """
    missing: list[str] = []
    if memory.backend == "weaviate-server" and memory.weaviate_url and not _tcp_reachable(
        memory.weaviate_url, _DEFAULT_PORTS["weaviate"],
    ):
        missing.append("weaviate")
    elif memory.backend == "qdrant-server" and memory.qdrant_url and not _tcp_reachable(
        memory.qdrant_url, _DEFAULT_PORTS["qdrant"],
    ):
        missing.append("qdrant")
    if memory.knowledge_graph == "neo4j" and memory.neo4j_uri and not _tcp_reachable(
        memory.neo4j_uri, _DEFAULT_PORTS["neo4j"],
    ):
        missing.append("neo4j")
    if memory.short_term_backend == "redis" and memory.redis_url and not _tcp_reachable(
        memory.redis_url, _DEFAULT_PORTS["redis"],
    ):
        missing.append("redis")
    return missing


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
    profile: str = "complet",
    services: dict[str, ServiceProbe] | None = None,
) -> MemoryPlan:
    """Calcule le bloc ``memory:`` cible pour *profile*, borné au réellement disponible.

    Aucun effet de bord : la fonction lit la config existante et retourne le
    delta. Un service déclaré par le profil mais injoignable descend en
    avertissement au lieu d'être écrit — une config qui ment coûte plus cher
    qu'une config incomplète.

    *profile* accepte le vocabulaire canonique (``lexical | standard | graphe |
    complet``, celui du schéma et de :mod:`grimoire.memory.profiles`) ainsi que
    les anciens noms ``vector``/``full`` par alias (#527) : les deux résolvent
    au même plan, et ``plan.profile`` rapporte toujours le nom canonique.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    if not memory_profiles.is_known(profile):
        raise ValueError(f"Unknown memory profile '{profile}', expected one of: {list(PROFILES)}")
    canonical = memory_profiles.resolve(profile).id

    probes = services if services is not None else probe_services()
    plan = MemoryPlan(profile=canonical, project_root=project_root, services=probes)

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

    target = _target_config(canonical, probes, prefix=prefix, project_name=project_name, plan=plan)
    plan.config = target
    plan.changes = _diff_config(cfg.memory, target, _raw_memory_block(config_path))
    plan.next_steps = _next_steps(canonical, probes, plan)
    return plan


def _finalize_layers(target: dict[str, Any]) -> None:
    """Pose ``layer_profile``/``retrieval_mode``/``vector_database`` d'après ce que
    *target* sert réellement, pas d'après le profil demandé (#527).

    Un profil ``complet`` qui retombe sur `lexical` faute de service vectoriel
    ne doit jamais écrire ``layer_profile: complet`` : la composition promise
    et la composition écrite doivent toujours coïncider, sinon le cockpit et
    ``memory status`` lisent une couche qui n'existe pas.
    """
    backend = target.get("backend", "lexical")
    if backend == "lexical":
        target["vector_database"] = False
        target["retrieval_mode"] = "lexical"
        target["layer_profile"] = "lexical"
        return
    target["vector_database"] = True
    target["retrieval_mode"] = "hybrid"
    if target.get("short_term_backend") == "redis" and target.get("knowledge_graph") == "neo4j":
        target["layer_profile"] = "complet"
    elif target.get("knowledge_graph") == "neo4j":
        target["layer_profile"] = "graphe"
    else:
        target["layer_profile"] = "standard"


def _target_config(
    profile: str,
    probes: dict[str, ServiceProbe],
    *,
    prefix: str,
    project_name: str,
    plan: MemoryPlan,
) -> dict[str, Any]:
    """Bloc ``memory:`` visé, réduit à ce que la machine peut réellement servir.

    *profile* est déjà le nom canonique (``lexical | standard | graphe |
    complet``) : la résolution d'alias se fait une seule fois, dans
    :func:`build_memory_plan`.
    """
    target: dict[str, Any] = {"collection_prefix": prefix}

    if profile == "lexical":
        target["backend"] = "lexical"
        _finalize_layers(target)
        return target

    # ── Couche vectorielle (standard, graphe, complet) ──
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
    elif qdrant.extra_installed and local_embedding_available(probes):
        # No server reachable, but the client library and a local embedding
        # engine both are: an embedded, file-local Qdrant (no service, no
        # Docker, nothing shared with any other project) still gives this
        # profile real semantic search instead of falling all the way back to
        # `lexical` for want of a server nobody asked to run (onboarding
        # audit 2026-09-18: "standard" was never reachable without one).
        target["backend"] = "qdrant-local"
        target["embedding_model"] = "sentence-transformers/all-MiniLM-L6-v2"
        plan.notes.append(
            "Aucun service vectoriel serveur — repli sur Qdrant embarqué "
            "(fichier local, aucun service, aucun Docker requis)."
        )
        _warn_unusable(plan, weaviate)
        _warn_unusable(plan, qdrant)
    else:
        target["backend"] = "lexical"
        plan.warnings.append(
            "Aucun backend vectoriel utilisable — repli sur `lexical` (BM25, zéro dépendance) : "
            "aucune capacité d'embedding locale (fastembed/sentence-transformers) et aucun "
            "service accessible."
        )
        _warn_unusable(plan, weaviate)
        _warn_unusable(plan, qdrant)

    if profile == "standard":
        _finalize_layers(target)
        return target

    # ── Couches graphe (graphe, complet) ──
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

    if profile == "graphe":
        _finalize_layers(target)
        return target

    # ── Couche chaude (complet uniquement) ──
    redis = probes["redis"]
    if redis.usable:
        target["short_term_backend"] = "redis"
        target["redis_url"] = redis.url
        plan.notes.append(
            f"Redis : espace de noms « {prefix} » (préfixe de clé par projet — "
            "isole ce projet des autres sur la même instance, cf. #527)."
        )
    else:
        plan.warnings.append("Redis indisponible — mémoire chaude laissée sur `sqlite`.")
        _warn_unusable(plan, redis)

    _finalize_layers(target)
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
        if change["key"] == "backend" and not change.get("absent") and change["old"]:
            # Laisse une trace du backend qu'on quitte : `memory migrate run`
            # la lit quand `--source` n'est pas fourni (#527). Sans elle, un
            # backend lexical remplacé par un backend vectoriel ne laisse
            # aucune trace de ce qu'il fallait migrer.
            from grimoire.memory.migration import write_previous_backend_breadcrumb

            write_previous_backend_breadcrumb(plan.project_root, str(change["old"]))
        data["memory"][change["key"]] = change["new"]
        written.append(str(change["key"]))

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return written


# ── Starting the stack (explicit consent only) ─────────────────────────────────

_COMPOSE_TARGET_TEMPLATE = "docker-compose.memory-target.tpl.yml"
_COMPOSE_TARGET_FILE = "docker-compose.memory-target.yml"
_STACK_WAIT_ATTEMPTS = 20
_STACK_WAIT_DELAY_SECONDS = 1.5
_COMPOSE_UP_TIMEOUT_SECONDS = 120.0
_REDIS_START_TIMEOUT_SECONDS = 30.0


def _ensure_compose_file(project_root: Path) -> Path:
    """The project's Weaviate+Neo4j compose file, copied from the bundled
    template if `grimoire init` never wrote one — a project that started on
    `lexical`/`qdrant-local` and only later reaches for `complet` has none.

    Raises :class:`GrimoireRuntimeError` — a named refusal, never a raw
    ``OSError`` — when the bundled template cannot be read (missing file,
    permissions, a packaging path issue): this is reachable from `grimoire
    init --memory-stack up` / `grimoire memory up --start`, which must report
    a single explanatory line and exit 1, not crash on an unhandled traceback
    (#619 review).
    """
    from grimoire.core.exceptions import GrimoireRuntimeError

    dest = project_root / _COMPOSE_TARGET_FILE
    if not dest.is_file():
        from grimoire.data import framework_path

        template = framework_path() / "memory" / _COMPOSE_TARGET_TEMPLATE
        try:
            content = template.read_text(encoding="utf-8")
        except OSError as exc:
            raise GrimoireRuntimeError(
                f"gabarit de pile mémoire illisible ({template}) : {exc}"
            ) from exc
        try:
            dest.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise GrimoireRuntimeError(
                f"impossible d'écrire {dest} : {exc}"
            ) from exc
    return dest


def _wait_reachable(url: str, default_port: int, *, attempts: int = _STACK_WAIT_ATTEMPTS) -> bool:
    """Poll *url* briefly for a freshly started service to answer."""
    for _ in range(attempts):
        if _tcp_reachable(url, default_port):
            return True
        time.sleep(_STACK_WAIT_DELAY_SECONDS)
    return False


def start_memory_stack(profile: str, project_root: Path) -> list[str]:
    """Start, via Docker Compose, the services *profile* needs and this
    machine does not yet serve.

    Explicit consent only — the caller (``grimoire memory up --start``, or
    ``grimoire init --memory-stack up``) is the consent; this function never
    runs on its own and is never reached from a plain ``-y``/express call.

    Scoped to ``graphe``/``complet`` — the only compositions that pin a
    server (Weaviate + Neo4j, plus Redis for ``complet``). ``standard``
    already gets real semantic search from the embedded ``qdrant-local``
    backend (:func:`build_memory_plan`) with nothing to start.

    Returns one human-readable status line per service this call touched;
    empty when the profile needs nothing started or Docker cannot be reached.

    Raises :class:`GrimoireRuntimeError` (never a raw ``OSError``) when the
    bundled compose template cannot be read or copied
    (:func:`_ensure_compose_file`) — every other failure in this function
    (Docker unreachable, ``docker compose`` erroring out) reports as a
    returned status line instead, but a template the kit itself cannot read
    is a packaging problem the caller must surface as a refusal, not silently
    swallow into a message list. Callers (``grimoire init
    --memory-stack up``, ``grimoire memory up --start``) catch it and exit 1.
    """
    canonical = memory_profiles.resolve(profile).id
    if canonical not in ("graphe", "complet"):
        return []
    if not docker_daemon_reachable():
        return ["Docker : démon injoignable — impossible de démarrer la pile mémoire."]

    messages: list[str] = []
    probes = probe_services()
    weaviate, neo4j = probes["weaviate"], probes["neo4j"]
    if not (weaviate.reachable and neo4j.reachable):
        compose = _ensure_compose_file(project_root)
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", compose.name, "up", "-d"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=_COMPOSE_UP_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            messages.append(f"docker compose : {exc}")
            return messages
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "erreur inconnue"
            messages.append(f"docker compose a échoué : {detail}")
            return messages
        messages.append(
            "Weaviate : démarré" if _wait_reachable(weaviate.url, _DEFAULT_PORTS["weaviate"])
            else "Weaviate : Docker Compose lancé, mais le service ne répond pas encore."
        )
        messages.append(
            "Neo4j : démarré" if _wait_reachable(neo4j.url, _DEFAULT_PORTS["neo4j"])
            else "Neo4j : Docker Compose lancé, mais le service ne répond pas encore."
        )

    if canonical == "complet":
        redis = probes["redis"]
        if not redis.reachable:
            try:
                subprocess.run(
                    _START_COMMANDS["redis"].split(),
                    capture_output=True,
                    text=True,
                    timeout=_REDIS_START_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                messages.append(f"Redis : {exc}")
            else:
                messages.append(
                    "Redis : démarré" if _wait_reachable(redis.url, _DEFAULT_PORTS["redis"])
                    else "Redis : conteneur lancé, mais le service ne répond pas encore."
                )
    return messages
