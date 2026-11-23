"""#1281, L7 : sonde des capacités d'hôte à l'exécution (``grimoire host probe --capabilities``).

Aucun hôte n'est lancé : le lanceur de session est remplacé par un faux hôte qui lit le hook posé
dans le projet jetable et l'exécute (le hook témoin est un petit script Python du kit), puis crée
ou non le fichier visé selon le scénario. Ce qui est vérifié : la forme de la capture, le
verdict, l'endroit où elle est écrite, la promotion humaine et ses refus.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.hosts import capability_probe as cp
from grimoire.hosts.capabilities import capture_shows_refusal

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts"
NOW = datetime(2026, 11, 22, 10, 30, 0, tzinfo=UTC)
#: Faux secrets assemblés à l'exécution : aucun motif de jeton n'est écrit en clair dans le dépôt.
FAKE_TOKEN = "gh" + "p_" + "A1b2C3d4" * 4
FAKE_MAIL = "someone" + "@" + "example.org"


def _target(key: str) -> cp.HeadlessHost:
    (target,) = cp.select_targets(key)
    return target


def _hook_command(target: cp.HeadlessHost, project: Path) -> str:
    """Retrouve la commande du hook témoin dans le fichier de l'hôte, comme l'hôte le ferait."""
    if target.key == "cursor":
        return json.loads((project / ".cursor/hooks.json").read_text())["hooks"]["preToolUse"][0]["command"]
    if target.key == "codex":
        return json.loads((project / ".codex/hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    if target.key == "gemini":
        return json.loads((project / ".gemini/settings.json").read_text())["hooks"]["BeforeTool"][0]["hooks"][0]["command"]
    if target.key == "antigravity":
        data = json.loads((project / ".agents/hooks.json").read_text())
        return data["grimoire-probe"]["PreToolUse"][0]["hooks"][0]["command"]
    if target.key == "copilot":
        return json.loads((project / ".github/hooks/grimoire-probe.json").read_text())["hooks"]["preToolUse"][0]["bash"]
    raise AssertionError(target.key)


class FakeHost:
    """Lanceur de remplacement : ``--version`` rend une version, la session joue un scénario."""

    def __init__(self, target: cp.HeadlessHost, scenario: str, *, version: str = "1.2.3", escape_to: Path | None = None) -> None:
        self.target = target
        self.scenario = scenario
        self.version = version
        self.escape_to = escape_to
        self.session: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.marker = ""

    def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
        if argv[1:] == list(self.target.version_args):
            return SimpleNamespace(returncode=0, stdout=f"{self.target.key} {self.version}\n", stderr="")
        self.session, self.kwargs = argv, kwargs
        self.marker = re.search(r"probe-target-[0-9a-f]{12}\.txt", " ".join(argv)).group(0)
        project = Path(kwargs["cwd"])
        if self.scenario == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if self.scenario == "oserror":
            raise OSError("boom")
        if self.scenario == "crash":
            raise RuntimeError("bug")
        if self.scenario in ("refused", "ignored", "mute", "escaped"):
            self._call_hook(project, f"touch {self.marker}")
        if self.scenario == "other-tool":
            self._call_hook(project, "ls")
        if self.scenario in ("ignored", "executed"):
            (project / self.marker).write_text("", encoding="utf-8")
        if self.scenario == "escaped":
            (self.escape_to or project.parent / "elsewhere").mkdir(exist_ok=True)
            ((self.escape_to or project.parent / "elsewhere") / self.marker).write_text("", encoding="utf-8")
        if self.scenario == "garbage":
            entry = {"payload": {"args": [str(project), self.marker]}, "exit_code": 2, "stdout": "pas du json"}
            lines = ["pas du json", "[1]", json.dumps(entry), json.dumps({"payload": "brut", "exit_code": 2})]
            (project.parent / "hook.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        said = "" if self.scenario == "mute" else f"refusé : {cp.DENY_REASON}"
        stdout = json.dumps({"result": f"{said} in {project}", "by": FAKE_MAIL, "token": FAKE_TOKEN})
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def _call_hook(self, project: Path, command: str) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": command},
            "cwd": str(project),
            "session_id": "sess-123",
            "transcript_path": "/somewhere/transcript.jsonl",
        }
        log = project.parent / "hook.log"
        if self.target.key == "opencode":
            plugin = (project / ".opencode/plugin/grimoire-probe.js").read_text()
            assert json.loads(plugin.split("const LOG = ", 1)[1].splitlines()[0]) == str(log)
            entry = {
                "payload": {"input": {"tool": "bash"}, "output": {"args": {"command": command}}},
                "exit_code": None,
                "stdout": None,
                "throws": cp.DENY_REASON,
            }
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
            return
        argv = shlex.split(_hook_command(self.target, project))
        done = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, check=False)
        assert done.returncode == self.target.deny_exit
        assert cp.DENY_REASON in done.stderr


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GRIMOIRE_TASK_ID", "should-not-leak")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda cls: home))
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)


def _probe(root: Path, key: str, scenario: str, **kwargs: Any) -> tuple[dict[str, Any], Path, FakeHost]:
    target = _target(key)
    fake = FakeHost(target, scenario, **kwargs)
    capture, path = cp.run_probe(target, root, binary=f"/opt/bin/{target.binaries[0]}", runner=fake, now=lambda: NOW)
    return capture, path, fake


# ── sélection et détection ───────────────────────────────────────────────────


def test_select_targets_by_key_alias_and_all() -> None:
    assert cp.select_targets("all") == cp.TARGETS
    assert cp.select_targets("agy")[0].key == "antigravity"
    assert cp.select_targets("cursor-agent")[0].key == "cursor"
    assert cp.select_targets("claude") == ()
    assert {t.key for t in cp.TARGETS} == {"cursor", "codex", "gemini", "antigravity", "opencode", "copilot"}


def test_detect_reads_binary_and_version_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "share" / "cursor-agent" / "agent"
    real.parent.mkdir(parents=True)
    real.write_text("")
    link = tmp_path / "bin" / "agent"
    link.parent.mkdir()
    link.symlink_to(real)
    paths = {"agent": str(link), "opencode": "/opt/bin/opencode"}
    monkeypatch.setattr(cp.shutil, "which", lambda name: paths.get(name))
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: Any) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="opencode 1.17.11" if "opencode" in argv[0] else "2026.11.20")

    found = {d.target.key: d for d in cp.detect(cp.TARGETS, tmp_path, runner=runner)}
    assert found["cursor"].binary == str(link) and found["cursor"].version == "2026.11.20"
    assert found["opencode"].version == "1.17.11"
    assert not found["codex"].installed and found["codex"].version == ""
    assert all(argv[1:] == ["--version"] for argv in calls)
    assert found["cursor"].to_dict()["binary"] == "agent" and found["cursor"].to_dict()["last_probe"] is None


def test_a_generic_agent_binary_is_not_taken_for_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """M3 : un `agent` qui ne mène pas à Cursor n'est pas Cursor."""
    monkeypatch.setattr(cp.shutil, "which", lambda name: "/usr/bin/agent" if name == "agent" else None)
    (cursor,) = cp.detect(cp.select_targets("cursor"), Path())
    assert not cursor.installed


