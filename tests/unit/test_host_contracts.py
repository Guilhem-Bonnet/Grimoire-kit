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
import os
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
    HostId.OPENCODE: "opencode",
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
            assert is_fenced(host, "1.17.11") is False, host
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


# ── #876, verrou 5 : le contrat est resondé à chaque nouvelle version ─────────

# Le faux binaire est un script à shebang sans extension, le PATH est joint par
# ``:`` et ``HOME`` est détourné (Windows lit ``USERPROFILE``) : harnais POSIX,
# constaté rouge sur windows-latest (run 40042732352, 7 tests). À porter.
_POSIX_HARNESS = pytest.mark.skipif(os.name == "nt", reason="faux binaire à shebang, PATH ':' et HOME : harnais POSIX")

_FAKE_CLAUDE = '''#!{python}
"""Faux binaire claude : --version, et un -p qui obéit aux hooks PreToolUse du projet courant."""
import json, subprocess, sys
VERSION = {version!r}
MODE = {mode!r}
if "--version" in sys.argv:
    print(VERSION + " (Claude Code)")
    sys.exit(0)
if MODE == "hang":
    import time; time.sleep(60)
cfg = json.load(open(".claude/settings.json"))
cmd = cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
rc = subprocess.run(cmd, shell=True, input="{{}}", text=True, capture_output=True).returncode
if MODE == "ignore-deny" or rc != 2:
    open("EXECUTED.marker", "w").close()
'''


def _fake_claude(tmp_path: Path, version: str, mode: str = "honour") -> str:
    import stat
    import sys

    path = tmp_path / "bin" / f"claude-{version}-{mode}"
    path.parent.mkdir(exist_ok=True)
    path.write_text(_FAKE_CLAUDE.format(python=sys.executable, version=version, mode=mode), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_HOST_PROBE", "1")  # le conftest le coupe ; ici seul un faux binaire est sondé
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@_POSIX_HARNESS
class TestProbeOnNewVersion:
    def test_unknown_version_triggers_probe_and_records_exact_version(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import ensure_probed, proof_for

        claude = _fake_claude(tmp_path, "2.1.309")
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False  # avant la sonde

        proof = ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude)

        assert proof is not None and proof.ok and proof.version == "2.1.309" and proof.probed_at
        assert proof_for(HostId.CLAUDE_CODE_CLI, "2.1.309") == proof
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is True
        assert contract_for(HostId.CLAUDE_CODE_CLI, "2.1.309").verified("pretooluse_deny")
        # La preuve vaut pour cette version exacte, jamais pour une plage.
        for other in ("2.1.308", "2.1.310", "2.2.0"):
            assert is_fenced(HostId.CLAUDE_CODE_CLI, other) is False

    def test_probe_runs_once_per_version(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import ensure_probed

        claude = _fake_claude(tmp_path, "2.1.309")
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude) is not None
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude) is None

    def test_captured_version_needs_no_probe(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import ensure_probed

        claude = _fake_claude(tmp_path, "2.1.301")
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.301", binary=claude) is None

    def test_binary_of_another_version_proves_nothing(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import probe_host, proof_for

        claude = _fake_claude(tmp_path, "2.1.310")
        proof = probe_host(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude)
        assert proof.ok is False
        assert proof_for(HostId.CLAUDE_CODE_CLI, "2.1.309") is None  # rien consigné pour 2.1.309
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False

    @pytest.mark.parametrize("value", [None, "", "0", "true", "yes"])
    def test_no_probe_without_opt_in_launches_no_claude_subprocess(
        self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        """Les identifiants de l'utilisateur ne servent jamais sans ``GRIMOIRE_HOST_PROBE=1``."""
        import subprocess

        from grimoire.hosts.contract_probe import ensure_probed, proof_for

        if value is None:
            monkeypatch.delenv("GRIMOIRE_HOST_PROBE", raising=False)
        else:
            monkeypatch.setenv("GRIMOIRE_HOST_PROBE", value)
        launched: list[object] = []
        real = subprocess.run
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a) or real(*a, **k))
        claude = _fake_claude(tmp_path, "2.1.309")
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude) is None
        assert launched == [], "aucun sous-processus sans opt-in"
        assert proof_for(HostId.CLAUDE_CODE_CLI, "2.1.309") is None
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False  # comportement sûr : reprise en waiting(human)

    def test_unproven_question_says_how_to_opt_in(self) -> None:
        from grimoire.missions.ledger_rules import unfenced_question

        class _View:
            session_id = "s1"
            actor_id = "a"
            host = "claude-code"
            host_version = "2.1.309"

        assert "GRIMOIRE_HOST_PROBE=1" in unfenced_question(_View())  # type: ignore[arg-type]

    def test_installed_version_is_read_once_per_binary(
        self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from grimoire.hosts.contract_probe import installed_version

        launched: list[object] = []
        real = subprocess.run
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a) or real(*a, **k))
        claude = _fake_claude(tmp_path, "2.1.309")
        assert [installed_version(HostId.CLAUDE_CODE_CLI, claude) for _ in range(3)] == ["2.1.309"] * 3
        assert len(launched) == 1


