---
audience: décision
---

# Mémoire du serveur cockpit — audit et bornage (2026-11-12)

Tâche : `GAO-cockpit-trou-001`. Constat de départ : le processus `grimoire cockpit serve`
occupe 1,35 Go de mémoire résidente après six heures, avec quatre projets enrôlés.
Ce document mesure les rétentions, nomme la cause et décrit les bornes posées.

## Méthode

Banc synthétique **hors du dossier de contrôle réel** (`tmp_path` uniquement) :

| Élément | Volume |
|---|---|
| Tâches du Mission Ledger | 300 (`TaskService.add`, dont 20 réclamées et passées `running`) |
| `_grimoire-output/traces/traces.jsonl` | 20 000 traces, 17,7 Mo |
| Journaux de session Claude Code | 5 × 10 Mo (50 Mo), 9 571 appels assistant chacun, sous un `CLAUDE_CONFIG_DIR` temporaire |
| Journaux de hooks | 20 fichiers `session-*.json` |
| Arbre de fichiers (route `files`) | 30 150 fichiers sous `_grimoire/overrides` |

Le harnais :

- `tracemalloc.start(12)` **avant la première lecture** — sinon la mémoire des caches
  construits au premier appel (index des traces, états de la Flotte) n'est jamais vue ;
- 200 lectures de chaque route sans traceur pour la durée et la stabilité, puis
  lectures sous traceur pour l'attribution (`/api/workspace/tasks`,
  `tasks?annotations=0`, `attention`, `sessions`, `sheet`, `evidence`, `capacity`,
  `fleet/live`) ;
- la sonde d'hôte (`claude agents --json`) est neutralisée : elle coûte plusieurs
  secondes par lecture et ne retient rien. Aucune lecture du dossier de contrôle
  sous `$HOME` n'a été faite.

Le traceur multiplie le coût d'une lecture par ~30 (allocation de chaque objet JSON) :
l'attribution a donc été faite sur les premières lectures, la campagne de 200 sur le
chemin réel sans traceur. Les valeurs ci-dessous sont celles du code **avant
correctif** (commit `faa33529` pour les numéros de ligne).

## Les trois plus gros porteurs

| # | Porteur | Racine de rétention (`fichier:ligne`) | Site d'allocation observé (`fichier:ligne`) | Retenu mesuré |
|---|---|---|---|---|
| 1 | **Flotte en direct — état par session** : un `_Call` par `message.id` et un `_Tool` par appel d'outil de **tout** le journal, gardés pour la vie du processus | `src/grimoire/tools/workspace_fleet_live.py:508` (`state.calls[...] = call`), `:537` (`state.open_tools[...] = tool`) | `workspace_fleet_live.py:122` (9,17 Mo / 47 855 obj, résumés d'outils), `:507` (4,02 Mo / 47 855 `_Call`), `:531` (3,65 Mo / 47 855 `_Tool`) | **48,9 Mo pour 50 Mo de journaux** (≈ 1 octet Python par octet de journal) |
| 2 | **Index des traces par tâche** : chaque `TraceRecord` de `traces.jsonl` est parsé et gardé dans `_by_task`, jamais évincé | `src/grimoire/traces/task_index.py:149` (`_by_task.setdefault(...).append(trace)`) | `json/decoder.py:361` via `task_index.py:146` (19,47 Mo / 400 000 obj), `traces/schemas.py:215` (3,97 Mo / 20 000 obj), `schemas.py:132/:95/:229` (4,1 Mo) | **≈ 28 Mo pour 17,7 Mo de traces** (≈ 1,7×) |
| 3 | **Cache des lectures du direct** : 64 réponses gardées, bornées en nombre mais pas en octets | `src/grimoire/tools/workspace_read_cache.py:113` (`_entries[key] = _Entry(...)`) | `workspace_api.py:255` (14,75 Mo), `transcript_usage.py:521` (8,91 Mo), `liveness.py:413` (5,22 Mo) | **61,6 Mo pour 64 entrées** (941 Ko en moyenne, board annoté de 300 tâches) |

## Pics transitoires (non retenus, mais ils gonflent la RSS)

| Route / opération | Pic alloué | Remarque |
|---|---|---|
| `fleet/live`, première lecture d'un journal | 71,4 Mo | `workspace_fleet_live.py:606` : `data = handle.read()` lit **tout** le journal d'un bloc (50 Mo ici, 115–195 Mo sur le poste) |
| `tasks` | 86,4 Mo | inclut la construction de l'index des traces (20 000 `TraceRecord`) |
| `sheet` | 102,6 Mo | recalcul complet du board annoté |
| `files` (30 150 fichiers) | 36,0 Mo, 5,7 s | `workspace_api._walk` matérialisait `sorted(root.rglob("*"))` **avant** d'appliquer `MAX_FILES_PER_TIER` ; 3,8 Mo retenus (digests). Corrigé en amont par #1217 (parcours `os.scandir` borné à 2 000 fichiers / 2 s), fusionné dans cette branche. |

## Cause retenue

**Des caches process sans borne en octets, dont la taille suit celle des fichiers du
projet, plus une lecture de journal en un bloc.** Ce ne sont pas des fuites : rien
n'est perdu, mais rien n'est jamais rendu tant que le serveur vit.

