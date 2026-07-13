# framework/ est gelé

> Statut effectif depuis 3.24.0 — appliqué mécaniquement par
> `scripts/check-code-ratchet.py` (Makefile `make ratchet`, job CI lint).

## Règle

Le code sous `framework/` (Python et shell) et les points d'entrée shell
racine (`grimoire-init.sh`, `grimoire.sh`, `install.sh`) ne peuvent que
**décroître** :

- aucun nouveau fichier `.py`/`.sh` n'entre dans la zone gelée — toute
  nouvelle capacité vit sous `src/grimoire/` ;
- aucun fichier gelé ne dépasse son plafond de lignes enregistré dans
  `scripts/code-ratchet-baseline.json` ;
- suppression et réduction sont toujours permises — c'est l'objectif ;
  après un lot de suppressions, resserrer les plafonds avec
  `python scripts/check-code-ratchet.py --rebaseline` (refuse toute
  hausse).

Les corrections de bugs restent possibles tant qu'elles tiennent dans le
plafond du fichier (une correction qui grossit un outil gelé est le
signal qu'il est temps de le porter sous `src/`).

## Pourquoi

Deux implémentations de la même plateforme cohabitent : le SDK
(`src/grimoire/`, ~34k lignes, chemin recommandé) et l'ère shell
(`framework/tools/` ~71k lignes + 4,3k lignes de bash). La dette ne se
résorbe pas si la zone legacy continue d'accueillir du code. Le gel rend
la direction mécanique : tout investissement va au SDK.

## Instrument de décision

`docs/framework-tools-inventory.md` (régénérable par
`python scripts/framework-usage-inventory.py`) classe chaque outil de
`framework/tools/` par usage réel. Politique de résorption :

| Classe | Traitement |
| --- | --- |
| REFERENCED | porter vers `src/` à la demande, puis supprimer l'original |
| TEST_ONLY | supprimer par lots avec leurs tests (aucun chemin runtime) |
| DOCS_ONLY | réécrire la doc vers l'équivalent SDK, puis supprimer |
| INTERNAL / UNREFERENCED | supprimer directement |

Le retrait complet du chemin shell est annoncé pour **4.0.0**
(avis de dépréciation dans `grimoire-init.sh` et
`docs/archetype-guide.md`).
