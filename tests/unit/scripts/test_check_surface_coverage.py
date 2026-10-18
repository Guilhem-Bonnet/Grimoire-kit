"""Registre de couverture par surface et cliquet monotone (D-09).

Avant : seul ``--cov-fail-under=75`` existait, aucun marqueur ``covers``, et
``tests/run-coverage.sh`` listait vingt et un fichiers dont neuf n'existaient
plus (et mesurait ``framework/tools``, qui n'existe pas). Une sous-commande, un
hook, un genre de flow ou un hôte pouvait perdre son dernier test sans que le
pourcentage bouge.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
from ruamel.yaml import YAML

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "check-surface-coverage.py"

def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_surface_coverage", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return done.stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


_UNITS = {"cli": ["flow run", "flow list"], "genre": ["fanout"], "hook": ["tool-policy"], "host": ["opencode"]}
_MARKED = (
    "import pytest\n\n"
    'pytestmark = pytest.mark.covers("cli:flow run", "cli:flow list")\n\n\n'
    "def test_x() -> None:\n"
    '    args = ["flow", "run"]\n'
    '    other = ["flow", "list"]\n'
    "    assert args and other\n"
)
_UNMARKED = _MARKED.replace('pytestmark = pytest.mark.covers("cli:flow run", "cli:flow list")\n\n', "")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, "src/keep.txt", "x\n")
    _write(root, "tests/unit/test_a.py", _MARKED)
    _write(root, "units.json", json.dumps(_UNITS))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


# ── critère 1 : retirer un marqueur fait échouer, en nommant l'unité ──


def test_removing_a_covers_marker_fails_and_names_the_unit(repo: Path) -> None:
    _write(repo, "tests/unit/test_a.py", _UNMARKED)
    _git(repo, "commit", "-q", "-am", "retire le marqueur")
    done = _run(repo, "--units-from", str(repo / "units.json"), "--check", "--base", "HEAD~1")
    assert done.returncode != 0, done.stdout + done.stderr
    assert "cli:flow run" in done.stderr and "cli:flow list" in done.stderr
    assert "test_a.py" in done.stderr


def test_unchanged_markers_pass_against_the_base(repo: Path) -> None:
    _write(repo, "tests/unit/test_b.py", "def test_y() -> None:\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "autre test")
    done = _run(repo, "--units-from", str(repo / "units.json"), "--check", "--base", "HEAD~1")
    assert done.returncode == 0, done.stderr


def test_a_new_uncovered_unit_is_listed_never_a_failure(repo: Path) -> None:
    units = {**_UNITS, "cli": [*_UNITS["cli"], "flow new"]}
    _write(repo, "units2.json", json.dumps(units))
    done = _run(repo, "--units-from", str(repo / "units2.json"), "--check", "--base", "HEAD")
    assert done.returncode == 0, done.stderr


def test_a_deleted_unit_is_not_a_loss() -> None:
    base = sc.build_registry(
        {"cli": ["flow run"]}, [sc.Claim("cli:flow run", "unit", "unit", "tests/unit/test_a.py")]
    )
    head = sc.build_registry({"cli": ["flow list"]}, [])
    assert sc.lost_units(base, head) == []


def test_an_unknown_base_fails_closed(repo: Path) -> None:
    done = _run(repo, "--units-from", str(repo / "units.json"), "--check", "--base", "pas-une-ref")
    assert done.returncode != 0


# ── niveau de preuve : le fichier borne la déclaration ──


def _ceiling(source: str, rel: str = "tests/unit/test_x.py") -> str:
    return str(sc._ceiling(rel, ast.parse(source)))


def test_ceiling_is_real_with_a_subprocess_integration_with_a_disk_unit_otherwise() -> None:
    assert _ceiling("import subprocess\nsubprocess.run(['x'])\n") == "real"
    assert _ceiling("from subprocess import run\nrun(['x'])\n") == "real"
    assert _ceiling("def test_a(tmp_path):\n    pass\n") == "integration"
    assert _ceiling("def test_a():\n    pass\n") == "unit"
    assert _ceiling("def test_a():\n    pass\n", "tests/e2e/test_x.py") == "real"


def test_declaring_real_in_a_file_without_subprocess_does_not_inflate_the_proof(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/unit/test_h.py",
        "import pytest\n\n"
        'pytestmark = pytest.mark.covers("hook:tool-policy", level="real")\n\n\n'
        'def test_h() -> None:\n    assert "tool-policy"\n',
    )
    claims, errors = sc.scan_claims(tmp_path)
    assert not errors, errors
    assert [(c.declared, c.effective) for c in claims] == [("real", "unit")]
    registry = sc.build_registry({"hook": ["tool-policy"]}, claims)
    assert registry["hook:tool-policy"].status == "under-level"


def test_a_side_effect_unit_covered_in_a_subprocess_is_covered(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/unit/test_h.py",
        "import subprocess\nimport pytest\n\n"
        'pytestmark = pytest.mark.covers("hook:tool-policy", level="real")\n\n\n'
        'def test_h() -> None:\n    subprocess.run(["x", "tool-policy"], check=False)\n',
    )
    claims, errors = sc.scan_claims(tmp_path)
    assert not errors, errors
    assert sc.build_registry({"hook": ["tool-policy"]}, claims)["hook:tool-policy"].status == "covered"


def test_dropping_below_the_minimal_level_is_a_loss() -> None:
    real = sc.Claim("hook:tool-policy", "real", "real", "tests/unit/test_h.py")
    weak = sc.Claim("hook:tool-policy", "unit", "unit", "tests/unit/test_h.py")
    units = {"hook": ["tool-policy"]}
    lost = sc.lost_units(sc.build_registry(units, [real]), sc.build_registry(units, [weak]))
    assert len(lost) == 1 and "hook:tool-policy" in lost[0]


# ── gardes de forme des revendications ──


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('pytestmark = pytest.mark.covers("cli:flow run")\n', "sans que le fichier la nomme"),
        ('NAME = "x"\npytestmark = pytest.mark.covers(NAME)\n', "littéral"),
        ('pytestmark = pytest.mark.covers("cli:flow run", level="bogus")\n', "level"),
        ('pytestmark = pytest.mark.covers("cli:flow run", mode="x")\n', "inconnu"),
        ("pytestmark = pytest.mark.covers()\n", "sans unité"),
    ],
)
def test_malformed_or_uncorroborated_claims_are_refused(tmp_path: Path, body: str, fragment: str) -> None:
    _write(tmp_path, "tests/unit/test_m.py", "import pytest\n\n" + body)
    _, errors = sc.scan_claims(tmp_path)
    assert any(fragment in e for e in errors), errors


def test_a_claim_outside_the_paths_ci_runs_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/integration/test_z.py",
        'import pytest\n\npytestmark = pytest.mark.covers("cli:flow run")\nX = ["flow", "run"]\n',
    )
    _, errors = sc.scan_claims(tmp_path)
    assert any("hors des chemins" in e for e in errors), errors


def test_an_unreadable_test_file_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    _write(tmp_path, "tests/unit/test_broken.py", "def (:\n")
    _, errors = sc.scan_claims(tmp_path)
    assert any("illisible" in e for e in errors), errors


def test_a_claim_for_a_missing_unit_is_refused() -> None:
    claims = [sc.Claim("cli:flow runn", "unit", "unit", "tests/unit/test_a.py")]
    errors = sc.guard_errors(_ROOT, _real_units(), claims)
    assert any("cli:flow runn" in e and "n'existe pas" in e for e in errors), errors


# ── critère 2 : anti-pourrissement sur l'arbre réel ──


_UNITS_CACHE: dict[str, list[str]] = {}


def _real_units() -> dict[str, list[str]]:
    if not _UNITS_CACHE:
        _UNITS_CACHE.update(sc.enumerate_units(_ROOT))
    return _UNITS_CACHE


def test_every_class_is_non_empty() -> None:
    units = _real_units()
    assert set(units) == set(sc.CLASSES)
    for cls in sc.CLASSES:
        assert units[cls], cls


def test_enumerated_counts_equal_an_independent_recount_of_the_sources() -> None:
    units = _real_units()
    counts = sc.recount(_ROOT)
    assert {cls: len(units[cls]) for cls in sc.CLASSES} == counts


def test_the_cli_tree_matches_a_direct_walk_of_the_typer_app() -> None:
    """Le décompte ne dépend pas de l'énumérateur du script : l'application elle-même."""
    import typer.main

    from grimoire.cli.app import app

    def leaves(command: object, prefix: tuple[str, ...]) -> int:
        if not hasattr(command, "list_commands"):
            return 1
        kids = [
            c
            for n in command.list_commands(None)  # type: ignore[attr-defined]
            if (c := command.get_command(None, n)) is not None and not getattr(c, "hidden", False)  # type: ignore[attr-defined]
        ]
        return sum(leaves(k, prefix) for k in kids) if kids else (1 if prefix else 0)

    assert leaves(typer.main.get_command(app), ()) == len(_real_units()["cli"])


def test_a_broken_enumerator_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    units = {**_real_units(), "genre": []}
    errors = sc.guard_errors(_ROOT, units, [])
    assert any("classe genre vide" in e for e in errors), errors
    off_by_one = {**_real_units(), "cli": _real_units()["cli"][:-1]}
    errors = sc.guard_errors(_ROOT, off_by_one, [])
    assert any("recomptage indépendant" in e for e in errors), errors


def test_the_enumeration_refuses_to_measure_another_tree(tmp_path: Path) -> None:
    fake = tmp_path / "t"
    (fake / "src" / "grimoire").mkdir(parents=True)
    (fake / "src" / "grimoire" / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        sc.enumerate_units(fake)


def test_the_real_tree_passes_its_own_guards_and_every_class_protects_something() -> None:
    registry, errors = sc.registry_for(_ROOT, None)
    assert not errors, errors
    for cls in sc.CLASSES:
        assert any(u.cls == cls and u.status == "covered" for u in registry.values()), (
            f"aucune unité {cls} couverte : le cliquet ne protégerait rien dans cette classe"
        )


@pytest.mark.skipif(not (_ROOT / ".git").exists(), reason="historique git requis")
def test_the_real_tree_does_not_lose_a_unit_against_head() -> None:
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(_ROOT), "--check", "--base", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_marker_is_registered() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("covers(") for m in markers)


# ── critère 3 : plus de liste manuelle périmée ──


def test_shell_runners_only_list_test_files_that_exist() -> None:
    """Un script shell qui cite ``tests/…py`` ne peut pas citer un fichier disparu."""
    pattern = re.compile(r"tests/[A-Za-z0-9_./-]+\.py")
    missing: list[str] = []
    scripts = [*(_ROOT / "tests").rglob("*.sh"), *(_ROOT / "scripts").rglob("*.sh")]
    for script in scripts:
        for cited in sorted(set(pattern.findall(script.read_text(encoding="utf-8")))):
            if not (_ROOT / cited).is_file():
                missing.append(f"{script.relative_to(_ROOT).as_posix()} -> {cited}")
    assert not missing, missing


def test_the_stale_coverage_runner_is_gone() -> None:
    assert not (_ROOT / "tests" / "run-coverage.sh").exists()


# ── câblage CI ──


def test_ci_runs_the_ratchet_against_the_base_of_the_pull_request() -> None:
    ci = _ROOT / ".github" / "workflows" / "ci-sdk.yml"
    if not ci.is_file():
        pytest.skip("dépôt source uniquement")
    job = YAML(typ="safe").load(ci.read_text(encoding="utf-8"))["jobs"]["surface-coverage"]
    assert job.get("if") == "github.event_name == 'pull_request'"
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "scripts/check-surface-coverage.py --check --base" in run
    assert "${{" not in run, "la référence de base passe par l'environnement, jamais interpolée dans le script"
