"""Premier lancement en lecture seule (P36) : ce que le guide du cockpit détecte.

Charge utile de ``GET /api/workspace/setup/guide``. L'état du projet (S0 à S3)
ne vient pas d'ici : la page le déduit elle-même des quatre lectures qui
existent déjà (``/api/health``, ``/api/memory/status`` sans sonde,
``/api/setup/run``, ``/api/status``). Ce module apporte ce qu'aucune d'elles
ne dit : la machine.

- **Détection** : Python, outil d'installation, Docker (le démon, pas
  seulement le binaire), moteur d'embedding local, réseau sortant (jamais
  sondé : une sonde mentirait derrière un proxy, comme le dit déjà
  ``grimoire init``), services mémoire déjà en ligne, hôtes d'agents, projets
  enregistrés.
- **Profils mémoire** : les quatre compositions de
  :mod:`grimoire.memory.profiles`, chacune possible ou non sur cette machine,
  avec sa raison. Un profil qu'on ne peut pas évaluer (Docker illisible) est
  « inconnu », jamais « impossible ».
- **Plan** : ce que les commandes proposées écriraient, les services qu'elles
  démarreraient, ceux qui seraient partagés avec d'autres projets, les extras
  Python à installer soi-même, et les commandes à copier.

Refus tenus ici : rien ne s'écrit, rien ne démarre. Les sondes sont celles de
``grimoire memory up`` (connexions TCP bornées, ``docker info``), passées par
:class:`GuideProbes` pour que les tests les rejouent sans réseau ni Docker.
"""

from __future__ import annotations

import platform
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.memory import profiles as memory_profiles
from grimoire.tools import memory_setup
from grimoire.tools.project_registry import load_registry

__all__ = ["SETUP_GUIDE_SCHEMA_VERSION", "GuideProbes", "setup_guide"]

SETUP_GUIDE_SCHEMA_VERSION = "grimoire-setup-guide/v1"

#: États d'une ligne de détection : le mot reste toujours écrit à côté.
OK, WARN, MISSING, UNKNOWN = "ok", "warn", "missing", "unknown"

_SERVICE_LABELS = {
    "weaviate": "Weaviate", "qdrant": "Qdrant", "neo4j": "Neo4j", "redis": "Redis", "ollama": "Ollama",
}

#: Services qu'un profil démarre s'ils ne répondent pas (``start_memory_stack``).
_PROFILE_SERVICES: dict[str, tuple[str, ...]] = {
    "lexical": (),
    "standard": (),
    "graphe": ("weaviate", "neo4j"),
    "complet": ("weaviate", "neo4j", "redis"),
}

#: Extras Python dont un profil a besoin pour servir ses couches.
_PROFILE_EXTRAS: dict[str, tuple[str, ...]] = {
    "lexical": (),
    "standard": ("qdrant",),
    "graphe": ("weaviate", "neo4j"),
    "complet": ("weaviate", "neo4j", "redis"),
}

#: Hôtes d'agents reconnus par leur binaire sur le PATH du cockpit.
_HOSTS = (("claude", "Claude Code"), ("code", "VS Code (Copilot)"), ("cursor", "Cursor"))


@dataclass(frozen=True)
class GuideProbes:
    """Les appels au système, injectables : les tests rejouent la machine."""

    which: Callable[[str], str | None] = shutil.which
    docker_daemon: Callable[[], bool] = memory_setup.docker_daemon_reachable
    services: Callable[[], dict[str, memory_setup.ServiceProbe]] = memory_setup.probe_services
    embedding: Callable[[dict[str, memory_setup.ServiceProbe]], bool] = memory_setup.local_embedding_available
    registry: Callable[[], list[dict[str, str]]] = load_registry
    python_version: Callable[[], str] = platform.python_version
    extras_hint: Callable[[list[str]], str] = memory_setup.extras_install_hint


@dataclass
class _Machine:
    """Ce que les sondes ont rendu ; ``None`` = lecture impossible, avec sa raison."""

    docker_cli: bool | None = None
    docker_daemon: bool | None = None
    services: dict[str, memory_setup.ServiceProbe] | None = None
    embedding: bool | None = None
    errors: dict[str, str] = field(default_factory=dict)


