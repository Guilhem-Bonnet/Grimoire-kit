"""Memory compositions are chosen as one unit — and must stay loadable.

The setup used to ask which single backend to use, so the other six Memory OS
layers kept their defaults in every project ever generated.  These tests pin
the two things that make a profile more than a label: it emits a block the
config parser accepts, and it narrows itself to what the backend can back.
"""

from __future__ import annotations

import pytest

from grimoire.core.config import GrimoireConfig, MemoryConfig
from grimoire.core.validator import _KNOWN_MEMORY_KEYS
from grimoire.memory import profiles


def _memory_section(profile: profiles.MemoryProfile, backend: str) -> dict[str, object]:
    """Parse a profile's emitted YAML block back into a memory mapping."""
    section: dict[str, object] = {"backend": backend}
    lines = profile.layers_block().splitlines()
    lines += [line for line in profile.connection_block(backend).splitlines() if line.strip()]
    for line in lines:
        key, _, raw = line.strip().partition(": ")
        value = raw.strip()
        if value in ("true", "false"):
            section[key] = value == "true"
        else:
            section[key] = value.strip('"')
    return section


class TestEmittedBlock:
    @pytest.mark.parametrize("profile", profiles.ordered(), ids=lambda p: p.id)
    def test_every_profile_parses_as_a_memory_config(self, profile: profiles.MemoryProfile) -> None:
        section = _memory_section(profile, profile.resolve_backend("auto"))

        config = MemoryConfig.from_dict(section)

        assert config.layer_profile == profile.id
        assert config.retrieval_mode == profile.retrieval_mode
        assert config.short_term_backend == profile.short_term_backend

    @pytest.mark.parametrize("profile", profiles.ordered(), ids=lambda p: p.id)
    def test_no_profile_emits_a_key_the_validator_rejects(self, profile: profiles.MemoryProfile) -> None:
        """A profile that emits an unknown key would warn on every doctor run."""
        known = set(_KNOWN_MEMORY_KEYS)
        emitted = set(_memory_section(profile, profile.resolve_backend("qdrant-server")))

        assert emitted <= known, f"unknown keys: {sorted(emitted - known)}"

    def test_layers_block_ends_with_a_newline(self) -> None:
        """The template substitutes it as a whole line block."""
        assert profiles.PROFILES["standard"].layers_block().endswith("\n")


class TestResolution:
    def test_the_legacy_id_still_resolves(self) -> None:
        """A project scaffolded before the rename keeps its composition."""
        assert profiles.resolve("weaviate-neo4j").id == "graphe"
        assert profiles.is_known("weaviate-neo4j")

    def test_an_unknown_id_falls_back_rather_than_raising(self) -> None:
        """A profile name is metadata: it must never break loading a memory."""
        assert profiles.resolve("profil-du-futur").id == profiles.DEFAULT_PROFILE
        assert not profiles.is_known("profil-du-futur")

    def test_empty_id_falls_back(self) -> None:
        assert profiles.resolve("").id == profiles.DEFAULT_PROFILE


class TestBackendNarrowing:
    def test_a_lexical_primary_cannot_claim_a_vector_layer(self) -> None:
        narrowed = profiles.PROFILES["standard"].for_backend("local")

        assert narrowed.vector_database is False
        assert narrowed.retrieval_mode == "lexical"

    def test_a_vector_primary_keeps_the_fusion(self) -> None:
        assert profiles.PROFILES["standard"].for_backend("qdrant-server").retrieval_mode == "hybrid"

    def test_a_semantic_primary_without_a_companion_declares_vector(self) -> None:
        """MemPalace searches semantically but carries no BM25 index to fuse."""
        assert profiles.PROFILES["standard"].for_backend("mempalace").retrieval_mode == "vector"

    def test_auto_keeps_the_declared_intent(self) -> None:
        """Resolution is deferred to runtime; narrowing here would guess."""
        assert profiles.PROFILES["standard"].for_backend("auto").retrieval_mode == "hybrid"

    def test_narrowing_never_widens(self) -> None:
        narrowed = profiles.PROFILES["lexical"].for_backend("qdrant-server")

        assert narrowed.vector_database is False
        assert narrowed.retrieval_mode == "lexical"


