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


class TestHookPathNeverReadsTheLedger:
    """Correctif de latence (revue de la PR) : le chemin du hook n'ouvre jamais
    ``traces.jsonl`` en lecture — seul ``grimoire hooks calibrate`` le fait.

    Mesuré (A/B, médiane de 7, hook réel) : la version précédente de
    ``record_hold_followup`` (``TraceLedger.open_policy_hold``, un
    ``_load_all()`` complet) coûtait ~10 ms de plus par appel contre un
    journal de 200 lignes déjà écrites — un coût qui ne fait que croître
    avec l'âge du projet. Le monkeypatch ci-dessous échoue bruyamment si
    quoi que ce soit sur le chemin du hook relit le journal.
    """

    def test_record_policy_hold_never_reads_the_ledger(self, tmp_path: Path, monkeypatch) -> None:
        # Un compteur, pas une exception qui lève : record_policy_hold enrobe
        # tout dans un ``except Exception: pass`` (best-effort, jamais au prix
        # du hook) — une ``AssertionError`` levée depuis ``_load_all`` serait
        # silencieusement avalée par ce même garde-fou et ce test verrait
        # passer une régression sans jamais la voir échouer.
        calls = {"count": 0}
        original = TraceLedger._load_all

        def _counting(self: TraceLedger) -> list[object]:
            calls["count"] += 1
            return original(self)

        monkeypatch.setattr(TraceLedger, "_load_all", _counting)
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        assert calls["count"] == 0, "record_policy_hold a relu traces.jsonl"

    def test_record_hold_followup_never_reads_the_ledger(self, tmp_path: Path, monkeypatch) -> None:
        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )

        calls = {"count": 0}
        original = TraceLedger._load_all

        def _counting(self: TraceLedger) -> list[object]:
            calls["count"] += 1
            return original(self)

        monkeypatch.setattr(TraceLedger, "_load_all", _counting)
        record_hold_followup(tmp_path, session_id="s-1", fingerprint="fp1", target="bash:rm")
        assert calls["count"] == 0, "record_hold_followup a relu traces.jsonl"

        # La calibration, elle, a le droit de relire — seule ce test
        # l'interdit explicitement au chemin du hook ci-dessus.
        monkeypatch.undo()
        report = TraceLedger(tmp_path / TRACES_DIR).policy_hold_calibration()
        assert report["groups"][0]["labels"]["retried_same"] == 1


class TestHoldStateFileIsBounded:
    """Le fichier d'état par session (pas le TraceLedger) est le seul relu par
    le chemin du hook — borné à ``_MAX_OPEN_HOLDS`` entrées (voir le
    docstring du module)."""

    def test_state_file_never_exceeds_the_cap(self, tmp_path: Path) -> None:
        from grimoire.hosts.decisions.calibration import _MAX_OPEN_HOLDS, _hold_state_path, _load_open_holds

        for i in range(_MAX_OPEN_HOLDS + 10):
            record_policy_hold(
                tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
                reason="tool_policy:ask", fingerprint=f"fp{i}", target="bash:rm",
            )
        holds = _load_open_holds(_hold_state_path(tmp_path, "s-1"))
        assert len(holds) == _MAX_OPEN_HOLDS
        # Les plus anciens tombent, les plus récents restent.
        assert holds[-1]["fingerprint"] == f"fp{_MAX_OPEN_HOLDS + 9}"

    def test_state_file_write_is_atomic_no_stray_tmp_file_left_behind(self, tmp_path: Path) -> None:
        from grimoire.hosts.decisions.calibration import _hold_state_path

        record_policy_hold(
            tmp_path, session_id="s-1", task_id="t1", hook_id="grimoire.tool-policy",
            reason="tool_policy:ask", fingerprint="fp1", target="bash:rm",
        )
        path = _hold_state_path(tmp_path, "s-1")
        assert path.is_file()
        assert list(path.parent.glob(".policy-holds-*.tmp")) == []


def test_a_repetition_nudge_is_journaled_as_a_policy_hold(tmp_path: Path) -> None:
    from grimoire.hosts.decisions import HookInput, decide_evidence_trace
    from grimoire.hosts.events import HookEvent

    for _ in range(6):
        decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=tmp_path,
                tool_name="Bash",
                tool_input={"command": "false-cmd"},
                tool_response={"exit_code": 1, "stderr": "boom"},
                session_id="s-rep",
            )
        )
    groups = TraceLedger(tmp_path / TRACES_DIR).policy_hold_calibration()["groups"]
    reasons = {g["reason"] for g in groups}
    assert "repetition:nudge" in reasons
