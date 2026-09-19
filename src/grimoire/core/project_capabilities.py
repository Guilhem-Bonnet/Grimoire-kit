"""What a project has not exploited yet — one source of truth for two surfaces.

Onboarding audit 2026-09-18 (constat #3 and the doctor/status gap in level 1
of the target experience) found the same problem twice: the post-install
"Next Steps" panel (``grimoire init``/``up``) never varied with what was
actually installed, and ``grimoire doctor``/``status`` never named a single
capability the project hadn't exploited yet, even though cockpit, governed
standard profiles, semantic memory and archetype specializations all already
exist and are all documented elsewhere. This module computes the same hint
list for both call sites so they can never disagree.

Every probe here is best-effort and silent on failure: a hint is a nudge, not
a diagnostic — it must never turn a healthy ``doctor``/``status`` red, and it
must never block ``init``/``up``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grimoire.core.standard_profile_manifest import read_profile
from grimoire.tools.project_registry import slug_for_path

_STANDARD_PROFILE_MARKER = "_grimoire/standard/standard-profile.yaml"

#: Archetypes whose own DNA discusses team/organisation-scale governance
#: (traceability, observability-by-design, security-first at a homelab-or-bigger
#: scale) — a bare `starter` profile under one of these is a sharper gap than
#: under `minimal`/`web-app`/`creative-studio`/`fix-loop`, which make no such
#: claim about who runs the project.
_TEAM_SCALE_ARCHETYPES = frozenset({"agentic-standard", "infra-ops", "platform-engineering"})

#: Memory backends this hint considers "not yet upgraded" — matches the
#: default a fresh ``init``/``up`` always lands on (issue #496: never
#: attached to a detected service silently). Used only as a fallback when a
#: caller has no ``layer_profile`` to pass (see :func:`memory_upgrade_target`).
_UNUPGRADED_BACKENDS = frozenset({"lexical", "local"})


@dataclass(frozen=True, slots=True)
class CapabilityHint:
    """One unexploited capability: what it is, and the command to reach it."""

    label: str
    command: str


def cockpit_registered(target: Path) -> bool:
    """Best-effort: is *target* already known to the local cockpit registry?

    A registry read failure reads as "not registered" — this is a nudge, not
    a diagnostic, and must never raise into ``doctor``/``status``/``init``.
    """
    try:
        return slug_for_path(target) is not None
    except OSError:
        return False


def standard_profile(target: Path) -> str | None:
    """The installed agentic-standard profile id, or ``None`` if not installed."""
    try:
        return read_profile(target / _STANDARD_PROFILE_MARKER)
    except (OSError, ValueError):
        return None


def memory_upgrade_target(layer_profile: str) -> str | None:
    """The next memory profile tier above *layer_profile* this machine could
    serve, or ``None`` when it is already there.

    ``"the machine could serve"`` means: a local embedding engine
    (fastembed/sentence-transformers, or a reachable Ollama) makes anything
    above ``lexical`` possible at all, and a reachable Docker daemon makes
    ``complet`` reachable on top of that — the same ladder
    :func:`grimoire.tools.memory_setup.recommend_profile` climbs for
    ``grimoire init``'s Memory step. Never asserts a specific service (e.g.
    Weaviate) is running right now — that is what `grimoire memory status`
    is for; this is a hint, not a diagnostic (module docstring).
    """
    from grimoire.memory import profiles as memory_profiles
    from grimoire.tools.memory_setup import docker_daemon_reachable, local_embedding_available

    if not local_embedding_available():
        feasible = "lexical"
    elif docker_daemon_reachable():
        feasible = "complet"
    else:
        feasible = "standard"

    order = memory_profiles.PROFILE_ORDER
    current = layer_profile if layer_profile in order else "lexical"
    if order.index(feasible) > order.index(current):
        return feasible
    return None


def unexploited_hints(
    target: Path,
    *,
    archetype: str,
    backend: str,
    no_cockpit: bool = False,
    layer_profile: str = "",
) -> list[CapabilityHint]:
    """Capabilities this project has not exploited yet, most relevant first.

    Empty when everything checked here is already in use — callers render
    nothing in that case (silent, per the 'découvrir' footer contract), never
    an empty panel section.

    ``layer_profile`` (the config's ``memory.layer_profile``, when the caller
    has it) drives the memory hint: it is compared against
    :func:`memory_upgrade_target` instead of the older "is it still
    lexical?" check, so a project already on ``standard`` still gets pointed
    at ``complet`` once Docker becomes available. Left empty, this falls back
    to the previous *backend*-only heuristic for callers that predate it.
    """
    hints: list[CapabilityHint] = []

    if archetype == "minimal":
        hints.append(CapabilityHint(
            "archetype still minimal (universal base, no specialization)",
            "grimoire registry list",
        ))

    profile = standard_profile(target)
    if profile is None:
        hints.append(CapabilityHint(
            "agentic governance not started (standard profile)",
            "grimoire standard needs",
        ))
    elif profile == "starter" and archetype in _TEAM_SCALE_ARCHETYPES:
        hints.append(CapabilityHint(
            f"'starter' profile below target for a {archetype} archetype (team/enterprise)",
            "grimoire standard init --profile governed",
        ))

    effective_profile = layer_profile or ("lexical" if backend in _UNUPGRADED_BACKENDS else "standard")
    upgrade_to = memory_upgrade_target(effective_profile)
    if upgrade_to:
        hints.append(CapabilityHint(
            f"memory profile '{effective_profile}' is below what this machine can serve ({upgrade_to})",
            f"grimoire memory up --profile {upgrade_to} --apply",
        ))

    if not no_cockpit and not cockpit_registered(target):
        hints.append(CapabilityHint(
            "multi-project cockpit not opened yet",
            "grimoire cockpit",
        ))

    return hints


def discover_footer_line(hints: list[CapabilityHint]) -> str | None:
    """The 'Découvrir' footer for ``grimoire doctor``/``status`` — ``None`` when
    *hints* is empty, so the footer stays silent once everything is exploited.
    """
    if not hints:
        return None
    commands = " · ".join(f"`{h.command}`" for h in hints)
    return f"Découvrir : {commands}"