def test_version_digits_are_ascii(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 : des chiffres non ASCII ne forment pas une version."""
    monkeypatch.setattr(cp.shutil, "which", lambda name: "/opt/bin/codex" if name == "codex" else None)
    runner = lambda argv, **_: SimpleNamespace(returncode=0, stdout="v١.٢.٣ puis 0.9.1", stderr="")  # noqa: E731
    (codex,) = cp.detect(cp.select_targets("codex"), tmp_path, runner=runner)
    assert codex.version == "0.9.1"


def test_detect_survives_a_broken_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda name: "/opt/bin/codex" if name == "codex" else None)

    def runner(argv: list[str], **_: Any) -> SimpleNamespace:
        raise OSError("exec format error")

    (codex,) = cp.detect(cp.select_targets("codex"), tmp_path, runner=runner)
    assert codex.installed and codex.version == ""


# ── session : verdicts et capture ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["cursor", "codex", "gemini", "antigravity", "opencode", "copilot"])
def test_a_refusal_is_captured_and_shows_refusal(tmp_path: Path, key: str) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    capture, path, fake = _probe(root, key, "refused")
    assert capture["verdict"] == cp.REFUSED, capture["observed"]
    observed = capture["observed"]
    assert observed["tool_executed"] is False and observed["hook_called"] is True and observed["hook_calls_on_target"] == 1
    assert capture_shows_refusal(capture)
    assert capture["promotable"] is (key != "antigravity")
    assert capture["host_version"] == "1.2.3" and capture["captured_on"] == "2026-11-22"
    assert capture["target_file"] == fake.marker
    assert path.parent == root / cp.PROBES_DIR / key
    assert json.loads(path.read_text()) == capture
    # Rien hors de _grimoire-output/host-probes, et le projet jetable a disparu.
    assert [p.name for p in root.iterdir()] == ["_grimoire-output"]
    assert not Path(fake.kwargs["cwd"]).exists()
    assert fake.kwargs["timeout"] == cp.DEFAULT_TIMEOUT


def test_payloads_keep_only_event_tool_and_args_and_are_redacted(tmp_path: Path) -> None:
    """C4 : liste blanche des clés de charge, expurgation des secrets dans la sortie."""
    capture, _, fake = _probe(tmp_path, "cursor", "refused")
    (call,) = capture["payloads"]["pre_tool_use"]
    assert set(call) == {"event", "tool", "args", "targets_marker", "hook_exit_code", "hook_stdout"}
    assert call["args"] == {"command": f"touch {fake.marker}"} and call["targets_marker"] is True
    text = json.dumps(capture)
    assert "sess-123" not in text and "transcript" not in text
    assert FAKE_TOKEN not in text and FAKE_MAIL not in text and "<REDACTED>" in capture["output"]["stdout"]
    assert str(fake.kwargs["cwd"]) not in capture["output"]["stdout"] and "<PROJECT>" in capture["output"]["stdout"]
    assert "--force" in fake.session and capture["captured_with"].startswith("/opt/bin/cursor-agent -p")


@pytest.mark.parametrize(
    "secret",
    [
        "gh" + "o_" + "x" * 24,
        "github" + "_pat_" + "y" * 30,
        "s" + "k-" + "z" * 24,
        "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "ey" + "JhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXZhbHVl",
        FAKE_MAIL,
    ],
)
def test_redact_covers_each_secret_family(secret: str) -> None:
    assert cp.redact(f"avant {secret} après") == "avant <REDACTED> après"


def test_the_session_environment_is_an_allowlist(tmp_path: Path) -> None:
    """C1 : base, LC_*, XDG_* et la seule authentification de l'hôte visé."""
    environ = {
        "PATH": "/bin", "HOME": "/h", "USER": "u", "LANG": "fr_FR.UTF-8", "LC_ALL": "C", "TERM": "xterm",
        "TMPDIR": "/t", "XDG_CONFIG_HOME": "/h/.config", "CURSOR_API_KEY": "sentinel-cursor",
        "SSH_AUTH_SOCK": "sentinel-ssh", "GH_TOKEN": "sentinel-gh", "OPENAI_API_KEY": "sentinel-openai",
        "AWS_SECRET_ACCESS_KEY": "sentinel-aws", "GRIMOIRE_TASK_ID": "sentinel-task", "CLAUDE_PROJECT_DIR": "sentinel",
    }  # fmt: skip
    env = cp.session_env(_target("cursor"), environ)
    assert set(env) == {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "XDG_CONFIG_HOME", "CURSOR_API_KEY"}
    assert "sentinel" not in json.dumps({k: v for k, v in env.items() if k != "CURSOR_API_KEY"})
    codex = cp.session_env(_target("codex"), environ)
    assert "OPENAI_API_KEY" in codex and "CURSOR_API_KEY" not in codex
    plan = cp.session_plan(_target("gemini"), tmp_path, environ)
    assert "GEMINI_CLI_TRUST_WORKSPACE" in plan["env"] and "--approval-mode yolo" in plan["auto_approve"]
    assert "sentinel-ssh" not in json.dumps(plan)


def test_the_real_session_runner_gets_the_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "sentinel-ssh")
    monkeypatch.setenv("GH_TOKEN", "sentinel-gh")
    capture, _, fake = _probe(tmp_path, "copilot", "refused")
    assert "SSH_AUTH_SOCK" not in fake.kwargs["env"] and "GH_TOKEN" not in fake.kwargs["env"]
    assert "GRIMOIRE_TASK_ID" not in fake.kwargs["env"]
    assert capture["verdict"] == cp.REFUSED


