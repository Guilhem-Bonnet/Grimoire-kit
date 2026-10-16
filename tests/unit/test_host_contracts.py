"""Contrats réels des hôtes (K0, issue #765) : une capacité vaut `false` sans fixture.

Le plan « Kanban agentique » décide du fencing, de la récolte d'un bail
perdu et de la mesure du modèle d'après cinq capacités d'hôte
(`pretooluse_deny`, `session_end`, `quota_signal`, `liveness_probe`,
`subagent_model`). Avant ce lot, `HostProfile.blocking_hooks` affirmait
« un hook peut refuser » pour Copilot sans qu'aucune charge utile réelle ne
l'atteste, et rien ne disait ce que le kit sait ou ne sait pas d'un hôte.

`tests/fixtures/hosts/<hôte>/` verse les captures (Claude Code 2.1.301) et un
`manifest.json` par hôte ; `grimoire.hosts.capabilities.contract_for` porte la
même table dans le code. Ces tests verrouillent trois choses :

1. la table du code est exactement celle des manifestes (aucune dérive) ;
2. une capacité `verified` a une fixture lisible, et la fixture dit bien ce que
   la capacité affirme (le contenu est relu, pas seulement l'existence) ;
3. un hôte sans capture, et un hôte inconnu, ne sont jamais « fencés ».
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import (
    CONTRACT_KEYS,
    ContractEvidence,
    ContractStatus,
    contract_for,
    is_fenced,
    recorded_contract,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts"

HOST_DIRS: dict[HostId, str] = {
    HostId.CLAUDE_CODE_CLI: "claude-code",
    HostId.GITHUB_COPILOT: "copilot",
    HostId.CODEX: "codex",
    HostId.CURSOR: "cursor",
    HostId.GEMINI_CLI: "gemini",
}


def _manifest(host: HostId) -> dict:
    return json.loads((FIXTURES / HOST_DIRS[host] / "manifest.json").read_text(encoding="utf-8"))


def _fixture(rel: str) -> dict:
    return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))


class TestTableMatchesManifests:
    @pytest.mark.parametrize("host", list(HOST_DIRS))
    def test_code_table_is_the_manifest(self, host: HostId) -> None:
        manifest = _manifest(host)
        contract = recorded_contract(host)
        assert contract.host_version == manifest["host_version"]
        assert set(manifest["capabilities"]) == set(CONTRACT_KEYS)
        for key in CONTRACT_KEYS:
            wanted = manifest["capabilities"][key]
            got = contract.evidence[key]
            assert got.status.value == wanted["status"], key
            assert list(got.fixtures) == wanted["fixtures"], key
            assert got.channel == wanted["channel"], key

    def test_nine_points_of_k0_are_covered_by_claude_code_fixtures(self) -> None:
        points = set()
        for path in (FIXTURES / "claude-code").glob("0*.json"):
            points.add(json.loads(path.read_text(encoding="utf-8"))["point"])
        assert points == set(range(1, 10))

    @pytest.mark.parametrize("host", list(HOST_DIRS))
    def test_every_listed_fixture_exists_and_names_its_host_version(self, host: HostId) -> None:
        contract = recorded_contract(host)
        for key, ev in contract.evidence.items():
            for rel in ev.fixtures:
                data = _fixture(rel)
                assert data["host_version"] == contract.host_version, (key, rel)
                assert data["capability"] in CONTRACT_KEYS


class TestNoFixtureMeansFalse:
    def test_verified_without_fixture_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fixture"):
            ContractEvidence(status=ContractStatus.VERIFIED)

    def test_unverified_capability_is_false(self) -> None:
        contract = recorded_contract(HostId.GITHUB_COPILOT)
        assert all(not contract.verified(key) for key in CONTRACT_KEYS)
        assert contract.verified("a_capability_nobody_captured") is False

    def test_unverified_entries_say_what_is_missing(self) -> None:
        for host in HOST_DIRS:
            for key, entry in _manifest(host)["capabilities"].items():
                if entry["status"] != "verified":
                    assert entry["note"].strip(), (host, key)

    def test_hosts_without_a_capture_are_not_fenced(self) -> None:
        for host in (HostId.GITHUB_COPILOT, HostId.CODEX, HostId.CURSOR, HostId.GEMINI_CLI, HostId.UNKNOWN):
            assert is_fenced(host, "2.1.301") is False, host
            assert is_fenced(host, None) is False, host

    def test_unknown_host_has_no_verified_capability(self) -> None:
        contract = recorded_contract(HostId.UNKNOWN)
        assert contract.host_version == ""
        assert not any(contract.verified(key) for key in CONTRACT_KEYS)


class TestProofIsPerHostVersion:
    """Une preuve faite sur 2.1.301 ne vaut pas pour une autre version (K0-S1-01)."""

    CAPTURED = "2.1.301"

    @pytest.mark.parametrize("version", ["2.1.300", "2.1.302", "2.2.0", "3.0.0", "2.1.30", "2.1.3010", "unknown"])
    def test_another_version_is_not_fenced(self, version: str) -> None:
        assert is_fenced(HostId.CLAUDE_CODE_CLI, version) is False

    @pytest.mark.parametrize("version", [None, "", "  "])
    def test_missing_version_is_not_fenced(self, version: str | None) -> None:
        assert is_fenced(HostId.CLAUDE_CODE_CLI, version) is False

    def test_captured_version_is_fenced(self) -> None:
        assert is_fenced(HostId.CLAUDE_CODE_CLI, self.CAPTURED) is True
        assert is_fenced(HostId.CLAUDE_CODE_CLI, f" {self.CAPTURED} ") is True

    def test_another_version_has_nothing_verified(self) -> None:
        contract = contract_for(HostId.CLAUDE_CODE_CLI, "2.1.300")
        assert not any(contract.verified(key) for key in CONTRACT_KEYS)
        assert contract.host_version == ""

    def test_captured_version_keeps_its_evidence(self) -> None:
        contract = contract_for(HostId.CLAUDE_CODE_CLI, self.CAPTURED)
        assert contract.verified("pretooluse_deny") and contract.host_version == self.CAPTURED

    def test_hosts_without_capture_are_not_fenced_whatever_the_version(self) -> None:
        for host in (HostId.GITHUB_COPILOT, HostId.CODEX, HostId.CURSOR, HostId.GEMINI_CLI, HostId.UNKNOWN):
            assert is_fenced(host, "") is False, host

    def test_version_is_a_required_argument(self) -> None:
        with pytest.raises(TypeError):
            is_fenced(HostId.CLAUDE_CODE_CLI)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            contract_for(HostId.CLAUDE_CODE_CLI)  # type: ignore[call-arg]


class TestVerifiedMeansProven:
    """Le contenu des fixtures porte la preuve ; le statut ne se déclare pas."""

    def test_claude_code_is_fenced_only_because_the_tool_really_stopped(self) -> None:
        contract = recorded_contract(HostId.CLAUDE_CODE_CLI)
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.301") is True
        control = _fixture("claude-code/01-pretooluse-control-allow.json")["observed"]
        assert control["tool_executed"] is True and control["marker_file_created"] is True
        denials = [f for f in contract.evidence["pretooluse_deny"].fixtures if "deny" in f]
        assert len(denials) == 2
        for rel in denials:
            observed = _fixture(rel)["observed"]
            assert observed["tool_executed"] is False and observed["marker_file_created"] is False, rel
            seen = observed["tool_result_seen_by_agent"]
            assert seen["is_error"] is True and "K0-DENY-MARKER" in seen["content"], rel

    def test_both_refusal_contracts_are_recorded(self) -> None:
        exit2 = _fixture("claude-code/01-pretooluse-deny-exit2.json")["observed"]
        assert exit2["hook_exit_code"] == 2 and "K0-DENY-MARKER" in exit2["hook_stderr"]
        js = _fixture("claude-code/01-pretooluse-deny-json.json")["observed"]
        out = js["hook_stdout"]["hookSpecificOutput"]
        assert js["hook_exit_code"] == 0 and out["permissionDecision"] == "deny"

    def test_session_end_fixture_is_a_real_payload(self) -> None:
        payload = _fixture("claude-code/02-session-end.json")["payloads"]["SessionEnd"]
        assert payload["hook_event_name"] == "SessionEnd" and payload["session_id"]

    def test_quota_is_unverified_because_no_exhausted_plan_was_seen(self) -> None:
        fx = _fixture("claude-code/03-quota-signal.json")
        assert fx["observed"]["exhausted_plan_observed"] is False
        assert not recorded_contract(HostId.CLAUDE_CODE_CLI).verified("quota_signal")
        assert fx["stream_event_allowed_state"]["rate_limit_info"]["status"] == "allowed"
        assert isinstance(fx["stream_event_allowed_state"]["rate_limit_info"]["resetsAt"], int)

    def test_subagent_model_is_read_from_the_transcript_not_the_payload(self) -> None:
        contract = recorded_contract(HostId.CLAUDE_CODE_CLI)
        fx = _fixture("claude-code/05-subagent-stop.json")
        assert fx["observed"]["model_field_in_SubagentStop_payload"] is False
        assert "model" not in fx["payloads"]["SubagentStop"]
        assert fx["payloads"]["SubagentStop"]["agent_transcript_path"]
        assert fx["observed"]["sub_agent_transcript_assistant_line"]["message_model"].startswith("claude-")
        assert "agent_transcript_path" in contract.evidence["subagent_model"].channel

    def test_agents_json_has_no_state_or_waiting_for_on_interactive_entries(self) -> None:
        fx = _fixture("claude-code/04-agents-json.json")
        assert fx["observed"]["state_field_present"] is False
        assert fx["observed"]["waitingFor_field_present"] is False
        assert {"pid", "sessionId", "status"} <= set(fx["observed"]["fields_seen"])
        assert recorded_contract(HostId.CLAUDE_CODE_CLI).verified("liveness_probe")

    def test_background_session_points_not_reached_stay_unverified(self) -> None:
        contract = recorded_contract(HostId.CLAUDE_CODE_CLI)
        fx = _fixture("claude-code/07-bg-untrusted-workspace.json")["observed"]
        assert fx["session_started"] is False and fx["process_exit_code"] == 1
        assert contract.verified("bg_workspace_trust")
        assert not contract.verified("bg_cost_source")
        assert not contract.verified("bg_wake")
        assert not contract.verified("resume_after_quota")


class TestFixturesAreSafeToCommit:
    FILES = sorted(p for p in FIXTURES.rglob("*.json"))

    def test_there_are_fixtures_to_scan(self) -> None:
        assert len(self.FILES) >= 16

    @pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_no_personal_path_identity_or_secret(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for needle in ("/home/", "/mnt/", "/Users/", "guilhem", "Travail", "@"):
            assert needle not in text, (needle, path.name)
        assert not re.search(r"(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{10,}|Bearer\s+\S+)", text)
        for uuid in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text):
            assert uuid.startswith("00000000-0000-4000-8000-"), (uuid, path.name)
