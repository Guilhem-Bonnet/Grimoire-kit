"""GAO-gardes-bash-001 — faux positifs des gardes Bash hors du projet.

Constat (1 585 commandes Bash d'un projet enrôlé, 09→12/11) : la garde
« suppression récursive ou forcée » refusait une suppression forcée *non
récursive* d'un fichier jetable du dossier temporaire du système ou du
scratchpad de la session — donc hors du dépôt — et la notice « code
python interprété » était réinjectée à chaque commande.

Ce qui est vérifié ici : le travail jetable hors du projet passe (y compris
un heredoc que l'interpréteur shell exécute et qui redirige sa sortie vers le
dossier temporaire), tandis que la suppression récursive et toute suppression
dans le projet gardent le refus d'avant. La notice, elle, n'est émise qu'une
fois par session.

Les chemins autorisés sont rendus déterministes : le dossier temporaire « du
système » est monkeypatché vers un frère du projet, et le scratchpad vient du
payload ``scratchpad_dir`` comme chez Claude Code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import decide_tool_policy, tool_facts
from grimoire.hosts.decisions._shared import Decision, HookEvent, HookInput, Outcome

PYTHON_HEREDOC = "python3 - <<'EOF'\nprint('hello')\nEOF\n"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    """Projet gouverné + dossier temporaire + scratchpad + un dossier « ailleurs »."""
    project = tmp_path / "proj"
    project.mkdir()
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    (project / "keep.txt").write_text("x", encoding="utf-8")

    system_tmp = tmp_path / "system-tmp"
    system_tmp.mkdir()
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # Un TMPDIR réel pointerait vers un dossier frère du projet : même
    # géométrie, mais sans dépendre de l'environnement de la machine.
    monkeypatch.setattr(tool_facts, "_system_temp_roots", lambda: (system_tmp.resolve(),))
    return project, system_tmp, scratchpad, elsewhere


def _decide(
    project: Path,
    command: str,
    *,
    session_id: str = "S1",
    scratchpad: Path | None = None,
) -> Decision:
    raw = {"scratchpad_dir": str(scratchpad)} if scratchpad is not None else {}
    hook = HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=project,
        tool_name="Bash",
        tool_input={"command": command},
        session_id=session_id,
        raw=raw,
    )
    return decide_tool_policy(hook)


# ── 1. Suppression forcée, non récursive, hors du projet ────────────────────


@pytest.mark.parametrize("flag", ["-f", "-vf", "--force"])
def test_forced_delete_of_a_system_temp_file_is_allowed(workspace: tuple[Path, Path, Path, Path], flag: str) -> None:
    project, system_tmp, _, _ = workspace
    decision = _decide(project, f"rm {flag} {system_tmp / 'gone.txt'}")
    assert decision.outcome is Outcome.ALLOW, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "rm -f {tmp}/gone.txt 2>/dev/null",
        "sudo rm -f {tmp}/gone.txt",
        "rm -f -- {tmp}/gone.txt",
    ],
)
def test_common_shapes_of_a_forced_temp_delete_are_allowed(
    workspace: tuple[Path, Path, Path, Path], command: str
) -> None:
    project, system_tmp, _, _ = workspace
    decision = _decide(project, command.format(tmp=system_tmp))
    assert decision.outcome is Outcome.ALLOW, decision.reason


def test_forced_delete_in_the_session_scratchpad_is_allowed(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, _, scratchpad, _ = workspace
    decision = _decide(project, f"rm -f {scratchpad / 'scratch.txt'}", scratchpad=scratchpad)
    assert decision.outcome is Outcome.ALLOW, decision.reason


def test_several_forced_deletes_across_temp_and_scratchpad_are_allowed(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, system_tmp, scratchpad, _ = workspace
    command = f"rm -f {system_tmp / 'a'} {scratchpad / 'b'}"
    assert _decide(project, command, scratchpad=scratchpad).outcome is Outcome.ALLOW


def test_a_symlink_under_temp_that_resolves_into_the_project_is_refused(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    """« chemin résolu » : le lien ne cache pas une cible du dépôt."""
    project, system_tmp, _, _ = workspace
    link = system_tmp / "peek"
    link.symlink_to(project / "keep.txt")
    assert _decide(project, f"rm -f {link}").outcome is Outcome.DENY


def test_a_symlink_under_temp_that_resolves_under_temp_is_allowed(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, system_tmp, _, _ = workspace
    real = system_tmp / "real.txt"
    real.write_text("x", encoding="utf-8")
    link = system_tmp / "alias"
    link.symlink_to(real)
    assert _decide(project, f"rm -f {link}").outcome is Outcome.ALLOW


# ── 2. Heredoc vers le dossier temporaire ───────────────────────────────────


def test_a_heredoc_redirected_into_temp_is_allowed(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    """Les corps de heredoc restent de la donnée, jamais une action."""
    project, system_tmp, _, _ = workspace
    command = f"cat > {system_tmp / 'notes.md'} <<'EOF'\nrm -rf /un/chemin/qui/n.existe/pas\nEOF\n"
    decision = _decide(project, command)
    assert decision.outcome is Outcome.ALLOW, decision.reason


def test_a_shell_heredoc_that_deletes_a_temp_file_is_allowed(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    """Le corps d'un heredoc lu par bash s'exécute : sa suppression jetable passe."""
    project, system_tmp, _, _ = workspace
    command = f"bash <<'EOF' > {system_tmp / 'out.txt'}\nrm -f {system_tmp / 'scratch'}\nEOF\n"
    decision = _decide(project, command)
    assert decision.outcome is Outcome.ALLOW, decision.reason


def test_a_shell_heredoc_that_recursively_deletes_a_temp_dir_is_refused(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, system_tmp, _, _ = workspace
    command = f"bash <<'EOF'\nrm -rf {system_tmp / 'build'}\nEOF\n"
    assert _decide(project, command).outcome is Outcome.DENY


# ── 3. Récursif, dans le projet, ou opaque : comportement inchangé ──────────


@pytest.mark.parametrize("flags", ["-rf", "-fr", "-Rf"])
def test_recursive_deletion_stays_destructive(workspace: tuple[Path, Path, Path, Path], flags: str) -> None:
    project, system_tmp, _, _ = workspace
    assert _decide(project, f"rm {flags} {system_tmp / 'build'}").outcome is Outcome.DENY


@pytest.mark.parametrize("paths", ["keep.txt", "{project}/keep.txt"])
def test_forced_deletion_inside_the_project_is_still_refused(
    workspace: tuple[Path, Path, Path, Path], paths: str
) -> None:
    project, _, _, _ = workspace
    command = f"rm -f {paths.format(project=project)}"
    assert _decide(project, command).outcome is Outcome.DENY


def test_forced_deletion_outside_both_project_and_temp_is_refused(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, _, _, elsewhere = workspace
    assert _decide(project, f"rm -f {elsewhere / 'x'}").outcome is Outcome.DENY


def test_a_glob_operand_is_not_resolved_and_keeps_the_refusal(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    """Un opérande que le shell développe n'est pas un chemin résolu : échec fermé."""
    project, system_tmp, _, _ = workspace
    assert _decide(project, f"rm -f {system_tmp}/*.txt").outcome is Outcome.DENY


