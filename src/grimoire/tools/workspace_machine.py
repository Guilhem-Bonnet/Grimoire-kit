"""Flotte › Données : la machine et « qui partage quoi » (P26). Lecture seule.

Charge utile de ``GET /api/workspace/data/machine`` : les services mémoire du
poste (Weaviate, Qdrant, Neo4j, Redis, Ollama), chacun avec son état, sa
version, son conteneur Docker, le projet Compose qui le possède (lu par
``docker inspect``), sa politique de redémarrage, l'extra Python qui le rend
utilisable par le kit, et les projets du registre qui l'utilisent. Puis les
partages : une collection, une base Neo4j ou un Redis lus par plusieurs
projets.

Refus tenus ici, pas rappelés à chaque appelant :

- rien ne démarre, rien ne s'écrit : ``docker ps`` et ``docker inspect``
  seulement, des connexions TCP et des ``GET`` de version, chacun borné ;
- une sonde ratée n'est jamais un zéro : Docker absent ou injoignable rend
  « inconnu » avec son motif, jamais « arrêté » ni « absent » ;
- aucun secret ne sort : du mot de passe Neo4j on ne rend que sa présence
  (booléen), et de ``docker inspect`` on ne lit que des champs nommés — jamais
  ``Config.Env``, où vivent ``NEO4J_AUTH`` et consorts ;
- les chemins de projets viennent du registre de la machine, jamais de la
  requête.

Les appels au système passent par :class:`MachineProbes`, injectable : les
tests rejouent Docker, le réseau et l'environnement sans rien de tout ça.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from grimoire.tools.memory_setup import _DEFAULT_PORTS, _DEFAULT_URLS, _EXTRA_MODULES, _START_COMMANDS, service_state

__all__ = ["MACHINE_SCHEMA_VERSION", "STATES", "MachineProbes", "machine_view"]

MACHINE_SCHEMA_VERSION = "grimoire-data-machine/v1"

#: Le vocabulaire d'état, dans l'ordre où la carte le lit. Chaque mot est
#: distinct : « inconnu » n'est jamais un « arrêté » faute de mieux.
STATES: tuple[tuple[str, str], ...] = (
    ("en_ligne", "En ligne"),
    ("demarrage", "Démarrage"),
    ("incomplet", "Incomplet"),
    ("emprunte", "Emprunté"),
    ("en_echec", "En échec"),
    ("arrete", "Arrêté"),
    ("absent", "Absent"),
    ("inconnu", "Inconnu"),
)
_STATE_LABEL = dict(STATES)

#: Délais des sondes : un service qui ne répond pas ne retient jamais la page.
TCP_TIMEOUT = 0.5
HTTP_TIMEOUT = 1.0
DOCKER_TIMEOUT = 3.0

_PASSWORD_ENV = "GRIMOIRE_NEO4J_PASSWORD"  # noqa: S105 - nom de variable, pas un secret
_NEO4J_HTTP_PORT = 7474
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})  # noqa: S104 - comparaison, aucun bind
#: Codes de sortie d'un conteneur arrêté proprement (``docker stop`` : SIGTERM, puis SIGKILL).
_CLEAN_EXIT_CODES = frozenset({0, 137, 143})

_KINDS: dict[str, dict[str, Any]] = {
    "weaviate": {"label": "Weaviate", "role": "store sémantique", "images": ("weaviate",)},
    "qdrant": {"label": "Qdrant", "role": "store sémantique", "images": ("qdrant",)},
    "neo4j": {"label": "Neo4j", "role": "graphe", "images": ("neo4j",)},
    "redis": {"label": "Redis", "role": "mémoire chaude", "images": ("redis",)},
    "ollama": {"label": "Ollama", "role": "embeddings", "images": ("ollama",)},
}


# ── Sondes injectables ─────────────────────────────────────────────────────────


def _run_docker(argv: list[str]) -> tuple[int, str, str]:
    """``docker …`` borné ; une panne devient un code et un motif, jamais une exception."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=DOCKER_TIMEOUT, check=False)
    except FileNotFoundError:
        return 127, "", "commande docker introuvable sur ce poste"
    except subprocess.TimeoutExpired:
        return 124, "", f"Docker n'a pas répondu en {DOCKER_TIMEOUT:g} s"
    except OSError as exc:
        return 126, "", str(exc)
    return done.returncode, done.stdout or "", done.stderr or ""


