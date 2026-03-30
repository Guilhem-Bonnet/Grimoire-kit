"""Unit tests for VS Code Copilot integration in scaffold.py.

Tests focus on the new methods added for Grimoire enrichment:
- _plan_copilot_prompts(): copies workflow prompts to .github/prompts/
- _plan_copilot_instruction_files(): renders instruction files to .github/instructions/
- Enhanced _plan_directories(): includes new Copilot directories
"""

import tempfile
from pathlib import Path

import pytest

from grimoire.core.scaffold import (
    FileCopy,
    ProjectScaffolder,
    ScaffoldPlan,
)
from grimoire.core.archetype_resolver import ResolvedArchetype


@pytest.fixture
def temp_project():
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def resolved_archetype():
    """Create a minimal ResolvedArchetype for testing."""
    return ResolvedArchetype(
        archetype="minimal",
        archetypes=["minimal"],
        stack_agents=[],
        feature_agents=[],
        reason="test"
    )


@pytest.fixture
def scaffolder(temp_project, resolved_archetype):
    """Create a ProjectScaffolder instance."""
    return ProjectScaffolder(
        target=temp_project,
        project_name="test-project",
        user_name="test-user",
        language="English",
        skill_level="expert",
        scan=None,
        resolved=resolved_archetype,
        backend="local"
    )


class TestPlanDirectoriesIncludesCopilot:
    """Test that _plan_directories() creates Copilot directories."""

    def test_plan_directories_creates_prompts_dir(self, scaffolder):
        """Verify .github/prompts/ is added to the plan."""
        plan = ScaffoldPlan()
        scaffolder._plan_directories(plan)
        
        prompts_path = scaffolder._target / ".github" / "prompts"
        assert prompts_path in plan.directories

    def test_plan_directories_creates_instructions_dir(self, scaffolder):
        """Verify .github/instructions/ is added to the plan."""
        plan = ScaffoldPlan()
        scaffolder._plan_directories(plan)
        
        instructions_path = scaffolder._target / ".github" / "instructions"
        assert instructions_path in plan.directories

    def test_plan_directories_creates_agents_dir(self, scaffolder):
        """Verify .github/agents/ is still created."""
        plan = ScaffoldPlan()
        scaffolder._plan_directories(plan)
        
        agents_path = scaffolder._target / ".github" / "agents"
        assert agents_path in plan.directories

    def test_plan_directories_count(self, scaffolder):
        """Verify expected number of directories are planned."""
        plan = ScaffoldPlan()
        scaffolder._plan_directories(plan)
        
        # Should be 15 directories total
        assert len(plan.directories) == 15


