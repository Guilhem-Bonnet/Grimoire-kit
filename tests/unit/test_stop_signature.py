"""W2-02 : garde Stop à signature de progrès, plafond par profil, relâchement tracé.

Avant : un ``Stop`` continué par l'hôte (``stop_hook_active``) sortait sans rien
regarder, que la session ait avancé ou non. Maintenant le refus est maintenu tant
que la signature change, sous un plafond par profil, et chaque relâchement est
tracé (``stop.released``).
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_file_write_event,
    read_guard_events,
)
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate
from grimoire.hosts.decisions.done_gate import caps_state_relpath
from grimoire.hosts.events import HookEvent

TASK = "bootstrap"
_ARTIFACT = "_grimoire-output/context/bootstrap/context-bundle.yaml"


def _project(root: Path, profile: str = "governed", *, git: bool = False) -> Path:
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True, encoding="utf-8")
    setup_standard_profile(root, profile_id=profile, task_id=TASK)
    board = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == TASK:
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board.write_text(stream.getvalue(), encoding="utf-8")
    scaffold_task_artifacts(root, task_id=TASK)
    (root / _ARTIFACT).unlink(missing_ok=True)  # gate rouge
    return root


def _stop(root: Path, *, active: bool) -> object:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id="s-1", stop_active=active))


def _progress(root: Path, n: int) -> None:
    """Un pas de plus de la session : une écriture journalisée (change l'empreinte d'arbre)."""
    append_evidence_event(root, TASK, build_file_write_event(f"/outside/progress-{n}.py"))


@pytest.fixture(autouse=True)
def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    """W2-02-S3 : le refus maintenu est en shadow par défaut ; ces tests mesurent l'opt-in ``enforce``."""
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "enforce")


def _shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_DONE_GATE", raising=False)


def _real_progress(root: Path, n: int) -> None:
    """Un vrai pas de travail : un fichier source de plus dans l'arbre git."""
    (root / f"work_{n}.py").write_text(f"x = {n}\n", encoding="utf-8")


def _run_hook_stop(root: Path, *, active: bool) -> tuple[str, dict]:  # type: ignore[type-arg]
    from grimoire.bridges.schemas import HostId
    from grimoire.hosts.runtime import run_hook

    payload = {"hook_event_name": "Stop", "session_id": "s-1", "stop_hook_active": active, "cwd": str(root)}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    return str(rendered.get("decision", "allow")), decision.detail


def test_same_signature_releases_the_second_stop_and_traces_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    second = _stop(root, active=True)
    assert second.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    released = [e for e in read_guard_events(root, TASK) if e["type"] == "stop.released"]
    assert len(released) == 1
    assert released[0]["reason"] == "same_signature"
    assert released[0]["red_gates"]


