"""Enhanced ``grimoire init`` — interactive wizard + full scaffolding.

Replaces the minimal init command with a complete project bootstrapping
experience: stack detection, archetype resolution, agent deployment,
framework installation, and a rich summary report.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from grimoire.__version__ import __version__
from grimoire.core.archetype_resolver import ArchetypeResolver, ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, ScaffoldPlan, ScaffoldResult
from grimoire.core.scanner import ScanResult, StackScanner
from grimoire.hosts.sync import sync_host_surfaces
from grimoire.memory import profiles as memory_profiles

logger = logging.getLogger(__name__)

console = Console(stderr=True)


# ── Archetype catalog — single source of truth ──────────────────────────────
#
# Every valid archetype id lives here exactly once.  ``KNOWN_ARCHETYPES`` (flag
# validation) and ``_ARCHETYPE_INFO`` (wizard display) are both derived from
# this catalog so they can never diverge again.
#
# Keep ids in sync with config.py / core/validator.py / core/schema.py.

@dataclass(frozen=True)
class ArchetypeSpec:
    """Metadata for one archetype id."""

    id: str
    label: str
    agents: str
    traits: str
    base: bool = False      # always included — not offered in the multi-select
    internal: bool = False  # deployment category (meta/stack/features), valid
                            # as a flag value but never offered in the wizard


# Order = wizard display order.
ARCHETYPE_CATALOG: tuple[ArchetypeSpec, ...] = (
    ArchetypeSpec("minimal", "Minimal", "3 meta-agents", "base layer — always included", base=True),
    ArchetypeSpec("web-app", "Web App", "2 agents", "TDD, type-safety, API-first"),
    ArchetypeSpec(
        "infra-ops", "Infra & DevOps", "7 agents",
        "homelab/self-hosted (Proxmox, K3s, Longhorn) — IaC, security-first, observability",
    ),
    ArchetypeSpec("platform-engineering", "Platform Eng.", "4 agents", "architecture-first, contract-driven"),
    ArchetypeSpec("agentic-standard", "Agentic Standard", "3 meta-agents", "normative traceability, evidence gates"),
    ArchetypeSpec("creative-studio", "Creative Studio", "5 agents", "visual-consistency, brand-voice"),
    ArchetypeSpec("fix-loop", "Fix Loop", "1 agent", "proof-of-execution, severity-adaptive"),
    ArchetypeSpec("meta", "Meta agents", "3 agents", "internal category — deployed with every archetype", internal=True),
    ArchetypeSpec("stack", "Stack experts", "per stack", "internal category — auto-selected from stack scan", internal=True),
    ArchetypeSpec("features", "Feature agents", "per feature", "internal category — auto-selected from scan", internal=True),
)

# Valid values for --archetype (flags accept every id, including internal ones,
# for backward compatibility).
KNOWN_ARCHETYPES = frozenset(spec.id for spec in ARCHETYPE_CATALOG)

KNOWN_BACKENDS = frozenset({"auto", "local", "lexical", "tantivy-local", "qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama"})

# Archetype human descriptions for the wizard (order = display order).
# Derived from the catalog: every selectable (non-internal, non-base) archetype.
_ARCHETYPE_INFO: dict[str, tuple[str, str, str]] = {
    spec.id: (spec.label, spec.agents, spec.traits)
    for spec in ARCHETYPE_CATALOG
    if not spec.internal and not spec.base
}
# Minimal is always base — not shown in multi-select
_ARCHETYPE_KEYS = list(_ARCHETYPE_INFO.keys())

_QDRANT_DEFAULT_URL = "http://localhost:6333"
_WEAVIATE_DEFAULT_URL = "http://localhost:8080"
_OLLAMA_DEFAULT_URL = "http://localhost:11434"
_QDRANT_COMPOSE_FILE = "docker-compose.memory.yml"

# ── Lite profile (issue Grimoire-kit#552, phase 2 lot 2.6) ─────────────────────

_CI_MARKERS = (
    ".github/workflows",
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    ".travis.yml",
    "Jenkinsfile",
)
_TEST_DIR_MARKERS = ("tests", "test", "spec", "__tests__")


def _looks_like_a_playground(target: Path) -> bool:
    """True when *target* has neither a CI config nor a test suite.

    A light, top-level-only heuristic — no recursive scan — used solely to
    surface the ``--lite`` suggestion in the wizard. It never changes what
    gets written; a false negative just means the suggestion isn't shown.
    """
    if any((target / marker).exists() for marker in _CI_MARKERS):
        return False
    for name in _TEST_DIR_MARKERS:
        candidate = target / name
        if candidate.is_dir() and any(candidate.iterdir()):
            return False
    return True


# ── Memory backend detection ─────────────────────────────────────────────────


def _http_ok(url: str, *, timeout: float = 2.0) -> bool:
    """Return True when a local HTTP probe responds successfully."""
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= int(resp.status) < 300
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _is_qdrant_reachable(qdrant_url: str = _QDRANT_DEFAULT_URL) -> bool:
    """Probe Qdrant's local HTTP API."""
    base = qdrant_url.rstrip("/")
    return any(_http_ok(f"{base}{endpoint}") for endpoint in ("/readyz", "/collections", "/healthz"))


def _is_weaviate_reachable(weaviate_url: str = _WEAVIATE_DEFAULT_URL) -> bool:
    """Probe Weaviate's local HTTP API."""
    base = weaviate_url.rstrip("/")
    return any(_http_ok(f"{base}{endpoint}") for endpoint in ("/v1/.well-known/ready", "/v1/meta"))


def _is_ollama_reachable(ollama_url: str = _OLLAMA_DEFAULT_URL) -> bool:
    """Probe a local Ollama's HTTP API.

    Named and shaped like :func:`_is_weaviate_reachable` /
    :func:`_is_qdrant_reachable` on purpose: the CI Windows job stalled ~9
    minutes on every ``-y init`` call that left this probe unmocked,
    precisely because it used to be an inline ``_http_ok(...)`` call with
    nothing to patch it by — a test could stub the other two services but
    not this one (see ``tests/test_cmd_init.py::_stub_unreachable_memory_services``).
    """
    return _http_ok(f"{ollama_url.rstrip('/')}/api/tags")


def detect_memory_backend() -> str:
    """Probe localhost for a Memory OS service running on this machine.

    Purely informational (issue Grimoire-kit#496) — it no longer decides
    ``init``'s backend. A project without an explicit ``--backend`` always
    falls back to an isolated default; what this function finds is only
    *suggested* in the report / offered as an explicit question in the
    interactive wizard, never attached to silently.

    Returns ``"qdrant-server"`` (not ``"qdrant-local"``) for a *reachable
    Qdrant HTTP server* — the two used to share the same string despite
    meaning opposite things: a server actually running on this machine
    versus the embedded, file-only backend with nothing to reach at all.
    Confirming this suggestion attaches to the real server (``qdrant_url``
    included via :data:`grimoire.memory.profiles.BACKEND_CONNECTION`); the
    old, colliding name would have silently created a second, unrelated
    embedded store instead.
    """
    if _is_weaviate_reachable():
        return "weaviate-server"

    if _is_qdrant_reachable():
        return "qdrant-server"

    if _is_ollama_reachable():
        return "ollama"

    return "local"