def test_gemini_session_trusts_only_the_throwaway_folder(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "gemini", "refused")
    assert fake.kwargs["env"]["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
    assert capture["observed"]["hook_exit_code"] == 2 and capture["observed"]["hook_stdout"] is None


def test_opencode_capture_records_the_throw(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "opencode", "refused")
    assert capture["observed"]["hook_throws"] == cp.DENY_REASON
    assert fake.session[:4] == ["/opt/bin/opencode", "run", "--format", "json"]


def test_a_refused_call_on_another_tool_is_not_a_refusal(tmp_path: Path) -> None:
    """B1 : seul un appel qui porte la commande demandée compte."""
    capture, _, _ = _probe(tmp_path, "codex", "other-tool")
    assert capture["verdict"] == cp.INCONCLUSIVE
    assert capture["observed"]["hook_calls"] == 1 and capture["observed"]["hook_calls_on_target"] == 0
    assert capture["promotable"] is False


def test_every_hook_call_is_recorded(tmp_path: Path) -> None:
    """B1 : tous les appels sont gardés, le refus retenu est celui qui porte la cible."""
    target = _target("codex")

    class TwoCalls(FakeHost):
        def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
            if argv[1:] != list(self.target.version_args):
                marker = re.search(r"probe-target-[0-9a-f]{12}\.txt", " ".join(argv)).group(0)
                self._call_hook(Path(kwargs["cwd"]), "ls")
                self._call_hook(Path(kwargs["cwd"]), f"touch {marker}")
            return super().__call__(argv, **kwargs) if argv[1:] == list(self.target.version_args) else SimpleNamespace(
                returncode=0, stdout=cp.DENY_REASON, stderr=""
            )

    capture, _ = cp.run_probe(target, tmp_path, binary="/opt/bin/codex", runner=TwoCalls(target, "x"), now=lambda: NOW)
    calls = capture["payloads"]["pre_tool_use"]
    assert [c["targets_marker"] for c in calls] == [False, True]
    assert capture["verdict"] == cp.REFUSED


def test_the_target_file_is_searched_outside_the_project(tmp_path: Path) -> None:
    """B2 : un fichier créé hors du projet jetable (dossier temporaire, cwd, HOME) vaut exécution."""
    capture, _, _ = _probe(tmp_path, "codex", "escaped")
    assert capture["verdict"] == cp.EXECUTED and capture["observed"]["marker_seen_in"] == ["<PROBE>"]
    capture, _, _ = _probe(tmp_path, "codex", "escaped", escape_to=Path.cwd())
    assert capture["verdict"] == cp.EXECUTED and "cwd" in capture["observed"]["marker_seen_in"]
    capture, _, _ = _probe(tmp_path, "codex", "escaped", escape_to=cp.Path.home())
    assert capture["verdict"] == cp.EXECUTED and "~" in capture["observed"]["marker_seen_in"]


def test_each_session_has_its_own_target_name(tmp_path: Path) -> None:
    """B2 : un fichier homonyme d'une autre session ne dit rien de celle-ci."""
    _, _, first = _probe(tmp_path, "codex", "refused")
    _, _, second = _probe(tmp_path, "codex", "refused")
    assert first.marker != second.marker


def test_a_refusal_the_host_output_does_not_show_is_inconclusive(tmp_path: Path) -> None:
    """B2 : la sortie de l'hôte est croisée avant de conclure ``refused``."""
    capture, _, _ = _probe(tmp_path, "copilot", "mute")
    assert capture["verdict"] == cp.INCONCLUSIVE and capture["observed"]["output_shows_refusal"] is False


@pytest.mark.parametrize(
    ("scenario", "verdict"),
    [
        ("executed", cp.EXECUTED),
        ("ignored", cp.EXECUTED),
        ("silent", cp.INCONCLUSIVE),
        ("timeout", cp.ERROR),
        ("oserror", cp.ERROR),
        ("crash", cp.ERROR),
    ],
)
def test_other_outcomes_never_show_refusal(tmp_path: Path, scenario: str, verdict: str) -> None:
    capture, _, _ = _probe(tmp_path, "codex", scenario)
    assert capture["verdict"] == verdict
    assert not capture_shows_refusal(capture)
    assert capture["promotable"] is False
    if scenario == "timeout":
        assert capture["observed"]["timed_out"] is True and "délai" in capture["reason"]
    if scenario == "crash":
        assert capture["reason"] == "session interrompue : RuntimeError"


def test_missing_binary_or_version_launches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda name: None)
    target = _target("copilot")
    fake = FakeHost(target, "refused")
    capture, _ = cp.run_probe(target, tmp_path, runner=fake, now=lambda: NOW)
    assert capture["verdict"] == cp.ERROR and capture["reason"] == "binaire introuvable" and fake.session == []
    shim = FakeHost(target, "refused", version="")
    capture, _ = cp.run_probe(target, tmp_path, binary="/usr/bin/copilot", runner=shim, now=lambda: NOW)
    assert capture["reason"].startswith("version illisible") and shim.session == []


