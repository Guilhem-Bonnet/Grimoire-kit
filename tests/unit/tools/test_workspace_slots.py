"""P41 — emplacements mémoire depuis le cockpit : attribuer, retirer, séparer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.memory.backends.base import MemoryEntry
from grimoire.tools import workspace_actions as wact
from grimoire.tools import workspace_slots as slots


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


def _project(root: Path, name: str, memory: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(f"project:\n  name: {name}\nmemory:\n{memory}", encoding="utf-8")
    return root


QDRANT = "  backend: qdrant-server\n  qdrant_url: http://localhost:6333\n  collection_prefix: shared\n"


class FakeStore:
    def __init__(self, entries: list[MemoryEntry] | None = None) -> None:
        self.rows: dict[str, MemoryEntry] = {e.id: e for e in entries or []}

    def get_all(self, **_kw: Any) -> list[MemoryEntry]:
        return list(self.rows.values())

    def upsert(self, entry_id: str, text: str, *, user_id: str = "", tags: Any = (), metadata: Any = None, created_at: Any = None) -> None:
        self.rows[entry_id] = MemoryEntry(id=entry_id, text=text, user_id=user_id, tags=list(tags), metadata=dict(metadata or {}))


def _entry(i: str, project: str) -> MemoryEntry:
    return MemoryEntry(id=i, text=f"souvenir {i}", user_id="u", tags=[], metadata={"project_name": project})


def _env(registry: list[dict[str, str]], stores: dict[str | None, FakeStore] | None = None) -> wact.ActionEnv:
    stores = stores if stores is not None else {}

    def open_store(_cfg: Any, _root: Path, collection: str | None) -> FakeStore:
        return stores.setdefault(collection, FakeStore())

    return wact.ActionEnv(
        registry=lambda: registry, open_store=open_store,
        now=lambda: datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
    )


def _run(root: Path, action: str, params: dict[str, Any], env: wact.ActionEnv, **apply_extra: Any) -> tuple[dict, dict]:
    plan = wact.plan_action(root, action, {"params": params}, env=env)
    assert plan["ok"], plan
    done = wact.apply_action(root, action, {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], **apply_extra}, env=env)
    return plan, done


# ── Attribuer / retirer : action A ────────────────────────────────────────────


def test_attribuer_est_une_action_a_apercu_puis_retour_arriere(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", "  backend: lexical\n")
    config = root / "project-context.yaml"
    before = config.read_text()
    env = _env([])
    assert wact.CATALOGUE["memory.slot"].klass == "A"

    plan = wact.plan_action(root, "memory.slot", {"params": {"kind": "qdrant", "location": "http://localhost:6333"}}, env=env)
    assert plan["ok"] and plan["class"] == "A"
    assert config.read_text() == before  # l'aperçu n'écrit pas
    assert any("qdrant_url" in e for e in plan["plan"]["effects"])

    done = wact.apply_action(root, "memory.slot", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    assert done["ok"] and done["rollback"]
    assert "collection_prefix: alpha" in config.read_text()

    undone = wact.rollback_action(root, "memory.slot", {"run_id": plan["plan_id"]}, env=env)
    assert undone["ok"] and config.read_text() == before


def test_retirer_ne_touche_a_aucun_souvenir_et_se_defait(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", QDRANT)
    config = root / "project-context.yaml"
    before = config.read_text()
    env = _env([])
    plan, done = _run(root, "memory.slot", {"op": "remove", "kind": "qdrant"}, env)
    assert done["ok"], done
    assert "qdrant_url: ''" in config.read_text() or 'qdrant_url: ""' in config.read_text()
    assert any("ni copiés ni effacés" in n for n in plan["plan"]["notes"])
    assert wact.rollback_action(root, "memory.slot", {"run_id": plan["plan_id"]}, env=env)["ok"]
    assert config.read_text() == before


def test_attribuer_un_emplacement_non_vide_avertit_et_propose_un_neuf(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", "  backend: lexical\n")
    b = _project(tmp_path / "b", "Beta", QDRANT)
    c = _project(tmp_path / "c", "Gamma", QDRANT)
    registry = [{"path": str(b)}, {"path": str(c)}]
    env = _env(registry)

    options = slots.slot_options(root / "project-context.yaml", "qdrant", registry)
    assert options["options"][0]["id"] == "new" and options["options"][0]["default"] is True
    assert options["options"][0]["name"] == "alpha"
    assert options["options"][1]["usedBy"] == ["Beta", "Gamma"]

    plan = wact.plan_action(
        root, "memory.slot",
        {"params": {"kind": "qdrant", "slot": "existing", "location": "http://localhost:6333", "name": "shared"}}, env=env,
    )
    assert plan["ok"]
    assert any("cet emplacement deviendra partagé avec 2" in n for n in plan["plan"]["notes"])
    assert plan["plan"]["facts"]["sharedWith"] == ["Beta", "Gamma"]

    neuf = wact.plan_action(root, "memory.slot", {"params": {"kind": "qdrant", "location": "http://localhost:6333"}}, env=env)
    assert not any("partagé" in n for n in neuf["plan"]["notes"])
    assert neuf["plan"]["facts"]["after"]["name"] == "alpha"


def test_redis_propose_le_namespace_avant_le_conteneur(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", "  short_term_backend: redis\n  redis_url: redis://localhost:6379/0\n")
    options = slots.slot_options(root / "project-context.yaml", "redis", [])
    assert [m["id"] for m in options["redisModes"]] == ["namespace", "container"]
    assert options["redisModes"][0]["default"] is True

    env = _env([])
    plan = wact.plan_action(root, "memory.slot", {"params": {"kind": "redis"}}, env=env)
    assert plan["ok"] and plan["plan"]["facts"]["redisMode"] == "namespace"
    assert plan["plan"]["facts"]["after"] == {
        "kind": "redis", "location": "redis://localhost:6379/0", "name": "alpha", "assigned": True,
    }
    sans_adresse = wact.plan_action(root, "memory.slot", {"params": {"kind": "redis", "redis_mode": "container"}}, env=env)
    assert not sans_adresse["ok"] and "adresse" in sans_adresse["blockers"][0]


def test_parametres_invalides_sont_refuses(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", "  backend: lexical\n")
    env = _env([])
    for params in ({"kind": "mongo"}, {"kind": "qdrant", "op": "x"}, {"kind": "qdrant"},
                   {"kind": "qdrant", "location": "pas une url"}, {"op": "remove", "kind": "qdrant"}):
        assert not wact.plan_action(root, "memory.slot", {"params": params}, env=env)["ok"], params


# ── Séparer : action D via memory split (P28) ─────────────────────────────────


def _shared(tmp_path: Path) -> tuple[Path, dict[str | None, FakeStore], wact.ActionEnv]:
    root = _project(tmp_path / "a", "Alpha", QDRANT)
    entries = [_entry("1", "Alpha"), _entry("2", "Alpha"), _entry("3", "Beta"), _entry("4", "")]
    stores: dict[str | None, FakeStore] = {None: FakeStore(entries)}
    return root, stores, _env([], stores)


def test_separer_exporte_copie_bascule_et_laisse_la_source(tmp_path: Path) -> None:
    root, stores, env = _shared(tmp_path)
    config = root / "project-context.yaml"
    before = config.read_text()
    assert wact.CATALOGUE["memory.split"].klass == "D"

    plan = wact.plan_action(root, "memory.split", {"params": {"kind": "qdrant"}}, env=env)
    assert plan["ok"] and plan["class"] == "D"
    assert plan["plan"]["needsConfirmation"] is True
    assert plan["plan"]["facts"]["target"] == "shared-alpha" and plan["plan"]["facts"]["count"] == 2
    assert "shared-alpha" not in stores and config.read_text() == before  # l'aperçu n'écrit rien

    refus = wact.apply_action(root, "memory.split", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    assert not refus["ok"] and "confirm" in refus["raison"]
    assert config.read_text() == before

    done = wact.apply_action(
        root, "memory.split", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], "confirm": True}, env=env,
    )
    assert done["ok"], done
    assert set(stores["shared-alpha"].rows) == {"1", "2"}  # identifiants conservés
    assert set(stores[None].rows) == {"1", "2", "3", "4"}  # source intacte : nettoyage différé
    assert "collection_prefix: shared-alpha" in config.read_text()
    export = Path(done["verification"]["export"])
    assert export.is_file() and json.loads(export.read_text())["count"] == 4  # export de toute la source

    undone = wact.rollback_action(root, "memory.split", {"run_id": plan["plan_id"]}, env=env)
    assert undone["ok"] and config.read_text() == before
    assert set(stores["shared-alpha"].rows) == {"1", "2"}  # la copie reste, rien n'est effacé


def test_separer_n_efface_jamais_la_source_meme_en_cas_d_echec(tmp_path: Path) -> None:
    root, stores, env = _shared(tmp_path)

    class Broken(FakeStore):
        def upsert(self, *_a: Any, **_k: Any) -> None:
            raise OSError("disque plein")

    stores["shared-alpha"] = Broken()
    config = root / "project-context.yaml"
    before = config.read_text()
    plan = wact.plan_action(root, "memory.split", {"params": {"kind": "qdrant"}}, env=env)
    done = wact.apply_action(root, "memory.split", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], "confirm": True}, env=env)
    assert not done["ok"] and "disque plein" in done["raison"]
    assert config.read_text() == before and len(stores[None].rows) == 4


def test_neo4j_est_non_separable_tant_que_p15_n_est_pas_livre(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", "  neo4j_uri: bolt://localhost:7687\n")
    plan = wact.plan_action(root, "memory.split", {"params": {"kind": "neo4j"}}, env=_env([]))
    assert not plan["ok"]
    assert plan["blockers"] == [slots.NEO4J_SPLIT_BLOCKER] and "non séparable" in plan["blockers"][0]
    assert plan["plan"]["facts"]["separable"] is False
    applied = wact.apply_action(root, "memory.split", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], "confirm": True}, env=_env([]))
    assert not applied["ok"]


def test_separer_sans_souvenir_du_projet_est_refuse(tmp_path: Path) -> None:
    root = _project(tmp_path / "a", "Alpha", QDRANT)
    env = _env([], {None: FakeStore([_entry("3", "Beta")])})
    plan = wact.plan_action(root, "memory.split", {"params": {"kind": "qdrant"}}, env=env)
    assert not plan["ok"] and "rien à séparer" in plan["blockers"][0]