#: Human-readable label for a service `detect_memory_backend()` can return,
#: used to build the suggestion line in the report and the wizard's explicit
#: question — never to decide anything on its own.
_DETECTED_SERVICE_LABELS: dict[str, str] = {
    "weaviate-server": f"Weaviate sur {_WEAVIATE_DEFAULT_URL}",
    "qdrant-server": f"Qdrant sur {_QDRANT_DEFAULT_URL}",
    "ollama": f"Ollama sur {_OLLAMA_DEFAULT_URL}",
}


def memory_service_suggestion(detected: str) -> str | None:
    """A one-line suggestion for a detected-but-unattached memory service.

    Returns ``None`` when nothing was detected (``detected == "local"``).
    """
    label = _DETECTED_SERVICE_LABELS.get(detected)
    if label is None:
        return None
    return (
        f"détecté : {label} — activez-le avec "
        "`grimoire memory up --profile standard --apply`"
    )


def _http_get_json(url: str, *, timeout: float = 2.0) -> dict[str, Any] | None:
    """GET *url* and parse it as JSON, or ``None`` on any failure.

    A probe failure (service down, unexpected payload…) must never look like
    a confirmed empty collection — callers treat ``None`` the same as "cannot
    tell", not as "safe to attach".
    """
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if not (200 <= int(resp.status) < 300):
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _weaviate_collection_has_content(collection: str, *, weaviate_url: str = _WEAVIATE_DEFAULT_URL) -> bool:
    """Whether *collection* already exists on Weaviate and holds objects."""
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection

    name = normalize_weaviate_collection(collection)
    base = weaviate_url.rstrip("/")
    data = _http_get_json(f"{base}/v1/objects?class={name}&limit=1")
    if not data:
        return False
    return bool(data.get("objects"))


def _qdrant_collection_has_content(collection: str, *, qdrant_url: str = _QDRANT_DEFAULT_URL) -> bool:
    """Whether *collection* already exists on Qdrant and holds points."""
    base = qdrant_url.rstrip("/")
    data = _http_get_json(f"{base}/collections/{collection}")
    if not data:
        return False
    result = data.get("result")
    if not isinstance(result, dict):
        return False
    return bool(result.get("points_count") or 0)


def collection_has_content(backend: str, collection: str) -> bool:
    """Best-effort, non-fatal probe: does *collection* already hold data?

    Anything unreachable or ambiguous reads as "empty" — a probe failure must
    never block ``init``; only a *confirmed* non-empty collection does
    (issue Grimoire-kit#496), and only for the backends that actually name a
    shared collection (Weaviate, Qdrant).
    """
    if backend == "weaviate-server":
        return _weaviate_collection_has_content(collection)
    if backend in ("qdrant-local", "qdrant-server"):
        return _qdrant_collection_has_content(collection)
    return False


def _is_docker_available() -> bool:
    """Whether a Docker CLI exists to bring the composed services up."""
    return shutil.which("docker") is not None


