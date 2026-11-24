"""Ce qui dit qu'un dépôt a une CI : marqueurs de premier niveau, sans parcours récursif.

Un seul fichier de ``.github/workflows/`` n'en est pas un : ``copilot-setup-steps.yml``.
Il prépare l'environnement de l'agent cloud Copilot (l'émetteur Copilot l'écrit,
#1280 P3) et ne lance aucun test. Le compter ferait passer pour « équipé d'une CI »
tout projet où Copilot est activé : le hook SessionStart le croirait gouverné,
``grimoire init`` ne proposerait plus ``--lite``. Le fichier est écarté par son nom,
qu'il vienne du kit ou du projet : dans les deux cas c'est l'environnement de l'agent.

Module sans dépendance : il est importé sur le chemin des hooks.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOWS_DIR = ".github/workflows"
#: Nom imposé par Copilot au workflow d'environnement de son agent cloud.
COPILOT_SETUP_STEPS = "copilot-setup-steps.yml"
#: Marqueurs de CI autres que ``.github/workflows`` (qui se juge fichier par fichier).
_OTHER_CI_MARKERS: tuple[str, ...] = (
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    ".travis.yml",
    "Jenkinsfile",
)


def github_ci_workflows(root: Path) -> tuple[str, ...]:
    """Les workflows GitHub Actions de *root* qui sont une CI (chemins POSIX relatifs, triés)."""
    workflows = root / WORKFLOWS_DIR
    try:
        entries = sorted(workflows.iterdir()) if workflows.is_dir() else []
    except OSError:
        return ()
    return tuple(
        f"{WORKFLOWS_DIR}/{entry.name}"
        for entry in entries
        if entry.suffix in {".yml", ".yaml"} and entry.name != COPILOT_SETUP_STEPS and entry.is_file()
    )


def _workflows_dir_is_ci(root: Path) -> bool:
    """``.github/workflows`` compte comme avant (sa seule présence), sauf s'il ne porte que ``copilot-setup-steps.yml``."""
    workflows = root / WORKFLOWS_DIR
    if not workflows.exists():
        return False
    try:
        names = [entry.name for entry in workflows.iterdir()] if workflows.is_dir() else []
    except OSError:
        return True
    return not names or any(name != COPILOT_SETUP_STEPS for name in names)


def has_ci(root: Path) -> bool:
    """Vrai si *root* porte une configuration de CI reconnue."""
    return any((root / marker).exists() for marker in _OTHER_CI_MARKERS) or _workflows_dir_is_ci(root)
