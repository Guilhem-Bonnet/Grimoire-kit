"""Repli des piles nues (#617, lot onboarding) — `recommend_naked_stack`,
`weak_signal_suggestion` et `ArchetypeResolver.resolve` sans règle confiante.

Doublon volontaire et compact de `tests/test_archetype_resolver.py` : la
couverture publiée ne compte que `tests/unit/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.archetype_resolver import (
    ArchetypeResolver,
    recommend_naked_stack,
    weak_signal_suggestion,
)
from grimoire.core.scanner import ScanResult, StackDetection


def _scan(root: Path, *stacks: tuple[str, tuple[str, ...]]) -> ScanResult:
    return ScanResult(
        stacks=tuple(StackDetection(name=n, confidence=0.9, evidence=ev) for n, ev in stacks),
        project_type="generic",
        root=root,
    )


class TestRecommendNakedStack:
    def test_empty_repo_is_stack_and_the_only_best_guess(self, tmp_path: Path) -> None:
        hint = recommend_naked_stack(_scan(tmp_path))
        assert hint.archetype == "stack"
        assert hint.is_best_guess is True
        assert hint.propose_discovery is True
        assert "No stack marker" in hint.reason

    def test_python_names_the_real_markers_and_the_tests_dir(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
        hint = recommend_naked_stack(_scan(tmp_path, ("python", ("poetry.lock", "Pipfile"))))
        assert hint.archetype == "stack"
        assert hint.is_best_guess is False
        assert hint.language == "python"
        assert "poetry.lock, Pipfile" in hint.reason
        assert "with a tests/ directory" in hint.reason
        assert "stack-python" in hint.reason
        assert "pyproject.toml" not in hint.reason

    def test_node_names_the_declared_test_runner(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"jest": "^29"}}), encoding="utf-8")
        hint = recommend_naked_stack(
            _scan(tmp_path, ("javascript", ("package.json",)), ("typescript", ("tsconfig.json",)))
        )
        assert hint.language == "node"
        assert "package.json, tsconfig.json (jest)" in hint.reason
        assert "stack-typescript" in hint.reason

    def test_node_with_unreadable_package_json_has_no_runner_note(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
        hint = recommend_naked_stack(_scan(tmp_path, ("javascript", ("package.json",))))
        assert "(" in hint.reason and "jest" not in hint.reason

    @pytest.mark.parametrize(
        ("stack", "evidence", "language", "fragment"),
        [
            ("go", ("go.mod",), "go", "Go project detected (go.mod)"),
            ("rust", ("Cargo.toml",), "rust", "no dedicated stack skill exists yet for Rust"),
        ],
    )
    def test_go_and_rust(
        self, tmp_path: Path, stack: str, evidence: tuple[str, ...], language: str, fragment: str
    ) -> None:
        hint = recommend_naked_stack(_scan(tmp_path, (stack, evidence)))
        assert hint.archetype == "stack"
        assert hint.language == language
        assert fragment in hint.reason
        assert hint.is_best_guess is False

    def test_docker_alone_is_stack_not_infra_and_cites_real_markers(self, tmp_path: Path) -> None:
        hint = recommend_naked_stack(_scan(tmp_path, ("docker", ("docker-compose.yml", ".dockerignore"))))
        assert hint.archetype == "stack"
        assert "Docker detected (docker-compose.yml, .dockerignore)" in hint.reason
        assert "stack-docker" in hint.reason
        assert "Dockerfile" not in hint.reason

    def test_other_stack_falls_to_the_generic_branch(self, tmp_path: Path) -> None:
        hint = recommend_naked_stack(_scan(tmp_path, ("java", ("pom.xml",))))
        assert hint.archetype == "stack"
        assert "Detected stack(s) (java)" in hint.reason
        assert "no dedicated stack skill yet" in hint.reason

    def test_missing_root_never_raises(self) -> None:
        hint = recommend_naked_stack(_scan(Path("/nonexistent/for/sure"), ("python", ("pyproject.toml",))))
        assert hint.archetype == "stack"
        assert "with a tests/" not in hint.reason


class TestWeakSignalSuggestion:
    def test_bundler_in_package_json_points_at_web_app(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"vite": "^5"}}), encoding="utf-8")
        assert weak_signal_suggestion(_scan(tmp_path, ("javascript", ("package.json",)))) == (
            "web-app",
            "frontend tooling detected (vite) with no confirmed framework",
        )

    def test_ci_plus_tests_points_at_fix_loop(self, tmp_path: Path) -> None:
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
        suggestion = weak_signal_suggestion(_scan(tmp_path, ("python", ("pyproject.toml",))))
        assert suggestion is not None
        assert suggestion[0] == "fix-loop"

    def test_ci_without_tests_is_no_signal(self, tmp_path: Path) -> None:
        (tmp_path / "Jenkinsfile").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        assert weak_signal_suggestion(_scan(tmp_path, ("python", ("pyproject.toml",)))) is None

    def test_package_json_without_bundler_is_no_signal(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "4"}}), encoding="utf-8")
        assert weak_signal_suggestion(_scan(tmp_path, ("javascript", ("package.json",)))) is None


class TestResolveWithoutConfidentRule:
    def test_weak_signal_wins_over_the_generic_fallback_and_is_a_guess(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"webpack": "5"}}), encoding="utf-8")
        resolved = ArchetypeResolver().resolve(_scan(tmp_path, ("javascript", ("package.json",))))
        assert resolved.archetype == "web-app"
        assert resolved.is_best_guess is True

    def test_naked_python_resolves_to_stack_not_a_guess(self, tmp_path: Path) -> None:
        resolved = ArchetypeResolver().resolve(_scan(tmp_path, ("python", ("requirements.txt",))))
        assert resolved.archetype == "stack"
        assert resolved.archetypes == ("stack",)
        assert resolved.is_best_guess is False
        assert "requirements.txt" in resolved.reason

    def test_empty_repo_resolves_to_stack_flagged_as_guess(self, tmp_path: Path) -> None:
        resolved = ArchetypeResolver().resolve(_scan(tmp_path))
        assert resolved.archetype == "stack"
        assert resolved.is_best_guess is True

    def test_explicit_minimal_stays_reachable(self, tmp_path: Path) -> None:
        resolved = ArchetypeResolver().resolve(_scan(tmp_path), archetype_override="minimal")
        assert resolved.archetype == "minimal"
        assert resolved.is_best_guess is False
