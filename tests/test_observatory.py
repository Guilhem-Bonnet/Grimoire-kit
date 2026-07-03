"""Tests for observatory.py — Grimoire Observatory: Interactive Visual Dashboard."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("observatory", TOOLS / "observatory.py")
obs = importlib.util.module_from_spec(_spec)
sys.modules["observatory"] = obs
_spec.loader.exec_module(obs)


# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_TRACE = """\
# Grimoire Trace — test
## Session session-001 — 2026-03-06

[2026-03-06T09:00:01Z] [orchestrator]    [SOG:routed]         intent="auth" | agents=[dev,qa] | mode=parallel
[2026-03-06T09:00:02Z] [HPE]            [HPE:build-dag]      tasks=4 | layers=3 | critical_path=2
[2026-03-06T09:00:05Z] [dev/Amelia]      [ACTIVATED]          context: implement auth
[2026-03-06T09:01:00Z] [dev/Amelia]      [ACTION:implement]   file: src/auth.ts
[2026-03-06T09:02:00Z] [dev/Amelia]      [HUP:preflight]      confidence=GREEN | sources=2
[2026-03-06T09:02:30Z] [dev/Amelia]      [DECISION]           JWT RS256 stateless
[2026-03-06T09:03:00Z] [qa/Quinn]        [ACTIVATED]          context: write tests
[2026-03-06T09:04:00Z] [qa/Quinn]        [ACTION:implement]   tests: auth.spec.ts — 5 tests
[2026-03-06T09:05:00Z] [HPE]            [HPE:complete]       task=implement | duration=2.1s | status=success | trust=91
[2026-03-06T09:05:30Z] [orchestrator]    [HANDOFF:dev→qa]     cross-validation auth module

## Session session-002 — 2026-03-06

[2026-03-06T10:00:00Z] [orchestrator]    [SOG:routed]         intent="docs" | agents=[tech-writer]
[2026-03-06T10:01:00Z] [tech-writer/Paige] [ACTION:document]  README.md updated
"""

SAMPLE_EVENTS = [
    {"id": "evt-001", "ts": "2026-03-06T09:00:05Z", "agent": "dev/Amelia", "type": "task_started", "payload": {"task_id": "implement-auth", "description": "Implement JWT auth"}, "trace_id": "session-001", "seq": 1},
    {"id": "evt-002", "ts": "2026-03-06T09:03:00Z", "agent": "qa/Quinn", "type": "task_started", "payload": {"task_id": "write-tests", "description": "Write auth tests"}, "trace_id": "session-001", "seq": 2},
    {"id": "evt-003", "ts": "2026-03-06T09:05:00Z", "agent": "dev/Amelia", "type": "task_completed", "payload": {"task_id": "implement-auth", "duration": "2.1s", "trust_score": 91}, "trace_id": "session-001", "seq": 3},
    {"id": "evt-004", "ts": "2026-03-06T09:05:30Z", "agent": "qa/Quinn", "type": "task_completed", "payload": {"task_id": "write-tests", "duration": "1.5s"}, "trace_id": "session-001", "seq": 4},
    {"id": "evt-005", "ts": "2026-03-06T09:02:00Z", "agent": "dev/Amelia", "type": "decision", "payload": {"topic": "auth", "choice": "JWT RS256"}, "trace_id": "session-001", "seq": 5},
]

SAMPLE_GRAPH = """\
agent_graph:
  version: "1.0"
  agents:
    dev:
      persona: "Amelia"
      static_capabilities: ["tdd", "implementation"]
      emergent_capabilities:
        - skill: "JWT patterns"
          confidence: 0.92
      metrics:
        tasks_completed: 12
        avg_trust_score: 89
        cross_validations_passed: 8
        hup_red_count: 0
    qa:
      persona: "Quinn"
      static_capabilities: ["testing", "security-review"]
      metrics:
        tasks_completed: 10
        avg_trust_score: 92
        cross_validations_passed: 6
        hup_red_count: 1
  relationships:
    - from: dev
      to: qa
      type: collaboration
      strength: 0.85
      interactions: 15
      avg_outcome_trust: 90
    - from: qa
      to: dev
      type: validation
      strength: 0.72
      interactions: 8
      avg_outcome_trust: 88
"""


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure with Grimoire data."""
    out = tmp_path / "_grimoire-output"
    out.mkdir()
    (out / "Grimoire_TRACE.md").write_text(SAMPLE_TRACE, encoding="utf-8")
    (out / ".event-log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n",
        encoding="utf-8",
    )
    (out / ".agent-graph.yaml").write_text(SAMPLE_GRAPH, encoding="utf-8")
    return tmp_path


