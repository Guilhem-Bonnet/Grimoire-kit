"""Tests du plan de mise en place Memory OS (``grimoire memory up``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import memory_setup as ms
from grimoire.tools.memory_setup import ServiceProbe, apply_memory_plan, build_memory_plan

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
