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
        """Decision 2026-09-18 (Guilhem, corrected same day): resolve() never
        installs minimal automatically — an empty repo gets `stack` (Atlas,
        the kit's generalist for any stack without an asserted domain),
        flagged as a best guess since there is no signal to base it on."""
        result = self.resolver.resolve(_scan())
        assert result.archetype == "stack"
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

    def test_python_alone_gives_stack_not_a_guess(self) -> None:
        """`stack` (Atlas) is the deliberate, correct pick for a naked
        backend language — not a guess to second-guess (decision 2026-09-18,
        Guilhem, corrected same day: is_best_guess only for an empty repo)."""
        result = self.resolver.resolve(_scan("python"))
        assert result.archetype == "stack"
        assert result.is_best_guess is False
        assert "stack-python" in result.reason

    def test_go_docker_gives_stack_naming_both_skills(self) -> None:
        """Decision 2026-09-18 (Guilhem, corrected): a bare Dockerfile is not
        IaC (IaC means Terraform/K8s/Ansible/Helm) — go+docker naked falls to
        `stack`, naming both stack-go and stack-docker."""
        result = self.resolver.resolve(_scan("go", "docker"))
        assert result.archetype == "stack"
        assert result.is_best_guess is False
        assert "stack-go" in result.reason
        assert "stack-docker" in result.reason

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

    def test_stack_fallback_sets_archetypes_tuple(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetypes == ("stack",)
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
        assert result.archetype == "stack"
        assert "No specific stack pattern matched" not in result.reason
        assert "Python" in result.reason

    def test_node_naked_reason_is_specific_not_generic(self) -> None:
        result = self.resolver.resolve(_scan("javascript"))
        assert result.archetype == "stack"
        assert "No specific stack pattern matched" not in result.reason
        assert "Node" in result.reason

    def test_empty_scan_reason_names_discovery(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetype == "stack"
        assert "No specific stack pattern matched" not in result.reason


class TestRecommendNakedStack:
    """Named archetype used by resolve()'s fallback and the wizard/report —
    decision 2026-09-18 (Guilhem, corrected same day): never `minimal`, and
    `stack` (Atlas) — never `platform-engineering` — is the honest default
    for "any stack without an asserted domain"."""

    def test_empty_repo_is_a_guess(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan())
        assert hint.archetype == "stack"
        assert hint.propose_discovery is True
        assert hint.is_best_guess is True
        assert "README" in hint.reason

    def test_python_naked_names_python_and_its_skill_not_a_guess(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("python"))
        assert hint.language == "python"
        assert hint.archetype == "stack"
        assert hint.propose_discovery is True
        assert hint.is_best_guess is False
        assert "Python" in hint.reason
        assert "stack-python" in hint.reason

    def test_node_naked_names_node_and_its_skill_not_a_guess(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("javascript"))
        assert hint.language == "node"
        assert hint.archetype == "stack"
        assert hint.propose_discovery is True
        assert hint.is_best_guess is False
        assert "Node" in hint.reason
        assert "stack-typescript" in hint.reason

    def test_go_naked_names_go_and_its_skill(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("go"))
        assert hint.language == "go"
        assert hint.archetype == "stack"
        assert hint.is_best_guess is False
        assert "Go" in hint.reason
        assert "stack-go" in hint.reason

    def test_rust_naked_names_rust_with_no_dedicated_skill_yet(self) -> None:
        """No `stack-rust` skill exists in the DNA yet — Atlas still applies
        as the bare generalist, honestly said."""
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("rust"))
        assert hint.language == "rust"
        assert hint.archetype == "stack"
        assert hint.is_best_guess is False
        assert "Rust" in hint.reason

    def test_unrecognized_stack_still_falls_to_stack_not_minimal(self) -> None:
        """Called directly on a stack it doesn't specifically recognize
        (react — normally caught by a resolve() rule first), it still never
        falls back to minimal — `stack` is its generic catch-all."""
        from grimoire.core.archetype_resolver import recommend_naked_stack

        hint = recommend_naked_stack(_scan("react"))
        assert hint.archetype == "stack"
        assert hint.is_best_guess is False


class TestWeakSignalSuggestion:
    """A narrower, more confident 'almost there' hint — checked first in
    resolve()'s fallback, before the generic `stack` recommendation
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

    def test_bare_dockerfile_is_not_a_weak_signal_anymore(self, tmp_path: Path) -> None:
        """Decision 2026-09-18 (Guilhem, corrected): IaC means Terraform/K8s/
        Ansible/Helm — a bare Dockerfile alone is no longer treated as an
        infra-ops signal; it falls through to `stack` (naming stack-docker)
        via recommend_naked_stack() instead."""
        from grimoire.core.archetype_resolver import weak_signal_suggestion

        scan = _scan("docker")
        scan = ScanResult(stacks=scan.stacks, project_type=scan.project_type, root=tmp_path)
        assert weak_signal_suggestion(scan) is None

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
    """Decision 2026-09-18 (Guilhem, product owner, corrected same day):
    `resolve()` never installs `minimal` on its own, for any naked/ambiguous/
    empty stack — only an explicit `-a minimal` override may still produce
    it. `stack` (Atlas) is the deliberate fallback, flagged as a best guess
    only for a truly empty repo."""

    def setup_method(self) -> None:
        self.resolver = ArchetypeResolver()

    @pytest.mark.parametrize(
        "stacks",
        [("python",), ("javascript",), ("rust",), ("go",), ("java",), ("ruby",)],
    )
    def test_naked_language_gives_stack_not_a_guess(self, stacks: tuple[str, ...]) -> None:
        result = self.resolver.resolve(_scan(*stacks))
        assert result.archetype == "stack"
        assert result.is_best_guess is False

    def test_empty_repo_gives_stack_as_a_guess(self) -> None:
        result = self.resolver.resolve(_scan())
        assert result.archetype == "stack"
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


class TestNakedStackReasonCitesRealMarkers:
    """Copilot review on PR #617: the naked-stack reason text named a fixed
    marker list per language (e.g. "pyproject.toml/setup.py/requirements.txt"
    for every Python project, "Dockerfile" for every Docker project), but the
    scanner (`_FILE_MARKERS`, scanner.py) recognises each stack from several
    alternative markers — the fixed text could claim a file that was never on
    disk. The reason must instead name whichever marker(s) `StackDetection.
    evidence` actually recorded for that scan."""

    def test_python_reason_names_the_evidence_actually_matched(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        scan = ScanResult(
            stacks=(StackDetection(name="python", confidence=0.7, evidence=("poetry.lock", "Pipfile")),),
            project_type="generic",
            root=Path("/fake"),
        )
        hint = recommend_naked_stack(scan)
        assert "poetry.lock" in hint.reason
        assert "Pipfile" in hint.reason
        assert "pyproject.toml" not in hint.reason
        assert "setup.py" not in hint.reason

    def test_node_reason_names_the_evidence_actually_matched(self) -> None:
        from grimoire.core.archetype_resolver import recommend_naked_stack

        scan = ScanResult(
            stacks=(StackDetection(name="javascript", confidence=0.6, evidence=("yarn.lock",)),),
            project_type="generic",
            root=Path("/fake"),
        )
        hint = recommend_naked_stack(scan)
        assert "yarn.lock" in hint.reason
        assert "package.json" not in hint.reason

    def test_docker_reason_names_the_evidence_actually_matched(self) -> None:
        """Same defect as Python, reported for Docker: `weak_signal_suggestion`
        used to say "Dockerfile detected" unconditionally (issue now moot,
        that branch was removed — a bare Dockerfile is no longer a weak
        signal); `recommend_naked_stack`'s own Docker-specific reason (the
        code path a Docker-only project now actually takes) must cite real
        evidence the same way."""
        from grimoire.core.archetype_resolver import recommend_naked_stack

        scan = ScanResult(
            stacks=(
                StackDetection(name="docker", confidence=0.8, evidence=("docker-compose.yml", ".dockerignore")),
            ),
            project_type="generic",
            root=Path("/fake"),
        )
        hint = recommend_naked_stack(scan)
        assert "docker-compose.yml" in hint.reason
        assert ".dockerignore" in hint.reason
        assert "Dockerfile" not in hint.reason
