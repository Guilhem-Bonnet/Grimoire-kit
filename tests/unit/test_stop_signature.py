"""W2-02 : garde Stop à signature de progrès, plafond par profil, relâchement tracé.

Avant : un ``Stop`` continué par l'hôte (``stop_hook_active``) sortait sans rien
regarder, que la session ait avancé ou non. Maintenant le refus est maintenu tant
que la signature change, sous un plafond par profil, et chaque relâchement est
tracé (``stop.released``).
"""

from __future__ import annotations

import io
import json
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


def _project(root: Path, profile: str = "governed") -> Path:
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
def _no_enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_DONE_GATE", raising=False)


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
