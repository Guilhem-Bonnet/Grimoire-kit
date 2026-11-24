"""``errorOccurred`` / ``userPromptTransformed`` sous Copilot CLI (#1280, P3).

La charge rejoue ``tests/fixtures/hosts/copilot/documented/error-occurred.json``
(forme documentée par https://docs.github.com/en/copilot/reference/hooks-reference,
pas une capture).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions.error_occurred import MAX_MESSAGE
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import main, parse_event, run_hook

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/hosts/copilot/documented/error-occurred.json"
DECISION = "grimoire.error-occurred"


def _payload() -> dict[str, Any]:
    return dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["payloads"][0]["input"])


def _root(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire").mkdir(exist_ok=True)
    return tmp_path


def _traces(root: Path) -> list[dict[str, Any]]:
    lines = [ln for p in sorted(root.rglob("traces.jsonl")) for ln in p.read_text(encoding="utf-8").splitlines()]
    return [json.loads(ln) for ln in lines]


def _run(root: Path, payload: dict[str, Any], event: HookEvent = HookEvent.ERROR_OCCURRED, decision: str = DECISION):
    return run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=event, project_root=root, decision_id=decision)


def test_event_names_resolve() -> None:
    assert parse_event("errorOccurred", HostId.GITHUB_COPILOT) is HookEvent.ERROR_OCCURRED
    assert parse_event("ErrorOccurred", HostId.GITHUB_COPILOT) is HookEvent.ERROR_OCCURRED
    assert parse_event("userPromptTransformed", HostId.GITHUB_COPILOT) is HookEvent.USER_PROMPT_TRANSFORMED


def test_error_is_journaled_and_never_blocks(tmp_path: Path) -> None:
    root = _root(tmp_path)
    rendered, decision, _ = _run(root, _payload())
    assert rendered == {}
    assert decision.abstain
    assert decision.detail["error_name"] == "ToolError"
    assert decision.detail["error_context"] == "tool_execution"
    assert decision.detail["recoverable"] is True
    assert "stack" not in json.dumps(decision.detail)
    assert "ToolError: boom" not in json.dumps(decision.detail)
    assert _traces(root), "l'erreur doit atteindre le ledger de traces"
    assert "error_context:tool_execution" in json.dumps(_traces(root))


def test_message_is_truncated_and_tool_kept_when_present(tmp_path: Path) -> None:
    payload = _payload()
    payload["error"] = {"message": "x" * 5000, "name": "E"}
    payload["toolName"] = "bash"
    _, decision, _ = _run(_root(tmp_path), payload)
    assert len(decision.detail["error_message"]) == MAX_MESSAGE
    assert decision.detail["tool"] == "bash"


def test_huge_tool_name_is_bounded(tmp_path: Path) -> None:
    root = _root(tmp_path)
    payload = _payload()
    payload["toolName"] = "t" * 100_000
    _, decision, _ = _run(root, payload)
    assert len(decision.detail["tool"]) <= 80
    assert all(len(json.dumps(t)) < 2000 for t in _traces(root))


def test_secrets_are_masked_in_message(tmp_path: Path) -> None:
    root = _root(tmp_path)
    token = "ghp_" + "a1B2c3D4e5" * 4
    payload = _payload()
    payload["error"] = {"message": f"clone failed with {token} and sk-{'z9' * 20}", "name": "E"}
    _, decision, _ = _run(root, payload)
    blob = json.dumps(decision.detail) + json.dumps(_traces(root))
    assert token not in blob and "sk-z9z9" not in blob
    assert "[redacted:" in decision.detail["error_message"]


@pytest.mark.parametrize("error", [None, "chaîne seule", 42, ["x"]])
def test_malformed_error_never_raises(tmp_path: Path, error: Any) -> None:
    rendered, decision, _ = _run(_root(tmp_path), {**_payload(), "error": error})
    assert rendered == {}
    assert decision.abstain


def test_vscode_snake_case_form(tmp_path: Path) -> None:
    payload = {
        "hook_event_name": "ErrorOccurred",
        "session_id": "s1",
        "timestamp": "2026-11-23T10:00:00.000Z",
        "cwd": str(tmp_path),
        "error": {"message": "m", "name": "N"},
        "error_context": "model_call",
        "recoverable": False,
    }
    _, decision, _ = _run(_root(tmp_path), payload)
    assert decision.detail["error_context"] == "model_call"
    assert decision.detail["recoverable"] is False


def test_entry_point_exits_zero_even_on_garbage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io

    root = _root(tmp_path)
    monkeypatch.chdir(root)
    for body in (json.dumps(_payload()), "{pas du json", ""):
        monkeypatch.setattr("sys.stdin", io.StringIO(body))
        code = main(["--host", "copilot", "--event", "errorOccurred", "--decision", DECISION])
        assert code == 0
        capsys.readouterr()


def test_user_prompt_transformed_traces_event_without_prompt(tmp_path: Path) -> None:
    root = _root(tmp_path)
    payload = {"sessionId": "s2", "timestamp": 1, "cwd": str(root), "prompt": "SECRET-PROMPT-TEXT"}
    rendered, decision, _ = _run(root, payload, HookEvent.USER_PROMPT_TRANSFORMED, "grimoire.event-trace")
    assert "updatedPrompt" not in json.dumps(rendered)
    assert "SECRET-PROMPT-TEXT" not in json.dumps(decision.detail)
    assert "SECRET-PROMPT-TEXT" not in json.dumps(_traces(root))


def test_copilot_emitter_wires_both_hooks_only_there(tmp_path: Path) -> None:
    surface = build_surface(tmp_path)
    files = {str(f.relpath): f for f in emitter_for(HostId.GITHUB_COPILOT).plan(surface, tmp_path).files}
    err = next(f for p, f in files.items() if p.endswith("grimoire-error-occurred.json"))
    entry = json.loads(err.content)["hooks"]["ErrorOccurred"][0]
    assert f"--decision {DECISION}" in entry["bash"]
    assert "ErrorOccurred" in entry["bash"]
    upt = next(f for p, f in files.items() if p.endswith("grimoire-user-prompt-transformed.json"))
    assert "UserPromptTransformed" in json.loads(upt.content)["hooks"]
    claude = emitter_for(HostId.CLAUDE_CODE_CLI).plan(surface, tmp_path)
    assert all(DECISION not in f.content for f in claude.files if isinstance(f.content, str))


def _detail_for(tmp_path: Path, **error: Any) -> dict[str, Any]:
    payload = _payload()
    payload.pop("toolName", None)
    payload.pop("tool_name", None)
    payload["error"] = {"name": "ToolError", "message": "x", **error}
    return _run(_root(tmp_path), payload)[1].detail


def test_pem_then_cut_ghp_leaves_no_token_fragment(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.error_occurred import _SCAN_WINDOW

    dashes = "-" * 5
    pem = f"{dashes}BEGIN RSA PRIVATE KEY{dashes}\nAAAA\n{dashes}END RSA PRIVATE KEY{dashes}"
    token = "ghp_" + "A" * 40
    # Le jeton est coupé par la fenêtre : il n'en reste que `ghp_AAAA…` sous 30 caractères.
    filler = "x " * ((_SCAN_WINDOW - 26 - len(pem) - 1) // 2)
    message = pem + " " + filler
    message += "y" * (_SCAN_WINDOW - len(message) - 12) + " " + token
    assert len(message) > _SCAN_WINDOW
    detail = _detail_for(tmp_path, message=message)
    assert "ghp_" not in detail["error_message"]


def test_name_context_and_tool_are_redacted(tmp_path: Path) -> None:
    token = "ghp_" + "B" * 40
    payload = _payload()
    payload["error"] = {"name": token, "message": "m"}
    payload["errorContext"] = token
    payload["toolName"] = token
    detail = _run(_root(tmp_path), payload)[1].detail
    assert "ghp_" not in json.dumps(detail)
    assert "[redacted:github-token]" in detail["error_name"]
