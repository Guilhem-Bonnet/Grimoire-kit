"""Tests for bmad.core.scanner — automatic stack detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.core.scanner import StackScanner


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    return tmp_path


# ── Empty project ─────────────────────────────────────────────────────────────


class TestEmptyProject:
    def test_no_stacks(self, empty_dir: Path) -> None:
        result = StackScanner(empty_dir).scan()
        assert result.stacks == ()
        assert result.project_type == "generic"

    def test_root_is_set(self, empty_dir: Path) -> None:
        result = StackScanner(empty_dir).scan()
        assert result.root == empty_dir


# ── Python project ────────────────────────────────────────────────────────────


class TestPythonProject:
    def test_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[build-system]\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "python" in names

    def test_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "python" in names

    def test_multiple_markers_boost_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / ".python-version").write_text("3.12")
        result = StackScanner(tmp_path).scan()
        py = next(s for s in result.stacks if s.name == "python")
        assert py.confidence > 0.7
        assert len(py.evidence) >= 3


# ── Go project ────────────────────────────────────────────────────────────────


class TestGoProject:
    def test_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/m\n")
        result = StackScanner(tmp_path).scan()
        go = next(s for s in result.stacks if s.name == "go")
        assert go.confidence >= 0.9


# ── Rust project ──────────────────────────────────────────────────────────────


class TestRustProject:
    def test_cargo(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        result = StackScanner(tmp_path).scan()
        r = next(s for s in result.stacks if s.name == "rust")
        assert r.confidence >= 0.9


# ── Java project ──────────────────────────────────────────────────────────────


class TestJavaProject:
    def test_pom(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project/>")
        result = StackScanner(tmp_path).scan()
        j = next(s for s in result.stacks if s.name == "java")
        assert j.confidence >= 0.8

    def test_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "java" in names


# ── Ruby project ──────────────────────────────────────────────────────────────


class TestRubyProject:
    def test_gemfile(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "ruby" in names


# ── C# project ────────────────────────────────────────────────────────────────


class TestCsharpProject:
    def test_csproj(self, tmp_path: Path) -> None:
        (tmp_path / "MyApp.csproj").write_text("<Project/>")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "csharp" in names


# ── Docker project ────────────────────────────────────────────────────────────


class TestDockerProject:
    def test_dockerfile(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "docker" in names

    def test_compose(self, tmp_path: Path) -> None:
        (tmp_path / "docker-compose.yml").write_text("services:\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "docker" in names


# ── Terraform project ─────────────────────────────────────────────────────────


class TestTerraformProject:
    def test_tf_files(self, tmp_path: Path) -> None:
        (tmp_path / "main.tf").write_text("resource {}")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "terraform" in names
        assert result.project_type == "infrastructure"


# ── Kubernetes project ────────────────────────────────────────────────────────


class TestKubernetesProject:
    def test_k8s_dir(self, tmp_path: Path) -> None:
        (tmp_path / "k8s").mkdir()
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "kubernetes" in names
        assert result.project_type == "infrastructure"


# ── Ansible project ───────────────────────────────────────────────────────────


class TestAnsibleProject:
    def test_ansible_cfg(self, tmp_path: Path) -> None:
        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "ansible" in names


# ── TypeScript project ────────────────────────────────────────────────────────


class TestTypeScriptProject:
    def test_tsconfig(self, tmp_path: Path) -> None:
        (tmp_path / "tsconfig.json").write_text("{}")
        (tmp_path / "package.json").write_text("{}")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "typescript" in names
        assert "javascript" in names


# ── React project ─────────────────────────────────────────────────────────────


class TestReactProject:
    def test_react_app(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.tsx").write_text("export default () => <div/>;")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "react" in names
        assert result.project_type == "webapp"


# ── Django project ────────────────────────────────────────────────────────────


class TestDjangoProject:
    def test_manage_py(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "django" in names
        assert result.project_type == "webapp"


# ── Multi-stack project ───────────────────────────────────────────────────────


class TestMultiStack:
    def test_python_docker_k8s(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "Dockerfile").write_text("")
        (tmp_path / "k8s").mkdir()
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "python" in names
        assert "docker" in names
        assert "kubernetes" in names
        assert result.project_type == "infrastructure"

    def test_stacks_sorted_by_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("")  # 0.9
        (tmp_path / ".python-version").write_text("3.12")  # 0.3
        result = StackScanner(tmp_path).scan()
        assert len(result.stacks) >= 2
        confidences = [s.confidence for s in result.stacks]
        assert confidences == sorted(confidences, reverse=True)


# ── Confidence cap ────────────────────────────────────────────────────────────


class TestConfidenceCap:
    def test_confidence_capped_at_1(self, tmp_path: Path) -> None:
        # Create all Python markers at once
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "setup.py").write_text("")
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "Pipfile").write_text("")
        (tmp_path / "poetry.lock").write_text("")
        (tmp_path / ".python-version").write_text("3.12")
        result = StackScanner(tmp_path).scan()
        py = next(s for s in result.stacks if s.name == "python")
        assert py.confidence <= 1.0


# ── Threshold ──────────────────────────────────────────────────────────────────


class TestThreshold:
    def test_low_signal_filtered(self, tmp_path: Path) -> None:
        """A single weak marker (conf < 0.3) should not appear."""
        # .python-version alone = 0.3 → should appear at threshold
        (tmp_path / ".python-version").write_text("3.12")
        result = StackScanner(tmp_path).scan()
        names = [s.name for s in result.stacks]
        assert "python" in names