class TestFeasibility:
    def test_a_bare_machine_is_offered_the_two_local_compositions(self) -> None:
        offered = [profile.id for profile in profiles.feasible(frozenset())]

        assert offered == ["lexical", "standard"]

    def test_docker_and_egress_unlock_the_graph(self) -> None:
        available = frozenset({profiles.REQ_EGRESS, profiles.REQ_DOCKER})

        assert "graphe" in [profile.id for profile in profiles.feasible(available)]
        assert "complet" not in [profile.id for profile in profiles.feasible(available)]

    def test_unmet_names_what_is_missing(self) -> None:
        unmet = profiles.PROFILES["complet"].unmet(frozenset({profiles.REQ_DOCKER}))

        assert set(unmet) == {profiles.REQ_EGRESS, profiles.REQ_REDIS}


class TestInference:
    @pytest.mark.parametrize(
        ("backend", "offline", "expected"),
        [
            ("auto", False, "standard"),
            ("qdrant-local", False, "standard"),
            ("ollama", False, "standard"),
            ("auto", True, "lexical"),
            ("qdrant-local", True, "lexical"),
            # A site that runs its own Weaviate declared that stack on purpose;
            # it gets its embedding model from a bundle, not from the network.
            ("weaviate-server", True, "graphe"),
            ("weaviate-server", False, "graphe"),
        ],
    )
    def test_inferred_composition(self, backend: str, offline: bool, expected: str) -> None:
        assert profiles.infer(backend, offline=offline).id == expected


class TestConnectionSettings:
    """A store that needs a URL gets it whatever composition sits on top."""

    def test_a_detected_weaviate_gets_its_url_under_a_plain_standard(self) -> None:
        """Regression: `standard` on a detected Weaviate emitted no URL at all,
        so `GrimoireConfig.validate` flagged every project generated on a
        machine that happened to run one."""
        block = profiles.PROFILES["standard"].connection_block("weaviate-server")

        assert 'weaviate_url: "http://localhost:8080"' in block
        assert "neo4j_uri" not in block

    def test_a_graph_profile_adds_the_layer_settings_on_top(self) -> None:
        block = profiles.PROFILES["graphe"].connection_block("weaviate-server")

        assert 'weaviate_url: "http://localhost:8080"' in block
        assert 'neo4j_uri: "bolt://localhost:7687"' in block

    def test_the_store_setting_is_never_emitted_twice(self) -> None:
        block = profiles.PROFILES["graphe"].connection_block("weaviate-server")

        assert block.count("qdrant_url") == 1

    def test_a_local_store_needs_nothing(self) -> None:
        assert profiles.PROFILES["lexical"].connection_block("lexical") == ""


class TestGeneratedConfigLoads:
    def test_a_graph_profile_produces_a_loadable_project_config(self, tmp_path) -> None:
        """The end contract: what a profile emits, `grimoire` can read back."""
        profile = profiles.PROFILES["graphe"]
        layers = profile.layers_block()
        connection = profile.connection_block(profile.resolve_backend("auto"))
        path = tmp_path / "project-context.yaml"
        path.write_text(
            "project:\n  name: Demo\n\n"
            "user:\n  name: Guilhem\n\n"
            f'memory:\n  backend: "{profile.resolve_backend("auto")}"{connection}\n{layers}',
            encoding="utf-8",
        )

        config = GrimoireConfig.from_yaml(path)

        assert config.memory.layer_profile == "graphe"
        assert config.memory.knowledge_graph == "neo4j"
        assert config.memory.neo4j_uri == "bolt://localhost:7687"
        assert config.memory.retrieval_mode == "hybrid"