Sur le poste (quatre projets, journaux de session de 115 à 195 Mo) :

- l'état de la Flotte en direct vaut environ la taille des journaux lus : 6 à 7 journaux
  de ~190 Mo suffisent à ~1,1–1,3 Go ;
- l'index des traces ajoute ~1,7× la taille de `traces.jsonl` ;
- le cache de lecture ajoute jusqu'à ~60 Mo (64 réponses de board annoté).

L'ordre de grandeur des 1,35 Go observés est donc expliqué par le seul premier porteur
sur une machine où plusieurs grosses sessions ont été ouvertes. **La composition exacte
du processus sur le poste n'a pas été mesurée** (le dossier de contrôle réel ne doit pas
être lu) ; c'est une extrapolation depuis le banc, pas une lecture du processus.

**Non mesuré** : le tick d'orchestration à 0,96 Go en deux minutes (processus distinct,
hors de ce banc) ; le coût 27–31 s de `/api/workspace/files` sur le clone imbriqué
(seul son pic mémoire a été mesuré ici, sur 30 150 fichiers).

## Corrections de cette PR

1. **Flotte en direct — lecture par blocs** (`workspace_fleet_live.READ_CHUNK_BYTES = 1 Mio`) :
   le journal n'est plus chargé d'un bloc ; seules les lignes complètes sont repliées,
   le reliquat attend la passe suivante. Une ligne démesurée (> 4 Mio) est sautée.
2. **Flotte en direct — états bornés** : `MAX_CALLS = 512` identifiants d'appels par
   session (les compteurs sont déjà agrégés par modèle, le coût affiché ne change pas),
   `MAX_FILE_STATES = 512` journaux (le plus anciennement lu sort, relu à zéro s'il
   redevient actif), 8 192 identifiants d'outils et 256 outils ouverts.
3. **Cache de lecture — borne en octets** (`MAX_TOTAL_BYTES = 48 Mio`) : la taille
   récursive des réponses gardées est plafonnée ; l'entrée la plus ancienne sort, y
   compris la réponse qui vient d'être calculée si elle dépasse à elle seule le budget.

Mesuré après correctif, même banc :

| Mesure | Avant | Après |
|---|---|---|
| Flotte : retenu (5 journaux de 10 Mo) | 48,9 Mo | **5,5 Mo** |
| Flotte : pic de la première lecture | 71,4 Mo | **10,3 Mo** |
| Flotte : 200 lectures (lecture chaude) | ≈ 1 s/lecture (banc 5 × 20 Mo) | **37 ms/lecture** (banc 5 × 10 Mo) |
| Cache : 64 entrées possibles | 61,6 Mo | **50,0 Mo (budget 48 Mio, 52 entrées)** |

## Ce qui n'est pas corrigé ici

- **Index des traces par tâche** (2ᵉ porteur) : le borner changerait les coûts affichés
  des tâches anciennes (le board lit l'historique complet de chaque tâche) — c'est une
  décision de produit, hors des deux causes que la tâche autorise à corriger. Piste :
  compacter par tâche les seules grandeurs que `task_usage` replie (tokens, cache,
  recettes, statut de coût) au lieu de garder chaque `TraceRecord`.

`files_view` (pic transitoire de 36 Mo mesuré ci-dessus) n'est pas corrigé *par cette
PR* : #1217 l'a borné en amont (`os.scandir`, 2 000 fichiers / 2 s), et sa fusion est
incluse ici.

## Reproduction

Le banc est construit et mesuré dans un même processus, tout sous `tmp_path` :
`HOME`, `CLAUDE_CONFIG_DIR` et `GRIMOIRE_COCKPIT_HOME` pointent dedans, la sonde
d'hôte est remplacée par `lambda: None`. Les tâches et les traces sont créées par
`TaskService`/`TraceRecord`, les journaux de session écrits sous
`$CLAUDE_CONFIG_DIR/projects/<slug>/<session>.jsonl`.

```python
import tracemalloc
from grimoire.tools import workspace_read_cache, workspace_routes
from grimoire.tools.workspace_routes import workspace_get

workspace_routes._host_probe = workspace_routes._host_entries = lambda: None

tracemalloc.start(12)               # avant la première lecture : sinon les caches sont invisibles
baseline = tracemalloc.take_snapshot()
for _ in range(200):
    workspace_read_cache.clear()    # une empreinte qui bouge, comme un serveur vivant
    workspace_get(root, "/api/workspace/tasks", {})
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.compare_to(baseline, "traceback")[:20]:
    frame = stat.traceback[-1]
    print(stat.size_diff, stat.count_diff, f"{frame.filename}:{frame.lineno}")
```

Pour la Flotte en direct (non cachée), appeler `fleet_live_view(root)` ; pour le
cache de lecture, remplir 64 clés distinctes et sommer
`workspace_read_cache.stats()["bytes"]`.


Les tests qui bornent ces états sont
`tests/unit/tools/test_workspace_read_cache.py` (taille totale plafonnée, plus ancienne
évincée) et `tests/unit/tools/test_fleet_live.py` (lecture par blocs, `MAX_CALLS`,
`MAX_FILE_STATES`).
