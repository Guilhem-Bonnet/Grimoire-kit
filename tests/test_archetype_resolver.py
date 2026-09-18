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

    def test_empty_scan_never_gives_minimal(self) -> None:
        """Decision 2026-09-18 (Guilhem): resolve() never installs minimal
        automatically — an empty repo gets the kit's most general specialized
        archetype instead, flagged as a best guess."""
        result = self.resolver.resolve(_scan())
        assert result.archetype == "platform-engineering"
        assert result.is_best_guess is True

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

    def test_python_alone_gives_platform_engineering_best_guess(self) -> None:
        result = self.resolver.resolve(_scan("python"))
        assert result.archetype == "platform-engineering"
        assert result.is_best_guess is True

    def test_go_docker_gives_infra_ops_via_weak_signal(self) -> None:
        """A bare Dockerfile is a more confident weak signal than the
        generic language fallback — checked first (see resolve())."""
        result = self.resolver.resolve(_scan("go", "docker"))
        assert result.archetype == "infra-ops"
        assert result.is_best_guess is True

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
        assert "stack-engineer" in result.stack_agents

    def test_python_produces_python_expert(self) -> None:
        result = self.resolver.resolve(_scan("python"))
        assert "stack-engineer" in result.stack_agents

    def test_typescript_produces_typescript_expert(self) -> None:
        result = self.resolver.resolve(_scan("typescript"))
        assert "stack-engineer" in result.stack_agents

    def test_javascript_produces_typescript_expert(self) -> None:
        """JavaScript maps to typescript-expert (same agent)."""
        result = self.resolver.resolve(_scan("javascript"))
        assert "stack-engineer" in result.stack_agents

    def test_no_duplicate_typescript_expert(self) -> None:
        """js et ts détectés ensemble ne livrent qu'un seul généraliste de pile."""
        result = self.resolver.resolve(_scan("javascript", "typescript"))
        assert result.stack_agents.count("stack-engineer") == 1

    def test_all_infra_stacks_produce_agents(self) -> None:
        result = self.resolver.resolve(_scan("terraform", "kubernetes", "ansible", "docker"))
        assert "stack-engineer" in result.stack_agents

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

    def test_archetypes_override_accepts_agentic_standard(self) -> None:
        result = self.resolver.resolve(_scan(), archetypes_override=["minimal", "agentic-standard"])
        assert result.archetype == "minimal"
        assert result.archetypes == ("minimal", "agentic-standard")
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

    def test_best_guess_sets_archetypes_tuple(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetypes == ("platform-engineering",)
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

    # ── Naked-stack recommendation (onboarding 60s, never minimal in silence) ──

    def test_python_naked_reason_is_specific_not_generic(self) -> None:
        """Never the opaque 'No specific stack pattern matched', and never minimal."""
        result = self.resolver.resolve(_scan("python"))
        assert result.archetype == "platform-engineering"
        assert "No specific stack pattern matched" not in result.reason
        assert "Python" in result.reason

    def test_node_naked_reason_is_specific_not_generic(self) -> None:
        result = self.resolver.resolve(_scan("javascript"))
        assert result.archetype == "web-app"
        assert "No specific stack pattern matched" not in result.reason
        assert "Node" in result.reason

    def test_empty_scan_reason_names_discovery(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetype == "platform-engineering"
        assert "No specific stack pattern matched" not in result.reason


class TestRecommendNakedStack:
    """Best-guess specialized archetype used by resolve()'s fallback and the
    wizard/report — decision 2026-09-18 (Guilhem): never minimal."""

    def test_empty_repo_proposes_discovery(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan())
        assert hint.archetype == "platform-engineering"
        assert hint.propose_discovery is True
        assert "README" in hint.reason

    def test_python_naked_names_python_and_proposes_discovery(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("python"))
        assert hint.language == "python"
        assert hint.archetype == "platform-engineering"
        assert hint.propose_discovery is True
        assert "Python" in hint.reason

    def test_node_naked_names_node_and_proposes_discovery(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("javascript"))
        assert hint.language == "node"
        assert hint.archetype == "web-app"
        assert hint.propose_discovery is True
        assert "Node" in hint.reason

    def test_rust_naked_names_rust(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("rust"))
        assert hint.language == "rust"
        assert hint.archetype == "platform-engineering"
        assert "Rust" in hint.reason

    def test_go_naked_names_go(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("go"))
        assert hint.language == "go"
        assert hint.archetype == "platform-engineering"
        assert "Go" in hint.reason

    def test_specialized_stack_recommendation_not_needed_directly(self) -> None:
        """A rule-matched stack is never routed through the naked-stack path by
        resolve() itself — recommend_naked_stack() is a pure fallback helper,
        callers only reach for it when resolve() already fell through."""
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("react"))
        # Called directly on a stack it doesn't specifically recognize, it
        # still never falls back to minimal — platform-engineering is its
        # generic catch-all.
        assert hint.archetype == "platform-engineering"


class TestWeakSignalSuggestion:
    """A narrower, more confident 'almost there' hint — checked first in
    resolve()'s fallback, before the generic language-based recommendation
    (onboarding audit 2026-09-18, level 1, express-path suggestion line)."""

    def test_bundler_in_package_json_suggests_web_app(self, tmp_path: Path) -> None:
        import json as _json

        from grimoire.core.archetype_resolver import weak_signal_suggestion

        (tmp_path / "package.json").write_text(
            _json.dumps({"devDependencies": {"vite": "^5.0.0", "jest": "^29.0.0"}}),
            encoding="utf-8",
        )
        scan = _scan("javascript", project_type="generic")
        scan = ScanResult(stacks=scan.stacks, project_type=scan.project_type, root=tmp_path)
        hint = weak_signal_suggestion(scan)
        assert hint is not None
        archetype, reason = hint
        assert archetype == "web-app"
        assert "vite" in reason or "webpack" in reason

    def test_bare_dockerfile_suggests_infra_ops(self, tmp_path: Path) -> None:
        from grimoire.core.archetype_resolver import weak_signal_suggestion

        scan = _scan("docker")
        scan = ScanResult(stacks=scan.stacks, project_type=scan.project_type, root=tmp_path)
        hint = weak_signal_suggestion(scan)
        assert hint is not None
        assert hint[0] == "infra-ops"

    def test_ci_and_tests_suggest_fix_loop(self, tmp_path: Path) -> None:
        from grimoire.core.archetype_resolver import weak_signal_suggestion

        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
        scan = _scan("python")
        scan = ScanResult(stacks=scan.stacks, project_type=scan.project_type, root=tmp_path)
        hint = weak_signal_suggestion(scan)
        assert hint is not None
        assert hint[0] == "fix-loop"

    def test_no_signal_returns_none(self, tmp_path: Path) -> None:
        from grimoire.core.archetype_resolver import weak_signal_suggestion

        scan = _scan("python")
        scan = ScanResult(stacks=scan.stacks, project_type=scan.project_type, root=tmp_path)
        assert weak_signal_suggestion(scan) is None


class TestNeverMinimalAutomatically:
    """Decision 2026-09-18 (Guilhem, product owner): `resolve()` never installs
    `minimal` on its own, for any naked/ambiguous/empty stack — only an
    explicit `-a minimal` override may still produce it."""

    def setup_method(self) -> None:
        self.resolver = ArchetypeResolver()

    @pytest.mark.parametrize(
        "stacks",
        [(), ("python",), ("javascript",), ("rust",), ("go",), ("java",), ("ruby",)],
    )
    def test_auto_detection_never_yields_minimal(self, stacks: tuple[str, ...]) -> None:
        result = self.resolver.resolve(_scan(*stacks))
        assert result.archetype != "minimal"
        assert result.is_best_guess is True

    def test_explicit_override_can_still_choose_minimal(self) -> None:
        """The one escape hatch: `-a minimal` (demo/test), unaffected by the
        auto-detection ban above."""
        result = self.resolver.resolve(_scan("python"), archetype_override="minimal")
        assert result.archetype == "minimal"
        assert result.is_best_guess is False

    def test_rule_matched_stack_is_not_flagged_as_best_guess(self) -> None:
        result = self.resolver.resolve(_scan("terraform"))
        assert result.archetype == "infra-ops"
        assert result.is_best_guess is False
