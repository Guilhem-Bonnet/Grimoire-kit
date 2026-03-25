"""Tests for core/scaffold.py — project scaffolding engine."""

from __future__ import annotations

from pathlib import Path

from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import (
    FileCopy,
    ProjectScaffolder,
    ScaffoldPlan,
    ScaffoldResult,
    TemplateRender,
)
from grimoire.core.scanner import ScanResult, StackDetection


def _scan(*stacks: str) -> ScanResult:
    return ScanResult(
        stacks=tuple(StackDetection(name=s, confidence=0.9, evidence=(f"{s}-marker",)) for s in stacks),
        project_type="infrastructure" if "terraform" in stacks else "generic",
        root=Path("/fake"),
    )


def _resolved(archetype: str = "minimal", stack_agents: tuple[str, ...] = (), feature_agents: tuple[str, ...] = ()) -> ResolvedArchetype:
    return ResolvedArchetype(
        archetype=archetype,
        stack_agents=stack_agents,
        feature_agents=feature_agents,
        reason="test",
    )


def _scaffolder(tmp_path: Path, *, archetype: str = "minimal", stacks: tuple[str, ...] = (), stack_agents: tuple[str, ...] = (), feature_agents: tuple[str, ...] = (), backend: str = "local") -> ProjectScaffolder:
    return ProjectScaffolder(
        tmp_path,
        project_name="test-project",
        user_name="Test User",
        language="Français",
        skill_level="intermediate",
        scan=_scan(*stacks),
        resolved=_resolved(archetype, stack_agents, feature_agents),
        backend=backend,
    )


class TestScaffoldPlan:
    def test_empty_plan(self) -> None:
        p = ScaffoldPlan()
        assert p.total_operations == 0

    def test_total_operations(self) -> None:
        p = ScaffoldPlan(
            directories=[Path("/a")],
            copies=[FileCopy(src=Path("/s"), dst=Path("/d"))],
            templates=[TemplateRender(dst=Path("/t"), content="x")],
        )
        assert p.total_operations == 3


class TestScaffoldResult:
    def test_total(self) -> None:
        r = ScaffoldResult(
            created_dirs=["a"],
            copied_files=["b", "c"],
            rendered_files=["d"],
        )
        assert r.total == 4


