"""Lecture de ``results.jsonl`` du banc à trois bras (W1-08a).

Module à part : ``three_arms.py`` dépasse déjà le seuil de taille.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_results_rows(results_path: Path) -> list[dict[str, Any]]:
    """Lit ``results.jsonl`` ligne à ligne ; ignore les lignes vides (comme ``--report-only``).

    Une ligne non vide qui n'est pas du JSON (fichier tronqué) n'est pas
    ignorée en silence : ``ValueError`` qui nomme le fichier et la ligne.
    """
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(results_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{results_path} : ligne {number} illisible ({exc.msg}), fichier tronqué ?") from exc
    return rows
