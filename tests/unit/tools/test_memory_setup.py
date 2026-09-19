"""Tests du plan de mise en place Memory OS (``grimoire memory up``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.tools import memory_setup as ms
from grimoire.tools.memory_setup import ServiceProbe, apply_memory_plan, build_memory_plan

#: Captured at import time, before the suite-wide autouse fixture in
#: ``conftest.py`` (``_default_no_docker_daemon``) patches the module
#: attribute to a fixed "no Docker" stub for every other test in the suite.
#: ``TestDockerDaemonReachable`` below tests the real implementation.
_REAL_DOCKER_DAEMON_REACHABLE = ms.docker_daemon_reachable

# ── Helpers ───────────────────────────────────────────────────────────────────


def _probe(sid: str, *, reachable: bool = True, installed: bool = True) -> ServiceProbe:
    extra = ms._EXTRA_MODULES[sid][0]
    return ServiceProbe(
        id=sid,
        url=ms._DEFAULT_URLS[sid],
        reachable=reachable,
        extra=extra,
        extra_installed=installed,
    )


def _all(**overrides: ServiceProbe) -> dict[str, ServiceProbe]:
    """Toutes les sondes utilisables, sauf celles explicitement remplacées."""
    probes = {sid: _probe(sid) for sid in ms._EXTRA_MODULES}
    probes.update(overrides)
    return probes


def _none() -> dict[str, ServiceProbe]:
    return {sid: _probe(sid, reachable=False, installed=False) for sid in ms._EXTRA_MODULES}


def _write_config(root: Path, body: str = '  backend: "auto"\n  collection_prefix: "grimoire"\n') -> Path:
    path = root / "project-context.yaml"
    path.write_text('project:\n  name: "Mon Super Projet"\n\nmemory:\n' + body, encoding="utf-8")
    return path


def _keys(plan: ms.MemoryPlan) -> set[str]:
    return {c["key"] for c in plan.changes}


# ── Profils ───────────────────────────────────────────────────────────────────


class TestProfiles:
    def test_unknown_profile_is_rejected(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        with pytest.raises(ValueError, match="Unknown memory profile"):
            build_memory_plan(tmp_path, profile="turbo", services=_all())

    def test_lexical_needs_no_service(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        assert plan.config["backend"] == "lexical"
        assert plan.config["vector_database"] is False
        assert plan.config["retrieval_mode"] == "lexical"
        # Aucune couche serveur ne doit apparaître.
        assert not {"neo4j_uri", "weaviate_url", "redis_url"} & _keys(plan)

    def test_vector_stops_before_the_graph(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="vector", services=_all())
        assert plan.config["backend"] == "weaviate-server"
        assert "neo4j_uri" not in plan.config
        assert "redis_url" not in plan.config

    def test_full_wires_every_layer(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        cfg = plan.config
        assert cfg["backend"] == "weaviate-server"
        assert cfg["neo4j_uri"] == "bolt://localhost:7687"
        assert cfg["knowledge_graph"] == cfg["memory_graph"] == "neo4j"
        assert cfg["code_graph"] == cfg["task_memory"] == "neo4j"
        assert cfg["short_term_backend"] == "redis"
        assert plan.warnings == []


# ── Règle centrale : on n'active que ce qui répond ────────────────────────────


class TestOnlyEnableWhatAnswers:
    def test_unreachable_neo4j_is_not_written(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="full", services=_all(neo4j=_probe("neo4j", reachable=False))
        )
        assert "neo4j_uri" not in plan.config
        assert plan.config.get("memory_graph") != "neo4j"
        assert any("Neo4j indisponible" in w for w in plan.warnings)

    def test_unreachable_service_warning_names_the_start_command(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="full", services=_all(neo4j=_probe("neo4j", reachable=False))
        )
        assert any("docker compose" in w for w in plan.warnings)

    def test_reachable_but_missing_extra_is_not_enabled(self, tmp_path: Path) -> None:
        """Service en ligne mais extra pip absent : le remède est différent."""
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="full", services=_all(neo4j=_probe("neo4j", installed=False))
        )
        assert "neo4j_uri" not in plan.config
        assert any('grimoire-kit[neo4j]' in w for w in plan.warnings)
        assert not any("injoignable" in w for w in plan.warnings if "neo4j" in w)

    def test_missing_redis_leaves_short_term_on_sqlite(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="full", services=_all(redis=_probe("redis", reachable=False))
        )
        assert "short_term_backend" not in plan.config
        assert any("Redis indisponible" in w for w in plan.warnings)

    def test_qdrant_takes_over_when_weaviate_is_down(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="full", services=_all(weaviate=_probe("weaviate", reachable=False))
        )
        assert plan.config["backend"] == "qdrant-server"
        assert plan.config["qdrant_url"] == "http://localhost:6333"

    def test_no_vector_backend_falls_back_to_lexical(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="full", services=_none())
        assert plan.config["backend"] == "lexical"
        assert any("Aucun backend vectoriel" in w for w in plan.warnings)
        # Rien de serveur n'est écrit quand rien ne répond.
        assert not {"neo4j_uri", "weaviate_url", "redis_url", "qdrant_url"} & set(plan.config)


# ── #527 — un seul vocabulaire, layer_profile/retrieval_mode cohérents ───────


class TestCanonicalVocabularyAndLayerFields:
    """CLI (lexical|vector|full) et schéma (lexical|standard|graphe|complet)
    parlaient deux langues différentes ; `up` n'écrivait ni `layer_profile` ni
    un `retrieval_mode` fidèle à ce qui tournait réellement (#527)."""

    @pytest.mark.parametrize(
        ("legacy", "canonical"), [("vector", "standard"), ("full", "complet")],
    )
    def test_legacy_names_resolve_to_the_canonical_profile(
        self, tmp_path: Path, legacy: str, canonical: str,
    ) -> None:
        _write_config(tmp_path)
        legacy_plan = build_memory_plan(tmp_path, profile=legacy, services=_all())
        canonical_plan = build_memory_plan(tmp_path, profile=canonical, services=_all())

        assert legacy_plan.profile == canonical
        assert legacy_plan.config == canonical_plan.config

    def test_lexical_profile_writes_matching_layer_fields(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        assert plan.config["layer_profile"] == "lexical"
        assert plan.config["retrieval_mode"] == "lexical"
        assert plan.config["vector_database"] is False

    def test_standard_profile_writes_matching_layer_fields(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="standard", services=_all())
        assert plan.config["layer_profile"] == "standard"
        assert plan.config["retrieval_mode"] == "hybrid"
        assert plan.config["vector_database"] is True

    def test_graphe_profile_writes_matching_layer_fields(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="graphe", services=_all(redis=_probe("redis", reachable=False)),
        )
        assert plan.config["layer_profile"] == "graphe"
        assert plan.config["retrieval_mode"] == "hybrid"

    def test_complet_profile_writes_matching_layer_fields(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="complet", services=_all())
        assert plan.config["layer_profile"] == "complet"
        assert plan.config["retrieval_mode"] == "hybrid"

    def test_requested_profile_never_outruns_what_is_actually_served(self, tmp_path: Path) -> None:
        """`complet` sans aucun service qui répond ne doit jamais écrire
        `layer_profile: complet` — la composition écrite doit toujours être
        celle réellement servie, jamais celle demandée (#527)."""
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="complet", services=_none())
        assert plan.config["backend"] == "lexical"
        assert plan.config["layer_profile"] == "lexical"
        assert plan.config["retrieval_mode"] == "lexical"

    def test_complet_without_neo4j_downgrades_layer_profile_to_standard(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="complet", services=_all(neo4j=_probe("neo4j", reachable=False)),
        )
        assert plan.config["backend"] == "weaviate-server"
        assert plan.config["layer_profile"] == "standard"

    def test_complet_without_redis_downgrades_layer_profile_to_graphe(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path, profile="complet", services=_all(redis=_probe("redis", reachable=False)),
        )
        assert plan.config["layer_profile"] == "graphe"

    def test_complet_with_redis_notes_the_project_namespace(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '  backend: "auto"\n  collection_prefix: "mon_super_projet"\n')
        plan = build_memory_plan(tmp_path, profile="complet", services=_all())
        assert any("mon_super_projet" in note for note in plan.notes)

    def test_lexical_never_notes_a_redis_namespace(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        assert plan.notes == []


# ── Diff contre le fichier, pas contre les valeurs par défaut ─────────────────


class TestDiffAgainstFile:
    def test_absent_key_is_a_change_even_at_default_value(self, tmp_path: Path) -> None:
        """``neo4j_password_env`` vaut déjà le défaut, mais rien ne le dit à l'opérateur."""
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        change = next(c for c in plan.changes if c["key"] == "neo4j_password_env")
        assert change["new"] == "GRIMOIRE_NEO4J_PASSWORD"
        assert change["absent"] is True

    def test_key_present_and_equal_is_not_a_change(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '  backend: "auto"\n  collection_prefix: "mon_super_projet"\n')
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        assert "collection_prefix" not in _keys(plan)

    def test_key_present_and_different_is_a_modification(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        change = next(c for c in plan.changes if c["key"] == "backend")
        assert change["old"] == "auto"
        assert change["absent"] is False


# ── Nommage des collections ───────────────────────────────────────────────────


class TestNaming:
    def test_prefix_derived_from_project_name(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="vector", services=_all())
        assert plan.config["collection_prefix"] == "mon_super_projet"
        assert plan.config["weaviate_collection"] == "MonSuperProjetMemory"

    def test_custom_prefix_is_preserved(self, tmp_path: Path) -> None:
        """Un préfixe déjà choisi ne doit pas être écrasé : il isole le projet."""
        _write_config(tmp_path, '  backend: "auto"\n  collection_prefix: "equipe_alpha"\n')
        plan = build_memory_plan(tmp_path, profile="vector", services=_all())
        assert plan.config["collection_prefix"] == "equipe_alpha"
        assert "collection_prefix" not in _keys(plan)


# ── Étapes suivantes ──────────────────────────────────────────────────────────


class TestNextSteps:
    def test_password_export_is_listed_when_neo4j_is_wired(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        assert any("export GRIMOIRE_NEO4J_PASSWORD" in s for s in plan.next_steps)
        assert any("memory gate" in s for s in plan.next_steps)

    def test_missing_extras_are_grouped_in_one_pip_command(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(
            tmp_path,
            profile="full",
            services=_all(
                neo4j=_probe("neo4j", installed=False),
                redis=_probe("redis", installed=False),
            ),
        )
        pip = next(s for s in plan.next_steps if s.startswith("pip install"))
        assert "neo4j" in pip
        assert "redis" in pip

    def test_lexical_profile_needs_no_follow_up(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="lexical", services=_none())
        assert plan.next_steps == []


# ── Absence de config ─────────────────────────────────────────────────────────


class TestUninitializedProject:
    def test_no_config_yields_a_warning_and_no_change(self, tmp_path: Path) -> None:
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        assert plan.changes == []
        assert any("grimoire init" in w for w in plan.warnings)

    def test_broken_config_is_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(":: not yaml ::", encoding="utf-8")
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        assert plan.changes == []
        assert plan.warnings


# ── Écriture ──────────────────────────────────────────────────────────────────


class TestApply:
    def test_writes_keys_and_preserves_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "project-context.yaml"
        path.write_text(
            "# Mon projet\n"
            'project:\n  name: "Mon Super Projet"   # nom affiché\n\n'
            "memory:\n  # backend résolu au démarrage\n"
            '  backend: "auto"\n  collection_prefix: "grimoire"\n',
            encoding="utf-8",
        )
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        written = apply_memory_plan(plan)

        text = path.read_text(encoding="utf-8")
        assert "# Mon projet" in text
        assert "# nom affiché" in text
        assert "# backend résolu au démarrage" in text
        assert "neo4j_uri" in written
        assert "weaviate-server" in text

    def test_apply_is_idempotent(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        apply_memory_plan(build_memory_plan(tmp_path, profile="full", services=_all()))
        second = build_memory_plan(tmp_path, profile="full", services=_all())
        assert second.changes == []
        assert apply_memory_plan(second) == []

    def test_result_reloads_as_a_valid_config(self, tmp_path: Path) -> None:
        """Le fichier écrit doit repasser la validation, sinon on a cassé le projet."""
        from grimoire.core.config import GrimoireConfig

        _write_config(tmp_path)
        apply_memory_plan(build_memory_plan(tmp_path, profile="full", services=_all()))
        cfg = GrimoireConfig.from_yaml(tmp_path / "project-context.yaml")
        assert cfg.memory.backend == "weaviate-server"
        assert cfg.memory.memory_graph == "neo4j"
        assert cfg.memory.short_term_backend == "redis"

    def test_nothing_written_without_changes(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        plan = build_memory_plan(tmp_path, profile="full", services=_all())
        plan.changes = []
        before = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
        assert apply_memory_plan(plan) == []
        assert (tmp_path / "project-context.yaml").read_text(encoding="utf-8") == before

    def test_missing_memory_section_is_created(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "Sans Memoire"\n', encoding="utf-8"
        )
        plan = build_memory_plan(tmp_path, profile="vector", services=_all())
        apply_memory_plan(plan)
        text = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
        assert "memory:" in text
        assert "weaviate-server" in text


# ── Sondes ────────────────────────────────────────────────────────────────────


class TestProbeServices:
    def test_probes_every_known_service(self) -> None:
        probes = ms.probe_services()
        assert set(probes) == set(ms._EXTRA_MODULES)

    def test_custom_urls_are_honoured(self) -> None:
        probes = ms.probe_services({"neo4j": "bolt://graph.internal:7999"})
        assert probes["neo4j"].url == "bolt://graph.internal:7999"

    def test_either_embedding_engine_satisfies_the_vector_extra(self) -> None:
        """fastembed OU sentence-transformers : tester un seul serait un faux négatif.

        Les extras `[qdrant]` et `[weaviate]` tirent fastembed ;
        sentence-transformers reste un repli utilisé s'il est déjà installé.
        N'exiger que le second ferait déclarer l'extra absent sur une
        installation valide, et `memory up` retomberait en lexical.
        """
        engines = ms._EXTRA_MODULES["weaviate"][1]
        assert "fastembed" in engines
        assert "sentence_transformers" in engines

    def test_module_installed_accepts_any_candidate(self) -> None:
        # `json` est toujours importable, `paquet_absent_xyz` jamais.
        assert ms._module_installed(("paquet_absent_xyz", "json")) is True
        assert ms._module_installed(("json",)) is True
        assert ms._module_installed(("paquet_absent_xyz",)) is False
        assert ms._module_installed(()) is False

    def test_usable_requires_both_service_and_extra(self) -> None:
        assert _probe("neo4j").usable is True
        assert _probe("neo4j", reachable=False).usable is False
        assert _probe("neo4j", installed=False).usable is False


# ── Repli local (issue Grimoire-kit#616, PR2 : "standard" sans docker) ────────


class TestStandardFallsBackToEmbeddedQdrant:
    """Sans serveur vectoriel joignable, `standard` ne doit plus retomber en
    `lexical` quand la machine peut embarquer un Qdrant local — c'est le
    profil "vecteurs locaux" de la décision du 2026-09-18."""

    def test_no_server_but_local_embedding_uses_embedded_qdrant(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        services = _all(
            weaviate=_probe("weaviate", reachable=False),
            qdrant=_probe("qdrant", reachable=False),
        )
        plan = build_memory_plan(tmp_path, profile="standard", services=services)
        assert plan.config["backend"] == "qdrant-local"
        assert plan.config["layer_profile"] == "standard"
        assert plan.config["retrieval_mode"] == "hybrid"
        assert any("embarqué" in note for note in plan.notes)

    def test_no_server_and_no_embedding_capacity_falls_back_to_lexical(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        services = _none()  # extra_installed False everywhere, including qdrant_client
        plan = build_memory_plan(tmp_path, profile="standard", services=services)
        assert plan.config["backend"] == "lexical"
        assert any("aucune capacité d'embedding locale" in w for w in plan.warnings)

    def test_qdrant_client_present_but_no_embedding_engine_falls_back_to_lexical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "local_embedding_available", lambda *a, **k: False)
        _write_config(tmp_path)
        services = _all(
            weaviate=_probe("weaviate", reachable=False),
            qdrant=_probe("qdrant", reachable=False, installed=True),
        )
        plan = build_memory_plan(tmp_path, profile="standard", services=services)
        assert plan.config["backend"] == "lexical"


class TestDockerDaemonReachable:
    @pytest.fixture(autouse=True)
    def _use_real_implementation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Overrides the suite-wide "no Docker" default (``conftest.py``) —
        this class tests the real probe, not the deterministic stub every
        other test relies on."""
        monkeypatch.setattr(ms, "docker_daemon_reachable", _REAL_DOCKER_DAEMON_REACHABLE)

    def test_no_docker_binary_reads_as_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.shutil, "which", lambda name: None)
        assert ms.docker_daemon_reachable() is False

    def test_binary_present_but_daemon_down_reads_as_unreachable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms.shutil, "which", lambda name: "/usr/bin/docker")

        class _Result:
            returncode = 1

        monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: _Result())
        assert ms.docker_daemon_reachable() is False

    def test_binary_and_daemon_both_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.shutil, "which", lambda name: "/usr/bin/docker")

        class _Result:
            returncode = 0

        monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: _Result())
        assert ms.docker_daemon_reachable() is True

    def test_a_timeout_reads_as_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.shutil, "which", lambda name: "/usr/bin/docker")

        def _raise(*a: object, **k: object) -> None:
            raise subprocess.TimeoutExpired(cmd="docker", timeout=2.0)

        monkeypatch.setattr(ms.subprocess, "run", _raise)
        assert ms.docker_daemon_reachable() is False


class TestLocalEmbeddingAvailable:
    def test_fastembed_installed_is_sufficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "_module_installed", lambda names: "fastembed" in names)
        assert ms.local_embedding_available() is True

    def test_neither_engine_but_ollama_usable_is_sufficient(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "_module_installed", lambda names: False)
        probes = _all(ollama=_probe("ollama"))
        assert ms.local_embedding_available(probes) is True

    def test_nothing_available_reads_as_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "_module_installed", lambda names: False)
        assert ms.local_embedding_available(_none()) is False


class TestLocalEmbeddingAvailableProbeCost:
    """#619 review: called with no ``probes`` (the ``doctor``/``status`` path,
    via ``memory_upgrade_target``), this must check Ollama alone — never
    ``probe_services()``'s full sweep of the other four memory services."""

    def test_without_probes_never_calls_probe_services(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "_module_installed", lambda names: False)

        def _boom(*a: object, **k: object) -> dict[str, ServiceProbe]:
            raise AssertionError("local_embedding_available() probed every service")

        monkeypatch.setattr(ms, "probe_services", _boom)
        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: False)

        assert ms.local_embedding_available() is False

    def test_without_probes_still_finds_a_usable_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "_module_installed", lambda names: names == ("ollama",))
        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: True)

        def _boom(*a: object, **k: object) -> dict[str, ServiceProbe]:
            raise AssertionError("local_embedding_available() probed every service")

        monkeypatch.setattr(ms, "probe_services", _boom)

        assert ms.local_embedding_available() is True

    def test_without_probes_ollama_module_missing_reads_as_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Server reachable but the python `ollama` extra not installed: not
        # usable, matching the pre-#619 `ServiceProbe.usable` semantics.
        monkeypatch.setattr(ms, "_module_installed", lambda names: False)
        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: True)
        assert ms.local_embedding_available() is False


class TestRecommendProfile:
    def test_no_embedding_capacity_recommends_lexical_regardless_of_docker(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "local_embedding_available", lambda *a, **k: False)
        profile_id, reason = ms.recommend_profile(_none(), docker_ready=True)
        assert profile_id == "lexical"
        assert "no local embedding capability" in reason

    def test_embedding_capacity_and_docker_recommends_complet(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "local_embedding_available", lambda *a, **k: True)
        profile_id, reason = ms.recommend_profile(_all(), docker_ready=True)
        assert profile_id == "complet"
        assert "Docker" in reason

    def test_embedding_capacity_without_docker_recommends_standard(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "local_embedding_available", lambda *a, **k: True)
        profile_id, _reason = ms.recommend_profile(_all(), docker_ready=False)
        assert profile_id == "standard"


class TestUnreachedConfiguredServices:
    """The gap `grimoire doctor` reports as 'pile mémoire non démarrée' —
    a project scaffolded for `complet`/`graphe` before its containers were
    ever started (2026-09-18 arbitrage: `-y` starts them, a bare non-TTY
    script run does not, but the config is written either way)."""

    def test_lexical_project_has_nothing_missing(self) -> None:
        from grimoire.core.config import MemoryConfig

        memory = MemoryConfig(backend="lexical")
        assert ms.unreached_configured_services(memory) == []

    def test_weaviate_backend_unreachable_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.core.config import MemoryConfig

        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: False)
        memory = MemoryConfig(backend="weaviate-server", weaviate_url="http://localhost:8080")
        assert "weaviate" in ms.unreached_configured_services(memory)

    def test_weaviate_backend_reachable_is_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.core.config import MemoryConfig

        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: True)
        memory = MemoryConfig(backend="weaviate-server", weaviate_url="http://localhost:8080")
        assert ms.unreached_configured_services(memory) == []

    def test_complet_reports_every_unreachable_layer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.core.config import MemoryConfig

        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: False)
        memory = MemoryConfig(
            backend="weaviate-server",
            weaviate_url="http://localhost:8080",
            knowledge_graph="neo4j",
            neo4j_uri="bolt://localhost:7687",
            short_term_backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        missing = ms.unreached_configured_services(memory)
        assert set(missing) == {"weaviate", "neo4j", "redis"}

    def test_embedded_qdrant_local_is_never_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`qdrant-local` names no server to reach at all — nothing to flag."""
        from grimoire.core.config import MemoryConfig

        monkeypatch.setattr(ms, "_tcp_reachable", lambda *a, **k: False)
        memory = MemoryConfig(backend="qdrant-local")
        assert ms.unreached_configured_services(memory) == []


class TestStartMemoryStack:
    """Docker is never actually invoked in this suite — every subprocess call
    is stubbed, per the project's rule against starting real containers in
    tests."""

    def test_standard_needs_nothing_started(self, tmp_path: Path) -> None:
        assert ms.start_memory_stack("standard", tmp_path) == []

    def test_lexical_needs_nothing_started(self, tmp_path: Path) -> None:
        assert ms.start_memory_stack("lexical", tmp_path) == []

    def test_no_docker_daemon_returns_a_single_explanatory_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "docker_daemon_reachable", lambda: False)
        messages = ms.start_memory_stack("complet", tmp_path)
        assert len(messages) == 1
        assert "injoignable" in messages[0]

    def test_already_reachable_services_start_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "docker_daemon_reachable", lambda: True)
        monkeypatch.setattr(ms, "probe_services", lambda *a, **k: _all())

        def _fail_if_called(*a: object, **k: object) -> None:
            raise AssertionError("docker compose must not run when already reachable")

        monkeypatch.setattr(ms.subprocess, "run", _fail_if_called)
        # `complet` still tries Redis unless it is also reachable — _all()
        # makes every probe reachable, so nothing should be started at all.
        assert ms.start_memory_stack("complet", tmp_path) == []

    def test_compose_is_invoked_and_waited_for_when_services_are_down(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "docker_daemon_reachable", lambda: True)
        down = _all(
            weaviate=_probe("weaviate", reachable=False),
            neo4j=_probe("neo4j", reachable=False),
            redis=_probe("redis", reachable=False),
        )
        monkeypatch.setattr(ms, "probe_services", lambda *a, **k: down)
        monkeypatch.setattr(ms, "_wait_reachable", lambda *a, **k: True)

        calls: list[list[str]] = []

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd: list[str], **kwargs: object) -> _Result:
            calls.append(cmd)
            return _Result()

        monkeypatch.setattr(ms.subprocess, "run", _fake_run)

        messages = ms.start_memory_stack("complet", tmp_path)

        assert any("compose" in c[1] for c in calls if len(c) > 1)
        assert any("Weaviate" in m and "démarré" in m for m in messages)
        assert any("Neo4j" in m and "démarré" in m for m in messages)
        assert any("Redis" in m and "démarré" in m for m in messages)
        assert (tmp_path / "docker-compose.memory-target.yml").is_file()

    def test_missing_compose_template_raises_a_named_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing/unreadable bundled compose template must not crash the
        command on a raw OSError — `_ensure_compose_file` refuses by name
        (#619 review), so `init --memory-stack up`/`memory up --start` can
        catch it and exit 1 cleanly instead of an unhandled traceback."""
        monkeypatch.setattr(ms, "docker_daemon_reachable", lambda: True)
        down = _all(weaviate=_probe("weaviate", reachable=False), neo4j=_probe("neo4j", reachable=False))
        monkeypatch.setattr(ms, "probe_services", lambda *a, **k: down)
        monkeypatch.setattr("grimoire.data.framework_path", lambda: tmp_path / "does-not-exist")

        with pytest.raises(GrimoireRuntimeError, match="illisible"):
            ms.start_memory_stack("complet", tmp_path)

    def test_compose_failure_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ms, "docker_daemon_reachable", lambda: True)
        down = _all(weaviate=_probe("weaviate", reachable=False), neo4j=_probe("neo4j", reachable=False))
        monkeypatch.setattr(ms, "probe_services", lambda *a, **k: down)

        class _Result:
            returncode = 1
            stdout = ""
            stderr = "compose plugin missing"

        monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: _Result())

        messages = ms.start_memory_stack("complet", tmp_path)
        assert any("échoué" in m for m in messages)
