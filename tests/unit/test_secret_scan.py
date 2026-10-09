"""Tests comportementaux du scan de secrets et de l'audit des workflows (D-08)."""

from __future__ import annotations

import os
import re
import shlex
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


# --- clés env souillées, casse, sorties d'étape (revue D08-T2-03 / T2-04) ---------------

TITLE_ENV = "          TITLE: ${{ github.event.issue.title }}\n"


def _job_env(env_lines: str, run: str, on: str = "[issues]") -> str:
    """Workflow dont le JOB porte ``env`` et dont l'étape exécute ``run``."""
    return (
        f"name: x\non: {on}\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n"
        f"    env:\n{env_lines}    steps:\n      - run: |\n          {run}\n"
    )


@pytest.mark.parametrize(
    "content",
    [
        _job_env(TITLE_ENV, 'echo "${{ env.TITLE }}"'),
        _job_env(TITLE_ENV, "echo ${{ env['TITLE'] }}"),
        _job_env(TITLE_ENV, "echo ${{ ENV.title }}"),
        _job_env(TITLE_ENV, "echo ${{ toJSON(env) }}"),
        _job_env(TITLE_ENV + "          COPY: ${{ env.TITLE }}\n", "echo ${{ env.COPY }}"),
        "name: x\non: [issues]\npermissions:\n  contents: read\nenv:\n"
        + TITLE_ENV
        + "jobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo ${{ env.TITLE }}\n",
        "name: x\non: [issues]\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - env:\n          TITLE: ${{ github.event.issue.title }}\n        run: echo ${{ env.TITLE }}\n",
    ],
    ids=["job-env", "bracket", "case", "whole-env", "chained", "workflow-env", "step-env"],
)
def test_env_value_read_back_through_expression_fails(tmp_path: Path, content: str) -> None:
    res = audit(tmp_path, content)
    assert res.returncode == 1, res.stdout
    assert "untrusted context" in res.stdout


def test_untainted_env_key_read_through_expression_passes(tmp_path: Path) -> None:
    content = _job_env(TITLE_ENV + "          MODE: release\n", "echo ${{ env.MODE }}")
    assert audit(tmp_path, content).returncode == 0


def test_env_value_read_as_shell_variable_passes(tmp_path: Path) -> None:
    assert audit(tmp_path, _job_env(TITLE_ENV, 'echo "$TITLE"')).returncode == 0


@pytest.mark.parametrize(
    "command",
    ['echo "${{ steps.meta.outputs.t }}"', 'echo "${{ needs.build.outputs.t }}"'],
)
def test_step_and_job_outputs_in_run_are_reported(tmp_path: Path, command: str) -> None:
    res = audit(tmp_path, workflow("      - run: |\n          " + command + "\n"))
    assert "WARNING" in res.stdout and "outputs" in res.stdout, res.stdout
    assert res.returncode == 0  # signalé, non bloquant : le dépôt en contient déjà


def test_env_tainted_checkout_ref_under_pull_request_target_fails(tmp_path: Path) -> None:
    content = (
        f"name: x\non:{TARGET}\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n"
        "    env:\n      PR_SHA: ${{ github.event.pull_request.head.sha }}\n    steps:\n"
        + _checkout("ref: ${{ env.PR_SHA }}")
    )
    res = audit(tmp_path, content)
    assert res.returncode == 1, res.stdout
    assert "checks out PR code under pull_request_target" in res.stdout


@pytest.mark.parametrize(
    "env_line",
    [
        "REF: refs/pull/${{ github.event.number }}/head",
        "REF: ${{ github.head_ref }}",
        "REF: ${{ github.event.pull_request.head.ref }}",
    ],
    ids=["pull-in-env", "head-ref-in-env", "event-ref-in-env"],
)
@pytest.mark.parametrize("use", ['"$REF"', '"${REF}"'], ids=["dollar", "braces"])
def test_fetching_a_pr_ref_carried_by_env_fails(tmp_path: Path, env_line: str, use: str) -> None:
    step = f"      - env:\n          {env_line}\n        run: git fetch origin {use}\n"
    res = audit(tmp_path, workflow(step, on=TARGET))
    assert res.returncode == 1, res.stdout
    assert "checks out PR code under pull_request_target" in res.stdout


