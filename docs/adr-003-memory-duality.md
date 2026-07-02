# ADR-003 — Dualité mémoire : SDK maintenu, framework/memory en maintenance

- **Statut** : accepté (2026-07-02)
- **Contexte** : versions 3.17-3.18

## Contexte

Le dépôt contient deux implémentations mémoire :

| Zone | Rôle | Consommateurs |
|---|---|---|
| `src/grimoire/memory/` | API SDK (`MemoryManager`, backends pluggables, projections, taxonomie, Memory OS) | CLI `grimoire memory`, cockpit, serveur MCP, tests unitaires |
| `framework/memory/` | Scripts standalone côté projet (`mem0-bridge.py`, `maintenance.py`, seeds, templates) | `grimoire-init.sh` (copie dans les projets), agents via `agent-base.md` / `grimoire-trace.md` |

Faits établis par investigation (2026-07-02) :

1. **Le SDK ne référence jamais `framework/memory`** — aucune importation ni
   invocation depuis `src/grimoire/`.
2. **Le wheel pèse 1,5 Mo** avec tout le framework embarqué : l'« allègement du
   wheel » ne résout aucun problème réel — l'option d'exclure `framework/` du
   paquet est rejetée (risque de casser la résolution
   `grimoire/data/framework` pour zéro gain).
3. `framework/memory` est l'outillage runtime du **chemin d'installation shell
   legacy** (`grimoire-init.sh`), passé en mode maintenance en 3.18.0.

## Décision

1. **`src/grimoire/memory` est l'unique chemin maintenu.** Toute nouvelle
   feature mémoire (backend, projection, politique, intégration cockpit/MCP)
   va exclusivement dans le SDK.
2. **`framework/memory` suit `grimoire-init.sh` en mode maintenance** :
   corrections de bugs et sécurité uniquement, aucun nouveau développement.
   La zone est lint-clean (ruff, scope CI) depuis la 3.18.0 et doit le rester.
3. **Pas de changement de packaging** : `framework/` reste embarqué dans le
   wheel via force-include (1,5 Mo total, non-problème).
4. **Retrait couplé** : le jour où le chemin shell legacy est retiré,
   `framework/memory` est retiré dans la même version majeure, avec un guide
   de migration vers `grimoire memory` (SDK).

## Conséquences

- Les agents des projets scaffoldés par le chemin shell continuent de
  fonctionner sans rupture.
- La documentation agent (`agent-base.md`) pourra, à terme, pointer vers
  `grimoire memory` au lieu de `mem0-bridge.py` — étape 2 de la transition,
  hors périmètre de cet ADR.
- Un contributeur qui propose une feature dans `framework/memory` est redirigé
  vers le SDK.
