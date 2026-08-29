"""L'étape mémoire du wizard : une composition, et seulement celles qui tiennent.

Le setup demandait un backend. Il demande maintenant une composition, et ne
propose que celles que la machine peut réellement servir : offrir un profil
qu'on ne pourra pas remplir est pire que d'en proposer un plus petit qui marche.
"""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.cli import cmd_init
from grimoire.memory import profiles


@pytest.fixture()
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pilote les prompts Rich et enregistre ce qui a été demandé."""
    state: dict[str, Any] = {"egress": True, "choice": None, "docker": False, "asked": [], "choices": None}

    def fake_confirm(prompt: str, **kwargs: Any) -> bool:
        state["asked"].append(prompt)
        return bool(state["egress"]) if "réseau sortant" in prompt else bool(state["docker"])

    def fake_prompt(prompt: str, **kwargs: Any) -> str:
        state["choices"] = kwargs.get("choices")
        return str(state["choice"] or kwargs.get("default"))

    monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(fake_confirm))
    monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(fake_prompt))
    return state


def _with_capabilities(monkeypatch: pytest.MonkeyPatch, *tokens: str) -> None:
    monkeypatch.setattr(
        cmd_init, "machine_capabilities", lambda *, has_egress: frozenset(tokens if has_egress else ())
    )


class TestChoiceIsAComposition:
    def test_the_default_is_the_recommended_composition(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)

        profile_id, backend, offline, qdrant = cmd_init._choose_memory_profile(
            "qdrant-local", offer_qdrant_docker=False
        )

        assert profile_id == profiles.DEFAULT_PROFILE
        # `standard` ne fixe pas de store : le backend détecté est conservé.
        assert backend == "qdrant-local"
        assert offline is False
        assert qdrant is False

    def test_a_composition_that_pins_its_store_wins_over_detection(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS, profiles.REQ_DOCKER)
        answers["choice"] = "3"  # graphe

        profile_id, backend, _, _ = cmd_init._choose_memory_profile("qdrant-local", offer_qdrant_docker=False)

        assert profile_id == "graphe"
        assert backend == "weaviate-server"


class TestOnlyServableCompositionsAreSelectable:
    def test_a_bare_machine_cannot_select_the_graph_profiles(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)

        cmd_init._choose_memory_profile("local", offer_qdrant_docker=False)

        # 1 = lexical, 2 = standard ; graphe et complet restent affichés mais
        # hors des choix acceptés.
        assert answers["choices"] == ["1", "2"]

    def test_docker_unlocks_the_graph_composition(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS, profiles.REQ_DOCKER)

        cmd_init._choose_memory_profile("local", offer_qdrant_docker=False)

        assert answers["choices"] == ["1", "2", "3"]


class TestEgress:
    def test_no_egress_leaves_only_the_lexical_composition(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sans réseau, aucun modèle d'embedding : tout le reste serait un
        store qu'on ne pourra jamais remplir."""
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS, profiles.REQ_DOCKER)
        answers["egress"] = False

        profile_id, _, offline, qdrant = cmd_init._choose_memory_profile("local", offer_qdrant_docker=True)

        assert answers["choices"] == ["1", "2"]
        assert profile_id in ("lexical", "standard")
        if profile_id == "lexical":
            assert offline is True
        assert qdrant is False

    def test_the_egress_question_is_only_asked_when_nothing_answers_locally(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)

        cmd_init._choose_memory_profile("qdrant-local", offer_qdrant_docker=False)

        assert not any("réseau sortant" in prompt for prompt in answers["asked"])


class TestQdrantContainer:
    def test_the_container_is_offered_only_for_the_unpinned_composition(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS, profiles.REQ_DOCKER)
        answers["choice"] = "3"  # graphe pins its own services

        cmd_init._choose_memory_profile("local", offer_qdrant_docker=True)

        assert not any("Qdrant" in prompt for prompt in answers["asked"])

    def test_accepting_the_container_switches_the_backend(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)
        answers["docker"] = True

        profile_id, backend, _, qdrant = cmd_init._choose_memory_profile("local", offer_qdrant_docker=True)

        assert profile_id == "standard"
        assert qdrant is True
        assert backend == "qdrant-server"

    def test_declining_the_container_leaves_the_backend_alone(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)
        answers["docker"] = False

        _, backend, _, qdrant = cmd_init._choose_memory_profile("local", offer_qdrant_docker=True)

        assert qdrant is False
        assert backend == "local"


class TestCapabilityProbing:
    def test_egress_is_answered_not_probed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cmd_init, "_is_docker_available", lambda: False)
        monkeypatch.setattr(cmd_init, "_is_redis_reachable", lambda: False)

        assert cmd_init.machine_capabilities(has_egress=True) == frozenset({profiles.REQ_EGRESS})
        assert cmd_init.machine_capabilities(has_egress=False) == frozenset()

    def test_reachable_services_become_capabilities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cmd_init, "_is_docker_available", lambda: True)
        monkeypatch.setattr(cmd_init, "_is_redis_reachable", lambda: True)

        assert cmd_init.machine_capabilities(has_egress=True) == frozenset(
            {profiles.REQ_EGRESS, profiles.REQ_DOCKER, profiles.REQ_REDIS}
        )

    def test_a_closed_port_is_not_a_capability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """La sonde Redis ne doit pas déclarer une couche chaude absente."""
        assert cmd_init._is_redis_reachable(port=1) is False
