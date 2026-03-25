"""Tests for core/archetype_resolver.py — stack→archetype mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.archetype_resolver import ArchetypeResolver, ResolvedArchetype
from grimoire.core.scanner import ScanResult, StackDetection


def _scan(*stacks: str, project_type: str = "generic") -> ScanResult:
    """Build a minimal ScanResult from stack names."""
    return ScanResult(
        stacks=tuple(StackDetection(name=s, confidence=0.9, evidence=(f"{s}-marker",)) for s in stacks),
        project_type=project_type,
        root=Path("/fake"),
    )


class TestArchetypeResolver:
    def setup_method(self) -> None:
        self.resolver = ArchetypeResolver()

    # ── Archetype selection ───────────────────────────────────────────

    def test_empty_scan_gives_minimal(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetype == "minimal"

    def test_terraform_gives_infra_ops(self) -> None:
        result = self.resolver.resolve(_scan("terraform"))
        assert result.archetype == "infra-ops"

    def test_kubernetes_gives_infra_ops(self) -> None:
        result = self.resolver.resolve(_scan("kubernetes"))
        assert result.archetype == "infra-ops"

    def test_ansible_gives_infra_ops(self) -> None:
        result = self.resolver.resolve(_scan("ansible"))
        assert result.archetype == "infra-ops"

    def test_react_gives_web_app(self) -> None:
        result = self.resolver.resolve(_scan("react"))
        assert result.archetype == "web-app"

    def test_vue_gives_web_app(self) -> None:
        result = self.resolver.resolve(_scan("vue"))
        assert result.archetype == "web-app"

    def test_django_gives_web_app(self) -> None:
        result = self.resolver.resolve(_scan("django"))
        assert result.archetype == "web-app"

    def test_fastapi_gives_web_app(self) -> None:
        result = self.resolver.resolve(_scan("fastapi"))
        assert result.archetype == "web-app"

    def test_react_python_gives_web_app(self) -> None:
        result = self.resolver.resolve(_scan("react", "python"))
        assert result.archetype == "web-app"

    def test_infra_takes_priority_over_web(self) -> None:
        """Terraform is checked before React in the rules."""
        result = self.resolver.resolve(_scan("terraform", "react"))
        assert result.archetype == "infra-ops"

    def test_python_alone_gives_minimal(self) -> None:
        result = self.resolver.resolve(_scan("python"))
        assert result.archetype == "minimal"

    def test_go_docker_gives_minimal(self) -> None:
        result = self.resolver.resolve(_scan("go", "docker"))
        assert result.archetype == "minimal"

    # ── Override ──────────────────────────────────────────────────────

    def test_override_ignores_auto_detection(self) -> None:
        result = self.resolver.resolve(_scan("terraform"), archetype_override="web-app")
        assert result.archetype == "web-app"
        assert "User selected" in result.reason

    def test_override_with_empty_string_falls_through(self) -> None:
        result = self.resolver.resolve(_scan("terraform"), archetype_override="")
        # Empty string is falsy → no override
        assert result.archetype == "infra-ops"

    def test_override_with_invalid_archetype_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown archetype"):
            self.resolver.resolve(_scan("python"), archetype_override="../../etc")

    # ── Stack agents ──────────────────────────────────────────────────

    def test_go_produces_go_expert(self) -> None:
        result = self.resolver.resolve(_scan("go"))
        assert "go-expert" in result.stack_agents

    def test_python_produces_python_expert(self) -> None:
        result = self.resolver.resolve(_scan("python"))
        assert "python-expert" in result.stack_agents

    def test_typescript_produces_typescript_expert(self) -> None:
        result = self.resolver.resolve(_scan("typescript"))
        assert "typescript-expert" in result.stack_agents

    def test_javascript_produces_typescript_expert(self) -> None:
        """JavaScript maps to typescript-expert (same agent)."""
        result = self.resolver.resolve(_scan("javascript"))
        assert "typescript-expert" in result.stack_agents

    def test_no_duplicate_typescript_expert(self) -> None:
        """Both js and ts should produce only one typescript-expert."""
        result = self.resolver.resolve(_scan("javascript", "typescript"))
        assert result.stack_agents.count("typescript-expert") == 1

    def test_all_infra_stacks_produce_agents(self) -> None:
        result = self.resolver.resolve(_scan("terraform", "kubernetes", "ansible", "docker"))
        assert "terraform-expert" in result.stack_agents
        assert "k8s-expert" in result.stack_agents
        assert "ansible-expert" in result.stack_agents
        assert "docker-expert" in result.stack_agents

    def test_unknown_stack_produces_no_agent(self) -> None:
        result = self.resolver.resolve(_scan("rust"))
        assert len(result.stack_agents) == 0

    def test_stack_agents_are_sorted(self) -> None:
        result = self.resolver.resolve(_scan("python", "go", "docker"))
        assert list(result.stack_agents) == sorted(result.stack_agents)

    # ── Feature agents ────────────────────────────────────────────────

    def test_local_backend_no_feature_agents(self) -> None:
        result = self.resolver.resolve(_scan(), backend="local")
        assert len(result.feature_agents) == 0

    def test_qdrant_local_produces_vectus(self) -> None:
        result = self.resolver.resolve(_scan(), backend="qdrant-local")
        assert "vectus" in result.feature_agents

    def test_qdrant_server_produces_vectus(self) -> None:
        result = self.resolver.resolve(_scan(), backend="qdrant-server")
        assert "vectus" in result.feature_agents

    def test_ollama_produces_vectus(self) -> None:
        result = self.resolver.resolve(_scan(), backend="ollama")
        assert "vectus" in result.feature_agents

    def test_auto_backend_no_feature_agents(self) -> None:
        result = self.resolver.resolve(_scan(), backend="auto")
        assert len(result.feature_agents) == 0

    # ── Result structure ──────────────────────────────────────────────

    def test_result_is_frozen(self) -> None:
        result = self.resolver.resolve(_scan())
        assert isinstance(result, ResolvedArchetype)
        with pytest.raises(AttributeError):
            result.archetype = "changed"  # type: ignore[misc]

    def test_reason_is_always_set(self) -> None:
        result = self.resolver.resolve(_scan())
        assert len(result.reason) > 0

    # ── Multi-archetype support ───────────────────────────────────────

    def test_archetypes_override_single(self) -> None:
        result = self.resolver.resolve(_scan(), archetypes_override=["web-app"])
        assert result.archetype == "web-app"
        assert result.archetypes == ("web-app",)
        assert not result.is_composite

    def test_archetypes_override_multiple(self) -> None:
        result = self.resolver.resolve(_scan(), archetypes_override=["web-app", "infra-ops"])
        assert result.archetype == "web-app"  # first = primary
        assert result.archetypes == ("web-app", "infra-ops")
        assert result.is_composite

    def test_archetypes_override_three(self) -> None:
        result = self.resolver.resolve(
            _scan(),
            archetypes_override=["web-app", "infra-ops", "fix-loop"],
        )
        assert result.archetypes == ("web-app", "infra-ops", "fix-loop")
        assert result.is_composite
        assert result.archetype == "web-app"

    def test_archetypes_override_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown archetype"):
            self.resolver.resolve(_scan(), archetypes_override=["web-app", "bogus"])

    def test_archetypes_override_takes_priority_over_single(self) -> None:
        result = self.resolver.resolve(
            _scan("terraform"),
            archetype_override="web-app",
            archetypes_override=["fix-loop", "infra-ops"],
        )
        # archetypes_override wins over archetype_override
        assert result.archetypes == ("fix-loop", "infra-ops")
        assert result.archetype == "fix-loop"

    def test_auto_detect_sets_archetypes_tuple(self) -> None:
        result = self.resolver.resolve(_scan("terraform"))
        assert result.archetypes == ("infra-ops",)
        assert not result.is_composite

    def test_minimal_sets_archetypes_tuple(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetypes == ("minimal",)
        assert not result.is_composite

    # ── suggest_archetypes ────────────────────────────────────────────

    def test_suggest_archetypes_empty_scan(self) -> None:
        suggestions = self.resolver.suggest_archetypes(_scan())
        assert suggestions == ["minimal"]

    def test_suggest_archetypes_terraform(self) -> None:
        suggestions = self.resolver.suggest_archetypes(_scan("terraform"))
        assert "infra-ops" in suggestions

    def test_suggest_archetypes_react(self) -> None:
        suggestions = self.resolver.suggest_archetypes(_scan("react"))
        assert "web-app" in suggestions

    def test_suggest_archetypes_no_duplicates(self) -> None:
        suggestions = self.resolver.suggest_archetypes(_scan("react", "vue", "django"))
        assert len(suggestions) == len(set(suggestions))
