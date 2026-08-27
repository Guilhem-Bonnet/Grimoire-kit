"""La suite ne doit jamais écrire dans le dépôt git ambiant.

Git exporte ``GIT_DIR`` et compagnie dans l'environnement de tout hook. Quand la
suite tourne depuis un ``git commit`` — c'est-à-dire depuis le gate pre-commit —
un ``git init`` dans un ``tmp_path`` crée le dépôt à ``GIT_DIR`` au lieu du
répertoire demandé, et les tests de hooks réécrivent ceux du dépôt réel.

Constaté en vrai le 2026-08-27 : ``tests/unit/cli/test_cmd_hooks.py`` a remplacé
``pre-commit``, ``commit-msg``, ``post-commit`` et ``post-checkout`` du dépôt de
travail par les talons de sa fixture. Le gate installé s'est retrouvé désarmé par
la suite de tests qu'il venait de lancer, sans que rien ne le signale.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

#: Le contrat est énoncé ici plutôt qu'importé du conftest : le test doit
#: tomber si quelqu'un raccourcit la liste côté implémentation.
GIT_REDIRECT_VARS = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def test_git_redirect_vars_are_absent() -> None:
    """Aucune variable de redirection git ne survit à l'isolation de session."""
    present = [var for var in GIT_REDIRECT_VARS if var in os.environ]
    assert present == [], f"variables de redirection git encore posées : {present}"


def test_conftest_covers_every_declared_var() -> None:
    """L'isolation du conftest doit couvrir exactement ce contrat."""
    conftest = Path(__file__).resolve().parents[1] / "conftest.py"
    source = conftest.read_text(encoding="utf-8")
    manquantes = [var for var in GIT_REDIRECT_VARS if f'"{var}"' not in source]
    assert manquantes == [], f"non couvertes par tests/conftest.py : {manquantes}"


def test_git_init_creates_the_repo_where_it_is_asked(tmp_path: Path) -> None:
    """`git init <dir>` doit créer le dépôt dans <dir>, pas ailleurs.

    C'est l'assertion qui tombe quand ``GIT_DIR`` fuit : git initialise alors le
    dépôt pointé par la variable et ``<dir>/.git`` n'existe jamais.
    """
    target = tmp_path / "projet"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    assert (target / ".git").is_dir()


def test_rev_parse_resolves_to_the_sandbox(tmp_path: Path) -> None:
    """`git rev-parse --git-dir` depuis le bac à sable ne doit pas viser dehors."""
    target = tmp_path / "projet"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    resolved = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=target, capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert Path(resolved).resolve() == (target / ".git").resolve()


def test_a_leaked_git_dir_would_be_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le garde-fou est vérifiable : en le levant, le dégât redevient visible.

    Sans ce test, rien ne prouverait que l'isolation sert à quelque chose — un
    garde qu'on ne sait pas faire échouer ne prouve rien.
    """
    sentinel = tmp_path / "depot-temoin"
    sentinel.mkdir()
    subprocess.run(["git", "init", "-q", str(sentinel)], check=True)
    hooks = sentinel / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/usr/bin/env bash\necho intact\n", encoding="utf-8")

    monkeypatch.setenv("GIT_DIR", str(sentinel / ".git"))
    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    subprocess.run(["git", "init", "-q", str(ailleurs)], check=True)

    # GIT_DIR détourne l'init : le dépôt demandé n'est pas créé.
    assert not (ailleurs / ".git").exists()
