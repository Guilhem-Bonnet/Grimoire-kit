"""A project declared without egress must be scaffolded in lexical mode.

Offering a vector store to a machine that cannot reach an embedding model
produces a collection that can never be filled — the generated config has to
say so from the start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder


def _scaffolder(
    tmp_path: Path, *, offline: bool, backend: str = "auto", profile: str = ""
) -> ProjectScaffolder:
    return ProjectScaffolder(
        tmp_path,
        project_name="Demo",
        user_name="Guilhem",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=ResolvedArchetype(
            archetype="minimal",
            stack_agents=(),
            feature_agents=(),
            reason="test",
        ),
        backend=backend,
        offline=offline,
        profile=profile,
    )


def test_offline_project_declares_lexical_retrieval(tmp_path: Path) -> None:
    memory = _scaffolder(tmp_path, offline=True)._tpl_vars()["memory_layers"]

    assert "vector_database: false" in memory
    assert 'retrieval_mode: "lexical"' in memory


def test_connected_project_composes_vector_and_lexical(tmp_path: Path) -> None:
    """A project that can reach a model gets both rankings, fused.

    The declared mode used to be ``vector``, which described only half of what
    was installed: the lexical companion was built and then never queried.
    """
    memory = _scaffolder(tmp_path, offline=False)._tpl_vars()["memory_layers"]

    assert "vector_database: true" in memory
    assert 'retrieval_mode: "hybrid"' in memory


def test_offline_defaults_to_false(tmp_path: Path) -> None:
    """The flag is opt-in: nothing changes for callers that ignore it."""
    scaffolder = ProjectScaffolder(
        tmp_path,
        project_name="Demo",
        user_name="Guilhem",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=ResolvedArchetype(archetype="minimal", stack_agents=(), feature_agents=(), reason="test"),
        backend="auto",
    )

    assert 'retrieval_mode: "hybrid"' in scaffolder._tpl_vars()["memory_layers"]


def test_offline_leaves_the_rest_of_the_block_intact(tmp_path: Path) -> None:
    memory = _scaffolder(tmp_path, offline=True)._tpl_vars()["memory_layers"]

    assert 'layer_profile: "lexical"' in memory
    assert 'short_term_backend: "sqlite"' in memory
    assert 'knowledge_graph: "sqlite-sidecar"' in memory
    assert 'visualization: "runtime-dashboard"' in memory


@pytest.mark.parametrize("backend", ["auto", "qdrant-local", "ollama"])
def test_offline_wins_over_the_detected_backend(tmp_path: Path, backend: str) -> None:
    memory = _scaffolder(tmp_path, offline=True, backend=backend)._tpl_vars()["memory_layers"]
    assert 'retrieval_mode: "lexical"' in memory


def test_a_reachable_weaviate_keeps_its_vector_profile(tmp_path: Path) -> None:
    """`offline` means "no egress *and* no vector service" — the wizard only
    sets it when nothing answers on localhost. A site running its own Weaviate
    keeps semantic retrieval and gets its model through a bundle instead.
    """
    memory = _scaffolder(tmp_path, offline=True, backend="weaviate-server")._tpl_vars()["memory_layers"]

    assert 'layer_profile: "graphe"' in memory
    assert "vector_database: true" in memory