def _attempt(machine: _Machine, key: str, call: Callable[[], Any]) -> Any:
    """Exécute une sonde ; une exception devient une raison, jamais un faux non."""
    try:
        return call()
    except Exception as exc:  # une sonde ne doit jamais faire tomber le guide
        machine.errors[key] = f"{type(exc).__name__} : {exc}"
        return None


def _probe(probes: GuideProbes) -> _Machine:
    machine = _Machine()
    machine.docker_cli = _attempt(machine, "docker", lambda: probes.which("docker") is not None)
    if machine.docker_cli:
        machine.docker_daemon = _attempt(machine, "docker", probes.docker_daemon)
    elif machine.docker_cli is False:
        machine.docker_daemon = False
    machine.services = _attempt(machine, "services", probes.services)
    if machine.services is not None:
        services = machine.services
        machine.embedding = _attempt(machine, "embedding", lambda: probes.embedding(services))
    return machine


def _check(check_id: str, label: str, state: str, word: str, detail: str, remedy: str = "") -> dict[str, str]:
    return {"id": check_id, "label": label, "state": state, "word": word, "detail": detail, "remedy": remedy}


def _docker_check(machine: _Machine) -> dict[str, str]:
    if machine.docker_daemon is True:
        return _check("docker", "Docker", OK, "démon joignable", "docker info répond.")
    if machine.docker_cli is None or (machine.docker_cli and machine.docker_daemon is None):
        reason = machine.errors.get("docker", "sonde non aboutie")
        return _check("docker", "Docker", UNKNOWN, "inconnu", f"Lecture impossible : {reason}.")
    if machine.docker_cli:
        return _check(
            "docker", "Docker", WARN, "démon muet",
            "Le binaire docker est là, mais docker info ne répond pas.",
            "Lance Docker Desktop ou le service docker, puis relis la machine.",
        )
    return _check(
        "docker", "Docker", MISSING, "absent", "docker introuvable sur le PATH du cockpit.",
        "Sans Docker, les profils graphe et complet restent hors de portée.",
    )


def _embedding_check(machine: _Machine) -> dict[str, str]:
    if machine.embedding is None:
        reason = machine.errors.get("embedding") or machine.errors.get("services", "sonde non aboutie")
        return _check("embedding", "Embeddings locaux", UNKNOWN, "inconnu", f"Lecture impossible : {reason}.")
    if machine.embedding:
        return _check(
            "embedding", "Embeddings locaux", OK, "disponibles",
            "fastembed, sentence-transformers ou un Ollama joignable calcule les vecteurs sans service distant.",
        )
    return _check(
        "embedding", "Embeddings locaux", MISSING, "aucun moteur",
        "Ni fastembed, ni sentence-transformers, ni Ollama : la recherche reste lexicale.",
        "Installe l'extra du profil choisi (voir le plan) pour la recherche sémantique.",
    )


def _services_check(machine: _Machine) -> dict[str, str]:
    if machine.services is None:
        reason = machine.errors.get("services", "sonde non aboutie")
        return _check("services", "Services mémoire en ligne", UNKNOWN, "inconnu", f"Lecture impossible : {reason}.")
    online = [_SERVICE_LABELS.get(sid, sid) for sid, probe in machine.services.items() if probe.reachable]
    if not online:
        return _check(
            "services", "Services mémoire en ligne", MISSING, "aucun",
            "Aucun service mémoire ne répond sur localhost (Weaviate, Qdrant, Neo4j, Redis, Ollama).",
        )
    return _check(
        "services", "Services mémoire en ligne", OK, ", ".join(online),
        "Déjà en ligne sur ce poste : un profil qui en a besoin les réutilise au lieu d'en démarrer d'autres.",
    )


def _installer_check(probes: GuideProbes) -> dict[str, str]:
    found = [name for name in ("uv", "pipx") if probes.which(name)]
    if found:
        return _check("installer", "Outil d'installation", OK, " et ".join(found), "Sert à installer et mettre à jour le kit.")
    return _check(
        "installer", "Outil d'installation", WARN, "ni uv ni pipx",
        "Le kit tourne, mais sa mise à jour passera par pip dans son environnement.",
        "uv installe Python et le kit en une commande.",
    )


def _hosts_check(probes: GuideProbes) -> dict[str, str]:
    found = [label for binary, label in _HOSTS if probes.which(binary)]
    if found:
        return _check("hosts", "Hôtes d'agents", OK, ", ".join(found), "Trouvés sur le PATH du cockpit.")
    return _check(
        "hosts", "Hôtes d'agents", WARN, "aucun trouvé",
        "Ni claude, ni code, ni cursor sur le PATH du cockpit : un hôte installé ailleurs n'est pas vu ici.",
    )


