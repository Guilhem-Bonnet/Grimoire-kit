"""Tests du producteur de handoff-packet (porté d'un hook d'atelier)."""

from __future__ import annotations

from grimoire.tools import handoff as h


def _capsule(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event": "SubagentStop",
        "agent": "tea",
        "task": "contre-analyse research-only",
        "taskType": "hooks-guardrails",
        "outputPreview": "3 risques identifiés.",
        "explicitFailure": False,
        "timestamp": "2026-07-21T09:00:00Z",
    }
    base.update(over)
    return base


class TestHandoff:
    def test_conforms_to_catalogue_contract(self) -> None:
        packet = h.build_handoff(_capsule())
        required = {
            "task_id",
            "summary",
            "evidence",
            "assumptions",
            "risks",
            "next_trigger",
        }
        assert required <= set(packet)
        assert packet["contract"] == "handoff-packet"
        assert packet["pattern"] == "ORC-03"

    def test_deterministic_derivation(self) -> None:
        packet = h.build_handoff(_capsule())
        assert packet["from"] == {"agent": "tea", "role": "subagent"}
        assert packet["task_id"] == "contre-analyse research-only"
        assert packet["summary"] == "3 risques identifiés."
        assert packet["status"] == "ok"
        assert packet["derivation"] == "deterministic-from-subagent-capsule"

    def test_failure_routes_next_trigger_and_risks(self) -> None:
        packet = h.build_handoff(_capsule(explicitFailure=True))
        assert packet["status"] == "failed"
        assert "corriger" in packet["next_trigger"] or "escalader" in packet["next_trigger"]
        assert packet["risks"] == ["échec explicite signalé"]

    def test_no_preview_falls_back(self) -> None:
        packet = h.build_handoff(_capsule(outputPreview=""))
        assert "Tâche traitée" in packet["summary"]
        assert packet["evidence"] == "aucune preuve capturée"

    def test_is_subagent_stop_guard(self) -> None:
        assert h.is_subagent_stop(_capsule()) is True
        assert h.is_subagent_stop({"event": "SubagentStart"}) is False
        assert h.is_subagent_stop({}) is False
