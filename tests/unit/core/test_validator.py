"""Tests for grimoire.core.validator — config schema validation."""

from __future__ import annotations

from grimoire.core.validator import ValidationError, validate_config


def _minimal() -> dict:
    return {"project": {"name": "test"}}


class TestRootValidation:
    def test_valid_minimal(self) -> None:
        assert validate_config(_minimal()) == []

    def test_not_a_dict(self) -> None:
        errs = validate_config("string")
        assert len(errs) == 1
        assert "mapping" in errs[0].message

    def test_none(self) -> None:
        errs = validate_config(None)
        assert len(errs) == 1

    def test_missing_project(self) -> None:
        errs = validate_config({"user": {"name": "x"}})
        assert any("project" in e.path for e in errs)


class TestProjectSection:
    def test_project_not_dict(self) -> None:
        errs = validate_config({"project": "bad"})
        assert any("project" in e.path and "mapping" in e.message for e in errs)

    def test_missing_name(self) -> None:
        errs = validate_config({"project": {"type": "webapp"}})
        assert any("project.name" in e.path for e in errs)

    def test_empty_name(self) -> None:
        errs = validate_config({"project": {"name": ""}})
        assert any("project.name" in e.path for e in errs)

    def test_invalid_type(self) -> None:
        data = {"project": {"name": "x", "type": "notatype"}}
        errs = validate_config(data)
        assert any("project.type" in e.path for e in errs)

    def test_valid_type(self) -> None:
        data = {"project": {"name": "x", "type": "api"}}
        assert validate_config(data) == []

    def test_stack_not_list(self) -> None:
        data = {"project": {"name": "x", "stack": "python"}}
        errs = validate_config(data)
        assert any("project.stack" in e.path for e in errs)

    def test_stack_non_string_entries(self) -> None:
        data = {"project": {"name": "x", "stack": ["python", 42]}}
        errs = validate_config(data)
        assert any("project.stack" in e.path for e in errs)

    def test_valid_stack(self) -> None:
        data = {"project": {"name": "x", "stack": ["python", "docker"]}}
        assert validate_config(data) == []

    def test_repos_not_list(self) -> None:
        data = {"project": {"name": "x", "repos": "bad"}}
        errs = validate_config(data)
        assert any("project.repos" in e.path for e in errs)

    def test_repo_missing_name(self) -> None:
        data = {"project": {"name": "x", "repos": [{"path": "."}]}}
        errs = validate_config(data)
        assert any("repos[0].name" in e.path for e in errs)

    def test_repo_not_dict(self) -> None:
        data = {"project": {"name": "x", "repos": ["bad"]}}
        errs = validate_config(data)
        assert any("repos[0]" in e.path for e in errs)


class TestUserSection:
    def test_user_not_dict(self) -> None:
        data = {**_minimal(), "user": "bad"}
        errs = validate_config(data)
        assert any("user" in e.path for e in errs)

    def test_invalid_skill_level(self) -> None:
        data = {**_minimal(), "user": {"skill_level": "genius"}}
        errs = validate_config(data)
        assert any("user.skill_level" in e.path for e in errs)

    def test_valid_skill_level(self) -> None:
        data = {**_minimal(), "user": {"skill_level": "expert"}}
        assert validate_config(data) == []


class TestMemorySection:
    def test_memory_not_dict(self) -> None:
        data = {**_minimal(), "memory": "bad"}
        errs = validate_config(data)
        assert any("memory" in e.path for e in errs)

    def test_invalid_backend(self) -> None:
        data = {**_minimal(), "memory": {"backend": "redis"}}
        errs = validate_config(data)
        assert any("memory.backend" in e.path for e in errs)

    def test_valid_backend(self) -> None:
        data = {**_minimal(), "memory": {"backend": "qdrant-local"}}
        assert validate_config(data) == []


class TestAgentsSection:
    def test_agents_not_dict(self) -> None:
        data = {**_minimal(), "agents": "bad"}
        errs = validate_config(data)
        assert any("agents" in e.path for e in errs)

    def test_unknown_archetype(self) -> None:
        data = {**_minimal(), "agents": {"archetype": "mega-stack"}}
        errs = validate_config(data)
        assert any("agents.archetype" in e.path for e in errs)

    def test_valid_archetype(self) -> None:
        data = {**_minimal(), "agents": {"archetype": "web-app"}}
        assert validate_config(data) == []

    def test_custom_agents_not_list(self) -> None:
        data = {**_minimal(), "agents": {"custom_agents": "bob"}}
        errs = validate_config(data)
        assert any("agents.custom_agents" in e.path for e in errs)

    def test_custom_agent_not_string(self) -> None:
        data = {**_minimal(), "agents": {"custom_agents": [42]}}
        errs = validate_config(data)
        assert any("custom_agents[0]" in e.path for e in errs)

    def test_duplicate_custom_agent(self) -> None:
        data = {**_minimal(), "agents": {"custom_agents": ["a", "a"]}}
        errs = validate_config(data)
        assert any("Duplicate" in e.message for e in errs)


class TestInstalledArchetypes:
    def test_not_list(self) -> None:
        data = {**_minimal(), "installed_archetypes": "minimal"}
        errs = validate_config(data)
        assert any("installed_archetypes" in e.path for e in errs)

    def test_valid_list(self) -> None:
        data = {**_minimal(), "installed_archetypes": ["minimal", "web-app"]}
        assert validate_config(data) == []


class TestValidationErrorStr:
    def test_without_suggestion(self) -> None:
        e = ValidationError(path="p", message="msg")
        assert str(e) == "[p] msg"

    def test_with_suggestion(self) -> None:
        e = ValidationError(path="p", message="msg", suggestion="fix it")
        assert "→ fix it" in str(e)


class TestFullConfig:
    def test_full_valid_config(self) -> None:
        data = {
            "project": {
                "name": "my-app",
                "type": "webapp",
                "stack": ["python", "docker"],
                "repos": [{"name": "my-app", "path": "."}],
            },
            "user": {
                "name": "Guilhem",
                "language": "Français",
                "skill_level": "expert",
            },
            "memory": {"backend": "local"},
            "agents": {
                "archetype": "minimal",
                "custom_agents": ["my-agent"],
            },
            "installed_archetypes": ["minimal"],
        }
        assert validate_config(data) == []

    def test_multiple_errors(self) -> None:
        data = {
            "project": {"type": "badtype"},  # missing name + bad type
            "user": {"skill_level": "god"},
            "memory": {"backend": "redis"},
        }
        errs = validate_config(data)
        assert len(errs) >= 4  # name + type + skill + backend