def _registry_check(probes: GuideProbes) -> dict[str, str]:
    try:
        entries = probes.registry()
    except Exception as exc:  # registre illisible : le dire
        return _check("projects", "Projets enregistrés", UNKNOWN, "inconnu", f"Registre illisible : {exc}.")
    count = len(entries)
    word = "aucun" if count == 0 else (f"{count} projet" if count == 1 else f"{count} projets")
    return _check("projects", "Projets enregistrés", OK if count else WARN, word, "Le registre du cockpit sur ce poste.")


def _capabilities(machine: _Machine) -> tuple[frozenset[str], set[str]]:
    """Jetons offerts aux profils, et ceux qu'on n'a pas pu évaluer.

    Le réseau sortant est supposé : on ne le sonde pas (``grimoire init`` le
    demande au lieu de le sonder). Redis est offert s'il répond, ou si Docker
    peut le démarrer (``memory up --start``).
    """
    tokens = {memory_profiles.REQ_EGRESS}
    unknown: set[str] = set()
    if machine.docker_daemon is True:
        tokens.add(memory_profiles.REQ_DOCKER)
    elif machine.docker_daemon is None:
        unknown.add(memory_profiles.REQ_DOCKER)
    redis = machine.services.get("redis") if machine.services else None
    if (redis is not None and redis.reachable) or machine.docker_daemon is True:
        tokens.add(memory_profiles.REQ_REDIS)
    elif machine.services is None or machine.docker_daemon is None:
        unknown.add(memory_profiles.REQ_REDIS)
    return frozenset(tokens), unknown


def _unmet_reason(token: str, machine: _Machine) -> str:
    if token == memory_profiles.REQ_DOCKER:
        if machine.docker_cli:
            return "demande Docker : le démon ne répond pas"
        return "demande Docker : introuvable sur ce poste"
    if token == memory_profiles.REQ_REDIS:
        return "demande un Redis joignable sur localhost:6379, ou Docker pour le démarrer"
    return f"demande {token}"


def _recommend(machine: _Machine, possible: dict[str, bool | None]) -> tuple[str, str]:
    """Le profil le plus riche que la machine tient — même ordre que ``recommend_profile``."""
    if machine.embedding is False:
        return "lexical", "aucun moteur d'embedding local : un profil vectoriel construirait un index vide"
    if machine.embedding is True and possible.get("complet") is True:
        return "complet", "Docker répond : le profil le plus complet que ce poste peut servir"
    if possible.get("standard") is not False:
        return "standard", "vecteurs locaux (Qdrant embarqué), sans Docker ni serveur"
    return "lexical", "le seul profil sans prérequis"


def _profile_commands(profile_id: str, start: list[str]) -> dict[str, list[dict[str, str]]]:
    memory_apply = f"grimoire memory up --profile {profile_id}" + (" --start" if start else "") + " --apply"
    return {
        "init": [
            {"command": f"grimoire init . -y --memory-profile {profile_id}",
             "why": "Équipe ce dossier : project-context.yaml, _grimoire/, fichiers des hôtes"
                    + (", et démarre les services manquants" if start else "") + "."},
            {"command": "grimoire up .", "why": "Termine la mise en place : identité, standard agentique, hôtes, doctor."},
            {"command": "grimoire doctor .", "why": "Vérifie l'installation, sans rien modifier."},
        ],
        "memory": [
            {"command": f"grimoire memory up --profile {profile_id}", "why": "Montre le plan mémoire, n'écrit rien."},
            {"command": memory_apply,
             "why": "Écrit le bloc memory: de project-context.yaml"
                    + (" après avoir démarré les services manquants." if start else ".")},
        ],
    }


def _extras_hint(machine: _Machine, probes: GuideProbes, extras: list[str]) -> str:
    """La commande d'installation des extras manquants, selon l'installeur du kit."""
    if not extras:
        return ""
    return str(_attempt(machine, "extras", lambda: probes.extras_hint(extras)) or ", ".join(extras))


