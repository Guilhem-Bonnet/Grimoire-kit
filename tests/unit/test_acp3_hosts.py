"""ACP3 : tous les hôtes du chat d'orchestrateur — table ACP, choix du projet, doctor, repli kanban."""

from __future__ import annotations

import json
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from grimoire.cli.doctor_acp import check_acp_hosts
from grimoire.hosts import acp_hosts
from grimoire.hosts.capabilities import HostId, all_profiles, profile_for
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_chat

# ── La table de lancement ───────────────────────────────────────────────────


def test_every_host_profile_declares_its_acp_command() -> None:
    profiles = all_profiles()
    assert {p.host_id for p in profiles} >= {
        HostId.CLAUDE_CODE_CLI,
        HostId.GEMINI_CLI,
        HostId.GITHUB_COPILOT,
        HostId.CODEX,
        HostId.CURSOR,
        HostId.OPENCODE,
    }
    for profile in profiles:
        assert profile.acp_command, f"{profile.display_name} ne déclare pas d'acp_command"


@pytest.mark.parametrize(
    ("host", "command"),
    [
        (HostId.GEMINI_CLI, ("gemini", "--acp")),
        (HostId.GITHUB_COPILOT, ("copilot", "--acp")),
        (HostId.CODEX, ("npx", "-y", "@agentclientprotocol/codex-acp")),
        (HostId.CURSOR, ("agent", "acp")),
        (HostId.OPENCODE, ("opencode", "acp")),
    ],
)
def test_the_launch_table_matches_the_documented_commands(host: HostId, command: tuple[str, ...]) -> None:
    assert profile_for(host).acp_command == command


def test_copilot_is_flagged_preview_and_gemini_documents_the_antigravity_move() -> None:
    assert profile_for(HostId.GITHUB_COPILOT).acp_preview is True
    assert "Antigravity" in profile_for(HostId.GEMINI_CLI).acp_note


# ── Le choix de l'hôte par projet ───────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(workspace_chat.AGENT_COMMAND_ENV, raising=False)
    monkeypatch.delenv(acp_hosts.HOST_ENV, raising=False)
    root = tmp_path / "projet"
    (root / ".git").mkdir(parents=True)
    return root


