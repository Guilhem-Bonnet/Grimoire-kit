"""#1281, L7 : sonde des capacités d'hôte à l'exécution (``grimoire host probe --capabilities``).

Aucun hôte n'est lancé : ``subprocess.run`` est remplacé par un faux hôte qui lit le hook posé
dans le projet jetable et l'exécute (le hook témoin est un petit script Python du kit), puis crée
ou non le fichier visé selon le scénario. Ce qui est vérifié : la forme de la capture, le
verdict, l'endroit où elle est écrite, la promotion humaine et ses refus.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
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
    """Remplace ``subprocess.run`` : ``--version`` rend une version, la session joue un scénario."""

    def __init__(self, target: cp.HeadlessHost, scenario: str, *, version: str = "1.2.3") -> None:
        self.target = target
        self.scenario = scenario
        self.version = version
        self.session: list[str] = []
        self.kwargs: dict[str, Any] = {}

    def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
        if argv[1:] == list(self.target.version_args):
            return SimpleNamespace(returncode=0, stdout=f"{self.target.key} {self.version}\n", stderr="")
        self.session, self.kwargs = argv, kwargs
        project = Path(kwargs["cwd"])
        if self.scenario == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if self.scenario == "oserror":
            raise OSError("boom")
        if self.scenario in ("refused", "ignored"):
            self._call_hook(project)
        if self.scenario == "garbage":
            entry = {"payload": {"args": [str(project), 1]}, "exit_code": 2, "stdout": "pas du json"}
            lines = ["pas du json", "[1]", json.dumps(entry)]
            (project.parent / "hook.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self.scenario in ("ignored", "executed"):
            (project / cp.TARGET_FILE).write_text("", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=f'{{"result": "ok in {project}"}}', stderr="")

    def _call_hook(self, project: Path) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Shell", "cwd": str(project)}
        if self.target.key == "opencode":
            plugin = (project / ".opencode/plugin/grimoire-probe.js").read_text()
            log = Path(json.loads(plugin.split("const LOG = ", 1)[1].splitlines()[0]))
            entry = {"payload": {"input": {"tool": "bash"}}, "exit_code": None, "stdout": None, "throws": cp.DENY_REASON}
            log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            return
        command = shlex.split(_hook_command(self.target, project))
        done = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, check=False)
        assert done.returncode == self.target.deny_exit
        assert cp.DENY_REASON in done.stderr


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_TASK_ID", "should-not-leak")


def _probe(tmp_path: Path, key: str, scenario: str, **kwargs: Any) -> tuple[dict[str, Any], Path, FakeHost]:
    target = _target(key)
    fake = FakeHost(target, scenario, **kwargs)
    capture, path = cp.run_probe(target, tmp_path, binary=f"/opt/bin/{target.binaries[0]}", runner=fake, now=lambda: NOW)
    return capture, path, fake


# ── sélection et détection ───────────────────────────────────────────────────


def test_select_targets_by_key_alias_and_all() -> None:
    assert cp.select_targets("all") == cp.TARGETS
    assert cp.select_targets("agy")[0].key == "antigravity"
    assert cp.select_targets("cursor-agent")[0].key == "cursor"
    assert cp.select_targets("claude") == ()
    assert {t.key for t in cp.TARGETS} == {"cursor", "codex", "gemini", "antigravity", "opencode", "copilot"}


def test_detect_reads_binary_and_version_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = {"agent": "/opt/bin/agent", "opencode": "/opt/bin/opencode"}
    monkeypatch.setattr(cp.shutil, "which", lambda name: paths.get(name))
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: Any) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="opencode 1.17.11" if "opencode" in argv[0] else "2026.11.20")

    found = {d.target.key: d for d in cp.detect(cp.TARGETS, tmp_path, runner=runner)}
    assert found["cursor"].binary == "/opt/bin/agent" and found["cursor"].version == "2026.11.20"
    assert found["opencode"].version == "1.17.11"
    assert not found["codex"].installed and found["codex"].version == ""
    assert all(argv[1:] == ["--version"] for argv in calls)
    assert found["cursor"].to_dict()["binary"] == "agent" and found["cursor"].to_dict()["last_probe"] is None


def test_detect_survives_a_broken_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda name: "/opt/bin/codex" if name == "codex" else None)

    def runner(argv: list[str], **_: Any) -> SimpleNamespace:
        raise OSError("exec format error")

    (codex,) = cp.detect(cp.select_targets("codex"), tmp_path, runner=runner)
    assert codex.installed and codex.version == ""


# ── session : verdicts et capture ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["cursor", "codex", "gemini", "antigravity", "opencode", "copilot"])
def test_a_refusal_is_captured_and_shows_refusal(tmp_path: Path, key: str) -> None:
    capture, path, fake = _probe(tmp_path, key, "refused")
    assert capture["verdict"] == cp.REFUSED
    assert capture["observed"]["tool_executed"] is False and capture["observed"]["hook_called"] is True
    assert capture_shows_refusal(capture)
    assert capture["promotable"] is (key != "antigravity")
    assert capture["host_version"] == "1.2.3" and capture["captured_on"] == "2026-11-22"
    assert path.parent == tmp_path / cp.PROBES_DIR / key
    assert json.loads(path.read_text()) == capture
    # Rien hors de _grimoire-output/host-probes, et le projet jetable a disparu.
    assert [p.name for p in tmp_path.iterdir()] == ["_grimoire-output"]
    assert not Path(fake.kwargs["cwd"]).exists()
    assert "GRIMOIRE_TASK_ID" not in fake.kwargs["env"]
    assert fake.kwargs["timeout"] == cp.DEFAULT_TIMEOUT


def test_payload_and_output_are_masked(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "cursor", "refused")
    project = str(fake.kwargs["cwd"])
    assert capture["payloads"]["pre_tool_use"]["cwd"] == "<PROJECT>"
    assert project not in capture["output"]["stdout"] and "<PROJECT>" in capture["output"]["stdout"]
    assert capture["observed"]["hook_stdout"]["permission"] == "deny"
    assert "--force" in fake.session and capture["captured_with"].startswith("/opt/bin/cursor-agent -p")


def test_gemini_session_trusts_only_the_throwaway_folder(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "gemini", "refused")
    assert fake.kwargs["env"]["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
    assert capture["observed"]["hook_exit_code"] == 2 and capture["observed"]["hook_stdout"] is None


def test_opencode_capture_records_the_throw(tmp_path: Path) -> None:
    capture, _, fake = _probe(tmp_path, "opencode", "refused")
    assert capture["observed"]["hook_throws"] == cp.DENY_REASON
    assert fake.session[:4] == ["/opt/bin/opencode", "run", "--format", "json"]


@pytest.mark.parametrize(
    ("scenario", "verdict"),
    [("executed", cp.EXECUTED), ("ignored", cp.EXECUTED), ("silent", cp.INCONCLUSIVE), ("timeout", cp.ERROR), ("oserror", cp.ERROR)],
)
def test_other_outcomes_never_show_refusal(tmp_path: Path, scenario: str, verdict: str) -> None:
    capture, _, _ = _probe(tmp_path, "codex", scenario)
    assert capture["verdict"] == verdict
    assert not capture_shows_refusal(capture)
    assert capture["promotable"] is False
    if scenario == "timeout":
        assert capture["observed"]["timed_out"] is True and "délai" in capture["reason"]


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
    capture, _, _ = _probe(tmp_path, "codex", "garbage")
    assert capture["observed"]["hook_calls"] == 1 and capture["observed"]["hook_stdout"] == "pas du json"
    assert capture["payloads"]["pre_tool_use"] == {"args": ["<PROJECT>", 1]}
    assert capture["verdict"] == cp.REFUSED  # code 2 du témoin, outil non exécuté


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
    (root / host).mkdir(parents=True)
    shutil.copy(FIXTURES / host / "manifest.json", root / host / "manifest.json")
    return root


def test_promote_copies_the_capture_and_updates_the_manifest(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "cursor", "refused")
    root = _fixtures(tmp_path)
    result = cp.promote(path, root, now=lambda: NOW)
    assert result.relpath == "cursor/01-pretooluse-deny-probe-1.2.3.json"
    fixture = json.loads(result.fixture.read_text())
    assert capture_shows_refusal(fixture) and fixture["promoted_on"] == "2026-11-22"
    manifest = json.loads(result.manifest.read_text())
    assert manifest["host_version"] == "1.2.3"
    assert manifest["capabilities"]["pretooluse_deny"]["status"] == "verified"
    assert manifest["capabilities"]["pretooluse_deny"]["fixtures"] == [result.relpath]
    assert manifest["capabilities"]["session_end"]["status"] == "unverified"
    assert any("blocking_captures" in line for line in result.instructions)
    with pytest.raises(cp.PromotionRefusedError, match="déjà versée"):
        cp.promote(path, root)


@pytest.mark.parametrize("scenario", ["executed", "silent", "timeout"])
def test_promote_refuses_a_capture_without_refusal(tmp_path: Path, scenario: str) -> None:
    _, path, _ = _probe(tmp_path, "cursor", scenario)
    with pytest.raises(cp.PromotionRefusedError, match="refus"):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_refuses_a_forged_verdict(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "cursor", "silent")
    forged = json.loads(path.read_text())
    forged["verdict"] = "refused"
    forged["observed"]["hook_exit_code"] = 2
    path.write_text(json.dumps(forged))
    with pytest.raises(cp.PromotionRefusedError, match="refus"):
        cp.promote(path, _fixtures(tmp_path))


def test_promote_refuses_antigravity_and_unknown_hosts(tmp_path: Path) -> None:
    _, path, _ = _probe(tmp_path, "antigravity", "refused")
    with pytest.raises(cp.PromotionRefusedError, match="non versable"):
        cp.promote(path, _fixtures(tmp_path))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"capability": "session_end"}, "capacité"),
        ({"host_version": ""}, "version"),
    ],
)
def test_promote_refuses_bad_fields(tmp_path: Path, change: dict[str, str], match: str) -> None:
    _, path, _ = _probe(tmp_path, "codex", "refused")
    path.write_text(json.dumps({**json.loads(path.read_text()), **change}))
    with pytest.raises(cp.PromotionRefusedError, match=match):
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
    manifest = json.loads(manifest_path.read_text())
    manifest["capabilities"]["session_end"]["status"] = "verified"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(cp.PromotionRefusedError, match="une version par manifeste"):
        cp.promote(path, root)
    # Seul pretooluse_deny prouvé sur l'ancienne version : la nouvelle capture le remplace.
    manifest["capabilities"]["session_end"]["status"] = "unverified"
    manifest_path.write_text(json.dumps(manifest))
    result = cp.promote(path, root)
    assert json.loads(result.manifest.read_text())["capabilities"]["pretooluse_deny"]["fixtures"] == [result.relpath]


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli(*args: str) -> Any:
    from grimoire.cli.app import app

    return CliRunner().invoke(app, list(args), env={"COLUMNS": "220"})


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


def test_cli_run_launches_one_session_per_installed_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_detect(targets: tuple[cp.HeadlessHost, ...], root: Path) -> tuple[cp.Detection, ...]:
        return (
            cp.Detection(_target("opencode"), "/opt/bin/opencode", "1.17.11"),
            cp.Detection(_target("copilot"), "/usr/bin/copilot", ""),
            cp.Detection(_target("codex"), "", ""),
        )

    launched: list[str] = []

    def fake_run(target: cp.HeadlessHost, root: Path, **kwargs: Any) -> tuple[dict[str, Any], Path]:
        launched.append(target.key)
        assert kwargs["timeout"] == 30
        return {"verdict": "refused", "reason": ""}, root / "x.json"

    monkeypatch.setattr(cp, "detect", fake_detect)
    monkeypatch.setattr(cp, "run_probe", fake_run)
    result = _cli("host", "probe", "--capabilities", "--run", "--timeout", "30", "--project-root", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert launched == ["opencode"]
    assert "refused" in result.output and "--promote" in result.output
    json_result = _cli("-o", "json", "host", "probe", "--capabilities", "--run", "--timeout", "30", "--project-root", str(tmp_path))
    assert json.loads(json_result.output)["captures"][0]["host"] == "opencode"


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
    again = _cli("-o", "json", "host", "probe", "--promote", str(good), "--fixtures-root", str(root))
    assert again.exit_code == 1 and json.loads(again.output)["promoted"] is False
    _, codex, _ = _probe(tmp_path, "codex", "refused")
    croot = _fixtures(tmp_path / "c", "codex")
    ok = _cli("-o", "json", "host", "probe", "--promote", str(codex), "--fixtures-root", str(croot))
    assert ok.exit_code == 0 and json.loads(ok.output)["fixture"].startswith("codex/")
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
