"""Lecture YAML qui tolère une écriture concurrente passagère.

Le board du standard — ``_grimoire/standard/task-board.yaml`` — est réécrit à
chaque transition de tâche, et lu en boucle par le tick d'orchestration, les
hooks et le cockpit. Une écriture passée par
:func:`grimoire.core.fsio.atomic_write` ne laisse jamais de fichier à moitié
écrit, mais la garantie ne couvre que les écrivains à jour : un lecteur peut
encore tomber sur le fichier qu'un outil d'une version antérieure réécrit en
place. Relire après un court délai suffit — la fenêtre d'un ``os.replace`` est
de l'ordre de la microseconde, pas de la seconde.

Une erreur qui survit aux tentatives reste une erreur : elle est levée par
:func:`load_yaml_with_retry`, ou comptée comme un document illisible par
:func:`load_yaml_mapping_or_empty`. Jamais de YAML vide rendu en silence.

``ruamel`` est importé à l'appel, pas au chargement du module : ``standard_state``
importe ce module sur le chemin des hooks, qui tourne à chaque appel d'outil.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

__all__ = [
    "TRANSIENT_READ_ATTEMPTS",
    "TRANSIENT_READ_DELAY_S",
    "load_yaml_mapping_or_empty",
    "load_yaml_with_retry",
]

#: Une tentative initiale puis trois relectures. La fenêtre visée est celle d'un
#: renommage ; au-delà, le fichier est cassé pour de vrai, pas en cours d'écriture.
TRANSIENT_READ_ATTEMPTS = 4
#: 20 ms entre deux tentatives : 60 ms au total, large devant un ``os.replace``
#: et court devant le budget d'un hook qui lit le board à chaque outil.
TRANSIENT_READ_DELAY_S = 0.02


def _parse(path: Path) -> Any:
    """Le document YAML de *path* (``ruamel`` importé ici, pas au chargement du module)."""
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(path)


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Ce qu'une écriture concurrente peut faire lever à un lecteur : I/O et analyse.

    ``ValueError`` y figure comme dans les lecteurs qu'il remplace : ``ruamel``
    signale certaines structures invalides par un ``ValueError`` nu.
    """
    from ruamel.yaml.error import YAMLError

    return (OSError, ValueError, YAMLError)


def load_yaml_with_retry(path: str | Path, *, attempts: int | None = None, delay: float | None = None) -> Any:
    """Charge *path* en YAML, en retentant une erreur d'analyse ou de lecture passagère.

    Lève la dernière erreur quand elle survit à *attempts* tentatives (défaut
    :data:`TRANSIENT_READ_ATTEMPTS`), espacées de *delay* secondes (défaut
    :data:`TRANSIENT_READ_DELAY_S`). Une erreur persistante n'est pas avalée :
    c'est le fichier qui est cassé, pas l'instant qui est mauvais.
    """
    tries = TRANSIENT_READ_ATTEMPTS if attempts is None else max(1, attempts)
    pause = TRANSIENT_READ_DELAY_S if delay is None else delay
    errors = _transient_errors()
    attempt = 0
    while True:
        try:
            return _parse(Path(path))
        except errors:
            attempt += 1
            if attempt >= tries:
                raise
            time.sleep(pause)


def load_yaml_mapping_or_empty(
    path: str | Path, *, attempts: int | None = None, delay: float | None = None
) -> dict[str, Any]:
    """Le mapping YAML de *path* — ``{}`` s'il est absent ou reste illisible après les tentatives.

    Pour les frontières qui ne doivent jamais lever (hook, migration) : les
    tentatives écartent l'écriture concurrente, et ce qui échoue encore est
    traité comme une absence par l'appelant — à lui de dire « illisible » quand
    la distinction compte (``standard_state._board_problem``).
    """
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        data = load_yaml_with_retry(target, attempts=attempts, delay=delay)
    except _transient_errors():
        return {}
    return data if isinstance(data, dict) else {}