def test_one_forbidden_target_keeps_the_whole_command_destructive(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, system_tmp, _, _ = workspace
    command = f"rm -f {system_tmp / 'a'} {project / 'keep.txt'}"
    assert _decide(project, command).outcome is Outcome.DENY


def test_another_destructive_pattern_in_the_same_command_is_still_caught(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, system_tmp, _, _ = workspace
    command = f"rm -f {system_tmp / 'a'} && git reset --hard origin/main"
    assert _decide(project, command).outcome is Outcome.DENY


# ── 4. La notice « code interprété », une fois par session ─────────────────


def test_the_interpreted_notice_is_emitted_once_per_session(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, _, _, _ = workspace
    first = _decide(project, PYTHON_HEREDOC, session_id="S-notice")
    second = _decide(project, PYTHON_HEREDOC, session_id="S-notice")

    assert first.outcome is Outcome.ALLOW
    assert "code python interprété" in first.context
    assert second.outcome is Outcome.ALLOW
    assert second.context == ""
    # Le détail garde la langue : c'est la notice, pas le fait, qui est tu.
    assert second.detail["interpreted_code"] == "python"


def test_another_session_receives_the_notice_again(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, _, _, _ = workspace
    _decide(project, PYTHON_HEREDOC, session_id="S-one")
    other = _decide(project, PYTHON_HEREDOC, session_id="S-two")
    assert "code python interprété" in other.context


def test_a_refused_interpreted_command_does_not_consume_the_notice(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    """Un refus pour une autre raison ne fait pas taire la notice pour la session."""
    project, _, _, _ = workspace
    refused = _decide(
        project,
        "python3 - <<'EOF'\nimport shutil; shutil.rmtree('/destructif')\nEOF\n",
        session_id="S-refused",
    )
    assert refused.outcome is Outcome.DENY
    allowed = _decide(project, PYTHON_HEREDOC, session_id="S-refused")
    assert allowed.outcome is Outcome.ALLOW
    assert "code python interprété" in allowed.context