def test_the_cheapest_declared_model_is_used(tmp_path: Path) -> None:
    policy = tmp_path / "_grimoire" / "standard" / "orchestration-policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("dispatch:\n  models:\n    cursor:\n      cheap: cursor-small\n", encoding="utf-8")
    capture, _, fake = _probe(tmp_path, "cursor", "refused")
    assert fake.session[-2:] == ["--model", "cursor-small"] and capture["model"] == "cursor-small"
    _, _, codex = _probe(tmp_path, "codex", "refused")
    assert "--model" not in codex.session  # rien de déclaré : défaut de l'hôte


def test_timeout_is_bounded(tmp_path: Path) -> None:
    target = _target("antigravity")
    fake = FakeHost(target, "refused")
    cp.run_probe(target, tmp_path, binary="/opt/bin/agy", timeout=10_000, runner=fake, now=lambda: NOW)
    assert fake.kwargs["timeout"] == cp.MAX_TIMEOUT and f"{cp.MAX_TIMEOUT}s" in fake.session


def test_a_noisy_hook_log_is_read_defensively(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "codex", "garbage")
    assert capture["observed"]["hook_calls"] == 2 and capture["observed"]["hook_stdout"] == "pas du json"
    assert capture["payloads"]["pre_tool_use"][1]["args"] is None
    assert capture["payloads"]["pre_tool_use"][0]["args"] == ["<PROJECT>", fake.marker]
    assert capture["verdict"] == cp.REFUSED and capture["promotable"] is False  # sortie du hook non conforme