def _is_redis_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """Probe a local Redis — the hot-memory layer cannot be composed without one."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def machine_capabilities(*, has_egress: bool) -> frozenset[str]:
    """Capability tokens this machine offers to the memory profiles.

    Egress is answered, not probed: a proxy that resolves DNS but blocks the
    model download would make any probe lie.
    """
    tokens = set()
    if has_egress:
        tokens.add(memory_profiles.REQ_EGRESS)
    if _is_docker_available():
        tokens.add(memory_profiles.REQ_DOCKER)
    if _is_redis_reachable():
        tokens.add(memory_profiles.REQ_REDIS)
    return frozenset(tokens)


def _recommend_memory_profile() -> tuple[str, str]:
    """The richest composition this machine can serve, and why.

    Used for both the wizard's Memory step and the express default
    (2026-09-18 decision, revised same day by Guilhem's arbitrage on #619):
    ``complet`` when Docker's daemon actually answers, ``standard`` with a
    local embedding engine but no Docker, ``lexical`` otherwise. Never
    *attaches to a detected service* (issue #496 intact): a ``complet``
    recommendation always writes the kit's own fixed default connection and
    a fresh, project-scoped collection, never a probed URL. Whether the
    missing services also get *started* is a separate, caller-decided
    consent question (see ``start_memory_stack``, ``run_init``) — this only
    ever recommends. Delegates to :mod:`grimoire.tools.memory_setup`, the
    same module ``grimoire memory up`` uses, so the two never drift apart.
    """
    from grimoire.tools.memory_setup import recommend_profile

    return recommend_profile()


def _wait_for_qdrant(qdrant_url: str = _QDRANT_DEFAULT_URL) -> bool:
    """Wait briefly for a freshly started Qdrant service to answer."""
    for _ in range(10):
        if _is_qdrant_reachable(qdrant_url):
            return True
        time.sleep(0.5)
    return False


def _start_qdrant_docker(target: Path) -> tuple[bool, str]:
    """Start the generated Qdrant Docker Compose service."""
    compose_file = target / _QDRANT_COMPOSE_FILE
    if not compose_file.is_file():
        return False, f"{_QDRANT_COMPOSE_FILE} introuvable dans le projet généré."

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file.name, "up", "-d"],
            cwd=target,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError:
        return False, "Docker CLI introuvable. Lance `docker compose -f docker-compose.memory.yml up -d` après installation."
    except subprocess.TimeoutExpired:
        return False, "Docker Compose n'a pas répondu. Relance `docker compose -f docker-compose.memory.yml up -d`."

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "erreur inconnue"
        return False, f"Docker Compose a échoué: {detail}"

    if _wait_for_qdrant():
        return True, "Qdrant est démarré sur http://localhost:6333."

    return False, "Docker Compose est lancé, mais Qdrant ne répond pas encore sur http://localhost:6333."


def _git_user_name() -> str:
    """Try to get git user.name, return empty on failure."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


# ── Interactive wizard ───────────────────────────────────────────────────────


def _choose_memory_profile(
    backend: str,
    *,
    offer_qdrant_docker: bool,
    recommended_id: str = "",
    recommended_reason: str = "",
    detected_service: str = "local",
) -> tuple[str, str, bool, bool, bool]:
    """Ask for a memory *composition*, not a backend.

    A project runs several memory layers at once; asking which single backend
    to use left the other six on their defaults forever.  Returns the chosen
    profile id, the backend it runs on, the offline verdict, whether to start
    Qdrant in Docker, and whether to start the full ``complet`` stack now
    (Guilhem's arbitrage on #619, same day as the decision below: the
    question defaults to *yes* — the point of recommending ``complet`` is
    that its potential gets exploited right away, not configured and left
    dormant).

    Only compositions this machine can actually serve are offered — an
    unreachable one is shown with the reason and cannot be selected, because a
    profile that cannot be filled is worse than a smaller one that can.

    ``recommended_id``/``recommended_reason`` (2026-09-18 onboarding decision,
    replacing the ``--lite``-only ``suggest_lite`` heuristic of
    Grimoire-kit#552) name the richest composition
    :func:`grimoire.tools.memory_setup.recommend_profile` found this machine
    can serve — ``complet`` with Docker available, ``standard`` with a local
    embedding engine but no Docker, ``lexical`` as the explained floor.
    Defaults to :data:`memory_profiles.DEFAULT_PROFILE` when left unset.

    ``detected_service`` (issue Grimoire-kit#496) is what
    :func:`detect_memory_backend` found on this machine — never applied on
    its own. When it differs from *backend* (meaning nothing explicit already
    claimed it), this asks a direct yes/no question before letting the
    ``standard`` composition resolve onto it; declining leaves the project on
    *backend* (``lexical`` by default), fully isolated.
    """
    if detected_service not in ("local", backend):
        hint = _DETECTED_SERVICE_LABELS.get(detected_service, detected_service)
        console.print(f"  [dim]Service mémoire détecté sur cette machine : {hint}.[/dim]")
        use_detected = Confirm.ask(
            "  [bold]L'utiliser pour la composition mémoire de ce projet ?[/bold]",
            default=False,
        )
        if use_detected:
            backend = detected_service
        else:
            console.print(
                "  [dim]→ ce projet restera isolé ; `grimoire memory up` pourra "
                "l'y attacher explicitement plus tard.[/dim]"
            )

    has_egress = True
    if offer_qdrant_docker:
        console.print("  [dim]Aucun service vectoriel sur localhost.[/dim]")
        # The question is about the network, not about Qdrant: a closed site
        # cannot reach an embedding model, so proposing a container there only
        # produces a store that can never be filled.
        has_egress = Confirm.ask(
            "  [bold]Cette machine a-t-elle un accès réseau sortant ?[/bold]",
            default=True,
        )

    available = machine_capabilities(has_egress=has_egress)

    console.print()
    console.print("  [bold]La mémoire est une composition de couches, pas un backend.[/bold]")
    console.print()
    recommended_id = recommended_id or memory_profiles.DEFAULT_PROFILE
    if recommended_reason:
        console.print(f"  [dim]{recommended_reason}.[/dim]")
        console.print()
    choices: list[str] = []
    default_choice = "1"
    for idx, profile in enumerate(memory_profiles.ordered(), 1):
        key = str(idx)
        unmet = profile.unmet(available)
        if unmet:
            reason = ", ".join(unmet)
            console.print(f"    [dim]{key}) {profile.label:<10} {profile.summary}[/dim]")
            console.print(f"       [dim]indisponible ici — manque : {reason}[/dim]")
            continue
        choices.append(key)
        if profile.id == recommended_id:
            default_choice = key
            console.print(f"    [bold]{key}[/bold]) {profile.label:<10} {profile.summary} [cyan]← recommandé[/cyan]")
        else:
            console.print(f"    [bold]{key}[/bold]) {profile.label:<10} {profile.summary}")

    console.print()
    if default_choice not in choices:
        default_choice = choices[0]
    raw = Prompt.ask("  [bold]Composition[/bold]", default=default_choice, choices=choices)
    chosen = memory_profiles.ordered()[int(raw) - 1]

    offline = chosen.id == "lexical" and not has_egress
    backend = chosen.resolve_backend(backend)

    # Proposed, never assumed: a container plus its volume is not something to
    # start behind the user's back on a first run.
    # `standard` is the only composition that does not pin its own store, so
    # it is the only one where a container still has to be offered.
    qdrant_docker = False
    if offer_qdrant_docker and has_egress and chosen.id == memory_profiles.DEFAULT_PROFILE:
        qdrant_docker = Confirm.ask(
            "  [bold]Démarrer Qdrant via Docker pour la couche sémantique ?[/bold]",
            default=False,
        )
        if qdrant_docker:
            backend = "qdrant-server"
    if chosen.id == "lexical":
        console.print("  [dim]→ BM25 seul : aucun modèle, aucun service, aucun réseau.[/dim]")
        console.print("  [dim]  Pour ajouter la couche sémantique plus tard : grimoire memory bundle install[/dim]")

    # `complet` is the one composition worth exploiting immediately rather
    # than leaving configured-but-dormant: defaults to yes (arbitrage
    # 2026-09-18, Guilhem on #619) — Enter starts Weaviate + Neo4j + Redis
    # via the kit's own compose templates before the wizard even finishes.
    start_stack = False
    if chosen.id == "complet":
        start_stack = Confirm.ask(
            "  [bold]Démarrer la pile mémoire complète (Weaviate + Neo4j + Redis) maintenant ?[/bold]",
            default=True,
        )
        if not start_stack:
            console.print(
                "  [dim]→ config écrite pour `complet` ; démarrez-la plus tard avec "
                "`grimoire memory up --profile complet --start --apply`.[/dim]"
            )

    return chosen.id, backend, offline, qdrant_docker, start_stack


def _run_wizard(
    target: Path,
    scan: ScanResult | None,
    resolved: ResolvedArchetype,
    backend: str,
    *,
    offer_qdrant_docker: bool = False,
    detected_service: str = "local",
) -> dict[str, Any]:
    """Interactive wizard — multi-select archetypes, returns config dict."""
    console.print()
    console.print(Panel.fit(
        f"[bold]Grimoire Kit v{__version__}[/bold] — Project Setup Wizard",
        border_style="cyan",
    ))

    # ── Step 1/5 · Identity ───────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[#----] 1/5 · Identity[/dim]")

    # Show detected stacks
    if scan and scan.stacks:
        console.print("  [bold]Stack detected:[/bold]")
        for det in scan.stacks:
            conf_pct = f"{det.confidence:.0%}"
            evidence = ", ".join(det.evidence[:3])
            console.print(f"    [green][OK][/green] {det.name} ({conf_pct}) — {evidence}")
        console.print()

    default_name = target.name
    project_name = Prompt.ask(
        "  [bold]Project name[/bold]",
        default=default_name,
    )

    git_name = _git_user_name()
    user_name = Prompt.ask(
        "  [bold]Your name[/bold]",
        default=git_name or "Developer",
    )

    # ── Step 2/5 · Preferences ────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[##---] 2/5 · Preferences[/dim]")

    _lang_choices = {"1": "Français", "2": "English"}
    console.print("  [bold]Language:[/bold]  1) Français  2) English")
    lang_input = Prompt.ask(
        "  [bold]Choose[/bold]",
        default="1",
        choices=["1", "2"],
    )
    language = _lang_choices[lang_input]

    _skill_choices = {"1": "beginner", "2": "intermediate", "3": "expert"}
    console.print("  [bold]Skill:[/bold]    1) Débutant  2) Intermédiaire  3) Expert")
    skill_input = Prompt.ask(
        "  [bold]Choose[/bold]",
        default="2",
        choices=["1", "2", "3"],
    )
    skill_level = _skill_choices[skill_input]

    # ── Step 3/5 · Memory composition ─────────────────────────────────
    console.print()
    console.print("  [dim]\\[###--] 3/5 · Mémoire[/dim]")
    recommended_id, recommended_reason = _recommend_memory_profile()
    profile_id, backend, offline, qdrant_docker, start_stack = _choose_memory_profile(
        backend,
        offer_qdrant_docker=offer_qdrant_docker,
        recommended_id=recommended_id,
        recommended_reason=recommended_reason,
        detected_service=detected_service,
    )

    # ── Step 4/5 · Archetypes (multi-select) ──────────────────────────
    console.print()
    console.print("  [dim]\\[####-] 4/5 · Archetypes[/dim]")
    console.print()
    console.print("  [bold]minimal[/bold] is always included — it's the base.")
    console.print("  Choose specializations to add:\n")

    # Compute auto-detected defaults from resolver — a best-guess pick
    # (decision 2026-09-18: resolve() never guesses `minimal`, but a guess is
    # still not a confident rule match) is never pre-filled as if detected;
    # the wizard falls through to guided discovery for it instead (below).
    auto_suggested: list[str] = (
        [] if resolved.is_best_guess
        else (list(resolved.archetypes) if resolved.archetypes else [resolved.archetype])
    )
    auto_indices: list[str] = []
    for idx, key in enumerate(_ARCHETYPE_KEYS, 1):
        label, agent_count, traits = _ARCHETYPE_INFO[key]
        marker = " [cyan]← detected[/cyan]" if key in auto_suggested and key != "minimal" else ""
        console.print(f"    [bold]{idx}[/bold]) {label:<22} {agent_count:<10} {traits}{marker}")
        if key in auto_suggested and key != "minimal":
            auto_indices.append(str(idx))

    console.print()
    console.print("    [bold]0[/bold]) Not sure — help me choose")
    console.print()

    # Onboarding audit 2026-09-18, constat #2: a blank default here used to
    # be "none" (→ minimal, no specialization), even though guided discovery
    # (option "0") already existed and worked. When a rule already detected
    # a specialization, its numeric auto-selection stays the default; only
    # the *no-signal* case (a bare Enter would previously install minimal in
    # silence) now defaults to guided discovery instead.
    default_input = ",".join(auto_indices) if auto_indices else "0"
    arch_input = Prompt.ask(
        "  [bold]Choice (ex: 1,3,5 or all)[/bold]",
        default=default_input,
    )

    # Parse selection
    selected_archetypes = _parse_archetype_selection(arch_input, scan)

    # Show composition preview
    _display_composition_preview(selected_archetypes)

    # Allow adjustment
    adjust = Prompt.ask(
        "  [bold]Adjust? (new numbers, or Enter to confirm)[/bold]",
        default="",
    )
    if adjust.strip():
        selected_archetypes = _parse_archetype_selection(adjust, scan)
        _display_composition_preview(selected_archetypes)

    # ── Step 5/5 · Confirm ────────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[#####] 5/5 · Confirmation[/dim]")
    arch_display = ", ".join(selected_archetypes) if selected_archetypes else "minimal"
    console.print()
    console.print("  [bold]Summary:[/bold]")
    console.print(f"    Project:     {project_name}")
    console.print(f"    User:        {user_name}")
    console.print(f"    Language:    {language}")
    console.print(f"    Skill level: {skill_level}")
    console.print(f"    Archetypes:  {arch_display}")
    memory_profile = memory_profiles.resolve(profile_id)
    console.print(f"    Mémoire:     {memory_profile.label} — {memory_profile.summary}")
    console.print(f"    Backend:     {backend}")
    if qdrant_docker:
        console.print("    Qdrant:      Docker local auto-start")
    console.print()

    if not Confirm.ask("  [bold]Proceed with installation?[/bold]", default=True):
        raise typer.Abort

    return {
        "project_name": project_name,
        "user_name": user_name,
        "language": language,
        "skill_level": skill_level,
        "archetypes": selected_archetypes,
        "archetype": selected_archetypes[0] if selected_archetypes else "minimal",
        "backend": backend,
        "qdrant_docker": qdrant_docker,
        "offline": offline,
        "memory_profile": profile_id,
        "start_memory_stack": start_stack,
    }


def _parse_archetype_selection(
    raw: str,
    scan: ScanResult | None = None,
) -> list[str]:
    """Parse user input like '1,3,5' or 'all' or '0' into archetype list."""
    raw = raw.strip().lower()

    if raw == "all":
        return list(_ARCHETYPE_KEYS)

    if raw in ("none", ""):
        return ["minimal"]

    if raw == "0":
        return _guided_discovery(scan)

    # Parse comma/space separated numbers
    parts = raw.replace(" ", ",").split(",")
    selected: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            idx = int(p)
        except ValueError:
            # Try as archetype name directly
            if p in _ARCHETYPE_KEYS and p not in selected:
                selected.append(p)
            continue
        if 1 <= idx <= len(_ARCHETYPE_KEYS):
            key = _ARCHETYPE_KEYS[idx - 1]
            if key not in selected:
                selected.append(key)

    return selected or ["minimal"]


def _guided_discovery(scan: ScanResult | None) -> list[str]:
    """3-question guided flow for users who don't know which archetypes to pick."""
    console.print()
    console.print("  [bold]── Guided Discovery ──[/bold]\n")

    # Detect defaults from scan
    detected = {d.name for d in scan.stacks} if scan and scan.stacks else set()
    has_frontend = bool(detected & {"react", "vue", "angular", "javascript", "typescript"})
    has_infra = bool(detected & {"terraform", "kubernetes", "ansible", "docker"})

    q1 = Confirm.ask(
        "  Does your project have a [bold]web frontend[/bold] (React, Vue, Angular)?",
        default=has_frontend,
    )
    q2 = Confirm.ask(
        "  Do you manage [bold]infrastructure[/bold] (K8s, Terraform, CI/CD)?",
        default=has_infra,
    )
    q3 = Confirm.ask(
        "  Do you need a [bold]certified fix loop[/bold] (TDD proofs, incident response)?",
        default=False,
    )

    result: list[str] = []
    if q1:
        result.append("web-app")
    if q2:
        result.append("infra-ops")
    if q3:
        result.append("fix-loop")

    # Decision 2026-09-18 (Guilhem, corrected same day): even a "no" to all
    # three questions never ends in `minimal` — no asserted domain (web,
    # infra, fix-loop) means `stack` (Atlas, the kit's generalist for exactly
    # this case), not `platform-engineering`.
    if not result:
        console.print(
            "  [dim]No specialization selected — stack (Atlas, the kit's generalist "
            "for a project with no asserted domain) covers this best.[/dim]"
        )
        return ["stack"]

    names = ", ".join(result)
    console.print(f"\n  [bold]Recommended:[/bold] {names}")
    return result


def _display_composition_preview(archetypes: list[str]) -> None:
    """Show what the selected composition includes."""
    console.print()
    console.print("  [bold]── Composition ──[/bold]")
    console.print("  Base : [bold]minimal[/bold] (3 meta-agents)")
    total_agents = 3  # meta-agents
    for key in archetypes:
        if key == "minimal":
            continue
        info = _ARCHETYPE_INFO.get(key)
        if info:
            label, agent_count, traits = info
            console.print(f"  {label:<22} → +{agent_count} · {traits}")
            # Parse agent count
            with contextlib.suppress(ValueError, IndexError):
                total_agents += int(agent_count.split()[0])
    console.print(f"\n  [dim]Total: ~{total_agents} agents[/dim]")


# ── Rich summary report ─────────────────────────────────────────────────────


def _memory_step_summary(target: Path, *, applied_profile: str, reason: str) -> dict[str, Any]:
    """Close the Memory step: a cheap, structural health check plus the
    upgrade path — the equivalent of `grimoire memory up --profile X --apply`
    already ran during scaffolding; this only reports on it.

    Deliberately never loads an embedding model (that first-use cost belongs
    to an explicit memory operation, e.g. `grimoire memory status`, not to
    `init` — see the module docstring of :mod:`grimoire.memory.embedding`).
    ``healthy`` only asserts the config landed on a profile this version
    knows, never that a remote service actually answers right now.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError
    from grimoire.tools.memory_setup import (
        docker_daemon_reachable,
        local_embedding_available,
        unreached_configured_services,
    )

    served = applied_profile
    backend = ""
    not_started: list[str] = []
    with contextlib.suppress(GrimoireConfigError, OSError):
        # A reporting step must never break `init`: any config-read failure
        # here just falls back to what this call already knows it applied.
        cfg = GrimoireConfig.from_yaml(target / "project-context.yaml")
        served = cfg.memory.layer_profile or applied_profile
        backend = cfg.memory.backend
        # A `complet`/`graphe` composition can be *configured* without ever
        # being *started* (no consent given — arbitrage 2026-09-18 on #619):
        # this is the gap `grimoire doctor` names "pile mémoire non démarrée".
        not_started = unreached_configured_services(cfg.memory)

    if not local_embedding_available():
        feasible = "lexical"
    elif docker_daemon_reachable():
        feasible = "complet"
    else:
        feasible = "standard"

    order = memory_profiles.PROFILE_ORDER
    served_rank = order.index(served) if served in order else 0
    upgrade_to = feasible if order.index(feasible) > served_rank else ""

    return {
        "served": served,
        "backend": backend,
        "reason": reason,
        "not_started": not_started,
        "healthy": served in order,
        "feasible": feasible,
        "upgrade_to": upgrade_to,
    }


def _display_report(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
    *,
    qdrant_docker_started: bool = False,
    qdrant_docker_message: str = "",
    detected_service: str = "local",
    no_cockpit: bool = False,
    memory_summary: dict[str, Any] | None = None,
) -> None:
    """Display a rich post-install report."""
    console.print()

    # Stack detection
    if scan and scan.stacks:
        stacks_str = " · ".join(
            f"[bold]{d.name}[/bold]" for d in scan.stacks
        )
        console.print(f"  [cyan]Stack:[/cyan] {stacks_str}")

    # Archetypes
    if resolved.is_composite:
        names = []
        for a in resolved.archetypes:
            ai = _ARCHETYPE_INFO.get(a)
            names.append(ai[0] if ai else a)
        console.print(f"  [cyan]Archetypes:[/cyan] {' + '.join(names)}")
    else:
        info = _ARCHETYPE_INFO.get(resolved.archetype, (resolved.archetype, "", ""))
        console.print(f"  [cyan]Archetype:[/cyan] {info[0]} ({resolved.reason})")
        # Decision 2026-09-18 (Guilhem): resolve() never installs `minimal`
        # automatically — an ambiguous stack still gets a named, applied,
        # specialized archetype (`is_best_guess`). That is never the same as
        # a deliberate choice, so it is always named as a guess with its
        # override command, never left to look like a confident detection.
        if resolved.is_best_guess:
            console.print(
                "  [yellow]Best guess:[/yellow] no confident match for this stack — "
                "change it with [cyan]grimoire up -a <archetype>[/cyan] or discover a "
                "better fit with [cyan]grimoire init --interactive[/cyan]."
            )
    console.print(f"  [cyan]Memory:[/cyan] {backend}")
    _backend_tips = {
        "local": "Mémoire fichier locale — aucune dépendance requise",
        "qdrant-local": "Qdrant embarqué (fichier local, aucun service) — recherche sémantique activée",
        "qdrant-server": "Qdrant distant configuré — vérifier avec grimoire doctor",
        "weaviate-server": "Weaviate + Neo4j configurés — vérifier avec grimoire memory status et memory migrate verify",
        "ollama": "Ollama détecté — embeddings locaux activés",
    }
    _tip = _backend_tips.get(backend)
    if _tip:
        console.print(f"           [dim]{_tip}[/dim]")
    if qdrant_docker_message:
        status = "[green]OK[/green]" if qdrant_docker_started else "[yellow]WARN[/yellow]"
        console.print(f"           {status} [dim]{qdrant_docker_message}[/dim]")
    if detected_service != backend:
        suggestion = memory_service_suggestion(detected_service)
        if suggestion:
            console.print(f"           [yellow]![/yellow] [dim]{suggestion}[/dim]")
    if memory_summary and memory_summary.get("reason"):
        console.print(f"           [dim]{memory_summary['reason']}.[/dim]")
    not_started = memory_summary.get("not_started") if memory_summary else None
    if memory_summary and not_started:
        # Configured (2026-09-18 arbitrage on #619) but not started — no
        # consent was given (bare non-TTY run, no `-y`, no `--memory-stack up`).
        console.print(
            f"           [yellow]![/yellow] [dim]pile mémoire non démarrée ({', '.join(not_started)}) : "
            f"grimoire memory up --profile {memory_summary['served']} --start --apply[/dim]"
        )
    else:
        upgrade_to = memory_summary.get("upgrade_to") if memory_summary else ""
        if upgrade_to:
            target_label = memory_profiles.resolve(upgrade_to).label
            start_flag = " --start" if upgrade_to in ("graphe", "complet") else ""
            console.print(
                f"           [yellow]^[/yellow] [dim]This machine can serve {target_label}: "
                f"grimoire memory up --profile {upgrade_to}{start_flag} --apply[/dim]"
            )
    console.print()

    # Agents deployed (categorized)
    agents_by_cat: dict[str, list[str]] = {}
    for label in result.copied_files:
        if "/" in label:
            cat, name = label.split("/", 1)
            agents_by_cat.setdefault(cat, []).append(name)
    _cat_icons = {"meta": "", "stack": "", "feature": ""}
    if agents_by_cat:
        console.print("  [cyan]Agents deployed:[/cyan]")
        for cat, agents in agents_by_cat.items():
            icon = _cat_icons.get(cat, "")
            agent_names = ", ".join(f"[bold]{a}[/bold]" for a in agents)
            console.print(f"    {icon} [dim]{cat}:[/dim] {agent_names}")
        console.print()

    # Summary counts
    console.print(f"  [dim]{len(result.created_dirs)} dirs · {len(result.copied_files)} files · {len(result.rendered_files)} configs[/dim]")
    console.print()

    # Next steps — dynamic (onboarding audit 2026-09-18, constat #3): the old
    # panel was byte-identical across every stack/archetype/profile
    # combination. This names what was installed and why, then offers at
    # most three actions chosen from the project's real state.
    from grimoire.core.onboarding_panel import build_next_steps

    panel = build_next_steps(
        target,
        resolved=resolved,
        backend=backend,
        no_cockpit=no_cockpit,
        layer_profile=(memory_summary or {}).get("served", ""),
    )
    body_lines = [
        "[bold]Your project is alive![/bold]\n",
        f"  {panel.headline}\n",
        f"  [bold cyan]Open it:[/bold cyan] [cyan]code {target.name}[/cyan]\n",
    ]
    for action in panel.actions:
        body_lines.append(f"  {action}")
    if panel.unexploited_line:
        body_lines.append(f"\n  [dim]{panel.unexploited_line}[/dim]")
    console.print(Panel(
        "\n".join(body_lines),
        title="[bold green]Next Steps[/bold green]",
        border_style="green",
    ))


def _display_dry_run(
    plan: ScaffoldPlan,
    target: Path,
    project_name: str,
    archetype: str,
    resolved: ResolvedArchetype | None = None,
) -> None:
    """Display what would happen in dry-run mode."""
    console.print("[bold]grimoire init --dry-run[/bold]")
    arch_display = archetype
    if resolved and resolved.is_composite:
        arch_display = ", ".join(resolved.archetypes)
    console.print(f"[dim]Scaffold plan for [bold]{project_name}[/bold] (archetypes: {arch_display})[/dim]\n")

    # Agent deployment breakdown
    agents_by_cat: dict[str, list[str]] = {}
    for fc in plan.copies:
        if fc.label and "/" in fc.label and fc.dst.suffix == ".md" and "/agents/" in str(fc.dst):
            cat, name = fc.label.split("/", 1)
            agents_by_cat.setdefault(cat, []).append(name)
    if agents_by_cat:
        console.print("[bold]Agents to deploy:[/bold]")
        _cat_icons = {"meta": "", "stack": "", "feature": ""}
        for cat, agents in agents_by_cat.items():
            icon = _cat_icons.get(cat, "")
            console.print(f"  {icon} [cyan]{cat}[/cyan]: {', '.join(agents)}")
        console.print()

    # DNA traits preview
    dna_copies = [fc for fc in plan.copies if "archetype.dna.yaml" in fc.label]
    if dna_copies:
        console.print(f"[bold]Archetype DNA:[/bold] {archetype}")
        try:
            dna_text = dna_copies[0].src.read_text(encoding="utf-8")
            for key in ("traits:", "constraints:", "values:"):
                if key in dna_text:
                    console.print(f"  [dim]{key}[/dim]")
                    in_section = False
                    for line in dna_text.splitlines():
                        if line.strip().startswith(key):
                            in_section = True
                            continue
                        if in_section:
                            if line.startswith(("  - ", "  - ")):
                                val = line.strip().lstrip("- ").split(":")[0]
                                console.print(f"    [green][OK][/green] {val}")
                            elif not line.startswith(" "):
                                break
        except OSError:
            pass
        console.print()

    if plan.directories:
        console.print("[bold]Directories:[/bold]")
        for d in plan.directories:
            console.print(f"  [cyan]mkdir[/cyan]  {d.relative_to(target)}/")

    if plan.copies:
        console.print("\n[bold]File copies:[/bold]")
        for fc in plan.copies:
            console.print(f"  [cyan]copy[/cyan]   {fc.label}")

    if plan.templates:
        console.print("\n[bold]Generated files:[/bold]")
        for tr in plan.templates:
            console.print(f"  [cyan]write[/cyan]  {tr.label}")

    # Gitignore preview
    gi_tpls = [t for t in plan.templates if ".gitignore" in (t.label or "")]
    if gi_tpls:
        console.print("\n[bold].gitignore patterns added:[/bold]")
        for line in gi_tpls[0].content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                console.print(f"  [dim]{stripped}[/dim]")

    console.print(f"\n[dim]Total: {plan.total_operations} operations[/dim]")


def _display_json(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
    *,
    qdrant_docker: dict[str, Any] | None = None,
    detected_service: str = "local",
    collection: str = "",
    memory_summary: dict[str, Any] | None = None,
    memory_stack_messages: list[str] | None = None,
) -> None:
    """Output JSON result for scripting."""
    data: dict[str, Any] = {
        "ok": True,
        "project": project_name,
        "path": str(target),
        "archetype": resolved.archetype,
        "archetypes": list(resolved.archetypes) if resolved.archetypes else [resolved.archetype],
        "backend": backend,
        "stacks": [d.name for d in scan.stacks] if scan else [],
        "agents": {
            "total": len(result.copied_files),
            "by_category": {},
            "list": result.copied_files,
        },
        "dirs_created": len(result.created_dirs),
        "files_copied": len(result.copied_files),
        "configs_generated": len(result.rendered_files),
    }
    if qdrant_docker is not None:
        data["qdrant_docker"] = qdrant_docker
    if collection:
        data["memory_collection"] = collection
    if detected_service != backend:
        suggestion = memory_service_suggestion(detected_service)
        if suggestion:
            data["memory_detected"] = detected_service
            data["memory_suggestion"] = suggestion
    if memory_summary:
        data["memory_profile"] = memory_summary
    if memory_stack_messages:
        data["memory_stack_started"] = memory_stack_messages
    for label in result.copied_files:
        if "/" in label:
            cat = label.split("/")[0]
            data["agents"]["by_category"].setdefault(cat, []).append(label.split("/", 1)[1])
    typer.echo(json.dumps(data, indent=2))


# ── Main entry point ─────────────────────────────────────────────────────────


def _maybe_register_cockpit(target: Path, project_name: str, fmt: str, *, no_cockpit: bool = False) -> None:
    """Auto-enrol the freshly scaffolded project in the local cockpit registry.

    Best-effort and non-fatal: a registry write failure never breaks ``init``.
    Opt out with ``--no-cockpit`` (``init``/``up``) or the ``GRIMOIRE_NO_COCKPIT``
    env var — the flag is the explicit, discoverable form the env var never
    had (issue #305): a throwaway project (scratch, ``/tmp``, a recipe) had no
    way to skip enrolment short of remembering an undocumented variable.

    Also refuses silently for a target under the OS temp directory: real
    machine registries have been found polluted with entries pointing at
    vanished ``tempfile.mkdtemp()`` directories — a manual smoke test or an
    escaped test isolation, never a project anyone meant to keep (#492, and
    seven more such entries found 2026-09-14). This guard is scoped to this
    *implicit* side effect of ``init``/``up`` only — explicit commands
    (``grimoire cockpit add``/``create``, ``grimoire serve --project-root``)
    are unaffected, since there the caller's intent is unambiguous.
    """
    from grimoire.tools.project_registry import is_scratch_path

    if no_cockpit or os.environ.get("GRIMOIRE_NO_COCKPIT"):
        # `%r` plutôt que `%s` (issue CodeQL py/log-injection) : `target` est un
        # nom de dossier choisi par l'appelant, et son affichage brut permettrait
        # d'y glisser un retour à la ligne pour forger une fausse entrée de log.
        # `repr()` échappe `\n`/`\r` au lieu de les émettre tels quels.
        logger.debug("cockpit auto-enrolment skipped for %r: GRIMOIRE_NO_COCKPIT/--no-cockpit", target)
        return
    if is_scratch_path(target):
        logger.debug("cockpit auto-enrolment skipped for %r: scratch path under the OS temp dir", target)
        return
    try:
        from grimoire.tools.project_registry import register_project

        slug = register_project(target, project_name)
    except OSError:
        return
    if slug and fmt != "json":
        console.print(
            f"[dim]Cockpit local : projet enregistré ([b]{slug}[/b]) — "
            "lance [b]grimoire cockpit[/b] pour gouverner tous tes projets.[/dim]"
        )


def validate_init_flags(archetype: str, backend: str, memory_profile: str) -> None:
    """Reject unknown archetype / backend / memory-profile values, with a hint.

    Lives here rather than in the CLI wiring module: the catalogs these values
    are checked against are declared in this file, and the wiring module is the
    one under a size ratchet.
    """
    if archetype:
        parts = [a.strip() for a in archetype.split(",") if a.strip()]
        invalid = [a for a in parts if a not in KNOWN_ARCHETYPES]
        if invalid:
            for a in invalid:
                console.print(f"[red]Unknown archetype:[/red] {a}")
                matches = difflib.get_close_matches(a, sorted(KNOWN_ARCHETYPES), n=2, cutoff=0.5)
                if matches:
                    console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
            console.print(f"Available: {', '.join(sorted(KNOWN_ARCHETYPES))}")
            raise typer.Exit(1)

    if backend not in KNOWN_BACKENDS:
        console.print(f"[red]Unknown backend:[/red] {backend}")
        matches = difflib.get_close_matches(backend, sorted(KNOWN_BACKENDS), n=2, cutoff=0.5)
        if matches:
            console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
        else:
            console.print(f"Available: {', '.join(sorted(KNOWN_BACKENDS))}")
        raise typer.Exit(1)

    if memory_profile and not memory_profiles.is_known(memory_profile):
        console.print(f"[red]Unknown memory profile:[/red] {memory_profile}")
        console.print(f"Available: {', '.join(memory_profiles.PROFILE_ORDER)}")
        raise typer.Exit(1)


def run_init(
    ctx: typer.Context,
    target: Path,
    *,
    name: str = "",
    archetype: str = "",
    backend: str = "auto",
    force: bool = False,
    dry_run: bool = False,
    qdrant_docker: bool = False,
    memory_profile: str = "",
    no_cockpit: bool = False,
    lite: bool = False,
    memory_collection: str = "",
    interactive: bool = False,
    memory_stack: str = "",
) -> None:
    """Execute the enhanced init flow: scan → resolve → wizard → scaffold → report.

    ``lite`` (``--lite``/``--profile lite``) is deprecated and kept only for
    backward compatibility: the light profile no longer exists (the installed
    experience is complete; the core adapts to each task), so it no longer
    changes anything here — the caller (``grimoire init``) already printed
    the deprecation notice. ``memory_stack == "up"`` is the explicit consent
    to start (Docker) the memory profile's missing services — see the Memory
    step below.
    """
    target = target.resolve()
    fmt = (ctx.obj or {}).get("output", "text")
    yes = (ctx.obj or {}).get("yes", False)

    if memory_profile and not memory_profiles.is_known(memory_profile):
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "error": f"unknown memory profile: {memory_profile}"}, indent=2))
        else:
            validate_init_flags("", "auto", memory_profile)
        raise typer.Exit(1)

    # Check existing project
    config_file = target / "project-context.yaml"
    if config_file.exists() and not force:
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "error": "project-context.yaml already exists"}, indent=2))
        else:
            console.print(f"[yellow]project-context.yaml already exists at {target}[/yellow]")
            console.print("Use [bold]--force[/bold] to overwrite.")
        raise typer.Exit(1)

    target.mkdir(parents=True, exist_ok=True)

    # Phase 1: Scan
    scanner = StackScanner(target)
    scan = scanner.scan()

    # Phase 2: Resolve backend
    requested_backend = backend
    qdrant_docker_requested = qdrant_docker
    has_tty = sys.stdin.isatty()
    is_interactive = (has_tty or interactive) and not yes and fmt != "json"
    # Detection is purely informational from here on (issue Grimoire-kit#496):
    # it used to decide the backend outright, silently attaching a fresh
    # project to whatever memory service happened to already be running on
    # the host — a shared collection, another project's memory. It is now
    # only *suggested* in the report, or offered as an explicit question in
    # the interactive wizard (`_choose_memory_profile`) — never applied by
    # `-y` or any other non-interactive call. Only probed when the backend is
    # actually left undecided — an explicit `--backend` needs no network
    # round-trip to be honored.
    detected_service = "local"
    memory_default_reason = ""
    if qdrant_docker_requested:
        backend = "qdrant-server"
    elif backend == "auto":
        detected_service = detect_memory_backend()
        backend = "lexical"
        # Onboarding decision 2026-09-18 (PR2, revised same day by Guilhem's
        # arbitrage on #619): the express/non-interactive path applies the
        # richest composition this machine can serve — `complet` included —
        # never a service merely *found* running (#496 stays intact: the
        # backend a `complet` recommendation writes is always the kit's own
        # fixed default, a fresh project-scoped collection, never whatever
        # `detect_memory_backend()` happened to find). Whether the missing
        # services actually get *started* is a separate consent question,
        # resolved below (``start_memory_stack_consent``) — this only ever
        # decides which profile gets written.
        if not memory_profile and not is_interactive:
            memory_profile, memory_default_reason = _recommend_memory_profile()
            if memory_profile == "standard":
                backend = "qdrant-local"
    # A composition that pins its own services decides the backend: asking for
    # `graphe` and landing on the detected qdrant would produce a config whose
    # graph layers point at a store that is not there.
    if memory_profile:
        backend = memory_profiles.resolve(memory_profile).resolve_backend(backend)

    # Phase 3: Resolve archetype
    resolver = ArchetypeResolver()
    # Parse comma-separated archetypes from CLI
    archetypes_override: list[str] | None = None
    if archetype:
        archetypes_override = [a.strip() for a in archetype.split(",") if a.strip()]
    resolved = resolver.resolve(
        scan,
        backend=backend,
        archetypes_override=archetypes_override,
    )

    # Phase 4: Interactive wizard or express mode
    project_name = name or target.name
    user_name = _git_user_name() or "Developer"
    language = "Français"
    skill_level = "intermediate"
    offline = False

    offer_qdrant_docker = requested_backend == "auto" and detected_service == "local"

    # Onboarding audit 2026-09-18, constat #1: without a real TTY, bare
    # `grimoire init` (no `-y`) used to run the express path in total
    # silence — indistinguishable from `-y` in its own output, with no sign
    # a choice was ever taken away. Named here, once, before the branch that
    # decides whether the wizard actually runs.
    if not is_interactive and not yes and not dry_run and fmt != "json" and not has_tty:
        console.print(
            "[dim]Express mode (no interactive terminal): "
            "`grimoire init --interactive` to rerun with guidance.[/dim]"
        )

    start_memory_stack_consent = False
    if is_interactive and not dry_run:
        wizard_result = _run_wizard(
            target,
            scan,
            resolved,
            backend,
            offer_qdrant_docker=offer_qdrant_docker,
            detected_service=detected_service,
        )
        project_name = wizard_result["project_name"]
        user_name = wizard_result["user_name"]
        language = wizard_result["language"]
        skill_level = wizard_result["skill_level"]
        offline = bool(wizard_result.get("offline", False))
        memory_profile = str(wizard_result.get("memory_profile", memory_profile))
        qdrant_docker_requested = qdrant_docker_requested or bool(wizard_result.get("qdrant_docker", False))
        start_memory_stack_consent = bool(wizard_result.get("start_memory_stack", False))
        # Re-resolve if user changed archetypes or backend
        new_archetypes = wizard_result.get("archetypes", [wizard_result.get("archetype", "minimal")])
        new_backend = wizard_result["backend"]
        current_archs = list(resolved.archetypes) if resolved.archetypes else [resolved.archetype]
        if set(new_archetypes) != set(current_archs) or new_backend != backend:
            resolved = resolver.resolve(
                scan,
                backend=new_backend,
                archetypes_override=new_archetypes,
            )
        backend = new_backend
    elif memory_profile == "complet":
        # Consent to *start* the missing services, for every non-interactive
        # path (Guilhem's arbitrage on #619, 2026-09-18): `-y` is itself the
        # explicit consent — the point of applying `complet` on this machine
        # is that its potential gets exploited right away, not configured
        # and left dormant — so Docker starts without asking anything.
        # `--memory-stack up` remains an equivalent, explicit override for a
        # bare non-TTY run that was not passed `-y` for other reasons. A
        # plain non-interactive run *without* either never starts a
        # container: `complet` still gets written (the report and `grimoire
        # doctor` both name the exact command to start it).
        start_memory_stack_consent = yes or memory_stack == "up"

    # Phase 4.5: Name the collection by project (issue Grimoire-kit#496) — a
    # shared backend used to get the fixed config default (or a hardcoded
    # `GrimoireMemory`), so every project on the same machine landed on the
    # very same collection. Only applies to backends that actually name one;
    # `local`/`lexical` already live under this project's own directory.
    collection_prefix = ""
    explicit_collection = memory_collection.strip()
    if backend in memory_profiles.VECTOR_BACKENDS:
        from grimoire.memory.taxonomy import slugify

        collection_prefix = explicit_collection or slugify(project_name, default="grimoire")
        if not explicit_collection and collection_has_content(backend, collection_prefix):
            message = (
                f"La collection « {collection_prefix} » existe déjà sur {backend} et n'est pas "
                "vide. Relancez avec --memory-collection <nom> pour vous y attacher "
                "explicitement — sans cette option, `grimoire init` n'attache jamais un "
                "projet neuf à une collection déjà peuplée."
            )
            if fmt == "json":
                typer.echo(json.dumps({"ok": False, "error": message}, indent=2))
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(1)

    # Phase 5: Plan
    scaffolder = ProjectScaffolder(
        target,
        project_name=project_name,
        user_name=user_name,
        language=language,
        skill_level=skill_level,
        scan=scan,
        resolved=resolved,
        backend=backend,
        offline=offline,
        force=force,
        profile=memory_profile,
        collection_prefix=collection_prefix,
    )
    plan = scaffolder.plan()

    # Dry-run — show plan and exit
    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({
                "dry_run": True,
                "directories": len(plan.directories),
                "copies": len(plan.copies),
                "templates": len(plan.templates),
                "archetype": resolved.archetype,
                "archetypes": list(resolved.archetypes) if resolved.archetypes else [resolved.archetype],
                "backend": backend,
                "stacks": [d.name for d in scan.stacks],
                "stack_agents": list(resolved.stack_agents),
                "feature_agents": list(resolved.feature_agents),
                "qdrant_docker": qdrant_docker_requested,
                "memory_collection": collection_prefix,
            }, indent=2))
        else:
            _display_dry_run(plan, target, project_name, resolved.archetype, resolved)
        return

    # Phase 6: Execute
    result = scaffolder.execute(plan)
    surfaces = sync_host_surfaces(target)
    if not surfaces.ok:
        console.print(f"[yellow]![/yellow] {surfaces.warning}")

    qdrant_docker_started = False
    qdrant_docker_message = ""
    if qdrant_docker_requested:
        if _is_qdrant_reachable():
            qdrant_docker_started = True
            qdrant_docker_message = "Qdrant est déjà disponible sur http://localhost:6333."
        else:
            qdrant_docker_started, qdrant_docker_message = _start_qdrant_docker(target)

    # Phase 6.5: Memory step closes with the equivalent of `memory up --apply`
    # plus a structural health check — never a full embedding-model load
    # (that cost belongs to the first real memory use, not to `init`).
    # Containers only ever start on consent (`start_memory_stack_consent`,
    # resolved above per the interactive/`-y`/`--memory-stack up`/bare-script
    # rules) — `complet` can be written without ever reaching this branch.
    memory_stack_messages: list[str] = []
    if start_memory_stack_consent:
        from grimoire.tools.memory_setup import start_memory_stack

        memory_stack_messages = start_memory_stack(memory_profile, target)
        if fmt != "json":
            for message in memory_stack_messages:
                console.print(f"[dim]{message}[/dim]")

    memory_summary = _memory_step_summary(
        target, applied_profile=memory_profile or "lexical", reason=memory_default_reason,
    )

    # Phase 7: Report
    if fmt == "json":
        docker_status = None
        if qdrant_docker_requested:
            docker_status = {
                "requested": True,
                "started": qdrant_docker_started,
                "message": qdrant_docker_message,
            }
        _display_json(
            target,
            result,
            resolved,
            scan,
            backend,
            project_name,
            qdrant_docker=docker_status,
            detected_service=detected_service,
            collection=collection_prefix,
            memory_summary=memory_summary,
            memory_stack_messages=memory_stack_messages,
        )
    else:
        _display_report(
            target,
            result,
            resolved,
            scan,
            backend,
            project_name,
            qdrant_docker_started=qdrant_docker_started,
            qdrant_docker_message=qdrant_docker_message,
            detected_service=detected_service,
            no_cockpit=no_cockpit,
            memory_summary=memory_summary,
        )

    _maybe_register_cockpit(target, project_name, fmt, no_cockpit=no_cockpit)


