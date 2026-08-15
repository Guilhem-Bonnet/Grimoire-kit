"""Tests de la télémétrie et de l'export OpenTelemetry GenAI (P3.3)."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.tools.blueprint_telemetry import event_files, otel_spans, read_events


def write_events(root: Path, source: str, events: list[dict[str, object]]) -> None:
    rel = {
        "hook-runtime": Path("_grimoire-runtime-output") / "hook-runtime" / "events.jsonl",
        "task-flow": Path("_grimoire-runtime-output") / "task-flow" / "events.jsonl",
    }[source]
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_aucun_journal_ne_produit_rien(tmp_path: Path) -> None:
    assert event_files(tmp_path) == []
    assert read_events(tmp_path) == {}
    assert otel_spans(tmp_path) == []


def test_une_ligne_illisible_ne_fait_pas_disparaitre_les_autres(tmp_path: Path) -> None:
    """Un journal tronqué en cours d'écriture arrive ; il ne doit rien effacer."""
    path = tmp_path / "_grimoire-runtime-output" / "task-flow" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"llm-call","model":"x"}\n{tronqué\n', encoding="utf-8")
    entries = read_events(tmp_path)["task-flow"]
    assert len(entries) == 2
    assert entries[0]["model"] == "x"
    assert entries[1]["raw"] == "{tronqué"


def test_un_appel_de_modele_devient_un_span_otel(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [{
        "type": "llm-call", "model": "sonnet", "provider": "anthropic",
        "promptTokens": 1200, "completionTokens": 300,
    }])
    span = otel_spans(tmp_path)[0]
    attrs = span["attributes"]
    assert attrs["gen_ai.request.model"] == "sonnet"
    assert attrs["gen_ai.system"] == "anthropic"
    assert attrs["gen_ai.usage.input_tokens"] == 1200
    assert attrs["gen_ai.usage.output_tokens"] == 300
    assert attrs["gen_ai.operation.name"] == "chat"
    assert span["name"] == "chat sonnet"


def test_un_appel_d_outil_est_une_autre_operation(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [{"type": "tool-call", "tool": "grep"}])
    span = otel_spans(tmp_path)[0]
    assert span["attributes"]["gen_ai.operation.name"] == "execute_tool"
    assert span["attributes"]["gen_ai.tool.name"] == "grep"


def test_le_bruit_de_cycle_de_vie_ne_devient_pas_un_span(tmp_path: Path) -> None:
    """Tout convertir noierait les appels réels sous des événements de hook."""
    write_events(tmp_path, "hook-runtime", [
        {"type": "session-start"}, {"type": "hook-fired"}, {"type": "llm-call", "model": "m"},
    ])
    spans = otel_spans(tmp_path)
    assert len(spans) == 1
    assert spans[0]["attributes"]["gen_ai.request.model"] == "m"


def test_une_erreur_devient_un_statut_de_span(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [
        {"type": "llm-call", "model": "m", "error": "timeout"}
    ])
    assert otel_spans(tmp_path)[0]["status"] == {"code": "ERROR", "message": "timeout"}


def test_la_source_reste_tracable(tmp_path: Path) -> None:
    write_events(tmp_path, "hook-runtime", [{"type": "llm-call", "model": "m"}])
    assert otel_spans(tmp_path)[0]["attributes"]["grimoire.source"] == "hook-runtime"


def test_horodatages_et_correlation_transmis_quand_presents(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [{
        "type": "llm-call", "model": "m",
        "startedAt": "2026-08-15T10:00:00Z", "endedAt": "2026-08-15T10:00:02Z",
        "traceId": "abc", "spanId": "def",
    }])
    span = otel_spans(tmp_path)[0]
    assert span["startTime"] == "2026-08-15T10:00:00Z"
    assert span["traceId"] == "abc"


def test_champs_absents_ne_sont_pas_inventes(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [{"type": "llm-call", "model": "m"}])
    span = otel_spans(tmp_path)[0]
    assert "startTime" not in span
    assert "status" not in span
    assert "gen_ai.usage.input_tokens" not in span["attributes"]


def test_la_limite_porte_sur_les_dernieres_lignes(tmp_path: Path) -> None:
    write_events(tmp_path, "task-flow", [
        {"type": "llm-call", "model": f"m{i}"} for i in range(10)
    ])
    spans = otel_spans(tmp_path, limit=3)
    assert [s["attributes"]["gen_ai.request.model"] for s in spans] == ["m7", "m8", "m9"]


def test_les_deux_sources_sont_lues(tmp_path: Path) -> None:
    write_events(tmp_path, "hook-runtime", [{"type": "llm-call", "model": "a"}])
    write_events(tmp_path, "task-flow", [{"type": "llm-call", "model": "b"}])
    models = {s["attributes"]["gen_ai.request.model"] for s in otel_spans(tmp_path)}
    assert models == {"a", "b"}
