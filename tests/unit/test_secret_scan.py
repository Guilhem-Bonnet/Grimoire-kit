"""Tests comportementaux du scan de secrets et de l'audit des workflows (D-08)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
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


def audit(
    tmp_path: Path,
    content: str | bytes,
    name: str = "w.yml",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
        env=env,
        timeout=60,
        check=False,
    )


def workflow(step: str, on: str = "[pull_request]", perms: bool = True) -> str:
    """Workflow minimal à un job dont l'unique étape est ``step`` (YAML déjà indenté de 6)."""
    head = f"name: x\non: {on}\n" + ("permissions:\n  contents: read\n" if perms else "")
    return head + f"jobs:\n  a:\n    runs-on: x\n    steps:\n{step}"


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


UNTRUSTED_RUN = [
    'echo "${{ github.event.pull_request.title }}"',
    "git checkout ${{ github.head_ref }}",
    "echo ${{ github.ref_name }}",
    "echo ${{ github.base_ref }}",
    "echo ${{ github.ref }}",
    "echo ${{ inputs.name }}",
    "echo ${{ github.event.inputs.name }}",
    'echo "${{ toJSON(github.event) }}"',
    "echo ${{ toJSON(github) }}",
    "echo ${{ github.event['pull_request']['title'] }}",
    "echo ${{ github['event'].pull_request.title }}",
    "echo ${{ inputs['name'] }}",
    "echo ${{ format('{0}', github.event.pull_request.title) }}",
    "echo ${{ join(github.event.commits.*.message, ' ') }}",
    "echo ${{ format('}}', github.event.pull_request.title) }}",
    "echo ${{ 'a' || github.head_ref }}",
    "echo ${{ github . event . pull_request.title }}",
]


@pytest.mark.parametrize("command", UNTRUSTED_RUN)
def test_untrusted_interpolation_in_run_fails(tmp_path: Path, command: str) -> None:
    res = audit(tmp_path, workflow("      - run: |\n          " + command + "\n"))
    assert res.returncode == 1, res.stdout
    assert "untrusted context" in res.stdout


def test_untrusted_interpolation_in_github_script_fails(tmp_path: Path) -> None:
    step = "      - uses: actions/github-script@v7\n        with:\n          script: |\n"
    step += "            core.info(`${{ format('{0}', github.event.pull_request.title) }}`)\n"
    res = audit(tmp_path, workflow(step))
    assert res.returncode == 1
    assert "untrusted context" in res.stdout


@pytest.mark.parametrize(
    "command",
    [
        "echo ${{ github.sha }}",
        "echo ${{ github.event_name }}",
        "echo ${{ github.repository }}",
        "echo ${{ matrix.os }}",
        "echo ${{ steps.x.outputs.inputs }}",
        "echo ${{ 'mentions github.event.title and inputs.x in a literal' }}",
        "echo ${{ format('{0}', 'it''s github.head_ref') }}",
    ],
)
def test_trusted_interpolation_in_run_passes(tmp_path: Path, command: str) -> None:
    res = audit(tmp_path, workflow("      - run: |\n          " + command + "\n"))
    assert res.returncode == 0, res.stdout


def test_untrusted_value_through_env_passes(tmp_path: Path) -> None:
    step = (
        "      - env:\n          TITLE: ${{ github.event.pull_request.title }}\n"
        '          REF: ${{ toJSON(github.event) }}\n        run: echo "$TITLE" "$REF"\n'
    )
    assert audit(tmp_path, workflow(step)).returncode == 0


TARGET = "\n  pull_request_target:\n    types: [opened]"


def _checkout(ref_line: str) -> str:
    return f"      - uses: actions/checkout@v7\n        with:\n          {ref_line}\n"


@pytest.mark.parametrize(
    "step",
    [
        _checkout("ref: ${{ github.event.pull_request.head.sha }}"),
        _checkout("ref: refs/pull/${{ github.event.number }}/merge"),
        _checkout("ref: ${{ github.event.after }}"),
        _checkout("ref: ${{ github.head_ref }}"),
        _checkout("ref: refs/pull/123/merge"),
        _checkout("repository: ${{ github.event.pull_request.head.repo.full_name }}"),
        _checkout("ref: ${{ format('refs/pull/{0}/head', github.event.number) }}"),
        '      - env:\n          N: ${{ github.event.number }}\n        run: gh pr checkout "$N"\n',
        "      - run: gh pr co 12\n",
        '      - env:\n          N: ${{ github.event.number }}\n        run: git fetch origin "pull/$N/head"\n',
    ],
    ids=[
        "head-sha",
        "refs-pull-number",
        "event-after",
        "head-ref",
        "refs-pull-literal",
        "head-repo",
        "format-wrapped",
        "gh-pr-checkout",
        "gh-pr-co",
        "git-fetch-pull",
    ],
)
def test_pull_request_target_checking_out_pr_code_fails(tmp_path: Path, step: str) -> None:
    res = audit(tmp_path, workflow(step, on=TARGET))
    assert res.returncode == 1, res.stdout
    assert "checks out PR code under pull_request_target" in res.stdout