class TestProjectScaffolder:
    def test_plan_creates_directories(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        dir_strs = [str(d) for d in plan.directories]
        assert any("_grimoire" in d for d in dir_strs)
        assert any("_grimoire-output" in d for d in dir_strs)
        assert any("agents" in d for d in dir_strs)

    def test_plan_includes_meta_agents(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        meta_labels = [lb for lb in labels if lb.startswith("meta/")]
        assert len(meta_labels) >= 3, f"Expected >=3 meta agents, got {meta_labels}"

    def test_plan_includes_archetype_agents(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path, archetype="infra-ops")
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        infra_labels = [lb for lb in labels if lb.startswith("infra-ops/")]
        assert len(infra_labels) >= 5, f"Expected >=5 infra agents, got {infra_labels}"

    def test_plan_includes_stack_agents(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path, stack_agents=("go-expert", "docker-expert"))
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        assert "stack/go-expert" in labels
        assert "stack/docker-expert" in labels

    def test_plan_includes_vectus(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path, feature_agents=("vectus",))
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        assert "feature/vectus" in labels

    def test_plan_no_vectus_for_local_backend(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path, feature_agents=())
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        assert "feature/vectus" not in labels

    def test_plan_includes_framework(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        labels = [fc.label for fc in plan.copies]
        assert "framework/agent-base.md" in labels
        assert "framework/cc-verify.sh" in labels

    def test_plan_includes_project_context(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        assert "project-context.yaml" in tpl_labels

    def test_plan_includes_memory_config(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        assert "_grimoire/_memory/config.yaml" in tpl_labels

    def test_plan_includes_agent_manifest(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        assert "agent-manifest.csv" in tpl_labels

    def test_plan_includes_shared_context(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        shared = [lb for lb in tpl_labels if "shared-context" in lb]
        assert len(shared) == 1

    def test_plan_archetype_shared_context_takes_priority(self, tmp_path: Path) -> None:
        """Archetypes with shared-context.tpl.md should override the default."""
        s = _scaffolder(tmp_path, archetype="infra-ops")
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        # Should have the archetype one, not the default
        assert "shared-context.md (archetype)" in tpl_labels
        assert "shared-context.md (default)" not in tpl_labels

    # ── Execute ───────────────────────────────────────────────────────

    def test_execute_creates_directories(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        res = s.execute(plan)
        assert len(res.created_dirs) >= 10
        assert (tmp_path / "_grimoire" / "_config" / "custom" / "agents").is_dir()

    def test_execute_copies_agents(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        agents_dir = tmp_path / "_grimoire" / "_config" / "custom" / "agents"
        assert agents_dir.is_dir()
        md_files = list(agents_dir.glob("*.md"))
        assert len(md_files) >= 3, f"Expected >=3 agent files, got {len(md_files)}"

    def test_execute_writes_project_context(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        ctx_file = tmp_path / "project-context.yaml"
        assert ctx_file.is_file()
        content = ctx_file.read_text()
        assert "test-project" in content
        assert "Test User" in content
        assert "minimal" in content

    def test_execute_writes_memory_config(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        cfg = tmp_path / "_grimoire" / "_memory" / "config.yaml"
        assert cfg.is_file()
        content = cfg.read_text()
        assert "Test User" in content

    def test_execute_writes_manifest(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        manifest = tmp_path / "_grimoire" / "_config" / "agent-manifest.csv"
        assert manifest.is_file()
        content = manifest.read_text()
        assert "name,file,role,icon" in content

    def test_execute_result_counts_match(self, tmp_path: Path) -> None:
        s = _scaffolder(tmp_path)
        plan = s.plan()
        result = s.execute(plan)
        assert result.total == plan.total_operations

    def test_full_infra_ops_scaffold(self, tmp_path: Path) -> None:
        """Integration test: full infra-ops scaffold with stack agents."""
        s = _scaffolder(
            tmp_path,
            archetype="infra-ops",
            stacks=("go", "terraform", "docker", "kubernetes"),
            stack_agents=("go-expert", "terraform-expert", "docker-expert", "k8s-expert"),
            feature_agents=("vectus",),
            backend="qdrant-local",
        )
        plan = s.plan()
        s.execute(plan)

        # Agents directory should have meta + infra-ops + stack + feature agents
        agents_dir = tmp_path / "_grimoire" / "_config" / "custom" / "agents"
        md_files = list(agents_dir.glob("*.md"))
        agent_names = {f.stem for f in md_files}

        # Meta agents
        assert "project-navigator" in agent_names
        assert "memory-keeper" in agent_names

        # Infra-ops agents
        assert "ops-engineer" in agent_names
        assert "k8s-navigator" in agent_names

        # Stack experts
        assert "go-expert" in agent_names
        assert "terraform-expert" in agent_names
        assert "docker-expert" in agent_names
        assert "k8s-expert" in agent_names

        # Feature agent
        assert "vectus" in agent_names

        # Project context should mention infra-ops
        ctx = (tmp_path / "project-context.yaml").read_text()
        assert "infra-ops" in ctx
        assert "qdrant-local" in ctx

    def test_idempotent_execute(self, tmp_path: Path) -> None:
        """Running execute twice should not fail."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        result2 = s.execute(plan)
        assert result2.total == plan.total_operations

    def test_plan_includes_copilot_instructions(self, tmp_path: Path) -> None:
        """Scaffold must generate .github/copilot-instructions.md."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        tpl_labels = [t.label for t in plan.templates]
        assert ".github/copilot-instructions.md" in tpl_labels

    def test_copilot_instructions_contains_agents_table(self, tmp_path: Path) -> None:
        """Generated copilot-instructions should list installed agents."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        ci = (tmp_path / ".github" / "copilot-instructions.md").read_text()
        assert "| Agent |" in ci
        assert "concierge" in ci

    def test_copilot_instructions_not_overwritten(self, tmp_path: Path) -> None:
        """Existing copilot-instructions.md should not be overwritten."""
        gh = tmp_path / ".github"
        gh.mkdir()
        existing = gh / "copilot-instructions.md"
        existing.write_text("# Custom instructions\n")
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        assert existing.read_text() == "# Custom instructions\n"

    def test_plan_generates_agent_wrappers(self, tmp_path: Path) -> None:
        """Each deployed agent must get a .github/agents/*.agent.md wrapper."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        wrapper_labels = [t.label for t in plan.templates if ".agent.md" in t.label]
        assert len(wrapper_labels) > 0
        assert any("concierge" in label for label in wrapper_labels)

    def test_agent_wrapper_has_frontmatter(self, tmp_path: Path) -> None:
        """Generated .agent.md wrapper must have YAML frontmatter with description."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        wrapper = tmp_path / ".github" / "agents" / "concierge.agent.md"
        assert wrapper.is_file()
        content = wrapper.read_text()
        assert content.startswith("---\n")
        assert "description:" in content

    def test_concierge_is_user_invocable(self, tmp_path: Path) -> None:
        """Concierge wrapper must NOT have user-invocable: false."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        content = (tmp_path / ".github" / "agents" / "concierge.agent.md").read_text()
        assert "user-invocable: false" not in content

    def test_non_concierge_agents_are_sub_agents(self, tmp_path: Path) -> None:
        """Non-concierge agents should be marked user-invocable: false."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        wrapper = tmp_path / ".github" / "agents" / "project-navigator.agent.md"
        assert wrapper.is_file()
        content = wrapper.read_text()
        assert "user-invocable: false" in content

    def test_agent_wrapper_references_internal_file(self, tmp_path: Path) -> None:
        """Wrapper activation must reference the internal agent file."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        content = (tmp_path / ".github" / "agents" / "concierge.agent.md").read_text()
        assert "_grimoire/_config/custom/agents/concierge.md" in content

    def test_agent_wrapper_not_overwritten(self, tmp_path: Path) -> None:
        """Existing .agent.md wrappers should not be overwritten."""
        gh = tmp_path / ".github" / "agents"
        gh.mkdir(parents=True)
        existing = gh / "concierge.agent.md"
        existing.write_text("# My custom agent\n")
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        assert existing.read_text() == "# My custom agent\n"

    def test_copilot_instructions_mentions_at_concierge(self, tmp_path: Path) -> None:
        """copilot-instructions.md should tell users about @concierge."""
        s = _scaffolder(tmp_path)
        plan = s.plan()
        s.execute(plan)
        ci = (tmp_path / ".github" / "copilot-instructions.md").read_text()
        assert "@concierge" in ci
