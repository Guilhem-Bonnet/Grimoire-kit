"""Tests for observatory.py — Grimoire Observatory: Interactive Visual Dashboard."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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
        agents, rels = obs.parse_agent_graph(path)
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
        for view in ["timeline", "swimlane", "dag", "graph", "tracelog", "metrics"]:
            assert f'id="view-{view}"' in html, f"Missing view: {view}"

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
