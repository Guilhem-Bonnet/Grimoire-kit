"""An archetype's workflows must be installed, not only its agents.

The defect these cover: `fix-loop` ships `workflow-closed-loop-fix.tpl.md` and
its agent's [FX] menu points at the installed copy, but the scaffolder planned
only agents, DNA and shared-context. The workflow reached no project, and the
menu resolved to `_grimoire/bmb/workflows/…` — a path nothing ever creates.
"""

from __future__ import annotations

import re
from pathlib import Path

from grimoire.archetypes import bundled_path
from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, _strip_tpl_suffix
from grimoire.core.scanner import ScanResult

WORKFLOWS_DIR = ("_grimoire", "_config", "custom", "workflows")


def _scaffolder(tmp_path: Path, archetype: str = "fix-loop") -> ProjectScaffolder:
    return ProjectScaffolder(
        tmp_path,
        project_name="test-project",
        user_name="Test User",
        language="Français",
        skill_level="intermediate",
        scan=ScanResult(stacks=(), project_type="generic", root=Path("/fake")),
        resolved=ResolvedArchetype(
            archetype=archetype,
            stack_agents=(),
            feature_agents=(),
            reason="test",
        ),
        backend="local",
    )


class TestStripTplSuffix:
    def test_strips_tpl_marker(self) -> None:
        assert _strip_tpl_suffix("workflow-closed-loop-fix.tpl.md") == "workflow-closed-loop-fix.md"

    def test_strips_on_non_markdown(self) -> None:
        assert _strip_tpl_suffix("workflow-graph.tpl.yaml") == "workflow-graph.yaml"

    def test_leaves_plain_names_alone(self) -> None:
        assert _strip_tpl_suffix("incident-response.md") == "incident-response.md"

    def test_leaves_tpl_extension_alone(self) -> None:
        """`github-cc-check.yml.tpl` ends with .tpl — it is not an infix marker."""
        assert _strip_tpl_suffix("github-cc-check.yml.tpl") == "github-cc-check.yml.tpl"

    def test_handles_extensionless_name(self) -> None:
        assert _strip_tpl_suffix("README") == "README"


class TestArchetypeWorkflowsAreInstalled:
    def test_workflow_is_planned(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path).plan()
        assert "workflow-closed-loop-fix.md" in [fc.dst.name for fc in plan.copies]

    def test_tpl_marker_never_reaches_the_project(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path).plan()
        assert not [fc for fc in plan.copies if ".tpl." in fc.dst.name]

    def test_lands_next_to_framework_workflows(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path).plan()
        wf = next(fc for fc in plan.copies if fc.dst.name == "workflow-closed-loop-fix.md")
        assert wf.dst.parent == tmp_path.joinpath(*WORKFLOWS_DIR)

    def test_archetype_without_workflows_is_unaffected(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path, archetype="minimal").plan()
        installed = {fc.dst for fc in plan.copies}
        shipped = {
            _strip_tpl_suffix(wf.name)
            for wf in (bundled_path() / "fix-loop" / "workflows").iterdir()
            if wf.is_file()
        }
        assert not {dst for dst in installed if dst.name in shipped}


class TestAgentReferencesResolve:
    def test_agent_exec_targets_are_installed(self, tmp_path: Path) -> None:
        """Scoped to workflows the kit ships.

        Agents also reference `_grimoire/core/workflows/party-mode/workflow.md`,
        which no archetype provides — a separate gap, tracked on its own. Widening
        this assertion to every exec target would fail permanently and hide the
        regression it is meant to catch.
        """
        archetypes_root = bundled_path()
        shipped = {
            _strip_tpl_suffix(wf.name)
            for wf in archetypes_root.glob("*/workflows/*")
            if wf.is_file()
        }
        assert shipped, "expected at least one archetype to ship a workflow"

        agent = archetypes_root / "fix-loop" / "agents" / "fix-loop-orchestrator.tpl.md"
        targets = [
            t
            for t in re.findall(r'exec="\{project-root\}/([^"]+)"', agent.read_text(encoding="utf-8"))
            if Path(t).name in shipped
        ]
        assert targets, "expected the fix-loop agent to reference its own workflow"

        planned = {fc.dst for fc in _scaffolder(tmp_path).plan().copies}
        for target in targets:
            assert tmp_path / target in planned, f"agent points at {target}, which nothing installs"
