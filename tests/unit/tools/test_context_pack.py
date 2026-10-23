"""Tests du context-pack durable de repo (porté d'un hook d'atelier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.tools import context_pack as cp

pytestmark = pytest.mark.covers("cli:context-pack")


def _fixed_now() -> datetime:
    return datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


class TestContextPack:
    def test_conforms_to_catalogue_contract(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        required = {
            "mission_id",
            "context_profile",
            "budget",
            "objective",
            "included_sources",
            "constraints",
        }
        assert required <= set(pack)
        assert pack["contract"] == "context-pack"
        assert pack["schemaVersion"] == cp.CONTEXT_PACK_SCHEMA_VERSION

    def test_included_and_excluded_sources(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("gouvernance\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        included = {s["path"] for s in pack["included_sources"]}
        excluded = {s["path"] for s in pack["excluded_sources"]}
        assert "CLAUDE.md" in included
        assert "README.md" in excluded  # absent → exclu
        gov = next(s for s in pack["included_sources"] if s["path"] == "CLAUDE.md")
        assert gov["confidence"] == "high"
        assert "sha256" in gov

    def test_sufficiency_and_open_questions(self, tmp_path: Path) -> None:
        # Sans source de gouvernance → partial + question ouverte.
        (tmp_path / "pyproject.toml").write_text("[x]\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        assert pack["scorecard"]["sufficiency"] == "partial"
        assert pack["open_questions"]
        # Avec README → sufficient, pas de question.
        (tmp_path / "README.md").write_text("# R\n", encoding="utf-8")
        pack2 = cp.build_context_pack(tmp_path, now=_fixed_now())
        assert pack2["scorecard"]["sufficiency"] == "sufficient"
        assert pack2["open_questions"] == []

    def test_expiry_ttl(self, tmp_path: Path) -> None:
        pack = cp.build_context_pack(tmp_path, now=_fixed_now(), ttl_days=7)
        assert pack["expiry"]["ttlDays"] == 7
        assert pack["expiry"]["expiresAt"] == "2026-07-28T12:00:00+00:00"
        assert pack["expiry"]["invalidateOn"] == "git HEAD change"

    def test_default_output_path(self, tmp_path: Path) -> None:
        out = cp.default_output_path(tmp_path)
        assert out.parent.name == "repo-contexts"
        assert out.name.endswith(".context-pack.json")


class TestOutputIgnored:
    """Issue #701 : la sortie par défaut vit dans le dépôt ciblé, git doit l'ignorer."""

    @staticmethod
    def _git_repo(path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q", str(path)], check=True)

    def test_default_output_is_added_to_the_target_gitignore(self, tmp_path: Path) -> None:
        import subprocess

        self._git_repo(tmp_path)
        out = cp.default_output_path(tmp_path)
        written = cp.ensure_output_ignored(tmp_path, out)
        assert written == tmp_path / ".gitignore"
        assert "_grimoire-runtime-output/repo-contexts/" in written.read_text(encoding="utf-8")
        out.parent.mkdir(parents=True)
        out.write_text("{}\n", encoding="utf-8")
        status = subprocess.run(
            ["git", "-C", str(tmp_path), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "repo-contexts" not in status
        assert status.strip() == "?? .gitignore"

    def test_existing_gitignore_is_appended_once(self, tmp_path: Path) -> None:
        self._git_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        out = cp.default_output_path(tmp_path)
        assert cp.ensure_output_ignored(tmp_path, out) is not None
        assert cp.ensure_output_ignored(tmp_path, out) is None
        text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert text.startswith("*.pyc\n")
        assert text.count("_grimoire-runtime-output/repo-contexts/") == 1

    def test_already_ignored_path_leaves_gitignore_alone(self, tmp_path: Path) -> None:
        self._git_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("_grimoire-runtime-output/\n", encoding="utf-8")
        assert cp.ensure_output_ignored(tmp_path, cp.default_output_path(tmp_path)) is None
        assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "_grimoire-runtime-output/\n"

    def test_not_a_git_repo_or_output_outside_root_does_nothing(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        assert cp.ensure_output_ignored(plain, cp.default_output_path(plain)) is None
        assert not (plain / ".gitignore").exists()
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git_repo(repo)
        assert cp.ensure_output_ignored(repo, tmp_path / "elsewhere.json") is None
        assert not (repo / ".gitignore").exists()

    def test_cli_writes_the_pack_and_the_gitignore(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grimoire.cli.app import app

        self._git_repo(tmp_path)
        (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
        result = CliRunner().invoke(app, ["context-pack", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert cp.default_output_path(tmp_path).is_file()
        assert "_grimoire-runtime-output/repo-contexts/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
