"""Santé de la mémoire et file d'hygiène (P25) — ``memory/quality`` et la route d'attention.

Ce qui est prouvé ici, sur des projets réels (store local écrit par le
``MemoryManager``) et, pour le graphe et l'index lexical, sur des doublures
qui comptent :

- la route ``memory/quality`` mesure textes distincts, utilisateurs, taux de
  provenance, part de projections, dérive store / lexical / graphe comptée
  sur la collection du projet, et rend les résultats de ``memory lint`` ;
- contradictions, supersessions orphelines, motifs transverses vieillissants,
  dérive de parité et graphe de code périmé deviennent des éléments TYPÉS de
  la route d'attention, chacun avec son geste ;
- le store de la Forge (25 « python is the best language » d'alice) met le
  projet en alerte ;
- une mesure impossible est « inconnue » avec son motif, jamais un zéro.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_hygiene as wh
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

FORGE_TEXT = "python is the best language"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wh, "_CACHE", {})


def _project(root: Path, name: str, extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: local\n{extra}", encoding="utf-8",
    )
    return root


def _manager(root: Path) -> MemoryManager:
    return MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)


def _forge(root: Path) -> Path:
    """Le store de la Forge : 25 fois le même texte de test, utilisateur alice, et un vrai souvenir."""
    _project(root, "Grimoire-Forge")
    mgr = _manager(root)
    for _ in range(25):
        mgr.store(FORGE_TEXT, user_id="alice")
    mgr.store("Qdrant partagé épinglé en 1.19.1.", metadata={"type": "decisions"}, actor="Guilhem")
    return root


def _criteria(report: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {c["id"]: c for c in report["criteria"]}


def _items(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in view["sections"] for item in section["items"]]


class _Count:
    def __init__(self, n: int) -> None:
        self.n = n

    def count(self) -> int:
        return self.n


class _Graph:
    """Un graphe partagé : le compte global ment, seul le compte par collection vaut."""

    def __init__(self, per_collection: dict[str, int]) -> None:
        self.per_collection = per_collection
        self.asked: list[str] = []

    def stats(self) -> dict[str, int]:
        return {"memories": 4320}

    def count_memories_in(self, collection: str) -> int:
        self.asked.append(collection)
        return self.per_collection.get(collection, 0)


def _wrap(monkeypatch: pytest.MonkeyPatch, *, lexical: int | None = None, graph: _Graph | None = None) -> None:
    """``open_readonly`` rend le vrai store du projet, flanqué d'un index lexical et d'un graphe doublés."""
    from grimoire.tools import workspace_knowledge as wk

    real = wk.open_readonly

    def opened(root: Path) -> tuple[Any, Any, str | None]:
        cfg, mgr, _reason = real(root)
        if mgr is None:
            local = cfg or GrimoireConfig.from_yaml(root / "project-context.yaml")
            mgr = MemoryManager.from_backend(_local_backend(root))
            cfg = local
        flanked = SimpleNamespace(
            backend=mgr.backend, lexical_companion=_Count(lexical) if lexical is not None else None,
            memory_graph=graph,
        )
        return cfg, flanked, None

    monkeypatch.setattr(wk, "open_readonly", opened)


def _local_backend(root: Path) -> Any:
    from grimoire.memory.backends.local import LocalMemoryBackend

    return LocalMemoryBackend(root / "_grimoire" / "_memory" / "grimoire.json")


# ── Critère 1 : memory/quality ──────────────────────────────────────────────


class TestQualityRoute:
    def test_la_route_mesure_le_store_et_lit_ses_parametres(self, tmp_path: Path) -> None:
        root = _forge(tmp_path / "forge")
        reg.save_registry([{"slug": "grimoire-forge", "name": "Grimoire-Forge", "path": str(root)}])
        assert f"{PREFIX}memory/quality" in GET_ROUTES

        report = workspace_get(tmp_path, f"{PREFIX}memory/quality", {"slug": ["grimoire-forge"]})

        assert report["schemaVersion"] == wh.QUALITY_SCHEMA_VERSION
        assert report["project"] == {"slug": "grimoire-forge", "name": "Grimoire-Forge"}
        measures = report["measures"]
        assert measures["entries"] == 26
        assert measures["distinctTexts"] == 2
        assert measures["duplicates"][0] == {"text": FORGE_TEXT, "count": 25}
        assert {u["id"]: (u["count"], u["test"]) for u in measures["users"]} == {
            "alice": (25, True), "global": (1, False),
        }
        assert measures["provenance"] == {"souvenirs": 26, "withProvenance": 1, "rate": 1 / 26}
        assert measures["projections"] == {"count": 0, "share": 0.0}
        crit = _criteria(report)
        assert (crit["distinct"]["state"], crit["distinct"]["value"]) == ("fail", "2")
        assert "×25" in crit["distinct"]["detail"]
        assert crit["users"]["state"] == "fail"
        assert "alice (25)" in crit["users"]["detail"]
        assert (crit["provenance"]["state"], crit["provenance"]["value"]) == ("fail", "4 %")
        assert crit["content"]["state"] == "ok"

    def test_part_de_projections_et_souvenirs_reels(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "kit", "kit")
        mgr = _manager(root)
        for i in range(3):
            mgr.store(f"def fonction_{i}(): pass", metadata={"projection_group": "code", "source_kind": "projection"})
        mgr.store("Le kit se publie par tag.", metadata={"type": "decisions"}, session_id="s-1")

        report = wh.memory_quality(root)

        assert report["measures"]["projections"] == {"count": 3, "share": 0.75}
        assert report["measures"]["provenance"]["rate"] == 1.0
        crit = _criteria(report)
        assert crit["content"]["value"] == "1"
        assert "75 % de projections (3)" in crit["content"]["detail"]
        assert crit["provenance"]["state"] == "ok"
        assert report["alert"] is False

    def test_rien_que_des_projections_echoue(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "copies", "copies")
        _manager(root).store("class A: pass", metadata={"projection_group": "code"})

        crit = _criteria(wh.memory_quality(root))

        assert crit["content"]["state"] == "fail"
        assert (crit["provenance"]["state"], crit["provenance"]["detail"]) == ("unknown", "aucun souvenir à mesurer")

    def test_derive_filtree_par_collection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "forge", "Grimoire-Forge")
        _manager(root).store("Un souvenir.", actor="Guilhem")
        _manager(root).store("Un autre souvenir.", actor="Guilhem")
        graph = _Graph({"": 2, "GrimoireKitMemory": 3717})
        _wrap(monkeypatch, lexical=1, graph=graph)

        report = wh.memory_quality(root)

        drift = report["measures"]["drift"]
        assert graph.asked == [""]
        assert drift == {"collection": "", "store": 2, "lexical": 1, "graph": 2, "lexicalDrift": 1,
                         "graphDrift": 0, "notes": []}
        crit = _criteria(report)["drift"]
        assert (crit["state"], crit["value"]) == ("fail", "1 d'écart")
        lexical = next(i for i in report["hygiene"] if i["kind"] == wh.MEMORY_PARITY)
        assert lexical["key"] == "lexical"
        assert lexical["ref"]["gesture"]["command"] == "grimoire memory reindex-lexical"

    def test_collection_weaviate_normalisee(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "forge"
        root.mkdir()
        (root / "project-context.yaml").write_text(
            "project:\n  name: Grimoire-Forge\nmemory:\n  backend: weaviate-server\n"
            "  weaviate_url: http://127.0.0.1:1\n  weaviate_collection: grimoire_forge_memory\n",
            encoding="utf-8",
        )
        _local_backend(root).store("Un souvenir.", metadata={"actor": "Guilhem"})
        from grimoire.memory import store_probe

        monkeypatch.setattr(store_probe, "store_exists", lambda _cfg, _root: True)
        graph = _Graph({"GrimoireForgeMemory": 4320})
        _wrap(monkeypatch, graph=graph)

        report = wh.memory_quality(root)

        assert graph.asked == ["GrimoireForgeMemory"]
        drift = report["measures"]["drift"]
        assert (drift["store"], drift["graph"], drift["graphDrift"]) == (1, 4320, -4319)
        parity = next(i for i in report["hygiene"] if i["kind"] == wh.MEMORY_PARITY)
        assert parity["ref"]["gesture"]["command"] == "grimoire memory gate --sync"
        assert "collection GrimoireForgeMemory" in parity["reason"]

    def test_un_graphe_illisible_est_inconnu(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "p", "p")
        _manager(root).store("Un souvenir.", actor="Guilhem")

        class _Broken(_Graph):
            def count_memories_in(self, collection: str) -> int:
                raise ConnectionError("neo4j injoignable")

        _wrap(monkeypatch, graph=_Broken({}))
        crit = _criteria(wh.memory_quality(root))["drift"]

        assert crit["state"] == "unknown"
        assert "graphe illisible : neo4j injoignable" in crit["detail"]

    def test_resultats_de_memory_lint(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "p", "p")
        memory = root / "_grimoire" / "_memory"
        (memory / "agent-learnings").mkdir(parents=True)
        (memory / "decisions-log.md").write_text(
            "- [2026-01-02] Backend Qdrant serveur adopté pour la mémoire partagée\n", encoding="utf-8",
        )
        (memory / "agent-learnings" / "dev.md").write_text(
            "- [2026-01-05] Backend Qdrant serveur rejeté pour la mémoire partagée\n", encoding="utf-8",
        )

        report = wh.memory_quality(root)

        lint = report["measures"]["lint"]
        assert (lint["files"], lint["entries"], lint["errors"]) == (2, 2, 1)
        assert lint["contradictions"][0]["files"] == ["decisions-log.md", "learnings/dev.md"]
        crit = _criteria(report)["lint"]
        assert (crit["state"], crit["value"]) == ("fail", "1 erreur")
        item = next(i for i in report["hygiene"] if i["kind"] == wh.MEMORY_CONTRADICTION)
        assert item["ref"]["gesture"]["command"] == "grimoire memory lint"
        assert "Resolved: " in item["ref"]["gesture"]["note"]
        assert "<termes>" not in item["ref"]["gesture"]["note"]

    def test_un_lint_trop_gros_est_inconnu(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "p", "p")
        memory = root / "_grimoire" / "_memory"
        memory.mkdir(parents=True)
        (memory / "decisions-log.md").write_text("- a\n- b\n- c\n", encoding="utf-8")
        monkeypatch.setattr(wh, "LINT_MAX_ENTRIES", 2)

        crit = _criteria(wh.memory_quality(root))["lint"]

        assert crit["state"] == "unknown"
        assert "grimoire memory lint" in crit["detail"]


class TestUnknownIsNotZero:
    def test_projet_absent_du_registre(self, tmp_path: Path) -> None:
        report = wh.memory_quality(tmp_path, slug="inconnu")

        assert report["reason"] == "projet absent du registre de la machine"
        assert report["criteria"] == []
        assert report["alert"] is False

    def test_projet_non_initialise(self, tmp_path: Path) -> None:
        report = wh.memory_quality(tmp_path)

        assert report["reason"] == "projet non initialisé (pas de project-context.yaml)"

    def test_store_jamais_cree_n_est_pas_cree(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "neuf", "neuf")

        report = wh.memory_quality(root)

        assert report["storeReason"] == "aucun store encore créé"
        crit = _criteria(report)
        assert {crit[c]["state"] for c in ("distinct", "users", "provenance", "content", "drift")} == {"unknown"}
        assert crit["distinct"]["value"] == "inconnu"
        assert not (root / "_grimoire" / "_memory" / "grimoire.json").exists()
        assert report["alert"] is False

    def test_store_vide_n_est_pas_en_alerte(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "vide", "vide")
        mgr = _manager(root)
        entry = mgr.store("éphémère")
        mgr.backend.delete(entry.id)

        report = wh.memory_quality(root)

        crit = _criteria(report)
        assert (crit["distinct"]["value"], crit["distinct"]["detail"]) == ("0", "store vide")
        assert crit["content"]["state"] == "unknown"
        assert report["alert"] is False

    def test_configuration_illisible(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project: [\n", encoding="utf-8")

        assert wh.memory_quality(tmp_path)["reason"].startswith("configuration illisible")


# ── Critère 2 : des éléments typés dans la route d'attention ───────────────


class TestAttentionItems:
    def test_supersession_orpheline(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "p", "p")
        mgr = _manager(root)
        orphan = mgr.backend.store("Ancienne règle de release.", metadata={"superseded_by": "disparue-123"})
        mgr.store("Une décision vivante.", actor="Guilhem")

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        item = next(i for i in _items(view) if i["kind"] == wh.MEMORY_ORPHAN)
        assert item["ref"]["entry"] == orphan.id
        assert item["ref"]["target"] == "disparue-123"
        assert item["ref"]["gesture"]["command"] == f"grimoire memory show {orphan.id}"
        assert "disparue-123" in item["reason"]

    def test_contradiction_et_section_a_entretenir(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "p", "p")
        memory = root / "_grimoire" / "_memory"
        (memory / "agent-learnings").mkdir(parents=True)
        (memory / "decisions-log.md").write_text("- Cache Redis adopté pour les sessions\n", encoding="utf-8")
        (memory / "agent-learnings" / "a.md").write_text("- Cache Redis abandonné pour les sessions\n",
                                                         encoding="utf-8")

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        maintain = next(s for s in view["sections"] if s["id"] == "maintain")
        kinds = [i["kind"] for i in maintain["items"]]
        assert wh.MEMORY_CONTRADICTION in kinds
        assert view["counts"][wh.MEMORY_CONTRADICTION] == 1

    def test_motif_transverse_vieillissant(self, tmp_path: Path) -> None:
        from grimoire.memory import shared as sh

        root = _project(tmp_path / "p", "Projet P", "  shared_collection: motifs\n")
        cfg = GrimoireConfig.from_yaml(root / "project-context.yaml")
        manager = sh.open_shared(cfg)
        assert manager is not None
        old = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=200)  # même référence que shared._today (UTC)
        aging = sh.promote(manager, "Toujours épingler la mineure d'un service partagé.",
                           domain="infra", project_name="Projet P", today=old)
        sh.promote(manager, "Garder une sauvegarde hors volume avant de recréer un conteneur.", domain="infra", project_name="Ailleurs", today=old)
        sh.promote(manager, "Relire le changelog amont avant toute montée de version.", domain="infra", project_name="Projet P")

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        items = [i for i in _items(view) if i["kind"] == wh.SHARED_AGING]
        assert [i["ref"]["gesture"]["command"] for i in items] == [f"grimoire memory shared confirm {aging.id}"]
        assert items[0]["ref"]["freshness"] == "aging"
        assert items[0]["reason"] == "dernière confirmation il y a 200 jours"
        crit = _criteria(wh.memory_quality(root))["shared"]
        assert (crit["state"], crit["value"]) == ("fail", "1 à reconfirmer")

    def test_graphe_de_code_perime(self, tmp_path: Path) -> None:
        from grimoire.codegraph.freshness import record_index

        root = _project(tmp_path / "p", "p")
        (root / "src").mkdir()
        module = root / "src" / "mod.py"
        module.write_text("def a():\n    return 1\n", encoding="utf-8")
        record_index(root, "src")
        assert _criteria(wh.memory_quality(root))["codegraph"]["state"] == "ok"
        module.write_text("def a():\n    return 2\n", encoding="utf-8")
        wh._CACHE.clear()

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        item = next(i for i in _items(view) if i["kind"] == wh.CODEGRAPH_STALE)
        assert item["ref"]["changed"] == 1
        assert item["ref"]["gesture"]["command"] == "grimoire_codegraph_index"
        assert "1 fichier changé" in item["reason"]

    def test_graphe_voulu_jamais_indexe_est_inconnu(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "p", "p", "  code_graph: neo4j\n")

        crit = _criteria(wh.memory_quality(root))["codegraph"]

        assert crit["state"] == "unknown"
        assert crit["detail"] == "jamais indexé : aucun reçu d'indexation"

    def test_derive_de_parite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "p", "p")
        _manager(root).store("Un souvenir.", actor="Guilhem")
        _wrap(monkeypatch, graph=_Graph({"": 5}))

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        item = next(i for i in _items(view) if i["kind"] == wh.MEMORY_PARITY)
        assert (item["ref"]["store"], item["ref"]["graph"]) == (1, 5)
        assert item["ref"]["gesture"]["command"] == "grimoire memory gate --sync"

    def test_chaque_element_porte_son_geste(self, tmp_path: Path) -> None:
        root = _forge(tmp_path / "forge")

        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        hygiene = [i for i in _items(view) if i["kind"] in wh.HYGIENE_KINDS]
        assert hygiene
        assert all(i["ref"]["gesture"]["command"] and i["ref"]["gesture"]["label"] for i in hygiene)
        assert all(i["id"].startswith(f"{i['kind']}:") for i in hygiene)

    def test_l_attention_ne_sonde_pas_un_serveur_non_sonde(self, tmp_path: Path) -> None:
        root = tmp_path / "srv"
        root.mkdir()
        (root / "project-context.yaml").write_text(
            "project:\n  name: srv\nmemory:\n  backend: qdrant-server\n  qdrant_url: http://127.0.0.1:1\n",
            encoding="utf-8",
        )

        report = wh.cached_report(root)

        assert report["storeReason"].startswith("mémoire non sondée")
        assert report["hygiene"] == []

    def test_le_cache_est_repris_puis_renouvele(self, tmp_path: Path) -> None:
        root = _forge(tmp_path / "forge")
        first = wh.cached_report(root)

        assert wh.cached_report(root) is first
        assert wh.cached_report(root, max_age=-1) is not first


# ── Critère 3 : la Forge sort en alerte ────────────────────────────────────


class TestForgeAlert:
    def test_le_store_de_la_forge_met_le_projet_en_alerte(self, tmp_path: Path) -> None:
        root = _forge(tmp_path / "forge")

        report = wh.memory_quality(root)

        assert report["alert"] is True
        assert {"Textes distincts", "Utilisateurs"} <= set(report["alertReasons"])
        assert report["summary"]["fail"] >= 2
        purge = next(i for i in report["hygiene"] if i["kind"] == wh.MEMORY_QUALITY)
        assert purge["title"] == "24 copies et des entrées de test dans le store"
        assert purge["reason"].startswith(f"« {FORGE_TEXT} » ×25 ; utilisateur alice")
        gesture = purge["ref"]["gesture"]
        assert gesture["command"].startswith("grimoire memory export --file ")
        assert gesture["command"].index("export") < gesture["command"].index("gc")
        assert gesture["preview"] == [{"text": FORGE_TEXT, "count": 25, "users": ["alice"]}]

    def test_et_l_alerte_est_poussee_dans_a_traiter(self, tmp_path: Path) -> None:
        root = _forge(tmp_path / "forge")
        reg.save_registry([{"slug": "grimoire-forge", "name": "Grimoire-Forge", "path": str(root)}])

        view = wa.attention_view(tmp_path, portfolio=True, service_probe=lambda *_: "online",
                                 watch=wa.ServiceWatch())

        item = next(i for i in _items(view) if i["kind"] == wh.MEMORY_QUALITY)
        assert item["id"] == "memory_quality:grimoire-forge:contamination"
        assert item["project"] == {"slug": "grimoire-forge", "name": "Grimoire-Forge"}
        assert item["ref"]["testUsers"] == ["alice"]

    def test_des_tests_seuls_sans_doublon(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "p", "p")
        _manager(root).store("un reste de test", user_id="bob")

        purge = next(i for i in wh.memory_quality(root)["hygiene"] if i["kind"] == wh.MEMORY_QUALITY)

        assert purge["title"] == "Entrées de test dans le store"
        assert "gc" not in purge["ref"]["gesture"]["command"]


# ── Le graphe compte par collection ────────────────────────────────────────


def test_neo4j_compte_les_souvenirs_d_une_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    calls: list[tuple[str, dict[str, Any]]] = []

    class _Driver:
        def execute_query(self, statement: str, **kwargs: Any) -> tuple[list[dict[str, int]], None, None]:
            calls.append((statement, kwargs))
            return ([{"count": 76}], None, None)

        def close(self) -> None:
            return None

    fake = SimpleNamespace(GraphDatabase=SimpleNamespace(driver=lambda *_a, **_k: _Driver()))
    monkeypatch.setitem(sys.modules, "neo4j", fake)
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    graph = Neo4jMemoryGraph(uri="bolt://x", user="neo4j", password="p", project="grimoire-forge")

    assert graph.count_memories_in("GrimoireForgeMemory") == 76
    statement, params = calls[-1]
    assert "weaviate_collection" in statement
    assert params["collection"] == "GrimoireForgeMemory"
    assert params["project"] == "grimoire-forge"