def _profiles(machine: _Machine, probes: GuideProbes) -> tuple[list[dict[str, Any]], dict[str, str]]:
    available, unknown = _capabilities(machine)
    possible: dict[str, bool | None] = {}
    reasons: dict[str, list[str]] = {}
    for profile in memory_profiles.ordered():
        unmet = profile.unmet(available)
        blocking = [token for token in unmet if token not in unknown]
        if blocking:
            possible[profile.id] = False
            reasons[profile.id] = [_unmet_reason(token, machine) for token in blocking]
        elif unmet:
            possible[profile.id] = None
            reasons[profile.id] = ["Docker n'a pas pu être lu : possible ou non, on ne sait pas"]
        else:
            possible[profile.id] = True
            reasons[profile.id] = []
    recommended, why = _recommend(machine, possible)
    services = machine.services or {}
    rows: list[dict[str, Any]] = []
    for profile in memory_profiles.ordered():
        needed = _PROFILE_SERVICES.get(profile.id, ())
        start = [_SERVICE_LABELS[s] for s in needed if not (services.get(s) and services[s].reachable)]
        shared = [_SERVICE_LABELS[s] for s in needed if services.get(s) and services[s].reachable]
        missing_extras = [
            extra for extra in _PROFILE_EXTRAS.get(profile.id, ())
            if services.get(extra) is not None and not services[extra].extra_installed
        ]
        caveats = []
        if memory_profiles.REQ_EGRESS in profile.requires:
            caveats.append("suppose un accès réseau sortant (non sondé)")
        if profile.id != "lexical" and machine.embedding is False:
            caveats.append("sans moteur d'embedding, la recherche reste lexicale")
        rows.append({
            "id": profile.id,
            "label": profile.label,
            "summary": profile.summary,
            "possible": possible[profile.id],
            "reasons": reasons[profile.id],
            "caveats": caveats,
            "recommended": profile.id == recommended,
            "start": start,
            "shared": shared,
            "extras": missing_extras,
            "extrasHint": _extras_hint(machine, probes, missing_extras),
            "commands": _profile_commands(profile.id, start),
        })
    return rows, {"id": recommended, "reason": why}


#: Ce que ``grimoire init`` puis ``grimoire up`` écrivent dans le dossier (relevé
#: sur un projet neuf) : l'arborescence du plan, pas une promesse au fichier près.
WRITES: tuple[dict[str, str], ...] = (
    {"path": "project-context.yaml", "what": "la configuration du projet, mémoire comprise"},
    {"path": "_grimoire/", "what": "le kit, le standard agentique, la mémoire du projet"},
    {"path": "_grimoire-output/", "what": "les sorties : preuves, journaux"},
    {"path": "AGENTS.md, CLAUDE.md, GEMINI.md", "what": "les consignes lues par les hôtes"},
    {"path": ".claude/, .github/, .cursor/, .opencode/", "what": "les fichiers propres à chaque hôte"},
    {"path": ".mcp.json, .gitignore", "what": "le serveur MCP du kit, les sorties ignorées par git"},
)


def setup_guide(project_root: Path, probes: GuideProbes | None = None) -> dict[str, Any]:
    """La charge utile du guide de premier lancement pour *project_root*."""
    probes = probes or GuideProbes()
    machine = _probe(probes)
    python = _attempt(machine, "python", probes.python_version)
    checks = [
        _check("python", "Python", OK, str(python), "L'interpréteur qui fait tourner ce cockpit.")
        if python else _check("python", "Python", UNKNOWN, "inconnu", "Version illisible."),
        _installer_check(probes),
        _docker_check(machine),
        _embedding_check(machine),
        _check(
            "egress", "Réseau sortant", UNKNOWN, "non sondé",
            "Une sonde mentirait derrière un proxy : grimoire init te le demande.",
            "Sans réseau sortant, seul le profil lexical reste possible.",
        ),
        _services_check(machine),
        _hosts_check(probes),
        _registry_check(probes),
    ]
    profiles, recommended = _profiles(machine, probes)
    return {
        "schemaVersion": SETUP_GUIDE_SCHEMA_VERSION,
        "projectRoot": str(project_root),
        "configured": (project_root / "project-context.yaml").is_file(),
        "checks": checks,
        "services": [
            {"id": sid, "label": _SERVICE_LABELS.get(sid, sid), "reachable": probe.reachable,
             "extra": probe.extra, "extraInstalled": probe.extra_installed}
            for sid, probe in (machine.services or {}).items()
        ],
        "profiles": profiles,
        "recommended": recommended,
        "writes": [dict(entry) for entry in WRITES],
    }
