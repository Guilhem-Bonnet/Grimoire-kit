"""Périmètre de CI d'une PR : seule une PR entièrement documentaire évite la suite complète."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "ci-scope.py"
_WORKFLOWS = _REPO / ".github" / "workflows"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="dépôt source uniquement")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_scope_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(name: str) -> dict[str, Any]:
    data: dict[str, Any] = YAML(typ="safe").load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("files", "full"),
    [
        (["docs/guide.md"], False),
        (["docs/a/b/c.md", "changelog.d/x.md", "README.md", "README.fr.md"], False),
        ([".github/PULL_REQUEST_TEMPLATE.md", "CONTRIBUTING.md"], False),
        (["docs/guide.md", "src/grimoire/cli/app.py"], True),
        ([".github/workflows/ci-sdk.yml"], True),
        (["mkdocs.yml"], True),
        (["framework/agents/x.md"], True),
        (["src/docs/x.md"], True),
        (["web/README.md"], True),
        ([], True),
        (["", "  "], True),
    ],
)
def test_decision(files: list[str], full: bool) -> None:
    assert _module().needs_full_ci(files) is full


def test_git_en_echec_rend_la_ci_complete(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--base", "nexistepas", "--head", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        check=True,
    )
    assert done.stdout.strip() == "full=true"


def test_diff_reel_sur_un_depot_jetable(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x\n", encoding="utf-8")
    git("add", "docs/a.md")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "doc")

    def scope() -> str:
        done = subprocess.run(
            [sys.executable, str(_SCRIPT), "--base", "HEAD^1", "--head", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
            check=True,
        )
        return done.stdout.strip()

    assert scope() == "full=false"
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "app.py")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "code")
    assert scope() == "full=true"


def test_un_renommage_de_src_vers_docs_rend_la_ci_complete(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=tmp_path, check=True, capture_output=True
        )

    git("init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("".join(f"ligne_{i} = {i}\n" for i in range(40)), encoding="utf-8")
    git("add", "src/mod.py")
    git("commit", "-q", "-m", "base")
    (tmp_path / "docs").mkdir()
    git("mv", "src/mod.py", "docs/mod.py")
    git("commit", "-q", "-m", "déplacement")
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--base", "HEAD^1", "--head", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        check=True,
    )
    assert done.stdout.strip() == "full=true", done.stderr


@pytest.mark.parametrize("name", ["ci-sdk.yml", "ci-validate.yml"])
def test_main_rejoue_tout_derriere_une_pr_documentaire(name: str) -> None:
    """Le filet d'une PR documentaire réduite est le push sur main : ses chemins doivent le déclencher."""
    triggers = _workflow(name).get("on") or _workflow(name)[True]
    push = {p.replace("/**", "/*") for p in triggers["push"]["paths"]}
    assert set(_module().DOC_ONLY_PATTERNS) <= push, set(_module().DOC_ONLY_PATTERNS) - push


@pytest.mark.parametrize("name", ["ci-sdk.yml", "ci-validate.yml"])
def test_seule_une_decision_explicite_reduit_la_ci(name: str) -> None:
    text = (_WORKFLOWS / name).read_text(encoding="utf-8")
    assert "outputs.full != 'true'" not in text and "outputs.full == 'false'" in text


@pytest.mark.parametrize("name", ["ci-sdk.yml", "ci-validate.yml"])
def test_le_job_de_perimetre_ne_reduit_jamais_main_et_ne_reduit_une_release_que_par_son_diff(name: str) -> None:
    job = _workflow(name)["jobs"]["changes"]
    decide = next(s for s in job["steps"] if s.get("id") == "scope")
    assert decide["env"]["ALWAYS_FULL"] == "${{ github.event_name != 'pull_request' }}"  # main, nuit, demande : tout
    release = decide["env"]["RELEASE"]
    assert "chore: release" in release and "release/" in release
    assert "scripts/ci-scope.py" in decide["run"] and "--release" in decide["run"]


@pytest.mark.parametrize(
    ("files", "full"),
    [
        (["CHANGELOG.md", "changelog.d/x.md", "version.txt", "src/grimoire/__version__.py"], False),
        (["registry/kit-file-hashes.json", "evals/ceremony-budget.yaml", "docs/a.md"], False),
        (["version.txt", "src/grimoire/core/x.py"], True),  # du code dans la release : suite complète
        (["version.txt", ".github/workflows/ci-sdk.yml"], True),
        ([], True),
    ],
)
def test_une_release_n_est_legere_que_si_son_diff_ne_touche_que_des_fichiers_de_release(
    files: list[str], full: bool
) -> None:
    assert _module().needs_full_ci(files, release=True) is full


def test_hors_release_un_fichier_de_release_reste_du_code() -> None:
    assert _module().needs_full_ci(["version.txt"]) is True
    assert _module().needs_full_ci(["registry/kit-file-hashes.json"]) is True


def test_aucun_declencheur_pr_des_workflows_requis_n_est_filtre_par_chemin() -> None:
    """Un workflow porteur d'un check requis, filtré par ``paths:``, laisse le check en attente."""
    for name in ("ci-sdk.yml", "ci-validate.yml", "rust-cores.yml", "agentic-standard.yml"):
        triggers = _workflow(name).get("on") or _workflow(name)[True]
        pr = triggers["pull_request"] or {}
        assert "paths" not in pr and "paths-ignore" not in pr, name


def test_codeql_ignore_exactement_les_chemins_documentaires() -> None:
    triggers = _workflow("codeql-analysis.yml").get("on") or _workflow("codeql-analysis.yml")[True]
    ignored = {p.replace("/**", "/*") for p in triggers["pull_request"]["paths-ignore"]}
    assert ignored == set(_module().DOC_ONLY_PATTERNS)
    assert "paths-ignore" not in (triggers["push"] or {})
