"""Capsule de reprise : écrite, bornée, relue au SessionStart, sans secret."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    build_file_write_event,
)
from grimoire.hosts.decisions import decide_activation, decide_context_capsule
from grimoire.hosts.decisions._shared import HookEvent, HookInput
from grimoire.hosts.decisions.resume_capsule import (
    CAPSULE_FILENAME,
    FRESH_SECONDS,
    MAX_CAPSULE_CHARS,
    build_resume_state,
    capsule_path,
    read_resume_capsule,
    scrub,
    write_capsule,
)
from grimoire.hosts.decisions.session_memory import SessionMemory, save_session_memory
from grimoire.hosts.runtime import run_hook

# Assemblé : le nom entier ne doit apparaître dans aucun texte du dépôt de test.
_ZONE = "." + "grim" + "oire" + "/" + "con" + "trol"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    return root


def _journal(root: Path, task: str = "bootstrap") -> None:
    append_evidence_event(root, task, build_file_write_event(str(root / "src" / "a.py")))
    append_evidence_event(root, task, build_file_write_event("src/b.py"))
    append_evidence_event(root, task, build_file_write_event(str(root / ".env")))
    append_evidence_event(root, task, build_bash_event("gh pr create --draft --title x 42", exit_code=0))
    append_evidence_event(root, task, build_bash_event("pytest tests/unit/test_a.py -q", exit_code=1))


def _memory(root: Path, session: str, *messages: str) -> None:
    mem = SessionMemory.new(session)
    mem.user_messages = list(messages)
    save_session_memory(root, mem)


def _precompact(root: Path, session: str = "s1") -> HookInput:
    return HookInput(event=HookEvent.PRE_COMPACT, project_root=root, session_id=session)


def _start(root: Path, source: str) -> HookInput:
    return HookInput(event=HookEvent.SESSION_START, project_root=root, raw={"source": source})


def test_the_state_is_built_from_owned_sources_and_bounded(project: Path) -> None:
    _journal(project)
    _memory(project, "s1", "ok", "Ajoute la reprise de session au SessionStart avec une capsule bornée")
    state = build_resume_state(project, "bootstrap", session_id="s1", gates_ok=True)
    assert "Ajoute la reprise de session" in state
    assert "src/a.py" in state and "src/b.py" in state
    assert ".env" not in state
    assert "PR #42" in state
    assert "`pytest tests/unit/test_a.py -q` → exit 1" in state
    assert "corriger puis relancer" in state
    assert len(state) <= MAX_CAPSULE_CHARS


def test_the_state_is_bounded_whatever_the_sources_hold(project: Path) -> None:
    for i in range(80):
        append_evidence_event(project, "bootstrap", build_file_write_event(f"src/{'d' * 60}{i}.py"))
    _memory(project, "s1", "x " * 2000)
    assert len(build_resume_state(project, "bootstrap", session_id="s1", budget=600)) <= 600
    assert len(decide_context_capsule(_precompact(project)).context) <= MAX_CAPSULE_CHARS + 400


def test_precompact_writes_the_capsule_on_disk(project: Path) -> None:
    _journal(project)
    _memory(project, "s1", "Finir la capsule de reprise et ses tests, sans rien oublier")
    payload = {"session_id": "s1", "hook_event_name": "PreCompact", "cwd": str(project)}
    run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.PRE_COMPACT, project_root=project)
    path = capsule_path(project, "bootstrap")
    assert path.name == CAPSULE_FILENAME
    written = path.read_text(encoding="utf-8")
    assert "Finir la capsule" in written and "src/a.py" in written


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_session_start_reads_the_capsule_back(project: Path, source: str) -> None:
    write_capsule(project, "bootstrap", "Objectif : finir la capsule\nPrésent : oui")
    decision = decide_activation(_start(project, source))
    assert "Objectif : finir la capsule" in decision.context
    assert decision.detail["resume_capsule_injected"] is True


def test_resume_drops_the_static_preamble_compact_keeps_it(project: Path) -> None:
    write_capsule(project, "bootstrap", "Objectif : x")
    resume = decide_activation(_start(project, "resume"))
    assert resume.detail["entry_agent"] == "" and resume.detail["dispatch_context_injected"] is False
    reference = decide_activation(_start(project, "startup"))
    compact = decide_activation(_start(project, "compact"))
    assert compact.detail["entry_agent"] == reference.detail["entry_agent"]


@pytest.mark.parametrize("source", ["compact", "resume", "startup"])
def test_nothing_is_injected_without_a_capsule(project: Path, source: str) -> None:
    decision = decide_activation(_start(project, source))
    assert "reprise de session" not in decision.context
    assert decision.detail["resume_capsule_injected"] is False


def test_a_fresh_session_gets_it_only_on_a_real_and_recent_task(project: Path) -> None:
    path = write_capsule(project, "bootstrap", "Objectif : y")
    assert read_resume_capsule(project, "bootstrap", source="startup", task_is_real=False) == ""
    assert "Objectif : y" in read_resume_capsule(project, "bootstrap", source="startup", task_is_real=True)
    later = time.time() + FRESH_SECONDS + 60
    assert read_resume_capsule(project, "bootstrap", source="startup", task_is_real=True, now=later) == ""
    old = time.time() - 10 * FRESH_SECONDS
    os.utime(path, (old, old))
    assert read_resume_capsule(project, "bootstrap", source="compact", task_is_real=False) != ""


def test_live_flow_lines_are_not_duplicated(project: Path) -> None:
    write_capsule(project, "bootstrap", "Objectif : z\n[Grimoire — flow actif] run wf-1 périmé")
    text = read_resume_capsule(project, "bootstrap", source="compact", task_is_real=False, live_flow=True)
    assert "wf-1" not in text and "Objectif : z" in text


def test_no_secret_and_no_control_folder_path(project: Path) -> None:
    home = Path(os.environ["HOME"])
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    zone_file = home / _ZONE / "mandate.json"
    _memory(project, "s1", f"utilise le jeton {secret} et lis {zone_file} avec password=hunter2 sous {home}/x")
    append_evidence_event(project, "bootstrap", build_file_write_event(str(zone_file)))
    body = build_resume_state(project, "bootstrap", session_id="s1")
    write_capsule(project, "bootstrap", f"brut {secret} {zone_file}")
    on_disk = capsule_path(project, "bootstrap").read_text(encoding="utf-8")
    for text in (body, on_disk, scrub(f"{home}/doc {zone_file}")):
        assert secret not in text and "hunter2" not in text
        assert _ZONE not in text and str(home) not in text


def test_git_branch_and_dirty_files_are_reported(project: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "pilot/x")
    git("config", "user.email", "t@example.org")
    git("config", "user.name", "t")
    (project / "f.txt").write_text("1", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-q", "-m", "init")
    (project / "f.txt").write_text("2", encoding="utf-8")
    assert "branche pilot/x, 1 modification(s) non commitée(s)" in build_resume_state(project, "bootstrap")


def test_the_handoff_command_writes_and_prints_a_line(project: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.cmd_host import host_app

    _journal(project)
    result = CliRunner().invoke(host_app, ["handoff", "--project-root", str(project), "--task", "bootstrap"])
    assert result.exit_code == 0, result.output
    assert capsule_path(project, "bootstrap").is_file()
    assert "claude " in result.output and "bootstrap" in result.output
