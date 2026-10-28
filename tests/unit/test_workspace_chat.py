"""ACP2 : le chat de l'orchestrateur du cockpit, contre un faux agent ACP interactif.

Deux couches : le module (:mod:`grimoire.tools.workspace_chat`) piloté directement, et un vrai
serveur du cockpit sur la loopback — c'est là que se prouvent les gardes du POST.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_chat
from grimoire.tools.request_guard import SESSION_HEADER, session_token
from grimoire.tools.workspace_routes import CHAT_STREAM_PATH, GET_ROUTES, POST_ROUTES, PREFIX

from . import _cockpit_http

AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "chat_agent.py"
SEND, PERMISSION, CANCEL = f"{PREFIX}chat/send", f"{PREFIX}chat/permission", f"{PREFIX}chat/cancel"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    log = tmp_path / "agent-log.jsonl"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    monkeypatch.setenv(workspace_chat.AGENT_COMMAND_ENV, f"{sys.executable} {AGENT} {log}")
    monkeypatch.setenv(workspace_chat.GRACE_ENV, "0")
    monkeypatch.delenv("FAKE_NO_LOAD", raising=False)
    yield log
    workspace_chat.shutdown_all()


@pytest.fixture
def agent_log(_env: Path) -> Path:
    return _env


def _received(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "projet"
    (path / ".git").mkdir(parents=True)
    return path


class Feed:
    """Lit le flux SSE d'une conversation (générateur du module) jusqu'à un événement attendu."""

    def __init__(self, root: Path, query: str = "") -> None:
        from urllib.parse import parse_qs

        self.events: list[dict[str, Any]] = []
        self._gen = workspace_chat.chat_stream(root, parse_qs(query))

    def until(self, predicate: Any, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        for event in self.events:
            if predicate(event):
                return event
        while time.monotonic() < deadline:
            chunk = next(self._gen)
            for line in chunk.decode().splitlines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    self.events.append(event)
                    if predicate(event):
                        return event
        raise AssertionError(f"événement attendu non reçu : {self.events}")

    def close(self) -> None:
        self._gen.close()


def _is(kind: str, **fields: Any) -> Any:
    return lambda e: e["type"] == kind and all(e.get(k) == v for k, v in fields.items())


def _agent_text(feed: Feed) -> str:
    return "".join(
        e["update"]["content"]["text"]
        for e in feed.events
        if e["type"] == "update" and e["update"].get("sessionUpdate") == "agent_message_chunk"
    )


# ── Réponse en flux, puis fin de tour ───────────────────────────────────────


def test_a_message_streams_the_agent_reply_then_the_end_of_turn(root: Path) -> None:
    feed = Feed(root)
    try:
        assert workspace_chat.chat_send(root, {"text": "bonjour"})["accepted"] is True
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    types = [e["type"] for e in feed.events]
    assert types.index("user") < types.index("update") < types.index("turn_ended")
    assert _agent_text(feed).startswith("Bonjour, je suis **l'orchestrateur**")
    assert next(e for e in feed.events if e["type"] == "turn_ended")["stop_reason"] == "end_turn"
    assert [e["seq"] for e in feed.events if e["seq"]] == sorted(e["seq"] for e in feed.events if e["seq"])


def test_the_cockpit_sends_the_human_text_verbatim_and_writes_no_ledger(root: Path, agent_log: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "crée la tâche X"})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    prompts = [m for m in _received(agent_log) if m.get("method") == "session/prompt"]
    assert prompts[0]["params"]["prompt"] == [{"type": "text", "text": "crée la tâche X"}]
    # ADR-007 : le chat relaie un tour humain, le cockpit n'écrit que l'identifiant de session.
    written = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    assert written == ["_grimoire-runtime-output/cockpit/chat-session.json"]


def test_tool_calls_plans_and_diffs_reach_the_page_as_they_are(root: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "outils"})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    kinds = [e["update"]["sessionUpdate"] for e in feed.events if e["type"] == "update"]
    assert {"tool_call", "tool_call_update", "plan"} <= set(kinds)
    done = next(e for e in feed.events if e["type"] == "update" and e["update"]["sessionUpdate"] == "tool_call_update")
    assert done["update"]["content"][0] == {
        "type": "diff",
        "path": "README.md",
        "oldText": "ancien titre",
        "newText": "nouveau titre",
    }


def test_an_empty_message_and_a_second_concurrent_turn_are_refused(root: Path) -> None:
    with pytest.raises(ValueError, match="vide"):
        workspace_chat.chat_send(root, {"text": "   "})
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "lent"})
        feed.until(_is("turn_started"))
        with pytest.raises(ValueError, match="déjà en cours"):
            workspace_chat.chat_send(root, {"text": "encore"})
        workspace_chat.chat_cancel(root, {})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()


# ── Permissions ─────────────────────────────────────────────────────────────


def test_a_permission_request_is_shown_with_its_options_and_refuse_is_sent_back(root: Path, agent_log: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "permission"})
        asked = feed.until(_is("permission"))
        assert asked["tool_call"]["title"] == "Écrire notes.md"
        assert [o["kind"] for o in asked["options"]] == ["allow_once", "allow_always", "reject_once"]
        workspace_chat.chat_permission(root, {"request_id": asked["request_id"], "decision": "reject"})
        resolved = feed.until(_is("permission_resolved"))
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    assert resolved["option_id"] == "no"
    answers = [m for m in _received(agent_log) if m.get("id") == 900]
    assert answers[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "no"}
    assert "Permission reçue : no" in _agent_text(feed)


@pytest.mark.parametrize(("decision", "option"), [("allow_once", "once"), ("allow_always", "always")])
def test_allow_once_and_always_are_sent_as_the_matching_option(
    root: Path, agent_log: Path, decision: str, option: str
) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "permission"})
        asked = feed.until(_is("permission"))
        workspace_chat.chat_permission(root, {"request_id": asked["request_id"], "decision": decision})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    assert next(m for m in _received(agent_log) if m.get("id") == 900)["result"]["outcome"]["optionId"] == option


def test_an_unknown_decision_or_request_grants_nothing(root: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "permission"})
        asked = feed.until(_is("permission"))
        with pytest.raises(ValueError, match="décision inconnue"):
            workspace_chat.chat_permission(root, {"request_id": asked["request_id"], "decision": "allow_all"})
        with pytest.raises(ValueError, match="aucune demande"):
            workspace_chat.chat_permission(root, {"request_id": "999", "decision": "reject"})
        workspace_chat.chat_cancel(root, {})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()


# ── Stop ────────────────────────────────────────────────────────────────────


def test_stop_sends_session_cancel_and_the_turn_ends_cancelled(root: Path, agent_log: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "lent"})
        feed.until(_is("turn_started"))
        feed.until(lambda e: e["type"] == "update")
        assert workspace_chat.chat_cancel(root, {}) == {"ok": True, "cancelled": True}
        ended = feed.until(_is("turn_ended"))
    finally:
        feed.close()
    assert ended["stop_reason"] == "cancelled"
    cancels = [m for m in _received(agent_log) if m.get("method") == "session/cancel"]
    assert len(cancels) == 1 and cancels[0]["params"]["sessionId"] == "chat-sess-1"


def test_stop_during_a_pending_permission_cancels_it_and_never_grants(root: Path, agent_log: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "permission"})
        feed.until(_is("permission"))
        workspace_chat.chat_cancel(root, {})
        resolved = feed.until(_is("permission_resolved"))
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    assert resolved["outcome"] == "cancelled" and resolved["option_id"] is None
    assert next(m for m in _received(agent_log) if m.get("id") == 900)["result"]["outcome"] == {"outcome": "cancelled"}


def test_stop_without_a_running_turn_is_a_no_op(root: Path) -> None:
    assert workspace_chat.chat_cancel(root, {}) == {"ok": True, "cancelled": False}


# ── Reprise ─────────────────────────────────────────────────────────────────


def test_reopening_resumes_the_conversation_with_session_load(root: Path, agent_log: Path) -> None:
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "bonjour"})
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    deadline = time.monotonic() + 10  # délai de grâce nul : l'agent s'arrête dès que la page est partie
    while workspace_chat.chat_state(root, {})["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not workspace_chat.chat_state(root, {})["running"]

    reopened = Feed(root, "resume=1")
    try:
        ready = reopened.until(_is("ready"))
        history = reopened.until(
            lambda e: e["type"] == "update" and e["update"]["sessionUpdate"] == "user_message_chunk"
        )
    finally:
        reopened.close()
    assert ready["resumed"] is True and ready["session_id"] == "chat-sess-1"
    assert history["update"]["content"]["text"].startswith("Avant la fermeture")
    loads = [m for m in _received(agent_log) if m.get("method") == "session/load"]
    assert [m["params"]["sessionId"] for m in loads] == ["chat-sess-1"]
    assert len([m for m in _received(agent_log) if m.get("method") == "session/new"]) == 1


def test_without_load_session_a_new_conversation_starts(
    root: Path, agent_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NO_LOAD", "1")
    workspace_chat._conversation(root)._store_session("ancienne-session")
    feed = Feed(root, "resume=1")
    try:
        ready = feed.until(_is("ready"))
    finally:
        feed.close()
    assert ready["resumed"] is False
    methods = [m.get("method") for m in _received(agent_log)]
    assert "session/load" not in methods and "session/new" in methods


def test_a_missing_agent_is_reported_in_the_stream_not_as_a_crash(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(workspace_chat.AGENT_COMMAND_ENV, "agent-acp-qui-n-existe-pas")
    feed = Feed(root)
    try:
        workspace_chat.chat_send(root, {"text": "bonjour"})
        error = feed.until(_is("error"))
        feed.until(_is("turn_ended"))
    finally:
        feed.close()
    assert "introuvable" in error["message"]
    assert workspace_chat.chat_state(root, {})["busy"] is False


# ── Tables de routes ────────────────────────────────────────────────────────


def test_the_chat_routes_are_registered() -> None:
    assert {SEND, PERMISSION, CANCEL, f"{PREFIX}chat/stop"} <= set(POST_ROUTES)
    assert f"{PREFIX}chat/state" in GET_ROUTES
    assert f"{PREFIX}chat/stream" == CHAT_STREAM_PATH and CHAT_STREAM_PATH not in GET_ROUTES


# ── Un vrai serveur du cockpit : gardes du POST, flux SSE ───────────────────


@pytest.fixture
def server(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    reg.save_registry([{"name": "Projet", "path": str(root.resolve()), "slug": "projet"}])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "projet")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", root.resolve())
    cmd_cockpit._API_CACHE.clear()
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield {"port": httpd.server_address[1], "root": root}
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


@pytest.mark.no_session_token
@pytest.mark.parametrize("route", ["send", "permission", "cancel", "stop"])
def test_a_chat_post_without_the_session_token_is_refused_and_starts_nothing(
    server: dict[str, Any], route: str
) -> None:
    status, body = _cockpit_http.request(
        server["port"],
        "POST",
        f"{PREFIX}chat/{route}?project=projet",
        data=json.dumps({"text": "bonjour", "request_id": "1", "decision": "allow_once"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 403 and "jeton" in body["error"]
    assert workspace_chat.chat_state(server["root"], {})["running"] is False
    assert workspace_chat.chat_state(server["root"], {})["seq"] == 0


@pytest.mark.no_session_token
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "null"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_a_chat_post_from_another_origin_is_refused_even_with_the_token(
    server: dict[str, Any], headers: dict[str, str], expected: int
) -> None:
    sent = {"Content-Type": "application/json", SESSION_HEADER: session_token(), **headers}
    status, _ = _cockpit_http.request(
        server["port"], "POST", f"{SEND}?project=projet", data=json.dumps({"text": "bonjour"}).encode(), headers=sent
    )
    assert status == expected
    assert workspace_chat.chat_state(server["root"], {})["seq"] == 0


def test_a_chat_post_on_a_project_that_is_only_watched_is_read_only(server: dict[str, Any], tmp_path: Path) -> None:
    other = tmp_path / "voisin"
    (other / ".git").mkdir(parents=True)
    reg.save_registry(
        [
            {"name": "Projet", "path": str(server["root"].resolve()), "slug": "projet"},
            {"name": "Voisin", "path": str(other.resolve()), "slug": "voisin"},
        ]
    )
    status, body = _cockpit_http.post(server["port"], f"{SEND}?project=voisin", {"text": "bonjour"})
    assert status == 403 and body["error"] == "hôte en lecture seule"
    assert workspace_chat.chat_state(other, {})["running"] is False


def test_over_http_the_reply_streams_then_the_turn_ends(server: dict[str, Any]) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=15)
    try:
        conn.request("GET", f"{CHAT_STREAM_PATH}?project=projet")
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type", "").startswith("text/event-stream")
        status, body = _cockpit_http.post(server["port"], f"{SEND}?project=projet", {"text": "bonjour"})
        assert status == 200 and body["accepted"] is True
        seen: list[str] = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and "turn_ended" not in seen:
            line = response.fp.readline().decode()
            if line.startswith("data: "):
                seen.append(json.loads(line[6:])["type"])
    finally:
        conn.close()
    assert "turn_ended" in seen and "update" in seen and seen.index("user") < seen.index("turn_ended")


def test_a_closed_page_unsubscribes_and_the_agent_stops_after_the_grace(server: dict[str, Any]) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=15)
    conn.request("GET", f"{CHAT_STREAM_PATH}?project=projet")
    response = conn.getresponse()
    response.fp.readline()
    assert workspace_chat.chat_state(server["root"], {})["subscribers"] == 1
    response.close()  # la réponse tient une référence au socket : sans elle, la page n'est pas partie
    conn.close()
    deadline = time.monotonic() + 10
    while workspace_chat.chat_state(server["root"], {})["subscribers"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert workspace_chat.chat_state(server["root"], {})["subscribers"] == 0