def test_changed_signature_keeps_the_block(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    _progress(root, 1)
    again = _stop(root, active=True)
    assert again.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert again.detail["stop_signature"]["release"] is False  # type: ignore[attr-defined]
    assert not [e for e in read_guard_events(root, TASK) if e["type"] == "stop.released"]


@pytest.mark.parametrize("profile", ["governed", "production"])
def test_the_cap_plus_one_stop_always_passes(tmp_path: Path, profile: str) -> None:
    from grimoire.hosts.decisions.done_gate import stop_cap

    root = _project(tmp_path, profile)
    cap = stop_cap(profile)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    for n in range(cap - 1):  # progrès à chaque tour : les refus 2..cap sont maintenus
        _progress(root, n)
        assert _stop(root, active=True).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    _progress(root, 99)  # la signature change encore, mais le plafond est atteint
    last = _stop(root, active=True)
    assert last.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    released = [e for e in read_guard_events(root, TASK) if e["type"] == "stop.released"]
    assert released[-1]["reason"] == "cap_reached"
    assert released[-1]["cap"] == cap


def test_caps_per_profile_are_fixed() -> None:
    from grimoire.hosts.decisions.done_gate import stop_cap

    assert (stop_cap("orchestrated"), stop_cap("governed"), stop_cap("production")) == (2, 4, 8)


def test_the_counter_survives_a_restart(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    stored = json.loads((root / caps_state_relpath(TASK)).read_text(encoding="utf-8"))
    assert stored["stop"]["count"] == 1  # lu depuis le disque : pas d'état en mémoire de processus
    _progress(root, 1)
    assert _stop(root, active=True).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert json.loads((root / caps_state_relpath(TASK)).read_text(encoding="utf-8"))["stop"]["count"] == 2


@pytest.mark.parametrize("corrupt", ['{"stop": {"count": "x", "signature": 3}}', '{"stop": 7}', "not json"])
def test_a_corrupt_state_falls_back_to_zero(tmp_path: Path, corrupt: str) -> None:
    root = _project(tmp_path)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    (root / caps_state_relpath(TASK)).write_text(corrupt, encoding="utf-8")
    _progress(root, 1)
    assert _stop(root, active=True).outcome is Outcome.ALLOW  # compteur à 0 : aucune chaîne, la session sort
    # ...et une nouvelle chaîne repart proprement.
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert json.loads((root / caps_state_relpath(TASK)).read_text(encoding="utf-8"))["stop"]["count"] == 1


def test_a_continued_stop_without_any_chain_still_passes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _stop(root, active=True).outcome is Outcome.ALLOW  # type: ignore[attr-defined]


# --- W2-02-S1 : l'empreinte d'arbre ignore ce que le hook Stop écrit lui-même -------------------------------


def test_a_git_project_with_no_action_releases_the_second_stop_with_same_signature(tmp_path: Path) -> None:
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    second = _stop(root, active=True)
    assert second.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert second.detail["stop_signature"]["reason"] == "same_signature"  # type: ignore[attr-defined]
    released = [e for e in read_guard_events(root, TASK) if e["type"] == "stop.released"]
    assert [e["reason"] for e in released] == ["same_signature"]


def test_a_git_project_with_no_action_releases_through_run_hook(tmp_path: Path) -> None:
    root = _project(tmp_path, git=True)
    decision, _ = _run_hook_stop(root, active=False)
    assert decision == "block"
    decision, detail = _run_hook_stop(root, active=True)  # run_hook écrit aussi traces/ et le journal d'événements
    assert decision == "allow"
    assert detail["stop_signature"]["reason"] == "same_signature"


def test_the_fingerprint_ignores_every_file_the_stop_hook_writes_itself(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.done_gate import _tree_fingerprint

    root = _project(tmp_path, git=True)
    before = _tree_fingerprint(root, TASK)
    assert before.startswith("git:")
    for rel in (
        "_grimoire-output/events/runtime-journal.jsonl",
        "_grimoire-output/traces/traces.jsonl",
        "_grimoire-output/traces/.traces.jsonl.lock",
        "_grimoire-output/.runs/evidence/bootstrap/done-gate-state.json",
    ):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("une ligne de plus\n", encoding="utf-8")
    assert _tree_fingerprint(root, TASK) == before


def test_the_fingerprint_still_sees_real_work_in_a_git_project(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.done_gate import _tree_fingerprint

    root = _project(tmp_path, git=True)
    before = _tree_fingerprint(root, TASK)
    _real_progress(root, 1)
    assert _tree_fingerprint(root, TASK) != before


def test_real_work_keeps_the_block_in_a_git_project(tmp_path: Path) -> None:
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    _real_progress(root, 1)
    again = _stop(root, active=True)
    assert again.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert again.detail["stop_signature"]["reason"] == "signature_changed"  # type: ignore[attr-defined]


# --- W2-02-S2 : un compteur qui ne s'écrit pas ne doit jamais tenir la session ---------------------------------


def _lock_state_dir(root: Path) -> Path:
    folder = (root / caps_state_relpath(TASK)).parent
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return folder


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignore les droits de répertoire")
def test_an_unwritable_counter_releases_the_continued_stop(tmp_path: Path) -> None:
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    folder = _lock_state_dir(root)
    try:
        outcomes = []
        for n in range(1, 6):
            _real_progress(root, n)  # la signature change à chaque tour : seul le compteur peut arrêter la boucle
            outcomes.append(_stop(root, active=True).outcome)  # type: ignore[attr-defined]
            if outcomes[-1] is Outcome.ALLOW:
                break
    finally:
        folder.chmod(stat.S_IRWXU)
    assert outcomes[-1] is Outcome.ALLOW
    assert len(outcomes) == 1  # relâché dès le premier Stop continué dont le compteur ne s'écrit pas


def test_an_unwritable_counter_is_released_and_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    real_replace = Path.replace

    def failing_replace(self: Path, target: str | os.PathLike[str]) -> Path:
        if Path(target).name == "done-gate-state.json":
            raise PermissionError(13, "lecture seule", str(target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    _real_progress(root, 1)
    step = _stop(root, active=True)
    assert step.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert step.detail["stop_signature"]["reason"] == "state_unwritable"  # type: ignore[attr-defined]
    events = read_guard_events(root, TASK)
    assert [e["reason"] for e in events if e["type"] == "stop.released"] == ["state_unwritable"]
    errors = [e for e in events if e["type"] == "guard.error"]
    assert errors
    assert errors[-1]["guard_id"] == "done_gate.stop_state"


def test_a_chain_that_cannot_be_opened_is_traced_and_the_next_stop_goes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, git=True)
    real_replace = Path.replace

    def failing_replace(self: Path, target: str | os.PathLike[str]) -> Path:
        if Path(target).name == "done-gate-state.json":
            raise PermissionError(13, "lecture seule", str(target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    errors = [e for e in read_guard_events(root, TASK) if e["type"] == "guard.error"]
    assert [e["guard_id"] for e in errors] == ["done_gate.stop_chain"]
    _real_progress(root, 1)
    assert _stop(root, active=True).outcome is Outcome.ALLOW  # type: ignore[attr-defined]


# --- W2-02-S3 : shadow d'abord ----------------------------------------------------------------------------------


def test_the_held_refusal_is_shadow_by_default_and_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shadow(monkeypatch)
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    _real_progress(root, 1)
    held = _stop(root, active=True)
    assert held.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert held.detail["stop_signature"]["shadow"] is True  # type: ignore[attr-defined]
    would = [e for e in read_guard_events(root, TASK) if e["type"] == "stop.would_block"]
    assert len(would) == 1
    assert (would[0]["blocks"], would[0]["cap"], would[0]["profile"]) == (2, 4, "governed")
    assert would[0]["red_gates"]
    assert not [e for e in read_guard_events(root, TASK) if e["type"] == "stop.released"]


def test_the_shadow_still_counts_so_the_measure_follows_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shadow(monkeypatch)
    root = _project(tmp_path, git=True)
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    for n in range(1, 5):
        _real_progress(root, n)
        assert _stop(root, active=True).outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    events = read_guard_events(root, TASK)
    assert [e["blocks"] for e in events if e["type"] == "stop.would_block"] == [2, 3, 4]
    assert [e["reason"] for e in events if e["type"] == "stop.released"] == ["cap_reached"]


def test_the_profile_option_turns_the_held_refusal_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shadow(monkeypatch)
    root = _project(tmp_path, git=True)
    profile = root / "_grimoire/standard/standard-profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8") + "\noptions:\n  done_gate: enforce\n", encoding="utf-8")
    assert _stop(root, active=False).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    _real_progress(root, 1)
    assert _stop(root, active=True).outcome is Outcome.BLOCK  # type: ignore[attr-defined]