@pytest.fixture
def empty_project(tmp_path):
    """Project with no Grimoire data at all."""
    (tmp_path / "_grimoire-output").mkdir()
    return tmp_path


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    """Send an HTTP request and return (status, parsed_json)."""
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)  # noqa: S310 - local test server only
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server only
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return int(response.status), parsed
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        parsed = json.loads(body) if body else {}
        return int(exc.code), parsed
    except URLError:
        return 0, {}


def _http_raw(method: str, url: str, body: bytes, *, content_type: str = "application/json") -> tuple[int, str]:
    """Send a raw-body HTTP request and return (status, response_text)."""
    request = Request(  # noqa: S310 - local test server only
        url,
        data=body,
        headers={"Content-Type": content_type},
        method=method,
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server only
            return int(response.status), response.read().decode("utf-8")
    except HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")
    except URLError:
        return 0, ""


def _wait_for_api_ready(base_url: str, timeout: float = 5.0) -> None:
    """Wait until the local API responds successfully."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = _http_json("GET", f"{base_url}/api/agent-config")
        if status == 200 and payload.get("ok") is True:
            return
        time.sleep(0.05)
    raise AssertionError("Observatory API server did not become ready in time")


def _start_observatory_server(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    commit_required: bool,
    read_only: bool,
) -> tuple[str, object, threading.Thread, dict, dict]:
    """Start cmd_serve in a background thread and capture the bound server instance."""
    holder: dict[str, object] = {}
    ready = threading.Event()
    base_server_class = getattr(obs.http.server, "ThreadingHTTPServer", obs.http.server.HTTPServer)

    class CapturingHTTPServer(base_server_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["server"] = self
            ready.set()

    if hasattr(obs.http.server, "ThreadingHTTPServer"):
        monkeypatch.setattr(obs.http.server, "ThreadingHTTPServer", CapturingHTTPServer)
    else:  # pragma: no cover - fallback for older Python runtimes
        monkeypatch.setattr(obs.http.server, "HTTPServer", CapturingHTTPServer)
    monkeypatch.setattr(obs.os, "chdir", lambda _path: None)

    argv = ["--project-root", str(project_root), "serve", "--host", "127.0.0.1", "--port", "0"]
    if commit_required:
        argv.append("--commit-required")
    if read_only:
        argv.append("--read-only")

    result: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def _run() -> None:
        try:
            result["code"] = obs.main(argv)
        except Exception as exc:  # pragma: no cover - explicit test failure path
            errors["exc"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    assert ready.wait(timeout=5), "HTTP server was not initialized"
    server = holder["server"]
    base_url = f"http://127.0.0.1:{server.server_port}"
    _wait_for_api_ready(base_url)
    return base_url, server, thread, result, errors


@pytest.fixture
def served_observatory(tmp_project, monkeypatch):
    base_url, server, thread, result, errors = _start_observatory_server(
        tmp_project,
        monkeypatch,
        commit_required=False,
        read_only=False,
    )
    try:
        yield base_url, tmp_project
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if "exc" in errors:
            raise errors["exc"]
        assert result.get("code") == 0


@pytest.fixture
def served_observatory_commit_required(tmp_project, monkeypatch):
    base_url, server, thread, result, errors = _start_observatory_server(
        tmp_project,
        monkeypatch,
        commit_required=True,
        read_only=False,
    )
    try:
        yield base_url, tmp_project
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if "exc" in errors:
            raise errors["exc"]
        assert result.get("code") == 0


@pytest.fixture
def served_observatory_read_only(tmp_project, monkeypatch):
    base_url, server, thread, result, errors = _start_observatory_server(
        tmp_project,
        monkeypatch,
        commit_required=False,
        read_only=True,
    )
    try:
        yield base_url, tmp_project
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if "exc" in errors:
            raise errors["exc"]
        assert result.get("code") == 0


def _apply_agent(
    base_url: str,
    *,
    agent_id: str,
    candidate: dict,
    commit_message: str = "",
) -> tuple[int, dict]:
    """Call apply endpoint helper."""
    return _http_json(
        "POST",
        f"{base_url}/api/agent-config/apply",
        {
            "agent_id": agent_id,
            "candidate": candidate,
            "commit_message": commit_message,
        },
    )


class TestAgentConfigApiIntegration:
    def test_get_agent_config_endpoint(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("GET", f"{base_url}/api/agent-config")
        assert status == 200
        assert payload["ok"] is True
        assert payload["commit_required"] is False
        assert payload["config"]["version"] == 0
        assert isinstance(payload["backups"], list)

    def test_diff_endpoint(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json(
            "POST",
            f"{base_url}/api/agent-config/diff",
            {
                "agent_id": "dev",
                "candidate": {
                    "name": "Dev Team",
                    "persona": "Amelia",
                    "description": "Core delivery",
                    "skills": ["tdd", "implementation", "debug"],
                },
            },
        )

        assert status == 200
        assert payload["ok"] is True
        fields = {entry["field"] for entry in payload["diff"]}
        assert fields == {"name", "description", "skills"}
        assert payload["next_version"] == payload["current_version"] + 1

    def test_apply_endpoint_persists_and_creates_backup(self, served_observatory):
        base_url, project_root = served_observatory
        out_dir = project_root / "_grimoire-output"

        first_candidate = {
            "name": "Dev Team",
            "persona": "Amelia",
            "description": "Core delivery",
            "skills": ["tdd", "implementation"],
        }
        status_1, payload_1 = _apply_agent(base_url, agent_id="dev", candidate=first_candidate)
        assert status_1 == 200
        assert payload_1["ok"] is True
        assert payload_1["changed"] is True
        assert payload_1["backup"] is None

        cfg1 = obs.load_agent_config(out_dir)
        assert cfg1["version"] == 1
        assert cfg1["agents"]["dev"]["version"] == 1
        assert cfg1["agents"]["dev"]["name"] == "Dev Team"

        second_candidate = {
            "name": "Dev Platform",
            "persona": "Amelia",
            "description": "Platform team",
            "skills": ["tdd", "refactor"],
        }
        status_2, payload_2 = _apply_agent(base_url, agent_id="dev", candidate=second_candidate)
        assert status_2 == 200
        assert payload_2["ok"] is True
        assert payload_2["changed"] is True
        assert payload_2["backup"] is not None

        cfg2 = obs.load_agent_config(out_dir)
        assert cfg2["version"] == 2
        assert cfg2["agents"]["dev"]["version"] == 2
        assert obs.list_agent_config_backups(out_dir)

    def test_rollback_endpoint(self, served_observatory):
        base_url, project_root = served_observatory
        out_dir = project_root / "_grimoire-output"

        first_candidate = {
            "name": "Dev Team",
            "persona": "Amelia",
            "description": "Core delivery",
            "skills": ["tdd", "implementation"],
        }
        second_candidate = {
            "name": "Dev Platform",
            "persona": "Amelia",
            "description": "Platform team",
            "skills": ["tdd", "refactor"],
        }
        _apply_agent(base_url, agent_id="dev", candidate=first_candidate)
        _apply_agent(base_url, agent_id="dev", candidate=second_candidate)

        status, payload = _http_json("POST", f"{base_url}/api/agent-config/rollback", {})
        assert status == 200
        assert payload["ok"] is True
        assert payload["restored"]

        cfg = obs.load_agent_config(out_dir)
        assert cfg["agents"]["dev"]["name"] == "Dev Team"
        assert cfg["version"] >= 3


class TestAgentConfigApiCommitRequired:
    def test_apply_rejects_missing_commit_message(self, served_observatory_commit_required):
        base_url, _project_root = served_observatory_commit_required
        status, payload = _apply_agent(
            base_url,
            agent_id="qa",
            candidate={"name": "QA Team", "skills": ["testing"]},
        )
        assert status == 400
        assert payload["ok"] is False
        assert "commit_message is required" in payload["error"]

    def test_apply_accepts_commit_message(self, served_observatory_commit_required):
        base_url, project_root = served_observatory_commit_required
        out_dir = project_root / "_grimoire-output"

        status, payload = _apply_agent(
            base_url,
            agent_id="qa",
            candidate={
                "name": "QA Team",
                "persona": "Quinn",
                "description": "Quality gate",
                "skills": ["testing", "security-review"],
            },
            commit_message="Align QA profile",
        )

        assert status == 200
        assert payload["ok"] is True
        assert payload["changed"] is True
        assert payload["commit_required"] is True

        cfg = obs.load_agent_config(out_dir)
        assert cfg["agents"]["qa"]["commit_message"] == "Align QA profile"


class TestAgentConfigApiErrors:
    def test_post_unknown_endpoint_returns_not_found(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("POST", f"{base_url}/api/agent-config/unknown", {})
        assert status == 404
        assert payload["ok"] is False
        assert "Unknown endpoint" in payload["error"]

    def test_get_unknown_endpoint_returns_not_found(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("GET", f"{base_url}/api/unknown")
        assert status == 404
        assert payload["ok"] is False

    def test_diff_requires_agent_id(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("POST", f"{base_url}/api/agent-config/diff", {"candidate": {"name": "x"}})
        assert status == 400
        assert payload["ok"] is False
        assert "agent_id is required" in payload["error"]

    def test_apply_requires_agent_id(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("POST", f"{base_url}/api/agent-config/apply", {"candidate": {"name": "x"}})
        assert status == 400
        assert payload["ok"] is False
        assert "agent_id is required" in payload["error"]

    def test_malformed_json_payload_returns_400(self, served_observatory):
        base_url, _project_root = served_observatory
        status, body = _http_raw(
            "POST",
            f"{base_url}/api/agent-config/diff",
            b"{invalid-json",
        )
        assert status == 400
        parsed = json.loads(body)
        assert parsed["ok"] is False
        assert "Invalid JSON payload" in parsed["error"]

    def test_rollback_without_backup_returns_not_found(self, served_observatory):
        base_url, _project_root = served_observatory
        status, payload = _http_json("POST", f"{base_url}/api/agent-config/rollback", {})
        assert status == 404
        assert payload["ok"] is False
        assert "No agent config backup" in payload["error"]


class TestAgentConfigApiReadOnly:
    def test_get_reports_read_only_mode(self, served_observatory_read_only):
        base_url, _project_root = served_observatory_read_only
        status, payload = _http_json("GET", f"{base_url}/api/agent-config")
        assert status == 200
        assert payload["ok"] is True
        assert payload["read_only"] is True

    def test_apply_is_forbidden_in_read_only_mode(self, served_observatory_read_only):
        base_url, _project_root = served_observatory_read_only
        status, payload = _apply_agent(
            base_url,
            agent_id="dev",
            candidate={"name": "Dev Team", "skills": ["tdd"]},
        )
        assert status == 403
        assert payload["ok"] is False
        assert "read-only" in payload["error"]

    def test_rollback_is_forbidden_in_read_only_mode(self, served_observatory_read_only):
        base_url, _project_root = served_observatory_read_only
        status, payload = _http_json("POST", f"{base_url}/api/agent-config/rollback", {})
        assert status == 403
        assert payload["ok"] is False
        assert "read-only" in payload["error"]


class TestAgentConfigApiConcurrency:
    def test_parallel_apply_requests_increment_versions(self, served_observatory):
        base_url, project_root = served_observatory
        out_dir = project_root / "_grimoire-output"

        worker_count = 6
        barrier = threading.Barrier(worker_count)
        results: list[tuple[int, dict]] = []
        results_lock = threading.Lock()

        def worker(index: int) -> None:
            barrier.wait(timeout=5)
            status, payload = _apply_agent(
                base_url,
                agent_id="dev",
                candidate={
                    "name": f"Dev Parallel {index}",
                    "persona": "Amelia",
                    "description": f"Parallel update {index}",
                    "skills": ["tdd", f"parallel-{index}"],
                },
            )
            with results_lock:
                results.append((status, payload))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert len(results) == worker_count
        assert all(status == 200 for status, _payload in results)
        assert all(payload.get("ok") is True for _status, payload in results)

        cfg = obs.load_agent_config(out_dir)
        assert cfg["version"] == worker_count
        assert cfg["agents"]["dev"]["version"] == worker_count
        assert cfg["agents"]["dev"]["name"].startswith("Dev Parallel")


class TestAgentConfigAuditTrail:
    def test_diff_apply_rollback_are_logged(self, served_observatory):
        base_url, project_root = served_observatory
        out_dir = project_root / "_grimoire-output"

        # Diff
        status_diff, payload_diff = _http_json(
            "POST",
            f"{base_url}/api/agent-config/diff",
            {
                "agent_id": "dev",
                "candidate": {
                    "name": "Dev Team",
                    "persona": "Amelia",
                    "description": "Core delivery",
                    "skills": ["tdd", "implementation"],
                },
            },
        )
        assert status_diff == 200
        assert payload_diff["ok"] is True

        # Apply twice to force backup creation before rollback
        _apply_agent(
            base_url,
            agent_id="dev",
            candidate={
                "name": "Dev Team",
                "persona": "Amelia",
                "description": "Core delivery",
                "skills": ["tdd", "implementation"],
            },
        )
        _apply_agent(
            base_url,
            agent_id="dev",
            candidate={
                "name": "Dev Platform",
                "persona": "Amelia",
                "description": "Platform team",
                "skills": ["tdd", "refactor"],
            },
            commit_message="Switch to platform profile",
        )

        status_roll, payload_roll = _http_json("POST", f"{base_url}/api/agent-config/rollback", {})
        assert status_roll == 200
        assert payload_roll["ok"] is True

        audit_path = out_dir / obs.AGENT_CONFIG_AUDIT_FILE
        assert audit_path.exists()
        rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows

        actions = {row.get("action") for row in rows}
        assert {"diff", "apply", "rollback"}.issubset(actions)

        apply_rows = [row for row in rows if row.get("action") == "apply" and row.get("status") in {"ok", "unchanged"}]
        assert apply_rows
        # At least one successful apply includes version transitions
        assert any(
            isinstance(row.get("before_version"), int) and isinstance(row.get("after_version"), int)
            for row in apply_rows
        )


# ── Parser Tests ─────────────────────────────────────────────────────────────


class TestParseTrace:
    def test_parses_entries(self, tmp_project):
        path = tmp_project / "_grimoire-output" / "Grimoire_TRACE.md"
        entries, sessions = obs.parse_trace(path)
        assert len(entries) == 12
        assert sessions == ["session-001", "session-002"]

    def test_first_entry_fields(self, tmp_project):
        path = tmp_project / "_grimoire-output" / "Grimoire_TRACE.md"
        entries, _ = obs.parse_trace(path)
        e = entries[0]
        assert e.agent == "orchestrator"
        assert e.event_type == "SOG:routed"
        assert "auth" in e.payload
        assert e.session == "session-001"
        assert e.timestamp == "2026-03-06T09:00:01Z"

    def test_session_boundary(self, tmp_project):
        path = tmp_project / "_grimoire-output" / "Grimoire_TRACE.md"
        entries, _ = obs.parse_trace(path)
        # Last 2 entries should be session-002
        assert entries[-1].session == "session-002"
        assert entries[-2].session == "session-002"

    def test_missing_file(self, tmp_path):
        entries, sessions = obs.parse_trace(tmp_path / "nonexistent.md")
        assert entries == []
        assert sessions == []


class TestParseEventLog:
    def test_parses_events(self, tmp_project):
        path = tmp_project / "_grimoire-output" / ".event-log.jsonl"
        events = obs.parse_event_log(path)
        assert len(events) == 5

    def test_event_fields(self, tmp_project):
        path = tmp_project / "_grimoire-output" / ".event-log.jsonl"
        events = obs.parse_event_log(path)
        e = events[0]
        assert e.id == "evt-001"
        assert e.agent == "dev/Amelia"
        assert e.type == "task_started"
        assert e.payload["task_id"] == "implement-auth"
        assert e.trace_id == "session-001"
        assert e.seq == 1

    def test_missing_file(self, tmp_path):
        assert obs.parse_event_log(tmp_path / "nope.jsonl") == []

    def test_malformed_line_skipped(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text('{"id":"ok","ts":"T","agent":"a","type":"t"}\nnot-json\n{"id":"ok2","ts":"T2","agent":"b","type":"t2"}\n')
        events = obs.parse_event_log(f)
        assert len(events) == 2


class TestParseAgentGraph:
    def test_parses_agents(self, tmp_project):
        path = tmp_project / "_grimoire-output" / ".agent-graph.yaml"
        agents, _rels = obs.parse_agent_graph(path)
        assert len(agents) == 2
        ids = {a.id for a in agents}
        assert "dev" in ids
        assert "qa" in ids

    def test_agent_details(self, tmp_project):
        path = tmp_project / "_grimoire-output" / ".agent-graph.yaml"
        agents, _ = obs.parse_agent_graph(path)
        dev = next(a for a in agents if a.id == "dev")
        assert dev.persona == "Amelia"
        assert "tdd" in dev.capabilities
        assert dev.metrics.get("avg_trust_score") == 89

    def test_relationships(self, tmp_project):
        path = tmp_project / "_grimoire-output" / ".agent-graph.yaml"
        _, rels = obs.parse_agent_graph(path)
        assert len(rels) == 2
        collab = next(r for r in rels if r.type == "collaboration")
        assert collab.from_agent == "dev"
        assert collab.to_agent == "qa"
        assert collab.strength == 0.85
        assert collab.interactions == 15

    def test_missing_file(self, tmp_path):
        agents, rels = obs.parse_agent_graph(tmp_path / "nope.yaml")
        assert agents == []
        assert rels == []


class TestYamlParser:
    def test_simple_key_value(self):
        data = obs._parse_yaml_simple('key: "hello"')
        assert data == {"key": "hello"}

    def test_numbers(self):
        data = obs._parse_yaml_simple("count: 42\nratio: 0.85")
        assert data["count"] == 42
        assert data["ratio"] == 0.85

    def test_booleans(self):
        data = obs._parse_yaml_simple("on: true\noff: false")
        assert data["on"] is True
        assert data["off"] is False

    def test_inline_list(self):
        data = obs._parse_yaml_simple('caps: [a, b, c]')
        assert data["caps"] == ["a", "b", "c"]

    def test_comments_stripped(self):
        data = obs._parse_yaml_simple('key: value  # comment')
        assert data["key"] == "value"

    def test_empty_input(self):
        assert obs._parse_yaml_simple("") == {}
        assert obs._parse_yaml_simple("  \n  \n") == {}


# ── Load & Aggregate Tests ───────────────────────────────────────────────────


class TestLoadAll:
    def test_loads_all_sources(self, tmp_project):
        data = obs.load_all(tmp_project)
        assert len(data.traces) == 12
        assert len(data.events) == 5
        assert len(data.agents) == 2
        assert len(data.relationships) == 2
        assert len(data.sessions) == 2

    def test_agent_ids_merged(self, tmp_project):
        data = obs.load_all(tmp_project)
        # Should have agents from traces + events + graph
        assert "dev/Amelia" in data.agent_ids
        assert "qa/Quinn" in data.agent_ids
        assert "orchestrator" in data.agent_ids
        assert "HPE" in data.agent_ids

    def test_event_types_collected(self, tmp_project):
        data = obs.load_all(tmp_project)
        assert "SOG:routed" in data.event_types
        assert "task_started" in data.event_types
        assert "DECISION" in data.event_types

    def test_empty_project(self, empty_project):
        data = obs.load_all(empty_project)
        assert data.traces == []
        assert data.events == []
        assert data.agents == []
        assert data.relationships == []


class TestAgentConfigLifecycle:
    def test_load_agent_config_defaults(self, tmp_project):
        out_dir = tmp_project / "_grimoire-output"
        cfg = obs.load_agent_config(out_dir)
        assert cfg["schema_version"] == obs.AGENT_CONFIG_SCHEMA_VERSION
        assert cfg["version"] == 0
        assert cfg["agents"] == {}

    def test_compute_agent_config_diff(self):
        current = {"name": "dev", "persona": "Amelia", "description": "", "skills": ["tdd"]}
        candidate = {"name": "Dev Team", "persona": "Amelia", "description": "Core dev", "skills": ["tdd", "debug"]}
        diff = obs.compute_agent_config_diff(current, candidate)
        fields = {item["field"] for item in diff}
        assert fields == {"name", "description", "skills"}

    def test_apply_update_versions_and_backup(self, tmp_project):
        out_dir = tmp_project / "_grimoire-output"

        first = obs.apply_agent_config_update(
            out_dir,
            "dev",
            {"name": "Dev Team", "persona": "Amelia", "description": "Core delivery", "skills": ["tdd", "implementation"]},
            updated_by="pytest",
        )
        assert first["changed"] is True
        assert first["backup"] is None

        cfg1 = obs.load_agent_config(out_dir)
        assert cfg1["version"] == 1
        assert cfg1["agents"]["dev"]["version"] == 1
        assert cfg1["agents"]["dev"]["name"] == "Dev Team"

        second = obs.apply_agent_config_update(
            out_dir,
            "dev",
            {"name": "Dev Platform", "persona": "Amelia", "description": "Platform team", "skills": ["tdd", "refactor"]},
            updated_by="pytest",
        )
        assert second["changed"] is True
        assert second["backup"] is not None

        cfg2 = obs.load_agent_config(out_dir)
        assert cfg2["version"] == 2
        assert cfg2["agents"]["dev"]["version"] == 2
        backups = obs.list_agent_config_backups(out_dir)
        assert backups

    def test_apply_requires_commit_message_in_commit_required_mode(self, tmp_project):
        out_dir = tmp_project / "_grimoire-output"
        with pytest.raises(ValueError):
            obs.apply_agent_config_update(
                out_dir,
                "qa",
                {"name": "QA Team", "skills": ["testing"]},
                commit_required=True,
                commit_message="",
            )

    def test_rollback_restores_previous_version(self, tmp_project):
        out_dir = tmp_project / "_grimoire-output"

        obs.apply_agent_config_update(
            out_dir,
            "dev",
            {"name": "Dev Team", "persona": "Amelia", "skills": ["tdd"]},
            updated_by="pytest",
        )
        obs.apply_agent_config_update(
            out_dir,
            "dev",
            {"name": "Dev Platform", "persona": "Amelia", "skills": ["tdd", "debug"]},
            updated_by="pytest",
        )

        before = obs.load_agent_config(out_dir)
        assert before["agents"]["dev"]["name"] == "Dev Platform"

        restored = obs.rollback_agent_config(out_dir, updated_by="pytest")
        assert restored["restored"]

        after = obs.load_agent_config(out_dir)
        assert after["agents"]["dev"]["name"] == "Dev Team"
        assert after["version"] >= 3

    def test_load_all_applies_overrides_to_agent_nodes(self, tmp_project):
        out_dir = tmp_project / "_grimoire-output"
        obs.apply_agent_config_update(
            out_dir,
            "dev",
            {"name": "Dev Team", "persona": "Amelia Prime", "description": "Refactor lead", "skills": ["refactor", "tdd"]},
            updated_by="pytest",
        )

        data = obs.load_all(tmp_project)
        dev = next(a for a in data.agents if a.id == "dev")
        assert dev.name == "Dev Team"
        assert dev.persona == "Amelia Prime"
        assert dev.description == "Refactor lead"
        assert dev.capabilities == ["refactor", "tdd"]


# ── Serialization Tests ──────────────────────────────────────────────────────


class TestDataToJson:
    def test_valid_json_output(self, tmp_project):
        data = obs.load_all(tmp_project)
        j = obs.data_to_json(data)
        parsed = json.loads(j)
        assert "traces" in parsed
        assert "events" in parsed
        assert "agents" in parsed
        assert "relationships" in parsed
        assert "sessions" in parsed
        assert "agent_ids" in parsed
        assert "event_types" in parsed

    def test_trace_serialized(self, tmp_project):
        data = obs.load_all(tmp_project)
        parsed = json.loads(obs.data_to_json(data))
        assert len(parsed["traces"]) == 12
        t0 = parsed["traces"][0]
        assert "timestamp" in t0
        assert "agent" in t0
        assert "event_type" in t0


# ── HTML Generation Tests ────────────────────────────────────────────────────


class TestGenerateHtml:
    def test_generates_valid_html(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "Grimoire Observatory" in html

    def test_data_embedded(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        # JSON data must be embedded
        assert '"traces"' in html
        assert '"events"' in html
        assert "orchestrator" in html
        assert "dev/Amelia" in html

    def test_no_placeholder_left(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "__Grimoire_DATA__" not in html
        assert "__AUTO_REFRESH__" not in html

    def test_auto_refresh_off_by_default(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'http-equiv="refresh"' not in html

    def test_auto_refresh_on(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data, auto_refresh=True)
        # No meta refresh (it loses tab/scroll state) — JS HEAD-check handles refresh
        assert 'http-equiv="refresh"' not in html
        assert "__AUTO_REFRESH__" not in html

    def test_all_views_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for view in ["overview", "timeline", "swimlane", "dag", "graph", "tracelog", "metrics"]:
            assert f'id="view-{view}"' in html, f"Missing view: {view}"

    def test_overview_is_default_tab(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        # Overview view should have the 'active' class
        assert 'id="view-overview"' in html
        # Overview tab should appear before other tabs
        ov_pos = html.index('data-view="overview"')
        tl_pos = html.index('data-view="timeline"')
        assert ov_pos < tl_pos

    def test_global_search_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'id="global-q"' in html
        assert "global-search" in html

    def test_export_button_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'id="btn-export"' in html

    def test_tooltip_element_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'id="obs-tooltip"' in html
        assert "obs-tooltip" in html

    def test_force_directed_graph_code(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        # Force-directed simulation references
        assert "REPULSION" in html
        assert "ATTRACTION" in html
        assert "simulate" in html
        assert "mousedown" in html  # drag support

    def test_overview_render_function(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "renderOverview" in html
        assert "ov-grid" in html
        assert "ov-workload" in html

    def test_tab_keyboard_shortcuts(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for i in range(1, 7):
            assert f">{i}</span>" in html

    def test_empty_project_generates_ok(self, empty_project):
        data = obs.load_all(empty_project)
        html = obs.generate_html(data)
        assert "<!DOCTYPE html>" in html
        assert "Grimoire Observatory" in html

    def test_no_inline_onclick(self, tmp_project):
        """Ensure event delegation via data-item-idx, no fragile inline onclick."""
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "data-item-idx" in html
        assert 'onclick=' not in html.split("<script>")[0]  # no onclick in HTML body

    def test_agent_config_ui_hooks_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "openAgentConfigDrawer" in html
        assert "ac-config-btn" in html
        assert "cfg-btn-apply" in html


# ── Pixel Office V2 Tests ────────────────────────────────────────────────────


class TestPixelOfficeHTML:
    """Test the Pixel Office V2 HTML/CSS/JS integration."""

    def test_office_tab_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'data-view="office"' in html
        assert "Office" in html

    def test_office_view_structure(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'id="view-office"' in html
        assert 'id="office-cv"' in html
        assert 'id="office-wrap"' in html
        assert 'id="office-minimap"' in html
        assert 'id="office-info"' in html

    def test_office_hud_buttons(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for btn_id in ["hud-grid", "hud-names", "hud-trust", "hud-bubbles", "hud-reset"]:
            assert f'id="{btn_id}"' in html, f"Missing HUD button: {btn_id}"

    def test_timeline_bar_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "timeline-bar" in html
        for el_id in ["tbar-play", "tbar-prev", "tbar-next", "tbar-start", "tbar-end",
                       "tbar-scrub", "tbar-progress", "tbar-thumb", "tbar-markers",
                       "tbar-current", "tbar-total", "tbar-speed", "tbar-session", "tbar-live"]:
            assert f'id="{el_id}"' in html, f"Missing timeline bar element: {el_id}"

    def test_timeline_bar_heatmap_canvas(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert 'id="tbar-heat-cv"' in html

    def test_office_css_classes(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for cls in [".office-wrap", ".office-minimap", ".office-info", ".office-hud",
                    ".timeline-bar", ".tbar-scrub", ".tbar-progress", ".tbar-thumb",
                    ".tbar-markers", ".tbar-speed"]:
            assert cls in html, f"Missing CSS class: {cls}"

    def test_game_engine_js_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        # Core game engine classes/functions must be in the JS
        for fn in ["Sprite Factory", "Office Layout", "makeCharSprite", "findPath",
                    "class Agent", "class TimelineEngine", "initOffice",
                    "renderOfficeFrame", "renderMinimap", "tbarTogglePlay"]:
            assert fn in html, f"Missing game engine component: {fn}"

    def test_office_external_asset_loader_present(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for marker in [
            "OFFICE_EXTERNAL_ASSET_FILES",
            "loadExternalOfficeAssets",
            "agentSpriteFromExternalAssets",
            "/_grimoire-output/assets/",
        ]:
            assert marker in html, f"Missing external asset loader marker: {marker}"

    def test_agent_theme_colors(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        assert "AGENT_THEME" in html
        for role in ["dev", "qa", "architect", "pm", "analyst", "sm", "orchestr"]:
            assert f"'{role}'" in html or f'"{role}"' in html

    def test_office_tab_is_first(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        office_pos = html.index('data-view="office"')
        overview_pos = html.index('data-view="overview"')
        assert office_pos < overview_pos, "Office tab should appear before Overview"

    def test_keyboard_shortcuts_updated(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        # Office shortcut key O
        assert ">O</span>" in html
        # Space for play/pause should be in JS
        assert "' '" in html or "Space" in html

    def test_office_zones_defined(self, tmp_project):
        data = obs.load_all(tmp_project)
        html = obs.generate_html(data)
        for zone in ["vision", "architect", "central", "dev", "ops", "commons"]:
            assert zone in html, f"Missing office zone: {zone}"


# ── CLI Tests ────────────────────────────────────────────────────────────────


class TestCLIGenerate:
    def test_generate_creates_file(self, tmp_project):
        ret = obs.main(["--project-root", str(tmp_project), "generate"])
        assert ret == 0
        out = tmp_project / "_grimoire-output" / "observatory.html"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_generate_empty_project(self, empty_project):
        ret = obs.main(["--project-root", str(empty_project), "generate"])
        assert ret == 0
        out = empty_project / "_grimoire-output" / "observatory.html"
        assert out.exists()


class TestCLIExport:
    def test_export_outputs_json(self, tmp_project, capsys):
        ret = obs.main(["--project-root", str(tmp_project), "export"])
        assert ret == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "traces" in parsed
        assert len(parsed["traces"]) == 12


class TestCLINoCommand:
    def test_no_command_shows_help(self, capsys):
        ret = obs.main([])
        assert ret == 0


# ── Directory & Trace Detection Tests ────────────────────────────────────────


class TestFindOutputDir:
    def test_prefers_dir_with_trace(self, tmp_path):
        """When both dirs exist, prefer the one with actual trace data."""
        go = tmp_path / "_grimoire-output"
        go.mkdir()
        runtime = tmp_path / "_grimoire-runtime-output"
        runtime.mkdir()
        (runtime / "GRIMOIRE_TRACE.md").write_text("# trace\n")
        assert obs._find_output_dir(tmp_path) == runtime

    def test_prefers_grimoire_trace_naming(self, tmp_path):
        go = tmp_path / "_grimoire-output"
        go.mkdir()
        (go / "Grimoire_TRACE.md").write_text("# trace\n")
        assert obs._find_output_dir(tmp_path) == go

    def test_fallback_to_existing_dir(self, tmp_path):
        """If no trace data, falls back to first existing dir."""
        go = tmp_path / "_grimoire-output"
        go.mkdir()
        assert obs._find_output_dir(tmp_path) == go

    def test_default_when_neither_exists(self, tmp_path):
        result = obs._find_output_dir(tmp_path)
        assert result == tmp_path / "_grimoire-output"


class TestFindTrace:
    def test_finds_grimoire_trace(self, tmp_path):
        (tmp_path / "Grimoire_TRACE.md").write_text("# trace\n")
        assert obs._find_trace(tmp_path) == tmp_path / "Grimoire_TRACE.md"

    def test_finds_bmad_trace(self, tmp_path):
        (tmp_path / "BMAD_TRACE.md").write_text("# trace\n")
        assert obs._find_trace(tmp_path) == tmp_path / "BMAD_TRACE.md"

    def test_prefers_grimoire_naming(self, tmp_path):
        (tmp_path / "Grimoire_TRACE.md").write_text("# trace\n")
        (tmp_path / "BMAD_TRACE.md").write_text("# trace\n")
        assert obs._find_trace(tmp_path) == tmp_path / "Grimoire_TRACE.md"

    def test_fallback_when_neither_exists(self, tmp_path):
        result = obs._find_trace(tmp_path)
        assert result == tmp_path / "Grimoire_TRACE.md"


class TestLoadAllRuntimeLayout:
    """Test load_all with runtime output directory layout."""

    def test_loads_from_runtime_output(self, tmp_path):
        out = tmp_path / "_grimoire-runtime-output"
        out.mkdir()
        (out / "GRIMOIRE_TRACE.md").write_text(SAMPLE_TRACE, encoding="utf-8")
        (out / ".event-log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n",
            encoding="utf-8",
        )
        data = obs.load_all(tmp_path)
        assert len(data.traces) == 12
        assert len(data.events) == 5
