"""#1133 : la classe de données décide du routage, en échec fermé."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.worker_provider import route_refusal, route_worker
from grimoire.providers.data_policy import data_policy_refusal, declared_data_class
from grimoire.providers.registry import REGISTRY_FILE, read_registry

REGISTRY = """\
providers:
  - id: ext
    enabled: true
    provider_type: hosted
    currency: api
    anthropic_base_url: https://ext.example/anthropic
    models: [{{id: ext-m, tier: mid}}, {{id: ext-m, tier: cheap}}]
    data_policy:
      allowed_data_classes: [project-source]
      forbidden_data_classes: [proprietary-game-data]
  - id: nue
    enabled: true
    provider_type: hosted
    models: [{{id: nue-m, tier: mid}}]
  - id: ici
    enabled: true
    provider_type: local
    models: [{{id: ici-m, tier: mid}}]
routing:
  default_data_class: "{default}"
"""


def _project(tmp_path: Path, default: str = "") -> Path:
    path = tmp_path / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REGISTRY.format(default=default), encoding="utf-8")
    return tmp_path


def _task(data_class: str = "") -> MissionTask:
    return MissionTask(
        id="T-1", mission_id="M", title="t", status=TaskState.READY, type=TaskType.IMPLEMENTATION,
        risk_profile=RiskProfile.STANDARD, acceptance=("La revue la juge READY",), created_at="2026-11-07T00:00:00Z",
        data_class=data_class,
    )


def _provider(root: Path, provider_id: str):
    return next(p for p in read_registry(root) if p.id == provider_id)


@pytest.fixture
def key_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "store"
    store.mkdir()
    (store / "ext.env").write_text("EXT_API_KEY=x\n", encoding="utf-8")
    monkeypatch.setattr("grimoire.missions.worker_provider.store_dir", lambda: store)
    monkeypatch.setattr("grimoire.missions.worker_provider.env_for_provider", lambda _p: {"EXT_API_KEY": "x"})
    return store


def test_sans_classe_declaree_pas_de_route(tmp_path: Path, key_store: Path) -> None:
    root = _project(tmp_path)
    assert route_worker(root, "ext", "mid", _task()) is None
    assert "aucune classe" in route_refusal(root, "ext", _task())


def test_classe_interdite_pas_de_route_et_motif_visible(tmp_path: Path, key_store: Path) -> None:
    root = _project(tmp_path)
    task = _task("proprietary-game-data")
    assert route_worker(root, "ext", "mid", task) is None
    assert "interdite" in route_refusal(root, "ext", task)


def test_classe_admise_route(tmp_path: Path, key_store: Path) -> None:
    root = _project(tmp_path)
    task = _task("project-source")
    route = route_worker(root, "ext", "mid", task)
    assert route is not None
    assert route.provider == "ext"
    assert route_refusal(root, "ext", task) == ""


def test_la_classe_du_projet_vaut_pour_une_tache_sans_classe(tmp_path: Path, key_store: Path) -> None:
    root = _project(tmp_path, default="project-source")
    assert route_worker(root, "ext", "mid", _task()) is not None
    assert declared_data_class(root, "") == ("project-source", "projet")


def test_la_classe_de_la_tache_prime_sur_celle_du_projet(tmp_path: Path, key_store: Path) -> None:
    root = _project(tmp_path, default="project-source")
    assert route_worker(root, "ext", "mid", _task("proprietary-game-data")) is None
    assert declared_data_class(root, "proprietary-game-data") == ("proprietary-game-data", "tâche")


def test_une_classe_inconnue_du_fournisseur_est_refusee(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert "pas admise" in data_policy_refusal(root, _provider(root, "ext"), "inventee")


def test_un_fournisseur_sans_data_policy_ne_recoit_rien(tmp_path: Path) -> None:
    root = _project(tmp_path, default="project-source")
    assert "pas admise" in data_policy_refusal(root, _provider(root, "nue"), "")


def test_un_fournisseur_local_n_a_pas_besoin_de_classe(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert data_policy_refusal(root, _provider(root, "ici"), "") == ""


def test_la_classe_survit_a_la_relecture_de_la_tache() -> None:
    task = _task("project-source")
    assert MissionTask.from_dict(task.to_dict()).data_class == "project-source"
    assert "data_class" not in _task().to_dict()


def test_un_projet_sans_registre_ne_declare_rien(tmp_path: Path) -> None:
    assert declared_data_class(tmp_path, "") == ("", "")


