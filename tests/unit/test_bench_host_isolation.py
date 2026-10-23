"""Isolation de l'hôte du banc (B-08, lot M, §17.8) : MCP, environnement, workspace, contrôle avant dépense.

Aucun appel de modèle : ``subprocess.Popen`` et le lanceur sont remplacés par des fakes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load(name: str, filename: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hi = _load("host_isolation", "host_isolation.py")
ta = _load("three_arms_host_isolation_test", "three_arms.py")
aa = _load("aidlc_arm_host_isolation_test", "aidlc_arm.py")
le = _load("live_claude_e2e_host_isolation_test", "live_claude_e2e.py")

OPERATOR_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/op",
    "LANG": "C.UTF-8",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_MESSAGING_TOKEN": "secret-token",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/run/user/1000/sock",
    "CLAUDE_CODE_SESSION_ID": "parent",
    "CLAUDE_CODE_ENTRYPOINT": "claude-vscode",
    "CLAUDE_PID": "42",
    "CLAUDE_OPS_GH_APP": "1",
    "VSCODE_IPC_HOOK": "/run/user/1000/vscode.sock",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
    "GIT_CONFIG_VALUE_0": "!gh-helper",
    "GRIMOIRE_NEO4J_PASSWORD": "pw",
    "GH_TOKEN": "ghp",
    "SSH_AUTH_SOCK": "/run/ssh",
    "ANTHROPIC_API_KEY": "sk-kept",
}


# ── 1. Chaque `claude -p` porte une config MCP stricte ──────────────────────


class _RecordingPopen:
    seen: ClassVar[list[tuple[list[str], dict[str, str]]]] = []

    def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
        del cwd, stderr
        _RecordingPopen.seen.append((list(cmd), dict(env)))
        stdout.write(b'{"type":"result","total_cost_usd":0.01,"num_turns":1,"is_error":false}\n')
        stdout.flush()

    def poll(self) -> int:
        return 0


def _headless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str, env: dict[str, str] | None = None) -> Any:
    _RecordingPopen.seen = []
    monkeypatch.setattr(ta.subprocess, "Popen", _RecordingPopen)
    task_dir = tmp_path / "repo"
    task_dir.mkdir(exist_ok=True)
    ta.run_claude_headless(
        task_dir, "p", home=tmp_path / "home", env=env, auth_mode="oauth-copy", model="m", arm=arm,
        extra_args=["--disallowedTools", "WebFetch", "WebSearch"], poll_interval=0,
    )  # fmt: skip
    return _RecordingPopen.seen[-1]


def test_nu_run_gets_strict_and_empty_mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd, _ = _headless(monkeypatch, tmp_path, "nu")
    i = cmd.index("--strict-mcp-config")
    assert cmd[i + 1 : i + 3] == ["--mcp-config", '{"mcpServers":{}}']
    # --disallowedTools est variadique : il reste en dernier, après la config MCP
    assert cmd.index("--mcp-config") < cmd.index("--disallowedTools")


def test_nu_ignores_a_mcp_json_even_inside_its_own_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"command": "x"}}}), encoding="utf-8")
    cmd, _ = _headless(monkeypatch, tmp_path, "nu")
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'


def test_governed_arm_declares_only_what_its_install_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "repo").mkdir()
    servers = {"grimoire": {"command": "grimoire-mcp", "args": []}}
    (tmp_path / "repo" / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    cmd, _ = _headless(monkeypatch, tmp_path, "kit-gov")
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": servers}


def test_aidlc_planned_command_is_strict_too(tmp_path: Path) -> None:
    cmd = aa.planned_claude_command("x", model="m", resume=None, max_budget_usd=1.0)
    assert cmd[cmd.index("--strict-mcp-config") + 1 : cmd.index("--strict-mcp-config") + 3] == [
        "--mcp-config",
        '{"mcpServers":{}}',
    ]


def test_live_e2e_attended_calls_have_no_server_and_claudeai_connectors_are_off(tmp_path: Path) -> None:
    args = le.call_args(session_id="s", resume=None, max_budget_usd=1.0, attend_only=True, mcp_config=tmp_path / "x")
    assert "--strict-mcp-config" in args and "--mcp-config" not in args
    env = le.clean_env(home=tmp_path, path_dirs=[tmp_path], tmpdir=tmp_path)
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


# ── 2. Rien de la session de l'opérateur ne passe à l'agent ────────────────


def test_operator_session_and_secrets_never_reach_the_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, env = _headless(monkeypatch, tmp_path, "nu", env=dict(OPERATOR_ENV))
    leaked = sorted(set(env) & (set(OPERATOR_ENV) - {"PATH", "HOME", "LANG", "ANTHROPIC_API_KEY"}))
    assert leaked == []
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
    assert env["ANTHROPIC_API_KEY"] == "sk-kept" and env["PATH"] == "/usr/bin"


def test_default_env_path_is_scrubbed_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in OPERATOR_ENV.items():
        monkeypatch.setenv(name, value)
    _, env = _headless(monkeypatch, tmp_path, "nu", env=None)
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in env and "GIT_CONFIG_KEY_0" not in env
    assert env["HOME"] == str(tmp_path / "home")


def test_run_environment_puts_tmpdir_under_the_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    env = ta.run_environment(
        ws / "t", "nu", home=ws / "homes" / "nu", grimoire_bin="grimoire", workspace=ws,
        base_env={"PATH": "/usr/bin", "TMPDIR": "/tmp"},
    )  # fmt: skip
    assert env["TMPDIR"] == str(ws / "tmp") and (ws / "tmp").is_dir()


# ── 3. Workspace hors de tout projet ───────────────────────────────────────


@pytest.mark.parametrize("marker", [".mcp.json", "CLAUDE.md", "AGENTS.md", ".claude"])
def test_workspace_under_a_project_is_refused(tmp_path: Path, marker: str) -> None:
    project = tmp_path / "forge"
    (project / "_scratch").mkdir(parents=True)
    target = project / marker
    target.mkdir() if marker == ".claude" else target.write_text("x", encoding="utf-8")
    with pytest.raises(hi.IsolationError, match=marker.replace(".", r"\.")):
        hi.refuse_project_ancestors(project / "_scratch" / "ws")
    assert hi.refuse_project_ancestors(project / "_scratch" / "ws", allow=True) == [target.resolve()]


def test_default_workspace_is_outside_any_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_BENCH_WORKSPACE", raising=False)
    assert ta._default_workspace() == hi.DEFAULT_WORKSPACE.resolve()


def _patch_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "polyglot-benchmark"
    ex = root / "python" / "exercises" / "practice" / "ex-0"
    (ex / ".meta").mkdir(parents=True)
    (ex / ".meta" / "config.json").write_text(json.dumps({"files": {"solution": ["a.py"], "test": ["a_test.py"]}}))
    (ex / ".docs").mkdir()
    (ex / ".docs" / "instructions.md").write_text("# ex\n")
    (ex / "a.py").write_text("pass\n")
    (ex / "a_test.py").write_text("def test_a():\n    assert False\n")
    monkeypatch.setattr(ta, "LANGUAGES", ("python",))
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: root)
    monkeypatch.setattr(ta, "sample_tasks", lambda catalog, seed=551: list(catalog)[:1])
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "disk_guard_ok", lambda workspace, min_free_gb=0: True)
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})
    monkeypatch.setattr(ta, "detect_claude_code_version", lambda: "test")
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    monkeypatch.setattr(ta, "resolve_auth_mode", lambda explicit: "oauth-copy")


def test_main_refuses_a_workspace_under_a_project_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, tmp_path)
    (tmp_path / "forge").mkdir()
    (tmp_path / "forge" / ".mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: pytest.fail("travail lancé malgré le refus"))
    ws = tmp_path / "forge" / "ws"
    assert ta.main(["--dry-run", "--arms", "nu", "--workspace", str(ws)]) == 1
    assert ".mcp.json" in capsys.readouterr().err
    assert not ws.exists()


# ── 4. Contrôle avant dépense ───────────────────────────────────────────────


def _init(**overrides: Any) -> str:
    event = {"type": "system", "subtype": "init", "cwd": "/r", "tools": ["Bash", "Read"], "mcp_servers": []}
    event.update(overrides)
    return json.dumps(event)


def _loaded(log: Path, *paths: Path) -> None:
    log.write_text("".join(json.dumps({"file_path": str(p)}) + "\n" for p in paths), encoding="utf-8")


def test_check_control_accepts_an_isolated_init(tmp_path: Path) -> None:
    repo, home, log = tmp_path / "repo", tmp_path / "home", tmp_path / "il.jsonl"
    _loaded(log, repo / "CLAUDE.md")
    lines = [_init(memory_paths={"auto": str(home / ".claude" / "projects" / "x" / "memory")})]
    problems, facts = hi.check_control(lines, repo=repo, home=home, instructions_log=log)
    assert problems == [] and facts["mcp_servers"] == []


def test_check_control_refuses_inherited_servers_and_foreign_instructions(tmp_path: Path) -> None:
    repo, home, log = tmp_path / "repo", tmp_path / "home", tmp_path / "il.jsonl"
    _loaded(log, repo / "CLAUDE.md", tmp_path / "forge" / "CLAUDE.md")
    servers = [{"name": "grimoire", "status": "connected", "source": "project"}]
    lines = [
        _init(mcp_servers=servers, tools=["Bash", "mcp__grimoire__task_update"], memory_paths={"auto": "/home/op/m"})
    ]
    problems, _ = hi.check_control(lines, repo=repo, home=home, instructions_log=log)
    text = "\n".join(problems)
    assert "grimoire" in text and "mcp__grimoire__task_update" in text
    assert "/home/op/m" in text and str(tmp_path / "forge" / "CLAUDE.md") in text


def test_check_control_fails_closed_when_the_hook_is_silent(tmp_path: Path) -> None:
    problems, _ = hi.check_control([_init()], repo=tmp_path / "r", home=tmp_path / "h", instructions_log=tmp_path / "x")
    assert any("muet" in p for p in problems)
    assert hi.check_control([], repo=tmp_path, home=tmp_path, instructions_log=tmp_path / "x")[0]


def test_main_runs_the_control_before_any_task_and_refuses_on_a_failed_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda *a, **k: pytest.fail("run de tâche lancé malgré un contrôle rouge"))
    seen: list[str] = []

    def _control(arms: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.extend(arms)
        guard = kwargs["guard"]
        guard.begin("isolation-control/nu")
        guard.record("isolation-control/nu", 0.02, True)
        return [{"arm": "nu", "ok": False, "problems": ["serveurs MCP non déclarés dans l'init : grimoire"]}]

    monkeypatch.setattr(ta, "verify_host_isolation", _control)
    ws = tmp_path / "ws"
    rc = ta.main(["--full", "--arms", "nu", "--k", "1", "--model", "m", "--max-total-usd", "5", "--workspace", str(ws)])
    assert rc == 1 and seen == ["nu"]
    assert "grimoire" in capsys.readouterr().err
    ledger = [json.loads(x) for x in (ws / "state" / "three-arms-budget.jsonl").read_text().splitlines()]
    assert [e["call_id"] for e in ledger] == ["isolation-control/nu", "isolation-control/nu"]  # compté au journal
    assert json.loads((ws / "state" / "isolation_control.json").read_text())[0]["ok"] is False


def test_run_control_counts_the_call_and_reads_the_real_transcript(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    guard = ta.BudgetGuard.open("1", ledger)
    root, home = tmp_path / "control" / "nu", tmp_path / "home"
    calls: list[dict[str, Any]] = []

    def _runner(repo: Path, prompt: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        settings = json.loads(kwargs["extra_args"][kwargs["extra_args"].index("--settings") + 1])
        hook = settings["hooks"]["InstructionsLoaded"][0]["hooks"][0]["command"]
        assert "instructions-loaded.jsonl" in hook
        _loaded(root / "instructions-loaded.jsonl", repo / "CLAUDE.md")
        kwargs["log_path"].write_text(_init(memory_paths={"auto": str(home / "m")}) + "\n", encoding="utf-8")
        return ta.RunOutcome(total_cost_usd=0.03, cost_known=True)

    report = hi.run_control(
        "nu", root=root, home=home, env={}, guard=guard, runner=_runner,
        provision=lambda h: __import__("contextlib").nullcontext(h), auth_mode="oauth-copy", model="m",
        budget_args=ta.budget_args,
    )  # fmt: skip
    assert report["ok"] is True, report["problems"]
    assert calls[0]["extra_args"][:2] == ["--max-budget-usd", "0.5000"]
    assert (root / "repo" / "CLAUDE.md").is_file()
    assert [json.loads(x)["event"] for x in ledger.read_text().splitlines()] == ["begin", "end"]
