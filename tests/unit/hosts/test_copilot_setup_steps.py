"""Agent cloud Copilot : `.github/workflows/copilot-setup-steps.yml` (épique #1280, lot P3)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from grimoire.__version__ import __version__
from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import CONFLICT_EDITED, CONFLICT_FOREIGN, MANAGED_MARKER, apply_plans
from grimoire.hosts.emitters.copilot_setup import JOB_NAME, SETUP_STEPS, setup_steps_file

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

LABEL = SETUP_STEPS.as_posix()
#: Clés du job que Copilot lit ; toute autre est ignorée (doc « Customize the agent environment »).
COPILOT_JOB_KEYS = {"steps", "permissions", "runs-on", "services", "snapshot", "timeout-minutes"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _sync(root: Path, preserve: frozenset[str] = frozenset()):
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    return apply_plans([emitter.plan(build_surface(root), root)], root, preserve=preserve)[0]


def _workflow(text: str) -> dict:
    data = yaml.safe_load(text)
    # PyYAML (YAML 1.1) lit la clé `on` comme le booléen True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_render_has_a_single_copilot_job_that_installs_the_project_kit_version() -> None:
    emitted = setup_steps_file("9.8.7")
    assert emitted.relpath == Path(".github/workflows/copilot-setup-steps.yml")
    assert emitted.content.startswith(f"# {MANAGED_MARKER}")
    workflow = _workflow(emitted.content)
    assert list(workflow["jobs"]) == [JOB_NAME]  # un seul job, au nom exact
    job = workflow["jobs"][JOB_NAME]
    assert set(job) <= COPILOT_JOB_KEYS
    assert job["timeout-minutes"] <= 59
    assert job["permissions"] == {"contents": "read"}
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert 'python -m pip install "grimoire-kit==9.8.7"' in runs
    setup = next(step for step in job["steps"] if "uses" in step)
    assert setup["uses"].startswith("actions/setup-python@")
    assert len(setup["uses"].split("@", 1)[1]) == 40  # épinglé par SHA
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["on"]["pull_request"]["paths"] == [LABEL]


def test_render_exposes_no_identifier() -> None:
    content = setup_steps_file().content
    for needle in ("secrets.", "${{", "env:", "token", "TOKEN", "write"):
        assert needle not in content, needle


def test_copilot_sync_writes_the_workflow_at_the_running_kit_version(project: Path) -> None:
    result = _sync(project)
    assert result.ok, result.conflicts
    assert LABEL in result.written
    text = (project / SETUP_STEPS).read_text(encoding="utf-8")
    assert f'"grimoire-kit=={__version__}"' in text
    assert LABEL in (project / ".github/hooks/README.md").read_text(encoding="utf-8")
    again = _sync(project)
    assert LABEL in again.unchanged


def test_a_foreign_workflow_is_never_overwritten(project: Path) -> None:
    mine = "name: mine\njobs:\n  copilot-setup-steps:\n    runs-on: ubuntu-latest\n    steps: []\n"
    (project / SETUP_STEPS).parent.mkdir(parents=True, exist_ok=True)
    (project / SETUP_STEPS).write_text(mine, encoding="utf-8")
    result = _sync(project)
    assert (project / SETUP_STEPS).read_text(encoding="utf-8") == mine
    assert result.conflicts.get(LABEL) == CONFLICT_FOREIGN  # le diagnostic, à la place de l'écriture
    assert LABEL not in result.written


def test_an_edited_kit_workflow_is_kept_and_reported(project: Path) -> None:
    _sync(project)
    path = project / SETUP_STEPS
    edited = path.read_text(encoding="utf-8") + "      - run: make deps\n"
    path.write_text(edited, encoding="utf-8")
    result = _sync(project)
    assert path.read_text(encoding="utf-8") == edited
    assert result.conflicts.get(LABEL) == CONFLICT_EDITED


def test_a_preserved_workflow_is_left_alone_without_conflict(project: Path) -> None:
    (project / SETUP_STEPS).parent.mkdir(parents=True, exist_ok=True)
    (project / SETUP_STEPS).write_text("name: mine\n", encoding="utf-8")
    result = _sync(project, preserve=frozenset({LABEL}))
    assert LABEL in result.preserved
    assert LABEL not in result.conflicts
    assert (project / SETUP_STEPS).read_text(encoding="utf-8") == "name: mine\n"