def test_fetching_a_trusted_ref_carried_by_env_passes(tmp_path: Path) -> None:
    step = '      - env:\n          REF: main\n        run: git fetch origin "$REF"\n'
    assert audit(tmp_path, workflow(step, on=TARGET)).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "echo ${{ GITHUB.EVENT.issue.title }}",
        "echo ${{ Github.Head_Ref }}",
        "echo ${{ INPUTS.x }}",
        "echo ${{ GitHub.Event_Name }} ${{ Github.REF_NAME }}",
        "echo ${{ toJSON(GITHUB) }}",
    ],
)
def test_context_names_are_case_insensitive(tmp_path: Path, command: str) -> None:
    res = audit(tmp_path, workflow("      - run: |\n          " + command + "\n"))
    assert res.returncode == 1, res.stdout
    assert "untrusted context" in res.stdout


def test_pull_request_target_checkout_is_case_insensitive(tmp_path: Path) -> None:
    res = audit(tmp_path, workflow(_checkout("ref: ${{ GitHub.Event.pull_request.head.sha }}"), on=TARGET))
    assert res.returncode == 1, res.stdout


def test_audit_doc_says_env_is_read_as_shell_variable() -> None:
    doc = SCRIPT.read_text(encoding="utf-8")
    assert "$CLE" in doc and "${{ env.CLE }}" in doc


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


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    res = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8", env=env
    )
    return res.stdout


def _require_gitleaks() -> None:
    """Saute si gitleaks manque, sauf si ``GRIMOIRE_REQUIRE_GITLEAKS`` l'exige (job de scan de la CI)."""
    if shutil.which("gitleaks") is not None:
        return
    if os.environ.get("GRIMOIRE_REQUIRE_GITLEAKS"):
        pytest.fail("gitleaks absent alors que GRIMOIRE_REQUIRE_GITLEAKS est défini")
    pytest.skip("gitleaks absent")


# Assemblé à l'exécution : aucun littéral de jeton dans ce fichier.
FAKE_TOKEN = "ghp_" + "aB3dE5gH7jK9" * 3


def _gitleaks(repo: Path, *extra: str) -> int:
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
            *extra,
            str(repo),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    return res.returncode


def _commit_file(repo: Path, relpath: str, content: str, message: str = "fixture") -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", relpath)
    _git(repo, "commit", "-q", "-m", message)