@pytest.mark.parametrize(
    "step",
    [
        "      - uses: actions/checkout@v7\n",
        _checkout("ref: refs/heads/main"),
        _checkout("ref: ${{ github.sha }}"),
        _checkout("ref: heads/release-head"),
        "      - run: git fetch origin main\n",
    ],
    ids=["default", "main", "github-sha", "head-word-alone", "fetch-main"],
)
def test_pull_request_target_without_pr_checkout_passes(tmp_path: Path, step: str) -> None:
    res = audit(tmp_path, workflow(step, on=TARGET))
    assert res.returncode == 0, res.stdout


def test_head_checkout_is_allowed_outside_pull_request_target(tmp_path: Path) -> None:
    step = _checkout("ref: ${{ github.event.pull_request.head.sha }}")
    assert audit(tmp_path, workflow(step)).returncode == 0


NO_PERMS = "name: x\non: [push]\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo hi\n"


def test_workflow_without_any_permissions_block_fails(tmp_path: Path) -> None:
    res = audit(tmp_path, NO_PERMS)
    assert res.returncode == 1
    assert "no permissions block" in res.stdout


def test_one_job_without_permissions_fails_when_workflow_declares_none(tmp_path: Path) -> None:
    content = (
        "name: x\non: [push]\njobs:\n"
        "  a:\n    runs-on: x\n    permissions:\n      contents: read\n    steps: []\n"
        "  b:\n    runs-on: x\n    steps: []\n"
    )
    res = audit(tmp_path, content)
    assert res.returncode == 1
    assert "Job 'b' has no permissions block" in res.stdout
    assert "Job 'a'" not in res.stdout


def test_permissions_on_every_job_passes(tmp_path: Path) -> None:
    content = (
        "name: x\non: [push]\njobs:\n"
        "  a:\n    runs-on: x\n    permissions:\n      contents: read\n    steps: []\n"
        "  b:\n    runs-on: x\n    permissions: {}\n    steps: []\n"
    )
    assert audit(tmp_path, content).returncode == 0


def test_workflow_level_empty_permissions_passes(tmp_path: Path) -> None:
    assert audit(tmp_path, NO_PERMS.replace("jobs:", "permissions: {}\njobs:")).returncode == 0


def test_output_is_ascii_even_with_accented_names(tmp_path: Path) -> None:
    content = (
        "name: x\non: [push]\njobs:\n"
        "  déploiement:\n    runs-on: x\n    permissions:\n      contents: write\n    steps: []\n"
        "  publié:\n    runs-on: x\n    permissions:\n      contents: write\n    steps: []\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    res = audit(tmp_path, content, env=env)
    assert res.returncode == 1
    assert "Traceback" not in res.stderr, res.stderr
    assert "d\\xe9ploiement" in res.stdout
    assert "publi\\xe9" in res.stdout
    assert res.stdout.isascii()
    assert "FAILED: 2 error(s)" in res.stdout


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


def test_scan_uses_the_repository_gitleaks_config() -> None:
    text = (WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8")
    assert "--config .gitleaks.toml" in text


def _config() -> dict[str, Any]:
    return tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))


def test_gitleaks_config_extends_defaults_and_allowlists_fixture_paths() -> None:
    cfg = _config()
    assert cfg["extend"]["useDefault"] is True
    paths = [p for al in cfg["allowlists"] for p in al["paths"]]
    assert paths, "aucune exemption par chemin"
    assert all(p.startswith("^") and p.endswith("$") for p in paths), "chemins non ancrés"


def test_gitleaksignore_only_lists_fingerprints_in_expected_shape() -> None:
    lines = [
        line
        for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert lines
    for line in lines:
        commit, _path, _rule, lineno = line.split(":")
        assert len(commit) == 40 and lineno.isdigit(), line


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, encoding="utf-8", env=env)


def _scan_repo(tmp_path: Path, relpath: str) -> int:
    """Commite un faux jeton à ``relpath`` dans un dépôt jetable et lance gitleaks avec la config du kit."""
    repo = tmp_path / "repo"
    target = repo / relpath
    target.parent.mkdir(parents=True)
    # Assemblé à l'exécution : aucun littéral de jeton dans ce fichier.
    target.write_text("TOKEN = '" + "ghp_" + "aB3dE5gH7jK9" * 3 + "'\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", relpath)
    _git(repo, "commit", "-q", "-m", "fixture")
    res = subprocess.run(
        [
            "gitleaks",
            "git",
            "--config",
            str(ROOT / ".gitleaks.toml"),
            "--no-banner",
            "--redact",
            "--exit-code",
            "1",
            str(repo),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    return res.returncode


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks absent")
def test_allowlisted_fixture_path_is_exempt_without_any_fingerprint(tmp_path: Path) -> None:
    # Le commit est créé ici, son SHA ne figure dans aucun .gitleaksignore : comme après un squash.
    assert _scan_repo(tmp_path, "tests/unit/memory/test_write_validation.py") == 0


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks absent")
def test_same_token_outside_allowlisted_path_is_still_a_leak(tmp_path: Path) -> None:
    assert _scan_repo(tmp_path, "src/grimoire/new_module.py") == 1
