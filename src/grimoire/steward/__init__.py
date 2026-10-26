"""L'intendant (steward) d'un projet : état des lieux en lecture seule, verdicts prouvés par du code (#876, lot 5).

Trois étages, aucun appel de LLM dans le kit :

1. :mod:`grimoire.steward.project_map` — la **carte** déterministe du projet (tâches du Mission
   Ledger, branches et worktrees, PR et issues ouvertes, signaux calculés), schéma
   ``grimoire-steward-map/v1`` ;
2. :mod:`grimoire.steward.verdicts` — la **validation** d'un fichier de verdicts produit par un
   agent (schéma ``grimoire-steward-verdicts/v1``) : chaque preuve typée est revérifiée contre la
   cible, git et GitHub ; un verdict dont une preuve ne se vérifie pas est rejeté avec sa raison ;
3. :mod:`grimoire.steward.report` — le **rapport** : carte + verdicts établis et rejetés, regroupés
   par action proposée. Aucune action n'est faite.

Le jugement (« livrée », « à faire »…) reste celui de l'agent qui suit la skill
``grimoire-steward`` ; seul ce que la preuve affirme est vérifié par du code.

**Lecture seule stricte.** Rien n'est écrit au ledger, au dépôt git ni sur GitHub ; les seules
écritures sont sous ``_grimoire-output/steward/`` (cartes, validations, rapports, et un magasin
d'objets git temporaire pour ``git merge-tree``, retiré après usage).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["MAP_SCHEMA", "OUTPUT_DIR", "REPORT_SCHEMA", "VALIDATION_SCHEMA", "VERDICTS_SCHEMA"]

MAP_SCHEMA = "grimoire-steward-map/v1"
VERDICTS_SCHEMA = "grimoire-steward-verdicts/v1"
VALIDATION_SCHEMA = "grimoire-steward-validation/v1"
REPORT_SCHEMA = "grimoire-steward-report/v1"

#: Le seul dossier où l'intendant écrit, relatif à la racine du projet.
OUTPUT_DIR = Path("_grimoire-output") / "steward"
