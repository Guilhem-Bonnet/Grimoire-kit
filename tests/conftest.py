"""Shared pytest fixtures for Grimoire-kit tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"


# ── Isolation de l'état utilisateur ───────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _isolate_cockpit_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Empêche la suite de tests d'écrire dans le vrai ``~/.grimoire/cockpit``.

    ``grimoire init`` enregistre le projet créé auprès du cockpit, sauf si
    ``GRIMOIRE_NO_COCKPIT`` est posé. Chaque test qui lance ``init`` sans ce
    garde-fou ajoutait donc une entrée au registre de la machine, pointant vers
    un répertoire pytest éphémère. Constaté sur un poste de développement :
    **10 378 entrées mortes** pour un seul projet réel.

    Le garde-fou vit ici et non dans chaque fichier de test, parce qu'un
    garde-fou qu'il faut penser à écrire finit toujours par manquer quelque
    part — c'est précisément ce qui s'est produit.
    """
    home = tmp_path_factory.mktemp("cockpit-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
        yield


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute path to the Grimoire-kit project root."""
    return ROOT


@pytest.fixture(scope="session")
def tools_dir() -> Path:
    """Absolute path to framework/tools/."""
    return TOOLS


@pytest.fixture
def tmp_grimoire_memory(tmp_path: Path) -> Path:
    """Temporary project with _grimoire/_memory/ directory."""
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def tmp_grimoire_project(tmp_path: Path) -> Path:
    """Temporary project with standard Grimoire directory structure."""
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-output").mkdir(parents=True)
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def init_project(tmp_path: Path) -> Path:
    """Fully initialised Grimoire project with valid config."""
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-output").mkdir(parents=True)
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "test-project"\n'
        'memory:\n  backend: "local"\n'
        'agents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    return tmp_path


# ── Markers ───────────────────────────────────────────────────────────────────
# Markers are registered in pyproject.toml [tool.pytest.ini_options].
# Usage:
#   @pytest.mark.slow          — long-running tests
#   @pytest.mark.integration   — tests requiring external services
#   @pytest.mark.regression    — known regression tests
