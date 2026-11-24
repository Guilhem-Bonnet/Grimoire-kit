"""Le workflow d'environnement de l'agent cloud Copilot n'est pas une CI (#1280 P3)."""

from __future__ import annotations

from pathlib import Path

from grimoire.cli.cmd_init import _looks_like_a_playground
from grimoire.core.ci_markers import COPILOT_SETUP_STEPS, github_ci_workflows, has_ci
from grimoire.hosts.emitters.copilot_setup import SETUP_STEPS, setup_steps_file


def _write(root: Path, rel: str, text: str = "x: 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_name_matches_what_the_copilot_emitter_writes() -> None:
    assert SETUP_STEPS.name == COPILOT_SETUP_STEPS


def test_copilot_setup_steps_alone_is_not_ci(tmp_path: Path) -> None:
    _write(tmp_path, SETUP_STEPS.as_posix(), setup_steps_file().content)
    assert not has_ci(tmp_path)
    assert github_ci_workflows(tmp_path) == ()
    assert _looks_like_a_playground(tmp_path)


def test_a_real_workflow_next_to_it_is_ci(tmp_path: Path) -> None:
    _write(tmp_path, SETUP_STEPS.as_posix())
    _write(tmp_path, ".github/workflows/ci.yml")
    assert has_ci(tmp_path)
    assert github_ci_workflows(tmp_path) == (".github/workflows/ci.yml",)
    assert not _looks_like_a_playground(tmp_path)


def test_other_markers_and_an_empty_workflows_dir_still_count(tmp_path: Path) -> None:
    assert not has_ci(tmp_path)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert has_ci(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _write(other, ".gitlab-ci.yml")
    assert has_ci(other)
