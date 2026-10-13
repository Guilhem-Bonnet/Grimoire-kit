"""Gardes du dépôt de tâche du banc à trois bras (#694, W1-08a).

Extraites de ``three_arms.py`` (déjà au-delà du seuil de taille, et ``scripts/``
n'est couvert par aucune garde de ratchet) : ``three_arms.py`` les importe et
les ré-exporte. Ce module n'importe rien de ``src/grimoire`` ni de
``three_arms`` : les fonctions ne demandent à la tâche que ``task_id``,
``exercise_dir`` et ``test_files``.
"""

from __future__ import annotations

import contextlib
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ContaminatedTaskRepoError(RuntimeError):
    """Le dépôt préparé n'est pas dans l'état initial attendu (#694, W1-08a)."""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
    )


def assert_task_repo_clean(task: Any, repo_dir: Path) -> None:
    """Refuse un dépôt de tâche contaminé, avant tout appel au modèle.

    Trois conditions, toutes fail-closed : aucun fichier de test caché de
    ``task.test_files`` ne doit être présent sur le disque (suivi, non suivi
    ou ignoré par git : l'agent le lirait dans tous les cas), aucun ne doit
    être suivi par git (même absent du disque : il reste dans l'historique),
    et l'historique doit compter exactement un commit (l'état initial). Un
    échec de ``git`` lui-même est traité comme une contamination : on ne devine
    pas.
    """
    present = sorted(rel for rel in task.test_files if (repo_dir / rel).exists())
    if present:
        raise ContaminatedTaskRepoError(
            f"{repo_dir} : test(s) caché(s) présent(s) dans le dépôt : {', '.join(present)}"
        )
    # ``-z`` : sans lui, git met entre guillemets (octal) les noms non ASCII.
    ls = _git(["ls-files", "-z"], repo_dir)
    if ls.returncode != 0:
        raise ContaminatedTaskRepoError(f"{repo_dir} : `git ls-files` a échoué ({ls.stderr.strip()[-200:]})")
    tracked = {name for name in ls.stdout.split("\0") if name}
    leaked = sorted(tracked & set(task.test_files))
    if leaked:
        raise ContaminatedTaskRepoError(f"{repo_dir} : test(s) caché(s) suivi(s) par git : {', '.join(leaked)}")
    count = _git(["rev-list", "--count", "HEAD"], repo_dir)
    if count.returncode != 0 or count.stdout.strip() != "1":
        raise ContaminatedTaskRepoError(
            f"{repo_dir} : l'historique doit compter exactement 1 commit, trouvé "
            f"{count.stdout.strip() or '?'} (code {count.returncode})"
        )


def _make_writable_and_retry(func: Callable[[str], object], path: str, _exc: BaseException) -> None:
    """``onexc`` de ``shutil.rmtree`` : rend l'entrée (et son dossier) inscriptible, puis réessaie.

    Les objets git sont en 0444 : sous Windows ``rmtree`` y échoue en
    ``PermissionError`` ; sous POSIX l'échec vient d'un dossier en lecture seule.
    """
    target = Path(path)
    for entry in (target.parent, target):
        with contextlib.suppress(OSError):
            entry.chmod(entry.stat().st_mode | stat.S_IWRITE | stat.S_IRUSR | (stat.S_IXUSR if entry.is_dir() else 0))
    func(path)


def reset_run_dir(run_dir: Path) -> None:
    """Supprime sans condition le dossier de run avant de le (re)préparer.

    Elle ne distingue pas un résidu de run interrompu d'un run terminé :
    l'appelant (``main``) signale l'effacement d'un run déjà enregistré dans
    ``results.jsonl``. ``assert_task_repo_clean`` reste le filet de sécurité
    après préparation. Lève ``RuntimeError`` si le dossier survit : on ne
    rejoue jamais dessus.
    """
    if run_dir.exists():
        shutil.rmtree(run_dir, onexc=_make_writable_and_retry)
    if run_dir.exists():
        raise RuntimeError(f"{run_dir} : impossible d'effacer le dossier de run existant")


def _read_instructions(exercise_dir: Path) -> str:
    docs_dir = exercise_dir / ".docs"
    parts = []
    main = docs_dir / "instructions.md"
    if main.is_file():
        parts.append(main.read_text(encoding="utf-8"))
    appendix = docs_dir / "instructions.append.md"
    if appendix.is_file():
        parts.append(appendix.read_text(encoding="utf-8"))
    return "\n\n".join(parts).strip() + "\n"


def prepare_task_repo(task: Any, dest: Path, *, include_tests: bool = False) -> None:
    """Construit un dépôt de tâche jetable pour ``task`` dans ``dest``.

    Copie l'énoncé (``TASK.md``) et les fichiers stub/support de l'exercice ;
    les fichiers de test sont exclus par défaut (« tests cachés à l'agent »),
    tout comme tout ce qui vit sous ``.meta/`` et ``.docs/`` (solution de
    référence, générateurs). ``git init`` pour que l'agent puisse diffs/commits
    s'il le souhaite.

    Lot J (#694) : refuse un dossier non vide pour éviter la contamination par
    des tests cachés d'une préparation antérieure.
    """
    # Refuse un dossier non vide pour éviter la contamination
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(
            f"Le dossier de destination {dest} existe déjà et n'est pas vide. "
            f"Impossible de préparer la tâche de façon isolée (risque de contamination)."
        )
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "TASK.md").write_text(_read_instructions(task.exercise_dir), encoding="utf-8")

    test_set = set(task.test_files)
    for item in sorted(task.exercise_dir.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(task.exercise_dir)
        rel_parts = rel.parts
        if rel_parts[0] in (".meta", ".docs"):
            continue
        rel_str = rel.as_posix()
        if rel_str in test_set and not include_tests:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)

    subprocess.run(["git", "init", "-q", "."], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=bench@grimoire-kit.local",
            "-c",
            "user.name=grimoire-bench",
            "commit",
            "-q",
            "-m",
            "task: état initial",
        ],
        cwd=dest,
        check=True,
    )


def run_dir_for(workspace: Path, task: Any, arm: str, run_index: int) -> Path:
    return workspace / "tasks" / str(task.task_id).replace("/", "__") / arm / f"run{run_index}"
