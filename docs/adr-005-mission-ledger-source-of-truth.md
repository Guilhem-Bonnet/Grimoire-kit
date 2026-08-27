# ADR-005 — Le Mission Ledger est la source, le task board une projection

- **Statut** : accepté (2026-08-27)
- **Contexte** : version 3.33, chantier tâches agentiques (issues #135-#142)

## Contexte

Le kit porte aujourd'hui **deux modèles de tâches concurrents**, sans conversion
entre eux.

| | `src/grimoire/missions/` | `_grimoire/standard/task-board.yaml` |
|---|---|---|
| Nature | JSONL append-only, machine à états validée | YAML déclaratif |
| États | 9 | 8 |
| Champs | `description`, `acceptance`, `guardrails`, `expected_evidence`, `dependencies`, `claim`, `risk_profile`, `type` | `title`, `acceptance_criteria`, réfs d'artefacts |
| Écriture | API Python, aucune surface utilisateur | édition manuelle du fichier |
| Lecture | projections cockpit, projections mémoire | `grimoire standard board verify`, page kanban |

Le premier est un moteur complet sans surface : pas de CLI, pas d'outil MCP, pas
d'écran, et aucun fichier ledger dans un projet réel. Le second est la seule
chose que l'utilisateur voit, et il ne sait presque rien de la tâche.

Tant que les deux coexistent, chaque brique du chantier doit être écrite deux
fois — et la première divergence de sémantique entre les deux jeux d'états
produira des tableaux qui mentent.

Un travail de conception antérieur (ADR-007 « Mission Board comme projection
causale », resté à l'état de proposition dans les artefacts d'atelier) avait
déjà tranché le principe. Cette ADR le porte dans le repo produit, où vivent les
décisions qui engagent ce que reçoit un utilisateur.

## Décision

**Le `MissionLedger` est la source de vérité. `task-board.yaml` en est une
projection exportée.**

Quatre conséquences opposables :

1. **Le board ne s'édite plus à la main comme source.** Il est régénéré depuis le
   ledger. Un projet peut continuer à le versionner : c'est un artefact de
   sortie du standard, au même titre qu'un `evidence-pack`.

2. **Une seule conversion d'états, écrite une fois.** Le mapping 9 → 8 vit dans
   un module unique, testé dans les deux sens. Toute nouvelle surface le
   réutilise plutôt que de le réinventer.

3. **Aucun état terminal ne s'importe de l'extérieur.** La discipline déjà en
   place dans `bridges/a2a_adapter.py` fait règle générale : un `completed` venu
   d'un système tiers devient `needs_verification`, jamais `closed`. La clôture
   reste une décision interne, adossée à une preuve.

4. **La projection ne remonte jamais vers la source.** Éditer le YAML ne change
   pas le ledger. Si la projection diverge, c'est la projection qui a tort.

## Alternatives écartées

**Faire du `task-board.yaml` la source et jeter le ledger.** Le YAML n'a ni
dépendances, ni claims, ni incidents, ni journal append-only ; il perd
l'historique à chaque écriture, et deux agents concurrents s'y écrasent. On
jetterait le seul composant capable de porter du travail agentique parallèle.

**Garder les deux et synchroniser dans les deux sens.** Une synchronisation
bidirectionnelle sans autorité désignée n'a pas de résolution de conflit
définissable : deux vérités, donc aucune.

**Ne rien décider et laisser chaque surface choisir.** C'est l'état actuel, et
c'est ce qui a produit un moteur sans surface d'un côté, un affichage sans
moteur de l'autre.

## Conséquences

- Le mapping d'états devient un point de passage obligé — et un point de rupture
  visible si quelqu'un ajoute un état d'un seul côté. C'est voulu : un test le
  garde.
- `grimoire standard board verify` continue de fonctionner, mais vérifie
  désormais une projection d'un ledger réel plutôt qu'un fichier écrit à la
  main — ou, à défaut de ledger, le fichier tel quel, pour ne pas casser les
  projets existants.
- Les surfaces d'écriture à venir (CLI, MCP, cockpit) visent le ledger. Aucune
  n'écrit le YAML.
- La page kanban gagne de quoi être lisible : le `MissionTask` porte une
  description, des garde-fous et des preuves attendues que le YAML n'avait pas.

## Références

- Issues #136 (ce lot), #142 (épic du chantier)
- `src/grimoire/missions/` — ledger, schémas, adaptateurs
- `src/grimoire/core/agentic_standard.py` — vérification du board
