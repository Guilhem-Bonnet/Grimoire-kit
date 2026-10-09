"""Tests du garde scripts/check-actions-pinned.py.

Un garde sans contrôle négatif n'est pas un garde : chaque forme que le parsing
ligne à ligne laissait passer (valeur sur plusieurs lignes, ``.yaml``, ``uses:``
de job, workflow illisible) a son test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-actions-pinned.py"
SHA = "a83e7850d592aacd9ed61157eb88bafe1c4b8289"


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _workflow(root: Path, body: str, name: str = "test.yml") -> Path:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _steps(*lines: str) -> str:
    body = "\n".join(f"      {line}" for line in lines)
    return f"name: T\non: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{body}\n"


class TestPinning:
    def test_unpinned_actions_detected(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps("- uses: actions/checkout@v1"))
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "actions/checkout@v1" in result.stderr

    def test_pinned_actions_pass(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps(f"- uses: actions/checkout@{SHA}  # v7"))
        result = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "✓" in result.stdout

    def test_multiple_unpinned_actions(self, tmp_path: Path) -> None:
        _workflow(
            tmp_path,
            _steps(
                "- uses: actions/checkout@v4",
                "- uses: actions/setup-python@v3",
                "- uses: pypa/gh-action-pypi-publish@release/v1",
                "- uses: dtolnay/rust-toolchain@stable",
            ),
        )
        result = _run(tmp_path)
        assert result.returncode != 0
        for ref in (
            "checkout@v4",
            "setup-python@v3",
            "gh-action-pypi-publish@release/v1",
            "rust-toolchain@stable",
        ):
            assert ref in result.stderr

    def test_mixed_pinned_and_unpinned(self, tmp_path: Path) -> None:
        _workflow(
            tmp_path,
            _steps(
                f"- uses: actions/checkout@{SHA}  # v7",
                "- uses: actions/setup-python@v1",
            ),
        )
        result = _run(tmp_path)
        assert result.returncode != 0
        assert "setup-python@v1" in result.stderr
        assert f"checkout@{SHA}" not in result.stderr


class TestF1Unverifiable:
    """F1 : un workflow qui ne peut pas être lu ne doit jamais valoir « conforme »."""

    @pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="chmod 000 sans effet sous Windows ou en root",
    )
    def test_unreadable_workflow_fails(self, tmp_path: Path) -> None:
        wf = _workflow(tmp_path, _steps("- uses: actions/checkout@v1"), "e.yml")
        wf.chmod(0)
        try:
            result = _run(tmp_path)
        finally:
            wf.chmod(0o644)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "non vérifi" in result.stderr
        assert "✓" not in result.stdout

    def test_directory_named_yml_fails(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps(f"- uses: actions/checkout@{SHA}  # v7"))
        (tmp_path / ".github" / "workflows" / "evil.yml").mkdir()
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "evil.yml" in result.stderr
        assert "✓" not in result.stdout

    def test_invalid_yaml_fails(self, tmp_path: Path) -> None:
        _workflow(tmp_path, "jobs: [unclosed\n")
        result = _run(tmp_path)
        assert result.returncode != 0
        assert "non vérifi" in result.stderr


class TestF2YamlForms:
    """F2 : le parsing YAML voit ce que le test de sous-chaîne ligne à ligne manquait."""

    def test_value_on_next_line(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps("- uses:", "    actions/checkout@v1"))
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "actions/checkout@v1" in result.stderr

    def test_yaml_extension_scanned(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps("- uses: actions/checkout@v1"), "x.yaml")
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "x.yaml" in result.stderr

    def test_empty_workflow_dir_fails(self, tmp_path: Path) -> None:
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "aucun workflow" in result.stderr

    def test_job_level_uses_checked(self, tmp_path: Path) -> None:
        _workflow(
            tmp_path,
            "name: T\non: push\njobs:\n  call:\n    uses: org/repo/.github/workflows/w.yml@main\n",
        )
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "w.yml@main" in result.stderr

    def test_job_level_uses_pinned_passes(self, tmp_path: Path) -> None:
        _workflow(
            tmp_path,
            f"name: T\non: push\njobs:\n  call:\n    uses: org/repo/.github/workflows/w.yml@{SHA}  # v2\n",
        )
        result = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr


class TestF5FalsePositives:
    """F5 : seules les valeurs de la clé `uses` comptent."""

    def test_local_action_exempt(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps("- uses: ./local-action"))
        result = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_comment_and_run_text_ignored(self, tmp_path: Path) -> None:
        _workflow(
            tmp_path,
            _steps(
                "# uses: actions/checkout@v1",
                "- run: 'echo \"uses: foo\"'",
                f"- uses: actions/checkout@{SHA}  # v7",
            ),
        )
        result = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_docker_without_digest_rejected(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps("- uses: docker://alpine:3.19"))
        result = _run(tmp_path)
        assert result.returncode != 0
        assert "docker://alpine:3.19" in result.stderr

    def test_docker_with_digest_passes(self, tmp_path: Path) -> None:
        digest = "0" * 64
        _workflow(tmp_path, _steps(f"- uses: docker://alpine@sha256:{digest}  # 3.19"))
        result = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr


class TestF6VersionComment:
    """F6 : un SHA sans commentaire de version est illisible et Dependabot ne le suit pas."""

    def test_pin_without_version_comment_fails(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps(f"- uses: actions/checkout@{SHA}"))
        result = _run(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "commentaire de version" in result.stderr

    def test_empty_comment_fails(self, tmp_path: Path) -> None:
        _workflow(tmp_path, _steps(f"- uses: actions/checkout@{SHA} #"))
        result = _run(tmp_path)
        assert result.returncode != 0


class TestF4Root:
    """F4 : le garde ne dépend plus du cwd."""

    def test_default_root_is_repo_not_cwd(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_current_repo_workflows(self) -> None:
        result = _run(REPO_ROOT)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "✓" in result.stdout

    def test_ci_runs_the_guard(self) -> None:
        ci = (REPO_ROOT / ".github" / "workflows" / "ci-sdk.yml").read_text(encoding="utf-8")
        assert "scripts/check-actions-pinned.py" in ci
        assert '".github/workflows/**"' in ci
