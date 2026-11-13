"""``scripts/check-js-syntax.py`` — un fichier JavaScript suivi sous web/ qui ne s'analyse pas est refusé.

Les tests qui interrogent Node se sautent quand ``node`` est absent du poste ;
ceux qui prouvent que le garde échoue faute de verdict n'en ont pas besoin.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "check-js-syntax.py"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node absent : syntaxe JavaScript non vérifiable ici"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_js_syntax", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()


def _repo(root: Path, **files: str) -> Path:
    """Un dépôt git jetable dont ``files`` (chemin → contenu) sont suivis."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    return root


@needs_node
def test_every_tracked_js_file_under_web_passes_node_check() -> None:
    assert MOD.problems(REPO) == []


def test_the_real_tree_has_js_files_to_check() -> None:
    files = MOD.tracked_files(REPO)
    assert "web/workspace/shell.js" in files
    assert all(name.startswith("web/") and name.endswith((".js", ".mjs")) for name in files)


@needs_node
def test_a_js_file_with_a_syntax_error_is_refused(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        **{
            "web/ok.js": "const a = { b: 1 };\n",
            "web/espaces/ko.js": "const a = { b: 1 ;\n",
            "web/outil.mjs": "export default function () { return ; ]\n",
        },
    )
    found = MOD.problems(root)
    assert [line.split(" : ")[0] for line in found] == ["web/espaces/ko.js", "web/outil.mjs"]
    assert all("SyntaxError" in line for line in found)
    assert MOD.main(["--root", str(root)]) == 1


@needs_node
def test_scripts_and_es_modules_share_the_js_extension(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        **{
            "web/classique.js": "(function () { 'use strict'; window.X = 1; })();\n",
            "web/module.js": "import { a } from './autre.js';\nexport const b = a;\n",
        },
    )
    assert MOD.problems(root) == []
    assert MOD.main(["--root", str(root)]) == 0


@needs_node
def test_only_tracked_files_under_web_are_judged(tmp_path: Path) -> None:
    root = _repo(tmp_path, **{"web/ok.js": "const a = 1;\n", "ailleurs/ko.js": "const = ;\n"})
    (root / "web" / "brouillon.js").write_text("const = ;\n", encoding="utf-8")
    assert MOD.tracked_files(root) == ["web/ok.js"]
    assert MOD.problems(root) == []


def test_a_tree_without_tracked_js_is_not_a_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, **{"web/index.html": "<!doctype html>\n"})
    with pytest.raises(MOD.GuardError, match="non vérifiée"):
        MOD.problems(root)
    assert MOD.main(["--root", str(root)]) == 1
    assert "non rendue" in capsys.readouterr().err


def test_a_directory_that_is_not_a_repository_is_not_a_pass(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "ok.js").write_text("const a = 1;\n", encoding="utf-8")
    assert MOD.main(["--root", str(tmp_path)]) == 1


def test_a_missing_node_fails_the_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path / "depot", **{"web/ok.js": "const a = 1;\n"})
    files = MOD.tracked_files(root)
    (tmp_path / "vide").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))
    with pytest.raises(MOD.GuardError, match="`node` introuvable"):
        MOD.problems(root, files)
    # Sans `git` non plus, la liste des fichiers suivis est illisible : toujours pas un succès.
    assert MOD.main(["--root", str(root)]) == 1


@needs_node
def test_a_node_that_cannot_read_es_modules_fails_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans détection de module, chaque module de web/ serait un faux rouge : le garde le dit au lieu de les lister."""
    monkeypatch.setattr(MOD, "_check", lambda node, path: "SyntaxError: Cannot use import statement outside a module")
    with pytest.raises(MOD.GuardError, match="ne reconnaît pas un module ES"):
        MOD.find_node()


def test_ci_and_makefile_run_the_guard() -> None:
    """Le garde n'a de valeur que lancé : `make lint-js` l'appelle, le job lint de ci-sdk.yml appelle `make lint-js`."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    target = re.search(r"(?m)^lint-js:.*\n((?:\t.*\n)+)", makefile)
    assert target is not None, "cible `lint-js` absente du Makefile"
    recipe = target.group(1)
    assert "scripts/check-js-syntax.py" in recipe
    assert re.search(r"eslint@\$\(ESLINT_VERSION\)", recipe), "ESLint se lance à la version épinglée"
    assert re.search(r"(?m)^ESLINT_VERSION \?= \d+\.\d+\.\d+$", makefile), "version exacte d'ESLint, pas une plage"

    workflow = YAML(typ="safe").load((REPO / ".github" / "workflows" / "ci-sdk.yml").read_text(encoding="utf-8"))
    runs = [str(step.get("run", "")).strip() for step in workflow["jobs"]["lint"]["steps"]]
    assert "make lint-js" in runs, "le job lint de ci-sdk.yml lance `make lint-js`"
