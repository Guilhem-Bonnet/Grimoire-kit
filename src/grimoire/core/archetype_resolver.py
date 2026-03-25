"""Map detected stacks to archetype and agent selections."""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.scanner import ScanResult


@dataclass(frozen=True, slots=True)
class ResolvedArchetype:
    """Result of archetype resolution from a scan."""

    archetype: str
    stack_agents: tuple[str, ...]
    feature_agents: tuple[str, ...]
    reason: str


# Stack name → expert agent filename (without .md)
STACK_AGENT_MAP: dict[str, str] = {
    "go": "go-expert",
    "python": "python-expert",
    "javascript": "typescript-expert",
    "typescript": "typescript-expert",
    "docker": "docker-expert",
    "terraform": "terraform-expert",
    "kubernetes": "k8s-expert",
    "ansible": "ansible-expert",
}

# Known archetype IDs — keep in sync with cli/app.py _KNOWN_ARCHETYPES
_VALID_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

# Archetype selection rules — evaluated top to bottom, first match wins.
# Each rule: (required_stacks, archetype_name, human_reason)
_ARCHETYPE_RULES: list[tuple[frozenset[str], str, str]] = [
    (frozenset({"terraform"}), "infra-ops", "Terraform detected"),
    (frozenset({"kubernetes"}), "infra-ops", "Kubernetes detected"),
    (frozenset({"ansible"}), "infra-ops", "Ansible detected"),
    (frozenset({"react", "python"}), "web-app", "React + Python backend"),
    (frozenset({"react", "go"}), "web-app", "React + Go backend"),
    (frozenset({"vue", "python"}), "web-app", "Vue + Python backend"),
    (frozenset({"vue", "go"}), "web-app", "Vue + Go backend"),
    (frozenset({"react"}), "web-app", "React frontend"),
    (frozenset({"vue"}), "web-app", "Vue frontend"),
    (frozenset({"django"}), "web-app", "Django web framework"),
    (frozenset({"fastapi"}), "web-app", "FastAPI service"),
]


class ArchetypeResolver:
    """Resolve a ScanResult into archetype + agent selections."""

    def resolve(
        self,
        scan: ScanResult,
        *,
        backend: str = "local",
        archetype_override: str | None = None,
    ) -> ResolvedArchetype:
        detected = {d.name for d in scan.stacks}

        # Archetype — use override or auto-select
        if archetype_override:
            if archetype_override not in _VALID_ARCHETYPES:
                msg = f"Unknown archetype: {archetype_override!r}"
                raise ValueError(msg)
            archetype = archetype_override
            reason = f"User selected: {archetype_override}"
        else:
            archetype = "minimal"
            reason = "No specific stack pattern matched"
            for required, arch, desc in _ARCHETYPE_RULES:
                if required <= detected:
                    archetype = arch
                    reason = desc
                    break

        # Stack agents — deduplicated
        stack_agents: list[str] = []
        seen: set[str] = set()
        for stack_name in sorted(detected):
            agent = STACK_AGENT_MAP.get(stack_name)
            if agent and agent not in seen:
                stack_agents.append(agent)
                seen.add(agent)

        # Feature agents
        feature_agents: list[str] = []
        if backend in ("qdrant-local", "qdrant-server", "ollama"):
            feature_agents.append("vectus")

        return ResolvedArchetype(
            archetype=archetype,
            stack_agents=tuple(stack_agents),
            feature_agents=tuple(feature_agents),
            reason=reason,
        )