def _installed(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(acp_hosts.shutil, "which", lambda n: f"/bin/{n}" if n in names else None)


def test_the_chat_reads_the_projects_chosen_host(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "gemini", "claude-agent-acp")
    assert acp_hosts.set_chosen_alias(project, "gemini") == "gemini"
    assert workspace_chat.chat_mode(project) == ("acp", ("gemini", "--acp"), "")
    assert workspace_chat.agent_command(project) == ("gemini", "--acp")
    acp_hosts.set_chosen_alias(project, "claude")
    assert workspace_chat.agent_command(project) == ("claude-agent-acp",)


def test_without_a_setting_the_detected_host_is_the_default(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "GEMINI.md").write_text("x", encoding="utf-8")
    _installed(monkeypatch, "gemini")
    assert acp_hosts.chosen_alias(project) == "gemini"
    assert workspace_chat.agent_command(project) == ("gemini", "--acp")
    acp_hosts.set_chosen_alias(project, "")  # revient à l'hôte détecté
    assert acp_hosts.chosen_alias(project) == "gemini"


def test_uninstalled_hosts_are_greyed_out_with_their_reason(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "gemini")
    by_alias = {c.alias: c for c in acp_hosts.host_choices(project)}
    assert by_alias["gemini"].available and not by_alias["gemini"].reason
    assert not by_alias["cursor"].available
    assert "agent" in by_alias["cursor"].reason and "PATH" in by_alias["cursor"].reason
    assert not by_alias["codex"].available  # npx absent
    assert by_alias["copilot"].preview is True
    assert by_alias["kanban"].available and by_alias["kanban"].mode == "kanban"


def test_codex_needs_both_npx_and_the_codex_cli(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "npx")
    assert not acp_hosts.acp_availability(profile_for(HostId.CODEX))[0]
    _installed(monkeypatch, "npx", "codex")
    assert acp_hosts.acp_availability(profile_for(HostId.CODEX))[0]


def test_an_unknown_host_is_refused_and_nothing_is_written(project: Path) -> None:
    with pytest.raises(ValueError, match="inconnu"):
        acp_hosts.set_chosen_alias(project, "emacs")
    assert not (project / acp_hosts.SETTING_RELPATH).exists()


def test_the_host_routes_list_and_set_the_choice(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "opencode")
    state = workspace_chat.chat_host(project, {"host": "opencode"})
    assert state["chosen"] == "opencode" and state["mode"] == "acp"
    assert workspace_chat.chat_hosts(project, {})["chosen"] == "opencode"
    with pytest.raises(ValueError, match="requis"):
        workspace_chat.chat_host(project, {})


# ── Doctor : initialize, sans prompt ────────────────────────────────────────

_AGENT = """#!{python}
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    with open({log!r}, "a") as fh:
        fh.write(msg.get("method", "?") + "\\n")
    if msg.get("method") == "initialize":
        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"protocolVersion": 1, "agentCapabilities": {{}}}}}}), flush=True)
"""


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


@pytest.mark.skipif(sys.platform == "win32", reason="faux hôtes en scripts à shebang : non exécutables sous Windows")
def test_doctor_reports_each_installed_host_whose_acp_answers_initialize_without_a_prompt(
    fake_path: Path, tmp_path: Path
) -> None:
    log = tmp_path / "methods.log"
    _fake_bin(fake_path, "gemini", _AGENT.format(python=sys.executable, log=str(log)))
    _fake_bin(fake_path, "opencode", f"#!{sys.executable}\nimport sys\nsys.exit(3)\n")  # installé mais muet
    checks = {c.name: c for c in check_acp_hosts(tmp_path)}
    assert set(checks) == {"acp_host-gemini-cli", "acp_host-opencode"}  # les hôtes absents ne sont pas rapportés
    assert checks["acp_host-gemini-cli"].level == "ok"
    assert "initialize" in checks["acp_host-gemini-cli"].detail
    assert checks["acp_host-opencode"].level == "warn"
    assert checks["acp_host-opencode"].passed  # un mode muet n'est pas une panne : le chat se replie
    assert log.read_text(encoding="utf-8").split() == ["initialize"]  # ni session/new, ni session/prompt


def test_doctor_reports_nothing_when_no_host_is_installed(fake_path: Path, tmp_path: Path) -> None:
    assert check_acp_hosts(tmp_path) == []


@pytest.mark.skipif(sys.platform == "win32", reason="faux hôtes en scripts à shebang : non exécutables sous Windows")
def test_doctor_marks_a_preview_host(fake_path: Path, tmp_path: Path) -> None:
    _fake_bin(fake_path, "copilot", f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
    (check,) = check_acp_hosts(tmp_path)
    assert "preview" in check.detail


# ── Repli kanban (Copilot dans VS Code) ─────────────────────────────────────


@pytest.fixture
def kanban(project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(project.parent / "ck"))
    monkeypatch.setenv(workspace_chat.GRACE_ENV, "0")
    _installed(monkeypatch)  # aucun hôte ACP sur le PATH : Copilot dans VS Code n'a rien à lancer
    acp_hosts.set_chosen_alias(project, "copilot")
    yield project
    workspace_chat.shutdown_all()


def _live_task(root: Path) -> tuple[TaskService, str]:
    service = TaskService(root)
    added = service.add("Tâche vivante", ("un critère",), ready=True)
    service.claim(added.task.id, "copilot-vscode")
    return service, added.task.id


def _until(feed_events: list[dict], gen: Iterator[bytes], predicate, timeout: float = 15.0) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in feed_events:
            if predicate(event):
                return event
        for line in next(gen).decode().splitlines():
            if line.startswith("data: "):
                feed_events.append(json.loads(line[6:]))
    raise AssertionError(f"événement attendu non reçu : {feed_events}")


def test_without_an_acp_host_the_chat_writes_a_directive_and_shows_the_reply_a_session_leaves(kanban: Path) -> None:
    service, task_id = _live_task(kanban)
    assert workspace_chat.chat_mode(kanban)[0] == "kanban"

    gen = workspace_chat.chat_stream(kanban, parse_qs(""))
    events: list[dict] = []
    try:
        _until(events, gen, lambda e: e["type"] == "hello")
        sent = workspace_chat.chat_send(kanban, {"text": "où en es-tu ?"})
        assert sent["mode"] == "kanban" and sent["task_id"] == task_id

        # la consigne est au kanban, en canal humain — la session la lira au tour suivant
        (directive,) = [d for d in TaskService(kanban).require(task_id).directives if d.text == "où en es-tu ?"]
        assert directive.kind == "directive" and directive.is_human and directive.author == "cockpit-chat"
        _until(events, gen, lambda e: e["type"] == "kanban_sent" and e["task_id"] == task_id)

        # plus tard, la session répond par une note de progression : le fil l'affiche
        service.note_progress(task_id, "Tests verts, PR en cours.", "copilot-vscode")
        reply = _until(events, gen, lambda e: e["type"] == "reply")
        assert reply["text"] == "Tests verts, PR en cours." and reply["task_id"] == task_id
        assert reply["actor"] == "copilot-vscode"
        assert [e["text"] for e in events if e["type"] == "user"] == ["où en es-tu ?"]  # pas de doublon
    finally:
        gen.close()


def test_a_reopened_chat_rebuilds_the_thread_from_the_ledger(kanban: Path) -> None:
    service, task_id = _live_task(kanban)
    workspace_chat.chat_send(kanban, {"text": "bilan ?"})
    service.note_progress(task_id, "Bilan : presque fini.", "copilot-vscode")
    workspace_chat.shutdown_all()  # le cockpit redémarre

    gen = workspace_chat.chat_stream(kanban, parse_qs(""))
    events: list[dict] = []
    try:
        _until(events, gen, lambda e: e["type"] == "reply")
    finally:
        gen.close()
    assert [e["text"] for e in events if e["type"] == "user"] == ["bilan ?"]
    assert [e["text"] for e in events if e["type"] == "reply"] == ["Bilan : presque fini."]


def test_the_kanban_fallback_refuses_an_ambiguous_target_and_writes_nothing(kanban: Path) -> None:
    with pytest.raises(ValueError, match="aucune tâche"):
        workspace_chat.chat_send(kanban, {"text": "allô"})
    service, first = _live_task(kanban)
    second = service.add("Autre", ("c",), ready=True).task.id
    service.claim(second, "autre")
    with pytest.raises(ValueError, match="plusieurs tâches"):
        workspace_chat.chat_send(kanban, {"text": "allô"})
    assert not TaskService(kanban).require(first).directives
    workspace_chat.chat_send(kanban, {"text": "allô", "task_id": second})  # nommée : accepté
    assert TaskService(kanban).require(second).directives[0].text == "allô"


def test_a_chosen_acp_host_does_not_use_the_kanban(kanban: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "gemini")
    acp_hosts.set_chosen_alias(kanban, "gemini")
    assert workspace_chat.chat_mode(kanban)[0] == "acp"
