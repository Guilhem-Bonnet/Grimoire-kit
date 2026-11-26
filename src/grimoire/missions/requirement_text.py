"""Le texte d'une exigence validée, gardé **pour l'affichage seulement** (dérive détaillée de ``cadrage trace``).

La validation scelle l'empreinte du texte (:func:`~grimoire.missions.requirement_gate.fingerprint`) ; l'empreinte
reste la seule référence de la validation. Le texte rendu ici est rangé à côté, dans la même entrée du journal
d'autorité (donc sous la même chaîne et les mêmes protections), pour pouvoir montrer *quoi* a changé. Aucune décision
ne le lit : qui peut valider, et ce qui vaut validation, ne changent pas.

Module pur : aucune lecture, aucune écriture, aucun import du gate.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable

__all__ = ["DIFF_MAX_LINES", "NO_TEXT_NOTE", "TEXT_MAX", "diff_lines", "validated_text"]

#: Le texte gardé tient dans une entrée de journal : au-delà, il est tronqué (l'empreinte, elle, porte tout).
TEXT_MAX = 2000
#: Lignes de diff montrées au plus ; le reste se compte.
DIFF_MAX_LINES = 12
NO_TEXT_NOTE = "validée avant la conservation du texte validé : la dérive est signalée sans détail"
TRUNCATED_NOTE = f"texte validé tronqué : la différence peut se trouver au-delà de {TEXT_MAX} caractères"
SAME_TEXT_NOTE ="aucune différence dans le texte affiché (périmètre ou identifiant modifié)"


def validated_text(title: str, priority: str, criteria: Iterable[str]) -> str:
    """Le texte lisible de ce que la MOA valide : priorité et formulation, puis chaque critère (ordre indifférent,
    comme l'empreinte), une ligne chacun. Borné à :data:`TEXT_MAX` caractères."""
    lines = [f"[{priority or '?'}] {title.strip()}"]
    lines.extend(f"critère : {c.strip()}" for c in sorted(c.strip() for c in criteria))
    text = "\n".join(lines)
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


def diff_lines(validated: str, current: str) -> list[str]:
    """Le diff court « validé → courant » : des lignes ``- ancien`` / ``+ nouveau``, au plus :data:`DIFF_MAX_LINES`
    (une dernière ligne compte le reste). Vide si les deux textes sont identiques."""
    rows = [
        row
        for row in difflib.unified_diff(validated.splitlines(), current.splitlines(), lineterm="", n=0)
        if row[:1] in "+-" and not row.startswith(("+++", "---"))
    ]
    rows = [f"{row[0]} {row[1:]}" for row in rows]
    if len(rows) > DIFF_MAX_LINES:
        rest = len(rows) - DIFF_MAX_LINES
        rows = [*rows[:DIFF_MAX_LINES], f"… {rest} ligne{'s' if rest > 1 else ''} de plus"]
    return rows
