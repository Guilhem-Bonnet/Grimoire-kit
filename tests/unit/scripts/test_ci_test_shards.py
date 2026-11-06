"""Répartition de la suite SDK entre jobs de CI : aucun fichier perdu ni rejoué."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "ci-test-shards.py"
_CI = _REPO / ".github" / "workflows" / "ci-sdk.yml"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="dépôt source uniquement")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_test_shards_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, sizes: dict[str, int]) -> Path:
    for name, size in sizes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * size, encoding="utf-8")
    return root


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = YAML(typ="safe").load(_CI.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_chaque_fichier_du_depot_part_exactement_une_fois() -> None:
    module = _module()
    parts = module.shards(_REPO, 4)
    placed = Counter(name for part in parts for name in part)

    expected = {p.relative_to(_REPO).as_posix() for p in module.test_files(_REPO)}
    assert set(placed) == expected
    assert set(placed.values()) == {1}
    assert "tests/test_agentic_standard.py" in placed
    assert all(parts)


def test_les_motifs_de_pytest_sont_tous_couverts(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_agentic_standard.py": 1,
            "tests/unit/test_a.py": 1,
            "tests/unit/sous/b_test.py": 1,
            "tests/unit/_aide.py": 1,
            "tests/unit/conftest.py": 1,
            "tests/e2e/test_hors_suite.py": 1,
        },
    )

    names = {p.relative_to(root).as_posix() for p in _module().test_files(root)}

    assert names == {"tests/test_agentic_standard.py", "tests/unit/test_a.py", "tests/unit/sous/b_test.py"}


def test_la_repartition_equilibre_la_charge_et_reste_stable(tmp_path: Path) -> None:
    sizes = {"tests/test_agentic_standard.py": 10, "tests/unit/test_gros.py": 100}
    sizes |= {f"tests/unit/test_{i}.py": 25 for i in range(4)}
    root = _tree(tmp_path, sizes)
    module = _module()

    parts = module.shards(root, 2)

    assert parts == module.shards(root, 2)
    gros = next(part for part in parts if "tests/unit/test_gros.py" in part)
    assert not any(f"tests/unit/test_{i}.py" in gros for i in range(4))


def test_un_nombre_de_jobs_invalide_est_refuse(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="au moins un job"):
        _module().shards(tmp_path, 0)


@pytest.mark.parametrize(("shard", "of"), [(0, 4), (5, 4)])
def test_un_numero_hors_borne_echoue(shard: int, of: int) -> None:
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--shard", str(shard), "--of", str(of)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode != 0


def test_la_ligne_de_commande_rend_la_part_demandee() -> None:
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--shard", "1", "--of", "4"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert done.stdout.splitlines() == _module().shards(_REPO, 4)[0]


def test_la_matrice_de_la_ci_et_le_script_parlent_du_meme_nombre_de_jobs() -> None:
    job = _jobs()["test-shard"]
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    run = next(str(s["run"]) for s in job["steps"] if "ci-test-shards.py" in str(s.get("run", "")))
    assert f"--of {len(shards)}" in run


def test_l_agregateur_porte_le_check_requis_et_refuse_un_job_non_vert() -> None:
    jobs = _jobs()
    aggregate = jobs["test-aggregate"]

    assert aggregate["name"] == "Tests (Python 3.12, ubuntu-latest)"
    assert aggregate["if"] == "always()"
    assert aggregate["needs"] == ["test-shard"]
    first = aggregate["steps"][0]
    assert "needs.test-shard.result" in str(first["env"]) and 'success' in str(first["run"])
    assert any("--fail-under=75" in str(s.get("run", "")) for s in aggregate["steps"])
    assert "test-aggregate" in jobs["build"]["needs"]
    assert {"os": "ubuntu-latest", "python-version": "3.12"} in jobs["test"]["strategy"]["matrix"]["exclude"]


def test_une_part_passe_les_memes_options_que_les_autres_systemes() -> None:
    jobs = _jobs()
    shard_run = next(str(s["run"]) for s in jobs["test-shard"]["steps"] if "pytest" in str(s.get("run", "")))
    for option in ("--dist loadfile", "-n auto", "--timeout=300", "--timeout-method=thread", "--cov=src/grimoire"):
        assert option in shard_run
    assert "-x" not in shard_run.split()
