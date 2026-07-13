"""Tests for evals/collect.py — run-record collector for the evals protocol."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "evals" / "collect.py"


def _load_module():
    name = "evals_collect_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def governed_project(tmp_path: Path) -> Path:
    from grimoire.core.agentic_standard import setup_standard_profile

    setup_standard_profile(tmp_path, profile_id="starter", project_name="evals-witness")
    return tmp_path


def test_governed_record_has_standard_metrics(governed_project: Path) -> None:
    mod = _load_module()
    record = mod.collect_record(governed_project, "web-app-todo", "bootstrap", "governed")
    assert record["$schema"] == "grimoire-evals-run-record/v2"
    assert record["arm"] == "governed"
    std = record["standard"]
    assert std["verify_ok"] is True
    assert std["profile"] == "starter"
    assert isinstance(std["score"], int)
    assert std["gate_ok"] is not None


def test_activated_uses_standard_task_id_and_review_gate(governed_project: Path) -> None:
    mod = _load_module()
    # Bug v1 : verify/gate étaient évalués sous le label de tâche d'éval
    # (artefacts inexistants) au lieu de l'id standard `bootstrap` — cf.
    # evals/reports/2026-07-03/ERRATA.md.
    record = mod.collect_record(governed_project, "web-app-todo", "feat-due-dates", "activated")
    assert record["task_id"] == "feat-due-dates"
    std = record["standard"]
    assert std is not None
    assert std["verify_ok"] is True  # artefacts lus sous `bootstrap`
    # Gate évalué à l'état cible `review` : un enrôlement pristine ne doit
    # pas le passer trivialement.
    assert std["gate_ok"] is False
    assert std["gate_missing"]


def test_external_schema_keeps_primary_regressions_rule(governed_project: Path) -> None:
    mod = _load_module()
    record = mod.collect_record(governed_project, "web-app-todo", "bootstrap", "governed")
    external = record["external"]
    # Règle primaire 2026-07-03 conservée, split secondaire ajouté (v2).
    assert "regressions" in external
    assert "regressions_hard" in external
    assert "regressions_adapted" in external


def test_baseline_record_on_bare_project(tmp_path: Path) -> None:
    mod = _load_module()
    record = mod.collect_record(tmp_path, "web-app-todo", "bootstrap", "baseline")
    # Protocole §Collecte : le bras baseline ne reçoit aucun artefact du
    # standard — ses métriques restent null (cf. 34db7160).
    assert record["standard"] is None


def test_external_metrics_stay_null(governed_project: Path) -> None:
    mod = _load_module()
    record = mod.collect_record(governed_project, "web-app-todo", "bootstrap", "governed")
    assert all(value is None for value in record["external"].values())


def test_invalid_arm_rejected(tmp_path: Path) -> None:
    mod = _load_module()
    with pytest.raises(ValueError, match="arm invalide"):
        mod.collect_record(tmp_path, "w", "t", "experimental")


def test_task_suites_parse_and_are_pinned() -> None:
    from ruamel.yaml import YAML

    mod = _load_module()
    yaml = YAML(typ="safe")
    for suite_path in sorted((_REPO_ROOT / "evals" / "tasks").glob("*.yaml")):
        suite = yaml.load(suite_path.read_text(encoding="utf-8"))
        assert suite["$schema"] == "grimoire-evals-task-suite/v1"
        # web-app-todo a 3 bras depuis le protocole v2 (PR #71) ;
        # terraform-houseserver reste à 2. Tous doivent être connus du
        # collecteur.
        assert suite["arms"][:2] == ["governed", "baseline"]
        assert set(suite["arms"]) <= set(mod.ARMS)
        assert suite["repetitions_min"] >= 5
        assert "amendments" in suite["pinned"]
        assert len(suite["tasks"]) >= 8
        for task in suite["tasks"]:
            assert task["id"] and task["kind"] and task["prompt"]
            assert task["acceptance"], f"tâche sans critères : {task['id']}"