class TestPlanCopilotPrompts:
    """Test _plan_copilot_prompts() copies workflow prompts."""

    def test_plan_copilot_prompts_copies_all_prompts(self, scaffolder):
        """Verify all 7 prompts are copied."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_prompts(plan)
        
        # Extract prompt copies
        prompt_copies = [
            c for c in plan.copies 
            if ".github/prompts" in str(c.dst)
        ]
        assert len(prompt_copies) == 7

    def test_plan_copilot_prompts_destination(self, scaffolder):
        """Verify all prompts go to .github/prompts/."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_prompts(plan)
        
        prompt_copies = [
            c for c in plan.copies 
            if ".github/prompts" in str(c.dst)
        ]
        for copy in prompt_copies:
            assert ".github/prompts" in str(copy.dst)
            assert copy.dst.suffix == ".md"

    def test_plan_copilot_prompts_names(self, scaffolder):
        """Verify expected prompt names are present."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_prompts(plan)
        
        prompt_copies = [
            c for c in plan.copies 
            if ".github/prompts" in str(c.dst)
        ]
        names = [c.dst.stem for c in prompt_copies]
        
        expected_stems = [
            "grimoire-changelog",
            "grimoire-dream",
            "grimoire-health-check",
            "grimoire-pre-push",
            "grimoire-self-heal",
            "grimoire-session-bootstrap",
            "grimoire-status",
        ]
        
        for expected in expected_stems:
            assert any(expected in n for n in names)

    def test_plan_copilot_prompts_no_overwrite(self, scaffolder, temp_project):
        """Verify existing prompts are not overwritten."""
        # Create existing prompt
        prompts_dir = temp_project / ".github" / "prompts"
        prompts_dir.mkdir(parents=True)
        existing = prompts_dir / "grimoire-session-bootstrap.prompt.md"
        existing.write_text("# Custom\n")
        
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_prompts(plan)
        
        # Should not include the session-bootstrap prompt
        bootstrap_copies = [
            c for c in plan.copies 
            if "session-bootstrap" in str(c.dst)
        ]
        assert len(bootstrap_copies) == 0


class TestPlanCopilotInstructions:
    """Test _plan_copilot_instruction_files() renders instructions."""

    def test_plan_copilot_instructions_creates_template(self, scaffolder):
        """Verify instruction file is rendered as template."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        instruction_renders = [
            t for t in plan.templates 
            if ".github/instructions" in str(t.dst)
        ]
        assert len(instruction_renders) >= 1

    def test_plan_copilot_instructions_destination(self, scaffolder):
        """Verify instruction goes to .github/instructions/."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        instruction_renders = [
            t for t in plan.templates 
            if ".github/instructions" in str(t.dst)
        ]
        for render in instruction_renders:
            assert ".github/instructions" in str(render.dst)
            assert render.dst.suffix == ".md"

    def test_plan_copilot_instructions_substitutes_variables(self, scaffolder):
        """Verify template variables are substituted."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        instruction_renders = [
            t for t in plan.templates 
            if ".github/instructions" in str(t.dst)
        ]
        assert len(instruction_renders) > 0
        
        render = instruction_renders[0]
        # Should have substituted project_name
        assert "test-project" in render.content
        assert "{{project_name}}" not in render.content
        # Should have substituted language
        assert "English" in render.content
        assert "{{language}}" not in render.content

    def test_plan_copilot_instructions_no_overwrite(self, scaffolder, temp_project):
        """Verify existing instruction files are not overwritten."""
        # Create existing instruction
        instr_dir = temp_project / ".github" / "instructions"
        instr_dir.mkdir(parents=True)
        existing = instr_dir / "grimoire-project.instructions.md"
        existing.write_text("# Custom\n")
        
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        # Should not be in the plan
        grimoire_project_renders = [
            t for t in plan.templates 
            if "grimoire-project" in str(t.dst)
        ]
        assert len(grimoire_project_renders) == 0


class TestAgentWrappers:
    """Test that agent wrappers are enhanced with proper activation steps."""

    def test_plan_agent_wrappers_creates_wrappers(self, scaffolder):
        """Verify agent wrappers are generated."""
        plan = ScaffoldPlan()
        # Add a mock agent to the plan
        agent_src = scaffolder._target / "test-agent.md"
        agent_src.write_text("---\n\n---\n# Test\n")
        plan.copies.append(FileCopy(
            src=agent_src,
            dst=scaffolder._target / "_grimoire" / "_config" / "custom" / "agents" / "test-agent.md",
            label="test/test-agent"
        ))
        
        scaffolder._plan_agent_wrappers(plan)
        
        # Should generate a wrapper template
        wrappers = [
            t for t in plan.templates 
            if ".agent.md" in str(t.dst)
        ]
        assert len(wrappers) > 0

    def test_agent_wrapper_includes_activation_instructions(self, scaffolder):
        """Verify wrappers include 5-step activation."""
        plan = ScaffoldPlan()
        agent_src = scaffolder._target / "test-agent.md"
        agent_src.write_text("---\n\n---\n# Test\n")
        plan.copies.append(FileCopy(
            src=agent_src,
            dst=scaffolder._target / "_grimoire" / "_config" / "custom" / "agents" / "test-agent.md",
            label="test/test-agent"
        ))
        
        scaffolder._plan_agent_wrappers(plan)
        
        wrappers = [
            t for t in plan.templates 
            if ".agent.md" in str(t.dst)
        ]
        assert len(wrappers) > 0
        
        wrapper = wrappers[0]
        # Check for activation steps
        assert "Load the full agent definition" in wrapper.content
        assert "Load project context" in wrapper.content
        assert "Load memory config" in wrapper.content
        assert "Follow ALL activation steps" in wrapper.content


