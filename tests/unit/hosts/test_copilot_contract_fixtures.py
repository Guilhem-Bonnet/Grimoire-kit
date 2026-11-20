"""Contrat Copilot capturé depuis des traces réelles (épique #1280, lot P1-0).

Les fixtures de ``tests/fixtures/hosts/copilot/`` viennent de journaux réels
(harness Copilot : journal de session ; harness Local : sorties affichées),
rédigés. Ces tests verrouillent :

1. chaque charge d'entrée est lue par ``normalize_input`` et donne le bon
   événement et le bon outil ;
2. la détection de dialecte dit ce que la forme permet, rien de plus ;
3. sans version d'hôte établie, aucune capacité Copilot n'est vérifiée, et
   aucune capacité, sur aucun hôte, n'est vérifiée sans fixture ;
4. aucune fixture ne contient de chemin personnel ni d'identifiant réel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import CONTRACT_KEYS, contract_for, is_fenced, recorded_contract
from grimoire.hosts.copilot_wire import (
    PayloadDialect,
    detect_dialect,
    event_from_alias,
    parse_tool_args,
    single_tool_call,
    tool_calls,
)
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import normalize_input, parse_event

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts"
COPILOT = FIXTURES / "copilot"
HARNESS_FIXTURES = sorted((COPILOT / "copilot-harness").glob("*.json"))
LOCAL_FIXTURES = sorted((COPILOT / "local-harness").glob("*.json"))
ALL_FIXTURES = HARNESS_FIXTURES + LOCAL_FIXTURES


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hook_cases() -> list[tuple[str, dict]]:
    cases = []
    for path in HARNESS_FIXTURES:
        for i, case in enumerate(_load(path).get("payloads", [])):
            cases.append((f"{path.stem}#{i}", case))
    return cases


HOOK_CASES = _hook_cases()


def test_fixtures_exist_for_both_harnesses() -> None:
    assert HARNESS_FIXTURES and LOCAL_FIXTURES
    assert {_load(p)["harness"] for p in ALL_FIXTURES} == {"copilot", "local"}
    observed_events = {case["hookType"] for _, case in HOOK_CASES}
    assert observed_events == {"sessionStart", "userPromptSubmitted", "preToolUse", "postToolUse", "preCompact"}


@pytest.mark.parametrize(("label", "case"), HOOK_CASES, ids=[label for label, _ in HOOK_CASES])
def test_each_payload_normalizes_to_its_event_and_tool(label: str, case: dict, tmp_path: Path) -> None:
    event = parse_event(case["hookType"], host_id=HostId.GITHUB_COPILOT)
    expect = case["expect"]
    assert event is HookEvent(expect["event"]), label
    hook = normalize_input(case["input"], event=event, project_root=tmp_path, host_id=HostId.GITHUB_COPILOT)
    assert hook.event is event
    assert hook.session_id == case["input"]["sessionId"]
    assert hook.host == HostId.GITHUB_COPILOT.value
    if "tool_name" in expect:
        assert hook.tool_name == expect["tool_name"], label
        assert sorted(hook.tool_input) == expect["tool_input_keys"], label
    if "tool_response_keys" in expect:
        assert sorted(hook.tool_response) == expect["tool_response_keys"], label
    if "prompt" in expect:
        assert hook.prompt == expect["prompt"]


def test_batched_tool_calls_are_all_listed_and_not_reduced_to_the_first(tmp_path: Path) -> None:
    case = _load(COPILOT / "copilot-harness" / "pretooluse-batch.json")["payloads"][0]
    payload = case["input"]
    assert [name for name, _ in tool_calls(payload)] == case["expect"]["tool_calls"]
    assert single_tool_call(payload) is None
    hook = normalize_input(payload, event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, host_id=HostId.GITHUB_COPILOT)
    # Un lot n'est pas réduit à son premier appel : `run_hook` juge chaque appel (test_copilot_batch_guard.py).
    assert hook.tool_name == ""


class TestDialect:
    @pytest.mark.parametrize(("label", "case"), HOOK_CASES, ids=[label for label, _ in HOOK_CASES])
    def test_observed_copilot_harness_payloads_are_camel(self, label: str, case: dict) -> None:
        assert detect_dialect(case["input"]) is PayloadDialect.COPILOT, label

    def test_snake_case_payload_is_not_attributed_to_a_harness(self) -> None:
        # Forme documentée (référence des hooks Local de VS Code), non capturée.
        documented = {
            "timestamp": "2026-11-18T00:00:00Z",
            "cwd": "<PROJECT>",
            "session_id": "00000000-0000-4000-8000-0000000000e1",
            "hook_event_name": "PreToolUse",
            "tool_name": "<local-tool-name>",
            "tool_input": {},
            "tool_use_id": "tool-123",
        }
        assert detect_dialect(documented) is PayloadDialect.SNAKE

    @pytest.mark.parametrize(
        "payload",
        [{}, {"cwd": "<PROJECT>"}, {"sessionId": "a", "session_id": "a"}],
    )
    def test_ambiguous_or_empty_payload_is_unknown(self, payload: dict) -> None:
        assert detect_dialect(payload) is PayloadDialect.UNKNOWN


class TestWireHelpers:
    def test_copilot_event_aliases(self) -> None:
        assert parse_event("userPromptSubmitted", host_id=HostId.GITHUB_COPILOT) is HookEvent.USER_PROMPT_SUBMIT
        assert parse_event("agentStop", host_id=HostId.GITHUB_COPILOT) is HookEvent.STOP
        assert parse_event("preToolUse") is HookEvent.PRE_TOOL_USE
        assert event_from_alias("nothing") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"a": 1}, {"a": 1}),
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", {}),
            ("not json", {}),
            ("", {}),
            (None, {}),
            (3, {}),
        ],
    )
    def test_parse_tool_args(self, raw: object, expected: dict) -> None:
        assert parse_tool_args(raw) == expected

    def test_tool_name_and_string_args_pair(self, tmp_path: Path) -> None:
        payload = {"sessionId": "s", "toolName": "bash", "toolArgs": '{"command": "ls"}'}
        assert single_tool_call(payload) == ("bash", {"command": "ls"})
        hook = normalize_input(
            payload, event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, host_id=HostId.GITHUB_COPILOT
        )
        assert (hook.tool_name, hook.tool_input) == ("bash", {"command": "ls"})

    def test_nameless_entries_are_ignored(self) -> None:
        assert tool_calls({"toolCalls": [{"args": "{}"}, "x", {"name": "view", "args": "{}"}]}) == (("view", {}),)

    def test_snake_case_payload_is_untouched(self, tmp_path: Path) -> None:
        payload = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        hook = normalize_input(
            payload, event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, host_id=HostId.GITHUB_COPILOT
        )
        assert (hook.tool_name, hook.tool_input) == ("Bash", {"command": "ls"})


class TestNothingVerifiedWithoutProof:
    def test_copilot_has_no_version_and_nothing_verified(self) -> None:
        contract = recorded_contract(HostId.GITHUB_COPILOT)
        assert contract.host_version == ""
        assert not any(contract.verified(key) for key in CONTRACT_KEYS)
        assert is_fenced(HostId.GITHUB_COPILOT, "1.145.3") is False
        assert not any(contract_for(HostId.GITHUB_COPILOT, "0.73.0").verified(k) for k in CONTRACT_KEYS)

    def test_attached_fixtures_name_their_capability_and_harness(self) -> None:
        contract = recorded_contract(HostId.GITHUB_COPILOT)
        attached = {rel for ev in contract.evidence.values() for rel in ev.fixtures}
        assert attached, "les traces relues doivent être rattachées au contrat"
        for rel in attached:
            data = _load(FIXTURES / rel)
            assert data["capability"] in CONTRACT_KEYS
            assert data["harness"] in {"copilot", "local"}
            assert data["version_note"].strip()

    @pytest.mark.parametrize("manifest", sorted(FIXTURES.glob("*/manifest.json")), ids=lambda p: p.parent.name)
    def test_a_verified_capability_always_has_a_readable_fixture(self, manifest: Path) -> None:
        for key, entry in _load(manifest)["capabilities"].items():
            if entry["status"] == "verified":
                assert entry["fixtures"], (manifest.parent.name, key)
                for rel in entry["fixtures"]:
                    if rel.startswith("probe:"):
                        continue
                    assert (FIXTURES / rel).is_file(), (manifest.parent.name, key, rel)


_PERSONAL = re.compile(r"/home/|/Users/|/mnt/|[A-Za-z]:\\\\|guilhem", re.IGNORECASE)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CALL_ID = re.compile(r"\b(?:toolu|call)_[A-Za-z0-9_]+")


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_fixtures_carry_no_personal_data(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _PERSONAL.search(text), path.name
    for uuid in _UUID.findall(text):
        assert uuid.startswith("00000000-0000-4000-8000-"), (path.name, uuid)
    for call_id in _CALL_ID.findall(text):
        assert "_REDACTED_" in call_id, (path.name, call_id)
