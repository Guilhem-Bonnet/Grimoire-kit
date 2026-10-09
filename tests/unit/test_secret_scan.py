"""Tests comportementaux du scan de secrets et de l'audit des workflows (D-08)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "audit-workflow-permissions.py"
WORKFLOWS = ROOT / ".github" / "workflows"

CLEAN = """\
name: ok
on: [push]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""


def audit(tmp_path: Path, content: str | bytes, name: str = "w.yml") -> subprocess.CompletedProcess[str]:
    wf = tmp_path / "workflows"
    wf.mkdir(exist_ok=True)
    path = wf / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(wf)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_clean_workflow_passes(tmp_path: Path) -> None:
    assert audit(tmp_path, CLEAN).returncode == 0


@pytest.mark.parametrize(
    "content",
    [
        "name: x\non: [push]\npermissions: write-all\njobs: {}\n",
        "name: x\non: [push]\njobs:\n  a:\n    runs-on: x\n    permissions: write-all\n    steps: []\n",
        "name: x\non: [push]\npermissions:\n  contents: write\njobs: {}\n",
        "name: x\non: [push]\njobs:\n  a:\n    runs-on: x\n    permissions:\n      contents: write\n    steps: []\n",
        "name: x\non: [push]\npermissions:\n  actions: write\n  pull-requests: write\n"
        "  security-events: write\n  deployments: write\njobs: {}\n",
    ],
    ids=["write-all", "job-write-all", "contents-write", "job-contents-write", "other-writes"],
)
def test_excessive_permissions_fail(tmp_path: Path, content: str) -> None:
    assert audit(tmp_path, content, "unlisted.yml").returncode == 1


def test_allowlisted_write_passes(tmp_path: Path) -> None:
    content = "name: x\non: [push]\npermissions:\n  contents: write\n  id-token: write\njobs: {}\n"
    assert audit(tmp_path, content, "publish.yml").returncode == 0


def test_allowlist_does_not_cover_other_permissions(tmp_path: Path) -> None:
    content = "name: x\non: [push]\npermissions:\n  contents: write\n  packages: write\njobs: {}\n"
    assert audit(tmp_path, content, "publish.yml").returncode == 1


@pytest.mark.parametrize(
    "content",
    [b"a: [unclosed\n", b"- one\n- two\n", b"", b"name: x\non: [push]\njobs: [a, b]\n"],
    ids=["invalid-yaml", "list-root", "empty", "jobs-not-mapping"],
)
def test_unparsable_workflow_fails(tmp_path: Path, content: bytes) -> None:
    assert audit(tmp_path, content).returncode == 1


def test_non_utf8_locale_does_not_open_hole(tmp_path: Path) -> None:
    wf = tmp_path / "workflows"
    wf.mkdir()
    (wf / "acc.yml").write_text(
        "# commentaire accentué é\nname: x\non: [push]\npermissions: write-all\njobs: {}\n",
        encoding="utf-8",
    )
    import os

    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "PYTHONIOENCODING": "ascii"}
    res = subprocess.run(
        [sys.executable, str(SCRIPT), str(wf)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 1
    assert "write-all" in res.stdout


def test_untrusted_interpolation_in_run_fails(tmp_path: Path) -> None:
    content = (
        "name: x\non: [pull_request]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        '      - run: echo "${{ github.event.pull_request.title }}"\n'
    )
    assert audit(tmp_path, content).returncode == 1


def test_head_ref_interpolation_in_run_fails(tmp_path: Path) -> None:
    content = (
        "name: x\non: [pull_request]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - run: git checkout ${{ github.head_ref }}\n"
    )
    assert audit(tmp_path, content).returncode == 1


def test_untrusted_value_through_env_passes(tmp_path: Path) -> None:
    content = (
        "name: x\non: [pull_request]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - env:\n          TITLE: ${{ github.event.pull_request.title }}\n"
        '        run: echo "$TITLE"\n'
    )
    assert audit(tmp_path, content).returncode == 0


def test_pull_request_target_with_head_checkout_fails(tmp_path: Path) -> None:
    content = (
        "name: x\non:\n  pull_request_target:\n    types: [opened]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - uses: actions/checkout@v7\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
    )
    assert audit(tmp_path, content).returncode == 1


def test_pull_request_target_without_head_checkout_passes(tmp_path: Path) -> None:
    content = (
        "name: x\non:\n  pull_request_target:\n    types: [opened]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - uses: actions/checkout@v7\n"
    )
    assert audit(tmp_path, content).returncode == 0


def test_missing_or_empty_directory_fails(tmp_path: Path) -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "absent")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert res.returncode == 1
    (tmp_path / "empty").mkdir()
    res = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "empty")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert res.returncode == 1


def test_repository_workflows_pass_audit() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT), str(WORKFLOWS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr


# --- secret-scan.yml -------------------------------------------------------


def _scan() -> dict[Any, Any]:
    data = yaml.safe_load((WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers() -> dict[Any, Any]:
    scan = _scan()
    trig = scan.get("on", scan.get(True))
    assert isinstance(trig, dict)
    return trig


def test_scan_runs_on_every_pull_request() -> None:
    trig = _triggers()
    assert "pull_request" in trig
    pr = trig["pull_request"] or {}
    assert isinstance(pr, dict)
    assert "paths" not in pr and "paths-ignore" not in pr


def test_scan_reads_full_history_with_pinned_verified_binary() -> None:
    text = (WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8")
    assert "gitleaks/gitleaks-action" not in text
    assert "fetch-depth: 0" in text
    assert "sha256sum" in text
    assert "gitleaks git" in text and "--exit-code 1" in text


def test_scan_actions_aligned_and_deps_pinned() -> None:
    text = (WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8")
    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v4" not in text
    assert "actions/checkout@v7" in text and "actions/setup-python@v7" in text
    assert "PyYAML==" in text