class TestAddToFrameworkPath:
    """Verify the _framework attribute is set correctly."""

    def test_scaffolder_has_framework_path(self, scaffolder):
        """Verify scaffolder can access framework files."""
        assert scaffolder._framework.is_dir()
        
        # Framework should contain copilot resources
        copilot_dir = scaffolder._framework / "copilot"
        assert copilot_dir.is_dir()

    def test_copilot_prompts_exist(self, scaffolder):
        """Verify copilot prompts are available."""
        prompts_dir = scaffolder._framework / "copilot" / "prompts"
        assert prompts_dir.is_dir()
        
        # Should have 7 prompts
        prompts = list(prompts_dir.glob("*.prompt.md"))
        assert len(prompts) == 7

    def test_copilot_instructions_exist(self, scaffolder):
        """Verify copilot instructions are available."""
        instructions_dir = scaffolder._framework / "copilot" / "instructions"
        assert instructions_dir.is_dir()
        
        # Should have at least 1 instruction file
        instructions = list(instructions_dir.glob("*.instructions.md"))
        assert len(instructions) >= 1


class TestFullScaffoldPlan:
    """Integration test: verify complete plan includes Copilot resources."""

    def test_full_plan_includes_copilot_directories(self, scaffolder):
        """Verify a complete plan has Copilot dirs."""
        plan = scaffolder.plan()
        
        dir_strs = [str(d) for d in plan.directories]
        assert any(".github/prompts" in d for d in dir_strs)
        assert any(".github/instructions" in d for d in dir_strs)

    def test_full_plan_includes_copilot_files(self, scaffolder):
        """Verify complete plan has prompts and instructions."""
        plan = scaffolder.plan()
        
        # Should have prompts
        prompts = [
            c for c in plan.copies 
            if ".github/prompts" in str(c.dst)
        ]
        assert len(prompts) == 7
        
        # Should have instructions
        instructions = [
            t for t in plan.templates 
            if ".github/instructions" in str(t.dst)
        ]
        assert len(instructions) >= 1

    def test_full_plan_respects_overwrite_protection(self, scaffolder, temp_project):
        """Verify plan respects existing user files."""
        # Create existing prompt and instruction
        prompts_dir = temp_project / ".github" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "grimoire-session-bootstrap.prompt.md").write_text("# Custom\n")
        
        instructions_dir = temp_project / ".github" / "instructions"
        instructions_dir.mkdir(parents=True)
        (instructions_dir / "grimoire-project.instructions.md").write_text("# Custom\n")
        
        plan = scaffolder.plan()
        
        # should not overwrite bootstrap
        bootstrap = [
            c for c in plan.copies 
            if "session-bootstrap" in str(c.dst)
        ]
        assert len(bootstrap) == 0
        
        # Should not overwrite grimoire-project instruction
        grimoire_project = [
            t for t in plan.templates 
            if "grimoire-project" in str(t.dst)
        ]
        assert len(grimoire_project) == 0


class TestTemplateVariableSubstitution:
    """Test that template variables are correctly substituted."""

    def test_project_name_substitution(self, scaffolder):
        """Verify project name is substituted in templates."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        for template in plan.templates:
            assert "test-project" in template.content

    def test_language_substitution(self, scaffolder):
        """Verify language is substituted in templates."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        for template in plan.templates:
            assert "English" in template.content

    def test_no_unreplaced_placeholders(self, scaffolder):
        """Verify no {{placeholder}} markers remain."""
        plan = ScaffoldPlan()
        scaffolder._plan_copilot_instruction_files(plan)
        
        for template in plan.templates:
            assert "{{project_name}}" not in template.content
            assert "{{language}}" not in template.content
