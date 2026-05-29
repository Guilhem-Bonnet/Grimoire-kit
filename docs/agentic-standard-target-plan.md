# Plan cible du runtime normatif agentique

Ce plan décrit la cible projet pour transformer la documentation normative Grimoire en runtime agentique gouverné. Le socle actuel fournit déjà `grimoire standard init/verify/audit/detect-providers`, les profils `minimal`, `orchestrated`, `governed`, les registres provider/knowledge et les artefacts de conformité. La suite consiste à rendre les patterns de kanban, mémoire, orchestration, contexte et preuves exécutables.

## Cible

```text
Normes et patterns
→ schémas machine-readable
→ artefacts projet
→ kanban normatif
→ mémoire multi-niveaux
→ orchestration de contexte
→ orchestration agentique
→ FSM evidence-gated
→ score conformité
→ remediation
→ CI / release gates
```

## P0 — Socle normatif exécutable

Objectif : convertir les concepts de la documentation en contrats vérifiables.

Livrables :

- `framework/agentic-standard/target-schema.yaml`
- schémas pour board, mémoire, contexte, orchestration, evidence gates, patterns, score ;
- extension de `profile-map.yaml` pour déclarer les obligations par profil ;
- tests de validation des contrats.

Critères de sortie :

- chaque règle normative pointe vers un artefact, un check, un gate, un score ou une remediation ;
- `minimal`, `orchestrated`, `governed` restent progressifs ;
- le mode `governed` peut devenir strict sans casser l’adoption initiale.

## P0 — Kanban normatif

Objectif : relier le travail réel aux preuves.

Artefact cible : `_grimoire/standard/task-board.yaml`.

Contenu minimal :

- backlog ;
- tâches avec `task_id`, statut, owner, priorité, blockers ;
- liens vers task envelope et evidence pack ;
- acceptance criteria ;
- gates attendus par transition.

Commandes cibles :

```bash
grimoire standard board verify
grimoire standard task create
grimoire standard task update
grimoire standard task verify
```

## P0 — Mémoire multi-niveaux gouvernée

Objectif : normer l’usage des mémoires existantes et futures.

Artefact cible : `_grimoire/standard/memory-policy.yaml`.

Niveaux :

- session ;
- project ;
- organization ;
- procedural ;
- semantic ;
- episodic ;
- long-term.

Chaque niveau doit déclarer :

- scope ;
- droits read/write ;
- rétention ;
- fraîcheur ;
- trust ;
- redaction ;
- compatibilité provider.

## P0 — Orchestrateur de contexte avancé

Objectif : produire un contexte déterministe, traçable et compatible avec la mission, la tâche, la mémoire, les sources knowledge et la policy provider.

Artefact cible : `_grimoire-output/context/{task_id}/context-bundle.yaml`.

Sources d’entrée :

- mission brief ;
- task board ;
- task envelope ;
- memory policy ;
- knowledge registry ;
- provider registry ;
- standard profile.

Commandes cibles :

```bash
grimoire standard context build --task-id bootstrap
grimoire standard context verify --task-id bootstrap
```

## P1 — Orchestration agentique normée

Objectif : convertir les patterns d’orchestration en politique exécutable.

Artefact cible : `_grimoire/standard/orchestration-policy.yaml`.

Domaines :

- rôles ;
- routing ;
- handoffs ;
- escalation ;
- arbitration ;
- review gates ;
- fallback ;
- compatibilité provider.

## P1 — FSM evidence-gated

Objectif : empêcher ou signaler les transitions sans preuves.

Artefact cible : `_grimoire/standard/evidence-gates.yaml`.

États cibles :

```text
proposed → ready → in_progress → review → accepted → released → archived
              ↘ blocked ↗
```

Commandes cibles :

```bash
grimoire standard gate check --task-id bootstrap
grimoire standard gate explain --task-id bootstrap
```

## P1 — Catalogue de patterns exécutable

Objectif : classer les patterns et les rendre applicables par CLI.

Artefact cible : `_grimoire/standard/pattern-catalog.yaml`.

Familles :

- context ;
- memory ;
- orchestration ;
- workflow ;
- governance ;
- provider ;
- knowledge ;
- security ;
- quality ;
- runtime UX.

Commandes cibles :

```bash
grimoire pattern list
grimoire pattern show context.context-bundle
grimoire pattern apply governance.evidence-gate
```

## P1 — Doc-to-graph / knowledge index

Objectif : relier les checks aux sources normatives.

Artefact cible : `_grimoire/standard/knowledge-graph-manifest.yaml`.

Le graphe doit relier :

- documents sources ;
- concepts ;
- obligations ;
- recommandations ;
- checks ;
- patterns ;
- artefacts générés.

## P2 — Score de conformité

Objectif : rendre l’audit lisible, pondéré et actionnable.

Artefact cible : `_grimoire/standard/compliance-score.yaml`.

Dimensions :

- artifacts ;
- provider policy ;
- knowledge registry ;
- task board ;
- memory policy ;
- context contract ;
- orchestration policy ;
- evidence gates ;
- pattern catalog ;
- knowledge graph ;
- CI release gate.

Commande cible :

```bash
grimoire standard score --profile governed
```

## P2 — Remediation automatique

Objectif : transformer l’audit en corrections contrôlées.

Artefact cible : `_grimoire/standard/remediation-plan.yaml`.

Commandes cibles :

```bash
grimoire standard fix --dry-run
grimoire standard fix --apply
```

Contraintes :

- aucune modification destructive sans `--force` ;
- aucune écriture hors racine projet ;
- chaque correction doit pointer vers un check et une source normative.

## P2 — Mode governed strict

Objectif : faire de `governed` un profil de release.

Règles :

- CI hard-fail sur score insuffisant ;
- providers verrouillés ;
- evidence gates obligatoires ;
- memory et knowledge freshness obligatoires ;
- release impossible si audit error.

## P3 — Adoption Forge et release

Objectif : faire de Forge le projet consommateur de référence.

Livrables :

- board Forge ;
- memory policy Forge ;
- context bundle bootstrap ;
- score audit Forge ;
- documentation Kit/Forge ;
- release PyPI.

## Validation globale

La cible sera considérée atteinte quand ces commandes seront stables :

```bash
grimoire standard init --profile governed
grimoire standard board verify
grimoire standard memory verify
grimoire standard context build --task-id bootstrap
grimoire standard gate check --task-id bootstrap
grimoire standard audit --markdown
grimoire standard score
```

