"""Gate ``codegraph_fresh`` — un graphe de code périmé ne soutient pas une revue.

Un projet qui a indexé son code (reçu écrit par
:func:`grimoire.codegraph.freshness.record_index`) s'engage à ce que le
graphe décrive le code présent. Aux états review/accepted/released, le gate
recalcule le verdict :

- ``current`` : rien à dire ;
- ``stale`` : erreur, avec les fichiers ajoutés, modifiés et supprimés, et
  la commande qui répare ;
- ``unverified`` avec un reçu qui existe mais ne se lit pas : erreur (un
  reçu illisible ne vaut pas un graphe frais).

Sans reçu (``no_receipt``) le projet n'a jamais indexé son code : le gate ne
s'applique pas, et rien n'est affirmé. Appelé depuis
:mod:`grimoire.core.standard_checks.gate_review_checks`.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_checks.base import StandardCheck

__all__ = ["codegraph_fresh_checks"]

_REMEDY = "remède : indexer à nouveau (outil MCP grimoire_codegraph_index)"


def codegraph_fresh_checks(root: Path) -> tuple[StandardCheck, ...]:
    from grimoire.codegraph.freshness import RECEIPT_PATH, check_freshness

    fresh = check_freshness(root)
    if fresh.verdict == "current" or fresh.reason == "no_receipt":
        return ()
    if fresh.verdict == "stale":
        touched = ", ".join(
            [f"+{p}" for p in fresh.added] + [f"~{p}" for p in fresh.modified] + [f"-{p}" for p in fresh.removed]
        )
        message = f"Le graphe de code est périmé : {touched}. {_REMEDY}."
    else:
        message = f"Le graphe de code est invérifiable ({fresh.reason}). {_REMEDY}."
    return (StandardCheck(id="gate.codegraph_fresh", severity="error", message=message, path=RECEIPT_PATH),)