@_POSIX_HARNESS
class TestProbeFailureIsNoBrake:
    @pytest.mark.parametrize("mode", ["ignore-deny"])
    def test_tool_executed_despite_deny_leaves_pretooluse_deny_false(self, tmp_path: Path, home: Path, mode: str) -> None:
        from grimoire.hosts.contract_probe import ensure_probed

        proof = ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=_fake_claude(tmp_path, "2.1.309", mode))
        assert proof is not None and proof.ok is False and proof.reason
        contract = contract_for(HostId.CLAUDE_CODE_CLI, "2.1.309")
        assert contract.verified("pretooluse_deny") is False
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False

    def test_timeout_is_a_recorded_failure_not_an_error(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import probe_host

        proof = probe_host(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=_fake_claude(tmp_path, "2.1.309", "hang"), timeout=1)
        assert proof.ok is False
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False

    def test_missing_binary_is_a_failure_without_range(self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.hosts.contract_probe import probe_host

        monkeypatch.setenv("PATH", str(tmp_path / "vide"))
        proof = probe_host(HostId.CLAUDE_CODE_CLI, "2.1.309")
        assert proof.ok is False
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.310") is False

    def test_failed_probe_is_not_replayed_before_the_delay(self, tmp_path: Path, home: Path) -> None:
        from grimoire.hosts.contract_probe import ensure_probed

        claude = _fake_claude(tmp_path, "2.1.309", "ignore-deny")
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude) is not None
        assert ensure_probed(HostId.CLAUDE_CODE_CLI, "2.1.309", binary=claude) is None

    def test_corrupt_proof_file_means_no_brake(self, home: Path) -> None:
        from grimoire.hosts.contract_probe import proofs_path

        proofs_path().parent.mkdir(parents=True)
        proofs_path().write_text("{pas du json", encoding="utf-8")
        assert is_fenced(HostId.CLAUDE_CODE_CLI, "2.1.309") is False


@_POSIX_HARNESS
class TestHostStatusShowsProof:
    def _status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> dict:
        from typer.testing import CliRunner

        from grimoire.cli.app import app

        project = tmp_path / "proj"
        project.mkdir()
        (project / "project-context.yaml").write_text(
            'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n', encoding="utf-8"
        )
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
        result = CliRunner().invoke(
            app, ["-o", "json", "host", "status", "--host", "claude", "--project-root", str(project), *extra]
        )
        return json.loads(result.stdout)[0]

    def test_status_shows_probed_version_and_date(self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        claude = _fake_claude(tmp_path, "2.1.309")
        Path(claude).rename(tmp_path / "bin" / "claude")
        item = self._status(tmp_path, monkeypatch, "--probe")
        assert item["host_version"] == "2.1.309"
        assert item["brake_proof"]["status"] == "verified"
        assert item["brake_proof"]["source"] == "probe"
        assert item["brake_proof"]["version"] == "2.1.309"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*", item["brake_proof"]["probed_at"])

    def test_status_without_probe_reports_unverified(self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        Path(_fake_claude(tmp_path, "2.1.309")).rename(tmp_path / "bin" / "claude")
        item = self._status(tmp_path, monkeypatch)
        assert item["brake_proof"]["status"] == "unverified"
        assert item["brake_proof"]["probed_at"] == ""


class TestEffectiveHostConfigDisablesProofs:
    """#886 (e) : la sonde teste un dossier temporaire ; une preuve ne tient pas si la configuration réelle coupe les hooks."""

    CAPTURED = "2.1.301"

    @pytest.fixture
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
        """HOME, projet et dossier managé injectés : jamais le vrai ``~/.claude`` ni le vrai ``/etc``."""
        from grimoire.hosts import contract_probe

        home, project, managed = tmp_path / "home", tmp_path / "projet", tmp_path / "managed"
        for d in (home / ".claude", project / ".claude", managed):
            d.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(contract_probe, "MANAGED_DIR", managed)
        return {"home": home, "project": project, "managed": managed}

    @staticmethod
    def _put(path: Path, data: object) -> None:
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def _fenced(self, env: dict[str, Path], version: str | None = None) -> bool:
        return is_fenced(HostId.CLAUDE_CODE_CLI, version or self.CAPTURED, project_root=env["project"])

    def test_without_any_settings_the_captured_proof_holds(self, env: dict[str, Path]) -> None:
        assert self._fenced(env) is True

    @pytest.mark.parametrize("where", ["home", "project", "local", "managed"])
    def test_disable_all_hooks_in_any_level_drops_the_proof(self, env: dict[str, Path], where: str) -> None:
        path = {
            "home": env["home"] / ".claude" / "settings.json",
            "project": env["project"] / ".claude" / "settings.json",
            "local": env["project"] / ".claude" / "settings.local.json",
            "managed": env["managed"] / "managed-settings.json",
        }[where]
        self._put(path, {"disableAllHooks": True})
        assert self._fenced(env) is False
        contract = contract_for(HostId.CLAUDE_CODE_CLI, self.CAPTURED, project_root=env["project"])
        assert not any(contract.verified(key) for key in CONTRACT_KEYS)

    def test_the_highest_level_wins_for_disable_all_hooks(self, env: dict[str, Path]) -> None:
        self._put(env["home"] / ".claude" / "settings.json", {"disableAllHooks": True})
        self._put(env["project"] / ".claude" / "settings.local.json", {"disableAllHooks": False})
        assert self._fenced(env) is True, "le local (false) l'emporte sur l'utilisateur (true)"
        self._put(env["managed"] / "managed-settings.json", {"disableAllHooks": True})
        assert self._fenced(env) is False, "le managé l'emporte sur tout"

    def test_a_quoted_boolean_is_ignored_like_claude_code_does(self, env: dict[str, Path]) -> None:
        self._put(env["home"] / ".claude" / "settings.json", {"disableAllHooks": "true"})
        assert self._fenced(env) is True

    def test_managed_hooks_only_without_grimoire_hooks_drops_the_proof(self, env: dict[str, Path]) -> None:
        self._put(env["managed"] / "managed-settings.json", {"allowManagedHooksOnly": True})
        assert self._fenced(env) is False

    def test_managed_hooks_only_keeps_the_proof_when_managed_carries_the_grimoire_hooks(
        self, env: dict[str, Path]
    ) -> None:
        hooks = {"PreToolUse": [{"hooks": [{"type": "command", "command": "grimoire-hook pretooluse"}]}]}
        self._put(env["managed"] / "managed-settings.json", {"allowManagedHooksOnly": True, "hooks": hooks})
        assert self._fenced(env) is True

    def test_a_managed_drop_in_counts(self, env: dict[str, Path]) -> None:
        (env["managed"] / "managed-settings.d").mkdir()
        self._put(env["managed"] / "managed-settings.d" / "50-hooks.json", {"disableAllHooks": True})
        assert self._fenced(env) is False

    def test_an_unreadable_settings_file_fails_closed(self, env: dict[str, Path]) -> None:
        self._put(env["project"] / ".claude" / "settings.json", "{pas du json")
        assert self._fenced(env) is False

    def test_a_probed_proof_is_not_retained_either(
        self, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from grimoire.hosts.contract_probe import ProbeProof, _record

        monkeypatch.setattr("grimoire.hosts.contract_probe.proofs_path", lambda: tmp_path / "proofs.json")
        _record(ProbeProof(HostId.CLAUDE_CODE_CLI.value, "2.1.400", True, "2026-10-23T10:00:00+00:00"))
        assert self._fenced(env, "2.1.400") is True
        self._put(env["home"] / ".claude" / "settings.json", {"disableAllHooks": True})
        assert self._fenced(env, "2.1.400") is False

    def test_claude_config_dir_moves_the_user_settings(self, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        elsewhere = env["home"].parent / "ailleurs"
        elsewhere.mkdir()
        self._put(elsewhere / "settings.json", {"disableAllHooks": True})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
        assert self._fenced(env) is False

    def test_the_check_never_raises_and_fails_closed(self, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        """Un dossier courant supprimé ou un dossier managé non traversable ne font pas planter un tick."""
        from grimoire.hosts import contract_probe

        def denied(_path: Path) -> object:
            raise PermissionError("refusé")

        real = contract_probe._read_settings
        monkeypatch.setattr(contract_probe, "_read_settings", denied)
        assert self._fenced(env) is False
        assert contract_probe.hooks_disabled_reason(env["project"])

        def gone() -> Path:
            raise FileNotFoundError("cwd supprimé")

        monkeypatch.setattr(contract_probe, "_read_settings", real)
        monkeypatch.setattr(Path, "cwd", staticmethod(gone))
        assert contract_probe.hooks_disabled_reason(None)
        assert is_fenced(HostId.CLAUDE_CODE_CLI, self.CAPTURED) is False

    @pytest.mark.parametrize(
        ("command", "carried"),
        [
            ("echo grimoire", False),
            ("/usr/bin/notgrimoire-hook --event PreToolUse", False),
            ("python -c 'print(1)'  # grimoire-hook", False),
            ("grimoire-hook --host claude-code --event PreToolUse", True),
            ('"$CLAUDE_PROJECT_DIR"/.venv/bin/grimoire-hook --host claude-code --event PreToolUse', True),
            ('"/home/a b/venv/bin/grimoire-hook" --host claude-code --event PreToolUse', True),
            ("C:/venv/Scripts/grimoire-hook.exe --host claude-code --event PreToolUse", True),
        ],
    )
    def test_managed_hooks_only_needs_a_managed_grimoire_hook_command(
        self, env: dict[str, Path], command: str, carried: bool
    ) -> None:
        hooks = {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}
        self._put(env["managed"] / "managed-settings.json", {"allowManagedHooksOnly": True, "hooks": hooks})
        assert self._fenced(env) is carried


class TestHostStatusFollowsTheEffectiveConfig:
    def test_status_does_not_claim_a_proof_when_hooks_are_disabled(
        self, tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from typer.testing import CliRunner

        from grimoire.cli.app import app

        (tmp_path / "bin").mkdir()
        Path(_fake_claude(tmp_path, "2.1.301")).rename(tmp_path / "bin" / "claude")
        project = tmp_path / "proj"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "settings.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
        (project / "project-context.yaml").write_text(
            'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n', encoding="utf-8"
        )
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
        result = CliRunner().invoke(
            app, ["-o", "json", "host", "status", "--host", "claude", "--project-root", str(project)]
        )
        proof = json.loads(result.stdout)[0]["brake_proof"]
        assert proof["status"] == "unverified"
        assert "disableAllHooks" in proof["reason"]
