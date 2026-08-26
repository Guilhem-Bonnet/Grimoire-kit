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

WORKFLOWS_DIR = ("_grimoire", "kit", "workflows")
AGENTS_DIR = ("_grimoire", "kit", "agents")


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


class TestPlaceholderRendering:
    """Install-time placeholders are resolved; the other three families are not."""

    def _installed(self, tmp_path: Path, archetypes: tuple[str, ...]) -> Path:
        scaffolder = _scaffolder(tmp_path)
        scaffolder._resolved = ResolvedArchetype(
            archetype=archetypes[0], archetypes=archetypes, stack_agents=(), feature_agents=(), reason="test",
        )
        scaffolder.execute(scaffolder.plan())
        return tmp_path

    def test_expert_roles_resolve_to_installed_agents(self, tmp_path: Path) -> None:
        root = self._installed(tmp_path, ("fix-loop", "infra-ops"))
        wf = (root.joinpath(*WORKFLOWS_DIR) / "workflow-closed-loop-fix.md").read_text(encoding="utf-8")
        assert "Forge (ops-engineer)" in wf
        assert "Probe (systems-debugger)" in wf

    def test_unfilled_role_reads_as_absent_not_as_a_marker(self, tmp_path: Path) -> None:
        root = self._installed(tmp_path, ("fix-loop",))
        wf = (root.joinpath(*WORKFLOWS_DIR) / "workflow-closed-loop-fix.md").read_text(encoding="utf-8")
        assert "{{ops_agent_name}}" not in wf
        assert "aucun" in wf

    def test_no_install_time_marker_survives(self, tmp_path: Path) -> None:
        root = self._installed(tmp_path, ("fix-loop", "infra-ops"))
        resolved_keys = ("ops_agent", "debug_agent", "tech_stack_list", "user_name", "project_name")
        for wf in root.joinpath(*WORKFLOWS_DIR).iterdir():
            text = wf.read_text(encoding="utf-8", errors="ignore")
            leaked = [k for k in resolved_keys if "{{" + k + "}}" in text]
            assert not leaked, f"{wf.name} still carries {leaked}"

    def test_runtime_slots_survive_installation(self, tmp_path: Path) -> None:
        """`{{current_step}}` & co are filled per run by the LLM, not at install."""
        root = self._installed(tmp_path, ("minimal",))
        status = (root.joinpath(*WORKFLOWS_DIR) / "workflow-status.md").read_text(encoding="utf-8")
        assert "{{current_step}}" in status
        assert "{{progress_bar}}" in status

    def test_blank_agent_template_keeps_its_blanks(self, tmp_path: Path) -> None:
        """The `minimal` archetype ships a fill-in-the-blank agent on purpose."""
        root = self._installed(tmp_path, ("minimal",))
        blank = root.joinpath(*AGENTS_DIR) / "custom-agent.md"
        assert "{{agent_name}}" in blank.read_text(encoding="utf-8")

    def test_infrastructure_placeholders_are_left_to_the_user(self, tmp_path: Path) -> None:
        """The kit cannot know `{{lxc_id}}` or `{{host_ip}}` — it must not guess."""
        root = self._installed(tmp_path, ("infra-ops",))
        agent = root.joinpath(*AGENTS_DIR) / "k8s-navigator.md"
        assert "{{vm_id}}" in agent.read_text(encoding="utf-8")


class TestAgentReferencesResolve:
    def test_every_shipped_agent_exec_target_is_installed(self, tmp_path: Path) -> None:
        """No shipped agent may point at a workflow nothing installs.

        Two defects this locks down: the fix-loop [FX] menu pointed at
        `_grimoire/bmb/workflows/…`, and 25 agents' [PM] menu pointed at
        `_grimoire/core/workflows/party-mode/workflow.md` — neither path was
        ever created by any installer.

        Installing every archetype at once is what makes the assertion total:
        a target satisfied only by an archetype the user did not pick is still
        a dead reference for that user, but it is a different failure, covered
        by the per-archetype tests above.
        """
        archetypes_root = bundled_path()
        every = tuple(sorted(d.name for d in archetypes_root.iterdir() if (d / "agents").is_dir()))

        targets: dict[str, str] = {}
        for agent in sorted(archetypes_root.glob("*/agents/*.md")):
            for target in re.findall(r'exec="\{project-root\}/([^"]+)"', agent.read_text(encoding="utf-8")):
                targets.setdefault(target, agent.name)
        assert targets, "expected shipped agents to reference workflows"

        scaffolder = _scaffolder(tmp_path)
        scaffolder._resolved = ResolvedArchetype(
            archetype=every[0], archetypes=every, stack_agents=(), feature_agents=(), reason="test",
        )
        planned = {fc.dst for fc in scaffolder.plan().copies}

        dead = {t: a for t, a in targets.items() if tmp_path / t not in planned}
        assert not dead, f"agents point at workflows nothing installs: {dead}"
