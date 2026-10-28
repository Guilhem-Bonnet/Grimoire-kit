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
(`src/grimoire/`, ~40k lignes, chemin recommandé) et l'ère shell
(`framework/tools/`, ~35k lignes après drainage, + 4,3k lignes de bash).
La dette ne se résorbe pas si la zone legacy continue d'accueillir du
code. Le gel rend la direction mécanique : tout investissement va au SDK.

## Instrument de décision

`planning/framework-tools-inventory.md` (régénérable par
`python scripts/framework-usage-inventory.py`) classe chaque outil de
`framework/tools/` par usage réel. Politique de résorption :

| Classe | Traitement |
| --- | --- |
| REFERENCED | porter vers `src/` à la demande, puis supprimer l'original |
| TRANSITIVE | chargé au runtime par un REFERENCED — drainer l'appelant d'abord |
| TEST_ONLY | supprimer par lots avec leurs tests (aucun chemin runtime) |
| DOCS_ONLY | réécrire la doc vers l'équivalent SDK, puis supprimer |
| INTERNAL / UNREFERENCED | supprimer directement |

Deux pièges vérifiés sur cet inventaire, encodés dans
`tests/unit/test_framework_usage_inventory.py` :

- un artefact **généré** qui énumère la zone (les plafonds du ratchet, les
  données du site) cite chaque outil sans en appeler aucun ; le compter comme
  référence classe tout en REFERENCED et rend l'instrument aveugle ;
- la reachability se calcule **en transitif** : les outils de l'ère shell
  s'appellent par chemin de fichier (`importlib`, `subprocess`), donc un outil
  chargé par un outil référencé n'est pas supprimable même s'il n'a que des
  tests.

## Décisions par outil (2026-10-26)

L'inventaire régénéré le 2026-10-26 (47 fichiers `.py`, 35 369 lignes ; les 55
fichiers suivis sous `framework/tools/` comptent aussi 8 non-`.py`) porte, pour
chaque outil, la décision de la politique ci-dessus dans sa colonne `décision` :

- 47 REFERENCED : **geler**, porter vers `src/` à la demande ; « geler » est leur
  état présent, la suppression de l'original suit le portage (tableau
  ci-dessus), pas avant. Les 6 outils classés TRANSITIVE le 2026-10-25
  (`rag-indexer.py`, `rag-retriever.py`, `memory-sync.py`, `llm-router.py`,
  `token-budget.py`, `incubator.py`) ont désormais une référence runtime directe
  et passent REFERENCED : même décision, geler ;
- aucun outil UNREFERENCED, INTERNAL, DOCS_ONLY, TEST_ONLY ni TRANSITIVE : **aucun
  outil à supprimer** à cette date.

### Fichiers non-`.py` de `framework/tools/` (8)

L'inventaire ne classe que les `.py` ; les 8 autres fichiers suivis reçoivent ici
leur classe (mêmes libellés que ci-dessus, établie par `git grep` du basename hors
index générés) et leur décision :

| Fichier | Classe | Référencé par | Décision |
| --- | --- | --- | --- |
| `stigmergy_hooks/stigmergy-emit.json` | REFERENCED | `src/grimoire/tools/stigmergy_hooks.py`, tests | geler : actif installé par le SDK ; porter sous `src/` avec le module |
| `stigmergy_hooks/stigmergy-sense.json` | REFERENCED | idem | geler (idem) |
| `stigmergy_hooks/scripts/stigmergy-emit-post-edit.sh` | REFERENCED | `stigmergy-emit.json`, `stigmergy_hooks.py` | geler (idem) |
| `stigmergy_hooks/scripts/stigmergy-emit-stop.sh` | REFERENCED | idem | geler (idem) |
| `stigmergy_hooks/scripts/stigmergy-sense.sh` | REFERENCED | `stigmergy-sense.json`, `stigmergy_hooks.py` | geler (idem) |
| `quick-check.sh` | REFERENCED | `framework/hooks/pre-push.sh` | geler ; disparaît avec le chemin shell (4.0.0) |
| `grimoire-completion.zsh` | DOCS_ONLY | gabarit de bug-report, `README.md` | geler : pas de remplaçant SDK ; à réécrire vers la complétion du CLI `grimoire` avant suppression |
| `README.md` | DOCS_ONLY | `docs/guides/choisir-un-archetype.md` | geler : à réécrire vers l'équivalent SDK, puis supprimer avec la zone |

Aucune suppression pour ces 8 fichiers non plus : tous ont soit un appelant runtime,
soit une documentation qui les cite et aucun remplaçant SDK.

### Suppressions

Aucune suppression en attente au 2026-10-26. Les retraits déjà faits sont ceux du
drainage du 2026-08-10 (voir ci-dessous) et de #111 (128 fichiers de
`framework/` drainés). Toute suppression future s'inscrit ici avec sa date et
son outil.

## État de la résorption

Point de départ (2026-07-12) : 109 fichiers, 71 604 lignes, dont 68 sans aucun
chemin d'accès. Après le drainage du 2026-08-10 : **47 fichiers**, dont 41
REFERENCED et 6 TRANSITIVE. Plus aucun TEST_ONLY.

Le retrait complet du chemin shell est annoncé pour **4.0.0**
(avis de dépréciation dans `grimoire-init.sh` et
`docs/archetype-guide.md`).
