"""Tests for grimoire.hosts.decisions.calibration (Refs #644)."""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.hosts.decisions.calibration import (
    action_fingerprint,
    record_hold_followup,
    record_policy_hold,
    target_key,
)
from grimoire.traces.ledger import HOLD_FOLLOWUP_TAG, POLICY_HOLD_TAG, TraceLedger


class TestActionFingerprint:
    def test_same_tool_and_detail_produce_the_same_fingerprint(self) -> None:
        assert action_fingerprint("Bash", "rm -rf /tmp/x") == action_fingerprint("Bash", "rm -rf /tmp/x")

    def test_a_different_detail_produces_a_different_fingerprint(self) -> None:
        assert action_fingerprint("Bash", "rm -rf /tmp/x") != action_fingerprint("Bash", "rm -rf /tmp/y")

    def test_never_contains_the_raw_command(self) -> None:
        """La contrainte du prompt : jamais le contenu, seulement une empreinte."""
        secret_ish = "curl https://example.com/super-secret-token-abc123"  # noqa: S105 — texte de commande, pas un mot de passe en dur
        fingerprint = action_fingerprint("Bash", secret_ish)
        assert "secret" not in fingerprint
        assert "curl" not in fingerprint
        assert secret_ish not in fingerprint


class TestTargetKey:
    def test_a_shell_command_reduces_to_tool_plus_verb(self) -> None:
        assert target_key("Bash", "git -C /somewhere push") == "bash:git"
        assert target_key("Bash", "rm -rf /tmp/x") == "bash:rm"

    def test_a_file_path_reduces_to_tool_plus_parent_dir_name(self) -> None:
        assert target_key("Write", "/repo/src/grimoire/foo.py") == "write:grimoire"

    def test_empty_detail_never_raises(self) -> None:
        assert target_key("Bash", "") == "bash:"


class TestRecordPolicyHoldAndFollowup:
    def _ledger(self, project_root: Path) -> TraceLedger:
        return TraceLedger(project_root / TRACES_DIR)

    def test_record_policy_hold_is_a_noop_without_a_session_id(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path,
            session_id="",
            task_id="t1",
            hook_id="grimoire.tool-policy",
            reason="tool_policy:ask",
            fingerprint="fp1",
            target="bash:rm",
        )
        assert not (tmp_path / TRACES_DIR / "traces.jsonl").exists()

    def test_record_policy_hold_writes_a_tagged_trace(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path,
            session_id="s-1",
            task_id="t1",
            hook_id="grimoire.tool-policy",
            reason="tool_policy:ask",
            fingerprint="fp1",
            target="bash:rm",
        )
        traces = self._ledger(tmp_path)._load_all()
        assert len(traces) == 1
        assert POLICY_HOLD_TAG in traces[0].tags
        assert "fingerprint:fp1" in traces[0].tags
        assert "target:bash:rm" in traces[0].tags

    def test_followup_labels_an_exact_replay_as_retried_same(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp1", target="bash:rm")
        report = self._ledger(tmp_path).policy_hold_calibration()
        assert report["groups"][0]["labels"]["retried_same"] == 1

    def test_followup_labels_a_same_target_different_fingerprint_as_retried_variant(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp-different", target="bash:rm")
        report = self._ledger(tmp_path).policy_hold_calibration()
        assert report["groups"][0]["labels"]["retried_variant"] == 1

    def test_followup_labels_an_unrelated_action_as_respected(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp-other", target="write:src")
        report = self._ledger(tmp_path).policy_hold_calibration()
        assert report["groups"][0]["labels"]["respected"] == 1

    def test_followup_is_a_noop_without_an_open_hold(self, tmp_path: Path) -> None:
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp1", target="bash:rm")
        traces = self._ledger(tmp_path)._load_all()
        assert not any(HOLD_FOLLOWUP_TAG in t.tags for t in traces)

    def test_followup_only_answers_the_matching_session(self, tmp_path: Path) -> None:
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        record_hold_followup(tmp_path, session_id="s-other", fingerprint="fp1", target="bash:rm")
        report = self._ledger(tmp_path).policy_hold_calibration()
        assert report["groups"][0]["labels"]["abandoned"] == 1
