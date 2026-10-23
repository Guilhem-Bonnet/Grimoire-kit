"""Lot de tâches nommé pour le banc (B-07, lot M) : une liste déclarée au lieu d'un tirage.

``sample_tasks`` tire 5 exercices par langue (graine 551) : impossible d'y
désigner les tâches où le ``nu`` échoue ou varie, et ``--subset`` (B-00) ne
fait que filtrer CE tirage. Ce module charge un fichier
``grimoire.bench.task-lot.v1`` qui nomme les tâches du catalogue polyglotte à
jouer, dans l'ordre déclaré, et refuse avant toute dépense :

- un fichier mal formé (liste vide, identifiant qui n'est pas ``<langue>/<slug>``,
  doublon) ;
- une tâche absente du catalogue (exercice inexistant, langue non chargée) ;
- un clone polyglotte à un autre commit que celui déclaré (``polyglot_commit``) :
  le clone du banc suit la tête du dépôt amont, un lot nommé doit désigner les
  mêmes énoncés et les mêmes tests cachés d'une campagne à l'autre.

Aucune dépendance à ``three_arms`` : il ne demande aux tâches qu'un ``task_id``.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "grimoire.bench.task-lot.v1"
_TASK_ID = re.compile(r"^[a-z0-9+#-]+/[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class TaskLot:
    """Un lot déclaré : identifiants dans l'ordre, commit polyglotte attendu (ou ``None``)."""

    tasks: tuple[str, ...]
    polyglot_commit: str | None


def load_task_lot(path: Path) -> TaskLot:
    """Lit et valide un fichier de lot ; ``ValueError`` sur toute forme inattendue."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} : JSON illisible ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path} : « schema » doit valoir {SCHEMA!r}")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks or not all(isinstance(t, str) for t in tasks):
        raise ValueError(f"{path} : « tasks » doit être une liste non vide d'identifiants")
    malformed = [t for t in tasks if not _TASK_ID.match(t)]
    if malformed:
        raise ValueError(f"{path} : identifiant(s) hors forme <langue>/<slug> : {', '.join(malformed)}")
    duplicated = sorted({t for t in tasks if tasks.count(t) > 1})
    if duplicated:
        raise ValueError(f"{path} : tâche(s) déclarée(s) deux fois : {', '.join(duplicated)}")
    commit = data.get("polyglot_commit")
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)):
        raise ValueError(f"{path} : « polyglot_commit » doit être un sha complet de 40 caractères hexadécimaux")
    return TaskLot(tasks=tuple(tasks), polyglot_commit=commit)


def select_tasks(catalog: Sequence[Any], lot: TaskLot) -> list[Any]:
    """Les tâches du catalogue nommées par le lot, dans l'ordre du lot ; arrêt si l'une manque."""
    by_id = {t.task_id: t for t in catalog}
    absent = [t for t in lot.tasks if t not in by_id]
    if absent:
        raise SystemExit(f"lot de tâches : absente(s) du catalogue ({', '.join(absent)}), aucun run lancé")
    return [by_id[t] for t in lot.tasks]


def verify_bench_commit(bench_root: Path, lot: TaskLot) -> str | None:
    """Commit du clone polyglotte ; arrêt s'il diffère du commit déclaré par le lot.

    Sans ``polyglot_commit`` déclaré, rend le commit lu (ou ``None`` si illisible)
    sans rien refuser. Avec, un commit illisible est un refus : on ne devine pas.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=bench_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    actual = result.stdout.strip() if result.returncode == 0 else None
    if lot.polyglot_commit is not None and actual != lot.polyglot_commit:
        raise SystemExit(
            f"lot de tâches : clone polyglotte à {actual or 'commit illisible'}, "
            f"le lot exige {lot.polyglot_commit} ; aucun run lancé"
        )
    return actual


def resolve_task_lot(path: Path, catalog: Sequence[Any], bench_root: Path) -> tuple[list[Any], str | None]:
    """Charge le lot, vérifie le clone, sélectionne ; tout refus est un ``SystemExit`` avant dépense.

    Rend les tâches dans l'ordre déclaré et le commit du clone polyglotte (pour ``selection.json``).
    """
    try:
        lot = load_task_lot(path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"lot de tâches : {exc}") from None
    commit = verify_bench_commit(bench_root, lot)
    return select_tasks(catalog, lot), commit