def _scan_repo(tmp_path: Path, relpath: str, content: str | None = None) -> int:
    """Commite ``content`` (par défaut un faux jeton) à ``relpath`` dans un dépôt jetable et lance gitleaks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit_file(repo, relpath, content if content is not None else "TOKEN = '" + FAKE_TOKEN + "'\n")
    return _gitleaks(repo)


def test_allowlisted_fixture_path_is_exempt_without_any_fingerprint(tmp_path: Path) -> None:
    _require_gitleaks()
    # Le commit est créé ici, son SHA ne figure dans aucun .gitleaksignore : comme après un squash.
    assert _scan_repo(tmp_path, "tests/unit/memory/test_write_validation.py") == 0


def test_same_token_outside_allowlisted_path_is_still_a_leak(tmp_path: Path) -> None:
    _require_gitleaks()
    assert _scan_repo(tmp_path, "src/grimoire/new_module.py") == 1


# --- D08-T2-05 : un module de production n'est jamais exempté par chemin -------------

PRODUCTION_FILES_WITH_FAKE_SECRETS = ["src/grimoire/memory/validation.py", "tests/unit/test_hook_ledger.py"]


@pytest.mark.parametrize("relpath", PRODUCTION_FILES_WITH_FAKE_SECRETS)
def test_config_does_not_exempt_whole_files_by_path(relpath: str) -> None:
    regexes = [re.compile(p) for al in _config()["allowlists"] for p in al.get("paths", [])]
    assert not any(rx.search(relpath) for rx in regexes), f"{relpath} exempté en bloc"


@pytest.mark.parametrize("relpath", PRODUCTION_FILES_WITH_FAKE_SECRETS)
def test_real_file_is_clean_thanks_to_its_inline_allow(tmp_path: Path, relpath: str) -> None:
    _require_gitleaks()
    assert _scan_repo(tmp_path, relpath, (ROOT / relpath).read_text(encoding="utf-8")) == 0


@pytest.mark.parametrize("relpath", PRODUCTION_FILES_WITH_FAKE_SECRETS)
def test_real_token_added_to_a_formerly_exempt_file_is_a_leak(tmp_path: Path, relpath: str) -> None:
    _require_gitleaks()
    content = (ROOT / relpath).read_text(encoding="utf-8") + "\nTOKEN = '" + FAKE_TOKEN + "'\n"
    assert _scan_repo(tmp_path, relpath, content) == 1


# --- D08-T2-01 : le verdict ne dépend que de l'historique du commit testé ---------------


def _scan_command() -> list[str]:
    """La ligne ``gitleaks git ...`` de secret-scan.yml, telle que le workflow la lance."""
    jobs = _scan()["jobs"]
    runs = [step["run"] for job in jobs.values() for step in job["steps"] if "gitleaks git" in step.get("run", "")]
    assert len(runs) == 1, runs
    args = shlex.split(runs[0])
    return [str(ROOT / ".gitleaks.toml") if a == ".gitleaks.toml" else a for a in args]


def test_scan_is_restricted_to_the_tested_commit() -> None:
    cmd = _scan_command()
    opts = [a for a in cmd if a.startswith("--log-opts")]
    assert opts, "gitleaks scannerait toutes les refs récupérées par fetch-depth: 0"
    value = opts[0].partition("=")[2] or cmd[cmd.index(opts[0]) + 1]
    assert value == "HEAD"


def test_leak_on_another_branch_does_not_fail_the_scan(tmp_path: Path) -> None:
    _require_gitleaks()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit_file(repo, "README.md", "clean\n", "base")
    _git(repo, "checkout", "-q", "-b", "other")
    _commit_file(repo, "src/leak.py", "TOKEN = '" + FAKE_TOKEN + "'\n", "leak")
    _git(repo, "checkout", "-q", "main")
    cmd = [*_scan_command()[:-1], str(repo)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False, cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr
    # Garde-fou du test : sans la restriction, la fuite de l'autre branche est bien vue.
    assert _gitleaks(repo) == 1


def test_leak_in_the_tested_history_still_fails_the_scan(tmp_path: Path) -> None:
    _require_gitleaks()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit_file(repo, "src/leak.py", "TOKEN = '" + FAKE_TOKEN + "'\n", "leak")
    cmd = [*_scan_command()[:-1], str(repo)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False, cwd=repo)
    assert res.returncode == 1, res.stdout + res.stderr


# --- D08-T2-02 : .gitleaksignore ne ment pas sur ce qu'il couvre ---------------------------


def _fingerprint_commits() -> list[str]:
    return [
        line.split(":")[0]
        for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def test_gitleaksignore_commits_are_all_reachable_from_head() -> None:
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "false":
        pytest.skip("historique git complet indisponible")
    for commit in dict.fromkeys(_fingerprint_commits()):
        res = subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert res.returncode == 0, f"{commit} n'est pas atteignable depuis HEAD (tag ou branche seulement)"


def test_gitleaksignore_header_matches_the_scanned_range() -> None:
    header = "\n".join(
        line for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )
    assert "966 commits (main" not in header, "966 est le total de toutes les refs, pas de main"
    assert "--log-opts" in header and "870" in header


# --- D08-T2-06 : les tests qui exécutent gitleaks tournent vraiment en CI ------------------


def test_scan_job_runs_this_test_module_with_gitleaks_required() -> None:
    job = _scan()["jobs"]["gitleaks"]
    steps = job["steps"]
    pytest_steps = [st for st in steps if "test_secret_scan.py" in st.get("run", "")]
    assert len(pytest_steps) == 1, "le job de scan doit lancer tests/unit/test_secret_scan.py"
    step = pytest_steps[0]
    assert "pytest" in step["run"]
    assert step.get("env", {}).get("GRIMOIRE_REQUIRE_GITLEAKS") == "1"
    names = [st.get("run", "") + st.get("uses", "") for st in steps]
    install = next(i for i, n in enumerate(names) if "sha256sum" in n)
    assert install < steps.index(step), "pytest doit passer après l'installation de gitleaks"
    assert any(st.get("uses", "").startswith("actions/setup-python") for st in steps)


def test_missing_gitleaks_fails_when_required_and_skips_otherwise(tmp_path: Path) -> None:
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    base = {k: v for k, v in os.environ.items() if k != "GRIMOIRE_REQUIRE_GITLEAKS"}
    base["PATH"] = str(empty_bin)  # ni gitleaks ni git : le test doit s'arrêter avant
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--noconftest",
        "-p",
        "no:cacheprovider",
        str(Path(__file__)),
        "-k",
        "test_allowlisted_fixture_path_is_exempt_without_any_fingerprint",
        "-o",
        "addopts=",
    ]

    def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=120, check=False
        )

    skipped = run(base)
    assert skipped.returncode == 0 and "1 skipped" in skipped.stdout, skipped.stdout + skipped.stderr
    required = run({**base, "GRIMOIRE_REQUIRE_GITLEAKS": "1"})
    assert required.returncode == 1 and "GRIMOIRE_REQUIRE_GITLEAKS" in required.stdout, required.stdout
