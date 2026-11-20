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


@pytest.mark.parametrize("name", ["ci-sdk.yml", "ci-validate.yml"])
def test_le_job_de_perimetre_ne_reduit_jamais_main_ni_une_release(name: str) -> None:
    job = _workflow(name)["jobs"]["changes"]
    decide = next(s for s in job["steps"] if s.get("id") == "scope")
    always = decide["env"]["ALWAYS_FULL"]
    assert "github.event_name != 'pull_request'" in always and "chore: release" in always
    assert "scripts/ci-scope.py" in decide["run"]


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