def _http_json(url: str) -> dict[str, Any] | None:
    """``GET`` JSON borné, http(s) seulement ; ``None`` sur tout échec."""
    if urlparse(url).scheme not in ("http", "https"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 - schéma vérifié ci-dessus
            data = json.loads(resp.read(65536).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _redis_version(host: str, port: int) -> tuple[str | None, str | None]:
    """``INFO server`` sur une socket brute : la version, ou le motif de son absence."""
    try:
        with socket.create_connection((host, port), timeout=HTTP_TIMEOUT) as sock:
            sock.sendall(b"INFO server\r\n")
            raw = sock.recv(8192).decode("utf-8", "replace")
    except OSError as exc:
        return None, f"version illisible : {exc}"
    if raw.startswith("-NOAUTH"):
        return None, "version illisible : Redis exige une authentification"
    for line in raw.splitlines():
        if line.startswith("redis_version:"):
            return line.split(":", 1)[1].strip(), None
    return None, "version illisible : réponse INFO inattendue"


def _version(kind: str, scheme: str, host: str, port: int) -> tuple[str | None, str | None]:
    """Version annoncée par le service lui-même, ou le motif de son silence."""
    if kind == "redis":
        return _redis_version(host, port)
    base = f"{scheme if scheme in ('http', 'https') else 'http'}://{host}:{port}"
    url, key = {
        "weaviate": (f"{base}/v1/meta", "version"),
        "qdrant": (f"{base}/", "version"),
        "ollama": (f"{base}/api/version", "version"),
        "neo4j": (f"http://{host}:{_NEO4J_HTTP_PORT}/", "neo4j_version"),
    }[kind]
    data = _http_json(url)
    value = data.get(key) if data else None
    if isinstance(value, str) and value:
        return value, None
    return None, f"version illisible : {url} n'a rien rendu d'exploitable"


def _module_installed(names: tuple[str, ...]) -> bool:
    from grimoire.tools.memory_setup import _module_installed as installed

    return installed(names)


@dataclass
class MachineProbes:
    """Tout ce que la carte demande au système, remplaçable un par un en test."""

    docker: Callable[[list[str]], tuple[int, str, str]] = _run_docker
    tcp: Callable[[str, str], str] = field(default=lambda kind, url: service_state(kind, url, timeout=TCP_TIMEOUT))
    version: Callable[[str, str, str, int], tuple[str | None, str | None]] = _version
    which: Callable[[str], str | None] = shutil.which
    module_installed: Callable[[tuple[str, ...]], bool] = _module_installed
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)


# ── Projets du registre et ce qu'ils utilisent ─────────────────────────────────


def _endpoint(kind: str, url: str) -> tuple[str, str, int]:
    """``(schéma, hôte, port)`` d'une URL de service ; hôte local normalisé en ``localhost``."""
    raw = url if "//" in url else f"//{url}"
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or "localhost"
        port = parsed.port or _DEFAULT_PORTS[kind]
    except ValueError:
        return "", "localhost", _DEFAULT_PORTS[kind]
    return parsed.scheme, ("localhost" if host in _LOCAL_HOSTS else host), port


def _redis_db(url: str) -> int:
    path = urlparse(url if "//" in url else f"//{url}").path.strip("/")
    return int(path) if path.isdigit() else 0


def _project_entries(project_root: Path) -> list[dict[str, str]]:
    """Le projet servi puis le registre, chaque dossier une seule fois."""
    from grimoire.tools.project_registry import load_registry, slug_for_path

    seen: set[str] = set()
    entries: list[dict[str, str]] = []
    served = project_root.resolve()
    for entry in [{"slug": slug_for_path(project_root) or "", "name": "", "path": str(served)}, *load_registry()]:
        raw = str(entry.get("path", "") or "")
        if not raw:
            continue
        key = str(Path(raw).expanduser().resolve())
        if key in seen:
            continue
        seen.add(key)
        entries.append({"slug": str(entry.get("slug", "") or ""), "name": str(entry.get("name", "") or ""), "path": key})
    return entries


def _uses(cfg: Any, root: Path) -> dict[str, dict[str, Any]]:
    """Ce que la config d'un projet demande à chaque service, sans rien ouvrir."""
    from grimoire.memory.manager import _redis_namespace, store_identity

    mem = cfg.memory
    uses: dict[str, dict[str, Any]] = {}
    location, collection = store_identity(cfg, root)
    backend = mem.backend
    if backend == "weaviate-server":
        url = mem.weaviate_url or _DEFAULT_URLS["weaviate"]
        uses["weaviate"] = {"url": url, "collection": collection}
    elif backend in ("qdrant-server", "ollama") and location.startswith(("http://", "https://")):
        uses["qdrant"] = {"url": location, "collection": collection}
    elif backend in ("qdrant-local", "ollama"):
        uses["embarque"] = {"collection": collection}
    if backend == "ollama":
        uses["ollama"] = {"url": mem.ollama_url or _DEFAULT_URLS["ollama"]}
    graph_layers = (mem.knowledge_graph, mem.memory_graph, mem.code_graph, mem.task_memory)
    if mem.neo4j_uri and "neo4j" in graph_layers:
        uses["neo4j"] = {"url": mem.neo4j_uri, "database": mem.neo4j_database, "passwordEnv": mem.neo4j_password_env}
    if mem.short_term_backend == "redis" and mem.redis_url:
        uses["redis"] = {"url": mem.redis_url, "db": _redis_db(mem.redis_url), "namespace": _redis_namespace(cfg)}
    return uses


def _project_row(entry: dict[str, str]) -> dict[str, Any]:
    root = Path(entry["path"])
    row: dict[str, Any] = {
        "slug": entry["slug"], "name": entry["name"] or root.name, "path": entry["path"],
        "backend": None, "uses": {}, "reason": None,
    }
    config_path = root / "project-context.yaml"
    if not config_path.is_file():
        row["reason"] = "projet non initialisé (pas de project-context.yaml)" if root.is_dir() else "dossier introuvable"
        return row
    try:
        from grimoire.core.config import GrimoireConfig

        cfg = GrimoireConfig.from_yaml(config_path)
        row["name"] = entry["name"] or cfg.project.name or root.name
        row["backend"] = cfg.memory.backend
        row["uses"] = _uses(cfg, root)
    except Exception as exc:  # une config cassée nomme sa panne, elle ne casse pas la carte
        row["reason"] = f"config illisible : {exc}"
    return row


# ── Docker : conteneurs et propriétaires ──────────────────────────────────────


@dataclass
class _Docker:
    reachable: bool
    reason: str | None
    containers: list[dict[str, Any]]


def _docker_listing(probes: MachineProbes) -> _Docker:
    code, out, err = probes.docker(["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"])
    if code != 0:
        first = (err or out).strip().splitlines()
        return _Docker(False, f"Docker injoignable : {first[0] if first else f'code {code}'}", [])
    containers = []
    for line in out.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            containers.append(data)
    return _Docker(True, None, containers)


def _published(ports: str) -> set[int]:
    """Ports hôte publiés, lus dans la colonne ``Ports`` de ``docker ps``."""
    found: set[int] = set()
    for chunk in str(ports or "").split(","):
        left = chunk.split("->", 1)[0]
        if "->" not in chunk or ":" not in left:
            continue
        low, _, high = left.rsplit(":", 1)[1].partition("-")
        if low.isdigit() and (not high or high.isdigit()):
            found.update(range(int(low), int(high or low) + 1))
    return found


def _image_matches(kind: str, image: str) -> bool:
    repo = str(image or "").rsplit("@", 1)[0]
    name = repo.rsplit("/", 1)[-1].split(":", 1)[0]
    return name in _KINDS[kind]["images"]


def _bindings(inspected: dict[str, Any]) -> set[int]:
    host_config = inspected.get("HostConfig") or {}
    found = set()
    for bindings in (host_config.get("PortBindings") or {}).values():
        for binding in bindings or []:
            port = str((binding or {}).get("HostPort", ""))
            if port.isdigit():
                found.add(int(port))
    return found


def _container_view(inspected: dict[str, Any], kind: str) -> dict[str, Any]:
    """Les champs nommés d'un ``docker inspect`` — jamais ``Config.Env``."""
    config = inspected.get("Config") or {}
    labels = config.get("Labels") or {}
    state = inspected.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    restart = (inspected.get("HostConfig") or {}).get("RestartPolicy") or {}
    bindings = _bindings(inspected)
    console_port = _NEO4J_HTTP_PORT
    if kind == "neo4j":
        for container_port, rows in ((inspected.get("HostConfig") or {}).get("PortBindings") or {}).items():
            if str(container_port).startswith(f"{_NEO4J_HTTP_PORT}/"):
                host_port = str(((rows or [{}])[0] or {}).get("HostPort", ""))
                console_port = int(host_port) if host_port.isdigit() else console_port
    compose_project = labels.get("com.docker.compose.project")
    return {
        "name": str(inspected.get("Name", "")).lstrip("/"),
        "image": config.get("Image"),
        "status": state.get("Status"),
        "exitCode": state.get("ExitCode"),
        "health": health,
        "restartPolicy": restart.get("Name") or "no",
        "compose": {
            "project": compose_project,
            "service": labels.get("com.docker.compose.service"),
            "workingDir": labels.get("com.docker.compose.project.working_dir"),
        } if compose_project else None,
        "volumes": [
            {"type": m.get("Type"), "name": m.get("Name") or m.get("Source"), "destination": m.get("Destination")}
            for m in inspected.get("Mounts") or [] if isinstance(m, dict)
        ],
        "ports": sorted(bindings),
        "_consolePort": console_port,
    }


def _inspect(probes: MachineProbes, names: list[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    if not names:
        return {}, None
    code, out, err = probes.docker(["docker", "inspect", *names])
    if code != 0 and not out.strip():
        first = err.strip().splitlines()
        return {}, f"docker inspect a échoué : {first[0] if first else f'code {code}'}"
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return {}, "docker inspect : réponse illisible"
    return {str(r.get("Name", "")).lstrip("/"): r for r in rows if isinstance(r, dict)}, None


# ── État d'un service ─────────────────────────────────────────────────────────


def _within(path: str | None, roots: list[str]) -> bool:
    if not path:
        return False
    target = Path(path).expanduser().resolve()
    return any(target == Path(r) or target.is_relative_to(Path(r)) for r in roots)


def _owner(container: dict[str, Any] | None, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Le projet du registre dont le dossier porte le Compose du conteneur, s'il y en a un."""
    compose = (container or {}).get("compose") or {}
    working_dir = compose.get("workingDir")
    if not working_dir:
        return None
    for project in projects:
        if _within(working_dir, [project["path"]]):
            return {"slug": project["slug"], "name": project["name"]}
    return {"slug": None, "name": compose.get("project") or Path(working_dir).name}


def _decide(service: dict[str, Any], tcp: str, docker: _Docker, probes: MachineProbes,
            consumers: list[dict[str, Any]]) -> tuple[str, str]:
    """``(état, motif)`` — un seul état par service, dans l'ordre de gravité qui compte pour agir."""
    kind, container = service["kind"], service["container"]
    if tcp == "inconnu":
        return "inconnu", f"la sonde n'a pas eu de réponse en {TCP_TIMEOUT:g} s : impossible de conclure"
    if tcp == "en_echec":
        return "en_echec", f"connexion impossible à {service['host']}:{service['port']} (erreur réseau)"
    if tcp == "en_ligne":
        if container and container.get("health") == "unhealthy":
            return "en_echec", "le service répond mais son conteneur est déclaré malade par son healthcheck"
        if container and (container.get("health") == "starting" or container.get("status") == "restarting"):
            return "demarrage", "le conteneur démarre : son healthcheck n'a pas encore conclu"
        compose = (container or {}).get("compose") or {}
        roots = [c["path"] for c in consumers]
        if consumers and compose.get("workingDir") and not _within(compose["workingDir"], roots):
            owner = service.get("owner") or {}
            return "emprunte", (
                f"utilisé par {', '.join(c['name'] for c in consumers)}, mais le conteneur appartient au "
                f"Compose « {compose.get('project')} » ({owner.get('name') or compose['workingDir']})"
            )
        missing = []
        if consumers and not service["extra"]["installed"]:
            missing.append(f"l'extra Python « {service['extra']['name']} » n'est pas importable par le cockpit")
        if kind == "neo4j" and consumers and not service.get("passwordPresent", True):
            missing.append(f"{_PASSWORD_ENV} absente de l'environnement du cockpit")
        if missing:
            return "incomplet", "le service répond, mais " + " ; ".join(missing)
        if not docker.reachable and kind != "ollama":
            return "en_ligne", f"le service répond ; conteneur et propriétaire non lus ({docker.reason})"
        return "en_ligne", "le service répond"
    # tcp == "arrete" : rien n'écoute sur le port.
    if service["host"] != "localhost":
        return "inconnu", "hôte distant : rien n'écoute, mais le cockpit ne voit pas ses conteneurs"
    if container:
        if container.get("status") in ("running", "restarting"):
            return "demarrage", "le conteneur tourne mais le port ne répond pas encore"
        code = container.get("exitCode")
        if container.get("status") == "dead" or (isinstance(code, int) and code not in _CLEAN_EXIT_CODES):
            return "en_echec", f"le conteneur s'est arrêté en erreur (code de sortie {code})"
        return "arrete", f"le conteneur « {container['name']} » existe mais est arrêté"
    if kind == "ollama" and probes.which("ollama"):
        return "arrete", "ollama est installé sur ce poste mais ne tourne pas"
    if not docker.reachable:
        why = docker.reason or "Docker injoignable"
        return "inconnu", f"rien n'écoute, et {why[0].lower() + why[1:]} : impossible de dire si un conteneur existe"
    return "absent", "aucun service n'écoute et aucun conteneur de cette image n'existe"


def _console(service: dict[str, Any]) -> dict[str, str]:
    kind, host, port = service["kind"], service["host"], service["port"]
    scheme = service["scheme"] if service["scheme"] in ("http", "https") else "http"
    if kind == "weaviate":
        return {"label": "Console Weaviate (API)", "url": f"{scheme}://{host}:{port}/v1/meta"}
    if kind == "qdrant":
        return {"label": "Tableau de bord Qdrant", "url": f"{scheme}://{host}:{port}/dashboard"}
    if kind == "neo4j":
        http_port = (service["container"] or {}).get("_consolePort", _NEO4J_HTTP_PORT)
        return {"label": f"Neo4j Browser :{http_port}", "url": f"http://{host}:{http_port}/browser/"}
    if kind == "redis":
        return {"label": "Client Redis (redis://)", "url": f"redis://{host}:{port}"}
    return {"label": "API Ollama (modèles)", "url": f"{scheme}://{host}:{port}/api/tags"}


def _console_command(service: dict[str, Any]) -> str:
    kind, host, port = service["kind"], service["host"], service["port"]
    return {
        "weaviate": f"curl -s http://{host}:{port}/v1/meta",
        "qdrant": f"curl -s http://{host}:{port}/collections",
        "neo4j": f"cypher-shell -a bolt://{host}:{port} -u neo4j",
        "redis": f"redis-cli -h {host} -p {port}",
        "ollama": "ollama list",
    }[kind]


def _command(service: dict[str, Any], state: str) -> dict[str, str]:
    """La commande à copier — jamais lancée par le cockpit (le bouton Démarrer attend ADR-008)."""
    container = service["container"] or {}
    name = container.get("name")
    if state in ("arrete", "absent"):
        if name:
            return {"label": "Démarrer le conteneur existant", "text": f"docker start {name}"}
        return {"label": "Démarrer le service", "text": _START_COMMANDS[service["kind"]]}
    if state == "demarrage" and name:
        return {"label": "Suivre le démarrage", "text": f"docker logs -f {name}"}
    if state == "en_echec" and name:
        return {"label": "Lire le journal du conteneur", "text": f"docker logs --tail 50 {name}"}
    if state == "incomplet":
        if not service["extra"]["installed"]:
            from grimoire.tools.memory_setup import extras_install_hint

            return {"label": "Installer l'extra Python", "text": extras_install_hint([service["extra"]["name"]])}
        return {"label": "Exporter le mot de passe Neo4j", "text": f"export {_PASSWORD_ENV}=<mot de passe>"}
    if state == "emprunte" and name:
        return {
            "label": "Voir à quel projet appartient le conteneur",
            "text": f"docker inspect --format '{{{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}}}' {name}",
        }
    if state == "inconnu" and "docker" in service.get("reason", "").lower():
        return {"label": "Vérifier Docker", "text": "docker info"}
    return {"label": "Ouvrir la console en ligne de commande", "text": _console_command(service)}


# ── Assemblage ───────────────────────────────────────────────────────────────


def _service_skeleton(kind: str, url: str) -> dict[str, Any]:
    scheme, host, port = _endpoint(kind, url)
    extra, modules = _EXTRA_MODULES[kind]
    return {
        "id": f"{kind}@{host}:{port}", "kind": kind, "label": _KINDS[kind]["label"], "role": _KINDS[kind]["role"],
        "url": url, "scheme": scheme, "host": host, "port": port,
        "extra": {"name": extra, "modules": list(modules), "installed": False},
        "container": None, "owner": None, "consumers": [], "shares": [],
        "version": None, "versionSource": None, "versionReason": None,
    }


def _match_containers(services: list[dict[str, Any]], docker: _Docker, probes: MachineProbes) -> str | None:
    """Rattache à chaque service local le conteneur qui publie son port (``docker inspect``)."""
    if not docker.reachable:
        return None
    wanted: dict[str, list[str]] = {}
    for service in services:
        if service["host"] != "localhost":
            continue
        for row in docker.containers:
            name = str(row.get("Names", "")).split(",")[0]
            running = row.get("State") == "running"
            if (running and service["port"] in _published(str(row.get("Ports", "")))) or (
                not running and _image_matches(service["kind"], str(row.get("Image", "")))
            ):
                wanted.setdefault(service["id"], []).append(name)
    names = sorted({n for group in wanted.values() for n in group})
    inspected, reason = _inspect(probes, names)
    for service in services:
        for name in wanted.get(service["id"], []):
            data = inspected.get(name)
            if data and service["port"] in _bindings(data):
                service["container"] = _container_view(data, service["kind"])
                break
    return reason


def _probe_service(service: dict[str, Any], probes: MachineProbes) -> str:
    tcp = probes.tcp(service["kind"], service["url"])
    if tcp == "en_ligne":
        version, reason = probes.version(service["kind"], service["scheme"], service["host"], service["port"])
        service["version"], service["versionReason"] = version, reason
        service["versionSource"] = "service" if version else None
    return tcp


def _image_version(service: dict[str, Any]) -> None:
    image = str((service["container"] or {}).get("image") or "")
    tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ""
    if not service["version"] and tag and tag != "latest":
        service["version"], service["versionSource"] = tag.lstrip("v"), "image"


def _shares(projects: list[dict[str, Any]], services: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Ce que plusieurs projets lisent ensemble : collection, base Neo4j, Redis."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for project in projects:
        for kind, use in project["uses"].items():
            if kind == "embarque":
                continue
            _, host, port = _endpoint(kind, use["url"])
            sid = f"{kind}@{host}:{port}"
            if kind in ("weaviate", "qdrant"):
                key: tuple[str, ...] = ("collection", sid, str(use["collection"]))
            elif kind == "neo4j":
                key = ("base", sid, str(use["database"]))
            elif kind == "redis":
                key = ("redis", sid, str(use["db"]))
            else:
                continue
            groups.setdefault(key, []).append({**use, "slug": project["slug"], "name": project["name"]})
    shares = []
    for (what, sid, target), users in sorted(groups.items()):
        if len(users) < 2:
            continue
        share: dict[str, Any] = {
            "kind": what, "service": sid, "target": target,
            "projects": [{"slug": u["slug"], "name": u["name"]} for u in users],
        }
        if what == "redis":
            namespaces = [str(u["namespace"]) for u in users]
            share["namespaces"] = namespaces
            share["collision"] = len(set(namespaces)) < len(namespaces)
        shares.append(share)
        if sid in services:
            services[sid]["shares"].append(share)
    return shares


def machine_view(project_root: Path, *, probes: MachineProbes | None = None) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/data/machine`` — une mesure fraîche, sans rien démarrer."""
    probes = probes or MachineProbes()
    projects = [_project_row(e) for e in _project_entries(project_root)]

    services: dict[str, dict[str, Any]] = {}
    for kind, url in _DEFAULT_URLS.items():
        skeleton = _service_skeleton(kind, url)
        services[skeleton["id"]] = skeleton
    for project in projects:
        for kind, use in project["uses"].items():
            if kind == "embarque":
                continue
            skeleton = _service_skeleton(kind, use["url"])
            service = services.setdefault(skeleton["id"], skeleton)
            service["consumers"].append({"slug": project["slug"], "name": project["name"], "path": project["path"]})

    ordered = list(services.values())
    with ThreadPoolExecutor(max_workers=len(ordered) + 1, thread_name_prefix="grimoire-machine") as pool:
        docker_future = pool.submit(_docker_listing, probes)
        tcp_futures = [pool.submit(_probe_service, s, probes) for s in ordered]
        docker = docker_future.result()
        tcp_states = [f.result() for f in tcp_futures]
    inspect_reason = _match_containers(ordered, docker, probes)

    envs: dict[str, list[str]] = {_PASSWORD_ENV: []}
    for project in projects:
        neo4j = project["uses"].get("neo4j")
        if neo4j:
            envs.setdefault(str(neo4j["passwordEnv"] or _PASSWORD_ENV), []).append(project["name"])
    password_envs = [{"env": name, "present": bool(probes.env.get(name)), "projects": users} for name, users in envs.items()]

    extras = []
    for kind, (extra, modules) in _EXTRA_MODULES.items():
        installed = bool(probes.module_installed(modules))
        extras.append({"kind": kind, "extra": extra, "modules": list(modules), "installed": installed})
    installed_by_kind = {e["kind"]: e["installed"] for e in extras}

    shares = _shares(projects, services)
    counts = dict.fromkeys(_STATE_LABEL, 0)
    for service, tcp in zip(ordered, tcp_states, strict=True):
        service["extra"]["installed"] = installed_by_kind[service["kind"]]
        if service["kind"] == "neo4j":
            env_names = {str(c_use["passwordEnv"] or _PASSWORD_ENV) for p in projects
                         if (c_use := p["uses"].get("neo4j")) and any(c["path"] == p["path"] for c in service["consumers"])}
            service["passwordPresent"] = all(bool(probes.env.get(n)) for n in env_names or {_PASSWORD_ENV})
        service["owner"] = _owner(service["container"], projects)
        _image_version(service)
        state, reason = _decide(service, tcp, docker, probes, service["consumers"])
        service["state"], service["stateLabel"], service["reason"] = state, _STATE_LABEL[state], reason
        service["console"] = _console(service)
        service["command"] = _command(service, state)
        if service["container"]:
            service["container"].pop("_consolePort", None)
        for consumer in service["consumers"]:
            consumer.pop("path", None)
        counts[state] += 1

    return {
        "schemaVersion": MACHINE_SCHEMA_VERSION,
        "docker": {"reachable": docker.reachable, "reason": docker.reason or inspect_reason,
                   "containers": len(docker.containers)},
        "neo4jPassword": {"env": _PASSWORD_ENV, "present": bool(probes.env.get(_PASSWORD_ENV))},
        "neo4jPasswordEnvs": password_envs,
        "extras": extras,
        "services": ordered,
        "projects": projects,
        "shares": shares,
        "states": [{"id": sid, "label": label} for sid, label in STATES],
        "summary": {"services": len(ordered), "byState": counts, "shares": len(shares)},
    }