# ── lanceur réel : groupe de processus ───────────────────────────────────────


def test_the_session_runner_returns_the_output(tmp_path: Path) -> None:
    done = cp._run_session([sys.executable, "-c", "print('ok')"], cwd=tmp_path, env=dict(os.environ), timeout=30)
    assert done.returncode == 0 and done.stdout.strip() == "ok"


def test_a_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    """C3 : un petit-enfant lancé par la session ne survit pas au délai."""
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        cp._run_session([sys.executable, "-c", script], cwd=tmp_path, env=dict(os.environ), timeout=3)
    child = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        pytest.fail("le petit-enfant a survécu au délai")


# ── captures locales ─────────────────────────────────────────────────────────


def test_latest_probe_and_local_probes(tmp_path: Path) -> None:
    folder = tmp_path / cp.PROBES_DIR / "gemini"
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(json.dumps({"probed_at": "2026-11-01T00:00:00+00:00", "host_version": "0.1"}))
    (folder / "b.json").write_text(json.dumps({"probed_at": "2026-11-20T00:00:00+00:00", "host_version": "0.2", "host": "gemini", "verdict": "inconclusive"}))
    (folder / "c.json").write_text("pas du json")
    (folder / "d.json").write_text(json.dumps({"no": "date"}))
    assert cp.latest_probe(tmp_path, "gemini")["host_version"] == "0.2"
    assert cp.latest_probe(tmp_path, "codex") is None
    _probe(tmp_path, "antigravity", "silent")
    found = cp.local_probes_for(tmp_path, HostId.GEMINI_CLI)
    assert [p["host"] for p in found] == ["gemini", "antigravity"]
    assert found[0]["path"] == "_grimoire-output/host-probes/gemini/b.json"
    assert cp.local_probes_for(tmp_path, HostId.CODEX) == []


# ── promotion humaine ────────────────────────────────────────────────────────


def _fixtures(tmp_path: Path, host: str = "cursor") -> Path:
    root = tmp_path / "fixtures"
    (root / host).mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / host / "manifest.json", root / host / "manifest.json")
    return root


def _edit(path: Path, **changes: Any) -> Path:
    data = json.loads(path.read_text())
    for dotted, value in changes.items():
        node = data
        *parents, leaf = dotted.split("__")
        for key in parents:
            node = node[key]
        node[leaf] = value
    path.write_text(json.dumps(data))
    return path


