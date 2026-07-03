"""Tests for grimoire.core.schema — JSON Schema generator."""

from __future__ import annotations

from grimoire.core.schema import generate_schema


class TestGenerateSchema:
    """Tests for ``generate_schema()``."""

    def test_returns_dict(self) -> None:
        schema = generate_schema()
        assert isinstance(schema, dict)

    def test_draft_version(self) -> None:
        schema = generate_schema()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_type_is_object(self) -> None:
        schema = generate_schema()
        assert schema["type"] == "object"

    def test_has_project_property(self) -> None:
        schema = generate_schema()
        assert "project" in schema["properties"]

    def test_has_user_property(self) -> None:
        schema = generate_schema()
        assert "user" in schema["properties"]

    def test_has_memory_property(self) -> None:
        schema = generate_schema()
        assert "memory" in schema["properties"]

    def test_has_agents_property(self) -> None:
        schema = generate_schema()
        assert "agents" in schema["properties"]

    def test_project_requires_name(self) -> None:
        schema = generate_schema()
        proj = schema["properties"]["project"]
        assert "name" in proj["required"]

    def test_project_type_enum(self) -> None:
        schema = generate_schema()
        proj = schema["properties"]["project"]
        type_prop = proj["properties"]["type"]
        assert "enum" in type_prop
        assert "webapp" in type_prop["enum"]
        assert "api" in type_prop["enum"]
        assert "framework" in type_prop["enum"]
        assert "meta" in type_prop["enum"]

    def test_memory_backend_enum(self) -> None:
        schema = generate_schema()
        mem = schema["properties"]["memory"]
        backend = mem["properties"]["backend"]
        assert "enum" in backend
        assert "auto" in backend["enum"]
        assert "weaviate-server" in backend["enum"]

    def test_memory_layers_are_described(self) -> None:
        schema = generate_schema()
        mem = schema["properties"]["memory"]
        for key in (
            "layer_profile",
            "short_term_backend",
            "redis_url",
            "weaviate_url",
            "weaviate_api_key_env",
            "weaviate_collection",
            "neo4j_uri",
            "neo4j_user",
            "neo4j_password_env",
            "neo4j_database",
            "migration_source_backend",
            "migration_target_backend",
            "migration_bundle_path",
            "knowledge_graph",
            "memory_graph",
            "code_graph",
            "task_memory",
            "visualization",
        ):
            assert key in mem["properties"]
        assert "redis" in mem["properties"]["short_term_backend"]["enum"]
        assert "neo4j" in mem["properties"]["memory_graph"]["enum"]

    def test_additional_properties_true(self) -> None:
        schema = generate_schema()
        assert schema.get("additionalProperties") is True

    def test_schema_is_deterministic(self) -> None:
        s1 = generate_schema()
        s2 = generate_schema()
        assert s1 == s2
