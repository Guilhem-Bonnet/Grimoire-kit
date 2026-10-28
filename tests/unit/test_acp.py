"""Pont ACP : machine d'états, client contre un faux agent, parité Rust/Python, roue py3-none-any."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from grimoire.acp import AcpClient, AcpError, PermissionRequest, new_machine, replay, rust_backend_available
from grimoire.hosts.capabilities import all_profiles

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "acp"
FAKE_AGENT = FIXTURES / "fake_agent.py"
TRANSCRIPTS = sorted(p.name.removesuffix(".transcript.json") for p in FIXTURES.glob("*.transcript.json"))

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_acp_core absent — maturin develop dans rust/grimoire-acp-core, ou le job CI rust-cores",
)


def _load(name: str, kind: str) -> Any:
    return json.loads((FIXTURES / f"{name}.{kind}.json").read_text(encoding="utf-8"))


def _client(name: str, tmp_path: Path, **kwargs: Any) -> AcpClient:
    return AcpClient(
        (sys.executable, str(FAKE_AGENT), str(FIXTURES / f"{name}.transcript.json")), tmp_path, request_timeout=15, **kwargs
    )


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "update" and e["kind"] == "agent_message_chunk")


# ── Transcriptions enregistrées : événements et états attendus ──────────────


def test_there_are_recorded_transcripts() -> None:
    assert len(TRANSCRIPTS) >= 6


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_python_machine_replays_each_transcript_to_its_golden(name: str) -> None:
    assert replay(_load(name, "transcript"), "python") == _load(name, "expected")


@requires_rust_core
@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_rust_machine_replays_each_transcript_to_the_same_golden(name: str) -> None:
    transcript = _load(name, "transcript")
    assert replay(transcript, "rust") == _load(name, "expected")
    assert replay(transcript, "rust") == replay(transcript, "python")


@requires_rust_core
def test_rust_native_replay_agrees_with_the_python_driven_one() -> None:
    import grimoire_acp_core

    for name in TRANSCRIPTS:
        native = json.loads(grimoire_acp_core.replay(json.dumps(_load(name, "transcript"))))
        assert native == _load(name, "expected"), name


def test_the_default_backend_is_python_and_rust_is_never_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_ACP_BACKEND", raising=False)
    assert type(new_machine()).__name__ == "PyMachine"
    with pytest.raises(ValueError, match="inconnu"):
        new_machine("fortran")


def test_the_machine_never_raises_on_garbage() -> None:
    m = new_machine("python")
    for junk in (None, 3, [], {}, {"jsonrpc": "2.0", "id": 1.5, "method": "x"}, {"jsonrpc": "2.0", "id": True, "method": "x"}):
        assert m.on_incoming(junk)[0]["type"] == "error"
        assert m.on_outgoing(junk)[0]["type"] == "error"
    assert m.state == "new"


def test_a_refused_message_changes_nothing() -> None:
    m = new_machine("python")
    before = m.snapshot()
    events = m.on_outgoing({"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"sessionId": "s", "prompt": []}})
    assert events[0]["code"] == "no_session"
    assert m.snapshot() == before


# ── Échange complet contre un faux agent (sous-processus) ───────────────────


def test_full_exchange_initialize_new_prompt_stream_then_end_of_turn(tmp_path: Path) -> None:
    with _client("full_exchange", tmp_path) as acp:
        assert acp.state == "ready"
        assert acp.new_session() == "sess-1"
        first = list(acp.prompt("bonjour"))
        assert _text(first) == "Bonjour !"
        assert [e["type"] for e in first][-1] == "turn_ended"
        assert first[-1]["stop_reason"] == "end_turn"
        assert acp.state == "idle"
        second = list(acp.prompt("encore"))
        assert second[-1]["stop_reason"] == "max_tokens"
    assert acp.state == "closed"


def test_the_agent_runs_in_the_project_folder(tmp_path: Path) -> None:
    script = tmp_path / "pwd_agent.py"
    script.write_text(
        "import json, os, sys\n"
        "json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': 1}}), flush=True)\n"
        "req = json.loads(sys.stdin.readline())\n"
        "assert req['params']['cwd'] == os.getcwd(), (req['params']['cwd'], os.getcwd())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': {'sessionId': 'p'}}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    project.mkdir()
    with AcpClient((sys.executable, str(script)), project, request_timeout=15) as acp:
        assert acp.new_session() == "p"


def test_fs_and_terminal_capabilities_are_not_announced(tmp_path: Path) -> None:
    script = tmp_path / "caps_agent.py"
    script.write_text(
        "import json, sys\n"
        "caps = json.loads(sys.stdin.readline())['params']['clientCapabilities']\n"
        "ok = caps == {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': 1 if ok else 99}}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    with AcpClient((sys.executable, str(script)), tmp_path, request_timeout=15) as acp:
        assert acp.state == "ready"


def test_a_missing_agent_command_says_so(tmp_path: Path) -> None:
    with pytest.raises(AcpError, match="introuvable"):
        AcpClient(("grimoire-no-such-acp-agent",), tmp_path).start()


def test_an_agent_dying_mid_exchange_raises_with_its_stderr(tmp_path: Path) -> None:
    script = tmp_path / "dying.py"
    script.write_text("import sys\nsys.stderr.write('auth requise\\n')\nsys.exit(1)\n", encoding="utf-8")
    with pytest.raises(AcpError, match="auth requise"):
        AcpClient((sys.executable, str(script)), tmp_path, request_timeout=15).start()


# ── Permissions et annulation ───────────────────────────────────────────────


@pytest.mark.parametrize(("name", "kinds", "expected_outcome", "expected_text"), [
    ("permission_granted", ("allow_once", "allow_always"), "granted", "ecrit"),
    ("permission_denied", ("reject_once", "reject_always"), "denied", "refuse"),
])
def test_permission_request_is_handed_to_the_caller_and_the_answer_goes_back(
    name: str, kinds: tuple[str, ...], expected_outcome: str, expected_text: str, tmp_path: Path
) -> None:
    seen: list[PermissionRequest] = []

    def handler(request: PermissionRequest) -> str | None:
        seen.append(request)
        return request.option_of_kind(*kinds)

    with _client(name, tmp_path) as acp:
        acp.new_session()
        events = list(acp.prompt("ecris notes.md", on_permission=handler))
    assert len(seen) == 1
    assert seen[0].tool_call_id == "call-1"
    assert [o["kind"] for o in seen[0].options] == ["allow_once", "reject_once"]
    resolved = [e for e in events if e["type"] == "permission_resolved"]
    assert [r["outcome"] for r in resolved] == [expected_outcome]
    assert _text(events) == expected_text  # l'agent n'avance que si sa réponse attendue lui est parvenue
    assert events[-1]["type"] == "turn_ended"


def test_without_a_handler_a_permission_is_cancelled_never_granted(tmp_path: Path) -> None:
    with _client("permission_granted", tmp_path) as acp:
        acp.new_session()
        with pytest.raises(AcpError, match="arrêté"):
            # le faux agent attend `allow` et sort en code 3 sur `cancelled` : preuve que rien n'est accordé
            list(acp.prompt("x"))
        assert acp.agent_events[-1]["type"] != "turn_ended"


def test_an_unknown_option_choice_is_cancelled(tmp_path: Path) -> None:
    with _client("permission_granted", tmp_path) as acp:
        acp.new_session()
        with pytest.raises(AcpError):
            list(acp.prompt("x", on_permission=lambda r: "n-importe-quoi"))


def test_cancel_interrupts_the_turn_and_cancels_the_pending_permission(tmp_path: Path) -> None:
    def handler(request: PermissionRequest) -> str | None:
        acp.cancel()  # l'utilisateur interrompt pendant que la question est posée
        return request.option_of_kind("allow_once")  # trop tard : la demande est déjà annulée

    with _client("cancel", tmp_path) as acp:
        acp.new_session()
        events = list(acp.prompt("long travail", on_permission=handler))
        assert events[-1] == {"type": "turn_ended", "session_id": "sess-1", "stop_reason": "cancelled"}
        resolved = [e for e in acp.agent_events if e["type"] == "permission_requested"]
        assert len(resolved) == 1
        assert acp.state == "idle"
        assert _text(list(acp.prompt("apres"))) == "ok"  # la session reste utilisable


def test_cancel_is_a_no_op_outside_a_turn(tmp_path: Path) -> None:
    with _client("full_exchange", tmp_path) as acp:
        acp.new_session()
        acp.cancel()
        assert acp.state == "idle"


def test_cancel_from_another_thread_reaches_the_agent(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "import json, sys\n"
        "def send(o): print(json.dumps(o), flush=True)\n"
        "json.loads(sys.stdin.readline()); send({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': 1}})\n"
        "json.loads(sys.stdin.readline()); send({'jsonrpc': '2.0', 'id': 2, 'result': {'sessionId': 's'}})\n"
        "json.loads(sys.stdin.readline())\n"
        "send({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': 's', 'update': "
        "{'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'a'}}}})\n"
        "m = json.loads(sys.stdin.readline())\n"
        "assert m['method'] == 'session/cancel'\n"
        "send({'jsonrpc': '2.0', 'id': 3, 'result': {'stopReason': 'cancelled'}})\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    with AcpClient((sys.executable, str(script)), tmp_path, request_timeout=15) as acp:
        acp.new_session()
        stream = acp.prompt("go", idle_timeout=15)
        assert next(stream)["type"] == "update"
        threading.Thread(target=acp.cancel).start()
        rest = list(stream)
    assert rest[-1]["stop_reason"] == "cancelled"


# ── session/load ────────────────────────────────────────────────────────────


def test_load_session_resumes_when_the_agent_announces_load_session(tmp_path: Path) -> None:
    with _client("load_supported", tmp_path) as acp:
        assert acp.supports_load_session
        assert acp.load_session("old-7") == "old-7"
        events = list(acp.prompt("suite"))
        assert _text(events) == "reprise"
        assert any(e["kind"] == "user_message_chunk" for e in acp.agent_events if e["type"] == "update")


def test_load_session_is_refused_cleanly_when_the_agent_does_not_announce_it(tmp_path: Path) -> None:
    with _client("load_unsupported", tmp_path) as acp:
        assert not acp.supports_load_session
        with pytest.raises(AcpError, match="loadSession"):
            acp.load_session("old-7")
        assert acp.state == "ready"  # rien n'a été envoyé, la session neuve passe encore
        assert acp.new_session() == "sess-1"


def test_an_agent_error_on_prompt_raises_and_leaves_the_session_usable(tmp_path: Path) -> None:
    script = tmp_path / "erroring.py"
    script.write_text(
        "import json, sys\n"
        "def send(o): print(json.dumps(o), flush=True)\n"
        "json.loads(sys.stdin.readline()); send({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': 1}})\n"
        "json.loads(sys.stdin.readline()); send({'jsonrpc': '2.0', 'id': 2, 'result': {'sessionId': 's'}})\n"
        "json.loads(sys.stdin.readline())\n"
        "send({'jsonrpc': '2.0', 'id': 3, 'error': {'code': -32000, 'message': 'quota'}})\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    with AcpClient((sys.executable, str(script)), tmp_path, request_timeout=15) as acp:
        acp.new_session()
        with pytest.raises(AcpError, match="quota"):
            list(acp.prompt("x"))
        assert acp.state == "idle"


# ── Table de lancement par hôte ─────────────────────────────────────────────


def test_every_known_host_declares_its_acp_command() -> None:
    table = {p.host_id.value: p.acp_command for p in all_profiles()}
    assert all(table.values()), table
    by_name = {p.display_name: p.acp_command for p in all_profiles()}
    assert by_name["Gemini CLI"] == ("gemini", "--acp")
    assert by_name["Claude Code"] == ("claude-agent-acp",)


# ── CLI de démonstration ────────────────────────────────────────────────────


def test_chat_command_prints_the_stream_and_asks_permissions_in_the_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    transcript = FIXTURES / "permission_granted.transcript.json"
    result = CliRunner().invoke(
        app,
        [
            "chat", "--host", "gemini", "--project-root", str(tmp_path), "ecris notes.md",
            "--agent-command", f"{sys.executable} {FAKE_AGENT} {transcript}",
        ],
        input="1\n",
    )
    assert result.exit_code == 0, result.output
    assert "ecrit" in result.output
    assert "Permission demandée" in result.output
    assert "fin du tour : end_turn" in result.output


def test_chat_command_rejects_an_unknown_host(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    result = CliRunner().invoke(app, ["chat", "--host", "emacs", "salut"])
    assert result.exit_code == 2


# ── La roue reste py3-none-any, sans Rust, et grimoire.acp tourne sans le module compilé ──


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv absent : la roue ne peut pas être construite ici")
    out = tmp_path_factory.mktemp("wheel")
    proc = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out), str(REPO)], capture_output=True, text=True, timeout=600, check=False
    )
    if proc.returncode != 0:
        pytest.skip(f"construction de la roue impossible (réseau ?) : {proc.stderr[-300:]}")
    return next(out.glob("*.whl"))


def test_the_built_wheel_is_universal_and_embeds_no_rust(built_wheel: Path) -> None:
    assert built_wheel.name.endswith("-py3-none-any.whl")
    names = zipfile.ZipFile(built_wheel).namelist()
    assert "grimoire/acp/machine.py" in names
    forbidden = [n for n in names if n.endswith((".rs", ".so", ".pyd", ".dylib")) or "Cargo." in n or n.startswith("rust/")]
    assert forbidden == []
    assert not any("grimoire_acp_core" in n for n in names)


def test_grimoire_acp_works_from_the_wheel_without_the_compiled_module(built_wheel: Path) -> None:
    code = (
        "import json, sys\n"
        "sys.modules['grimoire_acp_core'] = None  # comme si le module compilé n'était pas installé\n"
        f"sys.path.insert(0, {str(built_wheel)!r})\n"
        "import grimoire.acp as acp\n"
        "assert acp.__file__.startswith(sys.path[0]), acp.__file__\n"
        "assert not acp.rust_backend_available()\n"
        f"t = json.load(open({str(FIXTURES / 'full_exchange.transcript.json')!r}))\n"
        f"want = json.load(open({str(FIXTURES / 'full_exchange.expected.json')!r}))\n"
        "assert acp.replay(t) == want\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