def test_promote_copies_the_capture_and_updates_the_manifest(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "cursor", "refused")
    root = _fixtures(tmp_path)
    result = cp.promote(path, root, now=lambda: NOW)
    assert result.relpath == "cursor/01-pretooluse-deny-probe-1.2.3.json"
    fixture = json.loads(result.fixture.read_text())
    assert capture_shows_refusal(fixture) and fixture["promoted_on"] == "2026-11-22"
    assert json.loads(result.content) == fixture
    manifest = json.loads(result.manifest.read_text())
    assert manifest["host_version"] == "1.2.3"
    assert manifest["capabilities"]["pretooluse_deny"]["status"] == "verified"
    assert manifest["capabilities"]["pretooluse_deny"]["fixtures"] == [result.relpath]
    assert manifest["capabilities"]["session_end"]["status"] == "unverified"
    assert any("blocking_captures" in line for line in result.instructions)
    with pytest.raises(cp.PromotionRefusedError, match="déjà versée"):
        cp.promote(path, root)


@pytest.mark.parametrize("scenario", ["executed", "silent", "timeout", "other-tool", "mute"])
def test_promote_refuses_a_capture_without_refusal(tmp_path: Path, scenario: str) -> None:
    _, path, _ = _probe(tmp_path, "cursor", scenario)
    with pytest.raises(cp.PromotionRefusedError):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_refuses_a_forged_verdict(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "cursor", "silent")
    _edit(path, verdict="refused", observed__hook_exit_code=2)
    with pytest.raises(cp.PromotionRefusedError, match="refus"):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_recomputes_the_target_check_on_payloads(tmp_path: Path) -> None:
    """B1 : une capture dont aucun appel ne porte la cible est refusée, même verdict ``refused``."""
    _, path, _ = _probe(tmp_path, "cursor", "refused")
    data = json.loads(path.read_text())
    data["payloads"]["pre_tool_use"][0]["args"] = {"command": "ls"}
    path.write_text(json.dumps(data))
    with pytest.raises(cp.PromotionRefusedError, match="commande demandée"):
        cp.promote(path, _fixtures(tmp_path))


@pytest.mark.parametrize(
    "stdout",
    [
        {"permission": "allow", "decision": "deny"},
        {"permission": "deny", "decision": "allow"},
        {"permission": "deny", "user_message": "autre", "agent_message": "autre"},
    ],
)
def test_promote_requires_the_exact_witness_output(tmp_path: Path, stdout: dict[str, str]) -> None:
    """B3 : la sortie du hook doit être celle, déterministe, du hook témoin de l'hôte."""
    _, path, _ = _probe(tmp_path, "cursor", "refused")
    _edit(path, observed__hook_stdout=stdout, observed__hook_exit_code=0)
    with pytest.raises(cp.PromotionRefusedError):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_requires_the_witness_exit_code_and_throw(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "copilot", "refused")
    _edit(path, observed__hook_exit_code=1, observed__hook_stdout={"permissionDecision": "deny"})
    with pytest.raises(cp.PromotionRefusedError, match="hook témoin"):
        cp.promote(path, _fixtures(tmp_path, "copilot"))
    _, opencode, _ = _probe(tmp_path, "opencode", "refused")
    _edit(opencode, observed__hook_throws="autre chose")
    with pytest.raises(cp.PromotionRefusedError, match="plugin témoin"):
        cp.promote(opencode, _fixtures(tmp_path, "opencode"))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("host", "codex", "host, host_id"),
        ("host_id", "host-codex", "host, host_id"),
        ("captured_with", "/opt/bin/codex exec", "binaire de l'hôte"),
        ("captured_with", "'unterminated", "binaire de l'hôte"),
        ("capability", "session_end", "capacité"),
    ],
)
def test_promote_checks_the_capture_identity(tmp_path: Path, field: str, value: str, match: str) -> None:
    """C5 : hôte, identifiant et binaire de la capture cohérents avec probe_host."""
    _, path, _ = _probe(tmp_path, "cursor", "refused")
    _edit(path, **{field: value})
    with pytest.raises(cp.PromotionRefusedError, match=match):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_refuses_antigravity_and_unknown_hosts(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "antigravity", "refused")
    with pytest.raises(cp.PromotionRefusedError, match="non versable"):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_refuses_a_missing_version(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "codex", "refused")
    _edit(path, host_version="")
    with pytest.raises(cp.PromotionRefusedError, match="version"):
        cp.promote(path, _fixtures(tmp_path, "codex"))


def test_promote_refuses_unreadable_inputs(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1]")
    with pytest.raises(cp.PromotionRefusedError, match="table JSON"):
        cp.promote(bad, tmp_path)
    with pytest.raises(cp.PromotionRefusedError, match="illisible"):
        cp.promote(tmp_path / "absent.json", tmp_path)
    _, path, _ = _probe(tmp_path, "codex", "refused")
    with pytest.raises(cp.PromotionRefusedError, match="manifeste introuvable"):
        cp.promote(path, tmp_path / "nowhere")
    root = _fixtures(tmp_path, "codex")
    (root / "codex" / "manifest.json").write_text(json.dumps({"host": "codex"}))
    with pytest.raises(cp.PromotionRefusedError, match="manifeste illisible"):
        cp.promote(path, root)


def test_promote_refuses_mixing_versions_in_a_manifest(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "opencode", "refused")
    root = _fixtures(tmp_path, "opencode")
    manifest_path = root / "opencode" / "manifest.json"
    _edit(manifest_path, capabilities__session_end__status="verified")
    with pytest.raises(cp.PromotionRefusedError, match="une version par manifeste"):
        cp.promote(path, root)


def test_promote_needs_replace_to_supersede_another_version(tmp_path: Path) -> None:
    """C6 : pretooluse_deny déjà prouvé sur une autre version : remplacé seulement sur demande."""
    _, path, _ = _probe(tmp_path, "opencode", "refused")
    root = _fixtures(tmp_path, "opencode")
    with pytest.raises(cp.PromotionRefusedError, match="--replace"):
        cp.promote(path, root)
    result = cp.promote(path, root, replace=True)
    assert json.loads(result.manifest.read_text())["capabilities"]["pretooluse_deny"]["fixtures"] == [result.relpath]


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli(*args: str, input: str | None = None) -> Any:
    from grimoire.cli.app import app

    return CliRunner().invoke(app, list(args), env={"COLUMNS": "220"}, input=input)


def test_cli_capabilities_detects_without_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda name: "/opt/bin/opencode" if name == "opencode" else None)
    monkeypatch.setattr(cp.subprocess, "run", lambda argv, **_: SimpleNamespace(returncode=0, stdout="1.17.11", stderr=""))
    called: list[str] = []
    monkeypatch.setattr(cp, "run_probe", lambda *a, **k: called.append("run"))
    result = _cli("-o", "json", "host", "probe", "--capabilities", "--project-root", str(tmp_path))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    opencode = next(h for h in data["hosts"] if h["host"] == "opencode")
    assert opencode["installed"] and opencode["version"] == "1.17.11" and data["captures"] == []
    assert called == []
    text = _cli("host", "probe", "--capabilities", "--host", "opencode", "--project-root", str(tmp_path))
    assert text.exit_code == 0 and "--run" in text.output and "1.17.11" in text.output


def _fake_detect(targets: tuple[cp.HeadlessHost, ...], root: Path) -> tuple[cp.Detection, ...]:
    return (
        cp.Detection(_target("opencode"), "/opt/bin/opencode", "1.17.11"),
        cp.Detection(_target("copilot"), "/usr/bin/copilot", ""),
        cp.Detection(_target("codex"), "", ""),
    )


def test_cli_run_shows_the_plan_and_needs_yes_outside_a_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C2 : drapeaux, coût et variables montrés ; hors terminal, rien sans --yes."""
    launched: list[str] = []

    def fake_run(target: cp.HeadlessHost, root: Path, **kwargs: Any) -> tuple[dict[str, Any], Path]:
        launched.append(target.key)
        assert kwargs["timeout"] == 30
        return {"verdict": "[bold]refused", "reason": ""}, root / "x.json"

    monkeypatch.setattr(cp, "detect", _fake_detect)
    monkeypatch.setattr(cp, "run_probe", fake_run)
    refused = _cli("host", "probe", "--capabilities", "--run", "--timeout", "30", "--project-root", str(tmp_path))
    assert refused.exit_code == 2 and "--yes" in refused.output and launched == []
    assert "payants" in refused.output and "PATH" in refused.output
    result = _cli("host", "probe", "--capabilities", "--run", "--yes", "--timeout", "30", "--project-root", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert launched == ["opencode"]
    assert "[bold]refused" in result.output and "--promote" in result.output  # M4 : champ échappé, affiché tel quel
    json_result = _cli("-o", "json", "host", "probe", "--capabilities", "--run", "--yes", "--timeout", "30", "--project-root", str(tmp_path))
    assert json.loads(json_result.stdout)["captures"][0]["host"] == "opencode"  # le plan part sur stderr


def test_cli_run_asks_for_confirmation_in_a_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_host

    launched: list[str] = []
    monkeypatch.setattr(cp, "detect", _fake_detect)
    monkeypatch.setattr(cp, "run_probe", lambda target, root, **_: (launched.append(target.key) or ({"verdict": "refused", "reason": ""}, root / "x.json")))
    monkeypatch.setattr(cmd_host, "_interactive", lambda: True)
    no = _cli("host", "probe", "--capabilities", "--run", "--project-root", str(tmp_path), input="n\n")
    assert no.exit_code == 1 and launched == [] and "Rien n'a été lancé" in no.output
    yes = _cli("host", "probe", "--capabilities", "--run", "--project-root", str(tmp_path), input="y\n")
    assert yes.exit_code == 0 and launched == ["opencode"]


def test_cli_run_without_any_installed_host_launches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "detect", lambda targets, root: (cp.Detection(_target("codex"), "", ""),))
    monkeypatch.setattr(cp, "run_probe", lambda *a, **k: pytest.fail("aucune session attendue"))
    result = _cli("host", "probe", "--capabilities", "--run", "--host", "codex", "--project-root", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "--promote" not in result.output and "absent" in result.output


def test_cli_rejects_run_alone_and_unknown_hosts(tmp_path: Path) -> None:
    assert _cli("host", "probe", "--run", "--project-root", str(tmp_path)).exit_code == 2
    result = _cli("host", "probe", "--capabilities", "--host", "claude", "--project-root", str(tmp_path))
    assert result.exit_code == 1 and "non sondable" in result.output


def test_cli_promote(tmp_path: Path) -> None:
    _, good, _ = _probe(tmp_path, "cursor", "refused")
    root = _fixtures(tmp_path)
    result = _cli("host", "probe", "--promote", str(good), "--fixtures-root", str(root))
    assert result.exit_code == 0, result.output
    assert "Capture versée" in result.output and "blocking_captures" in result.output
    assert "Contenu versé" in result.output and '"verdict": "refused"' in result.output
    again = _cli("-o", "json", "host", "probe", "--promote", str(good), "--fixtures-root", str(root))
    assert again.exit_code == 1 and json.loads(again.output)["promoted"] is False
    _, codex, _ = _probe(tmp_path, "codex", "refused")
    croot = _fixtures(tmp_path / "c", "codex")
    ok = _cli("-o", "json", "host", "probe", "--promote", str(codex), "--fixtures-root", str(croot))
    assert ok.exit_code == 0 and json.loads(ok.output)["fixture"].startswith("codex/")
    assert json.loads(ok.output)["content"]["verdict"] == "refused"
    _, opencode, _ = _probe(tmp_path, "opencode", "refused")
    oroot = _fixtures(tmp_path / "o", "opencode")
    replaced = _cli("host", "probe", "--promote", str(opencode), "--replace", "--fixtures-root", str(oroot))
    assert replaced.exit_code == 0, replaced.output
    _, silent, _ = _probe(tmp_path, "cursor", "silent")
    refused = _cli("host", "probe", "--promote", str(silent), "--project-root", str(tmp_path))
    assert refused.exit_code == 1 and "Promotion refusée" in refused.output


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude", "gemini"]\n',
        encoding="utf-8",
    )
    return tmp_path


def test_status_and_matrix_show_the_last_local_probe_without_changing_claims(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _probe(root, "gemini", "refused")
    status = _cli("-o", "json", "host", "status", "--host", "gemini", "--project-root", str(root))
    (gemini,) = json.loads(status.output)
    assert gemini["local_probes"][0]["host_version"] == "1.2.3"
    assert gemini["blocking_hooks"] == []  # une capture locale n'annonce rien
    text = _cli("host", "status", "--host", "gemini", "--project-root", str(root))
    assert "dernière sonde locale (gemini)" in text.output and "ne change aucune annonce" in text.output
    matrix = _cli("-o", "json", "host", "matrix", "--project-root", str(root))
    assert json.loads(matrix.output)["local_probes"]["host-gemini-cli"][0]["verdict"] == "refused"
    table = _cli("host", "matrix", "--project-root", str(root))
    assert "dernière sonde locale (gemini)" in table.output
